"""Netlist IR data model -- the dataclasses/enums that make up ``NetlistModule``.

Split out of ``netlist.py`` (the god-module holding both the IR and the
graph -> IR build walk) so the IR can be read/imported on its own. This is a
pure data-model module: no build logic, no rendering. ``netlist.py`` re-
exports every name here so existing ``from ...graph.netlist import
NetlistModule`` (etc.) call sites are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models import (
    DisableStructureKind,
    LVType,
    ScalarValue,
)
from .interface_order import WiringRequirement
from .models import VIHealth, VIProperties
from .op_walk import ComponentPort
from .queries import ClassContext

# ============================================================
# The IR
# ============================================================


@dataclass(frozen=True)
class NetRef:
    """A reference to one net (wire) in the netlist.

    ``node``/``terminal`` identify the PRODUCING terminal (EDA ``refdes.pin``):
    ``node`` is the producing operation's display name, or ``None`` when the
    net is a VI-boundary control. ``terminal`` is the terminal NAME when known,
    else its index. ``occurrence`` is the ``#n`` disambiguator for repeated
    node display names (``None`` when the name is unique in the VI).
    ``bare`` is the net's short display name -- see the naming rule in
    ``.tmp/netlist-spec.md``.
    """

    node: str | None
    terminal: str
    occurrence: int | None
    bare: str
    # Phase A, ``render_lvnet``-ONLY: set (to the producing ``ConstantNode``'s
    # TRAILING uid -- the SAME id ``NetlistConstant.uid`` uses) when this
    # net's driver is a constant -- labeled or not. ``bare``/``node``/
    # ``terminal`` stay the literal VALUE text either way
    # (unchanged, so ``NetRef.render``/``netlist_to_dict`` are completely
    # unaffected).
    # Only ``render_lvnet`` reads this, to decide whether a LABELED
    # ("shared/named", see ``NetlistConstant``) constant should render as
    # ``= <name>#<n>`` instead of the inlined literal -- see
    # ``_render_lvnet_source``.
    constant_uid: str | None = None
    # Phase A, ``render_lvnet``-ONLY (like ``constant_uid`` above): the
    # ALREADY lvnet-escaped literal text (``_lvnet_const_value_str``, md
    # §4/§10) for a constant-driven net -- set alongside ``constant_uid`` in
    # ``_resolve_source_gn``'s constant branch. ``bare`` stays the OLD
    # ``op_walk._const_value_str`` text (unescaped strings, ``netlist_to_dict``
    # parity); ``render_lvnet`` prefers this field so a
    # control-char string constant escapes to one physical line instead of
    # breaking ``parse_lvnet``'s line-oriented grammar (closes the
    # ``Graphical Test Runner - Main UI - .vi`` round-trip xfail). ``None``
    # for every non-constant ``NetRef`` (a net name is never escaped).
    lvnet_value: str | None = None
    # Phase 3, ``render_lvnet``-ONLY (mirrors ``constant_uid`` above): the
    # producing NODE's own uid (the SAME id ``NetlistInstance.uid`` uses) for
    # a genuine node-terminal reference -- lets ``render_lvnet`` resolve the
    # reference straight to its producer's ``<base>_<uid>`` handle via
    # ``_LvnetHandles.by_uid`` (the identical mechanism a labeled constant
    # already uses via ``constant_uid``), instead of re-deriving it from
    # ``node``/``occurrence``. ``None`` for a boundary control, a structure-
    # scoped net, a constant, or a literal. ``node``/``occurrence``
    # stay populated exactly as before (``netlist_to_dict`` parity is
    # unaffected -- it never reads this field).
    producer_uid: str | None = None

    def render(self, *, qualified: bool) -> str:
        """Render this net reference.

        ``qualified`` -> ``"CallTestMethod.execTime"`` / ``"Not#2.0"``;
        unqualified (or boundary, ``node is None``) -> ``bare`` (e.g.
        ``"execTime"``).
        """
        if not qualified or self.node is None:
            return self.bare
        tag = f"#{self.occurrence}" if self.occurrence else ""
        return f"{self.node}{tag}.{self.terminal}"


@dataclass(frozen=True)
class NetlistTerminalBinding:
    """One input PORT on an instance: its declared name, its own faithful
    type, and how it's bound -- wired to a driver (``net``) or, since Phase A
    (lvnet ``default <value>`` -- see ``docs/_internal/design/netlist-
    language.md`` §4), genuinely unwired (``net is None``, ``default``
    carries the type's faithful substitute value).

    Verilog ``.port(net)`` / VHDL ``port => signal`` / Python-kwargs named-
    terminal association -- rendered as ``terminal=net`` (see
    ``instance_line``, which only ever sees a wired binding -- ``net is not
    None`` -- since that helper/``netlist_to_dict`` filter to those;
    ``render_lvnet`` renders every binding, wired or not, per §3/§4).
    ``terminal`` is the input terminal's own name (the same naming rule as
    everywhere else in this module: ``display_name or name or str(index)`` --
    for an nMux/decompose LIST terminal ``display_name`` IS the real field
    name, stamped once at load time; see ``_component_terminal_name``), NOT the
    source net's name. ``type`` is this terminal's OWN faithful
    ``LVType.type_descriptor()`` (never a Python type), present on every
    binding wired or not (lvnet §3: "never dropped").

    ``default`` is populated ONLY when ``net is None`` -- the type's
    faithful "use default if unwired" substitute (``_type_default``), NEVER
    fabricated. ``inverted`` mirrors the INPUT terminal's own
    ``Terminal.inverted`` flag (the "Not" bubble LabVIEW draws directly on a
    Compound Arithmetic input -- that input is negated before the node's own
    operation runs, e.g. ``x AND NOT y``). Annotation ONLY, exactly like
    ``NetlistInstance.operation``: it changes how ``instance_line``/
    ``netlist_to_dict`` DISPLAY this binding, never the net's identity --
    ``net`` (and its ``bare``/``occurrence``) stays exactly what it would be
    uninverted, so net-name disambiguation and diffing are untouched.

    ``pane_rank`` is a ``render_lvnet``-ONLY presentation hint -- this
    terminal's 0-based position in the LabVIEW connector-pane's canonical
    reading order (``graph.interface_order.ordered_interface``), which for a
    SubVI CALL differs from ``_call_terminals_gn``'s raw physical-pane-index
    order (the call node itself never carries its own
    ``connector_pattern_id`` -- verified empirically -- so the CALLEE's own
    top-level definition's pattern must be used; see
    ``_ordered_real_terminals_gn``). Stored SEPARATELY from list order (never
    reorders ``inputs`` itself) -- only ``render_lvnet`` sorts by it;
    ``netlist_to_dict`` never reads this field.
    """

    terminal: str
    type: str
    net: NetRef | None
    default: DefaultValue | None = None
    inverted: bool = False
    pane_rank: int = 0
    # The STRUCTURED type this terminal's ``type`` string was flattened
    # from -- ``render_lvnet``-ONLY (lvnet §10/§11): lets it tell a NAMED
    # enum/cluster/typedef from an anonymous one (``LVType.type_descriptor(
    # expand_named=False)``) without string-guessing the already-flattened
    # ``type`` label, which the no-string-matching law forbids.
    # ``render_lvnet`` only ever consumes ``build_netlist_from_graph``'s
    # output, which always sets it; ``netlist_to_dict`` never reads this
    # field.
    lv_type: LVType | None = None


@dataclass(frozen=True)
class NetlistPropertyAccess:
    """One property access on a Property Node -- ``PropertyDef.name``
    correlated to its VALUE terminal (``op_walk.correlate_property_terminals``,
    the SAME correlation ``render/nodes.py::_property_node_glyph`` and the
    load-time ``op_walk.stamp_property_value_names`` stamp use), annotated
    with the real read/write DIRECTION of that terminal -- ``"read"`` for an
    OUTPUT terminal (the property's current value flows out), ``"write"``
    for an INPUT terminal (a value flows in, setting the property).

    ``net`` is the SAME ``NetRef`` shape as any other instance terminal: for a
    read, the net this property PRODUCES (identical to the matching entry in
    ``NetlistInstance.outputs``); for a write, the net FEEDING it (identical
    to the matching ``NetlistTerminalBinding.net`` in ``NetlistInstance.inputs``)
    -- ``None`` only when a write property's value terminal is genuinely
    unwired (never fabricated).

    This is a structured ANNOTATION alongside ``inputs``/``outputs`` (which
    already carry the same ports, now labelled by property name via the
    load-time display-name stamp) -- not a replacement for them.
    """

    name: str
    direction: str  # "read" | "write"
    net: NetRef | None


@dataclass(frozen=True)
class NetlistOutput:
    """One output PORT on an instance: the net it produces, plus its own
    faithful type (lvnet §3/§10 -- a type is shown on EVERY terminal, wired
    or not, output ports included). Split out of a bare ``NetRef`` (Phase A)
    so ``render_lvnet`` can show ``out <terminal> : <Type>`` the same way an
    input terminal line does; ``netlist_to_dict`` keeps reading only ``.net``
    (see ``_collect_refs``/``instance_line``/``netlist_to_dict``), so its
    output is untouched.

    ``pane_rank`` mirrors ``NetlistTerminalBinding.pane_rank`` -- see there.
    """

    net: NetRef
    type: str
    pane_rank: int = 0
    # Mirrors ``NetlistTerminalBinding.lv_type`` -- see there.
    lv_type: LVType | None = None


class NetlistInstanceKind(str, Enum):
    """Which §7 node-kind keyword an INSTANCE renders as in ``render_lvnet``
    -- an explicit discriminator off the ``GraphNode``
    SUBCLASS (``VINode``/``PrimitiveNode``/property-bearing/invoke-bearing/
    ``LocalVariableNode``/``InPlaceNode``/``FormulaNode``), never off
    ``qualified_name`` (which is set even for a plain primitive -- see
    ``_build_instance_gn``). CLOSED (§2-§10, fully rendered): ``SUBVI``,
    ``FUNCTION``. Every other member is an OPEN construct (§17 item 6/7) --
    ``render_lvnet`` emits ONLY that member's header keyword plus a
    ``# TODO(lvnet): ...`` placeholder, never invented inner syntax.
    """

    SUBVI = "subvi"
    FUNCTION = "function"
    PROPERTY_NODE = "property-node"
    INVOKE_NODE = "invoke-node"
    LOCAL_VARIABLE = "local-variable"
    IN_PLACE_ELEMENT = "in-place-element"
    FORMULA_NODE = "formula-node"


@dataclass
class NetlistInstance:
    """One node instance -- a primitive, SubVI call, or other leaf op."""

    uid: str  # trailing node UID (matches ElementChange.uid / SVG data-node)
    name: str  # node / subVI / primitive display name
    occurrence: int | None
    # one binding per input terminal (Phase A: all, wired)
    inputs: list[NetlistTerminalBinding]
    outputs: list[NetlistOutput]  # one entry per output terminal, in terminal order
    # Which §7 keyword this instance renders as in ``render_lvnet`` -- see
    # ``NetlistInstanceKind``. Defaults to ``FUNCTION``.
    kind: NetlistInstanceKind = NetlistInstanceKind.FUNCTION
    # cpdArith's mode (add/multiply/and/or/xor) -- ``None`` for every other
    # instance. Annotation ONLY: rendered as a display suffix by
    # ``instance_line`` and carried as its own JSON key by ``_item_to_dict``,
    # but never folds into ``name``/``NetRef`` -- net names and occurrence
    # tags must stay exactly what they are today (see module docstring on
    # ``_component_identity_gn``: an And and an Or cpdArith are different
    # COMPONENTS but must not become different NET-naming identities here).
    operation: str | None = None
    # A Property Node's target object CLASS (e.g. "Bool", "Numeric", "VI" --
    # LabVIEW's own label under the node's icon) -- ``None`` for every other
    # instance kind. An Invoke Node's target object CLASS (e.g. "Library",
    # "VI Server") reuses this SAME field -- a Property Node is never an
    # Invoke Node, so the two never co-occur. Annotation ONLY, same
    # rendering/JSON treatment as ``operation`` above.
    object_name: str | None = None
    # An Invoke Node's method name -- the entire meaning of the node --
    # ``None`` for every other instance kind.
    # A distinct concept from cpdArith's ``operation``, so it gets its own
    # field rather than overloading it. Parameter terminal NAMES are never
    # available (they live in the method's VI-server signature, not the VI
    # file) -- ``inputs``/``outputs`` stay numeric; this is the one thing we
    # CAN say faithfully about an invoke call. Annotation ONLY, same
    # rendering/JSON treatment as ``operation``/``object_name`` above.
    method_name: str | None = None
    # The callee's class/lib-qualified identity (``op.qualified_name``, e.g.
    # "TestResult.lvclass:addError.vi"; a dynamic-dispatch call = its declaring
    # parent class). Pass-THROUGH for consumers (render/diff/JSON) that want the
    # qualified label -- ``name`` stays BARE because it is this instance's
    # by-name lookup convenience and ``uid`` is the real identity key.
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
    # raw index for a sequence frame. What ``render_lvnet`` prints.
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
    # build time via ``_has_output_tunnel_gn`` since the renderer only sees
    # the IR, not the original graph operation.
    passthrough: bool = False


@dataclass(frozen=True)
class NetlistTunnelInfo:
    """One loop tunnel's structural facts -- JSON-only (``netlist_to_dict``
    / the MCP ``get_context`` tool), never rendered as text (tunnels are
    otherwise dissolved into wire resolution, not surfaced as their own
    items -- see ``_build_loop_scope_gn``). Mirrors
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
    the SAME string a downstream consumer's ``NetlistTerminalBinding``/
    ``BoundaryOutput`` resolves to via ``_resolve_source_gn`` -- see
    ``_gamma_net_name_gn``, the one place that name is assembled.
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
    SAME string a downstream reference resolves to via ``_resolve_source_gn``
    -- a node INSIDE the loop reading the LEFT (``lSR``) terminal, or --
    rarer -- a consumer OUTSIDE the loop reading the RIGHT (``rSR``)
    terminal directly (see ``_is_mu_shift_register_read_gn``).

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
    """A structure: case / for / while / sequence / disabled / event / inplace.

    ``inplace`` is an In Place Element Structure -- like a loop, a single
    implicit body (``frames[0]``, no per-frame family, no selector) and no
    output MERGE of its own (a downstream reader of one of its output ports
    resolves to the ``inplace_<uid>.out<k>`` structure net directly). See
    ``netlist_build._build_inplace_scope_gn`` / ``render_lvnet.
    _render_lvnet_inplace_scope``."""

    uid: str
    kind: str  # "case"|"for"|"while"|"sequence"|"disabled"|"event"|"inplace"
    selector: NetRef | None
    frames: list[NetlistFrame]
    # Sequence-only (kind == "sequence") sub-kind: the EXPLICIT flat-vs-
    # stacked discriminator surfaced from the parser's XML class
    # (``SequenceNode.is_flat``) -- never inferred from the ambiguous
    # ``displayed_frame`` proxy (None for both a
    # flat sequence and an out-of-range legacy stacked one). Meaningless for
    # every other scope kind; the default (True/Flat) is also the lvnet
    # renderer's pre-existing hard-coded fallback, so an unpopulated
    # construction path renders exactly as before.
    sequence_is_flat: bool = True
    # Disabled-only (kind == "disabled") sub-kind: WHICH disable-family
    # structure this is (Diagram / Conditional / Type Specialization),
    # straight from ``DisableStructureNode.kind``. Meaningless for every
    # other scope kind; the default (DIAGRAM) is also the lvnet renderer's
    # pre-existing hard-coded fallback.
    disable_kind: DisableStructureKind = DisableStructureKind.DIAGRAM
    # Loop-only (kind in ("for", "while")) -- False/None/empty for every
    # other scope kind. JSON-only surface (see ``_item_to_dict``);
    # ``scope_header`` never reads these.
    parallel: bool = False
    parallel_static_workers: int | None = None
    tunnels: list[NetlistTunnelInfo] = field(default_factory=list)
    # One merge per structural value-merge point on this scope: a case
    # (kind == "case") carries one ``GammaMerge`` per output tunnel (see
    # ``_build_case_outputs_gn``); a loop (kind in ("for", "while")) carries
    # one ``MuMerge`` per shift register and one ``EtaMerge`` per output
    # tunnel (see ``_build_loop_shift_registers_gn``/``_build_loop_outputs_gn``,
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
    reading the output resolves to ``fb{k}`` via ``_resolve_source_gn`` -- see
    ``_is_feedback_output_read_gn``. ``uid`` is the master node's trailing UID.
    """

    uid: str
    net: str
    init: NetRef | DefaultValue
    recur: NetRef | None
    delay: int | None


@dataclass(frozen=True)
class NetlistConstant:
    """A NAMED constant body item (lvnet §7: ``constant <name>#<n> : <Type>
    = <value>``) -- Phase A promotes a ``ConstantNode`` to its OWN body item
    ONLY when it carries a real LabVIEW-authored ``label`` (a "shared/named"
    constant per §7's rule); an unlabeled ("one-off") constant stays inlined
    as a literal on the consuming terminal, exactly as ``_resolve_source_gn``
    already renders it -- see ``_labeled_constant_items_gn``.

    ``occurrence`` follows the SAME "``#n`` only when the display name
    repeats" convention as every other node (§9) -- here scoped to labeled
    constants sharing the same ``name``, via ``_GraphBuildCtx.
    constant_occurrence_by_uid`` (built once over ``ctx.constants``, the
    same list ``const_by_id`` is keyed from). ``value`` is the faithful
    display text (``op_walk._const_value_str``), NEVER a Python literal --
    kept BYTE-IDENTICAL to before this pass for ``netlist_to_dict`` parity.
    ``lvnet_value`` is ``render_lvnet``-ONLY
    (``_lvnet_const_value_str``, md §4/§10): the SAME faithful value, but
    with a string escaped for lvnet's own grammar (a control char can't
    survive unescaped in a line-oriented text surface) -- see
    ``NetRef.lvnet_value``'s docstring for the sibling field on the
    inline-literal side.
    """

    uid: str
    name: str
    occurrence: int | None
    type: str
    value: str
    lvnet_value: str


NetlistItem = NetlistInstance | NetlistScope | NetlistFeedback | NetlistConstant


@dataclass
class NetlistComponent:
    """One distinct component DECLARATION -- the Verilog-module /
    VHDL-entity half of the netlist: a typed port interface, declared once
    regardless of how many ``NetlistInstance``s call it.

    ``name`` is the VI name for a subVI, or the primitive/nMux/cpdArith
    display name for a leaf op -- with a `` (n)`` disambiguator suffix in
    the rare case where two instances sharing the same underlying
    primitive genuinely have different typed interfaces (a real
    polymorphic collision, not a merge bug -- see ``_dedupe_primitive_group_gn``).
    """

    name: str
    inputs: list[ComponentPort]
    outputs: list[ComponentPort]


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
    # Mirrors ``NetlistTerminalBinding.lv_type`` -- see there.
    lv_type: LVType | None = None


@dataclass(frozen=True)
class NetlistBoundaryInput:
    """One VI boundary control (§2's ``in`` line) -- the input-side mirror
    of ``BoundaryOutput``. Promoted from a bare ``(name, type_descriptor)``
    tuple (Phase A) to a proper dataclass so the structured ``LVType`` rides
    alongside the already-flattened string on the SAME record, never a
    second parallel lookup keyed by name.
    """

    name: str
    type_descriptor: str  # FAITHFUL LabVIEW type label, not a Python annotation
    # Mirrors ``NetlistTerminalBinding.lv_type`` -- see there.
    lv_type: LVType | None = None


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
    # lvnet §5's tri-state (+ unknown) connector requirement -- the LOSSLESS
    # replacement for the old ``is_required: bool`` (kept below as a derived
    # property so existing bool consumers keep working unchanged).
    wiring_requirement: WiringRequirement
    default: ScalarValue  # terminal default value, None when it has none
    # Mirrors ``NetlistTerminalBinding.lv_type`` -- see there.
    lv_type: LVType | None = None

    @property
    def is_required(self) -> bool:
        """Backward-compat bool view of ``wiring_requirement`` -- caller MUST
        wire it. Inputs only: ``requirement_state`` already folds a
        Required/Dynamic-Dispatch OUTPUT down to RECOMMENDED, so this is
        never ``True`` for an output.
        """
        return self.wiring_requirement == WiringRequirement.REQUIRED


@dataclass
class ConnectorPane:
    """A VI's connector pane: its wiring pattern plus its ordered terminals."""

    pattern_id: int | None  # the connector pattern (conId), None if unknown
    terminals: list[ConnectorPaneTerminal] = field(default_factory=list)


class DependencyKind(str, Enum):
    """The lvnet §7 `uses :` dependency-manifest kind -- DERIVED from a
    dependency's qualified identity file extension (``.vi`` -> subVI,
    ``.ctl`` -> typedef, ``.lvclass`` -> class), never a separate guessed
    input. See ``_dependency_kind_for``/``_build_dependency_manifest``.
    """

    SUBVI = "subVI"
    TYPEDEF = "typedef"
    CLASS = "class"


@dataclass(frozen=True)
class NetlistDependency:
    """One external FILE this VI directly depends on -- a subVI call, a
    referenced class (``.lvclass``), or a referenced typedef (``.ctl``).
    This is the lvnet §7 ``uses :`` dependency manifest (docs/_internal/
    design/netlist-language.md) -- the first "element" of the terse/verbose
    design, present in BOTH modes (a plain reference list; the dependency's
    own INTERFACE/type structure is a later, verbose-only element).

    ``path`` is a project-relative ``./...`` display path (§6's ``; ./path``
    annotation), or ``None`` when the dependency's recorded/searched
    reference doesn't resolve to an on-disk file -- NEVER fabricated (see
    ``_build_dependency_manifest``).

    ``interface`` is the §7a verbose-only element this ``NetlistDependency``
    docstring above forward-referenced: a ``subVI`` dependency's OWN
    connector-pane terminals (in order, inputs then outputs), reusing the
    SAME already-leaf-loaded graph a ``LoadMode.MINIMAL`` load populates
    (``load_mode.py``/``graph/loading.py``'s own leaf-load comment) and the
    SAME per-terminal extraction the VI's own boundary uses
    (``InMemoryVIGraph.get_inputs``/``get_outputs`` -> ``interface_order.
    ordered_interface`` -> ``_pane_terminal``) -- never a second, re-parsed
    VI (see ``_dependency_interface``). Rendered ONLY in verbose mode,
    indented under this entry's own ``uses :`` line (``_render_lvnet_uses``/
    ``_render_lvnet_dependency_interface``). Empty for a ``class``/``typedef``
    dependency (a connector pane is a VI-only concept) or an unresolved
    ``subVI`` dependency (not reachable in the loaded graph) -- NEVER
    fabricated, same rule as ``path`` above.
    """

    kind: DependencyKind
    qualified: str
    path: str | None
    interface: list[ConnectorPaneTerminal] = field(default_factory=list)


@dataclass
class NetlistModule:
    """The whole VI as a netlist."""

    vi_name: str
    # One entry per boundary control, error clusters included -- see
    # ``NetlistBoundaryInput``.
    inputs: list[NetlistBoundaryInput]
    # Each boundary indicator plus the net driving it (see BoundaryOutput).
    outputs: list[BoundaryOutput]
    body: list[NetlistItem] = field(default_factory=list)
    # Every distinct component (subVI or primitive/nMux/cpdArith) actually
    # used in the VI, declared once -- see ``_build_components_gn``. Sorted by
    # name for a deterministic ``## Components`` rendering.
    components: list[NetlistComponent] = field(default_factory=list)
    # User-settable VI Properties (Protection/Execution/…) -- carried through
    # for ``netlist_to_dict`` (the MCP ``get_context`` tool's JSON shape).
    # NOT rendered as netlist text -- ``describe.py`` has
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
    # the netlist text stays pure connectivity, ``describe.py`` renders
    # the pane's ``## Inputs``/``## Outputs``.
    connector_pane: ConnectorPane = field(
        default_factory=lambda: ConnectorPane(pattern_id=None)
    )
    # The lvnet §7 ``uses :`` dependency manifest -- see ``NetlistDependency``.
    # Populated by ``build_netlist_from_graph`` (see
    # ``_build_dependency_manifest``); ``netlist_to_dict`` never reads it --
    # ``render_lvnet`` is its only consumer.
    dependencies: list[NetlistDependency] = field(default_factory=list)
