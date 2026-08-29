"""Graph -> netlist IR builder.

Builds the ``NetlistModule`` IR from an ``InMemoryVIGraph`` (or, for the
legacy path, from a ``VIContext``'s ``Operation`` tree) -- see
``netlist.py``'s module docstring for the full pipeline this feeds into::

    graph (truth)  ->  NetlistModule (IR)  ->  { text render, diff render, viewer }

Two builders live here:

- ``build_netlist`` -- the OLD Operation-tree walker (kept for the parity
  test and ``describe --format netlist``).
- ``build_netlist_from_graph`` -- the graph-native walker that ``lvnet``
  (``render_lvnet``) consumes; ``index_module`` is a post-build index used
  only by ``diff.py``.

Split out of ``netlist.py`` purely to shrink it -- this is a mechanical,
behavior-preserving move, not a design change. ``netlist.py`` re-exports
``build_netlist``/``build_netlist_from_graph``/``index_module`` so every
existing ``from ...graph.netlist import build_netlist`` (etc.) call site
keeps working unchanged. This module must NEVER import from ``.netlist``
(``netlist.py`` imports these names back from here for re-export; a reverse
import would be a fragile, import-order-dependent circular import).
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import (
    CaseFrame,
    CaseOperation,
    DisableStructureOperation,
    EventFrame,
    EventOperation,
    FeedbackOperation,
    FormulaOperation,
    FPTerminal,
    InPlaceOperation,
    InvokeOperation,
    LoopOperation,
    LVType,
    LVTypeKind,
    Operation,
    PrimitiveOperation,
    PropertyOperation,
    SequenceFrame,
    SequenceOperation,
    SubVIOperation,
    Terminal,
    Tunnel,
    TunnelMode,
    TunnelTerminal,
    _is_error_cluster,
)
from ..parser import parse_vi
from ..parser.node_types import get_display_name
from .core import _OPERATION_KINDS, _graph_node_to_op_kind, _uid_of
from .interface_order import ordered_interface, requirement_state
from .loading import build_dep_ref_map, collect_direct_dep_qnames
from .lvnet_grammar import (
    _LVNET_TYPEDEF_NAV_PREFIX,
)
from .models import (
    AnyGraphNode,
    CaseStructureNode,
    Constant,
    ConstantNode,
    DisableStructureNode,
    EventStructureNode,
    FormulaNode,
    InPlaceNode,
    LocalVariableNode,
    LoopNode,
    SequenceNode,
    VIContext,
    VINode,
    WireEnd,
)
from .models import (
    PrimitiveNode as GraphPrimitiveNode,
)
from .netlist_models import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    DefaultValue,
    DependencyKind,
    EtaMerge,
    GammaCase,
    GammaMerge,
    MuMerge,
    NetlistBoundaryInput,
    NetlistComponent,
    NetlistConstant,
    NetlistDependency,
    NetlistFeedback,
    NetlistFrame,
    NetlistInstance,
    NetlistInstanceKind,
    NetlistItem,
    NetlistModule,
    NetlistOutput,
    NetlistPropertyAccess,
    NetlistScope,
    NetlistTerminalBinding,
    NetlistTunnelInfo,
    NetRef,
    _BuildCtx,
)
from .op_walk import (
    ComponentPort,
    _case_output_tunnel_outers,
    _const_value_str,
    _has_output_tunnel,
    _is_eta_output_tunnel,
    _is_gamma_output_tunnel,
    _is_mu_shift_register_read,
    _loop_output_tunnel_outers,
    _loop_shift_register_pairs,
    _paired_tunnel_id,
    _selector_label,
    _subvi_ports,
    _terminal_display_name,
    correlate_property_terminals,
    index_terminal_owners,
)
from .queries import ClassContext, collect_class_context
from .render_lvnet import (
    _lvnet_const_value_str,
    _lvnet_literal_token,
)

if TYPE_CHECKING:
    from .core import InMemoryVIGraph

# ============================================================
# build_netlist
# ============================================================


def _display_name(op: Operation) -> str:
    # Bare name here on purpose: the netlist MODEL uses instance names as lookup
    # keys (see ``_find_instance``), so a SubVI instance stays its bare name.
    # The class/lib-qualified identity is available on ``op.qualified_name`` /
    # ``op.display_name`` for any consumer that wants it (describe uses it).
    node_word = get_display_name(op.node_type) if op.node_type else None
    return op.name or node_word or "Node"


def _walk_flat(operations: list[Operation]) -> list[Operation]:
    """Flatten the operation tree in the same deterministic order
    ``_describe_op_list`` recurses it, for name-occurrence counting.

    Case/Sequence/Disable-structure frames recurse into
    ``frame.operations``; everything else recurses into ``inner_nodes``
    (this covers loop bodies and IPES bodies, matching describe.py's
    generic fallback).

    SHARED INVARIANT with ``op_walk.index_terminal_owners``/
    ``_find_op_owning_terminal``: those two recurse ``inner_nodes`` ALWAYS
    plus frames for structures, rather than branching case/sequence/disable/
    event to frames-only like this walk does. The two recursions only agree
    because a structure op never populates BOTH ``inner_nodes`` and
    ``frames`` at once -- so either one is a no-op on any given op and the
    walks visit the same set either way. Do NOT change this branching to
    "recurse inner_nodes always" without checking that invariant still
    holds -- this walk's order feeds occurrence/id assignment, so a changed
    order changes ``build_netlist``'s output, not just internal structure.
    """
    flat: list[Operation] = []
    for op in operations:
        flat.append(op)
        match op:
            case (
                CaseOperation()
                | SequenceOperation()
                | DisableStructureOperation()
                | EventOperation()
            ):
                for frame in op.frames:
                    flat.extend(_walk_flat(frame.operations))
            case _:
                if op.inner_nodes:
                    flat.extend(_walk_flat(op.inner_nodes))
    return flat


def _assign_occurrences(flat: list[Operation]) -> dict[str, int]:
    """Assign ``#n`` occurrence numbers to node UIDs whose display name
    repeats within the VI. 1-based, in ``flat``'s given order (the
    deterministic ``_walk_flat`` / ``_node_order_key`` order -- see the
    deterministic-node-order rule; ``build_netlist`` flattens once and
    passes the same list here and to every other ``_assign_*``/indexing
    helper). Names that are unique in the VI are absent from the returned
    map (no tag).

    ONLY instance ops participate: structures render via ``scope_header`` and
    never use the ``#n`` tag, so counting them (e.g. a CaseOperation whose
    name fell back to ``"Select"`` in this LV XML dialect) would wrongly
    inflate a real ``Select`` primitive to ``Select#2`` with no ``Select#1``
    ever shown. In-Place-Element structures are containers too (excluded from
    ``_build_components``), so they must not get an occurrence tag either.
    """
    insts = [
        op
        for op in flat
        if not isinstance(
            op,
            (
                CaseOperation,
                LoopOperation,
                SequenceOperation,
                DisableStructureOperation,
                InPlaceOperation,
                EventOperation,
                # Feedback Nodes render as a mu net (``fb{k}``), never as a named
                # instance -- counting them would tag a spurious "Feedback
                # Node#n" occurrence, same reason structures are excluded.
                FeedbackOperation,
            ),
        )
    ]
    names = [_display_name(op) for op in insts]
    counts = Counter(names)

    occurrence_by_uid: dict[str, int] = {}
    running: dict[str, int] = {}
    for op, name in zip(insts, names, strict=True):
        if counts[name] > 1:
            running[name] = running.get(name, 0) + 1
            occurrence_by_uid[_uid_of(op.id)] = running[name]
    return occurrence_by_uid


def _assign_sequential_ids(
    flat: list[Operation],
    predicate: Callable[[Operation], bool],
) -> dict[str, int]:
    """Deterministic 0-based id per operation matching ``predicate``, keyed
    by trailing node UID, in ``flat``'s given order (the deterministic
    ``_walk_flat`` / ``_node_order_key`` order -- see the deterministic-
    node-order rule).

    Shared by the three id spaces ``build_netlist`` threads through
    ``_BuildCtx`` -- identical but for the filter:

    - ``case_id_by_uid`` (``predicate`` selects ``CaseOperation``) -- used
      ONLY to name a case's gamma-merge output nets (``case{id}.out{k}``).
    - ``loop_id_by_uid`` (``LoopOperation``) -- the loop analogue, names a
      loop's mu/eta-merge nets (``loop{id}.shift{k}`` / ``loop{id}.out{k}``).
    - ``feedback_id_by_uid`` (``FeedbackOperation`` MASTERs only, via
      ``op.is_master``) -- names the ``fb{k}`` mu net a consumer reading the
      Feedback Node's output resolves to.

    None of these is a ``#n`` scope-header tag -- see ``_assign_occurrences``,
    which excludes structures for the same reason: a case/loop's own
    ``scope_header`` never carries an occurrence disambiguator.
    """
    ids: dict[str, int] = {}
    next_id = 0
    for op in flat:
        if predicate(op):
            ids[_uid_of(op.id)] = next_id
            next_id += 1
    return ids


def _is_feedback_output_read(op: Operation, term: Terminal) -> bool:
    """True when ``term`` is a Feedback Node MASTER's OUTPUT terminal
    (``leftFeedback`` -- the value read this iteration). A wire whose source
    lands here IS the mu recurrence, so ``_resolve_source`` names the
    ``fb{k}`` net instead of the raw producing node.terminal."""
    return (
        isinstance(op, FeedbackOperation)
        and op.is_master
        and term.direction == "output"
    )


def _tunnel_net_name(
    op: Operation,
    term: Terminal,
    id_map: dict[str, int],
    outers_fn: Callable[[Operation], list[Terminal]],
    prefix: str,
) -> str:
    """The merge net name for a case/loop output-tunnel OUTER terminal --
    ``{prefix}{id}.out{k}``, ``k`` being ``term``'s 0-based position among
    this structure's own output tunnels (``outers_fn`` -- the SAME
    enumerator ``_build_case_outputs``/``_build_loop_outputs`` use to number
    ``GammaMerge.net``/``EtaMerge.net``, so a consumer's resolved reference
    and the scope's own definition line always agree on the name).

    Shared by ``_gamma_net_name`` (case) and ``_eta_net_name`` (loop) --
    structurally identical but for the id map, the outer-terminal enumerator,
    and the net prefix.
    """
    id_ = id_map[_uid_of(op.id)]
    outers = outers_fn(op)
    k = next(i for i, t in enumerate(outers) if t.id == term.id)
    return f"{prefix}{id_}.out{k}"


def _gamma_net_name(op: Operation, term: Terminal, build_ctx: _BuildCtx) -> str:
    """The gamma-merge net name for a case's output tunnel OUTER terminal --
    ``case{id}.out{k}`` -- see ``_tunnel_net_name``."""
    return _tunnel_net_name(
        op,
        term,
        build_ctx.case_id_by_uid,
        _case_output_tunnel_outers,
        "case",
    )


def _mu_net_name(op: Operation, term: Terminal, build_ctx: _BuildCtx) -> str:
    """The mu-merge net name for a loop shift-register terminal (either the
    INNER ``lSR`` terminal or the OUTER ``rSR`` terminal -- see
    ``op_walk._is_mu_shift_register_read``) -- ``loop{id}.shift{k}``, ``k``
    being this terminal's shift-register PAIR's 0-based position
    (``_loop_shift_register_pairs``, the SAME ordering
    ``_build_loop_shift_registers`` uses to number ``MuMerge.net``, so a
    consumer's resolved reference and the loop scope's own definition line
    always agree on the name).
    """
    loop_id = build_ctx.loop_id_by_uid[_uid_of(op.id)]
    pairs = _loop_shift_register_pairs(op)
    k = next(
        i
        for i, (lsr, rsr) in enumerate(pairs)
        if lsr.inner_terminal_uid == term.id
        or (rsr is not None and rsr.outer_terminal_uid == term.id)
    )
    return f"loop{loop_id}.shift{k}"


def _eta_net_name(op: Operation, term: Terminal, build_ctx: _BuildCtx) -> str:
    """The eta-merge net name for a loop's output tunnel OUTER terminal --
    ``loop{id}.out{k}`` -- see ``_tunnel_net_name``."""
    return _tunnel_net_name(
        op,
        term,
        build_ctx.loop_id_by_uid,
        _loop_output_tunnel_outers,
        "loop",
    )


_ETA_INDEX_MODE_BY_TUNNEL_MODE: dict[TunnelMode, str] = {
    TunnelMode.INDEXING: "array",
    TunnelMode.LAST_VALUE: "last",
    TunnelMode.CONCATENATING: "concat",
    TunnelMode.PASSTHROUGH: "passthrough",
}


def _eta_index_mode(mode: TunnelMode | None) -> str:
    """``EtaMerge.index_mode`` from a loop output tunnel's BASE ``TunnelMode``
    -- faithful, never guessed: every base value maps to its own distinct
    label (``array``/``last``/``concat``/``passthrough``), so none are silently
    folded together. The orthogonal Conditional modifier is carried separately
    on ``EtaMerge.conditional``. ``None`` (shouldn't occur on a genuine
    ``lpTun`` tunnel) renders the honest ``"?"`` rather than guess.
    """
    if mode is None:
        return "?"
    return _ETA_INDEX_MODE_BY_TUNNEL_MODE.get(mode, "?")


_INT_UNDERLYING_TYPES = frozenset(
    {
        "NumInt8",
        "NumInt16",
        "NumInt32",
        "NumInt64",
        "NumUInt8",
        "NumUInt16",
        "NumUInt32",
        "NumUInt64",
    }
)
_FLOAT_UNDERLYING_TYPES = frozenset({"NumFloat32", "NumFloat64", "NumFloatExt"})
_COMPLEX_UNDERLYING_TYPES = frozenset(
    {"NumComplex64", "NumComplex128", "NumComplexExt"}
)


def _default_literal(lv_type: LVType | None) -> str:
    """The faithful LabVIEW VALUE text an unwired case-output tunnel carries
    under "use default if unwired" -- LabVIEW's own runtime substitution,
    never a Python literal (``type_defaults.py``'s forms are codegen-only;
    per the clean-room LAW, a live/faithful surface like the netlist must
    never describe a VI as Python outside the code generator). Only the
    shapes actually observed feeding a case output tunnel are covered here;
    anything else renders the honest ``"?"`` rather than guess.
    """
    if lv_type is None:
        return "?"
    if lv_type.kind == LVTypeKind.PRIMITIVE:
        underlying = lv_type.underlying_type or ""
        if underlying in _INT_UNDERLYING_TYPES:
            return "0"
        if underlying in _FLOAT_UNDERLYING_TYPES:
            return "0.0"
        if underlying in _COMPLEX_UNDERLYING_TYPES:
            return "0+0i"
        if underlying == "Boolean":
            return "False"
        if underlying in ("String", "SubString"):
            return '""'
        return "?"
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        if lv_type.values:
            return next(
                (name for name, ev in lv_type.values.items() if ev.value == 0),
                "0",
            )
        return "0"
    if lv_type.kind == LVTypeKind.ARRAY:
        return "[]"
    return "?"


def _type_default(lv_type: LVType | None) -> DefaultValue:
    label = lv_type.type_descriptor() if lv_type is not None else "?"
    return DefaultValue(literal=_default_literal(lv_type), type_descriptor=label)


def _term_ref(
    node_name: str,
    occurrence: int | None,
    term: Terminal,
) -> NetRef:
    """Build a NetRef for a terminal OWNED by (produced at) ``node_name``.

    Naming rule: a terminal with a resolved display name uses that name as
    ``bare`` -- preferring ``display_name`` (the resolved-def terminal name;
    for an nMux/decompose LIST terminal this is the real LabVIEW field name,
    stamped once graph-wide by ``op_walk.stamp_nmux_lane_names`` at load time
    -- see ``construction.py``) over the codegen ``name``, over a meaningless
    numeric index. An unnamed terminal's ``bare`` is the only place a
    number-terminal shows (``Node[#n].idx``), fully qualified up front since a
    bare index alone would be meaningless.
    """
    label = _terminal_display_name(term)
    terminal = label or str(term.index)
    if label:
        bare = label
    else:
        tag = f"#{occurrence}" if occurrence else ""
        bare = f"{node_name}{tag}.{term.index}"
    return NetRef(node=node_name, terminal=terminal, occurrence=occurrence, bare=bare)


def _resolve_source(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    terminal_id: str,
    build_ctx: _BuildCtx,
) -> NetRef | None:
    """Trace a terminal's incoming wire back to its producing net.

    Generalizes ``describe._resolve_selector`` to return a ``NetRef``
    instead of a formatted string, for reuse by both input-terminal
    resolution and case/loop selector resolution. Returns ``None`` only
    when the terminal has no incoming wire at all -- callers decide how to
    render that (omit, or fall back to the terminal's own name). Never
    fabricates a placeholder value.

    Hops THROUGH a structure's own tunnel terminals (case/loop/sequence
    input and output tunnels): when the wire's source lands on a
    terminal that is itself a tunnel endpoint owned by a structure op,
    jump to the PAIRED terminal on the other side of the tunnel
    (``_paired_tunnel_id`` -- the same outer<->inner pairing
    ``codegen/nodes/case.py``'s ``_bind_input_tunnels``/
    ``_bind_output_tunnels`` and ``CodeGenContext.resolve`` use) and keep
    tracing from there, so a selector/input fed through a tunnel resolves
    to the REAL producer, not the structure itself. ``seen`` guards
    against a cycle (e.g. a shift register feeding back on itself).
    """
    seen: set[str] = set()
    tid = terminal_id
    while True:
        if tid in seen:
            return None
        seen.add(tid)

        sources = graph.incoming_edges(tid)
        if not sources:
            return None
        src: WireEnd = sources[0]

        hit = build_ctx.owner_by_terminal.get(src.terminal_id)
        if hit is not None:
            op, term = hit
            if _is_gamma_output_tunnel(op, term):
                # A case output tunnel's outer terminal is shared by ONE
                # inner terminal PER FRAME -- which frame actually supplies
                # the value is selector-dependent, so this is a genuine
                # multi-producer MERGE, not a single wire. Stop here and
                # name the merge net instead of hopping into whichever frame
                # ``_paired_tunnel_id`` would pick first (finding #1) --
                # ``_build_case_outputs`` defines the merge itself.
                name = _gamma_net_name(op, term, build_ctx)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_eta_output_tunnel(op, term):
                # A loop output tunnel's outer terminal carries the value
                # LEAVING the loop -- an aggregation across every iteration
                # (auto-indexed array, last-value, ...), not one iteration's
                # producer. Stop here and name the merge net instead of
                # hopping via ``_paired_tunnel_id`` into the inner scalar
                # (the loop analogue of the case finding above) --
                # ``_build_loop_outputs`` defines the merge itself.
                name = _eta_net_name(op, term, build_ctx)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_mu_shift_register_read(op, term):
                # A shift register's LEFT terminal (read inside the loop) or
                # RIGHT terminal (rarely read directly from outside) IS the
                # Gated-SSA recurrence itself -- hopping via
                # ``_paired_tunnel_id`` would jump straight to the INIT wire
                # and silently drop the recurrence. Stop here and name the
                # merge net -- ``_build_loop_shift_registers`` defines it.
                name = _mu_net_name(op, term, build_ctx)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_feedback_output_read(op, term):
                # A Feedback Node's output terminal IS the mu recurrence
                # itself -- name the ``fb{k}`` net (defined by the standalone
                # ``NetlistFeedback`` item ``_build_feedback`` emits) rather
                # than the raw ``hiddenFBNode.0`` producer. Loop shift
                # register's standalone-node analogue. Indexed directly (like
                # gamma/eta/mu above): a MASTER Feedback Node always gets an
                # id from ``_assign_sequential_ids`` in ``build_netlist``, so
                # a miss here would mean a real bug, not a legitimate miss.
                name = f"fb{build_ctx.feedback_id_by_uid[_uid_of(op.id)]}"
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            paired = _paired_tunnel_id(op, term)
            if paired is not None and paired not in seen:
                tid = paired
                continue
            node_name = _display_name(op)
            occurrence = build_ctx.occurrence_by_uid.get(_uid_of(op.id))
            return _term_ref(node_name, occurrence, term)

        for t in ctx.inputs:
            if t.id == src.terminal_id:
                bare = t.name or str(t.index)
                return NetRef(node=None, terminal=bare, occurrence=None, bare=bare)

        # A wire fed directly by a constant renders the constant's real
        # VALUE (a literal), not the structural uid.terminal fallback below --
        # a literal has no producing node, so ``node=None`` (same
        # convention as a boundary control) and it always renders bare.
        const = build_ctx.const_by_id.get(src.node_id)
        if const is not None:
            value_str = _const_value_str(const)
            return NetRef(
                node=None,
                terminal=value_str,
                occurrence=None,
                bare=value_str,
            )

        # Structural fallback: identify by the wire source's own carried
        # info (never invent a placeholder like "x").
        node_name = src.name or _uid_of(src.node_id)
        terminal = str(src.index) if src.index is not None else src.terminal_id
        return NetRef(
            node=node_name,
            terminal=terminal,
            occurrence=None,
            bare=f"{node_name}.{terminal}",
        )


def _resolve_or_default(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    build_ctx: _BuildCtx,
    terminal_id: str | None,
    *fallback_terminals: Terminal | None,
) -> NetRef | DefaultValue:
    """Trace ``terminal_id`` via ``_resolve_source``, falling back to the
    type default of the first ``fallback_terminals`` entry that has a
    resolved ``lv_type`` when the trace comes back empty (``terminal_id`` is
    ``None``, meaning the caller has no terminal to even attempt tracing --
    or ``_resolve_source`` itself returns ``None``, meaning the terminal is
    genuinely unwired).

    Shared "trace-then-default" tail duplicated across ``_build_case_outputs``
    (an unwired case-output inner tunnel), ``_build_loop_shift_registers`` (an
    uninitialized shift register), ``_build_loop_outputs`` (an unwired loop
    output tunnel), and ``_build_feedback`` (an unwired Feedback Node
    initializer) -- each caller keeps its own terminal-selection logic (which
    terminal to trace, which terminals to fall back to, in what order); only
    this trace-then-default tail is common. Mirrors ``GammaCase.source``'s
    "use default if unwired" treatment -- LabVIEW's own runtime substitution,
    never fabricated.
    """
    source: NetRef | None = None
    if terminal_id is not None:
        source = _resolve_source(graph, ctx, terminal_id, build_ctx)
    if source is not None:
        return source
    lv_type = next(
        (
            t.lv_type
            for t in fallback_terminals
            if t is not None and t.lv_type is not None
        ),
        None,
    )
    return _type_default(lv_type)


def _input_ref(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    build_ctx: _BuildCtx,
    terminal: Terminal,
) -> NetRef | None:
    """Net feeding an INPUT terminal, or ``None`` when the terminal is
    UNWIRED.

    A netlist lists connections: a dangling connector-pane pin (an optional
    input left at its default in this call) is not an edge and is omitted,
    not rendered as a meaningless bare index. ``op.terminals`` enumerates a
    subVI's whole connector pane, so most pins on any given call are unwired.
    """
    return _resolve_source(graph, ctx, terminal.id, build_ctx)


def _selector_ref(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    build_ctx: _BuildCtx,
    terminal_id: str | None,
) -> NetRef | None:
    """Net gating a case selector / loop stop-condition, or None when there
    is no terminal to trace or its source can't be located."""
    if not terminal_id:
        return None
    return _resolve_source(graph, ctx, terminal_id, build_ctx)


def _build_property_accesses(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    build_ctx: _BuildCtx,
    op: PropertyOperation,
    name: str,
    occurrence: int | None,
) -> list[NetlistPropertyAccess]:
    """One ``NetlistPropertyAccess`` per accessed property, correlated to its
    VALUE terminal via ``op_walk.correlate_property_terminals`` -- the SAME
    correlation ``render/nodes.py::_property_node_glyph`` uses. A property
    whose value terminal can't be correlated (fewer value terminals than
    properties, or an unresolved name) is skipped here -- its terminal still
    renders via the generic ``inputs``/``outputs`` numeric-terminal fallback,
    never a fabricated name.
    """
    accesses: list[NetlistPropertyAccess] = []
    correlated = correlate_property_terminals(
        op.properties,
        op.terminals,
        op.value_terminal_ids,
    )
    for prop, term in correlated:
        if term is None:
            continue
        prop_name = (prop.name or "").strip()
        if not prop_name:
            continue
        if term.direction == "output":
            net = _term_ref(name, occurrence, term)
            accesses.append(
                NetlistPropertyAccess(name=prop_name, direction="read", net=net)
            )
        else:
            net = _input_ref(graph, ctx, build_ctx, term)
            accesses.append(
                NetlistPropertyAccess(name=prop_name, direction="write", net=net)
            )
    return accesses


def _instance_kind(
    op: Operation, *, is_property: bool, is_invoke: bool
) -> NetlistInstanceKind:
    """The Operation-based analogue of ``_instance_kind_gn`` -- off the
    ``Operation`` SUBCLASS. This path feeds only ``render_netlist``/
    ``netlist_to_dict`` (never ``render_lvnet``), so a coarse ``FUNCTION``
    fallback for any kind with no dedicated Operation subclass (a local
    variable, an In Place Element inner node) is harmless here."""
    if isinstance(op, SubVIOperation):
        return NetlistInstanceKind.SUBVI
    if is_property:
        return NetlistInstanceKind.PROPERTY_NODE
    if is_invoke:
        return NetlistInstanceKind.INVOKE_NODE
    if isinstance(op, FormulaOperation):
        return NetlistInstanceKind.FORMULA_NODE
    return NetlistInstanceKind.FUNCTION


def _build_instance(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: Operation,
    build_ctx: _BuildCtx,
) -> NetlistInstance:
    uid = _uid_of(op.id)
    name = _display_name(op)
    occurrence = build_ctx.occurrence_by_uid.get(uid)
    inputs = [
        NetlistTerminalBinding(
            terminal=_component_terminal_name(t),
            type=t.type_descriptor() or "?",
            net=ref,
            default=None,
            inverted=t.inverted,
            pane_rank=t.index,
            lv_type=t.lv_type,
        )
        for t in op.terminals
        if t.direction == "input"
        if (ref := _input_ref(graph, ctx, build_ctx, t)) is not None
    ]
    outputs = [
        NetlistOutput(
            net=_term_ref(name, occurrence, t),
            type=t.type_descriptor() or "?",
            pane_rank=t.index,
            lv_type=t.lv_type,
        )
        for t in op.terminals
        if t.direction == "output"
    ]
    operation = op.operation if isinstance(op, PrimitiveOperation) else None
    object_name: str | None = None
    method_name: str | None = None
    properties: list[NetlistPropertyAccess] = []
    is_property = isinstance(op, PropertyOperation)
    is_invoke = isinstance(op, InvokeOperation)
    if is_property:
        object_name = (op.object_name or "").strip() or None
        properties = _build_property_accesses(
            graph,
            ctx,
            build_ctx,
            op,
            name,
            occurrence,
        )
    elif is_invoke:
        object_name = (op.object_name or "").strip() or None
        method_name = (op.method_name or "").strip() or None
    return NetlistInstance(
        uid=uid,
        name=name,
        occurrence=occurrence,
        inputs=inputs,
        outputs=outputs,
        kind=_instance_kind(op, is_property=is_property, is_invoke=is_invoke),
        operation=operation,
        object_name=object_name,
        method_name=method_name,
        qualified_name=op.qualified_name,
        properties=properties,
    )


def _build_feedback(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: FeedbackOperation,
    build_ctx: _BuildCtx,
) -> NetlistFeedback:
    """Project a Feedback Node MASTER as a standalone mu (see
    ``NetlistFeedback``). ``init`` traces the master's OWN initializer (input)
    terminal; ``recur`` traces the linked write side's (slave's) single input
    terminal -- both via ``_resolve_source``, the same tracer every other net
    uses. ``init`` falls back to the terminal's type default when unwired
    (LabVIEW's own first-call seed, via ``_resolve_or_default``), mirroring
    ``_build_loop_shift_registers``; ``recur`` stays ``None`` when the
    Feedback Node is never written (a faithful state, never defaulted)."""
    k = build_ctx.feedback_id_by_uid[_uid_of(op.id)]

    # init: the master's initializer terminal (the one INPUT terminal),
    # falling back to the initializer's own type, else the output's.
    init_term = next((t for t in op.terminals if t.direction == "input"), None)
    out_term = next((t for t in op.terminals if t.direction == "output"), None)
    init = _resolve_or_default(
        graph,
        ctx,
        build_ctx,
        init_term.id if init_term is not None else None,
        init_term,
        out_term,
    )

    # recur: the written value on the linked write side's rightFeedback input.
    recur: NetRef | None = None
    slave = build_ctx.op_by_uid.get(op.partner_uid or "")
    if slave is not None:
        recur_term = next((t for t in slave.terminals if t.direction == "input"), None)
        if recur_term is not None:
            recur = _resolve_source(graph, ctx, recur_term.id, build_ctx)

    return NetlistFeedback(
        uid=_uid_of(op.id),
        net=f"fb{k}",
        init=init,
        recur=recur,
        delay=op.delay,
    )


def _build_items(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    operations: list[Operation],
    build_ctx: _BuildCtx,
) -> list[NetlistItem]:
    """Walk a list of operations exactly like ``_describe_op_list``
    recurses, projecting each into a ``NetlistInstance`` or
    ``NetlistScope``."""
    items: list[NetlistItem] = []
    for op in operations:
        match op:
            case CaseOperation():
                items.append(_build_case_scope(graph, ctx, op, build_ctx))
            case DisableStructureOperation():
                items.append(_build_disabled_scope(graph, ctx, op, build_ctx))
            case EventOperation():
                items.append(_build_event_scope(graph, ctx, op, build_ctx))
            case LoopOperation():
                items.append(_build_loop_scope(graph, ctx, op, build_ctx))
            case SequenceOperation():
                items.append(_build_sequence_scope(graph, ctx, op, build_ctx))
            case FeedbackOperation():
                # The MASTER becomes one ``fb{k}`` mu item; the write side
                # (slave) is DISSOLVED -- its written value is captured as the
                # master's ``recur`` in ``_build_feedback``, so emitting it as
                # its own instance would double-count the Feedback Node.
                if op.is_master:
                    items.append(_build_feedback(graph, ctx, op, build_ctx))
            case _:
                items.append(_build_instance(graph, ctx, op, build_ctx))
                if op.inner_nodes:
                    items.extend(_build_items(graph, ctx, op.inner_nodes, build_ctx))
    return items


def _selector_lv_type(op: Operation, selector_terminal: str | None) -> LVType | None:
    """The selector terminal's resolved type, by id lookup in ``op.terminals``
    -- ``None`` when there is no selector terminal (disable structures) or its
    type didn't resolve. Feeds ``_selector_label``'s enum-name/error-cluster/
    string formatting, same lookup ``render/scene.py::_frame_info`` does via
    ``graph.get_terminal`` (here we already have the op's own terminals, no
    graph round-trip needed)."""
    if not selector_terminal:
        return None
    for t in op.terminals:
        if t.id == selector_terminal:
            return t.lv_type
    return None


def _build_case_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: CaseOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    selector = _selector_ref(graph, ctx, build_ctx, op.selector_terminal)
    passthrough = _has_output_tunnel(op)
    lv_type = _selector_lv_type(op, op.selector_terminal)
    is_error = bool(lv_type and _is_error_cluster(lv_type))
    frames = [
        NetlistFrame(
            label=_selector_label(frame, lv_type, is_error),
            value=str(frame.selector_value),
            is_default=frame.is_default,
            body=_build_items(graph, ctx, frame.operations, build_ctx),
            passthrough=passthrough,
        )
        for frame in op.frames
    ]
    case_id = build_ctx.case_id_by_uid[_uid_of(op.id)]
    outputs: list[GammaMerge | MuMerge | EtaMerge] = [
        *_build_case_outputs(graph, ctx, op, build_ctx, case_id, selector, frames),
    ]
    return NetlistScope(
        uid=_uid_of(op.id),
        kind="case",
        selector=selector,
        frames=frames,
        outputs=outputs,
    )


def _build_case_outputs(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: CaseOperation,
    build_ctx: _BuildCtx,
    case_id: int,
    selector: NetRef | None,
    frames: list[NetlistFrame],
) -> list[GammaMerge]:
    """One ``GammaMerge`` per output tunnel on this case -- Gated-SSA's
    gamma: the case's own selector plus one (frame_key, source) pair per
    frame (see the module docstring's finding #1). ``frames`` supplies each
    frame's already-resolved display label/``is_default`` -- zipped against
    ``op.frames`` by position since both were built from that SAME list, in
    the same order. ``case_id`` is computed once by the caller
    (``_build_case_scope``), matching the loop scope's ``loop_id`` convention
    (see ``_build_loop_shift_registers``/``_build_loop_outputs``).
    """
    outers = _case_output_tunnel_outers(op)
    gammas: list[GammaMerge] = []
    for k, outer in enumerate(outers):
        cases: list[GammaCase] = []
        for raw_frame, nl_frame in zip(op.frames, frames, strict=True):
            inner = next(
                (
                    t
                    for t in op.terminals
                    if isinstance(t, TunnelTerminal)
                    and t.boundary == "inner"
                    and t.paired_id == outer.id
                    and t.frame == raw_frame.selector_value
                ),
                None,
            )
            # Unwired inner tunnel (or, defensively, no inner terminal found
            # at all) -- LabVIEW's "use default if unwired" routes the
            # tunnel's own TYPE default through; never omit the frame.
            source = _resolve_or_default(
                graph,
                ctx,
                build_ctx,
                inner.id if inner is not None else None,
                inner,
                outer,
            )
            frame_key = "default" if nl_frame.is_default else nl_frame.label
            cases.append(GammaCase(frame_key=frame_key, source=source))
        gammas.append(
            GammaMerge(
                net=f"case{case_id}.out{k}",
                selector=selector,
                cases=cases,
            )
        )
    return gammas


def _build_disabled_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: DisableStructureOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    passthrough = _has_output_tunnel(op)
    frames = [
        # NOT run through _selector_label: a disable structure has no
        # runtime selector, so CaseFrame.selector_value already IS the
        # display text ("Enabled"/"Disabled"/a symbol condition/"Frame N",
        # see parser/nodes/disable.py). Its ``is_default`` also means
        # something different here -- "this is the active/compiled-in
        # frame", not "the case's catch-all default" -- so it must NOT hit
        # _selector_label's ``is_default -> "Default"`` branch (that
        # branch is safe for CaseOperation only because the parser always
        # sets a case's default frame's selector_value to the literal
        # string "Default" -- parser/nodes/case.py -- which disable frames
        # never do). Same reasoning render/scene.py::_frame_info documents
        # for why it leaves disable frames out of frame_labels.
        NetlistFrame(
            label=str(frame.selector_value),
            value=str(frame.selector_value),
            is_default=frame.is_default,
            body=_build_items(graph, ctx, frame.operations, build_ctx),
            passthrough=passthrough,
        )
        for frame in op.frames
    ]
    return NetlistScope(
        uid=_uid_of(op.id),
        kind="disabled",
        selector=None,
        frames=frames,
        disable_kind=op.disable_kind,
    )


def _build_event_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: EventOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    """An Event Structure's frames -- one per registered event.

    NOT run through ``_selector_label``: there's no runtime selector (the
    active frame is chosen by whichever event fires), so ``EventFrame.
    event_label`` already IS the display text (LabVIEW's own bracketed
    ``[N] EventName`` rendering for the frame it last displayed, a
    reconstructed label from that frame's ``EventSpec`` for every other
    frame, degrading to an honest ``"[N]"`` when unresolvable -- see
    parser/nodes/event.py). Same reasoning as ``_build_disabled_scope``.
    """
    passthrough = _has_output_tunnel(op)
    frames = [
        NetlistFrame(
            label=frame.event_label,
            value=frame.event_label,
            is_default=False,
            body=_build_items(graph, ctx, frame.operations, build_ctx),
            passthrough=passthrough,
        )
        for frame in op.frames
    ]
    return NetlistScope(
        uid=_uid_of(op.id),
        kind="event",
        selector=None,
        frames=frames,
    )


def _build_sequence_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: SequenceOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    frames = [
        NetlistFrame(
            label=str(frame.index),
            value=str(frame.index),
            is_default=False,
            body=_build_items(graph, ctx, frame.operations, build_ctx),
            # Sequence frames are never "pass-through": a flat-sequence output
            # tunnel is ASSIGNED in exactly one frame (unlike a case, where an
            # unwired tunnel routes the input through), so an empty frame here
            # genuinely has no value flowing — ``(empty)``, not ``(pass-through)``.
            passthrough=False,
        )
        for frame in op.frames
    ]
    return NetlistScope(
        uid=_uid_of(op.id),
        kind="sequence",
        selector=None,
        frames=frames,
        sequence_is_flat=op.is_flat,
    )


def _build_loop_shift_registers(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: LoopOperation,
    build_ctx: _BuildCtx,
    loop_id: int,
    term_by_id: dict[str, Terminal],
) -> list[MuMerge]:
    """One ``MuMerge`` per shift register on this loop (Gated-SSA mu, see
    ``MuMerge``'s docstring) -- ``init``/``recur`` resolved from the SAME
    ``lSR``/``rSR`` pairing ``op_walk._loop_shift_register_pairs`` gives,
    which IS the canonical 0-based ``shift{k}`` numbering (matches
    ``_mu_net_name``, so a consumer's resolved reference and this scope's
    own definition line always agree). ``loop_id``/``term_by_id`` are
    computed once by the caller (``_build_loop_scope``) and shared with
    ``_build_loop_outputs``.
    """
    merges: list[MuMerge] = []
    for k, (lsr, rsr) in enumerate(_loop_shift_register_pairs(op)):
        # Uninitialized (or defensively unresolved) SR -- LabVIEW seeds it
        # to the type default on the VI's first call; never fabricated,
        # mirroring GammaCase's own unwired-tunnel fallback.
        outer_t = term_by_id.get(lsr.outer_terminal_uid)
        inner_t = term_by_id.get(lsr.inner_terminal_uid)
        init = _resolve_or_default(
            graph,
            ctx,
            build_ctx,
            lsr.outer_terminal_uid if lsr.sr_initialized else None,
            outer_t,
            inner_t,
        )
        recur = (
            _resolve_source(graph, ctx, rsr.inner_terminal_uid, build_ctx)
            if rsr is not None
            else None
        )
        merges.append(MuMerge(net=f"loop{loop_id}.shift{k}", init=init, recur=recur))
    return merges


def _build_loop_outputs(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: LoopOperation,
    build_ctx: _BuildCtx,
    loop_id: int,
    term_by_id: dict[str, Terminal],
) -> list[EtaMerge]:
    """One ``EtaMerge`` per output tunnel on this loop (Gated-SSA eta, see
    ``EtaMerge``'s docstring) -- ``value`` is the inner per-iteration
    producer, ``index_mode`` the tunnel's own ``TunnelMode``
    (``_eta_index_mode``). Numbering matches ``_eta_net_name``
    (``_loop_output_tunnel_outers``, the SAME ordering), so a consumer's
    resolved reference and this scope's own definition line always agree.
    ``loop_id``/``term_by_id`` are computed once by the caller
    (``_build_loop_scope``) and shared with ``_build_loop_shift_registers``.
    """
    tunnel_by_outer = {
        t.outer_terminal_uid: t for t in op.tunnels if t.tunnel_type == "lpTun"
    }
    outers = _loop_output_tunnel_outers(op)
    merges: list[EtaMerge] = []
    for k, outer in enumerate(outers):
        tunnel = tunnel_by_outer[outer.id]
        # Unwired/broken inner tunnel -- never omit; substitute the type
        # default like GammaCase's own unwired-tunnel fallback.
        inner_t = term_by_id.get(tunnel.inner_terminal_uid)
        value = _resolve_or_default(
            graph,
            ctx,
            build_ctx,
            tunnel.inner_terminal_uid,
            inner_t,
            outer,
        )
        merges.append(
            EtaMerge(
                net=f"loop{loop_id}.out{k}",
                index_mode=_eta_index_mode(tunnel.mode),
                conditional=tunnel.conditional,
                value=value,
            )
        )
    return merges


def _build_loop_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    op: LoopOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    kind = "while" if op.loop_type == "whileLoop" else "for"
    selector = _selector_ref(graph, ctx, build_ctx, op.stop_condition_terminal)
    body = _build_items(graph, ctx, op.inner_nodes, build_ctx)
    frame = NetlistFrame(
        label="",
        value="",
        is_default=False,
        body=body,
        passthrough=_has_output_tunnel(op),
    )
    tunnel_info = [
        NetlistTunnelInfo(
            tunnel_type=t.tunnel_type,
            mode=t.mode.value if t.mode is not None else None,
            sr_initialized=t.sr_initialized,
            sr_stack_depth=t.sr_stack_depth,
        )
        for t in op.tunnels
    ]
    loop_id = build_ctx.loop_id_by_uid[_uid_of(op.id)]
    term_by_id = {t.id: t for t in op.terminals}
    outputs: list[GammaMerge | MuMerge | EtaMerge] = [
        *_build_loop_shift_registers(
            graph,
            ctx,
            op,
            build_ctx,
            loop_id,
            term_by_id,
        ),
        *_build_loop_outputs(graph, ctx, op, build_ctx, loop_id, term_by_id),
    ]
    return NetlistScope(
        uid=_uid_of(op.id),
        kind=kind,
        selector=selector,
        frames=[frame],
        parallel=op.parallel,
        parallel_static_workers=op.parallel_static_workers,
        tunnels=tunnel_info,
        outputs=outputs,
    )


# ============================================================
# Component declarations (## Components / NetlistModule.components)
# ============================================================
#
# The Verilog-module / VHDL-entity half of the netlist: where ``body``
# instantiates each call, ``components`` declares each DISTINCT callee's
# typed port interface exactly once. SubVIs reuse ``op_walk._subvi_ports``
# (the same signature lookup describe.py's ``## Dependencies`` uses);
# primitives/nMux/cpdArith have no ``.vi`` interface to look up, so their
# ports are synthesized from a representative instance's own terminals.

_STRUCTURE_OPERATION_TYPES = (
    CaseOperation,
    LoopOperation,
    SequenceOperation,
    DisableStructureOperation,
    InPlaceOperation,
    EventOperation,
)


def _is_subvi_call(op: Operation) -> bool:
    """A SubVI call: a ``kind='vi'`` op with a name. (``describe.
    _collect_subvi_names`` applies the analogous filter keyed on the equivalent
    ``qualified_name`` — which is always set whenever ``name`` is.)"""
    return op.kind == "vi" and bool(op.name)


def _component_identity(op: Operation) -> tuple[object, ...]:
    """Stable identity for grouping repeated instances of the "same"
    primitive-like leaf component.

    ``primResID`` identifies a real LabVIEW primitive (``class="prim"``,
    e.g. Equal?/Not) unambiguously. Node classes with no primResID of
    their own (cpdArith, nMux, the array/string ops) share a bare
    ``node_type`` -- cpdArith's ``operation`` field (add/or/...) further
    disambiguates those, since ``op.name`` is the same generic "Compound
    Arithmetic" for every operation. Two instances landing in the same
    group are still verified to have the SAME synthesized interface before
    being merged -- see ``_dedupe_primitive_group``.
    """
    prim_res_id = op.primResID if isinstance(op, PrimitiveOperation) else None
    operation = op.operation if isinstance(op, PrimitiveOperation) else None
    return (op.node_type or "unknown", prim_res_id, operation)


def _component_terminal_name(term: Terminal) -> str:
    """Terminal name for one of ``op``'s own terminals, in a synthesized
    (not wire-derived) component declaration.

    ``display_name or name or str(index)``, per the netlist naming rule --
    for an nMux/decompose LIST terminal, ``display_name`` is the real
    LabVIEW field name, stamped once graph-wide at load time (see
    ``op_walk.stamp_nmux_lane_names``).
    """
    return _terminal_display_name(term) or str(term.index)


def _synthesize_ports(
    op: Operation,
) -> tuple[list[ComponentPort], list[ComponentPort]]:
    """Typed (inputs, outputs) for a primitive-like leaf op, from its OWN
    terminals -- there is no ``.vi`` connector pane to read a signature
    from. Includes error-cluster terminals: they are real ports on the
    node, matching every other interface in this module (subVI signatures,
    the VI's own signature line)."""
    ins: list[ComponentPort] = []
    outs: list[ComponentPort] = []
    for t in sorted(op.terminals, key=lambda t: t.index):
        port = ComponentPort(
            name=_component_terminal_name(t),
            type=t.lv_type.type_descriptor() if t.lv_type else "Any",
        )
        (ins if t.direction == "input" else outs).append(port)
    return ins, outs


def _port_signature(ins: list[ComponentPort], outs: list[ComponentPort]) -> str:
    """Canonical string form of a synthesized interface, used only to
    detect whether two same-identity instances genuinely differ (a real
    polymorphic collision) -- never rendered."""
    in_str = "|".join(f"{p.name}:{p.type}" for p in ins)
    out_str = "|".join(f"{p.name}:{p.type}" for p in outs)
    return f"{in_str}=>{out_str}"


def _dedupe_primitive_group(
    instances: list[tuple[Operation, list[ComponentPort], list[ComponentPort]]],
) -> list[NetlistComponent]:
    """Collapse same-identity instances sharing an interface into ONE
    ``NetlistComponent``. When instances of the same identity genuinely
    have DIFFERENT typed interfaces (polymorphic dispatch resolved
    differently per call site), keep them distinct with a `` (n)``
    disambiguator instead of silently merging -- first-seen order, so the
    result is deterministic given the deterministic node order upstream.
    """
    by_signature: dict[
        str, tuple[Operation, list[ComponentPort], list[ComponentPort]]
    ] = {}
    order: list[str] = []
    for op, ins, outs in instances:
        sig = _port_signature(ins, outs)
        if sig not in by_signature:
            by_signature[sig] = (op, ins, outs)
            order.append(sig)

    if len(order) == 1:
        op, ins, outs = by_signature[order[0]]
        return [NetlistComponent(name=_display_name(op), inputs=ins, outputs=outs)]

    result: list[NetlistComponent] = []
    for i, sig in enumerate(order, start=1):
        op, ins, outs = by_signature[sig]
        base = _display_name(op)
        name = base if i == 1 else f"{base} ({i})"
        result.append(NetlistComponent(name=name, inputs=ins, outputs=outs))
    return result


def _build_components(
    graph: InMemoryVIGraph,
    flat: list[Operation],
) -> list[NetlistComponent]:
    """Every distinct component (subVI or primitive-like leaf) actually
    used anywhere in the VI, sorted by declared name. Structures
    (case/loop/sequence/disabled/IPES) are containers, not components, and
    are excluded -- ``## Netlist`` already shows their scopes.

    ``flat`` is the whole VI's already-flattened operation list
    (``_walk_flat``) -- ``build_netlist`` flattens once and passes the same
    list here and to every other indexing helper.
    """
    subvi_order: list[str] = []
    seen_subvi: set[str] = set()
    subvi_reps: dict[str, Operation] = {}
    groups: dict[
        tuple[object, ...],
        list[tuple[Operation, list[ComponentPort], list[ComponentPort]]],
    ] = {}

    for op in flat:
        if isinstance(op, _STRUCTURE_OPERATION_TYPES):
            continue
        if _is_subvi_call(op):
            # Group by the callee's QUALIFIED identity, not the bare name, so two
            # classes' same-named methods (A.lvclass:run.vi vs B.lvclass:run.vi)
            # stay distinct components instead of collapsing into one. resolve_vi_name
            # (via _subvi_ports below) accepts the qualified form.
            key = op.qualified_name or op.name
            assert key is not None  # narrowed by _is_subvi_call
            if key not in seen_subvi:
                seen_subvi.add(key)
                subvi_order.append(key)
                subvi_reps[key] = op
            continue
        if not isinstance(op, PrimitiveOperation):
            continue
        ins, outs = _synthesize_ports(op)
        key = _component_identity(op)
        groups.setdefault(key, []).append((op, ins, outs))

    components: list[NetlistComponent] = []
    for key in subvi_order:
        ports = _subvi_ports(graph, key)
        if ports is not None and (ports[0] or ports[1]):
            ins, outs = ports
        else:
            # Signature unavailable (VI not loaded and no vilib entry), or
            # the front panel has no ports at all. Synthesize from the
            # actual call so ## Components declares exactly what ##
            # Netlist wires: both derive from the same call node's
            # terminals, so they can't disagree.
            ins, outs = _synthesize_ports(subvi_reps[key])
        components.append(NetlistComponent(name=key, inputs=ins, outputs=outs))
    for instances in groups.values():
        components.extend(_dedupe_primitive_group(instances))

    _disambiguate_cross_group_names(components)
    components.sort(key=lambda c: c.name)
    return components


def _disambiguate_cross_group_names(components: list[NetlistComponent]) -> None:
    """Second disambiguation pass, ACROSS identity groups (mutates in place).

    ``_dedupe_primitive_group`` only disambiguates repeats WITHIN one
    identity group (the same primitive, different resolved types). Two
    DIFFERENT primitives can still share a generic display name -- e.g. an
    Add and an Or ``cpdArith`` node are different identities (``operation``
    differs) but both display as "Compound Arithmetic" (``op.name`` doesn't
    vary by operation). Catch that cross-group case here so two distinct
    components never render under the identical name -- same `` (n)``
    numbered disambiguator, applied in the groups' existing (deterministic)
    order.
    """
    by_name: dict[str, list[NetlistComponent]] = {}
    for c in components:
        by_name.setdefault(c.name, []).append(c)
    for name, group in by_name.items():
        for i, c in enumerate(group[1:], start=2):
            c.name = f"{name} ({i})"


def _build_class_context(
    graph: InMemoryVIGraph,
    ctx: VIContext,
) -> ClassContext | None:
    """The JSON IR's class-context shape -- the shared
    ``queries.collect_class_context`` returned DIRECTLY (no netlist-local
    wrapper dataclass). describe.py's ``## Class`` section
    (``_describe_class_context``) builds on the SAME collector, so the two
    surfaces can't drift on what "the class context" means."""
    return collect_class_context(graph, ctx)


def build_netlist(graph: InMemoryVIGraph, vi_name: str) -> NetlistModule:
    """Walk a VI's operations into a ``NetlistModule`` IR.

    Deterministic: ``ctx.operations`` (and every nested ``inner_nodes`` /
    ``frames[].operations``) are already produced in ``_node_order_key``
    order by the graph layer, so no re-sorting is needed here -- only the
    occurrence counter, which walks in that same given order.
    """
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)
    root_ops = ctx.operations
    # Flattened once, reused everywhere a flat node-order walk is needed
    # (each id-assignment pass, ``op_by_uid``, ``_build_components``) --
    # previously each of those re-walked the tree independently.
    flat = _walk_flat(root_ops)

    build_ctx = _BuildCtx(
        occurrence_by_uid=_assign_occurrences(flat),
        const_by_id={c.id: c for c in ctx.constants},
        case_id_by_uid=_assign_sequential_ids(
            flat,
            lambda op: isinstance(op, CaseOperation),
        ),
        loop_id_by_uid=_assign_sequential_ids(
            flat,
            lambda op: isinstance(op, LoopOperation),
        ),
        feedback_id_by_uid=_assign_sequential_ids(
            flat,
            lambda op: isinstance(op, FeedbackOperation) and op.is_master,
        ),
        op_by_uid={op.id: op for op in flat},
        owner_by_terminal=index_terminal_owners(root_ops),
    )

    inputs = [
        NetlistBoundaryInput(
            name=t.name or "input",
            type_descriptor=t.lv_type.type_descriptor() if t.lv_type else "Any",
            lv_type=t.lv_type,
        )
        for t in ctx.inputs
    ]
    outputs = [
        BoundaryOutput(
            name=t.name or "output",
            type_descriptor=t.lv_type.type_descriptor() if t.lv_type else "Any",
            lv_type=t.lv_type,
            # An indicator is a sink; its incoming edge traces to the producing
            # net exactly like an input terminal does.
            source=_resolve_source(graph, ctx, t.id, build_ctx),
        )
        for t in ctx.outputs
    ]

    # The authored connector pane (canonical order already applied by get_inputs/
    # get_outputs): input terminals first, then outputs -- each self-tags
    # direction.
    connector_pane = ConnectorPane(
        pattern_id=ctx.connector_pattern_id,
        terminals=[_pane_terminal(t, "input") for t in ctx.inputs]
        + [_pane_terminal(t, "output") for t in ctx.outputs],
    )

    body = _build_items(graph, ctx, root_ops, build_ctx)
    components = _build_components(graph, flat)

    return NetlistModule(
        vi_name=vi_name,
        inputs=inputs,
        outputs=outputs,
        body=body,
        components=components,
        properties=ctx.properties,
        health=ctx.health,
        class_context=_build_class_context(graph, ctx),
        connector_pane=connector_pane,
    )


def _pane_terminal(
    t: Terminal, direction: str, *, off_pane: bool = False
) -> ConnectorPaneTerminal:
    """One interface terminal -> its ``ConnectorPaneTerminal`` contract record.

    ``off_pane`` forces ``index=None`` regardless of ``t.index`` -- an
    off-pane ``FPTerminal`` (a front-panel control/indicator not on the
    connector pane, ``is_public=False``) is stamped ``index=0`` at
    construction time (``construction.py``: ``slot_index if slot_index is
    not None else 0``) as a harmless placeholder, never a real pane slot,
    so passing it through unchanged would render a fabricated ``@0``. lvnet
    §2's off-pane row already renders correctly with no ``@`` at all once
    ``index`` is genuinely ``None`` (``render_lvnet._lvnet_pane_index_suffix``).
    """
    return ConnectorPaneTerminal(
        name=t.name or direction,
        # FAITHFUL label (family-word fallback when unresolved) -- same as
        # describe's ## Inputs/## Outputs, never the codegen "Any".
        type=t.type_label(),
        direction=direction,
        index=None if off_pane else t.index,
        wiring_requirement=requirement_state(t, direction),
        default=t.default_value,
        lv_type=t.lv_type,
    )


def _off_pane_terminals(
    graph: InMemoryVIGraph, vi_name: str, direction: str
) -> list[Terminal]:
    """Every front-panel control/indicator NOT on this VI's connector pane
    (``FPTerminal.is_public is False``) -- the off-pane front-panel objects
    ``get_inputs``/``get_outputs``' default ``public_only=True`` filters out
    of ``VIContext.inputs``/``.outputs`` (and so, previously, out of the
    ``front-panel :`` section entirely: see the module's off-pane-controls
    finding). Queried with ``public_only=False`` (the full front-panel
    control/indicator list) and filtered down to just the off-pane subset
    here, rather than adding a THIRD ``get_*`` variant to ``queries.py`` --
    ``is_public`` is the one fact this needs that ``ordered_interface``'s
    already-public-filtered getters don't expose.

    A non-``FPTerminal`` (never occurs for a VI's own boundary, but
    defensively) is treated as on-pane -- ``FPTerminal.is_public`` is the
    ONLY source of this fact, so anything without it can't be off-pane.
    """
    if direction == "input":
        terms = graph.get_inputs(vi_name, public_only=False)
    else:
        terms = graph.get_outputs(vi_name, public_only=False)
    return [t for t in terms if isinstance(t, FPTerminal) and not t.is_public]


# ============================================================
# build_netlist_from_graph -- PHASE 1 (byte-parity with build_netlist)
# ============================================================
#
# A SIBLING builder that walks the ``GraphNode`` graph directly -- never the
# ``Operation`` projection -- and must reproduce ``build_netlist``'s output
# BYTE-FOR-BYTE (see ``tests/test_netlist_from_graph_parity.py``). Every
# helper below has a ``_gn`` suffix (or is new) to keep it visually distinct
# from its Operation-based twin above; the model dataclasses and both
# serializers (``render_netlist``/``netlist_to_dict``) are shared VERBATIM --
# nothing below this point defines a new IR shape, only a new way to build the
# existing one.
#
# Node-type dispatch mirrors the Operation-based ``match`` statements using
# the ``GraphNode`` hierarchy (``graph/models.py``) instead: ``CaseOperation``
# -> ``CaseStructureNode``, ``LoopOperation`` -> ``LoopNode``,
# ``SequenceOperation`` -> ``SequenceNode``, ``DisableStructureOperation`` ->
# ``DisableStructureNode``, ``EventOperation`` -> ``EventStructureNode``,
# ``InPlaceOperation`` -> ``InPlaceNode``, a SubVI call -> ``VINode`` (with
# ``id != vi``), and primitive/property/invoke/Feedback-Node all share the one
# ``PrimitiveNode`` graph type (discriminated by ``.properties``/
# ``.method_name`` / ``graph.get_feedback_info``, mirroring
# ``operations.py::_build_operation``'s own dispatch priority).
#
# Two facts live ONLY as extra networkx node attributes, never on the
# Pydantic node models (see ``construction.py``) -- reached via the two small
# accessors added to ``queries.py`` for this builder:
# ``graph.get_feedback_info(node_id)`` (Feedback Node master/slave link +
# delay) and ``graph.get_poser_uid(node_id)`` (IPES decompose/recompose
# pairing).


_STRUCTURE_NODE_TYPES = (
    CaseStructureNode,
    LoopNode,
    SequenceNode,
    DisableStructureNode,
    InPlaceNode,
    EventStructureNode,
)

# Case/Sequence/Disable/Event -- structures whose inner nodes are grouped by
# FRAME (``node.frames`` + ``node.children`` filtered by ``child.frame``),
# mirroring ``_walk_flat``'s branch to ``frame.operations`` for these same
# four Operation types (see that function's SHARED INVARIANT docstring).
_FRAME_STRUCTURE_TYPES = (
    CaseStructureNode,
    SequenceNode,
    DisableStructureNode,
    EventStructureNode,
)


@dataclass(frozen=True)
class _GraphBuildCtx:
    """The graph-native analogue of ``_BuildCtx`` -- same id spaces
    (occurrence/feedback), keyed identically by trailing node UID, but with
    no ``op_by_uid``/``owner_by_terminal`` (the graph builder reaches a
    wire's producing node directly via ``graph.get_graph_node``/
    ``graph.get_terminal`` -- see ``_owning_node_gn`` -- instead of a
    precomputed Operation-tree index).

    Phase 3: no ``case_id_by_uid``/``loop_id_by_uid`` here -- a case/loop's
    own stable BD uid (``_uid_of(node.id)``, the SAME id ``NetlistScope.uid``
    already carries) names its merge nets directly now (see
    ``_tunnel_net_name_gn``/``_mu_net_name_gn``), so the small per-structure
    sequential counter ``_BuildCtx`` (the OLD Operation-based builder) still
    needs is never computed on this path.

    ``constant_occurrence_by_uid`` is Phase A's own id space (lvnet
    ``NetlistConstant`` only): the ``#n`` disambiguator for a LABELED
    constant whose ``label`` repeats within the VI -- keyed by the
    constant's own FULL qualified id (matching ``const_by_id``'s key, NOT
    the trailing uid the other maps use), built once over ``ctx.constants``
    via ``_assign_constant_occurrences`` (see there).
    """

    occurrence_by_uid: dict[str, int]
    const_by_id: dict[str, Constant]
    feedback_id_by_uid: dict[str, int]
    constant_occurrence_by_uid: dict[str, int]


def _display_name_gn(node: AnyGraphNode) -> str:
    """The graph-node analogue of ``_display_name`` -- same fallback chain
    (``name`` -> the node-type's human word -> ``"Node"``)."""
    node_word = get_display_name(node.node_type) if node.node_type else None
    return node.name or node_word or "Node"


def _has_output_tunnel_gn(node: AnyGraphNode) -> bool:
    """The graph-node analogue of ``op_walk._has_output_tunnel``."""
    return any(t.direction == "output" for t in node.terminals)


def _tunnels_from_terminals_gn(terminals: list[Terminal]) -> list[Tunnel]:
    """Reconstruct ``Tunnel`` objects from a structure's own terminal
    metadata -- a deliberate, purely-terminal-based DUPLICATE of
    ``OperationsMixin._tunnels_from_terminals`` (that method is a
    ``@staticmethod`` operating ONLY on ``terminals: list[Terminal]``, no
    Operation involved -- but importing ``InMemoryVIGraph`` for real at
    module level here would add a real (non-``TYPE_CHECKING``) import this
    module has deliberately avoided; duplicating this small, pure helper is
    cheaper than that risk). Keep in sync with that method if the tunnel
    reconstruction rule ever changes.
    """
    tunnels: list[Tunnel] = []
    seen_pairs: set[tuple[str, str]] = set()
    for term in terminals:
        if not isinstance(term, TunnelTerminal):
            continue
        if not term.tunnel_type or not term.paired_id:
            continue
        if term.boundary == "outer":
            outer_uid = term.id
            inner_uid = term.paired_id
        elif term.boundary == "inner":
            outer_uid = term.paired_id
            inner_uid = term.id
        else:
            continue
        pair_key = (outer_uid, inner_uid)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        tunnels.append(
            Tunnel(
                outer_terminal_uid=outer_uid,
                inner_terminal_uid=inner_uid,
                tunnel_type=term.tunnel_type,
                mode=term.mode,
                conditional=term.conditional,
                sr_initialized=term.sr_initialized,
                sr_stack_depth=term.sr_stack_depth,
            )
        )
    return tunnels


def _case_output_tunnel_outers_gn(node: AnyGraphNode) -> list[Terminal]:
    """The graph-node analogue of ``op_walk._case_output_tunnel_outers``."""
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.boundary == "outer"
        and t.direction == "output"
    ]


