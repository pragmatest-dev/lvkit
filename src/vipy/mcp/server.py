"""MCP server for VI analysis tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .tools import analyze_vi


# Create MCP server instance
app = Server("vipy-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="analyze_vi",
            description=(
                "Analyze a LabVIEW VI file and describe what it does. "
                "Returns JSON with VI structure (inputs, outputs, dataflow graph, dependencies). "
                "\n\n"
                "IMPORTANT: Present the results visually and descriptively:\n"
                "1. Summary: 1-2 sentences describing what the VI does\n"
                "2. Controls table: Input parameters with name, type, default value, description\n"
                "3. Indicators table: Output parameters with name, type, description\n"
                "4. Dataflow diagram: VISUALIZE the block diagram using the best format available "
                "(ASCII art, Mermaid, HTML, SVG, etc.). Show left-to-right flow from inputs → operations → outputs. "
                "Use graph.nodes and graph.edges to create the visualization. Do NOT show raw JSON.\n"
                "5. Dependencies: List SubVIs called with 1-sentence descriptions\n"
                "6. How it works: Detailed step-by-step breakdown using execution_order\n"
                "\n"
                "Focus on creating a clear, visual block diagram - LabVIEW is a visual dataflow language!"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_path": {
                        "type": "string",
                        "description": "Path to VI file (.vi) or block diagram XML (*_BDHb.xml)",
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of search paths for dependencies",
                        "default": [],
                    },
                },
                "required": ["vi_path"],
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    if name != "analyze_vi":
        raise ValueError(f"Unknown tool: {name}")

    vi_path = arguments.get("vi_path")
    search_paths = arguments.get("search_paths", [])

    if not vi_path:
        raise ValueError("vi_path is required")

    # Run analysis (synchronous function in async context)
    result = await asyncio.to_thread(analyze_vi, vi_path, search_paths)

    # Convert to JSON
    result_json = result.model_dump_json(indent=2)

    return [TextContent(type="text", text=result_json)]


async def async_main() -> None:
    """Run the MCP server via stdio (async)."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def main() -> None:
    """Run the MCP server via stdio (entry point)."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
