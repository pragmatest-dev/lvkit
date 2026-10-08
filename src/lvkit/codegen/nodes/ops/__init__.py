"""Per-language (Python) primitive op handlers, keyed by neutral ``op`` tag.

The data file (``primitives.json``) tags a primitive with a language-neutral
``op`` (e.g. ``"STRING_SUBSET"``); the target-language *translation* lives here,
in the code generator, not as a Python string baked into the data. A different
backend (Rust, …) would ship its own registry over the SAME ``op`` tags with
zero data edits.

A handler returns a ``python_code`` **template** in the established convention
(a ``dict`` of ``output_label -> expr``, or a bare ``str`` for a single output),
referencing inputs as ``in_<index>`` where ``<index>`` is the primitive's
connector-pane terminal index from the JSON entry. ``primitive.generate`` then
runs the SAME wiring/substitution path used for JSON templates — handlers own
only the *emission*, never the terminal resolution (DRY).

Add a new op: drop a ``ops/<name>.py`` module that ``@register_op("OP")`` a
function. It is auto-discovered here — no edit to this file — so parallel work
on different ops never touches a shared switch.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import TYPE_CHECKING

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

if TYPE_CHECKING:
    from ...context import CodeGenContext
    from ...fragment import CodeFragment

# op tag -> produces a python_code template (dict[label -> expr] or str).
OpTemplate = Callable[[PrimitiveNode, ResolvedPrimitive], "str | dict[str, str]"]

_OP_PYTHON: dict[str, OpTemplate] = {}


def register_op(op: str) -> Callable[[OpTemplate], OpTemplate]:
    """Register a handler for a neutral op tag."""

    def deco(fn: OpTemplate) -> OpTemplate:
        if op in _OP_PYTHON:
            raise ValueError(f"duplicate Python op handler for {op!r}")
        _OP_PYTHON[op] = fn
        return fn

    return deco


def get_op_template(op: str) -> OpTemplate | None:
    """The registered handler for ``op``, or None if no backend covers it yet."""
    return _OP_PYTHON.get(op)


# op tag -> emits a whole CodeFragment for shapes a single template can't
# express (e.g. per-row statements of an expanded node). Returns None to defer
# to the op's template handler.
OpFragment = Callable[[PrimitiveNode, "CodeGenContext"], "CodeFragment | None"]

_OP_FRAGMENTS: dict[str, OpFragment] = {}


def register_op_fragment(op: str) -> Callable[[OpFragment], OpFragment]:
    """Register a statement-emitting handler for a neutral op tag."""

    def deco(fn: OpFragment) -> OpFragment:
        if op in _OP_FRAGMENTS:
            raise ValueError(f"duplicate Python op fragment handler for {op!r}")
        _OP_FRAGMENTS[op] = fn
        return fn

    return deco


def get_op_fragment(op: str) -> OpFragment | None:
    """The registered fragment handler for ``op``, or None."""
    return _OP_FRAGMENTS.get(op)


# Auto-discover sibling handler modules so each ``@register_op`` runs on import
# and a new ops/<name>.py self-registers with no edit here.
for _mod in pkgutil.iter_modules(__path__):
    if not _mod.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_mod.name}")