def _is_gamma_output_tunnel_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """The graph-node analogue of ``op_walk._is_gamma_output_tunnel`` --
    finds the ">1 paired inner" shape directly off ``node.terminals``
    (inner tunnel terminals whose ``paired_id`` names this outer), rather
    than off a reconstructed ``op.tunnels`` list."""
    if not isinstance(node, CaseStructureNode) or not isinstance(term, TunnelTerminal):
        return False
    if term.boundary != "outer" or term.direction != "output":
        return False
    inners = {
        t.id
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.boundary == "inner"
        and t.paired_id == term.id
    }
    return len(inners) > 1


def _loop_output_tunnel_outers_gn(node: AnyGraphNode) -> list[Terminal]:
    """The graph-node analogue of ``op_walk._loop_output_tunnel_outers``."""
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.tunnel_type == "lpTun"
        and t.boundary == "outer"
        and t.direction == "output"
    ]


def _is_eta_output_tunnel_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """The graph-node analogue of ``op_walk._is_eta_output_tunnel``."""
    if not isinstance(node, LoopNode) or not isinstance(term, TunnelTerminal):
        return False
    return (
        term.tunnel_type == "lpTun"
        and term.boundary == "outer"
        and term.direction == "output"
    )


def _is_mu_shift_register_read_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """The graph-node analogue of ``op_walk._is_mu_shift_register_read``."""
    if not isinstance(node, LoopNode) or not isinstance(term, TunnelTerminal):
        return False
    if term.tunnel_type == "lSR" and term.boundary == "inner":
        return True
    return term.tunnel_type == "rSR" and term.boundary == "outer"


