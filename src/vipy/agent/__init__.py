"""Agent loop for validated LabVIEW-to-Python conversion.

This module provides an agent-based conversion pipeline that:
1. Processes VIs in dependency order (leaves first)
2. Generates Python code via LLM (Ollama)
3. Validates code (syntax, imports, types)
4. Retries with error feedback on failure
5. Generates shared types and primitives packages

Usage:
    from vipy.agent import ConversionAgent, ConversionConfig
    from vipy.memory_graph import InMemoryVIGraph

    # Load VIs into graph
    graph = InMemoryVIGraph()
    graph.load_vi("path/to/vi.vi")

    # Configure and run agent
    config = ConversionConfig(
        output_dir=Path("output"),
        max_retries=3,
        generate_ui=True,
    )
    agent = ConversionAgent(graph, config)
    results = agent.convert_all()

NiceGUI Integration:
    The agent generates UI wrappers using NiceGUI's reactive bindings.
    This enables:
    - Input widgets bound to state variables
    - Output indicators that update automatically
    - Async data streaming (for VIs with continuous output)

    For simple request/response VIs, the pattern is:
    1. User fills inputs
    2. Clicks execute
    3. Backend runs, updates output attributes
    4. NiceGUI bindings reflect results

    For streaming VIs (indicators inside loops):
    - Backend updates attributes continuously
    - NiceGUI bindings reflect changes in real-time
    - This matches LabVIEW's dataflow model
"""

from __future__ import annotations

from .context import VISignature
from .context_builder import ContextBuilder
from .loop_agent import ConversionAgent
from .loop_config import ConversionConfig, ConversionResult
from .primitives import PrimitiveRegistry, PrimitiveUsage
from .state import ConversionState, ConvertedModule
from .types import SharedType, SharedTypeRegistry
from .validator import (
    CodeValidator,
    ErrorFormatter,
    ValidationError,
    ValidationResult,
    ValidatorConfig,
)

__all__ = [
    # Main agent
    "ConversionAgent",
    "ConversionConfig",
    "ConversionResult",
    # State tracking
    "ConversionState",
    "ConvertedModule",
    # Context building
    "ContextBuilder",
    "VISignature",
    # Types
    "SharedType",
    "SharedTypeRegistry",
    # Primitives
    "PrimitiveRegistry",
    "PrimitiveUsage",
    # Validation
    "CodeValidator",
    "ValidatorConfig",
    "ValidationError",
    "ValidationResult",
    "ErrorFormatter",
]
