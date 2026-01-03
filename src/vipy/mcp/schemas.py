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
