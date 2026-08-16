"""JSON schemas for MCP tool outputs and tool definitions.

DEAD CODE: nothing in ``src/lvkit`` or ``tests`` imports ``TOOL_DEFINITIONS``,
``get_tool_schema``, ``get_all_tool_schemas``, ``GeneratedFileSchema``, or
``CodeGenResult`` (verified 2026-08-15). They predate the current MCP tool
surface, which is defined by the ``@mcp.tool()``-decorated functions in
``server.py`` (``index``, ``query``, ``query_schema``, ``describe``,
``read_vi``, ``unresolved``) — that module, not this dict, is the source of
truth for what the server exposes. ``TOOL_DEFINITIONS`` below still lists
retired tools (``load``, ``list_loaded``, ``analyze``, ``generate_python``,
``generate_documents``, ``generate_ast_code``) under a stale MCP-1.x-style
schema shape and even a stale ``read_vi`` signature (``vi_name`` — the real
tool takes ``vi_path``). Do not read this file to learn the tool surface.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ===== Tool Definitions (UNUSED — see module docstring) =====

TOOL_DEFINITIONS = {
    "analyze": {
        "description": (
            "Analyze a LabVIEW VI file and return its structure"
            " (inputs, outputs, dataflow graph, dependencies)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vi_path": {
                    "type": "string",
                    "description": "Path to VI file (.vi) or block diagram XML",
                },
                "search_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Directories to search for SubVI dependencies",
                },
                "load_mode": {
                    "type": "string",
                    "enum": ["none", "minimal", "full"],
                    "description": (
                        "Dependency depth: 'minimal' (default; this VI + direct "
                        "SubVI connector panes + type fields), 'full' (whole "
                        "tree), or 'none' (this VI only)."
                    ),
                    "default": "minimal",
                },
            },
            "required": ["vi_path"],
        },
    },
    "generate_documents": {
        "description": "Generate static HTML docs for VIs, libraries, or directories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "library_path": {
                    "type": "string",
                    "description": "Path to .lvlib, .lvclass, .vi, or directory",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Output directory for HTML files",
                },
                "search_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Directories to search for dependencies",
                },
                "load_mode": {
                    "type": "string",
                    "enum": ["none", "minimal", "full"],
                    "description": (
                        "Dependency depth: 'full' (default; complete "
                        "cross-references), 'minimal', or 'none'."
                    ),
                    "default": "full",
                },
            },
            "required": ["library_path", "output_dir"],
        },
    },
    "generate_python": {
        "description": "Generate Python code from a VI using AST translation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vi_path": {"type": "string", "description": "Path to VI file (.vi)"},
                "output_dir": {
                    "type": "string",
                    "description": "Output directory for generated Python",
                },
                "search_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Directories to search for dependencies",
                },
            },
            "required": ["vi_path", "output_dir"],
        },
    },
    "load": {
        "description": "Load a VI into the in-memory graph. Persists across calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vi_path": {"type": "string", "description": "Path to VI file (.vi)"},
                "search_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Directories to search for SubVI dependencies",
                },
                "load_mode": {
                    "type": "string",
                    "enum": ["none", "minimal", "full"],
                    "description": (
                        "Dependency depth: 'minimal' (default), 'full' (needed "
                        "before codegen), or 'none'."
                    ),
                    "default": "minimal",
                },
            },
            "required": ["vi_path"],
        },
    },
    "list_loaded": {
        "description": "List all VIs currently loaded in the graph.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "read_vi": {
        "description": (
            "Read a VI in full: resolved primitives, terminals, and dataflow."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vi_name": {
                    "type": "string",
                    "description": "Name of the VI (e.g., 'Strip Path.vi')",
                },
            },
            "required": ["vi_name"],
        },
    },
    "generate_ast_code": {
        "description": "Generate Python from a loaded VI using AST translation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vi_name": {
                    "type": "string",
                    "description": "Name of the VI to generate code for",
                },
            },
            "required": ["vi_name"],
        },
    },
    "read_file": {
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to read",
                },
            },
            "required": ["file_path"],
        },
    },
    "write_file": {
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to write",
                },
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["file_path", "content"],
        },
    },
}


def get_tool_schema(name: str) -> dict[str, Any]:
    """Get a tool schema by name, formatted for Anthropic API."""
    defn = TOOL_DEFINITIONS.get(name)
    if not defn:
        raise ValueError(f"Unknown tool: {name}")
    return {"name": name, **defn}


def get_all_tool_schemas() -> list[dict[str, Any]]:
    """Get all tool schemas formatted for Anthropic API."""
    return [{"name": name, **defn} for name, defn in TOOL_DEFINITIONS.items()]


class GeneratedFileSchema(BaseModel):
    """Schema for a single generated Python file."""

    path: str  # Relative path within output directory
    vi_name: str  # Source VI name
    status: str  # "ok", "syntax_error", "generation_error"
    code: str | None = None  # Generated code (if requested)
    error: str | None = None  # Error message if status != "ok"
    source_type: str = "ast"  # "ast", "vilib", "stub"


class CodeGenResult(BaseModel):
    """Result of Python code generation."""

    success: bool
    output_dir: str
    package_name: str
    files: list[GeneratedFileSchema] = Field(default_factory=list)
    summary: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # For agent evaluation
    total_vis: int = 0
    successful: int = 0
    failed: int = 0
    needs_review: list[str] = Field(
        default_factory=list
    )  # Files that need human review
