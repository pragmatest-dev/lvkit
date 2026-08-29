"""Pydantic data models for vilib/openg/drivers VI catalog entries."""

from __future__ import annotations

from pydantic import BaseModel, Field

from lvkit.primitive_resolver import NodeIcon


class VITerminal(BaseModel):
    """A terminal on a vilib VI."""

    name: str = ""
    index: int | None = None
    direction: str | None = None  # None = unknown, must come from observation
    type: str | None = None
    enum: str | None = None
    enum_values: list[tuple[int, str]] | None = None
    python_param: str | None = None
    default_value: str | None = None


class VIEntry(BaseModel):
    """A vilib/openg VI entry from JSON."""

    name: str = ""
    vi_path: str | None = None
    category: str | None = None
    description: str | None = None
    terminals: list[VITerminal] = Field(default_factory=list)
    python: str = ""
    python_code: str | None = None
    inline: bool = False
    imports: list[str] = Field(default_factory=list)
    status: str = "needs_review"
    # Public NI docs URL (labview-api-ref bundle). Replaces the old `page` PDF
    # ref (task #87) — devs don't have the licensed PDF. Absent when no
    # authoritative NI Functions page exists for this VI (category headers,
    # a few edge math funcs, ambiguous polymorphic channel ops).
    doc_url: str | None = None
    # Polymorphic variant support
    variant_signature: str | None = None  # Signature key for this variant
    is_variant: bool = False  # True if this is a variant entry
    # Reference terminal passthrough (output_param -> "passthrough_from:input_param")
    ref_terminals: dict[str, str] | None = None
    # Alternate names for matching (e.g., polymorphic instance names)
    match_names: list[str] = Field(default_factory=list)
    # polySelector dropdown names from VI XML (exact strings)
    poly_selector_names: list[str] = Field(default_factory=list)
    # Wrapper VI name for polymorphic variants (explicit, not derived)
    base_vi: str | None = None
    # Optional declarative render glyph (render/nodes.py::JsonGlyphResolver).
    # Absent on every existing entry — loader tolerates its absence.
    icon: NodeIcon | None = None