def _loop_shift_register_pairs_gn(
    node: AnyGraphNode,
) -> list[tuple[Tunnel, Tunnel | None]]:
    """The graph-node analogue of ``op_walk._loop_shift_register_pairs``."""
    tunnels = _tunnels_from_terminals_gn(node.terminals)
    lsrs = [t for t in tunnels if t.tunnel_type == "lSR"]
    rsrs = [t for t in tunnels if t.tunnel_type == "rSR"]
    return [(lsr, rsrs[i] if i < len(rsrs) else None) for i, lsr in enumerate(lsrs)]


def _paired_tunnel_id_gn(term: Terminal) -> str | None:
    """The graph-node analogue of ``op_walk._paired_tunnel_id`` -- reads
    ``TunnelTerminal.paired_id`` directly rather than re-deriving it from a
    reconstructed ``op.tunnels`` list. Equivalent to that lookup for every
    terminal actually reached here: a >1-paired case output-tunnel OUTER is
    always intercepted earlier by ``_is_gamma_output_tunnel_gn`` (same for
    the loop eta/mu equivalents), so by the time this is called ``term`` is
    guaranteed single-paired and ``paired_id`` alone is exact (see
    ``_tunnels_from_terminals_gn``: an outer's reconstructed pairing IS
    ``term.paired_id`` verbatim; only an inner's REVERSE lookup needs the
    full tunnel table, and ``term.paired_id`` already carries that too)."""
    if not isinstance(term, TunnelTerminal):
        return None
    return term.paired_id


