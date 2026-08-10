"""Format graph data as human-readable text.

Provides graph-level VI descriptions using a loaded ``InMemoryVIGraph``.
Used by the MCP server and CLI ``describe`` command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import (
    CaseOperation,
    DisableStructureOperation,
    EventOperation,
    InPlaceOperation,
    LoopOperation,
    LVType,
    Operation,
    PrimitiveOperation,
    SequenceOperation,
    Terminal,
)
from ..parser.node_types import get_display_name
from ..vilib_resolver import get_resolver as _get_vilib_resolver
from .core import kind_display
from .models import Constant, VIContext
from .netlist import build_netlist, component_line, render_netlist
from .op_walk import (
    _const_value_str,
    _find_op_owning_terminal,
    _has_output_tunnel,
    _render_ports,
    _subvi_ports,
)

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def describe_vi(
    graph: InMemoryVIGraph, vi_name: str, *, verbose: bool = False,
) -> str:
    """Describe a VI as a documentation page.

    Uses the graph's resolved types, names, constants, and dataflow to
    produce a complete reference for what this VI does. Default
    (non-verbose) output is unchanged: Inputs/Outputs/Class/Constants,
    then ``## Dependencies`` (subVI signatures) / ``## Control Flow`` /
    ``## Operations``.

    ``verbose`` replaces Dependencies + Control Flow + Operations with a
    declare-then-wire pair: ``## Components`` (every distinct subVI/primitive's
    typed interface, declared once -- see ``lvkit.graph.netlist``) followed by
    a final ``## Netlist`` (every node instantiated, node-first, with wiring
    -- its ``case (selector):`` scopes already cover control flow, so there is
    no separate ``## Control Flow`` section in verbose output).
    """
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    lines: list[str] = []

    # Title and signature
    lines.append(f"# {vi_name}")
    lines.append("")
    lines.append(f"  {_format_signature(ctx)}")
    lines.append("")

    # Interface: Inputs
    if ctx.inputs:
        lines.append("## Inputs")
        for inp in ctx.inputs:
            wiring = _wiring_label(inp.wiring_rule)
            lines.append(f"  {inp.name}: {_terminal_type_label(inp)} ({wiring})")
        lines.append("")
    else:
        lines.append("## Inputs")
        lines.append("  (none)")
        lines.append("")

    # Interface: Outputs
    if ctx.outputs:
        lines.append("## Outputs")
        for out in ctx.outputs:
            lines.append(f"  {out.name}: {_terminal_type_label(out)}")
        lines.append("")
    else:
        lines.append("## Outputs")
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

    # verbose: build the netlist IR once, shared by Components and Netlist.
    netlist_module = build_netlist(graph, vi_name) if verbose else None

    if verbose:
        assert netlist_module is not None
        if netlist_module.components:
            lines.append("## Components")
            for c in netlist_module.components:
                lines.append(f"  {component_line(c)}")
            lines.append("")
    else:
        # Dependencies: SubVI calls with their signatures and descriptions
        subvi_names = _collect_subvi_names(ctx.operations)
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

    # Control flow: non-verbose only. Verbose's ``## Netlist`` below already
    # covers control flow in full (its ``case (selector):`` scopes, with
    # correctly tunnel-resolved selectors) -- this shallower summary would
    # be both redundant and stale there (its selector naming doesn't hop
    # through tunnels the way ``build_netlist`` does).
    if not verbose:
        structures = _collect_structures(graph, ctx, ctx.operations, ctx.operations)
        if structures:
            lines.append("## Control Flow")
            for s in structures:
                lines.append(f"  {s}")
            lines.append("")

    if verbose:
        assert netlist_module is not None
        lines.append("## Netlist")
        lines.append(render_netlist(netlist_module))
    else:
        # Operations
        lines.append("## Operations")
        _describe_op_list(ctx.operations, ctx.constants, lines, indent=0)

    return "\n".join(lines)


def describe_operations(
    graph: InMemoryVIGraph, vi_name: str,
) -> str:
    """Describe a VI's operations in execution order."""
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    lines: list[str] = []
    lines.append(f"Operations for {vi_name}:")
    lines.append("")

    _describe_op_list(ctx.operations, ctx.constants, lines, indent=0)

    if ctx.outputs:
        lines.append("")
        lines.append("Returns:")
        for out in ctx.outputs:
            lines.append(f"  {out.name}: {_terminal_type_label(out)}")

    return "\n".join(lines)


