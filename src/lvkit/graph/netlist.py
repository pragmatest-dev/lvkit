"""Graph -> netlist text projection.

LabVIEW is the *schematic* view of code; this module adds the *netlist* --
the text form a schematic always has in EDA. Prior art deliberately copied:
structural HDL netlists (named signals + instances + nested scopes) and
compiler SSA/IR (named values). See ``.tmp/netlist-spec.md`` for the full
design contract (syntax is LOCKED).

ONE canonical projection, three consumers::

    graph (truth)  ->  NetlistModule (IR)  ->  { text render, diff render, viewer }

This module builds the IR (``build_netlist``) and renders it to text
(``render_netlist``). It reuses the same structure-walk helpers as
``describe.py`` (``_find_op_owning_terminal``, ``_has_output_tunnel``,
factored into ``op_walk.py`` to avoid a circular import between the two)
rather than duplicating the walk.

The IR also carries ``NetlistModule.components`` -- the Verilog-module /
VHDL-entity half of the netlist: every distinct subVI/primitive's typed
port interface, declared once (see ``_build_components``), alongside
``body``'s per-call instantiations. ``describe.py`` renders it as
``## Components``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import (
    CaseOperation,
    ClusterField,
    DisableStructureOperation,
    InPlaceOperation,
    LoopOperation,
    LVType,
    Operation,
    PrimitiveOperation,
    SequenceOperation,
    Terminal,
    _is_error_cluster,
)
from .models import Constant, VIContext, WireEnd
from .op_walk import (
    ComponentPort,
    _const_value_str,
    _find_op_owning_terminal,
    _has_output_tunnel,
    _is_nmux,
    _nmux_agg_fields,
    _nmux_raw_field_name,
    _paired_tunnel_id,
    _selector_label,
    _subvi_ports,
)

if TYPE_CHECKING:
    from .core import InMemoryVIGraph

# ============================================================
# The IR
# ============================================================


@dataclass(frozen=True)
class NetRef:
    """A reference to one net (wire) in the netlist.

    ``node``/``port`` identify the PRODUCING terminal (EDA ``refdes.pin``):
    ``node`` is the producing operation's display name, or ``None`` when the
    net is a VI-boundary control. ``port`` is the terminal NAME when known,
    else its index. ``occurrence`` is the ``#n`` disambiguator for repeated
    node display names (``None`` when the name is unique in the VI).
    ``bare`` is the net's short display name -- see the naming rule in
    ``.tmp/netlist-spec.md``.
    """

    node: str | None
    port: str
    occurrence: int | None
    bare: str

    def render(self, *, qualified: bool) -> str:
        """Render this net reference.

        ``qualified`` -> ``"CallTestMethod.execTime"`` / ``"Not#2.0"``;
        unqualified (or boundary, ``node is None``) -> ``bare`` (e.g.
        ``"execTime"``).
        """
        if not qualified or self.node is None:
            return self.bare
        tag = f"#{self.occurrence}" if self.occurrence else ""
        return f"{self.node}{tag}.{self.port}"


@dataclass(frozen=True)
class NetlistPortBinding:
    """One wired input: THIS instance's declared input PORT name, tied to
    the source ``NetRef`` feeding it.

    Verilog ``.port(net)`` / VHDL ``port => signal`` / Python-kwargs named-
    port association -- the rendered form is ``port=net`` (see
    ``instance_line``). ``port`` is the input terminal's own name (the same
    naming rule as everywhere else in this module: ``display_name or name or
    str(index)``, or the real nMux field name -- see ``_component_port_name``),
    NOT the source net's name.
    """

    port: str
    net: NetRef


@dataclass
class NetlistInstance:
    """One node instance -- a primitive, SubVI call, or other leaf op."""

    uid: str  # trailing node UID (matches ElementChange.uid / SVG data-node)
    name: str  # node / subVI / primitive display name
    occurrence: int | None
    inputs: list[NetlistPortBinding]  # port=net binding per wired input port
    outputs: list[NetRef]  # net produced at each output port, in terminal order


@dataclass
class NetlistFrame:
    """One frame of a scope (a case's frame, a sequence's frame, or a
    loop's single implicit body)."""

    # Faithful DISPLAY text -- "No Error"/enum item name(s)/range(s)/quoted
    # string/"True"/"False"/"Default" for a case frame (via
    # ``op_walk._selector_label``, the same labeler the renderer uses), the
    # raw index for a sequence frame. What ``render_netlist`` prints.
    label: str
    # The raw selector value (case) / index (sequence), always ``str()``.
    # Kept SEPARATE from ``label`` because ``diff.py`` keys frames across two
    # VI versions, and encodes ``data-path`` for the SVG (``_extend_frame_path``/
    # ``render/scene.py::encode_frame_path``) by this raw token -- identity
    # must stay stable even when the display label is enum-name-aware.
    value: str
    is_default: bool
    body: list[NetlistItem]
    # When ``body`` is empty: True -> render "(pass-through)" (the structure
    # has an output tunnel, so LabVIEW still routes a value through even
    # though this frame has no operations); False -> "(empty)". Computed at
    # build time via ``_has_output_tunnel`` since render_netlist only sees
    # the IR, not the original graph operation.
    passthrough: bool = False


@dataclass
class NetlistScope:
    """A structure: case / for / while / sequence / disabled."""

    uid: str
    kind: str  # "case" | "for" | "while" | "sequence" | "disabled"
    selector: NetRef | None
    frames: list[NetlistFrame]


NetlistItem = NetlistInstance | NetlistScope


@dataclass
class NetlistComponent:
    """One distinct component DECLARATION -- the Verilog-module /
    VHDL-entity half of the netlist: a typed port interface, declared once
    regardless of how many ``NetlistInstance``s call it.

    ``name`` is the VI name for a subVI, or the primitive/nMux/cpdArith
    display name for a leaf op -- with a `` (n)`` disambiguator suffix in
    the rare case where two instances sharing the same underlying
    primitive genuinely have different typed interfaces (a real
    polymorphic collision, not a merge bug -- see ``_dedupe_primitive_group``).
    """

    name: str
    inputs: list[ComponentPort]
    outputs: list[ComponentPort]


@dataclass(frozen=True)
class _BuildCtx:
    """Per-VI lookups threaded through the build walk (bundled so adding a
    new lookup doesn't ripple through every function signature).

    ``occurrence_by_uid``: ``#n`` disambiguator for repeated node display
    names, keyed by trailing node UID (see ``_assign_occurrences``).
    ``const_by_id``: every constant in the VI, keyed by its graph node id
    (``Constant.id``, matching a wire source's ``WireEnd.node_id``) -- lets
    ``_resolve_source`` join a wire's real constant VALUE instead of just
    the producing node's uid.port.
    """

    occurrence_by_uid: dict[str, int]
    const_by_id: dict[str, Constant]


@dataclass
class NetlistModule:
    """The whole VI as a netlist."""

    vi_name: str
    inputs: list[tuple[str, str]]  # (name, python_type) non-error only
    outputs: list[tuple[str, str]]
    body: list[NetlistItem] = field(default_factory=list)
    # Every distinct component (subVI or primitive/nMux/cpdArith) actually
    # used in the VI, declared once -- see ``_build_components``. Sorted by
    # name for a deterministic ``## Components`` rendering.
    components: list[NetlistComponent] = field(default_factory=list)


# ============================================================
# build_netlist
# ============================================================


def _uid_of(op_id: str) -> str:
    """Trailing UID from an op.id ('...run.vi::1065' -> '1065')."""
    return op_id.rsplit("::", 1)[-1]


def _display_name(op: Operation) -> str:
    return op.name or op.node_type or "Node"


def _walk_flat(operations: list[Operation]) -> list[Operation]:
    """Flatten the operation tree in the same deterministic order
    ``_describe_op_list`` recurses it, for name-occurrence counting.

    Case/Sequence/Disable-structure frames recurse into
    ``frame.operations``; everything else recurses into ``inner_nodes``
    (this covers loop bodies and IPES bodies, matching describe.py's
    generic fallback).
    """
    flat: list[Operation] = []
    for op in operations:
        flat.append(op)
        match op:
            case CaseOperation() | SequenceOperation() | DisableStructureOperation():
                for frame in op.frames:
                    flat.extend(_walk_flat(frame.operations))
            case _:
                if op.inner_nodes:
                    flat.extend(_walk_flat(op.inner_nodes))
    return flat


def _assign_occurrences(root_ops: list[Operation]) -> dict[str, int]:
    """Assign ``#n`` occurrence numbers to node UIDs whose display name
    repeats within the VI. 1-based, in deterministic node order (the
    operation tree is already produced in ``_node_order_key`` order by the
    graph layer -- see the deterministic-node-order rule). Names that are
    unique in the VI are absent from the returned map (no tag)."""
    flat = _walk_flat(root_ops)
    names = [_display_name(op) for op in flat]
    counts = Counter(names)

    occurrence_by_uid: dict[str, int] = {}
    running: dict[str, int] = {}
    for op, name in zip(flat, names, strict=True):
        if counts[name] > 1:
            running[name] = running.get(name, 0) + 1
            occurrence_by_uid[_uid_of(op.id)] = running[name]
    return occurrence_by_uid


def _term_ref(
    graph: InMemoryVIGraph,
    node_name: str,
    occurrence: int | None,
    op: Operation,
    term: Terminal,
) -> NetRef:
    """Build a NetRef for a terminal OWNED by (produced at) ``node_name``.

    Naming rule: a terminal with a resolved display name uses that name as
    ``bare`` -- preferring ``display_name`` (the resolved-def terminal name,
    ``construction.py``) over the codegen ``name``, and for an nMux
    (Bundle/Unbundle By Name) LIST output, the real LabVIEW field name via
    ``Terminal.nmux_field_index`` (``op_walk._nmux_raw_field_name``, same
    index resolution ``codegen/nodes/nmux.py`` uses) over a meaningless
    numeric index. An unnamed terminal's ``bare`` is the only place a
    number-port shows (``Node[#n].idx``), fully qualified up front since a
    bare index alone would be meaningless.
    """
    label = None
    if term.direction == "output" and _is_nmux(op):
        label = _nmux_raw_field_name(term, _nmux_agg_fields(op, graph))
    if label is None:
        label = term.display_name or term.name
    port = label or str(term.index)
    if label:
        bare = label
    else:
        tag = f"#{occurrence}" if occurrence else ""
        bare = f"{node_name}{tag}.{term.index}"
    return NetRef(node=node_name, port=port, occurrence=occurrence, bare=bare)


def _resolve_source(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
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

        hit = _find_op_owning_terminal(root_ops, src.terminal_id)
        if hit is not None:
            op, term = hit
            paired = _paired_tunnel_id(op, term)
            if paired is not None and paired not in seen:
                tid = paired
                continue
            node_name = _display_name(op)
            occurrence = build_ctx.occurrence_by_uid.get(_uid_of(op.id))
            return _term_ref(graph, node_name, occurrence, op, term)

        for t in ctx.inputs:
            if t.id == src.terminal_id:
                bare = t.name or str(t.index)
                return NetRef(node=None, port=bare, occurrence=None, bare=bare)

        # A wire fed directly by a constant renders the constant's real
        # VALUE (a literal), not the structural uid.port fallback below --
        # a literal has no producing node, so ``node=None`` (same
        # convention as a boundary control) and it always renders bare.
        const = build_ctx.const_by_id.get(src.node_id)
        if const is not None:
            value_str = _const_value_str(const)
            return NetRef(
                node=None, port=value_str, occurrence=None, bare=value_str,
            )

        # Structural fallback: identify by the wire source's own carried
        # info (never invent a placeholder like "x").
        node_name = src.name or _uid_of(src.node_id)
        port = str(src.index) if src.index is not None else src.terminal_id
        return NetRef(
            node=node_name, port=port, occurrence=None, bare=f"{node_name}.{port}",
        )


def _input_ref(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
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
    return _resolve_source(graph, ctx, root_ops, terminal.id, build_ctx)


def _selector_ref(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
    build_ctx: _BuildCtx,
    terminal_id: str | None,
) -> NetRef | None:
    """Net gating a case selector / loop stop-condition, or None when there
    is no terminal to trace or its source can't be located."""
    if not terminal_id:
        return None
    return _resolve_source(graph, ctx, root_ops, terminal_id, build_ctx)


def _build_instance(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
    op: Operation,
    build_ctx: _BuildCtx,
) -> NetlistInstance:
    uid = _uid_of(op.id)
    name = _display_name(op)
    occurrence = build_ctx.occurrence_by_uid.get(uid)
    class_fields = _nmux_agg_fields(op, graph) if _is_nmux(op) else None
    inputs = [
        NetlistPortBinding(port=_component_port_name(t, class_fields), net=ref)
        for t in op.terminals
        if t.direction == "input" and not t.is_error_cluster
        if (ref := _input_ref(graph, ctx, root_ops, build_ctx, t))
        is not None
    ]
    outputs = [
        _term_ref(graph, name, occurrence, op, t)
        for t in op.terminals
        if t.direction == "output" and not t.is_error_cluster
    ]
    return NetlistInstance(
        uid=uid, name=name, occurrence=occurrence, inputs=inputs, outputs=outputs,
    )


def _build_items(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    operations: list[Operation],
    root_ops: list[Operation],
    build_ctx: _BuildCtx,
) -> list[NetlistItem]:
    """Walk a list of operations exactly like ``_describe_op_list``
    recurses, projecting each into a ``NetlistInstance`` or
    ``NetlistScope``."""
    items: list[NetlistItem] = []
    for op in operations:
        match op:
            case CaseOperation():
                items.append(
                    _build_case_scope(graph, ctx, root_ops, op, build_ctx)
                )
            case DisableStructureOperation():
                items.append(
                    _build_disabled_scope(graph, ctx, root_ops, op, build_ctx)
                )
            case LoopOperation():
                items.append(
                    _build_loop_scope(graph, ctx, root_ops, op, build_ctx)
                )
            case SequenceOperation():
                items.append(
                    _build_sequence_scope(graph, ctx, root_ops, op, build_ctx)
                )
            case _:
                items.append(
                    _build_instance(graph, ctx, root_ops, op, build_ctx)
                )
                if op.inner_nodes:
                    items.extend(
                        _build_items(
                            graph, ctx, op.inner_nodes, root_ops, build_ctx,
                        )
                    )
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
    root_ops: list[Operation],
    op: CaseOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    selector = _selector_ref(
        graph, ctx, root_ops, build_ctx, op.selector_terminal,
    )
    passthrough = _has_output_tunnel(op)
    lv_type = _selector_lv_type(op, op.selector_terminal)
    is_error = bool(lv_type and _is_error_cluster(lv_type))
    frames = [
        NetlistFrame(
            label=_selector_label(frame, lv_type, is_error),
            value=str(frame.selector_value),
            is_default=frame.is_default,
            body=_build_items(
                graph, ctx, frame.operations, root_ops, build_ctx,
            ),
            passthrough=passthrough,
        )
        for frame in op.frames
    ]
    return NetlistScope(
        uid=_uid_of(op.id), kind="case", selector=selector, frames=frames,
    )


def _build_disabled_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
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
            body=_build_items(
                graph, ctx, frame.operations, root_ops, build_ctx,
            ),
            passthrough=passthrough,
        )
        for frame in op.frames
    ]
    return NetlistScope(
        uid=_uid_of(op.id), kind="disabled", selector=None, frames=frames,
    )


def _build_sequence_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
    op: SequenceOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    frames = [
        NetlistFrame(
            label=str(frame.index),
            value=str(frame.index),
            is_default=False,
            body=_build_items(
                graph, ctx, frame.operations, root_ops, build_ctx,
            ),
            passthrough=False,
        )
        for frame in op.frames
    ]
    return NetlistScope(
        uid=_uid_of(op.id), kind="sequence", selector=None, frames=frames,
    )


def _build_loop_scope(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
    op: LoopOperation,
    build_ctx: _BuildCtx,
) -> NetlistScope:
    kind = "while" if op.loop_type == "whileLoop" else "for"
    selector = _selector_ref(
        graph, ctx, root_ops, build_ctx, op.stop_condition_terminal,
    )
    body = _build_items(graph, ctx, op.inner_nodes, root_ops, build_ctx)
    frame = NetlistFrame(
        label="",
        value="",
        is_default=False,
        body=body,
        passthrough=_has_output_tunnel(op),
    )
    return NetlistScope(
        uid=_uid_of(op.id), kind=kind, selector=selector, frames=[frame],
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
    CaseOperation, LoopOperation, SequenceOperation,
    DisableStructureOperation, InPlaceOperation,
)


def _is_subvi_call(op: Operation) -> bool:
    """Same test ``describe._collect_subvi_names`` uses: a labeled SubVI
    call with a resolvable callee name."""
    return "SubVI" in op.labels and bool(op.name)


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


def _component_port_name(
    term: Terminal, class_fields: list[ClusterField] | None,
) -> str:
    """Port name for one of ``op``'s own terminals, in a synthesized
    (not wire-derived) component declaration.

    For an nMux LIST terminal (a named field on a Bundle/Unbundle By Name
    node -- input side for Bundle, output side for Unbundle), the real
    LabVIEW field name via ``_nmux_raw_field_name``. Everything else:
    ``display_name or name or str(index)``, per the netlist naming rule.
    """
    if term.nmux_role == "list":
        name = _nmux_raw_field_name(term, class_fields)
        if name is not None:
            return name
    return term.display_name or term.name or str(term.index)


def _synthesize_ports(
    op: Operation, graph: InMemoryVIGraph,
) -> tuple[list[ComponentPort], list[ComponentPort]]:
    """Typed (inputs, outputs) for a primitive-like leaf op, from its OWN
    terminals -- there is no ``.vi`` connector pane to read a signature
    from. Error-cluster terminals are omitted, matching every other
    interface in this module (subVI signatures, the VI's own signature
    line)."""
    class_fields = _nmux_agg_fields(op, graph) if _is_nmux(op) else None
    ins: list[ComponentPort] = []
    outs: list[ComponentPort] = []
    for t in sorted(op.terminals, key=lambda t: t.index):
        if t.is_error_cluster:
            continue
        port = ComponentPort(
            name=_component_port_name(t, class_fields), type=t.python_type(),
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
    graph: InMemoryVIGraph, root_ops: list[Operation],
) -> list[NetlistComponent]:
    """Every distinct component (subVI or primitive-like leaf) actually
    used anywhere in the VI, sorted by declared name. Structures
    (case/loop/sequence/disabled/IPES) are containers, not components, and
    are excluded -- ``## Netlist`` already shows their scopes."""
    subvi_order: list[str] = []
    seen_subvi: set[str] = set()
    groups: dict[
        tuple[object, ...],
        list[tuple[Operation, list[ComponentPort], list[ComponentPort]]],
    ] = {}

    for op in _walk_flat(root_ops):
        if isinstance(op, _STRUCTURE_OPERATION_TYPES):
            continue
        if _is_subvi_call(op):
            name = op.name
            assert name is not None  # narrowed by _is_subvi_call
            if name not in seen_subvi:
                seen_subvi.add(name)
                subvi_order.append(name)
            continue
        if not isinstance(op, PrimitiveOperation):
            continue
        ins, outs = _synthesize_ports(op, graph)
        key = _component_identity(op)
        groups.setdefault(key, []).append((op, ins, outs))

    components: list[NetlistComponent] = []
    for name in subvi_order:
        ports = _subvi_ports(graph, name)
        ins, outs = ports if ports is not None else ([], [])
        components.append(NetlistComponent(name=name, inputs=ins, outputs=outs))
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

    build_ctx = _BuildCtx(
        occurrence_by_uid=_assign_occurrences(root_ops),
        const_by_id={c.id: c for c in ctx.constants},
    )

    inputs = [
        (t.name or "input", t.python_type())
        for t in ctx.inputs
        if not t.is_error_cluster
    ]
    outputs = [
        (t.name or "output", t.python_type())
        for t in ctx.outputs
        if not t.is_error_cluster
    ]

    body = _build_items(graph, ctx, root_ops, root_ops, build_ctx)
    components = _build_components(graph, root_ops)

    return NetlistModule(
        vi_name=vi_name, inputs=inputs, outputs=outputs, body=body,
        components=components,
    )


# ============================================================
# render_netlist
# ============================================================


def _collect_refs(items: list[NetlistItem]) -> list[NetRef]:
    """Every NetRef appearing anywhere in a body (inputs, outputs, and
    scope selectors), for global bare-name disambiguation."""
    refs: list[NetRef] = []
    for item in items:
        match item:
            case NetlistInstance():
                refs.extend(b.net for b in item.inputs)
                refs.extend(item.outputs)
            case NetlistScope():
                if item.selector is not None:
                    refs.append(item.selector)
                for frame in item.frames:
                    refs.extend(_collect_refs(frame.body))
    return refs


def ambiguous_bares(module: NetlistModule) -> set[str]:
    """Bare names that map to more than one distinct producing net.

    Disambiguation rule: if two DIFFERENT source terminals would render the
    same ``bare``, both must be qualified wherever referenced (e.g.
    ``startTest.TestCase`` vs ``defaultTestResult.TestCase`` instead of two
    ambiguous ``TestCase``).
    """
    bare_to_identities: dict[str, set[tuple[str | None, int | None, str]]] = {}
    for ref in _collect_refs(module.body):
        identity = (ref.node, ref.occurrence, ref.port)
        bare_to_identities.setdefault(ref.bare, set()).add(identity)
    return {bare for bare, ids in bare_to_identities.items() if len(ids) > 1}


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

    walk(module.body)
    return instances, scopes


def instance_line(instance: NetlistInstance, ambiguous: set[str]) -> str:
    """NODE-FIRST netlist line: ``"name(ins) -> outs"`` (``"name(ins)"`` when no
    outputs) -- NO indent or gutter; callers own that.

    Node-first is the real netlist convention: SPICE (``R1 n1 n2 1k``) and
    Verilog (``and2 u1 (.a(w1), .b(w2))``) both lead with the COMPONENT, then
    its connections. The node is the subject; wires are its attributes. It also
    makes a VI and each node inside it read the SAME shape (``NAME(ins) -> outs``,
    matching the ``render_netlist`` signature line) and keeps a diff node-centric
    -- the changed node's name sits right after the ``+/-/~`` gutter.

    Inputs use NAMED-PORT association (Verilog ``.port(net)`` / VHDL
    ``port => signal`` / Python kwargs), rendered ``port=net`` -- each wire
    is tied to the declared input port it feeds, not left positional.
    """
    tag = f"#{instance.occurrence}" if instance.occurrence else ""
    name_disp = f"{instance.name}{tag}"
    ins = ", ".join(
        f"{b.port}={b.net.render(qualified=b.net.bare in ambiguous)}"
        for b in instance.inputs
    )
    base = f"{name_disp}({ins})"
    if instance.outputs:
        outs = ", ".join(ref.render(qualified=False) for ref in instance.outputs)
        return f"{base} -> {outs}"
    return base


def component_line(component: NetlistComponent) -> str:
    """NODE-FIRST typed interface DECLARATION: ``"name(a: T, b: U) -> (c: V)"``
    (``"name(ins)"`` when there are no outputs) -- NO indent; callers own that.

    Unlike ``instance_line`` (a CALL -- unparenthesized outputs, wire names),
    this is a DECLARATION -- typed ports, outputs parenthesized like the
    inputs, matching the VI's own signature line and the subVI signature
    ``## Dependencies`` already prints (``op_walk._render_ports``).
    """
    ins = ", ".join(f"{p.name}: {p.type}" for p in component.inputs)
    base = f"{component.name}({ins})"
    if not component.outputs:
        return base
    outs = ", ".join(f"{p.name}: {p.type}" for p in component.outputs)
    return f"{base} -> ({outs})"


def scope_header(scope: NetlistScope, ambiguous: set[str]) -> str:
    """``"case (sel):"`` / ``"while (sel):"`` / ``"for (sel):"`` /
    ``"sequence:"`` / ``"disabled:"`` -- NO indent, no frames."""
    if scope.kind == "sequence":
        return "sequence:"
    sel_str = None
    if scope.selector is not None:
        sel_str = scope.selector.render(qualified=scope.selector.bare in ambiguous)
    return f"{scope.kind} ({sel_str}):" if sel_str else f"{scope.kind}:"


def _render_instance(
    instance: NetlistInstance, indent: int, lines: list[str], ambiguous: set[str],
) -> None:
    prefix = "  " * indent
    lines.append(f"{prefix}{instance_line(instance, ambiguous)}")


def _render_frame_body(
    frame: NetlistFrame, indent: int, lines: list[str], ambiguous: set[str],
) -> None:
    if frame.body:
        _render_items(frame.body, indent, lines, ambiguous)
    elif frame.passthrough:
        lines.append("  " * indent + "(pass-through)")
    else:
        lines.append("  " * indent + "(empty)")


def _quoted_frame_label(label: str) -> str:
    """Wrap a case/disabled frame label in ONE pair of quotes for netlist
    display.

    Most labels (``No Error``, ``True``, ``Default``, an enum item name, an
    integer range) carry no quoting of their own and need one pair added
    here. A STRING selector is the exception: ``op_walk._selector_label``
    already returns it pre-quoted (``'"TestCase.lvclass"'``) so the renderer
    -- ``render/scene.py`` -- can show it as-is with no extra formatting.
    Re-wrapping that case here would double-quote it (``""...""``), so
    detect an already-quoted label and pass it through unchanged instead.
    """
    if len(label) >= 2 and label.startswith('"') and label.endswith('"'):
        return label
    return f'"{label}"'


def _render_scope(
    scope: NetlistScope, indent: int, lines: list[str], ambiguous: set[str],
) -> None:
    prefix = "  " * indent
    lines.append(f"{prefix}{scope_header(scope, ambiguous)}")

    if scope.kind in ("case", "disabled"):
        for frame in scope.frames:
            default = " (default)" if frame.is_default else ""
            label = _quoted_frame_label(frame.label)
            lines.append(f"{prefix}  {label}{default}:")
            _render_frame_body(frame, indent + 2, lines, ambiguous)
    elif scope.kind == "sequence":
        for frame in scope.frames:
            lines.append(f"{prefix}  frame {frame.label}:")
            _render_frame_body(frame, indent + 2, lines, ambiguous)
    else:  # "for" / "while"
        _render_frame_body(scope.frames[0], indent + 1, lines, ambiguous)


def _render_items(
    items: list[NetlistItem], indent: int, lines: list[str], ambiguous: set[str],
) -> None:
    for item in items:
        match item:
            case NetlistInstance():
                _render_instance(item, indent, lines, ambiguous)
            case NetlistScope():
                _render_scope(item, indent, lines, ambiguous)


def render_netlist(module: NetlistModule) -> str:
    """Render a ``NetlistModule`` to the locked netlist text syntax.

    See ``.tmp/netlist-spec.md`` -- syntax is LOCKED, ASCII only.
    """
    lines: list[str] = []
    in_names = ", ".join(name for name, _ in module.inputs)
    out_names = ", ".join(name for name, _ in module.outputs)
    lines.append(f"{module.vi_name} ({in_names}) -> ({out_names})")

    ambiguous = ambiguous_bares(module)
    _render_items(module.body, 0, lines, ambiguous)

    return "\n".join(lines)
