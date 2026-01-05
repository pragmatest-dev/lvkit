"""Conversion strategies for VI→Python translation.

Each strategy represents a different approach to LLM-based code generation:
- ast: AST-based deterministic code generation (syntax always valid)
- baseline: Single-shot generation with error retry
- tool_calling: LLM can call tools to gather information
- rich_feedback: Auto-include relevant code on errors
- two_phase: Plan first, then generate code
- template_fill: Generate skeleton, LLM fills in logic
- constraint_fix: Post-process to fix common issues
"""

from .base import ConversionStrategy, StrategyResult

# Strategy registry - populated as strategies are imported
STRATEGIES: dict[str, type[ConversionStrategy]] = {}


def register_strategy(cls: type[ConversionStrategy]) -> type[ConversionStrategy]:
    """Decorator to register a strategy."""
    STRATEGIES[cls.name] = cls
    return cls


def get_strategy(name: str) -> type[ConversionStrategy] | None:
    """Get a strategy class by name."""
    return STRATEGIES.get(name)


def list_strategies() -> list[str]:
    """List all registered strategy names."""
    return list(STRATEGIES.keys())


# Import strategies to register them
from .ast_based import BaselineStrategy  # AST-based is now the baseline
from .baseline import SkeletonStrategy   # Old baseline renamed to skeleton
from .tool_calling import ToolCallingStrategy
from .rich_feedback import RichFeedbackStrategy
from .two_phase import TwoPhaseStrategy
from .template_fill import TemplateFillStrategy
from .constraint_fix import ConstraintFixStrategy

__all__ = [
    "ConversionStrategy",
    "StrategyResult",
    "STRATEGIES",
    "register_strategy",
    "get_strategy",
    "list_strategies",
    "BaselineStrategy",
    "SkeletonStrategy",
    "ToolCallingStrategy",
    "RichFeedbackStrategy",
    "TwoPhaseStrategy",
    "TemplateFillStrategy",
    "ConstraintFixStrategy",
]
