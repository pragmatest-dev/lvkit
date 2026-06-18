"""Format graph data as human-readable text.

Provides graph-level VI descriptions using a loaded ``InMemoryVIGraph``.
Used by the MCP server and CLI ``describe`` command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import (
    CaseOperation,
    LoopOperation,
    LVType,
    Operation,
    PrimitiveOperation,
    SequenceOperation,
    Terminal,
)
from ..vilib_resolver import get_resolver as _get_vilib_resolver
from .models import Constant, VIContext

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def describe_vi(graph: InMemoryVIGraph, vi_name: str) -> str:
    """Describe a VI as a documentation page.

    Uses the graph's resolved types, names, constants, and dataflow
    to produce a complete reference for what this VI does.
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
    non_error_inputs = [i for i in ctx.inputs if not i.is_error_cluster]
    if non_error_inputs:
        lines.append("## Inputs")
        for inp in non_error_inputs:
            wiring = _wiring_label(inp.wiring_rule)
            lines.append(f"  {inp.name}: {inp.python_type()} ({wiring})")
        lines.append("")
    else:
        lines.append("## Inputs")
        lines.append("  (none)")
        lines.append("")

    # Interface: Outputs
    non_error_outputs = [o for o in ctx.outputs if not o.is_error_cluster]
    if non_error_outputs:
        lines.append("## Outputs")
        for out in non_error_outputs:
            lines.append(f"  {out.name}: {out.python_type()}")
        lines.append("")
    else:
        lines.append("## Outputs")
        lines.append("  (none)")
        lines.append("")

    # Class context: when this VI is a .lvclass method
    lines.extend(_describe_class_context(graph, ctx))

    # Constants: show actual values
    if ctx.constants:
        lines.append("## Constants")
        for c in ctx.constants:
            type_str = c.lv_type.to_python() if c.lv_type else "unknown"
            name = c.name or "(unnamed)"
            lines.append(f"  {name}: {type_str} = {c.value!r}")
        lines.append("")

    # Dependencies: SubVI calls with their signatures and descriptions
    subvi_names = _collect_subvi_names(ctx.operations)
    if subvi_names:
        lines.append("## Dependencies")
        for name in sorted(subvi_names):
            desc = _get_subvi_description(graph, name)
            sig = _subvi_signature(graph, name)
            entry = f"  {name}"
            if sig:
                entry += f": {sig}"
            if desc:
                entry += f" — {desc}"
            lines.append(entry)
        lines.append("")

    # Control flow
    structures = _collect_structures(graph, ctx, ctx.operations, ctx.operations)
    if structures:
        lines.append("## Control Flow")
        for s in structures:
            lines.append(f"  {s}")
        lines.append("")

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

    non_error_outputs = [
        o for o in ctx.outputs if not o.is_error_cluster
    ]
    if non_error_outputs:
        lines.append("")
        lines.append("Returns:")
        for out in non_error_outputs:
            lines.append(f"  {out.name}: {out.python_type()}")

    return "\n".join(lines)


def describe_dataflow(
    graph: InMemoryVIGraph,
    vi_name: str,
    operation_id: str | None = None,
) -> str:
    """Describe data flow — where values come from and go to."""
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
        lines.append(f"  {src_name} → {dst_name}")

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
            _describe_case_structure(op, lines)
        case LoopOperation():
            _describe_loop(op, lines)
        case SequenceOperation():
            _describe_sequence(op, lines)
        case _:
            lines.append(f"Operation {operation_id}: {op.name}")
            lines.append(f"  Type: {op.node_type}")
            lines.append(f"  Labels: {op.labels}")

    return "\n".join(lines)


def describe_constants(
    graph: InMemoryVIGraph, vi_name: str,
) -> str:
    """List all constants used in a VI."""
    vi_name = graph.resolve_vi_name(vi_name)
    constants = list(graph.get_constants(vi_name))

    lines = [f"Constants in {vi_name}:", ""]
    for c in constants:
        type_str = c.lv_type.to_python() if c.lv_type else "unknown"
        name = c.name or "(unnamed)"
        lines.append(f"  {name}: {type_str} = {c.value!r}")

    if not constants:
        lines.append("  (none)")

    return "\n".join(lines)


# === Helpers ===


def _format_signature(ctx: VIContext) -> str:
    """Format function signature from VIContext."""
    inputs = []
    for inp in ctx.inputs:
        if inp.is_error_cluster:
            continue
        name = inp.name or "input"
        type_str = inp.python_type()
        inputs.append(f"{name}: {type_str}")

    outputs = []
    for out in ctx.outputs:
        if out.is_error_cluster:
            continue
        outputs.append(f"{out.name}: {out.python_type()}")

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
        if "SubVI" in op.labels and op.name:
            names.add(op.name)
        match op:
            case CaseOperation() | SequenceOperation():
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
    """Compact human-readable LVType label for class fields."""
    return t.to_python() if t is not None else "Any"