def describe_dataflow(
    graph: InMemoryVIGraph,
    vi_name: str,
    operation_id: str | None = None,
) -> str:
    """Describe data flow -- where values come from and go to."""
    vi_name = graph.resolve_vi_name(vi_name)
    wires = list(graph.get_wires(vi_name))

    if operation_id:
        wires = [
            w for w in wires
            if w.source.node_id == operation_id
            or w.dest.node_id == operation_id
        ]

    lines: list[str] = []
    lines.append(
        f"Dataflow for {vi_name}"
        + (f" (operation {operation_id})" if operation_id else "")
        + ":"
    )
    lines.append("")

    for wire in wires:
        src_name = wire.source.name or wire.source.node_id.split("::")[-1]
        dst_name = wire.dest.name or wire.dest.node_id.split("::")[-1]
        lines.append(f"  {src_name} -> {dst_name}")

    if not wires:
        lines.append("  (no wires)")

    return "\n".join(lines)


def describe_structure(
    graph: InMemoryVIGraph,
    vi_name: str,
    operation_id: str,
) -> str:
    """Describe a structure node (case, loop, sequence) in detail."""
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    op = _find_operation(ctx.operations, operation_id)
    if op is None:
        return f"Operation {operation_id} not found in {vi_name}"

    lines: list[str] = []

    match op:
        case CaseOperation():
            _describe_case_structure(op, list(ctx.constants), lines)
        case LoopOperation():
            _describe_loop(op, lines)
        case SequenceOperation():
            _describe_sequence(op, lines)
        case _:
            lines.append(f"Operation {operation_id}: {op.display_name}")
            node_word = get_display_name(op.node_type) if op.node_type else "unknown"
            lines.append(f"  Type: {node_word}")
            lines.append(f"  Kind: {kind_display(op.kind)}")

    return "\n".join(lines)


def describe_constants(
    graph: InMemoryVIGraph, vi_name: str,
) -> str:
    """List all constants used in a VI."""
    vi_name = graph.resolve_vi_name(vi_name)
    constants = list(graph.get_constants(vi_name))

    lines = [f"Constants in {vi_name}:", ""]
    for c in constants:
        line = f"  {_describe_constant_line(c)}"
        if c.parent is not None:
            line += f"  [{c.parent.split('::')[-1]} frame {c.frame}]"
        lines.append(line)

    if not constants:
        lines.append("  (none)")

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


def _wiring_label(rule: int) -> str:
    """Convert wiring rule to human label."""
    return {
        0: "unknown",
        1: "required",
        2: "recommended",
        3: "optional",
    }.get(rule, "unknown")


def _collect_subvi_names(operations: list[Operation]) -> set[str]:
    """Collect all SubVI names from operations recursively."""
    names: set[str] = set()
    for op in operations:
        if op.kind == "vi" and op.name:
            names.add(op.name)
        match op:
            case (
                CaseOperation() | SequenceOperation() | EventOperation()
                | DisableStructureOperation()
            ):
                for frame in op.frames:
                    names.update(_collect_subvi_names(frame.operations))
            case _:
                pass
        names.update(_collect_subvi_names(op.inner_nodes))
    return names


