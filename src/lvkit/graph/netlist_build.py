"""Graph -> netlist IR builder.

Builds the ``NetlistModule`` IR directly from an ``InMemoryVIGraph`` --
the graph is the single source of truth -- see ``netlist.py``'s module
docstring for the full pipeline this feeds into::

    graph (truth)  ->  NetlistModule (IR)  ->  { text render, diff render, viewer }

``build_netlist_from_graph`` is the entry point, consumed by ``lvnet``
(``render_lvnet``); ``index_module`` is a post-build index used only by
``diff.py``.

Split out of ``netlist.py`` purely to shrink it -- this is a mechanical,
behavior-preserving move, not a design change. ``netlist.py`` re-exports
``build_netlist_from_graph``/``index_module`` so every existing ``from
...graph.netlist import build_netlist_from_graph`` (etc.) call site keeps
working unchanged. This module must NEVER import from ``.netlist``
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
    EventFrame,
    FPTerminal,
    LVType,
    LVTypeKind,
    SequenceFrame,
    Terminal,
    Tunnel,
    TunnelMode,
    TunnelTerminal,
    _is_error_cluster,
)
from ..parser.node_types import get_display_name
from .core import _OPERATION_KINDS, _graph_node_to_op_kind, _uid_of
from .interface_order import ordered_interface, requirement_state
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
)
from .op_walk import (
    ComponentPort,
    _const_value_str,
    _selector_label,
    _subvi_ports,
    _terminal_display_name,
    correlate_property_terminals,
)
from .operations import frame_key
from .queries import ClassContext, collect_class_context
from .render_lvnet import (
    _lvnet_const_value_str,
    _lvnet_literal_token,
)

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


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


def _component_terminal_name(term: Terminal) -> str:
    """Terminal name for one of ``op``'s own terminals, in a synthesized
    (not wire-derived) component declaration.

    ``display_name or name or str(index)``, per the netlist naming rule --
    for an nMux/decompose LIST terminal, ``display_name`` is the real
    LabVIEW field name, stamped once graph-wide at load time (see
    ``op_walk.stamp_nmux_lane_names``).
    """
    return _terminal_display_name(term) or str(term.index)


def _port_signature(ins: list[ComponentPort], outs: list[ComponentPort]) -> str:
    """Canonical string form of a synthesized interface, used only to
    detect whether two same-identity instances genuinely differ (a real
    polymorphic collision) -- never rendered."""
    in_str = "|".join(f"{p.name}:{p.type}" for p in ins)
    out_str = "|".join(f"{p.name}:{p.type}" for p in outs)
    return f"{in_str}=>{out_str}"


def _disambiguate_cross_group_names(components: list[NetlistComponent]) -> None:
    """Second disambiguation pass, ACROSS identity groups (mutates in place).

    ``_dedupe_primitive_group_gn`` only disambiguates repeats WITHIN one
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
# build_netlist_from_graph
# ============================================================
#
# The netlist IR builder: walks the ``GraphNode`` graph directly. Every
# helper below is the builder, kept with a ``_gn`` suffix matching
# ``tests/test_netlist_from_graph_parity.py``'s naming. The model
# dataclasses and every serializer (``netlist_to_dict``, ``render_lvnet``)
# are shared VERBATIM -- nothing below this point defines a new IR shape,
# only how it's built.
#
# Node-type dispatch matches on the ``GraphNode`` hierarchy
# (``graph/models.py``): ``CaseStructureNode``, ``LoopNode``,
# ``SequenceNode``, ``DisableStructureNode``, ``EventStructureNode``,
# ``InPlaceNode``, a SubVI call -> ``VINode`` (with ``id != vi``), and
# primitive/property/invoke/Feedback-Node all share the one
# ``PrimitiveNode`` graph type (discriminated by ``.properties``/
# ``.method_name`` / ``graph.get_feedback_info``).
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
# FRAME (``node.frames`` + ``node.children`` filtered by ``child.frame``).
_FRAME_STRUCTURE_TYPES = (
    CaseStructureNode,
    SequenceNode,
    DisableStructureNode,
    EventStructureNode,
)


