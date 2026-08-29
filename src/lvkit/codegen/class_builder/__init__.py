"""Build Python class AST from LabVIEW class (lvclass).

This package is a decomposition of what used to be a single
``class_builder.py`` module. It is split by responsibility:

- :mod:`.config` — :class:`ClassConfig` generation options
- :mod:`.core` — :class:`ClassBuilder` top-level module/class assembly
- :mod:`.init_builder` — ``__init__`` construction from private data fields
- :mod:`.properties` — ``@property`` generation from accessor pairs
- :mod:`.methods` — static/instance method AST generation
- :mod:`.terminals` — self/error terminal classification + return annotations
- :mod:`.naming` — constructor detection and class-name conversion

Every name previously importable from ``lvkit.codegen.class_builder``
remains importable from this exact path.
"""

from __future__ import annotations

from .config import ClassConfig
from .core import ClassBuilder, build_class

__all__ = [
    "ClassBuilder",
    "ClassConfig",
    "build_class",
]
