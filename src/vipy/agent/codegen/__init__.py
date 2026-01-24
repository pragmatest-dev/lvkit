"""Code generation from VI graph.

This module provides AST-based Python code generation from VI graph data.
All generated code is guaranteed to be syntactically valid Python.
"""

# New node-based builder
from .builder import build_module
from .class_builder import ClassBuilder, ClassConfig, build_class
from .context import CodeGenContext
from .dataflow import DataFlowTracer
from .expressions import ExpressionBuilder
from .fragment import CodeFragment
from .function import FunctionBuilder
from .imports import ImportBuilder
from .nodes import CodeGenError, MissingDependencyError, UnknownNodeError
from .stubs import StubGenerator

__all__ = [
    "DataFlowTracer",
    "ImportBuilder",
    "ExpressionBuilder",
    "FunctionBuilder",
    "StubGenerator",
    # New API
    "build_module",
    "build_class",
    "CodeGenContext",
    "CodeFragment",
    "ClassBuilder",
    "ClassConfig",
    # Exceptions
    "CodeGenError",
    "MissingDependencyError",
    "UnknownNodeError",
]