@dataclass(frozen=True)
class _GraphBuildCtx:
    """Per-VI lookups threaded through the graph-native build walk (bundled
    so adding a new lookup doesn't ripple through every function signature).
    Keyed identically by trailing node UID; the builder reaches a wire's
    producing node directly via ``graph.get_graph_node``/``graph.get_terminal``
    (see ``_owning_node_gn``) -- no precomputed node-tree index needed.

    No ``case_id_by_uid``/``loop_id_by_uid`` here -- a case/loop's own stable
    BD uid (``_uid_of(node.id)``, the SAME id ``NetlistScope.uid`` already
    carries) names its merge nets directly (see
    ``_tunnel_net_name_gn``/``_mu_net_name_gn``), so no separate per-structure
    sequential counter is needed.

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
    """A node's display name -- falls back from ``name`` to the node-type's
    human word to ``"Node"``.

    INTENTIONALLY DIVERGES from ``GraphNode.display_name`` (see its
    docstring): a netlist SubVI instance name is the caller-side call-site
    label (LabVIEW's own diagram name, e.g. ``listAllTestMethods_359``) --
    the CALLEE's class-qualified identity already has its own separate
    field (``NetlistInstance.qualified_name``), so prefixing the instance
    name too would duplicate it and change every class-method-call golden
    (verified empirically -- swapping in ``display_name`` here changes
    ``test_render_lvnet.py``'s golden SubVI lines from
    ``listAllTestMethods_359`` to ``TestCase.lvclass:listAllTestMethods_359``).
    """
    node_word = get_display_name(node.node_type) if node.node_type else None
    return node.name or node_word or "Node"


def _has_output_tunnel_gn(node: AnyGraphNode) -> bool:
    """True if the structure routes any value out through an output tunnel
    terminal."""
    return any(t.direction == "output" for t in node.terminals)


def _tunnels_from_terminals_gn(terminals: list[Terminal]) -> list[Tunnel]:
    """Reconstruct ``Tunnel`` objects from a structure's own terminal
    metadata -- a deliberate, purely-terminal-based DUPLICATE of
    ``OperationsMixin._tunnels_from_terminals`` (that method is a
    ``@staticmethod`` operating ONLY on ``terminals: list[Terminal]`` --
    but importing ``InMemoryVIGraph`` for real at module level here would
    add a real (non-``TYPE_CHECKING``) import this module has deliberately
    avoided; duplicating this small, pure helper is cheaper than that
    risk). Keep in sync with that method if the tunnel reconstruction rule
    ever changes.
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
    """Every OUTER-boundary output tunnel terminal on a case structure --
    one per case output (gamma) merge."""
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.boundary == "outer"
        and t.direction == "output"
    ]


def _is_gamma_output_tunnel_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """True when ``term`` is a case output tunnel genuinely fed by MORE THAN
    ONE frame (a real gamma merge, not a single-source hop-through) -- finds
    the ">1 paired inner" shape directly off ``node.terminals`` (inner
    tunnel terminals whose ``paired_id`` names this outer)."""
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
    """Every OUTER-boundary output loop tunnel terminal on a loop -- one per
    loop output (eta) merge."""
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.tunnel_type == "lpTun"
        and t.boundary == "outer"
        and t.direction == "output"
    ]


def _is_eta_output_tunnel_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """True when ``term`` is a loop's OUTER-boundary output tunnel -- an eta
    merge (the value carried out of the loop past its final iteration)."""
    if not isinstance(node, LoopNode) or not isinstance(term, TunnelTerminal):
        return False
    return (
        term.tunnel_type == "lpTun"
        and term.boundary == "outer"
        and term.direction == "output"
    )


def _is_mu_shift_register_read_gn(node: AnyGraphNode, term: Terminal) -> bool:
    """True when ``term`` reads a shift register's mu-merged net directly --
    the INNER left (``lSR``) terminal (read INSIDE the loop) or the OUTER
    right (``rSR``) terminal (read OUTSIDE it, the rarer case)."""
    if not isinstance(node, LoopNode) or not isinstance(term, TunnelTerminal):
        return False
    if term.tunnel_type == "lSR" and term.boundary == "inner":
        return True
    return term.tunnel_type == "rSR" and term.boundary == "outer"


