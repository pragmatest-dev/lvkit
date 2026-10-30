"""Format graph data as human-readable text.

Provides graph-level VI descriptions using a loaded ``InMemoryVIGraph``.
Used by the MCP server and CLI ``describe`` command.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import TYPE_CHECKING

from ..models import (
    CaseFrame,
    FPTerminal,
    LVType,
    ScalarValue,
    Terminal,
    TunnelTerminal,
)
from ..parser.node_types import get_display_name
from ..vi_files import is_vi_file, strip_vi_extension
from ..vilib_resolver import get_resolver as _get_vilib_resolver
from .interface_order import is_required
from .models import (
    AnyGraphNode,
    CaseStructureNode,
    Constant,
    DisableStructureNode,
    EventStructureNode,
    ExecutionProps,
    GlobalVariableNode,
    InPlaceNode,
    InstanceProps,
    KindProps,
    Label,
    LocalVariableNode,
    LoopNode,
    PrimitiveNode,
    SequenceNode,
    SharedVariableNode,
    StructureNode,
    ToolbarProps,
    VIContext,
    VINode,
    WindowProps,
    bool_str,
)
from .netlist_build import (
    StructureBorder,
    TracedSource,
    case_selector_type_gn,
    sequence_local_read_gn,
    trace_source_gn,
)
from .node_kinds import EdgeRel
from .op_walk import (
    _const_value_str,
    _render_ports,
    _selector_label,
    _subvi_ports,
    _terminal_display_name,
    selector_values_quoted_one_by_one,
)
from .queries import collect_class_context
from .render_lvnet import lvnet_literal_token
from .tunnel_names import (
    carried_name,
    condition_name,
    condition_source,
    constant_names,
    outer_tunnels,
    tunnel_kind_name,
)

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def describe_vi(
    graph: InMemoryVIGraph,
    vi_name: str,
    *,
    verbose: bool = False,
) -> str:
    """Describe a VI as a documentation page.

    Uses the graph's resolved types, names, constants, and dataflow to
    produce a complete reference for what this VI does. Output (verbose or
    not) always has the same section shape: Inputs/Outputs/Class/Constants,
    then ``## Dependencies`` (subVI signatures) / ``## Control Flow`` /
    ``## Operations``.

    ``verbose`` (``-v``) only adds DEPTH within those same sections -- every
    VI Property (not just non-default ones), the ``## Health`` section even
    when healthy, each pane terminal's connector-pattern slot index, and
    typed terminal detail -- it never swaps in a different section shape.
    The dataflow NETLIST (the declare-then-wire ``## Components`` +
    node-first wiring body) is a SEPARATE output entirely -- see
    ``lvkit.graph.netlist`` and the CLI's ``describe --format lvnet``.
    """
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    lines: list[str] = []

    # Title and signature
    lines.append(f"# {vi_name}")
    lines.append("")
    lines.append(f"  {_format_signature(ctx)}")
    lines.append("")

    # VI Properties: Protection/Execution/Window/…/Kind, faithfully rendered
    # (never a Python annotation -- these are LabVIEW's own VI Properties
    # dialog values, not a codegen target).
    lines.extend(_describe_properties(ctx, show_all=verbose))

    # VI Health: compile-health -- a SIBLING section to ## Properties
    # (VIHealth is a separate facet, never nested under VIProperties: it is
    # emergent state, not a user setting). Terse: shown only when broken;
    # verbose (-v): every field shown, healthy or not.
    lines.extend(_describe_health(ctx, show_all=verbose))

    # Connector pane, in canonical order (errors last, Required first, then pane
    # geometry). Terse: unmarked is the baseline; only the exceptions annotate --
    # ``(required)`` (a caller MUST wire it) and ``= default``. Verbose adds each
    # terminal's ``[idx N]`` pane slot and the VI's connector pattern.
    pattern_note = (
        f" (pattern {ctx.connector_pattern_id})"
        if verbose and ctx.connector_pattern_id is not None
        else ""
    )
    lines.append("## Inputs" + pattern_note)
    if ctx.inputs:
        for inp in ctx.inputs:
            lines.append("  " + _pane_terminal_line(inp, "input", verbose))
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("## Outputs")
    if ctx.outputs:
        for out in ctx.outputs:
            lines.append("  " + _pane_terminal_line(out, "output", verbose))
    else:
        lines.append("  (none)")
    lines.append("")

    lines.extend(_describe_off_pane_front_panel(ctx, verbose))

    # Class context: when this VI is a .lvclass method
    lines.extend(_describe_class_context(graph, ctx))

    # Constants: show actual values. Constants nested inside a structure
    # are shown with their frame (in Control Flow below), not here.
    top_level_constants = [c for c in ctx.constants if c.parent is None]
    if top_level_constants:
        lines.append("## Constants")
        for c in top_level_constants:
            lines.append(f"  {_describe_constant_line(c)}")
        lines.append("")

    # Comments: free labels (block-diagram annotations). Like constants,
    # only top-level ones are shown here -- a label nested inside a
    # structure frame would be shown alongside that frame's contents, but
    # containment for free labels isn't tracked yet (see LabelNode), so
    # every label currently surfaces here regardless of its real position.
    if ctx.labels:
        lines.append("## Comments")
        for lbl in ctx.labels:
            lines.append(f"  {_describe_label_line(lbl)}")
        lines.append("")

    # Dependencies: SubVI calls with their signatures and descriptions.
    # Same shape verbose or not -- verbose only deepens Properties/Health/
    # pane detail above; it never swaps this section out.
    top_nodes = graph.top_level_nodes(vi_name)
    subvi_callees = _collect_subvi_callees(graph, top_nodes, vi_name)
    global_vi_names, library_names = _collect_variable_dependencies(
        graph, top_nodes, vi_name
    )
    dotnet = graph.get_dotnet_assemblies(vi_name)
    if (
        subvi_callees
        or global_vi_names
        or library_names
        or dotnet.assemblies
        or dotnet.unreadable
    ):
        lines.append("## Dependencies")
        for name in sorted(global_vi_names):
            lines.append(f"  {name} (Global VI)")
        for name in sorted(library_names):
            lines.append(f"  {name} (Shared Variable library)")
        for assembly in dotnet.assemblies:
            lines.append(f"  {assembly.qualified} (.NET assembly)")
        for reason in dotnet.unreadable:
            lines.append(f"  (.NET unreadable: {reason})")
        for name, call in sorted(subvi_callees.items()):
            desc = _get_subvi_description(call.qualified_path)
            ports = _subvi_ports(
                graph, graph.loaded_callee_key(call), call.qualified_path
            )
            sig = _render_ports(*ports) if ports is not None else None
            entry = f"  {name}"
            if sig:
                entry += f": {sig}"
            if desc:
                entry += f" -- {desc}"
            lines.append(entry)
        lines.append("")

    # Control flow: structure summary (case/loop/sequence/... with their
    # gating selectors traced one hop back to the source).
    structures = _collect_structures(
        graph,
        ctx,
        vi_name,
        top_nodes,
        {c.id: c for c in ctx.constants},
    )
    if structures:
        lines.append("## Control Flow")
        for s in structures:
            lines.append(f"  {s}")
        lines.append("")

    # Operations
    lines.append("## Operations")
    _describe_op_list(graph, vi_name, top_nodes, ctx.constants, lines, indent=0)

    return "\n".join(lines)


# === Helpers ===


def _format_signature(ctx: VIContext) -> str:
    """Format function signature from VIContext."""
    inputs = []
    for inp in ctx.inputs:
        name = inp.name or "input"
        type_str = _terminal_type_label(inp)
        inputs.append(f"{name}: {type_str}")

    outputs = []
    for out in ctx.outputs:
        outputs.append(f"{out.name}: {_terminal_type_label(out)}")

    func_name = strip_vi_extension(ctx.name).replace(" ", "_").lower()
    params = ", ".join(inputs)
    ret = ", ".join(outputs) if outputs else "None"

    return f"{func_name}({params}) -> {ret}"


def default_suffix(default: ScalarValue) -> str:
    """`` = <value>`` for a terminal's default, or ``''`` when it has none.

    Only a genuinely-set default annotates (``default`` is ``None`` when the
    terminal carries no default). Strings render quoted so ``""`` reads as an
    intentional empty-string default, not a missing one.
    """
    if default is None:
        return ""
    return f' = "{default}"' if isinstance(default, str) else f" = {default}"


def _shown_caption(t: Terminal) -> str | None:
    """A front-panel control's caption when it is set, shown, and says
    something other than its label; else ``None``."""
    if (
        isinstance(t, FPTerminal)
        and t.caption is not None
        and t.caption_visible
        and t.caption != t.name
    ):
        return t.caption
    return None


def _describe_off_pane_front_panel(ctx: VIContext, verbose: bool) -> list[str]:
    """``## Front Panel (not on the connector pane)``: the controls and
    indicators the VI has on its front panel that no connector-pane terminal is
    wired to. Same ``name: type [= default]`` line as Inputs/Outputs. Empty
    when every front-panel object is on the connector pane."""
    if not ctx.off_pane_inputs and not ctx.off_pane_outputs:
        return []
    lines = ["## Front Panel (not on the connector pane)"]
    for heading, terminals, direction in (
        ("Controls", ctx.off_pane_inputs, "input"),
        ("Indicators", ctx.off_pane_outputs, "output"),
    ):
        if not terminals:
            continue
        lines.append(f"  {heading}")
        for t in terminals:
            lines.append("    " + _pane_terminal_line(t, direction, verbose))
    lines.append("")
    return lines


def _pane_terminal_line(t: Terminal, direction: str, verbose: bool) -> str:
    """One connector-pane terminal as ``name: type [(required)] [= default]``.

    Only the exceptions annotate -- ``(required)`` (Required, inputs only) and a
    set default. Verbose appends the ``[idx N]`` connector-pane slot.
    """
    line = f"{t.name}: {_terminal_type_label(t)}"
    caption = _shown_caption(t)
    if caption is not None:
        line += f" (caption {lvnet_literal_token(caption)})"
    if is_required(t, direction):
        line += " (required)"
    line += default_suffix(t.default_value)
    if verbose and t.index is not None and t.index >= 0:
        line += f" [idx {t.index}]"
    return line


def _collect_subvi_callees(
    graph: InMemoryVIGraph,
    nodes: list[AnyGraphNode],
    vi_name: str,
) -> dict[str, VINode]:
    """Collect all SubVI callees recursively: each one's QUALIFIED name — the
    class/lib-qualified identity (e.g. ``TestResult.lvclass:addError.vi``), so a
    dynamic-dispatch call reports its declaring parent class rather than a bare
    ``addError.vi`` — mapped to the call node. A name several calls share keeps
    the first call, in diagram order.

    Walks the graph directly (``child_nodes`` reaches every nested node —
    a structure's frame bodies AND a loop/IPES body alike — in one
    recursion)."""
    callees: dict[str, VINode] = {}
    for node in nodes:
        if isinstance(node, VINode) and node.id != node.vi_path and node.qualified_name:
            callees.setdefault(node.qualified_name, node)
        for name, call in _collect_subvi_callees(
            graph, graph.child_nodes(node.id, vi_name), vi_name
        ).items():
            callees.setdefault(name, call)
    return callees


def _collect_variable_dependencies(
    graph: InMemoryVIGraph,
    nodes: list[AnyGraphNode],
    vi_name: str,
) -> tuple[set[str], set[str]]:
    """The qualified names of the Global VIs and of the project libraries the
    nodes (and everything nested in them) read or write through global variable
    and Shared Variable nodes."""
    global_vis: set[str] = set()
    libraries: set[str] = set()
    for node in nodes:
        if isinstance(node, GlobalVariableNode):
            global_vis.add(node.global_vi.qualified_name)
        elif isinstance(node, SharedVariableNode) and node.library is not None:
            libraries.add(node.library.qualified_name)
        nested_vis, nested_libraries = _collect_variable_dependencies(
            graph, graph.child_nodes(node.id, vi_name), vi_name
        )
        global_vis.update(nested_vis)
        libraries.update(nested_libraries)
    return global_vis, libraries


def _get_subvi_description(recorded_path: str | None) -> str | None:
    """A short description for a SubVI, from the catalog entry of the path its
    call records."""
    resolver = _get_vilib_resolver()
    entry = resolver.resolve_recorded(recorded_path)
    if entry and entry.description:
        desc = entry.description
        if len(desc) > 100:
            desc = desc[:97] + "..."
        return desc
    return None


def type_label(t: LVType | None) -> str:
    """Compact, FAITHFUL LVType label for class fields (never a Python
    annotation — see ``LVType.type_descriptor()``). ``unknown`` (not ``Any``) when the
    field's type didn't resolve."""
    return t.type_descriptor() if t is not None else "unknown"


def _terminal_type_label(t: Terminal) -> str:
    """A terminal's type descriptor for display (never ``python_type()``'s
    codegen-target annotation); the KIND word when the type didn't resolve,
    ``unknown`` when even that is absent."""
    return t.type_label()


# === Graph-native operation-walk helpers ===
#
# describe consumes the graph directly (``top_level_nodes`` / ``child_nodes`` /
# ``enriched_terminals``), reading the ``GraphNode`` fields.


def _node_has_output_tunnel(node: AnyGraphNode) -> bool:
    """True if the structure routes any value out (so an empty frame is a
    pass-through, not truly empty). Reads terminal directions off the
    ``GraphNode``."""
    return any(t.direction == "output" for t in node.terminals)


def _is_feedback_node(graph: InMemoryVIGraph, node: AnyGraphNode) -> bool:
    """True for a Feedback Node's graph node -- a ``PrimitiveNode`` carrying
    the ``feedback_is_master`` graph attribute. Such nodes take the generic
    ``[type-word]`` one-liner rather than a ``[prim N]`` label."""
    return graph.is_feedback_master(node.id)


def _frame_child_nodes(
    graph: InMemoryVIGraph,
    node: AnyGraphNode,
    vi_name: str,
) -> dict[object, list[AnyGraphNode]]:
    """Group a structure's child ``GraphNode``s by their ``frame`` key. Keyed
    by the raw ``child.frame`` value, matched against a case frame's
    ``selector_value`` / a sequence frame's ``str(index)`` / an event frame's
    ``str(position)``."""
    out: dict[object, list[AnyGraphNode]] = {}
    for child in graph.child_nodes(node.id, vi_name):
        out.setdefault(child.frame, []).append(child)
    return out


def _is_ipes_border_node(graph: InMemoryVIGraph, node: AnyGraphNode) -> bool:
    """True for an In Place Element Structure's decompose/recompose BORDER
    node -- a ``poser_uid``-carrying node with list terminals in ONE direction
    only (list-out only = decompose, list-in only = recompose), so describe's
    IPES body omits the border nodes -- they sit on the structure boundary
    like tunnels, not in its body."""
    if graph._graph.nodes.get(node.id, {}).get("poser_uid") is None:
        return False
    has_list_out = any(
        t.nmux_role == "list" and t.direction == "output" for t in node.terminals
    )
    has_list_in = any(
        t.nmux_role == "list" and t.direction == "input" for t in node.terminals
    )
    return (has_list_out and not has_list_in) or (has_list_in and not has_list_out)


def _body_child_nodes(
    graph: InMemoryVIGraph,
    node: AnyGraphNode,
    vi_name: str,
) -> list[AnyGraphNode]:
    """A structure's frame-LESS body nodes (``frame is None``): a loop's body
    (and an IPES's inner nodes) carry no frame, while a frame-bearing
    structure's children all sit in a frame and are walked via
    :func:`_frame_child_nodes` instead. An In Place Element Structure's
    decompose/recompose border nodes are excluded
    (:func:`_is_ipes_border_node`) rather than treated as body."""
    body = [c for c in graph.child_nodes(node.id, vi_name) if c.frame is None]
    if isinstance(node, InPlaceNode):
        body = [c for c in body if not _is_ipes_border_node(graph, c)]
    return body


_FlagGroup = ExecutionProps | WindowProps | ToolbarProps | InstanceProps | KindProps


def _describe_flag_group(
    group: _FlagGroup,
    indent: str = "    ",
    *,
    show_all: bool = False,
) -> list[str]:
    """Render one VI-Properties sub-struct's fields.

    Values render lowercase via ``bool_str`` / the enum ``.value`` string,
    matching the netlist/diff convention -- never Python's capitalized
    ``str(bool)``.

    Default (terse, ``describe`` without ``-v``): bool fields show only when
    True, Enum fields only when != the dataclass default, ``int|None`` /
    ``str|None`` fields only when set -- keeps the common all-default case
    from dumping dozens of ``False``s. ``show_all`` (``describe --verbose``):
    EVERY field shows with its value -- verbose means verbose, nothing hidden
    (the same full listing the properties popover gives).
    """
    lines: list[str] = []
    for f in dataclass_fields(group):
        value = getattr(group, f.name)
        if isinstance(value, bool):
            if show_all or value:
                lines.append(f"{indent}{f.name}: {bool_str(value)}")
        elif isinstance(value, Enum):
            if show_all or value != f.default:
                lines.append(f"{indent}{f.name}: {value.value}")
        elif value is not None:
            lines.append(f"{indent}{f.name}: {value}")
    return lines


def _describe_properties(ctx: VIContext, *, show_all: bool = False) -> list[str]:
    """Render the VI's Properties dialog settings (Protection/Execution/
    Window/…) plus its Kind (what ROLE the VI plays), faithfully and
    grouped to match the dialog's own pages.

    ``lock_state`` always shows; ``lv_version``/``vi_type`` show only when
    present (a stub/synthetic ``VIContext`` has neither). Each nested group
    (execution/window/toolbar/instance/kind) shows only when it has something
    to render. Default (terse) shows only non-default fields; ``show_all``
    (``describe --verbose``) shows EVERY field -- verbose hides nothing.
    Compile-HEALTH is a SEPARATE ``## Health`` section (see
    ``_describe_health``), not part of this one.
    """
    props = ctx.properties
    lines = ["## Properties"]
    lines.append(f"  lock_state: {props.lock_state.value}")
    if props.lv_version is not None:
        lines.append(f"  lv_version: {props.lv_version}")
    if props.vi_type is not None:
        lines.append(f"  vi_type: {props.vi_type}")
    for group_name, group in (
        ("execution", props.execution),
        ("window", props.window),
        ("toolbar", props.toolbar),
        ("instance", props.instance),
        ("kind", props.kind),
    ):
        group_lines = _describe_flag_group(group, show_all=show_all)
        if group_lines:
            lines.append(f"  {group_name}:")
            lines.extend(group_lines)
    lines.append("")
    return lines


def _describe_health(ctx: VIContext, *, show_all: bool = False) -> list[str]:
    """Render the VI's compile-health state (``VIHealth``) -- a SIBLING
    section to ``## Properties``, never folded into it: this is emergent VI
    state (broken-ness), not a user-settable property.

    Default (terse): rendered ONLY when the VI is broken
    (``health.is_broken``), listing the true bad_* causes plus ``is_broken``;
    omitted entirely for a healthy VI (never an empty section). ``show_all``
    (``describe --verbose``): every field shown with its value, even for a
    healthy VI (all ``false``) -- verbose hides nothing.
    """
    health = ctx.health
    if not show_all and not health.is_broken:
        return []
    lines = ["## Health"]
    for f in dataclass_fields(health):
        val = getattr(health, f.name)
        if show_all or val:
            lines.append(f"  {f.name}: {bool_str(val)}")
    lines.append(f"  is_broken: {bool_str(health.is_broken)}")
    lines.append("")
    return lines


def _describe_class_context(
    graph: InMemoryVIGraph,
    ctx: VIContext,
) -> list[str]:
    """Describe the owning class when this VI is a .lvclass method.

    Surfaces the class's parent, fields, and sibling methods -- context an
    agent needs to write a method body but which is absent from the VI's
    own dataflow. Returns an empty list for non-class VIs.

    Renders ``queries.collect_class_context`` -- the SAME collector
    ``netlist._build_class_context`` wraps for the JSON IR -- so the two
    surfaces can't drift on what "the class context" means. ``fields``/
    ``methods`` (the class's private-data fields and sibling method names)
    aren't part of that shared context (netlist has no use for them) and are
    still gathered here directly.
    """
    cc = collect_class_context(graph, ctx)
    if cc is None:
        return []

    fields = graph.get_class_fields(cc.owning_class) or []
    siblings = sorted(
        {
            t.split(":")[-1]
            for _, t, e in graph._dep_graph.edges(cc.owning_class, data=True)
            if e.get("rel") == EdgeRel.OWNS and is_vi_file(t)
        }
    )

    lines = ["## Class", f"  {cc.owning_class}"]
    if cc.parent:
        lines.append(f"  parent: {cc.parent}")
    if cc.version:
        lines.append(f"  version: {cc.version}")
    if cc.ancestors:
        lines.append(f"  ancestors: {' -> '.join(cc.ancestors)}")
    if fields:
        lines.append("  fields:")
        for f in fields:
            lines.append(f"    {f.name}: {type_label(f.type)}")
    if siblings:
        lines.append(f"  methods: {', '.join(siblings)}")
    # This method's own item properties -- terse, only when non-default.
    if cc.scope:
        lines.append(f"  scope: {cc.scope}")
    if cc.is_static:
        lines.append(f"  is_static: {bool_str(cc.is_static)}")
    if cc.must_override:
        lines.append(f"  must_override: {bool_str(cc.must_override)}")
    if cc.must_call_parent:
        lines.append(f"  must_call_parent: {bool_str(cc.must_call_parent)}")
    lines.append("")
    return lines


def _frame_count(n: int) -> str:
    """``1 frame`` / ``3 frames``."""
    return f"{n} frame" if n == 1 else f"{n} frames"


def _structure_word(node: AnyGraphNode) -> str:
    """A structure's type in LabVIEW's words, with its own author text when it
    has one: ``For Loop "For Each Front Panel Control"``, ``Stacked
    Sequence``."""
    if isinstance(node, SequenceNode):
        type_word = f"{node.kind_word} Sequence"
    else:
        type_word = get_display_name(node.node_type) if node.node_type else "node"
    author = node.caption or node.label
    return f'{type_word} "{author}"' if author else type_word


def _node_output_name(node: AnyGraphNode, term: Terminal) -> str:
    """A node's output as a selector/stop wire's source: a local variable by
    its control, any other node by its identity plus the terminal's name (the
    bare node when it has one output and this terminal is unnamed)."""
    if isinstance(node, LocalVariableNode):
        return f"{node.control_name or node.display_name} (local variable)"
    identity = node.display_name if node.name else _structure_word(node)
    if label := _terminal_display_name(term):
        return f"{identity}.{label}"
    outputs = [t for t in node.terminals if t.direction == "output"]
    return identity if len(outputs) == 1 else f"{identity} output {term.index}"


def _border_name(
    owner: AnyGraphNode, term: Terminal, border: StructureBorder | None
) -> str:
    """A structure's border terminal as the source of a wire, in LabVIEW's
    words: ``While Loop shift register``, ``Case Structure tunnel``,
    ``sequence local (frame 0)``."""
    if border is StructureBorder.SEQUENCE_LOCAL:
        hit = sequence_local_read_gn(owner, term.id)
        assert hit is not None
        link = hit[1]
        if link.unreadable is not None:
            return f"sequence local (unreadable: {link.unreadable})"
        return f"sequence local (frame {link.write_frame})"
    if isinstance(term, TunnelTerminal):
        kind = tunnel_kind_name(term)
    elif border is StructureBorder.ITERATION_TERMINAL:
        kind = "iteration terminal"
    else:
        kind = _terminal_display_name(term) or "terminal"
    return f"{_structure_word(owner)} {kind}"


def _name_traced_source(
    graph: InMemoryVIGraph,
    traced: TracedSource,
    constants: dict[str, Constant],
) -> str | None:
    """Name what a selector/stop wire really comes from (see
    :func:`trace_source_gn`): a constant by its value, a front-panel input by
    its label, a structure border by its kind, a node by its identity and
    output. ``None`` when the graph does not say."""
    src = traced.wire_end
    if traced.owner is not None and traced.term is not None:
        if isinstance(traced.owner, StructureNode):
            return _border_name(traced.owner, traced.term, traced.border)
        return _node_output_name(traced.owner, traced.term)
    if traced.element_net is not None:
        node = graph.get_graph_node(src.node_id)
        term = graph.get_terminal(src.terminal_id)
        if node is not None and term is not None:
            return _node_output_name(node, term)
        return None
    if traced.boundary is not None:
        return traced.boundary.name
    if (const := constants.get(src.node_id)) is not None:
        return f'"{_const_value_str(const)}"'
    return src.name


def _resolve_selector(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    constants: dict[str, Constant],
    selector_terminal: str | None,
) -> str | None:
    """Name what gates a case selector / loop stop condition: its wire traced
    back through tunnels and shift registers to the real source
    (:func:`trace_source_gn`, the same trace lvnet uses). ``constants`` is the
    VI's constants by node id. ``None`` when nothing is wired."""
    if not selector_terminal:
        return None
    traced = trace_source_gn(graph, ctx, vi_name, selector_terminal)
    if traced is None:
        return None
    return _name_traced_source(graph, traced, constants)


