"""Number To Decimal / Hexadecimal String: an unwired ``width`` adds no padding."""

from __future__ import annotations

from lvkit.terminal_default import DefaultBehavior

from . import register_op_behaviors

for _prim_id in (1180, 1181):
    register_op_behaviors(f"prim:{_prim_id}", {DefaultBehavior.NO_PADDING: "0"})