def _loop_shift_register_pairs_gn(
    node: AnyGraphNode,
) -> list[tuple[Tunnel, Tunnel | None]]:
    """Every shift register on a loop, paired left (``lSR``) terminal with
    its matching right (``rSR``) terminal by position (``None`` when a
    left has no matching right)."""
    tunnels = _tunnels_from_terminals_gn(node.terminals)
    lsrs = [t for t in tunnels if t.tunnel_type == "lSR"]
    rsrs = [t for t in tunnels if t.tunnel_type == "rSR"]
    return [(lsr, rsrs[i] if i < len(rsrs) else None) for i, lsr in enumerate(lsrs)]


def _paired_tunnel_id_gn(term: Terminal) -> str | None:
    """A tunnel terminal's paired terminal id -- reads
    ``TunnelTerminal.paired_id`` directly. This is the paired id for every
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
    """A tunnel merge's net name (``<prefix>_<uid>.out<k>``) -- the
    structure's own stable BD uid (``_uid_of``, the SAME id
    ``NetlistScope.uid`` already uses) names the net directly."""
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
    """True when ``term`` reads a Feedback Node's output -- reads
    Feedback Node master/slave identity via ``graph.get_feedback_info``;
    at the graph level the node itself is still a plain ``PrimitiveNode``."""
    if not isinstance(node, GraphPrimitiveNode) or term.direction != "output":
        return False
    info = graph.get_feedback_info(node.id)
    return info is not None and info[0]


def _is_ipes_border_node_gn(graph: InMemoryVIGraph, node: AnyGraphNode) -> bool:
    """True when ``node`` is an In-Place-Element-Structure DECOMPOSE or
    RECOMPOSE border node (``graph.get_poser_uid`` set, and its own
    terminals carry list fields in only ONE direction; a poser_uid'd node
    with list terminals in BOTH directions, or none, is "regular", i.e.
    NOT a border node here).

    These border nodes sit on the structure's boundary, not its body, so a
    wire sourced there falls all the way through ``_resolve_source_gn`` to the
    raw ``uid.index`` structural fallback. Checked directly off the node's
    own terminals (no sibling/IPES context needed -- the classification is
    per-node)."""
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
# KINDS`` (core.py, shared with codegen) PLUS ``"local_variable"``. A
# local-variable read/write is a genuine lvnet §7 node (a distinct
# producer/sink the netlist must name), but it is NOT a codegen "operation"
# (codegen still resolves it however it already does, untouched) -- so this
# widening lives here, netlist-build-local, rather than in the shared
# ``_OPERATION_KINDS`` constant every other consumer (``operations.py``,
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
    """Given a wire endpoint's OWN carried ``node_id`` (``WireEnd.node_id``,
    set at construction time), resolve the owning ``GraphNode`` directly,
    gated to the graph-native netlist builder's "real node" kinds
    (``_GRAPH_NETLIST_NODE_KINDS`` -- ``_OPERATION_KINDS`` plus
    ``local_variable``; excludes constants/labels), explicitly excluding the
    VI's OWN definition node (``node_id == vi_name``: that node's own
    terminals are the VI's boundary controls -- ``ctx.inputs`` is the
    separate boundary-control check the caller falls through to), and
    excluding an IPES decompose/recompose border node (see
    ``_is_ipes_border_node_gn``).
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
    """Resolve the net feeding a terminal, hopping through structure tunnels
    and Feedback Node master/slave links as needed. Resolves a wire's
    producing node via ``_owning_node_gn``
    (``graph.get_graph_node``/``graph.get_terminal``-backed) -- no
    precomputed node-tree index needed."""
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
                    for t in graph.enriched_terminals(owner, vi_name)
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
            # stays populated unchanged (``netlist_to_dict`` parity -- it
            # never reads ``producer_uid``).
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
            # unchanged behavior, so ``netlist_to_dict`` never sees a
            # difference. ``constant_uid`` tags the PRODUCER (the SAME
            # trailing uid ``NetlistConstant.uid`` uses, not the full
            # qualified id -- see ``_build_constant_gn``) for
            # ``render_lvnet`` alone, which looks it up directly against its
            # own handle map (see ``_LvnetHandles.by_uid``/
            # ``_assign_lvnet_handles``) to decide whether to show the
            # inlined literal or the LABELED constant's own ``<handle>``
            # net name -- lvnet §7. ``lvnet_value`` is the SAME value,
            # escaped for lvnet's own grammar (``NetRef.lvnet_value``'s
            # docstring) -- ``render_lvnet`` prefers it; ``netlist_to_dict``
            # never reads it, so ``bare`` stays untouched.
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
    """Resolve ``terminal_id`` to its driving net, or -- when unwired -- the
    type default from the first fallback terminal that carries a resolved
    type."""
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
    """The net driving an input terminal, or ``None`` when unwired."""
    return _resolve_source_gn(graph, ctx, vi_name, terminal.id, build_ctx)


