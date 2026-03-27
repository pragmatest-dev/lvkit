"""JSON schemas for MCP tool outputs and tool definitions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ===== Tool Definitions (shared by MCP server and Claude agent) =====

TOOL_DEFINITIONS = {
    "analyze_vi": {
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
                "expand_subvis": {
                    "type": "boolean",
                    "description": "Load all SubVI dependencies recursively",
                    "default": True,
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
                "expand_subvis": {
                    "type": "boolean",
                    "description": "Load SubVI dependencies",
                    "default": True,
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
    "load_vi": {
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
                "expand_subvis": {
                    "type": "boolean",
                    "description": "Load all SubVI dependencies recursively",
                    "default": True,
                },
            },
            "required": ["vi_path"],
        },
    },
    "list_loaded_vis": {
        "description": "List all VIs currently loaded in the graph.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "get_vi_context": {
        "description": (
            "Get the full context for a loaded VI including"
            " resolved primitives, terminals, and dataflow."
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


class ControlSchema(BaseModel):
    """Schema for VI control (input)."""

    name: str
    type: str
    default_value: Any = None
    description: str = ""
    slot_index: int


class IndicatorSchema(BaseModel):
    """Schema for VI indicator (output)."""

    name: str
    type: str
    description: str = ""
    slot_index: int


class VIAnalysisResult(BaseModel):
    """Complete VI analysis result."""

    vi_name: str
    summary: str = ""
    controls: list[ControlSchema] = Field(default_factory=list)
    indicators: list[IndicatorSchema] = Field(default_factory=list)
    graph: dict[str, Any] = Field(default_factory=dict)
    dependencies: dict[str, str] = Field(default_factory=dict)
    execution_order: list[str] = Field(default_factory=list)


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