def _collect_structures(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    vi_name: str,
    nodes: list[AnyGraphNode],
    constants: dict[str, Constant],
) -> list[str]:
    """Summarize control flow structures.

    Case/loop entries are annotated with what gates them -- the selector
    (or stop-condition) wire traced back through tunnels and shift registers
    to its source (:func:`_resolve_selector`). ``constants`` is the VI's
    constants by node id, kept constant through recursion.

    A frame-bearing structure recurses each frame's grouped child nodes; the
    trailing recursion covers a loop/IPES body (frame-less children).
    """
    structures: list[str] = []
    for node in nodes:
        match node:
            case CaseStructureNode():
                gated = _resolve_selector(
                    graph,
                    ctx,
                    vi_name,
                    constants,
                    node.selector_terminal,
                )
                sel = f", gated on {gated}" if gated else ""
                structures.append(
                    f"Case structure ({_frame_count(len(node.frames))}{sel})"
                )
                fmap = _frame_child_nodes(graph, node, vi_name=vi_name)
                for frame in node.frames:
                    for s in _collect_structures(
                        graph,
                        ctx,
                        vi_name,
                        fmap.get(frame.selector_value, []),
                        constants,
                    ):
                        structures.append(f"  \\ {s}")
            case LoopNode():
                kind = "While loop" if node.loop_type == "whileLoop" else "For loop"
                gated = _resolve_selector(
                    graph,
                    ctx,
                    vi_name,
                    constants,
                    node.stop_condition_terminal,
                )
                structures.append(f"{kind} (stops when {gated})" if gated else kind)
            case SequenceNode():
                structures.append(
                    f"{node.kind_word} sequence ({_frame_count(len(node.frames))})"
                )
                for k, link in enumerate(node.sequence_locals):
                    if link.unreadable is not None:
                        structures.append(
                            f"  \\ Sequence local {k} unreadable: {link.unreadable}"
                        )
                fmap = _frame_child_nodes(graph, node, vi_name=vi_name)
                for frame in node.frames:
                    for s in _collect_structures(
                        graph,
                        ctx,
                        vi_name,
                        fmap.get(str(frame.index), []),
                        constants,
                    ):
                        structures.append(f"  \\ {s}")
            case EventStructureNode():
                structures.append(f"Event structure ({_frame_count(len(node.frames))})")
                fmap = _frame_child_nodes(graph, node, vi_name=vi_name)
                for i in range(len(node.frames)):
                    for s in _collect_structures(
                        graph,
                        ctx,
                        vi_name,
                        fmap.get(str(i), []),
                        constants,
                    ):
                        structures.append(f"  \\ {s}")
            case DisableStructureNode():
                structures.append(
                    f"Disable structure ({_frame_count(len(node.frames))})"
                )
                fmap = _frame_child_nodes(graph, node, vi_name=vi_name)
                for frame in node.frames:
                    for s in _collect_structures(
                        graph,
                        ctx,
                        vi_name,
                        fmap.get(frame.selector_value, []),
                        constants,
                    ):
                        structures.append(f"  \\ {s}")
            case _:
                pass
        structures.extend(
            f"  \\ {s}"
            for s in _collect_structures(
                graph,
                ctx,
                vi_name,
                _body_child_nodes(graph, node, vi_name),
                constants,
            )
        )
    return structures