def _selector_ref_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    build_ctx: _GraphBuildCtx,
    terminal_id: str | None,
) -> NetRef | None:
    """The net driving a structure's selector terminal, or ``None`` when
    there is no selector terminal or it is unwired."""
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
    """Every accessed property on a Property Node, built straight off
    ``node.properties``/``node.property_value_terminal_ids``, so
    ``op_walk.correlate_property_terminals`` is reused verbatim."""
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
    """``graph.enriched_terminals``'s terminals, Void-filtered (see
    ``_is_real_terminal``) and reordered into the LabVIEW connector-pane's
    canonical reading order (``graph.interface_order.ordered_interface``) --
    inputs first, then outputs, each in its own canonical order.

    Required for a SubVI CALL: ``enriched_terminals`` carries the CALLEE's
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
    ``enriched_terminals`` directly, unreordered) never changes.
    """
    real = [t for t in graph.enriched_terminals(node, vi_name) if _is_real_terminal(t)]
    if not (isinstance(node, VINode) and node.id != node.vi_path and node.name):
        return real
    # Resolve the CALLEE by its own qualified identity (``node.qualified_name``,
    # e.g. "TestSuite.lvclass:run.vi"), never by ``node.name`` -- the bare
    # filename ("run.vi") collides across every same-named override in a
    # dynamic-dispatch class hierarchy (``node.vi_path`` is the CALLER's key, not
    # the callee's -- see ``GraphNode.vi``/``SubVIBuildHandler``).
    resolved = graph.resolve_vi_name(node.qualified_name or node.name)
    callee = graph.get_graph_node(resolved)
    pattern_id = callee.connector_pattern_id if isinstance(callee, VINode) else None
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
    """Build the ``NetlistInstance`` for a leaf/instance node. Reached only
    for genuine leaf/instance nodes -- ``_build_items_gn`` dispatches Feedback
    Node masters/slaves to ``_build_feedback_gn``/dissolution before this is
    ever called, so a Feedback Node is never mistaken for a plain primitive
    here.

    Phase A: keeps EVERY terminal -- wired or not -- per lvnet §3/§4 (an
    unwired input carries its type's faithful ``default`` instead of a
    driver; see ``NetlistTerminalBinding``). Deliberately does NOT drop a dead
    ``Void`` pane slot HERE (unlike ``_ordered_real_terminals_gn``, a
    presentation-only helper) -- ``netlist_to_dict`` filters back down to
    wired-only input bindings at its own read site (see the module's
    Phase A docstring note there); ``render_lvnet`` is the one consumer
    that drops a Void binding (by its own ``type`` field, at render
    time -- see ``_is_void_type``) and shows an unwired one.
    """
    uid = _uid_of(node.id)
    name = _display_name_gn(node)
    occurrence = build_ctx.occurrence_by_uid.get(uid)
    terminals = graph.enriched_terminals(node, vi_name)
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
    """Build the ``NetlistFeedback`` body item for a Feedback Node master --
    ``graph.get_feedback_info`` supplies the partner uid and delay, and the
    linked write (slave) side is fetched via ``graph.get_graph_node``."""
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
    excludes constants/labels), in the given order, widened here
    (netlist-only) to also keep a local-variable node."""
    nodes: list[AnyGraphNode] = []
    for uid in uids:
        node = graph.get_graph_node(uid)
        if node is not None and (
            _graph_node_to_op_kind(node) in _GRAPH_NETLIST_NODE_KINDS
        ):
            nodes.append(node)
    return nodes


def _top_level_nodes_gn(graph: InMemoryVIGraph, vi_name: str) -> list[AnyGraphNode]:
    """Top-level nodes in dataflow order -- ``graph.top_level_nodes`` is the
    shared tree-walk helper (``OperationsMixin``) every generator uses,
    reused directly rather than duplicating its ``get_operation_order``-based
    walk here. ``extra_kinds=("local_variable",)`` widens ONLY this call's
    own top-level scan -- codegen's default call keeps seeing none, so a
    top-level local-variable read/write joins the SAME dataflow-topological
    sort as every other node (a read orders before its consumer via the
    real wire edge between them) instead of being dropped before ordering
    even starts."""
    return graph.top_level_nodes(vi_name, extra_kinds=("local_variable",))


def _body_nodes_gn(
    graph: InMemoryVIGraph, vi_name: str, node: AnyGraphNode
) -> list[AnyGraphNode]:
    """``graph.child_nodes`` -- the shared tree-walk helper -- widened the
    same way ``_top_level_nodes_gn`` widens ``top_level_nodes``, so a
    top-level-style local-variable node nested in a structure is kept too."""
    return graph.child_nodes(node.id, vi_name, extra_kinds=("local_variable",))


def _frame_child_uids_gn(
    graph: InMemoryVIGraph,
    node: AnyGraphNode,
    frame: CaseFrame | SequenceFrame | EventFrame,
    position: int,
) -> list[str]:
    """RAW child uids of ``node`` belonging to ONE frame (matching each
    child's own ``.frame`` attribute against :func:`frame_key`),
    unsorted/unfiltered-by-kind. Split out of ``_frame_nodes_gn`` so
    ``_build_items_gn``'s labeled-constant extraction
    (``_labeled_constant_items_gn``) can find a constant child the SAME way
    an instance child is found, without ``_resolve_op_nodes_gn``'s
    ``_OPERATION_KINDS`` gate dropping it first."""
    key = frame_key(frame, position)
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
    """The nodes belonging to ONE frame: ``node.children`` filtered to this
    frame's key (matching each child's own ``.frame`` attribute against
    :func:`frame_key`), then dataflow-sorted exactly like ``_body_nodes_gn``.
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
    Element Structure (``_walk_flat_gn``/``_build_items_gn`` only ever
    recurse into THIS subset; decompose/recompose nodes are dropped from the
    flat walk/body entirely -- see the module's Phase 1 docstring). Filters via
    ``_is_ipes_border_node_gn`` -- the SAME per-node classification
    ``_owning_node_gn`` uses to exclude a border node's terminals from
    wire-source resolution."""
    all_nodes = _body_nodes_gn(graph, vi_name, node)
    return [n for n in all_nodes if not _is_ipes_border_node_gn(graph, n)]


def _walk_flat_nodes_gn(
    graph: InMemoryVIGraph, vi_name: str, nodes: list[AnyGraphNode]
) -> list[AnyGraphNode]:
    """Flatten ``nodes`` into a single depth-first list: frame-structures
    recurse per-frame, an In-Place Element Structure recurses into its
    "regular" children only, and everything else recurses into its
    (dataflow-sorted) children."""
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
    """Assign an ``#n`` occurrence tag to each instance whose display name
    repeats among the flat node list (§9), in source order. Excludes
    structures and Feedback Node masters/slaves, which never take one."""
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
    """A 0-based sequential id per node matching ``predicate``, in flat
    source order."""
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
    """Build one ``GammaMerge`` per case output tunnel, with one
    ``GammaCase`` per frame (the source that tunnel resolves to when that
    frame runs)."""
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
    """The selector terminal's own resolved ``LVType``, or ``None`` when
    there is no selector terminal or it has no resolved type."""
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
    """Build the ``NetlistScope`` for a case structure: one frame per case,
    plus the case output (gamma) merges."""
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
    """Build the ``NetlistScope`` for a Diagram/Conditional/Type-
    Specialization Disable structure: one frame per disable branch."""
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
    """Build the ``NetlistScope`` for an Event structure: one frame per
    event case."""
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
    """Build the ``NetlistScope`` for a Sequence structure: one frame per
    sequence frame, in index order."""
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
    """Build one ``MuMerge`` per shift register on a loop, pairing each
    left/right terminal into its ``init``/``recur`` sources."""
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
    """Build one ``EtaMerge`` per loop output tunnel."""
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
    """Build the ``NetlistScope`` for a For/While loop: its single implicit
    body frame, plus its shift-register (mu) and output-tunnel (eta)
    merges."""
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
    """Build the body items (instances, scopes, feedback nodes, promoted
    labeled constants) for one container -- a VI's top level or a
    structure's frame.

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
            # ``_build_feedback_gn``).
            if fb[0]:
                items.append(_build_feedback_gn(graph, ctx, vi_name, node, build_ctx))
        else:
            items.append(_build_instance_gn(graph, ctx, vi_name, node, build_ctx))
    return items


def _is_subvi_call_gn(node: AnyGraphNode) -> bool:
    """True when ``node`` is a genuine SubVI CALL. Every ``VINode``
    reached by the flat walk is inherently a SubVI CALL (``node.id !=
    node.vi_path``) -- the VI's own definition node is never part of it (see
    ``_owning_node_gn``/``get_operation_order``)."""
    return isinstance(node, VINode) and bool(node.name)


def _is_primitive_component_gn(graph: InMemoryVIGraph, node: AnyGraphNode) -> bool:
    """True when ``node`` becomes its own declared component: a plain
    primitive, NEVER a property/invoke node, and NEVER a Feedback Node."""
    return (
        isinstance(node, GraphPrimitiveNode)
        and not node.properties
        and not node.method_name
        and graph.get_feedback_info(node.id) is None
    )


def _component_identity_gn(node: AnyGraphNode) -> tuple[object, ...]:
    """A primitive's component-identity key -- keyed off
    ``PrimitiveNode.prim_id``, distinguishing e.g. an And from an Or
    cpdArith so they never collide as the same declared component."""
    prim_res_id = node.prim_id if isinstance(node, GraphPrimitiveNode) else None
    operation = node.operation if isinstance(node, GraphPrimitiveNode) else None
    return (node.node_type or "unknown", prim_res_id, operation)


def _synthesize_ports_gn(
    terminals: list[Terminal],
) -> tuple[list[ComponentPort], list[ComponentPort]]:
    """Build a component's declared input/output port lists from its
    terminals, sorted by pane index -- takes the
    terminal list directly (rather than a node) so a SubVI-call fallback can
    pass ``graph.enriched_terminals``'s CALLEE-enriched names, same as
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
    """Collapse a group of instances sharing one component identity into its
    declared ``NetlistComponent``(s) -- one per distinct typed port
    signature, disambiguated by a `` (n)`` suffix when a polymorphic
    primitive genuinely has more than one signature within the group."""
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
    """Build the module's declared ``NetlistComponent`` list: one entry per
    distinct subVI or primitive/nMux/cpdArith component actually used,
    grouped by component identity and deduped by typed port signature."""
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
            ins, outs = _synthesize_ports_gn(graph.enriched_terminals(rep, vi_name))
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
    cross-drive path on Windows).
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
    """The lvnet §7 ``uses :`` manifest: every external file this VI directly
    depends on (subVI calls, referenced classes, referenced typedefs) -- read
    ENTIRELY from the resolved dependency graph, never a re-parse. Direct deps
    come from ``graph.get_vi_dependencies`` (the path-keyed ``_dep_graph``
    successors the loader already resolved, #26); each dep's on-disk path from
    ``graph._dependency_file_path`` (loaded / absent-stub / pseudo-root cases
    all handled); its display qname from the dep node's own ``qname`` or, for a
    minimal-load bare stub, from the caller's own subVI node
    (``iter_nodes`` -> ``owning_libraries``/``name`` -- the SAME identity the
    block-diagram prints); its interface from the graph. Empty only when the VI
    has no dependencies -- never a guess, never a filesystem re-resolution.
    """
    vi_name = graph.resolve_vi_name(vi_name)
    dep_keys = graph.get_vi_dependencies(vi_name)
    if not dep_keys:
        return []

    # Caller-scoped subVI identities straight off the graph's own nodes -- the
    # SAME qualified-name + owning-library chain the block-diagram prints. Keyed
    # by the on-disk subpath they imply (``Lib1/Class/Do.vi``), so each resolved
    # dependency path can be given its display qname even when a minimal-load
    # dep node is a bare path-keyed stub with no qname attribute of its own.
    def _subpath(ol: list[str], name: str) -> str:
        return "/".join([*(Path(p).stem for p in ol), name])

    subvi_qname_by_subpath: dict[str, str] = {}
    for n in graph.iter_nodes(vi_name):
        if getattr(n, "node_type", None) != "iUse":
            continue
        ol = list(getattr(n, "owning_libraries", None) or [])
        name = getattr(n, "name", None) or ""
        if not name:
            continue
        # Full qualified name from the owning chain (the bare ``qualified_name``
        # field is only partially qualified for some intra-project callees).
        qname = (
            ":".join([*ol, name])
            if ol
            else (getattr(n, "qualified_name", None) or name)
        )
        subvi_qname_by_subpath[_subpath(ol, name)] = qname

    own = graph.get_vi_source_path(vi_name)
    search = graph._search_paths or ([own.parent] if own is not None else [])
    rel_base = search[0] if search else Path.cwd()

    # A class/library member .vi lives in the SAME directory as its owning
    # .lvclass/.lvlib -- so a member whose own node came out unqualified is
    # qualified by the class dependency sitting beside it (both are already in
    # this manifest's dep set). Graph-native: no path re-resolution.
    class_qname_by_dir: dict[str, str] = {}
    for key in dep_keys:
        n = graph._dep_graph.nodes.get(key, {})
        if n.get("node_type") in ("library", "class"):
            p = graph._dependency_file_path(key)
            if p is not None and n.get("qname"):
                class_qname_by_dir[str(p.parent)] = n["qname"]

    manifest: list[NetlistDependency] = []
    seen: set[str] = set()
    for key in dep_keys:
        node = graph._dep_graph.nodes.get(key, {})
        resolved = graph._dependency_file_path(key)
        # qname: the dep node's own (typedefs/classes/loaded VIs carry it);
        # else the caller's subVI node matched by on-disk subpath; else the leaf.
        qname = node.get("qname")
        if not qname:
            kpath = str(resolved or key)
            qname = next(
                (q for sp, q in subvi_qname_by_subpath.items() if kpath.endswith(sp)),
                None,
            ) or (resolved.name if resolved else Path(key).name)
        # A bare subVI-member name is qualified by the class beside it on disk
        # (only .vi members -- never the .lvclass/.lvlib node itself, nor a
        # typedef, which stay on their own bare name).
        if (
            ":" not in qname
            and resolved is not None
            and resolved.suffix.lower() == ".vi"
        ):
            owner = class_qname_by_dir.get(str(resolved.parent))
            if owner:
                qname = f"{owner}:{resolved.name}"
        if qname in seen:
            continue
        seen.add(qname)

        nt = node.get("node_type")
        hint: DependencyKind | None = None
        if nt == "typedef":
            hint = DependencyKind.TYPEDEF
        elif nt in ("library", "class"):
            hint = DependencyKind.CLASS
        elif qname in subvi_qname_by_subpath.values() or (
            resolved is not None and resolved.suffix.lower() == ".vi"
        ):
            hint = DependencyKind.SUBVI
        kind = _dependency_kind_for(qname, hint)
        manifest.append(
            NetlistDependency(
                kind=kind,
                qualified=qname,
                path=_project_relative_display(resolved, rel_base),
                interface=_dependency_interface(graph, kind, qname),
            )
        )
    return sorted(manifest, key=lambda d: d.qualified)


def build_netlist_from_graph(graph: InMemoryVIGraph, vi_name: str) -> NetlistModule:
    """Walk a VI's ``GraphNode`` graph directly into a ``NetlistModule`` IR:
    same IR, same net names, same byte-for-byte text/JSON output.

    The netlist *body* is built by walking the graph nodes directly. Facets
    (inputs/outputs/constants/connector pattern/properties/health) come from
    ``get_vi_context``.
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
