"""Build the Python class of a LabVIEW class (lvclass).

- :mod:`.config` -- :class:`ClassConfig` generation options
- :mod:`.core` -- :class:`ClassBuilder` module and class assembly
- :mod:`.init_builder` -- the attributes and ``__init__`` from private data fields
- :mod:`.properties` -- ``@property`` generation from accessor pairs
- :mod:`.methods` -- method AST generation from a ``MethodShape``
- :mod:`.shared_state` -- the module-level code the methods share
"""

from __future__ import annotations

from .config import ClassConfig
from .core import ClassBuilder, build_class_module

__all__ = [
    "ClassBuilder",
    "ClassConfig",
    "build_class_module",
]