def _tunnel_net_name_gn(
    node: AnyGraphNode,
    term: Terminal,
    outers_fn: Callable[[AnyGraphNode], list[Terminal]],
    prefix: str,
) -> str:
    """The graph-node analogue of ``_tunnel_net_name`` -- Phase 3: the
    structure's own stable BD uid (``_uid_of``, the SAME id
    ``NetlistScope.uid`` already uses) names the net directly, replacing the
    small per-structure sequential counter (``case_id_by_uid``/
    ``loop_id_by_uid``, still used by the OLD Operation-based ``build_
    netlist`` -- this graph-node path never reads those maps anymore)."""
    uid = _uid_of(node.id)
    outers = outers_fn(node)
    k = next(i for i, t in enumerate(outers) if t.id == term.id)
    return f"{prefix}_{uid}.out{k}"


def _gamma_net_name_gn(node: AnyGraphNode, term: Terminal) -> str:
    return _tunnel_net_name_gn(node, term, _case_output_tunnel_outers_gn, "case")


def _eta_net_name_gn(node: AnyGraphNode, term: Terminal) -> str:
    return _tunnel_net_name_gn(node, term, _loop_output_tunnel_outers_gn, "loop")


def _mu_net_name_gn(node: AnyGraphNode, term: Terminal) -> str:
    """Phase 3: ``loop_<uid>.shift<k>`` -- see ``_tunnel_net_name_gn``."""
    loop_uid = _uid_of(node.id)
    pairs = _loop_shift_register_pairs_gn(node)
    k = next(
        i
        for i, (lsr, rsr) in enumerate(pairs)
        if lsr.inner_terminal_uid == term.id
        or (rsr is not None and rsr.outer_terminal_uid == term.id)
    )
    return f"loop_{loop_uid}.shift{k}"


# Frame-only structures (sequence/disabled/event, ``_FRAME_STRUCTURE_TYPES``
# minus ``CaseStructureNode``) never carry their own ``GammaMerge``/
# ``EtaMerge`` model (``NetlistScope.outputs`` stays empty for these kinds --
# see its own docstring) -- but a consumer downstream of one of their output
# tunnels still needs a net name that recovers the STRUCTURE's identity, the
# same way ``case_<uid>.out<k>``/``loop_<uid>.out<k>`` do. The prefix is the
# structure's own ``NetlistScope.kind`` string verbatim (``sequence``/
# ``disabled``/``event``) -- unlike loop, which generalizes its two kinds
# (``for``/``while``) to one net-vocabulary word (``loop``), each of these
# three kinds already IS its own net-vocabulary word, so no such mapping is
# needed beyond the node-type -> kind-string lookup below.
_FRAME_ONLY_NET_PREFIX: dict[type, str] = {
    SequenceNode: "sequence",
    DisableStructureNode: "disabled",
    EventStructureNode: "event",
}


