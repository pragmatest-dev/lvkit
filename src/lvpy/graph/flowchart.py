"""Generate Mermaid flowcharts from the dataflow graph.

Produces structured flowcharts that show execution order,
case frames, loops, and data flow — closer to how LabVIEW
block diagrams actually look.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import (
    CaseOperation,
    LoopOperation,
    Operation,
    PrimitiveOperation,
    SequenceOperation,
)
from .models import Constant, VIContext

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def flowchart(graph: InMemoryVIGraph, vi_name: str) -> str:
    """Generate a Mermaid flowchart for a VI's dataflow."""
    vi_name = graph.resolve_vi_name(vi_name)
    ctx = graph.get_vi_context(vi_name)

    lines = ["flowchart LR"]

    # Inputs
    for inp in ctx.inputs:
        if inp.is_error_cluster:
            continue
        safe_id = _safe_id(inp.id)
        label = f"{inp.name}: {inp.python_type()}"
        lines.append(f'  {safe_id}[/"{label}"/]')
        lines.append(f"  style {safe_id} fill:#E8F5E9,stroke:#4CAF50")

    # Outputs
    for out in ctx.outputs:
        if out.is_error_cluster:
            continue
        safe_id = _safe_id(out.id)
        label = f"{out.name}: {out.python_type()}"
        lines.append(f'  {safe_id}[\\"{label}"\\]')
        lines.append(f"  style {safe_id} fill:#E8F5E9,stroke:#4CAF50")

    # Constants
    for c in ctx.constants:
        safe_id = _safe_id(c.id)
        val_str = _format_constant(c)
        lines.append(f'  {safe_id}(["{val_str}"])')
        lines.append(f"  style {safe_id} fill:#F3E5F5,stroke:#9C27B0")

    # Operations
    _render_operations(ctx.operations, ctx.constants, lines, indent=1)

    # Wires
    _render_wires(ctx, lines)

    return "\n".join(lines)