def _const_type_str(c: Constant) -> str:
    """FAITHFUL human-readable type label for a constant (``type_descriptor()``
    already handles the error-cluster case internally)."""
    return c.lv_type.type_descriptor() if c.lv_type else "unknown"


def _describe_constant_line(c: Constant) -> str:
    """One-line ``name: type = value`` for a constant."""
    name = c.label or "(unnamed)"
    return f"{name}: {_const_type_str(c)} = {_const_value_str(c)}"


def _describe_label_line(lbl: Label) -> str:
    """One-line free-label text, noting its attachment target when known."""
    text = lbl.text.replace("\n", " / ")
    if lbl.attached_to:
        return f'"{text}" (attached to {lbl.attached_to.split("::")[-1]})'
    return f'"{text}"'


def _frame_constants(
    constants: list[Constant],
    parent_id: str,
    frame_key: object,
) -> list[Constant]:
    """Constants attributed to a specific frame of a structure."""
    return [
        c for c in constants if c.parent == parent_id and str(c.frame) == str(frame_key)
    ]


def _case_frame_header(node: CaseStructureNode, frame: CaseFrame) -> str:
    """A case frame's header text: the same label lvnet and JSON show for it
    (enum item names, ``No Error``/``Error``, integer ranges, quoted strings),
    which carries its own Default marker where one applies. Quoting is lvnet's
    (``lvnet_literal_token``): a string selector quotes each of its values, any
    other label is quoted once as a whole."""
    lv_type, is_error = case_selector_type_gn(node)
    label = _selector_label(frame, lv_type, is_error, quote_value=lvnet_literal_token)
    if selector_values_quoted_one_by_one(frame, lv_type, is_error):
        return label
    return lvnet_literal_token(label)