def _frame_output_tunnel_outers_gn(node: AnyGraphNode) -> list[Terminal]:
    """Output-tunnel outer terminals for a frame-only structure (sequence/
    disabled/event) -- the analogue of ``_case_output_tunnel_outers_gn``/
    ``_loop_output_tunnel_outers_gn`` for the structure kinds with no
    dedicated merge model of their own."""
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.boundary == "outer"
        and t.direction == "output"
    ]


def _is_frame_output_tunnel_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """True for an output-tunnel OUTER terminal on a frame-only structure
    (sequence/disabled/event) -- the analogue of
    ``_is_gamma_output_tunnel_gn``/``_is_eta_output_tunnel_gn``/
    ``_is_mu_shift_register_read_gn`` for the structure kinds with no
    dedicated merge model. Unlike a case's gamma tunnel (claimed only when
    ``>1`` inner is paired) or a loop's eta tunnel (every ``lpTun`` outer
    qualifies), a frame-only structure's output tunnel is claimed
    UNCONDITIONALLY here -- the same "this net's identity belongs to the
    STRUCTURE, not to whichever frame happens to drive it" rule case/loop
    already apply uniformly to their own output tunnels (§ ``_tunnel_net_
    name_gn``), extended to the frame-only kinds that never got a merge
    model to carry it."""
    if type(node) not in _FRAME_ONLY_NET_PREFIX:
        return False
    return (
        isinstance(term, TunnelTerminal)
        and term.boundary == "outer"
        and term.direction == "output"
    )


def _frame_net_name_gn(node: AnyGraphNode, term: Terminal) -> str:
    """``<prefix>_<uid>.out<k>`` for a frame-only structure's output tunnel
    -- see ``_tunnel_net_name_gn``; ``prefix`` is ``_FRAME_ONLY_NET_PREFIX``'s
    lookup for ``node``'s own type."""
    prefix = _FRAME_ONLY_NET_PREFIX[type(node)]
    return _tunnel_net_name_gn(node, term, _frame_output_tunnel_outers_gn, prefix)


def _inplace_output_outers_gn(node: AnyGraphNode) -> list[Terminal]:
    """External OUTPUT ports of an In Place Element Structure -- every
    output-direction terminal EXCEPT an inner tunnel boundary (an inner
    tunnel terminal is read from INSIDE the body and resolves transparently
    to its paired outer source, never as a structure-scoped net). Covers both
    a border ``decomposeRecomposeTunnel``'s outer-output terminal AND a plain
    In-Place-In/Out-Element output port (e.g. the Error passed through a DVR
    element), which -- unlike a loop/case/sequence output tunnel -- is NOT a
    ``TunnelTerminal`` at all, so the frame-only ``_frame_output_tunnel_outers_gn``
    (TunnelTerminal-only) can't name it. Ordered by ``.index`` so the ``outK``
    numbering is stable/byte-reproducible."""
    outers = [
        t
        for t in node.terminals
        if t.direction == "output"
        and not (isinstance(t, TunnelTerminal) and t.boundary == "inner")
    ]
    return sorted(outers, key=lambda t: t.index)


def _is_inplace_output_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """True for an external OUTPUT port on an In Place Element Structure --
    the IPES analogue of ``_is_frame_output_tunnel_gn`` (the value's identity
    belongs to the STRUCTURE, named ``inplace_<uid>.out<k>``, not to whatever
    border element drives it)."""
    if not isinstance(node, InPlaceNode):
        return False
    return any(o.id == term.id for o in _inplace_output_outers_gn(node))


def _inplace_net_name_gn(node: AnyGraphNode, term: Terminal) -> str:
    """``inplace_<uid>.out<k>`` for an IPES external output port -- see
    ``_tunnel_net_name_gn`` (the SAME ``<prefix>_<uid>.out<k>`` shape
    case/loop/sequence use)."""
    return _tunnel_net_name_gn(node, term, _inplace_output_outers_gn, "inplace")


def _is_feedback_output_read_gn(
    graph: InMemoryVIGraph, node: AnyGraphNode, term: Terminal
) -> bool:
    """The graph-node analogue of ``_is_feedback_output_read`` -- ``graph.
    get_feedback_info`` replaces ``isinstance(op, FeedbackOperation) and
    op.is_master`` (the Operation-layer reclassification of a Feedback Node
    master; at the graph level it's still a plain ``PrimitiveNode``)."""
    if not isinstance(node, GraphPrimitiveNode) or term.direction != "output":
        return False
    info = graph.get_feedback_info(node.id)
    return info is not None and info[0]


def _is_ipes_border_node_gn(graph: InMemoryVIGraph, node: AnyGraphNode) -> bool:
    """True when ``node`` is an In-Place-Element-Structure DECOMPOSE or
    RECOMPOSE border node (``graph.get_poser_uid`` set, and its own
    terminals carry list fields in only ONE direction -- the same per-node
    test ``operations._classify_ipes_ops`` applies; a poser_uid'd node with
    list terminals in BOTH directions, or none, is "regular", i.e. NOT a
    border node here).

    These border nodes are deliberately excluded from ``InPlaceOperation.
    inner_nodes`` (see ``_classify_ipes_ops`` -- they're lifted onto
    ``decompose_ops``/``recompose_ops`` instead), so ``index_terminal_owners``
    (which only walks ``inner_nodes``/frames) never registers their
    terminals either -- a wire sourced there falls all the way through
    ``_resolve_source`` to the raw ``uid.index`` structural fallback. This is
    the graph-level mirror of that exclusion, checked directly off the
    node's own terminals (no sibling/IPES context needed -- the classification
    is per-node)."""
    if not isinstance(node, GraphPrimitiveNode):
        return False
    if not graph.get_poser_uid(node.id):
        return False
    has_list_out = any(
        t.nmux_role == "list" and t.direction == "output" for t in node.terminals
    )
    has_list_in = any(
        t.nmux_role == "list" and t.direction == "input" for t in node.terminals
    )
    return (has_list_out and not has_list_in) or (has_list_in and not has_list_out)


# The graph-native netlist builder's OWN "real node" gate -- ``_OPERATION_
# KINDS`` (core.py, shared with codegen/``get_operations``) PLUS
# ``"local_variable"``. A local-variable read/write is a genuine lvnet §7
# node (a distinct producer/sink the netlist must name), but it is NOT a
# codegen "operation" (codegen still resolves it however it already does,
# untouched) -- so this widening lives here, netlist-build-local, rather than
# in the shared ``_OPERATION_KINDS`` constant every other consumer
# (``operations.py``'s ``get_operations``/``_build_inner_nodes``,
# ``analysis.py``, ``queries.py``'s own top-level scan) relies on. Used by
# ``_owning_node_gn`` (a local-variable node becomes a resolvable WIRE-SOURCE
# owner, so a downstream reader's ``NetRef`` points at the read node itself --
# never hops through to the tapped control) and ``_resolve_op_nodes_gn`` (a
# local-variable node participates in body/top-level enumeration like any
# other leaf instance).
_GRAPH_NETLIST_NODE_KINDS = (*_OPERATION_KINDS, "local_variable")


def _owning_node_gn(
    graph: InMemoryVIGraph, node_id: str, vi_name: str
) -> AnyGraphNode | None:
    """The graph-node analogue of ``_BuildCtx.owner_by_terminal`` -- given a
    wire endpoint's OWN carried ``node_id`` (``WireEnd.node_id``, set at
    construction time), resolve the owning ``GraphNode`` directly, gated to
    the graph-native netlist builder's "real node" kinds
    (``_GRAPH_NETLIST_NODE_KINDS`` -- ``_OPERATION_KINDS`` plus
    ``local_variable``; excludes constants/labels), explicitly excluding the
    VI's OWN definition node (``node_id == vi_name``: that node's own
    terminals are the VI's boundary controls, which ``get_operations``/
    ``index_terminal_owners`` never include in the op tree either --
    ``ctx.inputs`` is the separate boundary-control check the caller falls
    through to), and excluding an IPES decompose/recompose border node (see
    ``_is_ipes_border_node_gn`` -- ``index_terminal_owners`` never reaches
    those either).
    """
    if node_id == vi_name:
        return None
    node = graph.get_graph_node(node_id)
    if node is None:
        return None
    if _graph_node_to_op_kind(node) not in _GRAPH_NETLIST_NODE_KINDS:
        return None
    if _is_ipes_border_node_gn(graph, node):
        return None
    return node


def _resolve_source_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    terminal_id: str,
    build_ctx: _GraphBuildCtx,
) -> NetRef | None:
    """The graph-node analogue of ``_resolve_source`` -- identical hop/merge
    logic, but resolves a wire's producing node via ``_owning_node_gn``
    (``graph.get_graph_node``/``graph.get_terminal``-backed) instead of the
    precomputed ``owner_by_terminal`` Operation-tree index."""
    seen: set[str] = set()
    tid = terminal_id
    while True:
        if tid in seen:
            return None
        seen.add(tid)

        sources = graph.incoming_edges(tid)
        if not sources:
            return None
        src: WireEnd = sources[0]

        owner = _owning_node_gn(graph, src.node_id, vi_name)
        term = (
            next(
                (
                    t
                    for t in _call_terminals_gn(graph, vi_name, owner)
                    if t.id == src.terminal_id
                ),
                None,
            )
            if owner is not None
            else None
        )
        if owner is not None and term is not None:
            if _is_gamma_output_tunnel_gn(owner, term):
                name = _gamma_net_name_gn(owner, term)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_eta_output_tunnel_gn(owner, term):
                name = _eta_net_name_gn(owner, term)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_mu_shift_register_read_gn(owner, term):
                name = _mu_net_name_gn(owner, term)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_frame_output_tunnel_gn(owner, term):
                name = _frame_net_name_gn(owner, term)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_inplace_output_gn(owner, term):
                name = _inplace_net_name_gn(owner, term)
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            if _is_feedback_output_read_gn(graph, owner, term):
                name = f"fb{build_ctx.feedback_id_by_uid[_uid_of(owner.id)]}"
                return NetRef(node=None, terminal=name, occurrence=None, bare=name)
            paired = _paired_tunnel_id_gn(term)
            if paired is not None and paired not in seen:
                tid = paired
                continue
            node_name = _display_name_gn(owner)
            occurrence = build_ctx.occurrence_by_uid.get(_uid_of(owner.id))
            # Phase 3: tag this reference with its producer's own uid
            # (``NetRef.producer_uid``) so ``render_lvnet`` can resolve it
            # straight to the producer's ``<base>_<uid>`` handle via
            # ``_LvnetHandles.by_uid`` -- the SAME mechanism a labeled
            # constant already uses via ``constant_uid``. ``occurrence``
            # stays populated unchanged (``render_netlist``/``netlist_to_
            # dict`` parity -- neither reads ``producer_uid``).
            return replace(
                _term_ref(node_name, occurrence, term),
                producer_uid=_uid_of(owner.id),
            )

        for t in ctx.inputs:
            if t.id == src.terminal_id:
                bare = t.name or str(t.index)
                return NetRef(node=None, terminal=bare, occurrence=None, bare=bare)

        const = build_ctx.const_by_id.get(src.node_id)
        if const is not None:
            # ``bare``/``node``/``terminal`` stay the inlined literal VALUE --
            # unchanged behavior, so ``render_netlist``/``netlist_to_dict``
            # (and the graph<->Operation parity test) never see a
            # difference. ``constant_uid`` tags the PRODUCER (the SAME
            # trailing uid ``NetlistConstant.uid`` uses, not the full
            # qualified id -- see ``_build_constant_gn``) for
            # ``render_lvnet`` alone, which looks it up directly against its
            # own handle map (see ``_LvnetHandles.by_uid``/
            # ``_assign_lvnet_handles``) to decide whether to show the
            # inlined literal or the LABELED constant's own ``<handle>``
            # net name -- lvnet §7. ``lvnet_value`` is the SAME value,
            # escaped for lvnet's own grammar (``NetRef.lvnet_value``'s
            # docstring) -- ``render_lvnet`` prefers it; ``render_netlist``/
            # ``netlist_to_dict`` never read it, so ``bare`` stays untouched.
            value_str = _const_value_str(const)
            return NetRef(
                node=None,
                terminal=value_str,
                occurrence=None,
                bare=value_str,
                constant_uid=_uid_of(const.id),
                lvnet_value=_lvnet_const_value_str(const),
            )

        node_name = src.name or _uid_of(src.node_id)
        terminal = str(src.index) if src.index is not None else src.terminal_id
        return NetRef(
            node=node_name,
            terminal=terminal,
            occurrence=None,
            bare=f"{node_name}.{terminal}",
        )


def _resolve_or_default_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    build_ctx: _GraphBuildCtx,
    terminal_id: str | None,
    *fallback_terminals: Terminal | None,
) -> NetRef | DefaultValue:
    """The graph-node analogue of ``_resolve_or_default``."""
    source: NetRef | None = None
    if terminal_id is not None:
        source = _resolve_source_gn(graph, ctx, vi_name, terminal_id, build_ctx)
    if source is not None:
        return source
    lv_type = next(
        (
            t.lv_type
            for t in fallback_terminals
            if t is not None and t.lv_type is not None
        ),
        None,
    )
    return _type_default(lv_type)


def _input_ref_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    build_ctx: _GraphBuildCtx,
    terminal: Terminal,
) -> NetRef | None:
    """The graph-node analogue of ``_input_ref``."""
    return _resolve_source_gn(graph, ctx, vi_name, terminal.id, build_ctx)


def _selector_ref_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    build_ctx: _GraphBuildCtx,
    terminal_id: str | None,
) -> NetRef | None:
    """The graph-node analogue of ``_selector_ref``."""
    if not terminal_id:
        return None
    return _resolve_source_gn(graph, ctx, vi_name, terminal_id, build_ctx)


def _build_property_accesses_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    build_ctx: _GraphBuildCtx,
    node: GraphPrimitiveNode,
    name: str,
    occurrence: int | None,
) -> list[NetlistPropertyAccess]:
    """The graph-node analogue of ``_build_property_accesses`` -- ``node.
    properties``/``node.property_value_terminal_ids`` are already the SAME
    fields ``PropertyOperation`` copies them from (see ``_build_operation``),
    so ``op_walk.correlate_property_terminals`` is reused verbatim."""
    accesses: list[NetlistPropertyAccess] = []
    correlated = correlate_property_terminals(
        node.properties,
        node.terminals,
        node.property_value_terminal_ids,
    )
    for prop, term in correlated:
        if term is None:
            continue
        prop_name = (prop.name or "").strip()
        if not prop_name:
            continue
        if term.direction == "output":
            net = _term_ref(name, occurrence, term)
            accesses.append(
                NetlistPropertyAccess(name=prop_name, direction="read", net=net)
            )
        else:
            net = _input_ref_gn(graph, ctx, vi_name, build_ctx, term)
            accesses.append(
                NetlistPropertyAccess(name=prop_name, direction="write", net=net)
            )
    return accesses


def _call_terminals_gn(
    graph: InMemoryVIGraph, vi_name: str, node: AnyGraphNode
) -> list[Terminal]:
    """The displayed/wired terminal list for ``node`` -- for a SubVI CALL
    (``VINode`` with ``id != vi``), the CALLEE's real parameter names
    enriched in via ``OperationsMixin._enrich_subvi_terminals_typed`` (the
    SAME graph-native method ``_build_operation`` calls to build a
    ``SubVIOperation``'s terminals -- it reads only graph state, no
    ``Operation`` involved, so reusing it directly is exact rather than
    re-derived); every other node kind's own ``terminals`` unchanged.
    Without this, a SubVI call's INPUT/OUTPUT terminal names would show the
    caller-side placeholder name instead of the callee's real parameter name
    (e.g. ``start path`` misread as ``error out``)."""
    if isinstance(node, VINode) and node.id != node.vi:
        # Resolve by the callee's QUALIFIED name, never the bare ``node.name``
        # ("run.vi") -- a bare-name lookup collides across every same-named
        # override in a dynamic-dispatch class hierarchy.
        return graph._enrich_subvi_terminals_typed(  # noqa: SLF001
            list(node.terminals), node.qualified_name or node.name, vi_name
        )
    return node.terminals


def _is_real_terminal(t: Terminal) -> bool:
    """Excludes a dead connector-pane pattern slot -- LabVIEW's own
    ``Void`` type marking a physical pane cell with no real callee
    parameter (never wired, never named -- e.g. a big class-method VI's
    spare/reserved pane slots). The SAME signal ``render/draw.py``'s
    ``_terminal_is_informative`` checks ("A Void terminal is a dead pane
    slot with no data"), reproduced here via ``Terminal.type_descriptor()``
    directly (never a new import -- ``graph/`` does not depend on
    ``render/``) so Phase A's "keep every terminal" doesn't surface pane
    geometry noise that was never a genuine terminal.
    """
    return not (t.lv_type is not None and t.lv_type.type_descriptor() == "Void")


