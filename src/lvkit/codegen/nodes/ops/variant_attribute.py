"""Get Variant Attribute: an unwired ``default value`` is an empty variant, which
is ``None`` in Python."""

from __future__ import annotations

from lvkit.terminal_default import DefaultBehavior

from . import register_op_behaviors

register_op_behaviors("prim:8205", {DefaultBehavior.EMPTY_VARIANT: "None"})