def _describe_frame_body(
    graph: InMemoryVIGraph,
    vi_name: str,
    frame_nodes: list[AnyGraphNode],
    frame_consts: list[Constant],
    all_constants: list[Constant],
    lines: list[str],
    prefix: str,
    indent: int,
    *,
    passthrough: bool,
) -> None:
    """Render a frame's nodes + attributed constants, or a placeholder."""
    if frame_nodes or frame_consts:
        if frame_nodes:
            _describe_op_list(
                graph, vi_name, frame_nodes, all_constants, lines, indent + 2
            )
        for c in frame_consts:
            lines.append(f"{prefix}    {_describe_constant_line(c)}")
    elif passthrough:
        lines.append(f"{prefix}    (pass-through)")
    else:
        lines.append(f"{prefix}    (empty)")


def _describe_op_list(
    graph: InMemoryVIGraph,
    vi_name: str,
    nodes: list[AnyGraphNode],
    constants: list[Constant],
    lines: list[str],
    indent: int,
) -> None:
    """Describe a list of graph nodes with indentation."""
    prefix = "  " * indent

    for node in nodes:
        op_desc = _describe_single_op(graph, vi_name, node)
        lines.append(f"{prefix}{op_desc}")

        match node:
            case CaseStructureNode():
                passthrough = _node_has_output_tunnel(node)
                fmap = _frame_child_nodes(graph, node, vi_name)
                for frame in node.frames:
                    lines.append(f"{prefix}  Frame {_case_frame_header(node, frame)}:")
                    _describe_frame_body(
                        graph,
                        vi_name,
                        fmap.get(frame.selector_value, []),
                        _frame_constants(constants, node.id, frame.selector_value),
                        constants,
                        lines,
                        prefix,
                        indent,
                        passthrough=passthrough,
                    )
            case SequenceNode():
                fmap = _frame_child_nodes(graph, node, vi_name)
                for i, frame in enumerate(node.frames):
                    lines.append(f"{prefix}  Frame {i}:")
                    _describe_frame_body(
                        graph,
                        vi_name,
                        fmap.get(str(frame.index), []),
                        _frame_constants(constants, node.id, i),
                        constants,
                        lines,
                        prefix,
                        indent,
                        passthrough=False,
                    )
            case EventStructureNode():
                fmap = _frame_child_nodes(graph, node, vi_name)
                for i, frame in enumerate(node.frames):
                    lines.append(f"{prefix}  Frame {frame.event_label}:")
                    _describe_frame_body(
                        graph,
                        vi_name,
                        fmap.get(str(i), []),
                        _frame_constants(constants, node.id, frame.index),
                        constants,
                        lines,
                        prefix,
                        indent,
                        passthrough=False,
                    )
            case DisableStructureNode():
                passthrough = _node_has_output_tunnel(node)
                fmap = _frame_child_nodes(graph, node, vi_name)
                for frame in node.frames:
                    default = " (default)" if frame.is_default else ""
                    lines.append(f'{prefix}  Frame "{frame.selector_value}"{default}:')
                    _describe_frame_body(
                        graph,
                        vi_name,
                        fmap.get(frame.selector_value, []),
                        _frame_constants(constants, node.id, frame.selector_value),
                        constants,
                        lines,
                        prefix,
                        indent,
                        passthrough=passthrough,
                    )
            case _:
                body = _body_child_nodes(graph, node, vi_name)
                if body:
                    _describe_op_list(
                        graph,
                        vi_name,
                        body,
                        constants,
                        lines,
                        indent + 1,
                    )
                for c in (c for c in constants if c.parent == node.id):
                    lines.append(f"{prefix}  {_describe_constant_line(c)}")
                if isinstance(node, LoopNode):
                    lines += _describe_loop_tunnels(graph, vi_name, node, prefix)