def _ordered_real_terminals_gn(
    graph: InMemoryVIGraph, vi_name: str, node: AnyGraphNode
) -> list[Terminal]:
    """``_call_terminals_gn``'s terminals, Void-filtered (see
    ``_is_real_terminal``) and reordered into the LabVIEW connector-pane's
    canonical reading order (``graph.interface_order.ordered_interface``) --
    inputs first, then outputs, each in its own canonical order.

    Required for a SubVI CALL: ``_call_terminals_gn`` carries the CALLEE's
    real parameter NAMES but stays in the CALL NODE's own raw physical
    pane-slot order, and a call node never carries its own
    ``connector_pattern_id`` (verified empirically -- always ``None``), so
    the CALLEE's own top-level VI definition's pattern must be fetched and
    applied instead. For every other node kind (primitive, constant, ...
    -- no connector-pane pattern), the existing terminal order already is
    the reading order and is returned unchanged.

    This is a PRESENTATION-only helper -- ``render_lvnet`` uses it to sort
    by ``NetlistTerminalBinding``/``NetlistOutput.pane_rank``; the STORED
    ``NetlistInstance.inputs``/``.outputs`` order (built from
    ``_call_terminals_gn`` directly, unreordered) never changes, so
    ``render_netlist``/``netlist_to_dict`` -- and the parity test -- stay
    byte-identical to the Operation-based builder.
    """
    real = [t for t in _call_terminals_gn(graph, vi_name, node) if _is_real_terminal(t)]
    if not (isinstance(node, VINode) and node.id != node.vi and node.name):
        return real
    # Resolve the CALLEE by its own qualified identity (``node.qualified_name``,
    # e.g. "TestSuite.lvclass:run.vi"), never by ``node.name`` -- the bare
    # filename ("run.vi") collides across every same-named override in a
    # dynamic-dispatch class hierarchy (``node.vi`` is the CALLER's key, not
    # the callee's -- see ``GraphNode.vi``/``SubVIBuildHandler``).
    resolved = graph.resolve_vi_name(node.qualified_name or node.name)
    callee = graph.get_graph_node(resolved)
    pattern_id = (
        callee.connector_pattern_id if isinstance(callee, VINode) else None
    )
    ins = [t for t in real if t.direction == "input"]
    outs = [t for t in real if t.direction == "output"]
    return ordered_interface(ins, "input", pattern_id) + ordered_interface(
        outs, "output", pattern_id
    )


_NETLIST_INSTANCE_KIND_BY_NODE_TYPE: dict[type, NetlistInstanceKind] = {
    InPlaceNode: NetlistInstanceKind.IN_PLACE_ELEMENT,
    FormulaNode: NetlistInstanceKind.FORMULA_NODE,
    LocalVariableNode: NetlistInstanceKind.LOCAL_VARIABLE,
}


def _instance_kind_gn(
    node: AnyGraphNode, *, is_property: bool, is_invoke: bool
) -> NetlistInstanceKind:
    """The §7 node-kind keyword for ``node`` (see ``NetlistInstanceKind``) --
    off the ``GraphNode`` SUBCLASS (and the property/invoke discriminators
    ``_build_instance_gn`` already computes from ``node.properties``/
    ``node.method_name``), never off ``qualified_name`` (set even for a
    plain primitive)."""
    if isinstance(node, VINode):
        return NetlistInstanceKind.SUBVI
    if is_property:
        return NetlistInstanceKind.PROPERTY_NODE
    if is_invoke:
        return NetlistInstanceKind.INVOKE_NODE
    for node_type, kind in _NETLIST_INSTANCE_KIND_BY_NODE_TYPE.items():
        if isinstance(node, node_type):
            return kind
    return NetlistInstanceKind.FUNCTION


def _build_instance_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: AnyGraphNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistInstance:
    """The graph-node analogue of ``_build_instance``. Reached only for
    genuine leaf/instance nodes -- ``_build_items_gn`` dispatches Feedback
    Node masters/slaves to ``_build_feedback_gn``/dissolution before this is
    ever called, so a Feedback Node is never mistaken for a plain primitive
    here.

    Phase A: keeps EVERY terminal -- wired or not -- per lvnet §3/§4 (an
    unwired input carries its type's faithful ``default`` instead of a
    driver; see ``NetlistTerminalBinding``). Deliberately does NOT drop a dead
    ``Void`` pane slot HERE (unlike ``_ordered_real_terminals_gn``, a
    presentation-only helper) -- ``_call_terminals_gn``'s terminal list, and
    the Void ones among them, are exactly what the Operation-based ``op.
    terminals`` already carries too, so stored counts must match it 1:1 for
    the graph<->Operation parity test. ``render_netlist``/``netlist_to_dict``
    stay byte-identical to the Operation-based builder by filtering back
    down to wired-only bindings at THEIR two read sites (see the module's
    Phase A docstring note there); ``render_lvnet`` is the one consumer that
    drops a Void binding (by its own ``type`` field, at render time -- see
    ``_is_void_binding``) and shows an unwired one.
    """
    uid = _uid_of(node.id)
    name = _display_name_gn(node)
    occurrence = build_ctx.occurrence_by_uid.get(uid)
    terminals = _call_terminals_gn(graph, vi_name, node)
    pane_rank_by_id = {
        t.id: i for i, t in enumerate(_ordered_real_terminals_gn(graph, vi_name, node))
    }
    inputs = [
        NetlistTerminalBinding(
            terminal=_component_terminal_name(t),
            type=t.type_descriptor() or "?",
            net=(net := _input_ref_gn(graph, ctx, vi_name, build_ctx, t)),
            default=None if net is not None else _type_default(t.lv_type),
            inverted=t.inverted,
            pane_rank=pane_rank_by_id.get(t.id, t.index),
            lv_type=t.lv_type,
        )
        for t in terminals
        if t.direction == "input"
    ]
    outputs = [
        NetlistOutput(
            net=_term_ref(name, occurrence, t),
            type=t.type_descriptor() or "?",
            pane_rank=pane_rank_by_id.get(t.id, t.index),
            lv_type=t.lv_type,
        )
        for t in terminals
        if t.direction == "output"
    ]
    is_property = isinstance(node, GraphPrimitiveNode) and bool(node.properties)
    is_invoke = (
        isinstance(node, GraphPrimitiveNode)
        and not is_property
        and bool(node.method_name)
    )
    operation = (
        node.operation
        if isinstance(node, GraphPrimitiveNode) and not is_property and not is_invoke
        else None
    )
    object_name: str | None = None
    method_name: str | None = None
    properties: list[NetlistPropertyAccess] = []
    if is_property:
        assert isinstance(node, GraphPrimitiveNode)
        object_name = (node.object_name or "").strip() or None
        properties = _build_property_accesses_gn(
            graph, ctx, vi_name, build_ctx, node, name, occurrence
        )
    elif is_invoke:
        assert isinstance(node, GraphPrimitiveNode)
        object_name = (node.object_name or "").strip() or None
        method_name = (node.method_name or "").strip() or None
    qualified_name = getattr(node, "qualified_name", None) or node.name
    kind = _instance_kind_gn(node, is_property=is_property, is_invoke=is_invoke)
    return NetlistInstance(
        uid=uid,
        name=name,
        occurrence=occurrence,
        inputs=inputs,
        outputs=outputs,
        kind=kind,
        operation=operation,
        object_name=object_name,
        method_name=method_name,
        qualified_name=qualified_name,
        properties=properties,
    )


def _build_feedback_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: AnyGraphNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistFeedback:
    """The graph-node analogue of ``_build_feedback`` -- ``graph.
    get_feedback_info`` replaces ``op.partner_uid``/``op.delay``, and the
    linked write side is fetched via ``graph.get_graph_node`` instead of
    ``build_ctx.op_by_uid``."""
    fb = graph.get_feedback_info(node.id)
    assert fb is not None
    _, partner_uid, delay = fb
    k = build_ctx.feedback_id_by_uid[_uid_of(node.id)]

    init_term = next((t for t in node.terminals if t.direction == "input"), None)
    out_term = next((t for t in node.terminals if t.direction == "output"), None)
    init = _resolve_or_default_gn(
        graph,
        ctx,
        vi_name,
        build_ctx,
        init_term.id if init_term is not None else None,
        init_term,
        out_term,
    )

    recur: NetRef | None = None
    slave = graph.get_graph_node(partner_uid) if partner_uid else None
    if slave is not None:
        recur_term = next((t for t in slave.terminals if t.direction == "input"), None)
        if recur_term is not None:
            recur = _resolve_source_gn(graph, ctx, vi_name, recur_term.id, build_ctx)

    return NetlistFeedback(
        uid=_uid_of(node.id),
        net=f"fb{k}",
        init=init,
        recur=recur,
        delay=delay,
    )


def _resolve_op_nodes_gn(graph: InMemoryVIGraph, uids: list[str]) -> list[AnyGraphNode]:
    """Resolve ``uids`` to typed graph nodes, keeping only the graph-native
    netlist builder's "real node" kinds (``_GRAPH_NETLIST_NODE_KINDS`` --
    excludes constants/labels), in the given order. The graph-node analogue
    of the ``op_kind in _OPERATION_KINDS`` filter ``_build_inner_nodes``/
    ``get_operations``/``get_operation_order`` apply before constructing an
    ``Operation`` for a uid, widened here (netlist-only) to also keep a
    local-variable node."""
    nodes: list[AnyGraphNode] = []
    for uid in uids:
        node = graph.get_graph_node(uid)
        if node is not None and (
            _graph_node_to_op_kind(node) in _GRAPH_NETLIST_NODE_KINDS
        ):
            nodes.append(node)
    return nodes


def _top_level_nodes_gn(graph: InMemoryVIGraph, vi_name: str) -> list[AnyGraphNode]:
    """The graph-node analogue of ``ctx.operations``'s top-level ordering --
    ``graph.get_operation_order`` IS the same dataflow-topological,
    ``_node_order_key``-tie-broken sort ``get_operations`` (which builds
    ``ctx.operations``) itself sorts by; this reuses it directly rather than
    re-deriving the order (a pure graph-level function already, no
    ``Operation`` involved). ``extra_kinds=("local_variable",)`` widens ONLY
    this call's own top-level scan -- ``get_operations``/codegen's default
    call keeps seeing none, so a top-level local-variable read/write joins
    the SAME dataflow-topological sort as every other node (a read orders
    before its consumer via the real wire edge between them) instead of
    being dropped before ordering even starts."""
    return _resolve_op_nodes_gn(
        graph, graph.get_operation_order(vi_name, extra_kinds=("local_variable",))
    )


def _body_nodes_gn(
    graph: InMemoryVIGraph, vi_name: str, node: AnyGraphNode
) -> list[AnyGraphNode]:
    """The graph-node analogue of ``_build_inner_nodes`` -- ``node.children``
    (the forward containment adjacency, already in ``_node_order_key`` order)
    topologically re-sorted by dataflow via ``graph._sort_inner_uids`` (the
    SAME pure graph-level tie-break ``_build_inner_nodes`` itself calls --
    reused directly rather than re-derived, for the identical reason
    ``_top_level_nodes_gn`` reuses ``get_operation_order``)."""
    if not node.children:
        return []
    sorted_uids = graph._sort_inner_uids(node.children, vi_name)  # noqa: SLF001
    return _resolve_op_nodes_gn(graph, sorted_uids)


def _frame_key_gn(
    frame: CaseFrame | SequenceFrame | EventFrame, position: int
) -> str | int:
    """The graph-node analogue of ``_populate_frame_operations``'s per-frame
    match key: a case/disable frame's own ``selector_value``, a sequence
    frame's ``str(index)``, an event frame's list POSITION (there is no
    selector_value/index of its own -- see ``_populate_frame_operations``)."""
    if isinstance(frame, CaseFrame):
        return frame.selector_value
    if isinstance(frame, SequenceFrame):
        return str(frame.index)
    return str(position)


def _frame_child_uids_gn(
    graph: InMemoryVIGraph,
    node: AnyGraphNode,
    frame: CaseFrame | SequenceFrame | EventFrame,
    position: int,
) -> list[str]:
    """RAW child uids of ``node`` belonging to ONE frame (matching each
    child's own ``.frame`` attribute -- the SAME field ``_group_children_by_
    frame`` groups by), unsorted/unfiltered-by-kind. Split out of
    ``_frame_nodes_gn`` so ``_build_items_gn``'s labeled-constant extraction
    (``_labeled_constant_items_gn``) can find a constant child the SAME way
    an instance child is found, without ``_resolve_op_nodes_gn``'s
    ``_OPERATION_KINDS`` gate dropping it first."""
    key = _frame_key_gn(frame, position)
    return [
        uid
        for uid in node.children
        if (child := graph.get_graph_node(uid)) is not None and child.frame == key
    ]


def _frame_nodes_gn(
    graph: InMemoryVIGraph,
    vi_name: str,
    node: AnyGraphNode,
    frame: CaseFrame | SequenceFrame | EventFrame,
    position: int,
) -> list[AnyGraphNode]:
    """The graph-node analogue of ``_populate_frame_operations`` for ONE
    frame: ``node.children`` filtered to this frame's key (matching each
    child's own ``.frame`` attribute -- the SAME field ``_group_children_by_
    frame`` groups by), then dataflow-sorted exactly like ``_body_nodes_gn``.
    """
    child_uids = _frame_child_uids_gn(graph, node, frame, position)
    if not child_uids:
        return []
    sorted_uids = graph._sort_inner_uids(child_uids, vi_name)  # noqa: SLF001
    return _resolve_op_nodes_gn(graph, sorted_uids)


def _ipes_regular_nodes_gn(
    graph: InMemoryVIGraph, vi_name: str, node: AnyGraphNode
) -> list[AnyGraphNode]:
    """The "regular" (non decompose/recompose) children of an In-Place-
    Element Structure -- the graph-node analogue of
    ``InPlaceOperation.inner_nodes`` (``_walk_flat``/``_build_items`` only
    ever recurse into THIS subset; decompose/recompose ops are dropped from
    the flat walk/body entirely, matching ``build_netlist``'s existing gap --
    see the module's Phase 1 docstring). Filters via ``_is_ipes_border_node_gn``
    -- the SAME per-node classification ``_owning_node_gn`` uses to exclude a
    border node's terminals from wire-source resolution."""
    all_nodes = _body_nodes_gn(graph, vi_name, node)
    return [n for n in all_nodes if not _is_ipes_border_node_gn(graph, n)]


def _walk_flat_nodes_gn(
    graph: InMemoryVIGraph, vi_name: str, nodes: list[AnyGraphNode]
) -> list[AnyGraphNode]:
    """The graph-node analogue of ``_walk_flat`` -- SAME recursion shape
    (frame-structures recurse per-frame, an In-Place Element Structure
    recurses into its "regular" children only, everything else recurses into
    its (dataflow-sorted) children), applied to freshly-resolved node lists
    instead of an already-built ``Operation`` tree."""
    flat: list[AnyGraphNode] = []
    for node in nodes:
        flat.append(node)
        if isinstance(node, _FRAME_STRUCTURE_TYPES):
            for i, frame in enumerate(node.frames):
                flat.extend(
                    _walk_flat_nodes_gn(
                        graph, vi_name, _frame_nodes_gn(graph, vi_name, node, frame, i)
                    )
                )
        elif isinstance(node, InPlaceNode):
            flat.extend(
                _walk_flat_nodes_gn(
                    graph, vi_name, _ipes_regular_nodes_gn(graph, vi_name, node)
                )
            )
        elif node.children:
            flat.extend(
                _walk_flat_nodes_gn(
                    graph, vi_name, _body_nodes_gn(graph, vi_name, node)
                )
            )
    return flat


def _walk_flat_gn(graph: InMemoryVIGraph, vi_name: str) -> list[AnyGraphNode]:
    return _walk_flat_nodes_gn(graph, vi_name, _top_level_nodes_gn(graph, vi_name))


_OCCURRENCE_EXCLUDED_TYPES = _STRUCTURE_NODE_TYPES


def _assign_occurrences_gn(
    graph: InMemoryVIGraph, flat: list[AnyGraphNode]
) -> dict[str, int]:
    """The graph-node analogue of ``_assign_occurrences``."""
    insts = [
        node
        for node in flat
        if not isinstance(node, _OCCURRENCE_EXCLUDED_TYPES)
        and not (
            isinstance(node, GraphPrimitiveNode)
            and graph.get_feedback_info(node.id) is not None
        )
    ]
    names = [_display_name_gn(node) for node in insts]
    counts = Counter(names)

    occurrence_by_uid: dict[str, int] = {}
    running: dict[str, int] = {}
    for node, name in zip(insts, names, strict=True):
        if counts[name] > 1:
            running[name] = running.get(name, 0) + 1
            occurrence_by_uid[_uid_of(node.id)] = running[name]
    return occurrence_by_uid


def _assign_sequential_ids_gn(
    flat: list[AnyGraphNode],
    predicate: Callable[[AnyGraphNode], bool],
) -> dict[str, int]:
    """The graph-node analogue of ``_assign_sequential_ids``."""
    ids: dict[str, int] = {}
    next_id = 0
    for node in flat:
        if predicate(node):
            ids[_uid_of(node.id)] = next_id
            next_id += 1
    return ids