def _get_subvi_description(
    graph: InMemoryVIGraph, vi_name: str,
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
    annotation — see ``LVType.lv_label()``)."""
    return t.lv_label() if t is not None else "Any"


def _terminal_type_label(t: Terminal) -> str:
    """FAITHFUL LabVIEW type label for a terminal (never ``python_type()``'s
    codegen-target Python annotation)."""
    return t.lv_type.lv_label() if t.lv_type is not None else "Any"


def _describe_class_context(
    graph: InMemoryVIGraph, ctx: VIContext,
) -> list[str]:
    """Describe the owning class when this VI is a .lvclass method.

    Surfaces the class's parent, fields, and sibling methods -- context an
    agent needs to write a method body but which is absent from the VI's
    own dataflow. Returns an empty list for non-class VIs.
    """
    cls = ctx.library
    if not cls or not cls.endswith(".lvclass"):
        return []
    if not graph._dep_graph.has_node(cls):
        return []

    attrs = graph._dep_graph.nodes[cls]
    fields = graph.get_class_fields(cls) or []
    siblings = sorted({
        t.split(":")[-1]
        for _, t, e in graph._dep_graph.edges(cls, data=True)
        if e.get("rel") == "owns" and t.endswith(".vi")
    })

    lines = ["## Class", f"  {cls}"]
    parent = attrs.get("parent_class")
    if parent:
        lines.append(f"  parent: {parent}")
    if fields:
        lines.append("  fields:")
        for f in fields:
            lines.append(f"    {f.name}: {_type_label(f.type)}")
    if siblings:
        lines.append(f"  methods: {', '.join(siblings)}")
    lines.append("")
    return lines


def _resolve_selector(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    root_ops: list[Operation],
    selector_terminal: str | None,
) -> str | None:
    """Trace a selector/stop wire back one hop to identify what gates it.

    Returns a short ``Node.terminal`` label, a front-panel input name, or
    None when the wire source can't be located.
    """
    if not selector_terminal:
        return None
    sources = graph.incoming_edges(selector_terminal)
    if not sources:
        return None
    src = sources[0]
    hit = _find_op_owning_terminal(root_ops, src.terminal_id)
    if hit is None:
        for t in ctx.inputs:
            if t.id == src.terminal_id:
                return t.name
        return None
    op, term = hit
    term_name = term.name or f"idx={term.index}"
    return f"{_gated_source(op)}.{term_name}"


def _gated_source(op: Operation) -> str:
    """Name a selector/stop wire's source for a ``gated on …`` line, composed
    for THIS view's intent — keep the structure TYPE visible. An identity-
    bearing source (subVI/primitive) shows its identity (``display_name``); an
    identity-LESS structure (``name is None``) shows its type word plus its own
    author text, so a labeled loop reads ``For Loop "For Each Front Panel
    Control"`` and an unlabeled one reads ``For Loop`` — the type is never
    dropped in favour of a free-text label. Uses only clean truth fields (the
    ``name`` split), never a string comparison against a type word."""
    if op.name:
        return op.display_name
    author = op.caption or op.label
    type_word = get_display_name(op.node_type) if op.node_type else "node"
    return f'{type_word} "{author}"' if author else type_word


def _collect_structures(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    operations: list[Operation],
    root_ops: list[Operation],
) -> list[str]:
    """Summarize control flow structures.

    Case/loop entries are annotated with what gates them -- the selector
    (or stop-condition) wire traced back one hop to its source operation
    or front-panel input. ``root_ops`` is the VI's full top-level operation
    list, kept constant through recursion so selector tracing can reach any
    node regardless of nesting depth.
    """
    structures: list[str] = []
    for op in operations:
        match op:
            case CaseOperation():
                n_frames = len(op.frames)
                gated = _resolve_selector(
                    graph, ctx, root_ops, op.selector_terminal,
                )
                if gated:
                    sel = f", gated on {gated}"
                elif op.selector_terminal:
                    sel = f", selector: {op.selector_terminal}"
                else:
                    sel = ""
                structures.append(f"Case structure ({n_frames} frames{sel})")
                for frame in op.frames:
                    for s in _collect_structures(
                        graph, ctx, frame.operations, root_ops,
                    ):
                        structures.append(f"  \\ {s}")
            case LoopOperation():
                kind = "While loop" if op.loop_type == "whileLoop" else "For loop"
                gated = _resolve_selector(
                    graph, ctx, root_ops, op.stop_condition_terminal,
                )
                structures.append(
                    f"{kind} (stops when {gated})" if gated else kind
                )
            case SequenceOperation():
                n_frames = len(op.frames)
                structures.append(f"Flat sequence ({n_frames} frames)")
                for frame in op.frames:
                    for s in _collect_structures(
                        graph, ctx, frame.operations, root_ops,
                    ):
                        structures.append(f"  \\ {s}")
            case EventOperation():
                n_frames = len(op.frames)
                structures.append(f"Event structure ({n_frames} frames)")
                for frame in op.frames:
                    for s in _collect_structures(
                        graph, ctx, frame.operations, root_ops,
                    ):
                        structures.append(f"  \\ {s}")
            case DisableStructureOperation():
                n_frames = len(op.frames)
                structures.append(f"Disable structure ({n_frames} frames)")
                for frame in op.frames:
                    for s in _collect_structures(
                        graph, ctx, frame.operations, root_ops,
                    ):
                        structures.append(f"  \\ {s}")
            case _:
                pass
        structures.extend(
            f"  \\ {s}"
            for s in _collect_structures(graph, ctx, op.inner_nodes, root_ops)
        )
    return structures


def _count_operations(operations: list[Operation]) -> int:
    """Count total operations including nested."""
    count = len(operations)
    for op in operations:
        match op:
            case CaseOperation() | SequenceOperation():
                for frame in op.frames:
                    count += _count_operations(frame.operations)
            case _:
                pass
        count += _count_operations(op.inner_nodes)
    return count


def _const_type_str(c: Constant) -> str:
    """FAITHFUL human-readable type label for a constant (``lv_label()``
    already handles the error-cluster case internally)."""
    return c.lv_type.lv_label() if c.lv_type else "unknown"


def _describe_constant_line(c: Constant) -> str:
    """One-line ``name: type = value`` for a constant."""
    name = c.label or "(unnamed)"
    return f"{name}: {_const_type_str(c)} = {_const_value_str(c)}"


def _frame_constants(
    constants: list[Constant], parent_id: str, frame_key: object,
) -> list[Constant]:
    """Constants attributed to a specific frame of a structure."""
    return [
        c for c in constants
        if c.parent == parent_id and str(c.frame) == str(frame_key)
    ]


def _describe_frame_body(
    frame_ops: list[Operation],
    frame_consts: list[Constant],
    all_constants: list[Constant],
    lines: list[str],
    prefix: str,
    indent: int,
    *,
    passthrough: bool,
) -> None:
    """Render a frame's operations + attributed constants, or a placeholder."""
    if frame_ops or frame_consts:
        if frame_ops:
            _describe_op_list(frame_ops, all_constants, lines, indent + 2)
        for c in frame_consts:
            lines.append(f"{prefix}    {_describe_constant_line(c)}")
    elif passthrough:
        lines.append(f"{prefix}    (pass-through)")
    else:
        lines.append(f"{prefix}    (empty)")


