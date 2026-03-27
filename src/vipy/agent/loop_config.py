"""Configuration and result dataclasses for conversion agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..llm import LLMConfig

if TYPE_CHECKING:
    pass

# Type alias for graph - accepts either Neo4j or in-memory graph
GraphType = "VIGraph | InMemoryVIGraph"


@dataclass
class ConversionConfig:
    """Configuration for the conversion agent."""

    output_dir: Path
    max_retries: int = 3
    generate_ui: bool = False  # Generate NiceGUI wrappers
    llm_config: LLMConfig = field(default_factory=LLMConfig)

    # Validation settings
    validate_syntax: bool = True
    validate_imports: bool = True
    validate_types: bool = True  # Run mypy

    # Strategy setting - which conversion strategy to use
    strategy: str = "baseline"  # baseline, two_phase, template_fill, etc.

    # Agentic mode settings (deprecated - use strategy="tool_calling" instead)
    use_agentic_fallback: bool = False
    agentic_max_iterations: int = 10


@dataclass
class ConversionResult:
    """Result of converting a single VI."""

    vi_name: str
    python_code: str
    output_path: Path | None
    success: bool
    errors: list[str] = field(default_factory=list)
    attempts: int = 1
    ui_path: Path | None = None  # Path to UI wrapper if generated
    is_stub: bool = False  # True if this is a stub for a missing dependency