def _assign_constant_occurrences(constants: list[Constant]) -> dict[str, int]:
    """Phase A's ``#n`` disambiguator for a LABELED constant (lvnet
    ``NetlistConstant`` -- see §9's "a node/constant whose display name
    repeats is disambiguated with #N"), scoped to labeled constants only and
    counted PER LABEL -- the same "``#n`` only when it repeats" convention
    ``_assign_occurrences_gn`` applies to instances, applied here to
    ``Constant.label`` instead of an instance's display name. An unlabeled
    constant never participates (it is never promoted to its own
    ``NetlistConstant`` body item -- see ``_labeled_constant_items_gn`` --
    so it needs no occurrence tag).

    ``constants`` is ``ctx.constants`` -- already ``_node_order_key``-sorted
    (see ``queries.get_constants``) -- so this is deterministic. Keyed by
    each constant's own FULL qualified id (matching ``_GraphBuildCtx.
    const_by_id``'s key), not the trailing uid.
    """
    labeled = [c for c in constants if (c.label or "").strip()]
    labels = [(c.label or "").strip() for c in labeled]
    counts = Counter(labels)

    occurrence_by_id: dict[str, int] = {}
    running: dict[str, int] = {}
    for c, label in zip(labeled, labels, strict=True):
        if counts[label] > 1:
            running[label] = running.get(label, 0) + 1
            occurrence_by_id[c.id] = running[label]
    return occurrence_by_id


def _build_constant_gn(
    node: ConstantNode, build_ctx: _GraphBuildCtx
) -> NetlistConstant:
    """One LABELED constant, promoted to its own lvnet §7 ``constant`` body
    item -- see ``_labeled_constant_items_gn``. Reuses ``build_ctx.
    const_by_id``/``op_walk._const_value_str`` -- the SAME faithful VALUE
    text ``_resolve_source_gn``'s inline-literal branch already renders --
    rather than reformatting ``node.value`` fresh."""
    label = (node.label or "").strip()
    const = build_ctx.const_by_id.get(node.id)
    type_desc = node.lv_type.type_descriptor() if node.lv_type else "?"
    value = _const_value_str(const) if const is not None else str(node.value)
    lvnet_value = (
        _lvnet_const_value_str(const)
        if const is not None
        else _lvnet_literal_token(node.value)
    )
    occurrence = build_ctx.constant_occurrence_by_uid.get(node.id)
    return NetlistConstant(
        uid=_uid_of(node.id),
        name=label,
        occurrence=occurrence,
        type=type_desc,
        value=value,
        lvnet_value=lvnet_value,
    )


def _labeled_constant_items_gn(
    graph: InMemoryVIGraph,
    child_uids: list[str] | tuple[str, ...],
    build_ctx: _GraphBuildCtx,
) -> list[NetlistConstant]:
    """Every LABELED ``ConstantNode`` directly among ``child_uids`` (lvnet
    §7: "a shared/named constant becomes a constant node referenced by
    net"), in their given (``GraphNode.children``, already
    ``_node_order_key`` order) document order -- placed FIRST in the owning
    scope's body (the golden shows ``constant GUID#1`` ahead of the frame's
    instances; a constant has no dataflow dependency of its own, so a
    stable "declared first" position is natural and deterministic -- see
    ``_build_items_gn``). An UNLABELED ("one-off") constant is never
    promoted here -- it stays inlined as a literal at its point of use (see
    ``_resolve_source_gn``); this is the ONLY place that data-driven split
    is decided (never a string-matched/hardcoded list).
    """
    result: list[NetlistConstant] = []
    for uid in child_uids:
        child = graph.get_graph_node(uid)
        if isinstance(child, ConstantNode) and (child.label or "").strip():
            result.append(_build_constant_gn(child, build_ctx))
    return result


def _build_case_outputs_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: CaseStructureNode,
    build_ctx: _GraphBuildCtx,
    case_uid: str,
    selector: NetRef | None,
    frames: list[NetlistFrame],
) -> list[GammaMerge]:
    """The graph-node analogue of ``_build_case_outputs``."""
    outers = _case_output_tunnel_outers_gn(node)
    gammas: list[GammaMerge] = []
    for k, outer in enumerate(outers):
        cases: list[GammaCase] = []
        for raw_frame, nl_frame in zip(node.frames, frames, strict=True):
            inner = next(
                (
                    t
                    for t in node.terminals
                    if isinstance(t, TunnelTerminal)
                    and t.boundary == "inner"
                    and t.paired_id == outer.id
                    and t.frame == raw_frame.selector_value
                ),
                None,
            )
            source = _resolve_or_default_gn(
                graph,
                ctx,
                vi_name,
                build_ctx,
                inner.id if inner is not None else None,
                inner,
                outer,
            )
            frame_key = "default" if nl_frame.is_default else nl_frame.label
            cases.append(GammaCase(frame_key=frame_key, source=source))
        gammas.append(
            GammaMerge(net=f"case_{case_uid}.out{k}", selector=selector, cases=cases)
        )
    return gammas


def _selector_lv_type_gn(
    node: AnyGraphNode, selector_terminal: str | None
) -> LVType | None:
    """The graph-node analogue of ``_selector_lv_type``."""
    if not selector_terminal:
        return None
    for t in node.terminals:
        if t.id == selector_terminal:
            return t.lv_type
    return None


def _build_case_scope_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: CaseStructureNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistScope:
    """The graph-node analogue of ``_build_case_scope``."""
    selector = _selector_ref_gn(graph, ctx, vi_name, build_ctx, node.selector_terminal)
    passthrough = _has_output_tunnel_gn(node)
    lv_type = _selector_lv_type_gn(node, node.selector_terminal)
    is_error = bool(lv_type and _is_error_cluster(lv_type))
    frames = [
        NetlistFrame(
            label=_selector_label(frame, lv_type, is_error),
            value=str(frame.selector_value),
            is_default=frame.is_default,
            body=_build_items_gn(
                graph,
                ctx,
                vi_name,
                _frame_nodes_gn(graph, vi_name, node, frame, i),
                build_ctx,
                owner_children=_frame_child_uids_gn(graph, node, frame, i),
            ),
            passthrough=passthrough,
        )
        for i, frame in enumerate(node.frames)
    ]
    case_uid = _uid_of(node.id)
    outputs: list[GammaMerge | MuMerge | EtaMerge] = [
        *_build_case_outputs_gn(
            graph, ctx, vi_name, node, build_ctx, case_uid, selector, frames
        ),
    ]
    return NetlistScope(
        uid=_uid_of(node.id),
        kind="case",
        selector=selector,
        frames=frames,
        outputs=outputs,
    )


def _build_disabled_scope_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: DisableStructureNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistScope:
    """The graph-node analogue of ``_build_disabled_scope``."""
    passthrough = _has_output_tunnel_gn(node)
    frames = [
        NetlistFrame(
            label=str(frame.selector_value),
            value=str(frame.selector_value),
            is_default=frame.is_default,
            body=_build_items_gn(
                graph,
                ctx,
                vi_name,
                _frame_nodes_gn(graph, vi_name, node, frame, i),
                build_ctx,
                owner_children=_frame_child_uids_gn(graph, node, frame, i),
            ),
            passthrough=passthrough,
        )
        for i, frame in enumerate(node.frames)
    ]
    return NetlistScope(
        uid=_uid_of(node.id),
        kind="disabled",
        selector=None,
        frames=frames,
        disable_kind=node.kind,
    )


def _build_event_scope_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: EventStructureNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistScope:
    """The graph-node analogue of ``_build_event_scope``."""
    passthrough = _has_output_tunnel_gn(node)
    frames = [
        NetlistFrame(
            label=frame.event_label,
            value=frame.event_label,
            is_default=False,
            body=_build_items_gn(
                graph,
                ctx,
                vi_name,
                _frame_nodes_gn(graph, vi_name, node, frame, i),
                build_ctx,
                owner_children=_frame_child_uids_gn(graph, node, frame, i),
            ),
            passthrough=passthrough,
        )
        for i, frame in enumerate(node.frames)
    ]
    return NetlistScope(
        uid=_uid_of(node.id), kind="event", selector=None, frames=frames
    )


def _build_sequence_scope_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: SequenceNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistScope:
    """The graph-node analogue of ``_build_sequence_scope``."""
    frames = [
        NetlistFrame(
            label=str(frame.index),
            value=str(frame.index),
            is_default=False,
            body=_build_items_gn(
                graph,
                ctx,
                vi_name,
                _frame_nodes_gn(graph, vi_name, node, frame, i),
                build_ctx,
                owner_children=_frame_child_uids_gn(graph, node, frame, i),
            ),
            passthrough=False,
        )
        for i, frame in enumerate(node.frames)
    ]
    return NetlistScope(
        uid=_uid_of(node.id),
        kind="sequence",
        selector=None,
        frames=frames,
        sequence_is_flat=node.is_flat,
    )


def _build_loop_shift_registers_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: LoopNode,
    build_ctx: _GraphBuildCtx,
    loop_uid: str,
    term_by_id: dict[str, Terminal],
) -> list[MuMerge]:
    """The graph-node analogue of ``_build_loop_shift_registers``."""
    merges: list[MuMerge] = []
    for k, (lsr, rsr) in enumerate(_loop_shift_register_pairs_gn(node)):
        outer_t = term_by_id.get(lsr.outer_terminal_uid)
        inner_t = term_by_id.get(lsr.inner_terminal_uid)
        init = _resolve_or_default_gn(
            graph,
            ctx,
            vi_name,
            build_ctx,
            lsr.outer_terminal_uid if lsr.sr_initialized else None,
            outer_t,
            inner_t,
        )
        recur = (
            _resolve_source_gn(graph, ctx, vi_name, rsr.inner_terminal_uid, build_ctx)
            if rsr is not None
            else None
        )
        merges.append(MuMerge(net=f"loop_{loop_uid}.shift{k}", init=init, recur=recur))
    return merges


def _build_loop_outputs_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: LoopNode,
    build_ctx: _GraphBuildCtx,
    loop_uid: str,
    term_by_id: dict[str, Terminal],
) -> list[EtaMerge]:
    """The graph-node analogue of ``_build_loop_outputs``."""
    tunnels = _tunnels_from_terminals_gn(node.terminals)
    tunnel_by_outer = {
        t.outer_terminal_uid: t for t in tunnels if t.tunnel_type == "lpTun"
    }
    outers = _loop_output_tunnel_outers_gn(node)
    merges: list[EtaMerge] = []
    for k, outer in enumerate(outers):
        tunnel = tunnel_by_outer[outer.id]
        inner_t = term_by_id.get(tunnel.inner_terminal_uid)
        value = _resolve_or_default_gn(
            graph, ctx, vi_name, build_ctx, tunnel.inner_terminal_uid, inner_t, outer
        )
        merges.append(
            EtaMerge(
                net=f"loop_{loop_uid}.out{k}",
                index_mode=_eta_index_mode(tunnel.mode),
                conditional=tunnel.conditional,
                value=value,
            )
        )
    return merges


def _build_loop_scope_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: LoopNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistScope:
    """The graph-node analogue of ``_build_loop_scope``."""
    kind = "while" if node.loop_type == "whileLoop" else "for"
    selector = _selector_ref_gn(
        graph, ctx, vi_name, build_ctx, node.stop_condition_terminal
    )
    body = _build_items_gn(
        graph,
        ctx,
        vi_name,
        _body_nodes_gn(graph, vi_name, node),
        build_ctx,
        owner_children=node.children,
    )
    frame = NetlistFrame(
        label="",
        value="",
        is_default=False,
        body=body,
        passthrough=_has_output_tunnel_gn(node),
    )
    tunnels = _tunnels_from_terminals_gn(node.terminals)
    tunnel_info = [
        NetlistTunnelInfo(
            tunnel_type=t.tunnel_type,
            mode=t.mode.value if t.mode is not None else None,
            sr_initialized=t.sr_initialized,
            sr_stack_depth=t.sr_stack_depth,
        )
        for t in tunnels
    ]
    loop_uid = _uid_of(node.id)
    term_by_id = {t.id: t for t in node.terminals}
    outputs: list[GammaMerge | MuMerge | EtaMerge] = [
        *_build_loop_shift_registers_gn(
            graph, ctx, vi_name, node, build_ctx, loop_uid, term_by_id
        ),
        *_build_loop_outputs_gn(
            graph, ctx, vi_name, node, build_ctx, loop_uid, term_by_id
        ),
    ]
    return NetlistScope(
        uid=_uid_of(node.id),
        kind=kind,
        selector=selector,
        frames=[frame],
        parallel=node.parallel,
        parallel_static_workers=node.parallel_static_workers,
        tunnels=tunnel_info,
        outputs=outputs,
    )


def _build_inplace_scope_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: InPlaceNode,
    build_ctx: _GraphBuildCtx,
) -> NetlistScope:
    """An In Place Element Structure -- a scope with a single implicit body
    (like a loop, NOT a per-frame family), whose body is the structure's
    "regular" (non decompose/recompose border) children. Its border ports
    (the ``decomposeRecomposeTunnel`` and the In-Place-In/Out-Element ports)
    are NOT emitted as their own items here: a downstream reader of one of the
    IPES's OUTPUT ports resolves straight to the structure-scoped
    ``inplace_<uid>.out<k>`` net (``_is_inplace_output_gn`` in
    ``_resolve_source_gn``), the same "the net's identity belongs to the
    STRUCTURE" rule the frame-only families apply to their own output tunnels
    -- so, exactly like a sequence/disabled/event scope, the scope header +
    body IS the whole rendering (``render_lvnet._render_lvnet_inplace_scope``).
    Faithful + round-trippable, NOT execution-worthy: an IPES has no headless
    Python analogue (it is transparent -- mutable references), so this projects
    its structure/containment without inventing decompose/recompose semantics
    the model does not carry."""
    body = _build_items_gn(
        graph,
        ctx,
        vi_name,
        _ipes_regular_nodes_gn(graph, vi_name, node),
        build_ctx,
        owner_children=node.children,
    )
    frame = NetlistFrame(
        label="",
        value="",
        is_default=False,
        body=body,
        passthrough=_has_output_tunnel_gn(node),
    )
    return NetlistScope(
        uid=_uid_of(node.id),
        kind="inplace",
        selector=None,
        frames=[frame],
    )


def _build_items_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    nodes: list[AnyGraphNode],
    build_ctx: _GraphBuildCtx,
    owner_children: list[str] | tuple[str, ...] = (),
) -> list[NetlistItem]:
    """The graph-node analogue of ``_build_items``.

    ``owner_children`` is this SAME scope's raw (unfiltered-by-kind) child
    uid list -- ``nodes`` has already been filtered to
    ``_GRAPH_NETLIST_NODE_KINDS`` (see ``_resolve_op_nodes_gn``), which drops
    a ``ConstantNode`` entirely,
    so a Phase A labeled constant (lvnet §7) can only be found from the RAW
    list. Every labeled constant among ``owner_children`` is placed FIRST
    (see ``_labeled_constant_items_gn``); callers with no meaningful raw
    child list (there are none left after Phase A -- every caller now passes
    one) default to ``()``, meaning "no constants to promote here"."""
    items: list[NetlistItem] = [
        *_labeled_constant_items_gn(graph, owner_children, build_ctx)
    ]
    for node in nodes:
        if isinstance(node, CaseStructureNode):
            items.append(_build_case_scope_gn(graph, ctx, vi_name, node, build_ctx))
        elif isinstance(node, DisableStructureNode):
            items.append(_build_disabled_scope_gn(graph, ctx, vi_name, node, build_ctx))
        elif isinstance(node, EventStructureNode):
            items.append(_build_event_scope_gn(graph, ctx, vi_name, node, build_ctx))
        elif isinstance(node, LoopNode):
            items.append(_build_loop_scope_gn(graph, ctx, vi_name, node, build_ctx))
        elif isinstance(node, SequenceNode):
            items.append(_build_sequence_scope_gn(graph, ctx, vi_name, node, build_ctx))
        elif isinstance(node, InPlaceNode):
            # An In Place Element Structure becomes its OWN scope (kind
            # ``"inplace"``), with its regular children as the nested body --
            # NOT a flat instance followed by its inner nodes hoisted up as
            # siblings (which lost the containment and left a ``# TODO(lvnet)``
            # placeholder). See ``_build_inplace_scope_gn``.
            items.append(_build_inplace_scope_gn(graph, ctx, vi_name, node, build_ctx))
        elif (
            isinstance(node, GraphPrimitiveNode)
            and (fb := graph.get_feedback_info(node.id)) is not None
        ):
            # The MASTER becomes one ``fb{k}`` mu item; the SLAVE is dissolved
            # (its written value is captured as the master's ``recur`` in
            # ``_build_feedback_gn``) -- mirrors ``_build_items``'s
            # ``FeedbackOperation`` branch.
            if fb[0]:
                items.append(_build_feedback_gn(graph, ctx, vi_name, node, build_ctx))
        else:
            items.append(_build_instance_gn(graph, ctx, vi_name, node, build_ctx))
    return items