def _describe_op_list(
    operations: list[Operation],
    constants: list[Constant],
    lines: list[str],
    indent: int,
) -> None:
    """Describe a list of operations with indentation."""
    prefix = "  " * indent

    for op in operations:
        op_desc = _describe_single_op(op)
        lines.append(f"{prefix}{op_desc}")

        match op:
            case CaseOperation():
                passthrough = _has_output_tunnel(op)
                for frame in op.frames:
                    default = " (default)" if frame.is_default else ""
                    lines.append(
                        f'{prefix}  Frame "{frame.selector_value}"{default}:'
                    )
                    _describe_frame_body(
                        frame.operations,
                        _frame_constants(constants, op.id, frame.selector_value),
                        constants, lines, prefix, indent,
                        passthrough=passthrough,
                    )
            case SequenceOperation():
                for i, frame in enumerate(op.frames):
                    lines.append(f'{prefix}  Frame {i}:')
                    _describe_frame_body(
                        frame.operations,
                        _frame_constants(constants, op.id, i),
                        constants, lines, prefix, indent,
                        passthrough=False,
                    )
            case EventOperation():
                for frame in op.frames:
                    lines.append(f'{prefix}  Frame {frame.event_label}:')
                    _describe_frame_body(
                        frame.operations,
                        _frame_constants(constants, op.id, frame.index),
                        constants, lines, prefix, indent,
                        passthrough=False,
                    )
            case DisableStructureOperation():
                passthrough = _has_output_tunnel(op)
                for frame in op.frames:
                    default = " (default)" if frame.is_default else ""
                    lines.append(
                        f'{prefix}  Frame "{frame.selector_value}"{default}:'
                    )
                    _describe_frame_body(
                        frame.operations,
                        _frame_constants(constants, op.id, frame.selector_value),
                        constants, lines, prefix, indent,
                        passthrough=passthrough,
                    )
            case _:
                if op.inner_nodes:
                    _describe_op_list(
                        op.inner_nodes, constants, lines, indent + 1,
                    )
                for c in (c for c in constants if c.parent == op.id):
                    lines.append(f"{prefix}  {_describe_constant_line(c)}")


