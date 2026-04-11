"""MCP server for VI analysis tools.

Supports two modes:
1. Stateless tools (analyze, generate_documents, generate_python) - subprocess-based
2. Stateful graph tools (load, get_context, etc.) - graph persists across calls
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .. import primitive_resolver, vilib_resolver
from ..codegen import build_module
from ..graph import InMemoryVIGraph
from ..graph.describe import (
    describe_constants as describe_constants_text,
)
from ..graph.describe import (
    describe_dataflow as describe_dataflow_text,
)
from ..graph.describe import (
    describe_operations as describe_operations_text,
)
from ..graph.describe import (
    describe_structure as describe_structure_text,
)
from ..graph.describe import (
    describe_vi as describe_vi_text,
)
from ..project_store import find_project_store
from .tools import analyze_vi, generate_documents, generate_python


def _configure_resolvers_for_vi(vi_path: str | Path) -> None:
    """Discover .lvpy/ from a VI path and reset resolvers.

    MCP may serve multiple projects in one session, so we re-resolve the
    project store on every tool call that knows a target VI path.

    The path may be a file (a .vi), a directory (an .lvlib, .lvclass, or a
    folder of VIs), or a path that doesn't exist yet. We start the search
    from the path itself when it's a directory and from its parent when
    it's a file, then walk up looking for .lvpy/.
    """
    p = Path(vi_path).resolve()
    start = p if p.is_dir() else p.parent
    store = find_project_store(start=start)
    primitive_resolver.reset_resolver(project_data_dir=store)
    vilib_resolver.reset_resolver(project_data_dir=store)

# Create MCP server instance
app = Server("lvpy-mcp")

# Stateful graph - persists across tool calls in the session
_graph = None


def _get_graph():
    """Get or create the in-memory graph."""
    global _graph
    if _graph is None:
        _graph = InMemoryVIGraph()
    return _graph


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="analyze",
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
                    "soft_unresolved": {
                        "type": "boolean",
                        "description": (
                            "If true, unknown primitives / vi.lib VIs are "
                            "emitted as inline `raise PrimitiveResolutionNeeded(...)` "
                            "/ `raise VILibResolutionNeeded(...)` statements "
                            "instead of failing the build. Lets a downstream "
                            "LLM see the diagnostic in context and either "
                            "write a mapping into .lvpy/ or replace the "
                            "raise with a contextual fix."
                        ),
                        "default": False,
                    },
                },
                "required": ["vi_path", "output_dir"],
            },
        ),
        # ===== Stateful Graph Tools =====
        Tool(
            name="load",
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
            name="list_loaded",
            description="List all VIs currently loaded in the graph.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_context",
            description=(
                "Get the full context for a loaded VI including inputs, outputs, "
                "operations, wires, and constants. Use after load."
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
                "Use after load to generate code for a specific VI."
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
        # ===== Graph Exploration Tools (LLM-readable) =====
        Tool(
            name="describe",
            description=(
                "Describe a loaded VI's purpose, signature, and structure "
                "in human-readable text.\n\n"
                "Shows: function signature, inputs/outputs with types, "
                "SubVI calls with descriptions, control flow structures, "
                "and key statistics.\n\n"
                "Use this first to understand what a VI does before diving "
                "into operations or dataflow."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_name": {
                        "type": "string",
                        "description": "Name of the VI",
                    },
                },
                "required": ["vi_name"],
            },
        ),
        Tool(
            name="get_operations",
            description=(
                "Get the operations (execution steps) of a loaded VI "
                "in human-readable format.\n\n"
                "Shows operations in execution order with nested structures "
                "(case frames, loop bodies). Each operation shows what it "
                "does, its inputs/outputs, and primitive ID if applicable.\n\n"
                "Use after describe to understand the detailed logic."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_name": {
                        "type": "string",
                        "description": "Name of the VI",
                    },
                },
                "required": ["vi_name"],
            },
        ),
        Tool(
            name="get_dataflow",
            description=(
                "Show data flow (wire connections) for a loaded VI.\n\n"
                "Shows which operations feed data to which other operations. "
                "Optionally filter to show only connections for a specific "
                "operation.\n\n"
                "Use to trace how values flow through the VI."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_name": {
                        "type": "string",
                        "description": "Name of the VI",
                    },
                    "operation_id": {
                        "type": "string",
                        "description": (
                            "Optional: filter to show only wires connected "
                            "to this operation"
                        ),
                    },
                },
                "required": ["vi_name"],
            },
        ),
        Tool(
            name="get_structure",
            description=(
                "Get detailed information about a structure node "
                "(case, loop, or sequence).\n\n"
                "Shows: selector type and values for cases, "
                "tunnel connections for loops, frame contents, "
                "and inner operations.\n\n"
                "Use when you need to understand a specific "
                "control flow structure."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_name": {
                        "type": "string",
                        "description": "Name of the VI",
                    },
                    "operation_id": {
                        "type": "string",
                        "description": "ID of the structure operation",
                    },
                },
                "required": ["vi_name", "operation_id"],
            },
        ),
        Tool(
            name="get_constants",
            description=(
                "List all constant values used in a loaded VI.\n\n"
                "Shows each constant's name, type, and value."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vi_name": {
                        "type": "string",
                        "description": "Name of the VI",
                    },
                },
                "required": ["vi_name"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    if name == "analyze":
        vi_path = arguments.get("vi_path")
        search_paths = arguments.get("search_paths", [])
        expand_subvis = arguments.get("expand_subvis", True)

        if not vi_path:
            raise ValueError("vi_path is required")

        _configure_resolvers_for_vi(vi_path)

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

        _configure_resolvers_for_vi(library_path)

        # Run documentation generation (synchronous function in async context)
        result = await asyncio.to_thread(
            generate_documents, library_path, output_dir, search_paths, expand_subvis
        )

        return [TextContent(type="text", text=result)]

    elif name == "generate_python":
        vi_path = arguments.get("vi_path")
        output_dir = arguments.get("output_dir")
        search_paths = arguments.get("search_paths", [])
        soft_unresolved = arguments.get("soft_unresolved", False)

        if not vi_path:
            raise ValueError("vi_path is required")
        if not output_dir:
            raise ValueError("output_dir is required")

        _configure_resolvers_for_vi(vi_path)

        # Run code generation (synchronous function in async context)
        result = await asyncio.to_thread(
            generate_python, vi_path, output_dir, search_paths,
            include_code=False, soft_unresolved=soft_unresolved,
        )

        # Return JSON for structured parsing by agent
        result_json = result.model_dump_json(indent=2)
        return [TextContent(type="text", text=result_json)]

    # ===== Stateful Graph Tools =====

    elif name == "load":
        vi_path = arguments.get("vi_path")
        search_paths = arguments.get("search_paths", [])
        expand_subvis = arguments.get("expand_subvis", True)

        if not vi_path:
            raise ValueError("vi_path is required")

        _configure_resolvers_for_vi(vi_path)

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

    elif name == "list_loaded":
        graph = _get_graph()
        vis = list(graph.list_vis())
        return [
            TextContent(type="text", text=json.dumps({"loaded_vis": vis}, indent=2))
        ]

    elif name == "get_context":
        vi_name = arguments.get("vi_name")
        if not vi_name:
            raise ValueError("vi_name is required")

        graph = _get_graph()
        context = graph.get_vi_context(vi_name)
        if not context.inputs and not context.outputs and not context.operations:
            return [TextContent(type="text", text=f"VI not found: {vi_name}")]

        # VIContext is Pydantic — model_dump() handles nested Pydantic types.
        # LVType (dataclass) instances are serialized via default=str fallback
        # until LVType migrates to Pydantic.
        serialized = context.model_dump()
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
            return build_module(context, vi_name)

        try:
            code = await asyncio.to_thread(_generate)
            return [TextContent(type="text", text=code)]
        except Exception as e:
            return [TextContent(type="text", text=f"AST generation failed: {e}")]

    # ===== Graph Exploration Tools =====

    elif name == "describe":
        vi_name = arguments.get("vi_name")
        if not vi_name:
            raise ValueError("vi_name is required")

        graph = _get_graph()
        text = describe_vi_text(graph, vi_name)
        return [TextContent(type="text", text=text)]

    elif name == "get_operations":
        vi_name = arguments.get("vi_name")
        if not vi_name:
            raise ValueError("vi_name is required")

        graph = _get_graph()
        text = describe_operations_text(graph, vi_name)
        return [TextContent(type="text", text=text)]

    elif name == "get_dataflow":
        vi_name = arguments.get("vi_name")
        operation_id = arguments.get("operation_id")
        if not vi_name:
            raise ValueError("vi_name is required")

        graph = _get_graph()
        text = describe_dataflow_text(graph, vi_name, operation_id)
        return [TextContent(type="text", text=text)]

    elif name == "get_structure":
        vi_name = arguments.get("vi_name")
        operation_id = arguments.get("operation_id")
        if not vi_name:
            raise ValueError("vi_name is required")
        if not operation_id:
            raise ValueError("operation_id is required")

        graph = _get_graph()
        text = describe_structure_text(graph, vi_name, operation_id)
        return [TextContent(type="text", text=text)]

    elif name == "get_constants":
        vi_name = arguments.get("vi_name")
        if not vi_name:
            raise ValueError("vi_name is required")

        graph = _get_graph()
        text = describe_constants_text(graph, vi_name)
        return [TextContent(type="text", text=text)]

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