def _is_subvi_call_gn(node: AnyGraphNode) -> bool:
    """The graph-node analogue of ``_is_subvi_call``. Every ``VINode``
    reached by the flat walk is inherently a SubVI CALL (``node.id !=
    node.vi``) -- the VI's own definition node is never part of it (see
    ``_owning_node_gn``/``get_operation_order``)."""
    return isinstance(node, VINode) and bool(node.name)


def _is_primitive_component_gn(graph: InMemoryVIGraph, node: AnyGraphNode) -> bool:
    """True when ``node`` becomes its own declared component -- the
    graph-node analogue of ``isinstance(op, PrimitiveOperation)`` in
    ``_build_components``: a plain primitive, NEVER a property/invoke node
    (those become ``PropertyOperation``/``InvokeOperation`` at the Operation
    layer, which are not ``PrimitiveOperation`` subclasses either) and NEVER
    a Feedback Node (which becomes ``FeedbackOperation`` instead)."""
    return (
        isinstance(node, GraphPrimitiveNode)
        and not node.properties
        and not node.method_name
        and graph.get_feedback_info(node.id) is None
    )


def _component_identity_gn(node: AnyGraphNode) -> tuple[object, ...]:
    """The graph-node analogue of ``_component_identity`` -- ``PrimitiveNode.
    prim_id`` is the SAME value ``PrimitiveOperation.primResID`` copies it
    from (see ``_build_operation``)."""
    prim_res_id = node.prim_id if isinstance(node, GraphPrimitiveNode) else None
    operation = node.operation if isinstance(node, GraphPrimitiveNode) else None
    return (node.node_type or "unknown", prim_res_id, operation)


def _synthesize_ports_gn(
    terminals: list[Terminal],
) -> tuple[list[ComponentPort], list[ComponentPort]]:
    """The graph-node analogue of ``_synthesize_ports`` -- takes the
    terminal list directly (rather than a node) so a SubVI-call fallback can
    pass ``_call_terminals_gn``'s CALLEE-enriched names, same as
    ``_build_instance_gn``."""
    ins: list[ComponentPort] = []
    outs: list[ComponentPort] = []
    for t in sorted(terminals, key=lambda t: t.index):
        port = ComponentPort(
            name=_component_terminal_name(t),
            type=t.lv_type.type_descriptor() if t.lv_type else "Any",
        )
        (ins if t.direction == "input" else outs).append(port)
    return ins, outs


def _dedupe_primitive_group_gn(
    instances: list[tuple[AnyGraphNode, list[ComponentPort], list[ComponentPort]]],
) -> list[NetlistComponent]:
    """The graph-node analogue of ``_dedupe_primitive_group``."""
    by_signature: dict[
        str, tuple[AnyGraphNode, list[ComponentPort], list[ComponentPort]]
    ] = {}
    order: list[str] = []
    for node, ins, outs in instances:
        sig = _port_signature(ins, outs)
        if sig not in by_signature:
            by_signature[sig] = (node, ins, outs)
            order.append(sig)

    if len(order) == 1:
        node, ins, outs = by_signature[order[0]]
        return [NetlistComponent(name=_display_name_gn(node), inputs=ins, outputs=outs)]

    result: list[NetlistComponent] = []
    for i, sig in enumerate(order, start=1):
        node, ins, outs = by_signature[sig]
        base = _display_name_gn(node)
        name = base if i == 1 else f"{base} ({i})"
        result.append(NetlistComponent(name=name, inputs=ins, outputs=outs))
    return result


def _build_components_gn(
    graph: InMemoryVIGraph, vi_name: str, flat: list[AnyGraphNode]
) -> list[NetlistComponent]:
    """The graph-node analogue of ``_build_components``."""
    subvi_order: list[str] = []
    seen_subvi: set[str] = set()
    subvi_reps: dict[str, AnyGraphNode] = {}
    groups: dict[
        tuple[object, ...],
        list[tuple[AnyGraphNode, list[ComponentPort], list[ComponentPort]]],
    ] = {}

    for node in flat:
        if isinstance(node, _STRUCTURE_NODE_TYPES):
            continue
        if _is_subvi_call_gn(node):
            assert isinstance(node, VINode)
            key = node.qualified_name or node.name
            assert key is not None
            if key not in seen_subvi:
                seen_subvi.add(key)
                subvi_order.append(key)
                subvi_reps[key] = node
            continue
        if not _is_primitive_component_gn(graph, node):
            continue
        ins, outs = _synthesize_ports_gn(node.terminals)
        key = _component_identity_gn(node)
        groups.setdefault(key, []).append((node, ins, outs))

    components: list[NetlistComponent] = []
    for key in subvi_order:
        ports = _subvi_ports(graph, key)
        if ports is not None and (ports[0] or ports[1]):
            ins, outs = ports
        else:
            rep = subvi_reps[key]
            ins, outs = _synthesize_ports_gn(_call_terminals_gn(graph, vi_name, rep))
        components.append(NetlistComponent(name=key, inputs=ins, outputs=outs))
    for instances in groups.values():
        components.extend(_dedupe_primitive_group_gn(instances))

    _disambiguate_cross_group_names(components)
    components.sort(key=lambda c: c.name)
    return components


def _project_relative_display(resolved: Path | None, base: Path) -> str | None:
    """A ``./``-prefixed, forward-slash project-relative display path for the
    lvnet §6 ``; ./path`` annotation. Best-effort, never fabricated: ``None``
    when ``resolved`` is ``None`` or ``os.path.relpath`` itself fails (e.g. a
    cross-drive path on Windows) -- mirrors ``cli.py``'s ``_repo_relative_path``,
    the existing precedent for this exact annotation on the OLD ``describe
    --format netlist``'s ``## Components`` path comment.
    """
    if resolved is None:
        return None
    try:
        rel = os.path.relpath(resolved.resolve(), base.resolve())
    except (OSError, ValueError):
        return None
    return _LVNET_TYPEDEF_NAV_PREFIX + rel.replace(os.sep, "/")


def _dependency_interface(
    graph: InMemoryVIGraph, kind: DependencyKind, qname: str
) -> list[ConnectorPaneTerminal]:
    """The §7a verbose-only inline interface for one ``uses :`` entry: a
    ``subVI`` dependency's OWN connector-pane terminals (inputs then
    outputs), pulled from the SAME already-leaf-loaded graph a
    ``LoadMode.MINIMAL`` load populates for every direct SubVI (its own
    connector pane, per ``graph/loading.py``'s leaf-load comment) -- never a
    second, re-parsed VI. Reuses the exact primitives this VI's OWN boundary
    is built from: ``InMemoryVIGraph.get_inputs``/``get_outputs`` (already
    canonically ordered by ``interface_order.ordered_interface``) projected
    through ``_pane_terminal``, the SAME helper ``build_netlist_from_graph``
    calls for ``module.connector_pane``.

    ``class``/``typedef`` dependencies get no interface (a connector pane is
    a VI-only concept) -- ``[]``. A ``subVI`` dependency that isn't
    resolvable in the loaded graph (``graph.resolve_vi_name(qname)`` falls
    through to a name with no ``VINode``) also yields ``[]``: ``get_inputs``/
    ``get_outputs`` already return ``[]`` for a ``vi_name`` absent from the
    graph, so this is never fabricated, only genuinely absent.
    """
    if kind != DependencyKind.SUBVI:
        return []
    dep_key = graph.resolve_vi_name(qname)
    return [_pane_terminal(t, "input") for t in graph.get_inputs(dep_key)] + [
        _pane_terminal(t, "output") for t in graph.get_outputs(dep_key)
    ]


def _dependency_kind_for(
    qualified: str, source_hint: DependencyKind | None = None
) -> DependencyKind:
    """The §7 ``uses :`` kind word for one dependency's fully-qualified
    identity. The file extension is authoritative WHEN PRESENT (``.vi`` /
    ``.ctl`` / ``.lvclass``); otherwise the kind comes from ``source_hint`` --
    which of the VI's own dependency TABLES the qname was drawn from (its
    SubVI call table vs its referenced-type map), the real data-driven
    classification rather than a filename guess. Some genuine dependencies
    carry no extension in their recorded qname (an external/library function
    such as ``IMAQ AVI2 Close``); a called dependency with neither an
    extension nor a hint is a SubVI (the only kind reached by a call), never
    an error."""
    leaf = qualified.rsplit(":", 1)[-1]
    if leaf.endswith(".vi"):
        return DependencyKind.SUBVI
    if leaf.endswith(".ctl"):
        return DependencyKind.TYPEDEF
    if leaf.endswith(".lvclass"):
        return DependencyKind.CLASS
    return source_hint if source_hint is not None else DependencyKind.SUBVI


def _build_dependency_manifest(
    graph: InMemoryVIGraph, vi_name: str
) -> list[NetlistDependency]:
    """The lvnet §7 ``uses :`` manifest: every external file this VI
    directly depends on (subVI calls, referenced classes, referenced
    typedefs) -- built with the SAME primitives the loader itself walks a
    VI's dependencies with (``collect_direct_dep_qnames``/
    ``build_dep_ref_map``, ``graph/loading.py``) and the SAME path-resolution
    ``lvkit.list_deps`` reuses (``InMemoryVIGraph._resolve_dependency_path``
    plus its ``.lvclass`` walk-up fallback) -- never a separate,
    reimplemented mechanism.

    Needs the VI's own on-disk ``.vi`` file (``graph.get_vi_source_path``) to
    re-derive its ``subvi_qualified_names``/``type_map``/``dependency_refs``:
    the already-loaded graph does not retain these PER VI once loading
    finishes (they are a transient, load-time-only projection consumed
    inside ``_load_vi_recursive`` and then discarded). Returns ``[]`` when
    the source file can't be found or re-parsed (e.g. a graph built straight
    from BD-heap XML with no ``.vi`` sibling) -- never a guess.
    """
    source_path = graph.get_vi_source_path(vi_name)
    if source_path is None or not source_path.exists():
        return []

    try:
        vi = parse_vi(source_path)
    except (RuntimeError, OSError, ValueError):
        return []

    metadata = vi.metadata
    own_qname = metadata.qualified_name or source_path.name
    all_dep_qnames = collect_direct_dep_qnames(
        metadata.subvi_qualified_names, metadata.type_map, own_qname
    )
    if not all_dep_qnames:
        return []

    dep_ref_map = build_dep_ref_map(metadata.dependency_refs)
    search_paths = graph._search_paths or [source_path.parent]
    rel_base = search_paths[0]

    # Which of the VI's own dependency tables each qname came from -- the
    # data-driven kind used when the qname carries no file extension (see
    # ``_dependency_kind_for``). SubVI calls win over a type reference for a
    # qname that is somehow in both (a called subVI is the stronger signal).
    subvi_qnames = {q for q in metadata.subvi_qualified_names if q}
    class_qnames = {
        t.classname
        for t in metadata.type_map.values()
        if t.classname and t.classname != "LabVIEW Object"
    }
    typedef_qnames = {
        t.typedef_name for t in metadata.type_map.values() if t.typedef_name
    }

    def _source_hint(qname: str) -> DependencyKind | None:
        if qname in subvi_qnames:
            return DependencyKind.SUBVI
        if qname in class_qnames:
            return DependencyKind.CLASS
        if qname in typedef_qnames:
            return DependencyKind.TYPEDEF
        return None

    manifest: list[NetlistDependency] = []
    for qname in sorted(all_dep_qnames):
        leaf = qname.rsplit(":", 1)[-1]
        resolved = graph._resolve_dependency_path(
            qname, dep_ref_map.get(qname), source_path, search_paths
        )
        if resolved is None and leaf.endswith(".lvclass"):
            # Mirrors lvkit.list_deps._resolve_one's caller-side walk-up
            # fallback: a class referenced only by TYPE, whose .lvclass sits
            # one directory up rather than on a search path.
            resolved = graph._walk_up_find(source_path.parent, leaf)
        kind = _dependency_kind_for(qname, _source_hint(qname))
        manifest.append(
            NetlistDependency(
                kind=kind,
                qualified=qname,
                path=_project_relative_display(resolved, rel_base),
                interface=_dependency_interface(graph, kind, qname),
            )
        )
    return manifest


def build_netlist_from_graph(graph: InMemoryVIGraph, vi_name: str) -> NetlistModule:
    """Walk a VI's ``GraphNode`` graph directly into a ``NetlistModule`` IR --
    the PHASE 1 sibling of ``build_netlist`` (see the module docstring section
    above): same IR, same net names, same byte-for-byte text/JSON output
    (``tests/test_netlist_from_graph_parity.py``).

    The netlist *body* is built by walking the graph nodes; it never reads the
    ``Operation`` projection. Facets (inputs/outputs/constants/connector
    pattern/properties/health) still come from ``get_vi_context``, which builds
    the ``Operation`` list as a side effect -- Phase 2 swaps that for the
    per-facet getters (``get_inputs``/``get_outputs``/``get_constants``/
    ``get_vi_properties``/``get_vi_health``) so no projection is built at all.
    """
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    flat = _walk_flat_gn(graph, vi_name)

    build_ctx = _GraphBuildCtx(
        occurrence_by_uid=_assign_occurrences_gn(graph, flat),
        const_by_id={c.id: c for c in ctx.constants},
        feedback_id_by_uid=_assign_sequential_ids_gn(
            flat,
            lambda n: (
                isinstance(n, GraphPrimitiveNode)
                and (fb := graph.get_feedback_info(n.id)) is not None
                and fb[0]
            ),
        ),
        constant_occurrence_by_uid=_assign_constant_occurrences(ctx.constants),
    )

    inputs = [
        NetlistBoundaryInput(
            name=t.name or "input",
            type_descriptor=t.lv_type.type_descriptor() if t.lv_type else "Any",
            lv_type=t.lv_type,
        )
        for t in ctx.inputs
    ]
    outputs = [
        BoundaryOutput(
            name=t.name or "output",
            type_descriptor=t.lv_type.type_descriptor() if t.lv_type else "Any",
            lv_type=t.lv_type,
            source=_resolve_source_gn(graph, ctx, vi_name, t.id, build_ctx),
        )
        for t in ctx.outputs
    ]

    # On-pane terminals first (unchanged order/identity -- `@<index>` rows),
    # THEN every off-pane front-panel control/indicator (`is_public=False`,
    # no connector-pane slot -- `_off_pane_terminals`), inputs before
    # outputs in both groups. `boundary_signature`/`reconstruct_module`
    # (lvnet_parse.py/lvnet_reconstruct.py) both key "is this on-pane?" off
    # `ConnectorPaneTerminal.index is None`, so ordering here only affects
    # display, never round-trip identity.
    connector_pane = ConnectorPane(
        pattern_id=ctx.connector_pattern_id,
        terminals=[_pane_terminal(t, "input") for t in ctx.inputs]
        + [_pane_terminal(t, "output") for t in ctx.outputs]
        + [
            _pane_terminal(t, "input", off_pane=True)
            for t in _off_pane_terminals(graph, vi_name, "input")
        ]
        + [
            _pane_terminal(t, "output", off_pane=True)
            for t in _off_pane_terminals(graph, vi_name, "output")
        ],
    )

    vi_def_node = graph.get_graph_node(vi_name)
    top_level_children = vi_def_node.children if vi_def_node is not None else []
    body = _build_items_gn(
        graph,
        ctx,
        vi_name,
        _top_level_nodes_gn(graph, vi_name),
        build_ctx,
        owner_children=top_level_children,
    )
    components = _build_components_gn(graph, vi_name, flat)
    dependencies = _build_dependency_manifest(graph, vi_name)

    return NetlistModule(
        vi_name=vi_name,
        inputs=inputs,
        outputs=outputs,
        body=body,
        components=components,
        properties=ctx.properties,
        health=ctx.health,
        class_context=_build_class_context(graph, ctx),
        connector_pane=connector_pane,
        dependencies=dependencies,
    )


# ============================================================
# index_module
# ============================================================
#
# Not part of either text renderer's call graph -- used only by ``diff.py``
# to look up a changed node/structure's full instance/scope by uid.
# ``component_line`` (the CLI's ``## Components`` table line) lives in
# ``netlist_render_deprecated.py`` instead -- it renders, it doesn't build.


def index_module(
    module: NetlistModule,
) -> tuple[dict[str, NetlistInstance], dict[str, NetlistScope]]:
    """Walk the IR tree, keyed by ``.uid`` -- for the diff renderer to look
    up a changed node/structure's full instance/scope by the SAME uid the
    UID-keyed ``ChangeMap`` (``diff.py``) uses."""
    instances: dict[str, NetlistInstance] = {}
    scopes: dict[str, NetlistScope] = {}

    def walk(items: list[NetlistItem]) -> None:
        for item in items:
            match item:
                case NetlistInstance():
                    instances[item.uid] = item
                case NetlistScope():
                    scopes[item.uid] = item
                    for frame in item.frames:
                        walk(frame.body)
                case NetlistFeedback():
                    # A Feedback Node is neither an instance nor a scope --
                    # not indexed here (diff tracks its uid via
                    # _walk_netlist_order for ordering only).
                    pass
                case NetlistConstant():
                    # Not indexed here either -- diff.py's use of this index
                    # predates Phase A's constant promotion.
                    pass

    walk(module.body)
    return instances, scopes