def _describe_loop_tunnels(
    graph: InMemoryVIGraph, vi_name: str, node: LoopNode, prefix: str
) -> list[str]:
    """A loop's tunnels, shift registers and count terminal, inputs first:
    each in LabVIEW's words, with what it carries and, on a Conditional
    tunnel, what drives its condition."""
    tunnels = outer_tunnels(node)
    if not tunnels:
        return []
    wires = graph.get_wires(vi_name)
    constants = constant_names(graph, vi_name)
    lines = [f"{prefix}  Tunnels:"]
    for t in tunnels:
        line = f"{prefix}    {'in ' if t.direction == 'input' else 'out'} "
        line += tunnel_kind_name(t)
        if carried := carried_name(graph, wires, t.id):
            line += f" ({carried})"
        if t.condition_terminal is not None:
            wire = condition_source(wires, t)
            source = condition_name(graph, wire, constants) if wire else "unwired"
            line += f", condition ← {source}"
        lines.append(line)
    return lines


def _describe_single_op(
    graph: InMemoryVIGraph,
    vi_name: str,
    node: AnyGraphNode,
) -> str:
    """One-line description of a graph node."""
    name = node.name or "unnamed"

    if isinstance(node, VINode):
        # Class-qualified identity (``node.display_name`` -- owning_libraries
        # chain + name, so a dispatch call reads its declaring parent class,
        # e.g. ``TestResult.lvclass:addError.vi``). Unified with
        # ``diff._elem_label``'s identical use of ``display_name`` for a
        # SubVI-call node (verified empirically: identical text to the old
        # ``node.qualified_name or name`` on every describe-exercising test
        # and corpus VI checked, including a real dynamic-dispatch call --
        # JKI-VI-Tester's ``loadTestsFromTestCase.vi`` calling
        # ``TestCase.lvclass:listAllTestMethods.vi``).
        label = node.display_name
        terms = graph.enriched_terminals(node, vi_name)
        named_inputs = [t.name for t in terms if t.direction == "input" and t.name]
        named_outputs = [t.name for t in terms if t.direction == "output" and t.name]
        if named_inputs or named_outputs:
            in_str = ", ".join(named_inputs)
            out_str = ", ".join(named_outputs)
            return f"{label}({in_str}) -> {out_str}"
        return label

    match node:
        case PrimitiveNode():
            # Property/Invoke/Feedback nodes are also ``PrimitiveNode``s --
            # keep them on the generic ``[type-word]`` one-liner, only a
            # genuine primitive gets a ``[prim N]`` label.
            if node.properties or node.method_name or _is_feedback_node(graph, node):
                node_word = (
                    get_display_name(node.node_type) if node.node_type else "unknown"
                )
                return f"{name} [{node_word}]"
            if node.prim_id is not None:
                return f"{name} [prim {node.prim_id}]"
            return name
        case GlobalVariableNode():
            access = "write" if node.is_write else "read"
            return (
                f"Global Variable `{node.control_name}` "
                f"({node.global_vi.qualified_name}) {access}"
            )
        case SharedVariableNode():
            access = "write" if node.is_write else "read"
            if node.library is not None:
                published = node.library.kind.label
                if node.library.network_path is not None:
                    published += f" at {node.library.network_path}"
                where = f" ({node.library.qualified_name}, {published})"
            elif node.library_unreadable is not None:
                where = f" (library link unreadable: {node.library_unreadable})"
            else:
                where = ""
            return f"Shared Variable `{node.variable_name}`{where} {access}"
        case CaseStructureNode():
            return f"Case Structure ({_frame_count(len(node.frames))})"
        case LoopNode():
            if node.loop_type == "whileLoop":
                return "While Loop"
            return "For Loop"
        case SequenceNode():
            return f"{node.kind_word} Sequence"
        case InPlaceNode() | DisableStructureNode() | EventStructureNode():
            # These structures have no codegen identity (``name`` is None) --
            # compose the faithful human label from caption/label/type-word
            # (``node.display_name``) instead of the raw internal XML class
            # ("decomposeRecomposeStructure", "commentNode", "eventStruct")
            # the way the generic case below does for truly unhandled kinds.
            return node.display_name
        case _:
            node_word = (
                get_display_name(node.node_type) if node.node_type else "unknown"
            )
            return f"{name} [{node_word}]"
