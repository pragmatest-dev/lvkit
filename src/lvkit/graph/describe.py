"""Format graph data as human-readable text.

Provides graph-level VI descriptions using a loaded ``InMemoryVIGraph``.
Used by the MCP server and CLI ``describe`` command.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import TYPE_CHECKING

from ..models import (
    LVType,
    ScalarValue,
    Terminal,
)
from ..parser.node_types import get_display_name
from ..vilib_resolver import get_resolver as _get_vilib_resolver
from .interface_order import is_required
from .models import (
    AnyGraphNode,
    CaseStructureNode,
    Constant,
    DisableStructureNode,
    EventStructureNode,
    ExecutionProps,
    InPlaceNode,
    InstanceProps,
    KindProps,
    Label,
    LoopNode,
    PrimitiveNode,
    SequenceNode,
    ToolbarProps,
    VIContext,
    VINode,
    WindowProps,
    bool_str,
)
from .op_walk import (
    _const_value_str,
    _render_ports,
    _subvi_ports,
)
from .queries import collect_class_context

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
    subvi_names = _collect_subvi_names(graph, top_nodes, vi_name)
    if subvi_names:
        lines.append("## Dependencies")
        for name in sorted(subvi_names):
            desc = _get_subvi_description(graph, name)
            ports = _subvi_ports(graph, name)
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
        top_nodes,
        _index_terminal_owners(graph, vi_name),
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

    func_name = ctx.name.replace(".vi", "").replace(" ", "_").lower()
    params = ", ".join(inputs)
    ret = ", ".join(outputs) if outputs else "None"

    return f"{func_name}({params}) -> {ret}"


def _default_suffix(default: ScalarValue) -> str:
    """`` = <value>`` for a terminal's default, or ``''`` when it has none.

    Only a genuinely-set default annotates (``default`` is ``None`` when the
    terminal carries no default). Strings render quoted so ``""`` reads as an
    intentional empty-string default, not a missing one.
    """
    if default is None:
        return ""
    return f' = "{default}"' if isinstance(default, str) else f" = {default}"


def _pane_terminal_line(t: Terminal, direction: str, verbose: bool) -> str:
    """One connector-pane terminal as ``name: type [(required)] [= default]``.

    Only the exceptions annotate -- ``(required)`` (Required, inputs only) and a
    set default. Verbose appends the ``[idx N]`` connector-pane slot.
    """
    line = f"{t.name}: {_terminal_type_label(t)}"
    if is_required(t, direction):
        line += " (required)"
    line += _default_suffix(t.default_value)
    if verbose and t.index is not None and t.index >= 0:
        line += f" [idx {t.index}]"
    return line


def _collect_subvi_names(
    graph: InMemoryVIGraph,
    nodes: list[AnyGraphNode],
    vi_name: str,
) -> set[str]:
    """Collect all SubVI callees' QUALIFIED names recursively — the
    class/lib-qualified identity (e.g. ``TestResult.lvclass:addError.vi``), so a
    dynamic-dispatch call reports its declaring parent class rather than a bare
    ``addError.vi``. resolve_vi_name accepts the qualified form, so the ports /
    description lookups below still resolve (to the correct copy).

    Walks the graph directly (``child_nodes`` reaches every nested node —
    a structure's frame bodies AND a loop/IPES body alike — in one
    recursion)."""
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, VINode) and node.id != node.vi_path and node.qualified_name:
            names.add(node.qualified_name)
        names.update(
            _collect_subvi_names(graph, graph.child_nodes(node.id, vi_name), vi_name)
        )
    return names


def _get_subvi_description(
    graph: InMemoryVIGraph,
    vi_name: str,
) -> str | None:
    """Get a short description for a SubVI."""
    resolver = _get_vilib_resolver()
    entry = resolver.resolve_by_name(vi_name)
    if entry and entry.description:
        desc = entry.description
        if len(desc) > 100:
            desc = desc[:97] + "..."
        return desc
    return None


def _type_label(t: LVType | None) -> str:
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


def _index_terminal_owners(
    graph: InMemoryVIGraph,
    vi_name: str,
    nodes: list[AnyGraphNode] | None = None,
    out: dict[str, tuple[AnyGraphNode, Terminal]] | None = None,
) -> dict[str, tuple[AnyGraphNode, Terminal]]:
    """Map every terminal id -> its owning ``(node, terminal)`` in ONE walk --
    the graph-native mirror of ``op_walk.index_terminal_owners``, built once so
    selector tracing (:func:`_resolve_selector`) is an O(1) lookup that reaches
    any node at any nesting depth (``child_nodes`` covers frame bodies and
    loop/IPES bodies alike). Uses ``enriched_terminals`` so a subVI terminal's
    callee-resolved name is the one recorded."""
    if out is None:
        out = {}
    if nodes is None:
        nodes = graph.top_level_nodes(vi_name)
    for node in nodes:
        for t in graph.enriched_terminals(node, vi_name):
            if t.id not in out:
                out[t.id] = (node, t)
        _index_terminal_owners(graph, vi_name, graph.child_nodes(node.id, vi_name), out)
    return out


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
            if e.get("rel") == "owns" and t.endswith(".vi")
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
            lines.append(f"    {f.name}: {_type_label(f.type)}")
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


def _resolve_selector(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    owner_by_terminal: dict[str, tuple[AnyGraphNode, Terminal]],
    selector_terminal: str | None,
) -> str | None:
    """Trace a selector/stop wire back one hop to identify what gates it.

    Returns a short ``Node.terminal`` label, a front-panel input name, or
    None when the wire source can't be located. ``owner_by_terminal`` is the
    VI-wide terminal->owner index (:func:`_index_terminal_owners`, built
    once) -- an O(1) lookup instead of re-scanning the whole node tree per call.
    """
    if not selector_terminal:
        return None
    sources = graph.incoming_edges(selector_terminal)
    if not sources:
        return None
    src = sources[0]
    hit = owner_by_terminal.get(src.terminal_id)
    if hit is None:
        for t in ctx.inputs:
            if t.id == src.terminal_id:
                return t.name
        return None
    node, term = hit
    term_name = term.name or f"idx={term.index}"
    return f"{_gated_source(node)}.{term_name}"


def _gated_source(node: AnyGraphNode) -> str:
    """Name a selector/stop wire's source for a ``gated on …`` line, composed
    for THIS view's intent — keep the structure TYPE visible. An identity-
    bearing source (subVI/primitive) shows its identity (``display_name``); an
    identity-LESS structure (``name is None``) shows its type word plus its own
    author text, so a labeled loop reads ``For Loop "For Each Front Panel
    Control"`` and an unlabeled one reads ``For Loop`` — the type is never
    dropped in favour of a free-text label. Uses only clean truth fields (the
    ``name`` split), never a string comparison against a type word."""
    if node.name:
        return node.display_name
    author = node.caption or node.label
    type_word = get_display_name(node.node_type) if node.node_type else "node"
    return f'{type_word} "{author}"' if author else type_word


def _collect_structures(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    nodes: list[AnyGraphNode],
    owner_by_terminal: dict[str, tuple[AnyGraphNode, Terminal]],
) -> list[str]:
    """Summarize control flow structures.

    Case/loop entries are annotated with what gates them -- the selector
    (or stop-condition) wire traced back one hop to its source node
    or front-panel input. ``owner_by_terminal`` is the VI-wide terminal->owner
    index (built once at the top-level call), kept constant through recursion
    so selector tracing is an O(1) lookup that reaches any node regardless of
    nesting depth -- never an O(n) node-tree rescan per structure.

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
                    owner_by_terminal,
                    node.selector_terminal,
                )
                if gated:
                    sel = f", gated on {gated}"
                elif node.selector_terminal:
                    sel = f", selector: {node.selector_terminal}"
                else:
                    sel = ""
                structures.append(f"Case structure ({len(node.frames)} frames{sel})")
                fmap = _frame_child_nodes(graph, node, vi_name=ctx.name)
                for frame in node.frames:
                    for s in _collect_structures(
                        graph,
                        ctx,
                        fmap.get(frame.selector_value, []),
                        owner_by_terminal,
                    ):
                        structures.append(f"  \\ {s}")
            case LoopNode():
                kind = "While loop" if node.loop_type == "whileLoop" else "For loop"
                gated = _resolve_selector(
                    graph,
                    ctx,
                    owner_by_terminal,
                    node.stop_condition_terminal,
                )
                structures.append(f"{kind} (stops when {gated})" if gated else kind)
            case SequenceNode():
                structures.append(f"Flat sequence ({len(node.frames)} frames)")
                fmap = _frame_child_nodes(graph, node, vi_name=ctx.name)
                for frame in node.frames:
                    for s in _collect_structures(
                        graph,
                        ctx,
                        fmap.get(str(frame.index), []),
                        owner_by_terminal,
                    ):
                        structures.append(f"  \\ {s}")
            case EventStructureNode():
                structures.append(f"Event structure ({len(node.frames)} frames)")
                fmap = _frame_child_nodes(graph, node, vi_name=ctx.name)
                for i in range(len(node.frames)):
                    for s in _collect_structures(
                        graph,
                        ctx,
                        fmap.get(str(i), []),
                        owner_by_terminal,
                    ):
                        structures.append(f"  \\ {s}")
            case DisableStructureNode():
                structures.append(f"Disable structure ({len(node.frames)} frames)")
                fmap = _frame_child_nodes(graph, node, vi_name=ctx.name)
                for frame in node.frames:
                    for s in _collect_structures(
                        graph,
                        ctx,
                        fmap.get(frame.selector_value, []),
                        owner_by_terminal,
                    ):
                        structures.append(f"  \\ {s}")
            case _:
                pass
        structures.extend(
            f"  \\ {s}"
            for s in _collect_structures(
                graph,
                ctx,
                _body_child_nodes(graph, node, ctx.name),
                owner_by_terminal,
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
        case CaseStructureNode():
            return f"Case Structure ({len(node.frames)} frames)"
        case LoopNode():
            if node.loop_type == "whileLoop":
                return "While Loop"
            return "For Loop"
        case SequenceNode():
            return "Flat Sequence"
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