def _describe_class_context(
    graph: InMemoryVIGraph, ctx: VIContext,
) -> list[str]:
    """Describe the owning class when this VI is a .lvclass method.

    Surfaces the class's parent, fields, and sibling methods — context an
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


def _subvi_signature(graph: InMemoryVIGraph, name: str) -> str | None:
    """One-line signature for a called SubVI.

    Loaded VIs use their resolved front-panel signature; unloaded vilib
    refs fall back to the resolver's terminal layout. Returns None when
    neither is available.
    """
    loaded = set(graph.list_vis())
    qname = name
    if qname not in loaded:
        try:
            resolved = graph.resolve_vi_name(name)
        except (KeyError, ValueError):
            resolved = None
        if resolved in loaded:
            qname = resolved  # type: ignore[assignment]

    if qname in loaded:
        sctx = graph.get_vi_context(qname)
        ins = [
            f"{t.name}: {t.python_type()}"
            for t in sctx.inputs if not t.is_error_cluster
        ]
        outs = [
            f"{t.name}: {t.python_type()}"
            for t in sctx.outputs if not t.is_error_cluster
        ]
        return f"({', '.join(ins)}) → ({', '.join(outs)})"

    entry = _get_vilib_resolver().resolve_by_name(name)
    if entry is not None and entry.terminals:
        ins = [
            f"{t.name}: {t.type}"
            for t in entry.terminals if t.direction == "input"
        ]
        outs = [
            f"{t.name}: {t.type}"
            for t in entry.terminals if t.direction == "output"
        ]
        return f"({', '.join(ins)}) → ({', '.join(outs)})"
    return None


def _find_op_owning_terminal(
    operations: list[Operation], terminal_id: str | None,
) -> tuple[Operation, Terminal] | None:
    """Recursively find the operation that owns the given terminal."""
    if terminal_id is None:
        return None
    for op in operations:
        for t in op.terminals:
            if t.id == terminal_id:
                return op, t
        hit = _find_op_owning_terminal(op.inner_nodes, terminal_id)
        if hit:
            return hit
        if isinstance(op, (CaseOperation, SequenceOperation)):
            for frame in op.frames:
                hit = _find_op_owning_terminal(frame.operations, terminal_id)
                if hit:
                    return hit
    return None


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
    op_name = op.name or op.id
    term_name = term.name or f"idx={term.index}"
    return f"{op_name}.{term_name}"


def _collect_structures(
    graph: InMemoryVIGraph,
    ctx: VIContext,
    operations: list[Operation],
    root_ops: list[Operation],
) -> list[str]:
    """Summarize control flow structures.

    Case/loop entries are annotated with what gates them — the selector
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
                        structures.append(f"  └ {s}")
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
                        structures.append(f"  └ {s}")
            case _:
                pass
        structures.extend(
            f"  └ {s}"
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
                for frame in op.frames:
                    default = " (default)" if frame.is_default else ""
                    lines.append(
                        f'{prefix}  Frame "{frame.selector_value}"{default}:'
                    )
                    if frame.operations:
                        _describe_op_list(
                            frame.operations, constants, lines,
                            indent + 2,
                        )
                    else:
                        lines.append(f"{prefix}    (empty)")
            case SequenceOperation():
                for i, frame in enumerate(op.frames):
                    lines.append(f'{prefix}  Frame {i}:')
                    if frame.operations:
                        _describe_op_list(
                            frame.operations, constants, lines,
                            indent + 2,
                        )
                    else:
                        lines.append(f"{prefix}    (empty)")
            case _:
                if op.inner_nodes:
                    _describe_op_list(
                        op.inner_nodes, constants, lines, indent + 1,
                    )


def _describe_single_op(op: Operation) -> str:
    """One-line description of an operation."""
    name = op.name or "unnamed"

    if "SubVI" in op.labels:
        named_inputs = [
            t.name for t in op.terminals
            if t.direction == "input" and not t.is_error_cluster and t.name
        ]
        named_outputs = [
            t.name for t in op.terminals
            if t.direction == "output" and not t.is_error_cluster and t.name
        ]
        if named_inputs or named_outputs:
            in_str = ", ".join(named_inputs)
            out_str = ", ".join(named_outputs)
            return f"{name}({in_str}) → {out_str}"
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
        case _:
            return f"{name} [{op.node_type or 'unknown'}]"


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
    op: CaseOperation, lines: list[str],
) -> None:
    """Describe a case structure in detail."""
    lines.append(f"Case Structure: {op.id}")
    if op.selector_terminal:
        lines.append(f"  Selector terminal: {op.selector_terminal}")

    for t in op.terminals:
        if t.id == op.selector_terminal and t.lv_type:
            lines.append(f"  Selector type: {t.lv_type.to_python()}")
            break

    lines.append(f"  Frames: {len(op.frames)}")
    for frame in op.frames:
        default = " (default)" if frame.is_default else ""
        lines.append(
            f"  Frame \"{frame.selector_value}\"{default}:"
            f" {len(frame.operations)} operations"
        )
        for fop in frame.operations:
            lines.append(f"    - {_describe_single_op(fop)}")


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
                f" → inner={tunnel.inner_terminal_uid}"
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