def flowchart_html(graph: InMemoryVIGraph, vi_name: str) -> str:
    """Generate a self-contained HTML page with the Mermaid flowchart."""
    mermaid = flowchart(graph, vi_name)
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{vi_name} — Flowchart</title>
  <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
  <style>
    body {{ font-family: arial; margin: 20px; background: #fafafa; }}
    h1 {{ font-size: 18px; color: #333; }}
    .mermaid {{ background: white; padding: 20px; border-radius: 8px;
               box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  </style>
</head>
<body>
  <h1>{vi_name}</h1>
  <div class="mermaid">
{mermaid}
  </div>
  <script>mermaid.initialize({{startOnLoad:true, theme:'neutral'}});</script>
</body>
</html>"""


def _format_constant(c: Constant) -> str:
    """Format a constant value for display."""
    val = str(c.value) if c.value is not None else "?"
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    if len(val) > 20:
        val = val[:17] + "..."
    return val


def _safe_id(raw: str) -> str:
    """Convert a node/terminal ID to a valid Mermaid ID."""
    return raw.replace("::", "_").replace(".", "_").replace(
        " ", "_"
    ).replace("-", "_").replace("(", "").replace(")", "").replace(
        "/", "_"
    ).replace("\\", "_")


def _render_operations(
    operations: list[Operation],
    constants: list[Constant],
    lines: list[str],
    indent: int,
) -> None:
    """Render operations as Mermaid nodes."""
    prefix = "  " * indent

    for op in operations:
        safe_id = _safe_id(op.id)

        match op:
            case SequenceOperation() if op.frames:
                lines.append(f"{prefix}subgraph {safe_id}[Sequence]")
                for i, frame in enumerate(op.frames):
                    frame_id = f"{safe_id}_s{i}"
                    lines.append(
                        f"{prefix}  subgraph {frame_id}[Frame {i}]"
                    )
                    if frame.operations:
                        _render_operations(
                            frame.operations, constants, lines,
                            indent + 2,
                        )
                    lines.append(f"{prefix}  end")
                lines.append(f"{prefix}end")
                lines.append(
                    f"  style {safe_id} fill:#FFF3E0,stroke:#FF9800"
                )

            case CaseOperation() if op.frames:
                label = "Case Structure"
                lines.append(f"{prefix}subgraph {safe_id}[{label}]")
                for frame in op.frames:
                    frame_id = f"{safe_id}_f{_safe_id(str(frame.selector_value))}"
                    default = " *" if frame.is_default else ""
                    frame_label = f"Frame: {frame.selector_value}{default}"
                    lines.append(f"{prefix}  subgraph {frame_id}[{frame_label}]")
                    if frame.operations:
                        _render_operations(
                            frame.operations, constants, lines,
                            indent + 2,
                        )
                    else:
                        empty_id = f"{frame_id}_empty"
                        lines.append(f'{prefix}    {empty_id}["(empty)"]')
                        lines.append(
                            f"  style {empty_id} fill:#f5f5f5,stroke:#ccc"
                        )
                    lines.append(f"{prefix}  end")
                lines.append(f"{prefix}end")
                lines.append(
                    f"  style {safe_id} fill:#FFF3E0,stroke:#FF9800"
                )

            case LoopOperation():
                loop_label = (
                    "While Loop" if op.loop_type == "whileLoop"
                    else "For Loop"
                )
                lines.append(f"{prefix}subgraph {safe_id}[{loop_label}]")
                if op.inner_nodes:
                    _render_operations(
                        op.inner_nodes, constants, lines, indent + 1,
                    )
                lines.append(f"{prefix}end")
                lines.append(
                    f"  style {safe_id} fill:#FFF3E0,stroke:#FF9800"
                )

            case _ if "SubVI" in op.labels:
                label = (op.name or "SubVI").replace(".vi", "")
                lines.append(f'{prefix}{safe_id}["{label}"]')
                lines.append(
                    f"  style {safe_id} fill:#E8F5E9,stroke:#4CAF50"
                )

            case PrimitiveOperation():
                label = op.name or f"prim {op.primResID}"
                lines.append(f'{prefix}{safe_id}["{label}"]')
                lines.append(
                    f"  style {safe_id} fill:#E3F2FD,stroke:#2196F3"
                )

            case _:
                label = op.name or op.node_type or "?"
                lines.append(f'{prefix}{safe_id}["{label}"]')


def _render_wires(ctx: VIContext, lines: list[str]) -> None:
    """Render dataflow connections."""
    rendered: set[str] = set()
    _collect_op_ids(ctx.operations, rendered)
    for c in ctx.constants:
        rendered.add(c.id)

    structure_ids: set[str] = set()
    _collect_structure_ids(ctx.operations, structure_ids)

    seen_edges: set[str] = set()

    for wire in ctx.data_flow:
        src_node = wire.source.node_id
        dst_node = wire.dest.node_id

        if src_node == dst_node:
            continue
        if src_node in structure_ids and dst_node in structure_ids:
            continue
        if src_node not in rendered or dst_node not in rendered:
            continue

        src_safe = _safe_id(src_node)
        dst_safe = _safe_id(dst_node)
        edge_key = f"{src_safe}-->{dst_safe}"

        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            lines.append(f"  {src_safe} --> {dst_safe}")

    # Sequential edges between sequence frames
    for op in ctx.operations:
        match op:
            case SequenceOperation() if op.frames:
                for i in range(len(op.frames) - 1):
                    f1 = op.frames[i]
                    f2 = op.frames[i + 1]
                    if f1.operations and f2.operations:
                        last_op = f1.operations[-1]
                        first_op = f2.operations[0]
                        src_safe = _safe_id(last_op.id)
                        dst_safe = _safe_id(first_op.id)
                        edge_key = f"{src_safe}-->{dst_safe}"
                        if edge_key not in seen_edges:
                            seen_edges.add(edge_key)
                            lines.append(
                                f"  {src_safe} -.-> {dst_safe}"
                            )


def _collect_op_ids(
    operations: list[Operation], ids: set[str],
) -> None:
    """Collect all operation IDs recursively."""
    for op in operations:
        ids.add(op.id)
        match op:
            case CaseOperation() | SequenceOperation():
                for frame in op.frames:
                    _collect_op_ids(frame.operations, ids)
            case _:
                pass
        _collect_op_ids(op.inner_nodes, ids)


def _collect_structure_ids(
    operations: list[Operation], ids: set[str],
) -> None:
    """Collect structure node IDs."""
    for op in operations:
        match op:
            case CaseOperation() | SequenceOperation():
                ids.add(op.id)
                for frame in op.frames:
                    _collect_structure_ids(frame.operations, ids)
            case LoopOperation():
                ids.add(op.id)
            case _:
                pass
        _collect_structure_ids(op.inner_nodes, ids)
