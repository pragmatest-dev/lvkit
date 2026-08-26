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
``describe.py`` (``index_terminal_owners``, ``_has_output_tunnel``,
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
from collections.abc import Callable
from dataclasses import asdict as _dataclass_asdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..models import (
    CaseFrame,
    CaseOperation,
    DisableStructureOperation,
    EventFrame,
    EventOperation,
    FeedbackOperation,
    InPlaceOperation,
    InvokeOperation,
    LoopOperation,
    LVType,
    LVTypeKind,
    Operation,
    PrimitiveOperation,
    PropertyOperation,
    ScalarValue,
    SequenceFrame,
    SequenceOperation,
    Terminal,
    Tunnel,
    TunnelMode,
    TunnelTerminal,
    _is_error_cluster,
)
from ..parser.node_types import get_display_name
from .core import _OPERATION_KINDS, _graph_node_to_op_kind, _uid_of
from .interface_order import is_required
from .models import (
    AnyGraphNode,
    CaseStructureNode,
    Constant,
    DisableStructureNode,
    EventStructureNode,
    InPlaceNode,
    LoopNode,
    SequenceNode,
    VIContext,
    VIHealth,
    VINode,
    VIProperties,
    WireEnd,
    vi_health_to_dict,
    vi_properties_to_dict,
)
from .models import (
    PrimitiveNode as GraphPrimitiveNode,
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
    str(index)`` -- for an nMux/decompose LIST terminal ``display_name`` IS
    the real field name, stamped once at load time; see
    ``_component_port_name``), NOT the source net's name.

    ``inverted`` mirrors the INPUT terminal's own ``Terminal.inverted`` flag
    (the "Not" bubble LabVIEW draws directly on a Compound Arithmetic input --
    that input is negated before the node's own operation runs, e.g.
    ``x AND NOT y``). Annotation ONLY, exactly like ``NetlistInstance.
    operation``: it changes how ``instance_line``/``netlist_to_dict`` DISPLAY
    this binding, never the net's identity -- ``net`` (and its ``bare``/
    ``occurrence``) stays exactly what it would be uninverted, so net-name
    disambiguation and diffing are untouched.
    """

    port: str
    net: NetRef
    inverted: bool = False


@dataclass(frozen=True)
class NetlistPropertyAccess:
    """One property access on a Property Node -- ``PropertyDef.name``
    correlated to its VALUE terminal (``op_walk.correlate_property_terminals``,
    the SAME correlation ``render/nodes.py::_property_node_glyph`` and the
    load-time ``op_walk.stamp_property_value_names`` stamp use), annotated
    with the real read/write DIRECTION of that terminal -- ``"read"`` for an
    OUTPUT terminal (the property's current value flows out), ``"write"``
    for an INPUT terminal (a value flows in, setting the property).

    ``net`` is the SAME ``NetRef`` shape as any other instance port: for a
    read, the net this property PRODUCES (identical to the matching entry in
    ``NetlistInstance.outputs``); for a write, the net FEEDING it (identical
    to the matching ``NetlistPortBinding.net`` in ``NetlistInstance.inputs``)
    -- ``None`` only when a write property's value terminal is genuinely
    unwired (never fabricated).

    This is a structured ANNOTATION alongside ``inputs``/``outputs`` (which
    already carry the same ports, now labelled by property name via the
    load-time display-name stamp) -- not a replacement for them.
    """

    name: str
    direction: str  # "read" | "write"
    net: NetRef | None


@dataclass
class NetlistInstance:
    """One node instance -- a primitive, SubVI call, or other leaf op."""

    uid: str  # trailing node UID (matches ElementChange.uid / SVG data-node)
    name: str  # node / subVI / primitive display name
    occurrence: int | None
    inputs: list[NetlistPortBinding]  # port=net binding per wired input port
    outputs: list[NetRef]  # net produced at each output port, in terminal order
    # cpdArith's mode (add/multiply/and/or/xor) -- ``None`` for every other
    # instance. Annotation ONLY: rendered as a display suffix by
    # ``instance_line`` and carried as its own JSON key by ``_item_to_dict``,
    # but never folds into ``name``/``NetRef`` -- net names and occurrence
    # tags must stay exactly what they are today (see module docstring on
    # ``_component_identity``: an And and an Or cpdArith are different
    # COMPONENTS but must not become different NET-naming identities here).
    operation: str | None = None
    # A Property Node's target object CLASS (``PropertyOperation.
    # object_name``, e.g. "Bool", "Numeric", "VI" -- LabVIEW's own label
    # under the node's icon) -- ``None`` for every other instance kind.
    # An Invoke Node's target object CLASS (``InvokeOperation.object_name``,
    # e.g. "Library", "VI Server") reuses this SAME field -- a
    # ``PropertyOperation`` is never an ``InvokeOperation``, so the two never
    # co-occur. Annotation ONLY, same rendering/JSON treatment as
    # ``operation`` above.
    object_name: str | None = None
    # An Invoke Node's method name (``InvokeOperation.method_name``) -- the
    # entire meaning of the node -- ``None`` for every other instance kind.
    # A distinct concept from cpdArith's ``operation``, so it gets its own
    # field rather than overloading it. Parameter port NAMES are never
    # available (they live in the method's VI-server signature, not the VI
    # file) -- ``inputs``/``outputs`` stay numeric; this is the one thing we
    # CAN say faithfully about an invoke call. Annotation ONLY, same
    # rendering/JSON treatment as ``operation``/``object_name`` above.
    method_name: str | None = None
    # The callee's class/lib-qualified identity (``op.qualified_name``, e.g.
    # "TestResult.lvclass:addError.vi"; a dynamic-dispatch call = its declaring
    # parent class). Pass-THROUGH for consumers (render/diff/JSON) that want the
    # qualified label -- ``name`` stays BARE because it is this instance's lookup
    # convenience (``_find_instance``) and ``uid`` is the real identity key.
    # Annotation ONLY, same treatment as ``operation``/``object_name`` above;
    # never folds into ``name`` or net identities.
    qualified_name: str | None = None
    # Accessed properties (Property Node only) -- empty for every other
    # instance. See ``NetlistPropertyAccess``.
    properties: list[NetlistPropertyAccess] = field(default_factory=list)


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


@dataclass(frozen=True)
class NetlistTunnelInfo:
    """One loop tunnel's structural facts -- JSON-only (``netlist_to_dict``
    / the MCP ``get_context`` tool), never rendered by ``render_netlist``'s
    ASCII text (tunnels are otherwise dissolved into wire resolution, not
    surfaced as their own items -- see ``_build_loop_scope``). Mirrors
    ``models.Tunnel``'s new fields directly; ``mode`` is the enum's string
    VALUE (JSON-serializable), not the raw ``TunnelMode``.
    """

    tunnel_type: str
    mode: str | None
    sr_initialized: bool | None
    sr_stack_depth: int | None


@dataclass(frozen=True)
class DefaultValue:
    """The type default LabVIEW substitutes for an unwired case-output
    tunnel ("use default if unwired" -- LabVIEW's real runtime behavior,
    never a Python literal; see ``_default_literal``'s docstring for why
    ``type_defaults.py``'s codegen-only forms are the wrong source here).

    ``literal`` is the faithful default VALUE token (``"0"``, ``"0.0"``,
    ``"False"``, ...); ``type_descriptor`` is the type's own faithful label
    (``LVType.type_descriptor()``, e.g. ``"I32"``). Renders ``"0 (I32 default)"``.
    """

    literal: str
    type_descriptor: str

    def render(self) -> str:
        return f"{self.literal} ({self.type_descriptor} default)"


@dataclass(frozen=True)
class GammaCase:
    """One frame's contribution to a gamma merge (see ``GammaMerge``):
    ``frame_key`` is the frame's own faithful selector label (``"default"``
    for the catch-all default frame -- a distinguished literal, never the
    frame's own ``"Default"`` display text, so a gamma line can't be
    confused with a case-frame header); ``source`` is that frame's real
    producer net, or the tunnel's type ``DefaultValue`` when the frame left
    it unwired (LabVIEW routes the type default through, per LV's own
    "use default if unwired" tunnel semantics -- never omitted, never
    fabricated as a wire that doesn't exist).
    """

    frame_key: str
    source: NetRef | DefaultValue


@dataclass(frozen=True)
class GammaMerge:
    """A case structure's OUTPUT tunnel, modeled as Gated-SSA's classic
    gamma node: ONE named net (``case{id}.out{k}``) governed by the case's
    selector, carrying exactly one source PER FRAME -- never a single
    hop-through producer (netlist.py's finding #1: which frame actually
    supplies the value is selector-dependent at runtime, so a case output
    tunnel is a genuine multi-producer merge, not a single wire). ``net`` is
    the SAME string a downstream consumer's ``NetlistPortBinding``/
    ``BoundaryOutput`` resolves to via ``_resolve_source`` -- see
    ``_gamma_net_name``, the one place that name is assembled.
    """

    net: str
    selector: NetRef | None
    cases: list[GammaCase]


@dataclass(frozen=True)
class MuMerge:
    """A loop shift register modeled as Gated-SSA's classic mu node: ONE
    named net (``loop{id}.shift{k}``) carrying the recurrence across
    iterations -- ``init`` (the value before the first iteration) and
    ``recur`` (the value fed back for the NEXT iteration). ``net`` is the
    SAME string a downstream reference resolves to via ``_resolve_source``
    -- a node INSIDE the loop reading the LEFT (``lSR``) terminal, or --
    rarer -- a consumer OUTSIDE the loop reading the RIGHT (``rSR``)
    terminal directly (see ``op_walk._is_mu_shift_register_read``).

    ``init`` is the outer source when the SR is initialized
    (``Tunnel.sr_initialized``), else the type ``DefaultValue`` -- LabVIEW
    seeds an uninitialized shift register to its type's default on the VI's
    first call, never fabricated, mirroring ``GammaCase.source``'s "use
    default if unwired" treatment. ``recur`` is the source wired into the
    RIGHT (``rSR``) terminal inside the loop, or ``None`` when the shift
    register is genuinely never written to -- a real, faithful state (the
    value never changes across iterations), NOT an unresolved placeholder,
    so it is never defaulted the way ``init`` is.
    """

    net: str
    init: NetRef | DefaultValue
    recur: NetRef | None


@dataclass(frozen=True)
class EtaMerge:
    """A loop's OUTPUT tunnel modeled as Gated-SSA's classic eta node: ONE
    named net (``loop{id}.out{k}``) carrying the value LEAVING the loop --
    never a single hop-through to one iteration's producer (netlist.py's
    loop finding: which values actually reach a downstream consumer depends
    on the tunnel's aggregation mode, so a loop output tunnel is a genuine
    merge across iterations, not a plain wire). ``index_mode`` is the BASE
    ``TunnelMode`` (see ``_eta_index_mode``) -- ``"array"`` for auto-indexing,
    ``"last"`` for last-value, ``"concat"``/``"passthrough"`` for the rarer
    modes -- and ``conditional`` is the orthogonal Conditional modifier
    (aggregate/keep a value only on iterations where a per-iteration boolean
    holds). ``value`` is the inner PER-ITERATION producer feeding this tunnel
    -- the type ``DefaultValue`` on the rare unwired/broken-wire case, never
    omitted.
    """

    net: str
    index_mode: str  # base TunnelMode, lowercased ("array"/"last"/...)
    conditional: bool  # LabVIEW's Conditional modifier, orthogonal to index_mode
    value: NetRef | DefaultValue


@dataclass
class NetlistScope:
    """A structure: case / for / while / sequence / disabled / event."""

    uid: str
    kind: str  # "case" | "for" | "while" | "sequence" | "disabled" | "event"
    selector: NetRef | None
    frames: list[NetlistFrame]
    # Loop-only (kind in ("for", "while")) -- False/None/empty for every
    # other scope kind. JSON-only surface (see ``_item_to_dict``); the ASCII
    # renderer (``scope_header``/``render_netlist``) never reads these.
    parallel: bool = False
    parallel_static_workers: int | None = None
    tunnels: list[NetlistTunnelInfo] = field(default_factory=list)
    # One merge per structural value-merge point on this scope: a case
    # (kind == "case") carries one ``GammaMerge`` per output tunnel (see
    # ``_build_case_outputs``); a loop (kind in ("for", "while")) carries
    # one ``MuMerge`` per shift register and one ``EtaMerge`` per output
    # tunnel (see ``_build_loop_shift_registers``/``_build_loop_outputs``,
    # shift registers first). Empty for every other scope kind (sequence/
    # disabled/event have no such merge).
    outputs: list[GammaMerge | MuMerge | EtaMerge] = field(default_factory=list)


@dataclass(frozen=True)
class NetlistFeedback:
    """A LabVIEW Feedback Node (z^-N) projected as Gated-SSA's classic mu --
    the SAME merge a loop shift register gets (see ``MuMerge``), but a
    STANDALONE body item rather than a loop-scope output, because a Feedback
    Node is its own node (it may sit anywhere, even outside a loop, carrying
    state across VI calls). ONE named net (``fb{k}``) carries ``init`` (the
    value before the first iteration) then ``recur`` (the value fed back for
    the NEXT iteration).

    ``init`` is the source wired into the master's INITIALIZER terminal, or
    the type ``DefaultValue`` when unwired -- LabVIEW seeds an uninitialized
    Feedback Node to its type default, never fabricated, mirroring
    ``MuMerge.init``. ``recur`` is the source wired into the linked write side
    (``slaveFBInputNode``'s ``rightFeedback`` input), or ``None`` when the
    Feedback Node is genuinely never written to (a real, faithful state, not
    an unresolved placeholder). ``delay`` is the z^-N depth
    (``feedbackNodeDelay``); ``None`` when absent. A downstream consumer
    reading the output resolves to ``fb{k}`` via ``_resolve_source`` -- see
    ``_is_feedback_output_read``. ``uid`` is the master node's trailing UID.
    """

    uid: str
    net: str
    init: NetRef | DefaultValue
    recur: NetRef | None
    delay: int | None


NetlistItem = NetlistInstance | NetlistScope | NetlistFeedback


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

    ``case_id_by_uid``: deterministic ``case0``, ``case1``, … id per
    ``CaseOperation``, keyed by trailing node UID (see
    ``_assign_sequential_ids``) -- used ONLY to name a case's gamma-merge
    output nets (``case{id}.out{k}``), never as a scope-header ``#n`` tag.

    ``loop_id_by_uid``: deterministic ``loop0``, ``loop1``, … id per
    ``LoopOperation``, keyed by trailing node UID (see
    ``_assign_sequential_ids``) -- the loop analogue of ``case_id_by_uid``,
    used ONLY to name a loop's mu/eta-merge nets (``loop{id}.shift{k}`` /
    ``loop{id}.out{k}``).
    """

    occurrence_by_uid: dict[str, int]
    const_by_id: dict[str, Constant]
    case_id_by_uid: dict[str, int]
    loop_id_by_uid: dict[str, int]
    # ``fb0``, ``fb1``, … id per Feedback Node MASTER, keyed by trailing node
    # UID (see ``_assign_sequential_ids``) -- names the ``fb{k}`` mu net a
    # consumer reading the Feedback Node's output resolves to.
    feedback_id_by_uid: dict[str, int]
    # Every operation keyed by its full ``op.id`` -- lets a Feedback Node
    # master reach its linked write side (``FeedbackOperation.partner_uid``)
    # to resolve the mu ``recur`` source. Built once via ``_walk_flat``.
    op_by_uid: dict[str, Operation]
    # Every terminal id -> its owning ``(op, terminal)``, built ONCE (see
    # ``op_walk.index_terminal_owners``). ``_resolve_source`` does an O(1)
    # lookup here instead of re-scanning the whole op tree per wire -- that
    # linear rescan was an O(n^2) hot spot (6.4M ``_find_op_owning_terminal``
    # calls on a 1k-node VI, ~half of build_netlist's time).
    owner_by_terminal: dict[str, tuple[Operation, Terminal]]


@dataclass
class BoundaryOutput:
    """A VI front-panel indicator (boundary output) and the net that drives it.

    The producer→indicator wire is real dataflow the graph carries; without
    ``source`` the netlist would declare the output's name/type but drop which
    net produces it (the mirror of an input, which IS a source). ``source`` is
    ``None`` only when the indicator is genuinely unwired.
    """

    name: str
    type_descriptor: str  # FAITHFUL LabVIEW type label, not a Python annotation
    source: NetRef | None


@dataclass
class ConnectorPaneTerminal:
    """One terminal of a VI's connector pane — the authored CONTRACT for a pane
    slot, distinct from the net that drives/reads it (a ``BoundaryOutput`` carries
    the wiring; this carries the pane terminal). "Connector pane", not
    "signature": a signature is the Python/codegen lens; the VI's own word for its
    interface is the connector pane. Ordered canonically by
    ``graph.interface_order`` (errors last, Required first, then pane geometry).
    """

    name: str
    type: str  # FAITHFUL LabVIEW type label, not a Python annotation
    direction: str  # "input" | "output"
    index: int | None  # connector-pane slot index (None if unassigned)
    is_required: bool  # caller MUST wire it — inputs only (never an output)
    default: ScalarValue  # terminal default value, None when it has none


@dataclass
class ConnectorPane:
    """A VI's connector pane: its wiring pattern plus its ordered terminals."""

    pattern_id: int | None  # the connector pattern (conId), None if unknown
    terminals: list[ConnectorPaneTerminal] = field(default_factory=list)


@dataclass
class NetlistModule:
    """The whole VI as a netlist."""

    vi_name: str
    # (name, type_descriptor) for all boundary controls, error clusters included —
    # the FAITHFUL LabVIEW type label, not a Python annotation.
    inputs: list[tuple[str, str]]
    # Each boundary indicator plus the net driving it (see BoundaryOutput).
    outputs: list[BoundaryOutput]
    body: list[NetlistItem] = field(default_factory=list)
    # Every distinct component (subVI or primitive/nMux/cpdArith) actually
    # used in the VI, declared once -- see ``_build_components``. Sorted by
    # name for a deterministic ``## Components`` rendering.
    components: list[NetlistComponent] = field(default_factory=list)
    # User-settable VI Properties (Protection/Execution/…) -- carried through
    # for ``netlist_to_dict`` (the MCP ``get_context`` tool's JSON shape).
    # NOT rendered by ``render_netlist``'s ASCII text -- ``describe.py`` has
    # its own faithful ``## Properties`` section.
    properties: VIProperties = field(default_factory=VIProperties)
    # Compile-health -- a SIBLING facet to ``properties``, never nested
    # inside it (emergent state, not a user setting). Same
    # netlist_to_dict-only carry-through as ``properties`` above.
    health: VIHealth = field(default_factory=VIHealth)
    # The owning class's context when this VI is a .lvclass method -- the
    # describe.py ``## Class`` section's JSON counterpart. None for a non-
    # method VI. Same netlist_to_dict-only carry-through as the two above.
    # ``ClassContext`` (queries.py) is shared verbatim -- no netlist-local
    # wrapper type.
    class_context: ClassContext | None = None
    # The VI's authored connector pane: its pattern + every terminal
    # (canonically ordered). A SIBLING facet to the connectivity ``inputs``/
    # ``outputs`` above -- carried through for ``netlist_to_dict`` (get_context);
    # ``render_netlist``'s ASCII stays pure connectivity, ``describe.py`` renders
    # the pane's ``## Inputs``/``## Outputs``.
    connector_pane: ConnectorPane = field(
        default_factory=lambda: ConnectorPane(pattern_id=None)
    )


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
    ``fb{k}`` net instead of the raw producing node.port."""
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
    number-port shows (``Node[#n].idx``), fully qualified up front since a
    bare index alone would be meaningless.
    """
    label = _terminal_display_name(term)
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
                return NetRef(node=None, port=name, occurrence=None, bare=name)
            if _is_eta_output_tunnel(op, term):
                # A loop output tunnel's outer terminal carries the value
                # LEAVING the loop -- an aggregation across every iteration
                # (auto-indexed array, last-value, ...), not one iteration's
                # producer. Stop here and name the merge net instead of
                # hopping via ``_paired_tunnel_id`` into the inner scalar
                # (the loop analogue of the case finding above) --
                # ``_build_loop_outputs`` defines the merge itself.
                name = _eta_net_name(op, term, build_ctx)
                return NetRef(node=None, port=name, occurrence=None, bare=name)
            if _is_mu_shift_register_read(op, term):
                # A shift register's LEFT terminal (read inside the loop) or
                # RIGHT terminal (rarely read directly from outside) IS the
                # Gated-SSA recurrence itself -- hopping via
                # ``_paired_tunnel_id`` would jump straight to the INIT wire
                # and silently drop the recurrence. Stop here and name the
                # merge net -- ``_build_loop_shift_registers`` defines it.
                name = _mu_net_name(op, term, build_ctx)
                return NetRef(node=None, port=name, occurrence=None, bare=name)
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
                return NetRef(node=None, port=name, occurrence=None, bare=name)
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
                return NetRef(node=None, port=bare, occurrence=None, bare=bare)

        # A wire fed directly by a constant renders the constant's real
        # VALUE (a literal), not the structural uid.port fallback below --
        # a literal has no producing node, so ``node=None`` (same
        # convention as a boundary control) and it always renders bare.
        const = build_ctx.const_by_id.get(src.node_id)
        if const is not None:
            value_str = _const_value_str(const)
            return NetRef(
                node=None,
                port=value_str,
                occurrence=None,
                bare=value_str,
            )

        # Structural fallback: identify by the wire source's own carried
        # info (never invent a placeholder like "x").
        node_name = src.name or _uid_of(src.node_id)
        port = str(src.index) if src.index is not None else src.terminal_id
        return NetRef(
            node=node_name,
            port=port,
            occurrence=None,
            bare=f"{node_name}.{port}",
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
    renders via the generic ``inputs``/``outputs`` numeric-port fallback,
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
        NetlistPortBinding(port=_component_port_name(t), net=ref, inverted=t.inverted)
        for t in op.terminals
        if t.direction == "input"
        if (ref := _input_ref(graph, ctx, build_ctx, t)) is not None
    ]
    outputs = [
        _term_ref(name, occurrence, t) for t in op.terminals if t.direction == "output"
    ]
    operation = op.operation if isinstance(op, PrimitiveOperation) else None
    object_name: str | None = None
    method_name: str | None = None
    properties: list[NetlistPropertyAccess] = []
    if isinstance(op, PropertyOperation):
        object_name = (op.object_name or "").strip() or None
        properties = _build_property_accesses(
            graph,
            ctx,
            build_ctx,
            op,
            name,
            occurrence,
        )
    elif isinstance(op, InvokeOperation):
        object_name = (op.object_name or "").strip() or None
        method_name = (op.method_name or "").strip() or None
    return NetlistInstance(
        uid=uid,
        name=name,
        occurrence=occurrence,
        inputs=inputs,
        outputs=outputs,
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


def _component_port_name(term: Terminal) -> str:
    """Port name for one of ``op``'s own terminals, in a synthesized
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
            name=_component_port_name(t),
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
        (t.name or "input", t.lv_type.type_descriptor() if t.lv_type else "Any")
        for t in ctx.inputs
    ]
    outputs = [
        BoundaryOutput(
            name=t.name or "output",
            type_descriptor=t.lv_type.type_descriptor() if t.lv_type else "Any",
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


def _pane_terminal(t: Terminal, direction: str) -> ConnectorPaneTerminal:
    """One interface terminal -> its ``ConnectorPaneTerminal`` contract record."""
    return ConnectorPaneTerminal(
        name=t.name or direction,
        # FAITHFUL label (family-word fallback when unresolved) -- same as
        # describe's ## Inputs/## Outputs, never the codegen "Any".
        type=t.type_label(),
        direction=direction,
        index=t.index,
        is_required=is_required(t, direction),
        default=t.default_value,
    )


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
    """The graph-native analogue of ``_BuildCtx`` -- same four id spaces
    (occurrence/case/loop/feedback), keyed identically by trailing node UID,
    but with no ``op_by_uid``/``owner_by_terminal`` (the graph builder reaches
    a wire's producing node directly via ``graph.get_graph_node``/
    ``graph.get_terminal`` -- see ``_owning_node_gn`` -- instead of a
    precomputed Operation-tree index)."""

    occurrence_by_uid: dict[str, int]
    const_by_id: dict[str, Constant]
    case_id_by_uid: dict[str, int]
    loop_id_by_uid: dict[str, int]
    feedback_id_by_uid: dict[str, int]


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
    id_map: dict[str, int],
    outers_fn: Callable[[AnyGraphNode], list[Terminal]],
    prefix: str,
) -> str:
    """The graph-node analogue of ``_tunnel_net_name``."""
    id_ = id_map[_uid_of(node.id)]
    outers = outers_fn(node)
    k = next(i for i, t in enumerate(outers) if t.id == term.id)
    return f"{prefix}{id_}.out{k}"


def _gamma_net_name_gn(
    node: AnyGraphNode, term: Terminal, build_ctx: _GraphBuildCtx
) -> str:
    return _tunnel_net_name_gn(
        node, term, build_ctx.case_id_by_uid, _case_output_tunnel_outers_gn, "case"
    )


def _eta_net_name_gn(
    node: AnyGraphNode, term: Terminal, build_ctx: _GraphBuildCtx
) -> str:
    return _tunnel_net_name_gn(
        node, term, build_ctx.loop_id_by_uid, _loop_output_tunnel_outers_gn, "loop"
    )


def _mu_net_name_gn(
    node: AnyGraphNode, term: Terminal, build_ctx: _GraphBuildCtx
) -> str:
    loop_id = build_ctx.loop_id_by_uid[_uid_of(node.id)]
    pairs = _loop_shift_register_pairs_gn(node)
    k = next(
        i
        for i, (lsr, rsr) in enumerate(pairs)
        if lsr.inner_terminal_uid == term.id
        or (rsr is not None and rsr.outer_terminal_uid == term.id)
    )
    return f"loop{loop_id}.shift{k}"


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


def _owning_node_gn(
    graph: InMemoryVIGraph, node_id: str, vi_name: str
) -> AnyGraphNode | None:
    """The graph-node analogue of ``_BuildCtx.owner_by_terminal`` -- given a
    wire endpoint's OWN carried ``node_id`` (``WireEnd.node_id``, set at
    construction time), resolve the owning ``GraphNode`` directly, gated to
    the same set of "real operation" node kinds ``index_terminal_owners``'s
    Operation-tree walk would have covered (``_OPERATION_KINDS`` --excludes
    constants/labels/local variables), explicitly excluding the VI's OWN
    definition node (``node_id == vi_name``: that node's own terminals are
    the VI's boundary controls, which ``get_operations``/``index_
    terminal_owners`` never include in the op tree either -- ``ctx.inputs``
    is the separate boundary-control check the caller falls through to), and
    excluding an IPES decompose/recompose border node (see
    ``_is_ipes_border_node_gn`` -- ``index_terminal_owners`` never reaches
    those either).
    """
    if node_id == vi_name:
        return None
    node = graph.get_graph_node(node_id)
    if node is None:
        return None
    if _graph_node_to_op_kind(node) not in _OPERATION_KINDS:
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
                name = _gamma_net_name_gn(owner, term, build_ctx)
                return NetRef(node=None, port=name, occurrence=None, bare=name)
            if _is_eta_output_tunnel_gn(owner, term):
                name = _eta_net_name_gn(owner, term, build_ctx)
                return NetRef(node=None, port=name, occurrence=None, bare=name)
            if _is_mu_shift_register_read_gn(owner, term):
                name = _mu_net_name_gn(owner, term, build_ctx)
                return NetRef(node=None, port=name, occurrence=None, bare=name)
            if _is_feedback_output_read_gn(graph, owner, term):
                name = f"fb{build_ctx.feedback_id_by_uid[_uid_of(owner.id)]}"
                return NetRef(node=None, port=name, occurrence=None, bare=name)
            paired = _paired_tunnel_id_gn(term)
            if paired is not None and paired not in seen:
                tid = paired
                continue
            node_name = _display_name_gn(owner)
            occurrence = build_ctx.occurrence_by_uid.get(_uid_of(owner.id))
            return _term_ref(node_name, occurrence, term)

        for t in ctx.inputs:
            if t.id == src.terminal_id:
                bare = t.name or str(t.index)
                return NetRef(node=None, port=bare, occurrence=None, bare=bare)

        const = build_ctx.const_by_id.get(src.node_id)
        if const is not None:
            value_str = _const_value_str(const)
            return NetRef(
                node=None,
                port=value_str,
                occurrence=None,
                bare=value_str,
            )

        node_name = src.name or _uid_of(src.node_id)
        port = str(src.index) if src.index is not None else src.terminal_id
        return NetRef(
            node=node_name,
            port=port,
            occurrence=None,
            bare=f"{node_name}.{port}",
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
    Without this, a SubVI call's INPUT/OUTPUT port names would show the
    caller-side placeholder name instead of the callee's real parameter name
    (e.g. ``start path`` misread as ``error out``)."""
    if isinstance(node, VINode) and node.id != node.vi:
        return graph._enrich_subvi_terminals_typed(  # noqa: SLF001
            list(node.terminals), node.name, vi_name
        )
    return node.terminals


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
    here."""
    uid = _uid_of(node.id)
    name = _display_name_gn(node)
    occurrence = build_ctx.occurrence_by_uid.get(uid)
    terminals = _call_terminals_gn(graph, vi_name, node)
    inputs = [
        NetlistPortBinding(port=_component_port_name(t), net=ref, inverted=t.inverted)
        for t in terminals
        if t.direction == "input"
        if (ref := _input_ref_gn(graph, ctx, vi_name, build_ctx, t)) is not None
    ]
    outputs = [
        _term_ref(name, occurrence, t) for t in terminals if t.direction == "output"
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
    return NetlistInstance(
        uid=uid,
        name=name,
        occurrence=occurrence,
        inputs=inputs,
        outputs=outputs,
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
    """Resolve ``uids`` to typed graph nodes, keeping only "real operation"
    kinds (``_OPERATION_KINDS`` -- excludes constants/labels/local
    variables), in the given order. The graph-node analogue of the
    ``op_kind in _OPERATION_KINDS`` filter ``_build_inner_nodes``/
    ``get_operations``/``get_operation_order`` apply before constructing an
    ``Operation`` for a uid."""
    nodes: list[AnyGraphNode] = []
    for uid in uids:
        node = graph.get_graph_node(uid)
        if node is not None and _graph_node_to_op_kind(node) in _OPERATION_KINDS:
            nodes.append(node)
    return nodes


def _top_level_nodes_gn(graph: InMemoryVIGraph, vi_name: str) -> list[AnyGraphNode]:
    """The graph-node analogue of ``ctx.operations``'s top-level ordering --
    ``graph.get_operation_order`` IS the same dataflow-topological,
    ``_node_order_key``-tie-broken sort ``get_operations`` (which builds
    ``ctx.operations``) itself sorts by; this reuses it directly rather than
    re-deriving the order (a pure graph-level function already, no
    ``Operation`` involved)."""
    return _resolve_op_nodes_gn(graph, graph.get_operation_order(vi_name))


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
    key = _frame_key_gn(frame, position)
    child_uids = []
    for uid in node.children:
        child = graph.get_graph_node(uid)
        if child is not None and child.frame == key:
            child_uids.append(uid)
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


def _build_case_outputs_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: CaseStructureNode,
    build_ctx: _GraphBuildCtx,
    case_id: int,
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
            GammaMerge(net=f"case{case_id}.out{k}", selector=selector, cases=cases)
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
            ),
            passthrough=passthrough,
        )
        for i, frame in enumerate(node.frames)
    ]
    case_id = build_ctx.case_id_by_uid[_uid_of(node.id)]
    outputs: list[GammaMerge | MuMerge | EtaMerge] = [
        *_build_case_outputs_gn(
            graph, ctx, vi_name, node, build_ctx, case_id, selector, frames
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
            ),
            passthrough=passthrough,
        )
        for i, frame in enumerate(node.frames)
    ]
    return NetlistScope(
        uid=_uid_of(node.id), kind="disabled", selector=None, frames=frames
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
            ),
            passthrough=False,
        )
        for i, frame in enumerate(node.frames)
    ]
    return NetlistScope(
        uid=_uid_of(node.id), kind="sequence", selector=None, frames=frames
    )


def _build_loop_shift_registers_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: LoopNode,
    build_ctx: _GraphBuildCtx,
    loop_id: int,
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
        merges.append(MuMerge(net=f"loop{loop_id}.shift{k}", init=init, recur=recur))
    return merges


def _build_loop_outputs_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    node: LoopNode,
    build_ctx: _GraphBuildCtx,
    loop_id: int,
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
                net=f"loop{loop_id}.out{k}",
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
        graph, ctx, vi_name, _body_nodes_gn(graph, vi_name, node), build_ctx
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
    loop_id = build_ctx.loop_id_by_uid[_uid_of(node.id)]
    term_by_id = {t.id: t for t in node.terminals}
    outputs: list[GammaMerge | MuMerge | EtaMerge] = [
        *_build_loop_shift_registers_gn(
            graph, ctx, vi_name, node, build_ctx, loop_id, term_by_id
        ),
        *_build_loop_outputs_gn(
            graph, ctx, vi_name, node, build_ctx, loop_id, term_by_id
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


def _build_items_gn(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    nodes: list[AnyGraphNode],
    build_ctx: _GraphBuildCtx,
) -> list[NetlistItem]:
    """The graph-node analogue of ``_build_items``."""
    items: list[NetlistItem] = []
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
            if isinstance(node, InPlaceNode):
                items.extend(
                    _build_items_gn(
                        graph,
                        ctx,
                        vi_name,
                        _ipes_regular_nodes_gn(graph, vi_name, node),
                        build_ctx,
                    )
                )
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
            name=_component_port_name(t),
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
        case_id_by_uid=_assign_sequential_ids_gn(
            flat, lambda n: isinstance(n, CaseStructureNode)
        ),
        loop_id_by_uid=_assign_sequential_ids_gn(
            flat, lambda n: isinstance(n, LoopNode)
        ),
        feedback_id_by_uid=_assign_sequential_ids_gn(
            flat,
            lambda n: (
                isinstance(n, GraphPrimitiveNode)
                and (fb := graph.get_feedback_info(n.id)) is not None
                and fb[0]
            ),
        ),
    )

    inputs = [
        (t.name or "input", t.lv_type.type_descriptor() if t.lv_type else "Any")
        for t in ctx.inputs
    ]
    outputs = [
        BoundaryOutput(
            name=t.name or "output",
            type_descriptor=t.lv_type.type_descriptor() if t.lv_type else "Any",
            source=_resolve_source_gn(graph, ctx, vi_name, t.id, build_ctx),
        )
        for t in ctx.outputs
    ]

    connector_pane = ConnectorPane(
        pattern_id=ctx.connector_pattern_id,
        terminals=[_pane_terminal(t, "input") for t in ctx.inputs]
        + [_pane_terminal(t, "output") for t in ctx.outputs],
    )

    body = _build_items_gn(
        graph, ctx, vi_name, _top_level_nodes_gn(graph, vi_name), build_ctx
    )
    components = _build_components_gn(graph, vi_name, flat)

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


# ============================================================
# render_netlist
# ============================================================


def _collect_refs(items: list[NetlistItem]) -> list[NetRef]:
    """Every NetRef appearing anywhere in a body (inputs, outputs, and
    scope selectors), for global bare-name disambiguation.

    Deliberately does NOT recurse into any merge's source refs -- neither a
    scope's ``GammaMerge.cases[].source`` / ``MuMerge.init``/``recur`` /
    ``EtaMerge.value``, nor (symmetrically) a standalone ``NetlistFeedback``'s
    ``init``/``recur``. Every one of those sources aliases a producer already
    reachable elsewhere in the walk -- a boundary control (never ambiguous by
    itself) or some instance's own output (collected when THAT instance is
    walked) -- so re-adding the same identity here is redundant, not a
    genuinely new disambiguation source.
    """
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
            case NetlistFeedback():
                pass
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
                case NetlistFeedback():
                    # A Feedback Node is neither an instance nor a scope --
                    # not indexed here (diff tracks its uid via
                    # _walk_netlist_order for ordering only).
                    pass

    walk(module.body)
    return instances, scopes


def _instance_name_display(instance: NetlistInstance) -> str:
    """The instance's own header text -- name, ``#n`` occurrence tag, and any
    bracket suffix -- with NO ports/connections (that's ``instance_line``'s
    job). Split out of ``instance_line`` since the header assembly (occurrence
    + operation suffix + object/method suffix) is a self-contained concern.

    A Property Node's ``object_name`` (its target CLASS, e.g. "Bool") gets
    the same bracket-suffix treatment as ``operation`` -- the two never
    co-occur (a ``PropertyOperation`` is never a ``PrimitiveOperation``), so
    one visual slot serves both without collision: ``Property Node#1 [Bool]``.

    An Invoke Node's ``method_name`` gets the SAME bracket suffix slot,
    rendered ``object:method`` (``Invoke Node#1 [Library:Open Project]``) --
    ``:`` reads as "the method OF this object", the same idiom the
    qualified-name display already uses elsewhere (``owning_libraries``
    joined with ``:``). When ``object_name`` is absent the bracket holds
    just the method.
    """
    tag = f"#{instance.occurrence}" if instance.occurrence else ""
    op_suffix = f" [{instance.operation}]" if instance.operation else ""
    if instance.method_name:
        obj = (
            f"{instance.object_name}:{instance.method_name}"
            if instance.object_name
            else instance.method_name
        )
        obj_suffix = f" [{obj}]"
    elif instance.object_name:
        obj_suffix = f" [{instance.object_name}]"
    else:
        obj_suffix = ""
    return f"{instance.name}{tag}{op_suffix}{obj_suffix}"


def instance_line(instance: NetlistInstance, ambiguous: set[str]) -> str:
    """NODE-FIRST netlist line: ``"name(ins) -> outs"`` (``"name(ins)"`` when no
    outputs) -- NO indent or gutter; callers own that.

    Node-first is the real netlist convention: SPICE (``R1 n1 n2 1k``) and
    Verilog (``and2 u1 (.a(w1), .b(w2))``) both lead with the COMPONENT, then
    its connections. The node is the subject; wires are its attributes. It also
    makes a VI and each node inside it read the SAME shape (``NAME(ins) -> outs``,
    matching the ``render_netlist`` signature line) and keeps a diff node-centric
    -- the changed node's name sits right after the ``+/-/~`` gutter. The
    header itself (``name``/``#n``/bracket suffixes) is ``_instance_name_display``.

    Inputs use NAMED-PORT association (Verilog ``.port(net)`` / VHDL
    ``port => signal`` / Python kwargs), rendered ``port=net`` -- each wire
    is tied to the declared input port it feeds, not left positional. An
    inverted input (``NetlistPortBinding.inverted`` -- the "Not" bubble
    LabVIEW draws directly on that input, negating it before the node's own
    operation runs) renders ``port=not(net)``: a function-form wrapper around
    the net, ASCII and arrow-safe (``->`` only, never ``<-``), the same idiom
    ``_render_merge_source``/the module docstring already reserve arrows for.
    A non-inverted input is unchanged from before this flag existed.

    Each accessed property's port is already labelled by its real NAME (not
    a numeric index) via the load-time ``op_walk.stamp_property_value_names``
    stamp on its VALUE terminal's ``display_name`` -- a WRITTEN property
    shows as an input binding (``Value=<net>``), a READ property as a named
    output net -- so no further special-casing is needed here; direction is
    unambiguous from which side of ``->`` a property's port appears on (see
    ``NetlistInstance.properties`` for the JSON-only structured mirror of
    the same facts).

    Parameter port NAMES are never available in the VI file for an Invoke
    Node (they live in the method's VI-server signature) -- ``ins``/``outs``
    below stay numeric for an invoke node; only the node's OWN identity
    (``_instance_name_display``) gains the method it calls.
    """
    name_disp = _instance_name_display(instance)

    def _bind(b: NetlistPortBinding) -> str:
        net = b.net.render(qualified=b.net.bare in ambiguous)
        # An inverted input wraps the net in `not(...)` -- a function form that
        # reads clearly and can't be mistaken for the primitive "Not Equal?"
        # the way a bare `NOT `/`!` prefix glued to the name would.
        return f"{b.port}={f'not({net})' if b.inverted else net}"

    ins = ", ".join(_bind(b) for b in instance.inputs)
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
    instance: NetlistInstance,
    indent: int,
    lines: list[str],
    ambiguous: set[str],
) -> None:
    prefix = "  " * indent
    lines.append(f"{prefix}{instance_line(instance, ambiguous)}")


def _render_frame_body(
    frame: NetlistFrame,
    indent: int,
    lines: list[str],
    ambiguous: set[str],
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
    scope: NetlistScope,
    indent: int,
    lines: list[str],
    ambiguous: set[str],
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
    elif scope.kind == "event":
        # frame.label is already LabVIEW's own faithful bracketed rendering
        # (or an honest "[N]" placeholder) -- no extra quoting/prefix needed,
        # unlike a case's plain selector-value label.
        for frame in scope.frames:
            lines.append(f"{prefix}  {frame.label}:")
            _render_frame_body(frame, indent + 2, lines, ambiguous)
    else:  # "for" / "while"
        _render_frame_body(scope.frames[0], indent + 1, lines, ambiguous)

    # One merge definition line per structural value-merge point on this
    # scope (see NetlistScope.outputs docstring) -- a case's GammaMerge, or
    # a loop's MuMerge/EtaMerge -- at the same indent as this scope's own
    # frame labels/body.
    for merge in scope.outputs:
        lines.append(f"{prefix}  {_merge_definition_line(merge, ambiguous)}")


def _render_merge_source(source: NetRef | DefaultValue, ambiguous: set[str]) -> str:
    if isinstance(source, DefaultValue):
        return source.render()
    return source.render(qualified=source.bare in ambiguous)


def _short_net(net: str) -> str:
    """The SHORT local name (``out0``/``shift0``/...) of a fully qualified
    merge net (``case3.out0``/``loop1.shift0``/...) -- a definition line
    sits inside its own scope, so it never repeats that scope's own id
    prefix (the same convention a frame's own header doesn't repeat the
    case's selector name either). Feedback Nodes intentionally do NOT use
    this -- a standalone ``fb{k}`` net has no scope prefix to strip, so
    ``_feedback_definition_line`` renders ``fb.net`` in full.
    """
    return net.rsplit(".", 1)[-1]


def _gamma_definition_line(gamma: GammaMerge, ambiguous: set[str]) -> str:
    """``"out0 := gamma(selector; True -> subtract3.difference, default -> 0
    (I32 default))"`` -- the SHORT local name (``out{k}``, not the fully
    qualified ``case{id}.out{k}``) since this line sits inside that case's
    own scope, the same convention a frame's own header doesn't repeat the
    case's selector name either. Arrow is ``->`` ONLY (the netlist syntax is
    locked ASCII, no ``<-``, see ``.tmp/netlist-spec.md``).
    """
    sel_str = (
        gamma.selector.render(qualified=gamma.selector.bare in ambiguous)
        if gamma.selector is not None
        else "?"
    )
    cases_str = ", ".join(
        f"{c.frame_key} -> {_render_merge_source(c.source, ambiguous)}"
        for c in gamma.cases
    )
    return f"{_short_net(gamma.net)} := gamma({sel_str}; {cases_str})"


def _render_mu(
    net: str,
    tag: str,
    init: NetRef | DefaultValue,
    recur: NetRef | None,
    ambiguous: set[str],
) -> str:
    """``"{net} := mu{tag}(init -> ..., recur -> ...)"`` -- the shared mu
    render both a loop shift register's ``_mu_definition_line`` (short local
    ``shift{k}`` name, no tag) and a standalone Feedback Node's
    ``_feedback_definition_line`` (full ``fb{k}`` name, ``[z^-N]`` tag) use.
    ``recur`` is omitted entirely (never rendered as an unresolved ``?``)
    when the shift register / Feedback Node is genuinely never written to --
    see ``MuMerge.recur``'s docstring. ``->`` ONLY (locked ASCII, no ``<-``).
    """
    init_str = _render_merge_source(init, ambiguous)
    if recur is None:
        return f"{net} := mu{tag}(init -> {init_str})"
    recur_str = _render_merge_source(recur, ambiguous)
    return f"{net} := mu{tag}(init -> {init_str}, recur -> {recur_str})"


def _mu_definition_line(mu: MuMerge, ambiguous: set[str]) -> str:
    """``"shift0 := mu(init -> seed_net, recur -> Increment.result)"`` -- the
    SHORT local name (``shift{k}``) -- see ``_render_mu``.
    """
    return _render_mu(_short_net(mu.net), "", mu.init, mu.recur, ambiguous)


def _eta_definition_line(eta: EtaMerge, ambiguous: set[str]) -> str:
    """``"out0 := eta(array, Accumulate.result)"`` -- the SHORT local name
    (``out{k}``), ``index_mode`` first (matching ``EtaMerge`` field order). The
    orthogonal Conditional modifier appends ``+cond`` to the mode token
    (``eta(array+cond, ...)`` = conditionally index into the array).
    """
    value_str = _render_merge_source(eta.value, ambiguous)
    mode = f"{eta.index_mode}+cond" if eta.conditional else eta.index_mode
    return f"{_short_net(eta.net)} := eta({mode}, {value_str})"


def _feedback_definition_line(fb: NetlistFeedback, ambiguous: set[str]) -> str:
    """``"fb0 := mu[z^-1](init -> 0.0 (DBL default), recur -> now.0)"`` -- a
    Feedback Node as a standalone mu, the SAME ``init -> …, recur -> …`` form
    a loop shift register's ``_mu_definition_line`` uses (see ``_render_mu``),
    tagged ``[z^-N]`` with the z-transform delay depth (LabVIEW's own
    "z^-1 block" view) when the file carries one. The FULL ``fb{k}`` net name
    is used (not ``_short_net``) -- unlike a scope's own merges, a Feedback
    Node has no enclosing scope prefix to strip."""
    tag = f"[z^-{fb.delay}]" if fb.delay is not None else ""
    return _render_mu(fb.net, tag, fb.init, fb.recur, ambiguous)


def _merge_definition_line(
    merge: GammaMerge | MuMerge | EtaMerge,
    ambiguous: set[str],
) -> str:
    if isinstance(merge, GammaMerge):
        return _gamma_definition_line(merge, ambiguous)
    if isinstance(merge, MuMerge):
        return _mu_definition_line(merge, ambiguous)
    return _eta_definition_line(merge, ambiguous)


def _render_items(
    items: list[NetlistItem],
    indent: int,
    lines: list[str],
    ambiguous: set[str],
) -> None:
    for item in items:
        match item:
            case NetlistInstance():
                _render_instance(item, indent, lines, ambiguous)
            case NetlistScope():
                _render_scope(item, indent, lines, ambiguous)
            case NetlistFeedback():
                lines.append("  " * indent + _feedback_definition_line(item, ambiguous))


def _netref_to_dict(ref: NetRef) -> dict[str, Any]:
    return {
        "node": ref.node,
        "port": ref.port,
        "occurrence": ref.occurrence,
        "bare": ref.bare,
    }


def _frame_to_dict(frame: NetlistFrame) -> dict[str, Any]:
    return {
        "label": frame.label,
        "value": frame.value,
        "is_default": frame.is_default,
        "passthrough": frame.passthrough,
        "body": [_item_to_dict(i) for i in frame.body],
    }


def _merge_source_to_dict(source: NetRef | DefaultValue) -> dict[str, Any]:
    if isinstance(source, DefaultValue):
        return {
            "kind": "default",
            "type": source.type_descriptor,
            "literal": source.literal,
        }
    return _netref_to_dict(source)


def _gamma_case_to_dict(case: GammaCase) -> dict[str, Any]:
    return {"frame": case.frame_key, "source": _merge_source_to_dict(case.source)}


def _gamma_to_dict(gamma: GammaMerge) -> dict[str, Any]:
    return {
        "net": gamma.net,
        "kind": "gamma",
        "selector": _netref_to_dict(gamma.selector) if gamma.selector else None,
        "cases": [_gamma_case_to_dict(c) for c in gamma.cases],
    }


def _mu_to_dict(mu: MuMerge) -> dict[str, Any]:
    return {
        "net": mu.net,
        "kind": "mu",
        "init": _merge_source_to_dict(mu.init),
        "recur": _netref_to_dict(mu.recur) if mu.recur is not None else None,
    }


def _eta_to_dict(eta: EtaMerge) -> dict[str, Any]:
    return {
        "net": eta.net,
        "kind": "eta",
        "index_mode": eta.index_mode,
        "conditional": eta.conditional,
        "value": _merge_source_to_dict(eta.value),
    }


def _merge_to_dict(merge: GammaMerge | MuMerge | EtaMerge) -> dict[str, Any]:
    if isinstance(merge, GammaMerge):
        return _gamma_to_dict(merge)
    if isinstance(merge, MuMerge):
        return _mu_to_dict(merge)
    return _eta_to_dict(merge)


def _component_to_dict(comp: NetlistComponent) -> dict[str, Any]:
    return {
        "name": comp.name,
        "inputs": [{"name": p.name, "type": p.type} for p in comp.inputs],
        "outputs": [{"name": p.name, "type": p.type} for p in comp.outputs],
    }


def _property_access_to_dict(access: NetlistPropertyAccess) -> dict[str, Any]:
    return {
        "name": access.name,
        "direction": access.direction,
        "net": _netref_to_dict(access.net) if access.net is not None else None,
    }


def _feedback_to_dict(fb: NetlistFeedback) -> dict[str, Any]:
    """A standalone Feedback Node body item -- the JSON counterpart of
    ``_feedback_definition_line``'s ``fb{k} := mu[z^-N](...)`` text. ``net``
    leads (matching ``_gamma_to_dict``/``_mu_to_dict``/``_eta_to_dict``'s key
    order -- a Feedback Node IS a standalone mu merge, see ``NetlistFeedback``'s
    docstring), then the ``kind`` discriminator (here always ``"feedback"``,
    distinguishing this from the ``instance``/``scope`` body-item kinds
    ``_item_to_dict`` also tags)."""
    return {
        "net": fb.net,
        "kind": "feedback",
        "uid": fb.uid,
        "delay": fb.delay,
        "init": _merge_source_to_dict(fb.init),
        "recur": _netref_to_dict(fb.recur) if fb.recur is not None else None,
    }


def _item_to_dict(item: NetlistItem) -> dict[str, Any]:
    """One body item, tagged with a ``kind`` discriminator so the
    ``instance``/``scope``/``feedback`` union survives JSON (``asdict`` would
    erase it)."""
    if isinstance(item, NetlistInstance):
        return {
            "kind": "instance",
            "uid": item.uid,
            "name": item.name,
            # The callee's class/lib-qualified identity (bare ``name`` stays the
            # lookup key); a dynamic-dispatch call reports its declaring parent.
            "qualified_name": item.qualified_name,
            "occurrence": item.occurrence,
            "operation": item.operation,
            # Property Node / Invoke Node only (see NetlistInstance
            # docstring) -- ``None``/``[]`` for every other instance kind.
            "object": item.object_name,
            # Invoke Node only -- the method it calls. ``None`` otherwise.
            "method": item.method_name,
            "properties": [_property_access_to_dict(p) for p in item.properties],
            "inputs": [
                {
                    "port": b.port,
                    "net": _netref_to_dict(b.net),
                    "inverted": b.inverted,
                }
                for b in item.inputs
            ],
            "outputs": [_netref_to_dict(o) for o in item.outputs],
        }
    if isinstance(item, NetlistFeedback):
        return _feedback_to_dict(item)
    d: dict[str, Any] = {
        "kind": "scope",
        "uid": item.uid,
        "scope_kind": item.kind,
        "selector": _netref_to_dict(item.selector) if item.selector else None,
        "frames": [_frame_to_dict(f) for f in item.frames],
        # Always present (empty for sequence/disabled/event scopes) -- see
        # NetlistScope.outputs docstring: a case scope's GammaMerge, or a
        # loop scope's MuMerge/EtaMerge, tagged-union by "kind".
        "outputs": [_merge_to_dict(m) for m in item.outputs],
    }
    # Loop-only facts (see NetlistScope docstring) -- omitted for non-loop
    # scope kinds rather than always-present-but-empty, to keep the JSON
    # shape for case/sequence/disabled/event scopes unchanged.
    if item.kind in ("for", "while"):
        d["parallel"] = item.parallel
        d["parallel_static_workers"] = item.parallel_static_workers
        d["tunnels"] = [
            {
                "tunnel_type": t.tunnel_type,
                "mode": t.mode,
                "sr_initialized": t.sr_initialized,
                "sr_stack_depth": t.sr_stack_depth,
            }
            for t in item.tunnels
        ]
    return d


def netlist_to_dict(module: NetlistModule) -> dict[str, Any]:
    """The netlist IR as a faithful JSON-able tree — the STRUCTURED counterpart
    to :func:`render_netlist`'s ASCII projection.

    One canonical structure for every ``format="json"`` surface (describe, diff,
    the MCP tools) so they never drift into per-command ad-hoc shapes. Lossless
    against the IR: boundary ``inputs``/``outputs`` carry the FAITHFUL LabVIEW
    type label (not a Python annotation), the ``instance``/``scope`` union is
    ``kind``-tagged, and scopes nest their frames' bodies recursively.
    """
    return {
        "vi": module.vi_name,
        # Authored connector pane (pattern + canonically-ordered terminals), a
        # sibling to the connectivity inputs/outputs below.
        "connector_pane": {
            "pattern": module.connector_pane.pattern_id,
            "terminals": [
                {
                    "name": p.name,
                    "type": p.type,
                    "direction": p.direction,
                    "index": p.index,
                    "required": p.is_required,
                    "default": p.default,
                }
                for p in module.connector_pane.terminals
            ],
        },
        "inputs": [{"name": n, "type": t} for n, t in module.inputs],
        "outputs": [
            {
                "name": o.name,
                "type": o.type_descriptor,
                "source": _netref_to_dict(o.source) if o.source else None,
            }
            for o in module.outputs
        ],
        "components": [_component_to_dict(c) for c in module.components],
        "body": [_item_to_dict(i) for i in module.body],
        "properties": vi_properties_to_dict(module.properties),
        "health": vi_health_to_dict(module.health),
        "class_context": (
            _dataclass_asdict(module.class_context)
            if module.class_context is not None
            else None
        ),
    }


def render_netlist(module: NetlistModule, *, display_name: str | None = None) -> str:
    """Render a ``NetlistModule`` to the locked netlist text syntax.

    See ``.tmp/netlist-spec.md`` -- syntax is LOCKED, ASCII only.

    ``display_name`` overrides the header line's VI label (``module.vi_name``
    is the resolved ``vi_key`` -- a source-path identity, not fit for
    display -- see ``describe.py``'s ``--format netlist`` naming rules,
    which pass the qualified display name or a repo-relative path here).
    Defaults to ``module.vi_name`` for every existing caller (JSON/diff/the
    embedded viewer never pass this).
    """
    lines: list[str] = []
    in_names = ", ".join(name for name, _ in module.inputs)
    # Show each output's driving net inline as ``name=source`` (arrow-free, the
    # same ``port=net`` idiom instance inputs use); bare name when unwired.
    ambiguous = ambiguous_bares(module)
    out_names = ", ".join(
        f"{o.name}={o.source.render(qualified=o.source.bare in ambiguous)}"
        if o.source is not None
        else o.name
        for o in module.outputs
    )
    header_name = display_name if display_name is not None else module.vi_name
    lines.append(f"{header_name} ({in_names}) -> ({out_names})")

    _render_items(module.body, 0, lines, ambiguous)

    return "\n".join(lines)