def _describe_single_op(op: Operation) -> str:
    """One-line description of an operation."""
    name = op.name or "unnamed"

    if op.kind == "vi":
        named_inputs = [
            t.name for t in op.terminals
            if t.direction == "input" and t.name
        ]
        named_outputs = [
            t.name for t in op.terminals
            if t.direction == "output" and t.name
        ]
        if named_inputs or named_outputs:
            in_str = ", ".join(named_inputs)
            out_str = ", ".join(named_outputs)
            return f"{name}({in_str}) -> {out_str}"
        return name

    match op:
        case PrimitiveOperation():
            prim_desc = name
            if op.primResID:
                prim_desc = f"{name} [prim {op.primResID}]"
            return prim_desc
        case CaseOperation():
            return f"Case Structure ({len(op.frames)} frames)"
        case LoopOperation():
            if op.loop_type == "whileLoop":
                return "While Loop"
            return "For Loop"
        case SequenceOperation():
            return "Flat Sequence"
        case InPlaceOperation() | DisableStructureOperation() | EventOperation():
            # These structures have no codegen identity (``name`` is None) --
            # compose the faithful human label from caption/label/type-word
            # (``display_name``) instead of the old baked-in ``name`` --
            # never fall back to the raw internal XML class
            # ("decomposeRecomposeStructure", "commentNode", "eventStruct")
            # the way the generic case below does for truly unhandled
            # operation kinds.
            return op.display_name
        case _:
            node_word = get_display_name(op.node_type) if op.node_type else "unknown"
            return f"{name} [{node_word}]"


def _find_operation(
    operations: list[Operation], op_id: str,
) -> Operation | None:
    """Find an operation by ID, searching recursively."""
    for op in operations:
        if op.id == op_id:
            return op
        match op:
            case CaseOperation() | SequenceOperation():
                for frame in op.frames:
                    found = _find_operation(frame.operations, op_id)
                    if found:
                        return found
            case _:
                pass
        found = _find_operation(op.inner_nodes, op_id)
        if found:
            return found
    return None


def _describe_case_structure(
    op: CaseOperation, constants: list[Constant], lines: list[str],
) -> None:
    """Describe a case structure in detail."""
    lines.append(f"Case Structure: {op.id}")
    if op.selector_terminal:
        lines.append(f"  Selector terminal: {op.selector_terminal}")

    for t in op.terminals:
        if t.id == op.selector_terminal and t.lv_type:
            lines.append(f"  Selector type: {t.lv_type.lv_label()}")
            break

    passthrough = _has_output_tunnel(op)
    lines.append(f"  Frames: {len(op.frames)}")
    for frame in op.frames:
        default = " (default)" if frame.is_default else ""
        frame_consts = _frame_constants(constants, op.id, frame.selector_value)
        lines.append(
            f"  Frame \"{frame.selector_value}\"{default}:"
            f" {len(frame.operations)} operations,"
            f" {len(frame_consts)} constants"
        )
        for fop in frame.operations:
            lines.append(f"    - {_describe_single_op(fop)}")
        for c in frame_consts:
            lines.append(f"    - constant {_describe_constant_line(c)}")
        if not frame.operations and not frame_consts and passthrough:
            lines.append("    - (pass-through)")


def _describe_loop(op: LoopOperation, lines: list[str]) -> None:
    """Describe a loop in detail."""
    loop_kind = "While Loop" if op.loop_type == "whileLoop" else "For Loop"
    lines.append(f"{loop_kind}: {op.id}")

    if op.stop_condition_terminal:
        lines.append(
            f"  Stop condition: {op.stop_condition_terminal}"
        )

    if op.tunnels:
        lines.append("  Tunnels:")
        for tunnel in op.tunnels:
            lines.append(
                f"    {tunnel.tunnel_type}:"
                f" outer={tunnel.outer_terminal_uid}"
                f" -> inner={tunnel.inner_terminal_uid}"
            )

    if op.inner_nodes:
        lines.append(f"  Body: {len(op.inner_nodes)} operations")
        for inner in op.inner_nodes:
            lines.append(f"    - {_describe_single_op(inner)}")


def _describe_sequence(
    op: SequenceOperation, lines: list[str],
) -> None:
    """Describe a flat sequence."""
    lines.append(f"Flat Sequence: {op.id}")
    if op.frames:
        lines.append(f"  Frames: {len(op.frames)}")
        for i, frame in enumerate(op.frames):
            lines.append(
                f"  Frame {i}: {len(frame.operations)} operations"
            )
            for fop in frame.operations:
                lines.append(f"    - {_describe_single_op(fop)}")
    elif op.inner_nodes:
        lines.append(f"  Operations: {len(op.inner_nodes)}")
        for inner in op.inner_nodes:
            lines.append(f"    - {_describe_single_op(inner)}")
