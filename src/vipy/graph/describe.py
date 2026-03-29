"""Format graph data as human-readable text for LLM consumption.

These functions take graph query results and produce structured text
that an LLM can read and reason about — not JSON dumps, not code,
but narrative descriptions of what a VI does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..graph_types import (
    Constant,
    Operation,
    VIContext,
)

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def describe_vi(graph: InMemoryVIGraph, vi_name: str) -> str:
    """Describe a VI's purpose, signature, and structure.

    Returns a human-readable overview suitable for an LLM to understand
    what the VI does before diving into operations.
    """
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    lines: list[str] = []

    # Signature
    sig = _format_signature(ctx)
    lines.append(sig)
    lines.append("")

    # Inputs
    if ctx.inputs:
        lines.append("Inputs:")
        for inp in ctx.inputs:
            if inp.is_error_cluster:
                lines.append(
                    f"  - {inp.name}: ErrorCluster"
                    f" [handled via exceptions]"
                )
                continue
            wiring = _wiring_label(inp.wiring_rule)
            lines.append(
                f"  - {inp.name}: {inp.python_type()}"
                f" ({wiring})"
            )
        lines.append("")

    # Outputs
    if ctx.outputs:
        lines.append("Outputs:")
        for out in ctx.outputs:
            if out.is_error_cluster:
                lines.append(
                    f"  - {out.name}: ErrorCluster"
                    f" [handled via exceptions]"
                )
                continue
            lines.append(f"  - {out.name}: {out.python_type()}")
        lines.append("")

    # SubVI calls
    subvi_names = _collect_subvi_names(ctx.operations)
    if subvi_names:
        lines.append("SubVI calls:")
        for name in sorted(subvi_names):
            desc = _get_subvi_description(graph, name)
            if desc:
                lines.append(f"  - {name} — {desc}")
            else:
                lines.append(f"  - {name}")
        lines.append("")

    # Structure summary
    structures = _collect_structures(ctx.operations)
    if structures:
        lines.append("Control flow:")
        for s in structures:
            lines.append(f"  - {s}")
        lines.append("")

    # Stats
    op_count = _count_operations(ctx.operations)
    const_count = len(ctx.constants)
    lines.append(f"Operations: {op_count}")
    lines.append(f"Constants: {const_count}")
    if ctx.has_parallel_branches:
        lines.append("Parallel branches: yes")

    return "\n".join(lines)


def describe_operations(
    graph: InMemoryVIGraph, vi_name: str,
) -> str:
    """Describe a VI's operations in execution order.

    Shows topological tiers, parallel groups, and nested structures
    with human-readable operation descriptions.
    """
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    lines: list[str] = []
    lines.append(f"Operations for {vi_name}:")
    lines.append("")

    _describe_op_list(ctx.operations, ctx.constants, lines, indent=0)

    # Return value
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
    """Describe data flow — where values come from and go to.

    If operation_id is given, shows only wires connected to that operation.
    Otherwise shows all wires.
    """
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

    if op.case_frames:
        _describe_case_structure(op, lines)
    elif op.loop_type:
        _describe_loop(op, lines)
    elif "FlatSequence" in op.labels or "Sequence" in op.labels:
        _describe_sequence(op, lines)
    else:
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
        for frame in op.case_frames:
            names.update(_collect_subvi_names(frame.operations))
        names.update(_collect_subvi_names(op.inner_nodes))
    return names


def _get_subvi_description(
    graph: InMemoryVIGraph, vi_name: str,
) -> str | None:
    """Get a short description for a SubVI."""
    from ..vilib_resolver import get_resolver

    resolver = get_resolver()
    entry = resolver.resolve_by_name(vi_name)
    if entry and entry.description:
        # Truncate long descriptions
        desc = entry.description
        if len(desc) > 100:
            desc = desc[:97] + "..."
        return desc
    return None


def _collect_structures(
    operations: list[Operation],
) -> list[str]:
    """Summarize control flow structures."""
    structures: list[str] = []
    for op in operations:
        if op.case_frames:
            selector = op.selector_terminal or "unknown"
            n_frames = len(op.case_frames)
            structures.append(
                f"Case structure ({n_frames} frames,"
                f" selector: {selector})"
            )
        elif op.loop_type == "whileLoop":
            structures.append("While loop")
        elif op.loop_type == "forLoop":
            structures.append("For loop")
        elif "FlatSequence" in op.labels:
            n_frames = len(op.case_frames) if op.case_frames else 0
            n_inner = len(op.inner_nodes)
            structures.append(
                f"Flat sequence ({n_frames or n_inner} frames)"
            )
        # Recurse
        for frame in op.case_frames:
            for s in _collect_structures(frame.operations):
                structures.append(f"  └ {s}")
        for s in _collect_structures(op.inner_nodes):
            structures.append(f"  └ {s}")
    return structures


def _count_operations(operations: list[Operation]) -> int:
    """Count total operations including nested."""
    count = len(operations)
    for op in operations:
        for frame in op.case_frames:
            count += _count_operations(frame.operations)
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

        # Case frames
        if op.case_frames:
            for frame in op.case_frames:
                default = " (default)" if frame.is_default else ""
                lines.append(
                    f"{prefix}  Frame"
                    f" \"{frame.selector_value}\"{default}:"
                )
                if frame.operations:
                    _describe_op_list(
                        frame.operations, constants, lines,
                        indent + 2,
                    )
                else:
                    lines.append(f"{prefix}    (empty)")

        # Inner nodes (loop body, sequence frames)
        if op.inner_nodes and not op.case_frames:
            _describe_op_list(
                op.inner_nodes, constants, lines, indent + 1,
            )


def _describe_single_op(op: Operation) -> str:
    """One-line description of an operation."""
    name = op.name or "unnamed"

    if "SubVI" in op.labels:
        inputs = [
            t for t in op.terminals
            if t.direction == "input" and not t.is_error_cluster
        ]
        outputs = [
            t for t in op.terminals
            if t.direction == "output" and not t.is_error_cluster
        ]
        in_str = ", ".join(
            t.name or f"idx{t.index}" for t in inputs
        )
        out_str = ", ".join(
            t.name or f"idx{t.index}" for t in outputs
        )
        return f"{name}({in_str}) → {out_str}"

    if "Primitive" in op.labels:
        prim_desc = name
        if op.primResID:
            prim_desc = f"{name} [prim {op.primResID}]"
        return prim_desc

    if op.case_frames:
        n_frames = len(op.case_frames)
        return f"Case Structure ({n_frames} frames)"

    if op.loop_type == "whileLoop":
        return "While Loop"
    if op.loop_type == "forLoop":
        return "For Loop"

    if "FlatSequence" in op.labels:
        return "Flat Sequence"

    return f"{name} [{op.node_type or 'unknown'}]"


def _find_operation(
    operations: list[Operation], op_id: str,
) -> Operation | None:
    """Find an operation by ID, searching recursively."""
    for op in operations:
        if op.id == op_id:
            return op
        for frame in op.case_frames:
            found = _find_operation(frame.operations, op_id)
            if found:
                return found
        found = _find_operation(op.inner_nodes, op_id)
        if found:
            return found
    return None


def _describe_case_structure(
    op: Operation, lines: list[str],
) -> None:
    """Describe a case structure in detail."""
    lines.append(f"Case Structure: {op.id}")
    if op.selector_terminal:
        lines.append(f"  Selector terminal: {op.selector_terminal}")

    # Find selector type from terminals
    for t in op.terminals:
        if t.id == op.selector_terminal and t.lv_type:
            lines.append(f"  Selector type: {t.lv_type.to_python()}")
            break

    lines.append(f"  Frames: {len(op.case_frames)}")
    for frame in op.case_frames:
        default = " (default)" if frame.is_default else ""
        lines.append(
            f"  Frame \"{frame.selector_value}\"{default}:"
            f" {len(frame.operations)} operations"
        )
        for fop in frame.operations:
            lines.append(f"    - {_describe_single_op(fop)}")


def _describe_loop(op: Operation, lines: list[str]) -> None:
    """Describe a loop in detail."""
    loop_kind = "While Loop" if op.loop_type == "whileLoop" else "For Loop"
    lines.append(f"{loop_kind}: {op.id}")

    if op.stop_condition_terminal:
        lines.append(
            f"  Stop condition: {op.stop_condition_terminal}"
        )

    # Tunnels
    if op.tunnels:
        lines.append("  Tunnels:")
        for tunnel in op.tunnels:
            lines.append(
                f"    {tunnel.tunnel_type}:"
                f" outer={tunnel.outer_terminal_uid}"
                f" → inner={tunnel.inner_terminal_uid}"
            )

    # Inner operations
    if op.inner_nodes:
        lines.append(f"  Body: {len(op.inner_nodes)} operations")
        for inner in op.inner_nodes:
            lines.append(f"    - {_describe_single_op(inner)}")


def _describe_sequence(op: Operation, lines: list[str]) -> None:
    """Describe a flat sequence."""
    lines.append(f"Flat Sequence: {op.id}")
    if op.case_frames:
        lines.append(f"  Frames: {len(op.case_frames)}")
        for i, frame in enumerate(op.case_frames):
            lines.append(
                f"  Frame {i}: {len(frame.operations)} operations"
            )
            for fop in frame.operations:
                lines.append(f"    - {_describe_single_op(fop)}")
    elif op.inner_nodes:
        lines.append(f"  Operations: {len(op.inner_nodes)}")
        for inner in op.inner_nodes:
            lines.append(f"    - {_describe_single_op(inner)}")
