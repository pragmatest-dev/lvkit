"""MCP server for VI analysis tools.

Supports two modes:
1. Stateless tools (analyze_vi, generate_documents, generate_python) - subprocess-based
2. Stateful graph tools (load_vi, get_vi_context, etc.) - graph persists across calls
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .tools import analyze_vi, generate_documents, generate_python

# Create MCP server instance
app = Server("vipy-mcp")

# Stateful graph - persists across tool calls in the session
_graph = None


def _get_graph():
    """Get or create the in-memory graph."""
    global _graph
    if _graph is None:
        from ..memory_graph import InMemoryVIGraph

        _graph = InMemoryVIGraph()
    return _graph


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="analyze_vi",
            description=(
                "Analyze a LabVIEW VI file and describe what it does. "
                "Returns JSON with VI structure "
                "(inputs, outputs, dataflow graph, dependencies). "
                "\n\n"
                "IMPORTANT: Present the results visually and descriptively:\n"
                "1. Summary: 1-2 sentences describing what the VI does\n"
                "2. Controls table: Input parameters (name, type, default, desc)\n"
                "3. Indicators table: Output parameters with name, type, description\n"
                "4. Dataflow diagram: Visual flowchart of the block diagram dataflow. "
                "Show left-to-right flow from inputs → operations → outputs. "
                "Use graph.operations and graph.data_flow to build the visualization. "
                "Prefer Mermaid flowchart format, but render appropriately for your "
                "environment: if rendered diagrams are unsupported (e.g., terminal), "
                "draw ASCII art instead. "
                "CRITICAL: The user must SEE the dataflow visually - do NOT dump raw "
                "JSON or show unrendered Mermaid code as plain text.\n"
                "5. Dependencies: List SubVIs called with 1-sentence descriptions\n"
                "6. How it works: Step-by-step breakdown using execution_order\n"
                "\n"
                "Focus on a clear, visual block diagram - "
                "LabVIEW is a visual dataflow language!"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_path": {
                        "type": "string",
                        "description": (
                            "Path to VI file (.vi) or block diagram XML (*_BDHb.xml)"
                        ),
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of search paths for dependencies",
                        "default": [],
                    },
                    "expand_subvis": {
                        "type": "boolean",
                        "description": (
                            "Load all SubVI dependencies (slower, complete) "
                            "or just this VI (faster, limited)"
                        ),
                        "default": True,
                    },
                },
                "required": ["vi_path"],
            },
        ),
        Tool(
            name="generate_documents",
            description=(
                "Generate static HTML documentation for LabVIEW VIs, libraries, "
                "classes, or directories. "
                "Creates a complete static website with individual pages for each VI, "
                "cross-references, and a table of contents.\n\n"
                "Each VI page includes:\n"
                "- Summary and signature (inputs/outputs)\n"
                "- Detailed parameter tables\n"
                "- Visual dataflow diagram\n"
                "- Dependencies (called SubVIs) with links\n"
                "- Reverse links (VIs that call this one)\n\n"
                "The output is a self-contained static HTML site with embedded CSS, "
                "suitable for browsing locally or hosting on a web server.\n\n"
                "IMPORTANT: This tool generates files directly and returns a summary. "
                "Inform the user where the docs were generated and provide the path "
                "to index.html."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "library_path": {
                        "type": "string",
                        "description": (
                            "Path to .lvlib file, .lvclass file, .vi file, "
                            "or directory containing VIs"
                        ),
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for HTML documentation files",
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of search paths for resolving dependencies"
                        ),
                        "default": [],
                    },
                    "expand_subvis": {
                        "type": "boolean",
                        "description": (
                            "Load SubVI dependencies for complete cross-references "
                            "(slower) or just library VIs (faster)"
                        ),
                        "default": True,
                    },
                },
                "required": ["library_path", "output_dir"],
            },
        ),
        Tool(
            name="generate_python",
            description=(
                "Generate Python code from a LabVIEW VI using AST-based "
                "translation.\n\n"
                "This tool converts VI dataflow logic to executable Python code. "
                "It handles SubVI dependencies, primitives, and control/indicator "
                "types.\n\n"
                "OUTPUT REVIEW WORKFLOW:\n"
                "1. Files are written to output_dir/<package_name>/\n"
                "2. Response includes list of generated files with status (ok/error)\n"
                "3. 'needs_review' list shows files requiring agent attention\n"
                "4. 'errors' list shows specific problems to fix\n"
                "5. Agent should READ the generated files and CORRECT any issues\n\n"
                "COMMON ISSUES TO FIX:\n"
                "- Missing dependencies: Add correct search_paths or implement stubs\n"
                "- Syntax errors: Review and fix the generated code\n"
                "- Stub functions: Implement missing VI logic\n\n"
                "The agent receiving this output should:\n"
                "1. Check if success=true\n"
                "2. If not, read files in 'needs_review' to understand problems\n"
                "3. Fix issues by editing the generated files\n"
                "4. Re-run if search paths were missing"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_path": {
                        "type": "string",
                        "description": (
                            "Path to VI file (.vi) or block diagram XML (*_BDHb.xml)"
                        ),
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for generated Python package",
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Search paths for VI dependencies (e.g., OpenG libraries)"
                        ),
                        "default": [],
                    },
                },
                "required": ["vi_path", "output_dir"],
            },
        ),
        # ===== Stateful Graph Tools =====
        Tool(
            name="load_vi",
            description=(
                "Load a VI into the in-memory graph. "
                "The graph persists across tool calls.\n\n"
                "Use this to load VIs before querying them with get_vi_context, "
                "get_primitive_info, or generate_ast_code.\n\n"
                "Returns list of loaded VIs "
                "(includes dependencies if expand_subvis=true)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_path": {
                        "type": "string",
                        "description": "Path to VI file (.vi)",
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Directories to search for SubVI dependencies",
                        "default": [],
                    },
                    "expand_subvis": {
                        "type": "boolean",
                        "description": "Load all SubVI dependencies recursively",
                        "default": True,
                    },
                },
                "required": ["vi_path"],
            },
        ),
        Tool(
            name="list_loaded_vis",
            description="List all VIs currently loaded in the graph.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_vi_context",
            description=(
                "Get the full context for a loaded VI including inputs, outputs, "
                "operations, wires, and constants. Use after load_vi."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_name": {
                        "type": "string",
                        "description": "Name of the VI (e.g., 'Strip Path.vi')",
                    },
                },
                "required": ["vi_name"],
            },
        ),
        Tool(
            name="generate_ast_code",
            description=(
                "Generate Python code from a loaded VI using deterministic "
                "AST translation.\n\n"
                "Always produces valid Python syntax. May have PRIMITIVE_xxx stubs for "
                "unknown primitives that need manual implementation.\n\n"
                "Use after load_vi to generate code for a specific VI."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_name": {
                        "type": "string",
                        "description": "Name of the VI to generate code for",
                    },
                },
                "required": ["vi_name"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    if name == "analyze_vi":
        vi_path = arguments.get("vi_path")
        search_paths = arguments.get("search_paths", [])
        expand_subvis = arguments.get("expand_subvis", True)

        if not vi_path:
            raise ValueError("vi_path is required")

        # Run analysis (synchronous function in async context)
        result = await asyncio.to_thread(
            analyze_vi, vi_path, search_paths, expand_subvis
        )

        # Convert to JSON
        result_json = result.model_dump_json(indent=2)

        return [TextContent(type="text", text=result_json)]

    elif name == "generate_documents":
        library_path = arguments.get("library_path")
        output_dir = arguments.get("output_dir")
        search_paths = arguments.get("search_paths", [])
        expand_subvis = arguments.get("expand_subvis", True)

        if not library_path:
            raise ValueError("library_path is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        # Run documentation generation (synchronous function in async context)
        result = await asyncio.to_thread(
            generate_documents, library_path, output_dir, search_paths, expand_subvis
        )

        return [TextContent(type="text", text=result)]

    elif name == "generate_python":
        vi_path = arguments.get("vi_path")
        output_dir = arguments.get("output_dir")
        search_paths = arguments.get("search_paths", [])

        if not vi_path:
            raise ValueError("vi_path is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        # Run code generation (synchronous function in async context)
        result = await asyncio.to_thread(
            generate_python, vi_path, output_dir, search_paths
        )

        # Return JSON for structured parsing by agent
        result_json = result.model_dump_json(indent=2)
        return [TextContent(type="text", text=result_json)]

    # ===== Stateful Graph Tools =====

    elif name == "load_vi":
        vi_path = arguments.get("vi_path")
        search_paths = arguments.get("search_paths", [])
        expand_subvis = arguments.get("expand_subvis", True)

        if not vi_path:
            raise ValueError("vi_path is required")

        def _load():
            graph = _get_graph()
            search_path_objs = [Path(p) for p in search_paths] if search_paths else None
            graph.load_vi(
                Path(vi_path),
                expand_subvis=expand_subvis,
                search_paths=search_path_objs,
            )
            return list(graph.list_vis())

        loaded = await asyncio.to_thread(_load)
        return [
            TextContent(type="text", text=json.dumps({"loaded_vis": loaded}, indent=2))
        ]

    elif name == "list_loaded_vis":
        graph = _get_graph()
        vis = list(graph.list_vis())
        return [
            TextContent(type="text", text=json.dumps({"loaded_vis": vis}, indent=2))
        ]

    elif name == "get_vi_context":
        vi_name = arguments.get("vi_name")
        if not vi_name:
            raise ValueError("vi_name is required")

        graph = _get_graph()
        context = graph.get_vi_context(vi_name)
        if not context.inputs and not context.outputs and not context.operations:
            return [TextContent(type="text", text=f"VI not found: {vi_name}")]

        # Serialize with dataclass and Pydantic support
        from pydantic import BaseModel

        def _serialize(obj):
            if isinstance(obj, BaseModel):
                return obj.model_dump()
            elif is_dataclass(obj) and not isinstance(obj, type):
                return {
                    f.name: _serialize(getattr(obj, f.name))
                    for f in fields(obj)
                }
            elif isinstance(obj, list):
                return [_serialize(x) for x in obj]
            elif isinstance(obj, dict):
                return {k: _serialize(v) for k, v in obj.items()}
            return obj

        serialized = _serialize(context)
        return [
            TextContent(type="text", text=json.dumps(serialized, indent=2, default=str))
        ]

    elif name == "generate_ast_code":
        vi_name = arguments.get("vi_name")
        if not vi_name:
            raise ValueError("vi_name is required")

        graph = _get_graph()
        context = graph.get_vi_context(vi_name)
        if not context.inputs and not context.outputs and not context.operations:
            return [TextContent(type="text", text=f"VI not found: {vi_name}")]

        def _generate():
            from ..agent.codegen import build_module

            return build_module(context, vi_name)

        try:
            code = await asyncio.to_thread(_generate)
            return [TextContent(type="text", text=code)]
        except Exception as e:
            return [TextContent(type="text", text=f"AST generation failed: {e}")]

    else:
        raise ValueError(f"Unknown tool: {name}")


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
