"""Base interface for conversion strategies.

All strategies receive the same inputs (VI context from graph) and return
the same output format (StrategyResult). They differ only in how they
interact with the LLM to generate code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..validator import CodeValidator
    from ...llm import LLMConfig


@dataclass
class StrategyResult:
    """Result from a conversion strategy."""

    success: bool
    code: str
    attempts: int
    time_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class ConversionStrategy(ABC):
    """Abstract base class for conversion strategies.

    All strategies:
    1. Receive the same VI context (from graph database)
    2. Use the same validator (syntax, imports, types, completeness)
    3. Return a StrategyResult

    They differ in HOW they interact with the LLM:
    - Baseline: Single-shot + retry with errors
    - Tool Calling: LLM can call tools to gather info
    - Rich Feedback: Auto-include relevant code on errors
    - Two-Phase: Plan first, then code
    - Template Fill: Generate skeleton, LLM fills blanks
    - Constraint Fix: Post-process to fix imports/calls
    """

    name: str = "base"
    description: str = "Base strategy (not implemented)"

    def __init__(
        self,
        validator: CodeValidator,
        llm_config: LLMConfig,
        output_dir: Path,
        max_attempts: int = 3,
    ) -> None:
        self.validator = validator
        self.llm_config = llm_config
        self.output_dir = output_dir
        self.max_attempts = max_attempts

    @abstractmethod
    def convert(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Convert a VI to Python code.

        Args:
            vi_name: Name of the VI being converted
            vi_context: Full VI context from graph.get_vi_context()
                - inputs: List of input controls
                - outputs: List of output indicators
                - operations: List of operations (primitives, SubVIs, loops, etc.)
                - data_flow: Terminal-level connections
                - constants: Constant values on the diagram
            converted_deps: Already-converted SubVI signatures
                - Keys: VI names
                - Values: VISignature with import_statement, python_function, etc.
            primitive_names: List of available primitive function names
            primitive_context: Primitive details by ID
                - python_function: Function name to call
                - python_hint: Python code hint
                - terminals: Input/output terminal info

        Returns:
            StrategyResult with success status, generated code, and metadata
        """
        pass

    def _extract_code(self, response: str) -> str:
        """Extract Python code from LLM response."""
        if "```python" in response:
            start = response.find("```python") + 9
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        if "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        return response.strip()

    def _get_expected_subvis(self, vi_context: dict[str, Any]) -> list[str]:
        """Extract expected SubVI names from operations."""
        return [
            op["name"]
            for op in vi_context.get("operations", [])
            if "SubVI" in op.get("labels", []) and op.get("name")
        ]

    def _get_library_name(self, vi_name: str) -> str | None:
        """Extract library name from qualified VI name.

        Args:
            vi_name: Qualified name like "Library.lvlib:SubVI.vi" or just "SubVI.vi"

        Returns:
            Library name (lowercase, underscored) or None if not in a library
        """
        if ":" not in vi_name:
            return None
        library = vi_name.split(":", 1)[0]
        library = library.replace(".lvlib", "").replace(".lvclass", "")
        result = library.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or None
