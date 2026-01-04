"""MCP server for VI analysis tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .tools import analyze_vi, generate_documents, generate_python


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
                "4. Dataflow diagram: Draw an ASCII art diagram showing the visual block diagram. "
                "Show left-to-right flow from inputs → operations → outputs using boxes, arrows, and lines. "
                "Use graph.operations and graph.data_flow to create the visualization. "
                "CRITICAL: Render an actual visual diagram with ASCII characters - do NOT show Mermaid code, "
                "JSON, or any other source code. The user needs to SEE the dataflow, not read markup.\n"
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
                    "expand_subvis": {
                        "type": "boolean",
                        "description": "Load all SubVI dependencies (slower, complete) or just this VI (faster, limited)",
                        "default": True,
                    },
                },
                "required": ["vi_path"],
            },
        ),
        Tool(
            name="generate_documents",
            description=(
                "Generate static HTML documentation for LabVIEW VIs, libraries, classes, or directories. "
                "Creates a complete static website with individual pages for each VI, cross-references, "
                "and a table of contents.\n\n"
                "Each VI page includes:\n"
                "- Summary and signature (inputs/outputs)\n"
                "- Detailed parameter tables\n"
                "- Visual dataflow diagram\n"
                "- Dependencies (called SubVIs) with links\n"
                "- Reverse links (VIs that call this one)\n\n"
                "The output is a self-contained static HTML site with embedded CSS, "
                "suitable for browsing locally or hosting on a web server.\n\n"
                "IMPORTANT: This tool generates files directly and returns a summary. "
                "You should inform the user where the documentation was generated and provide the path to index.html."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "library_path": {
                        "type": "string",
                        "description": "Path to .lvlib file, .lvclass file, .vi file, or directory containing VIs",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for HTML documentation files",
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of search paths for resolving dependencies",
                        "default": [],
                    },
                    "expand_subvis": {
                        "type": "boolean",
                        "description": "Load SubVI dependencies for complete cross-references (slower) or just library VIs (faster)",
                        "default": True,
                    },
                },
                "required": ["library_path", "output_dir"],
            },
        ),
        Tool(
            name="generate_python",
            description=(
                "Generate Python code from a LabVIEW VI using AST-based translation.\n\n"
                "This tool converts VI dataflow logic to executable Python code. "
                "It handles SubVI dependencies, primitives, and control/indicator types.\n\n"
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
                        "description": "Path to VI file (.vi) or block diagram XML (*_BDHb.xml)",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for generated Python package",
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Search paths for VI dependencies (e.g., OpenG libraries)",
                        "default": [],
                    },
                },
                "required": ["vi_path", "output_dir"],
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
        result = await asyncio.to_thread(analyze_vi, vi_path, search_paths, expand_subvis)

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
