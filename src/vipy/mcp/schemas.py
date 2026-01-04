"""JSON schemas for MCP tool outputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class GraphNodeSchema(BaseModel):
    """Schema for a node in the dataflow graph."""

    id: str
    label: str  # Human-readable label
    type: str  # "subvi", "primitive", "control", "indicator", "constant"
    name: str | None = None
    prim_id: int | None = None  # For primitives
    lv_type: str | None = None  # LabVIEW type (for controls/indicators)


class GraphEdgeSchema(BaseModel):
    """Schema for an edge in the dataflow graph."""

    from_node: str  # Terminal ID
    to_node: str  # Terminal ID
    from_label: str | None = None  # Human-readable source
    to_label: str | None = None  # Human-readable destination


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
    needs_review: list[str] = Field(default_factory=list)  # Files that need human review
