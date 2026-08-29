"""Terminal classification and return-annotation helpers for class generation."""

from __future__ import annotations

import ast

from lvkit.models import Terminal
from lvkit.type_defaults import _is_class_refnum

from ..ast_utils import parse_expr


class _TerminalClassifierMixin:
    """Self/error terminal detection and return-annotation building."""

    def _is_self_input(self, inp: Terminal, class_name: str) -> bool:
        """Check if input is the class instance (becomes self).

        Uses lv_type to detect class refnums by type, not name.
        """
        lv_type = getattr(inp, "lv_type", None)
        if lv_type and _is_class_refnum(lv_type, class_name):
            return True
        return False

    def _is_self_output(self, out: Terminal, class_name: str) -> bool:
        """Check if output is the class instance (filtered from return).

        Uses lv_type to detect class refnums by type, not name.
        """
        lv_type = getattr(out, "lv_type", None)
        if lv_type and _is_class_refnum(lv_type, class_name):
            return True
        return False

    def _is_error_output(self, out: Terminal) -> bool:
        """Check if output is an error cluster (should not be in return).

        Python uses exceptions instead of error clusters.
        Uses is_error_cluster which checks the type — no name guessing.
        """
        return out.is_error_cluster

    def _build_return_annotation(self, outputs: list[Terminal]) -> ast.expr:
        """Build return type annotation from outputs using lv_type."""
        if not outputs:
            return ast.Constant(value=None)

        if len(outputs) == 1:
            out = outputs[0]
            lv_type = getattr(out, "lv_type", None)
            if lv_type:
                return parse_expr(lv_type.to_python())
            return ast.Name(id="Any", ctx=ast.Load())

        # Multiple outputs - tuple
        elts = []
        for out in outputs:
            lv_type = getattr(out, "lv_type", None)
            if lv_type:
                elts.append(parse_expr(lv_type.to_python()))
            else:
                elts.append(ast.Name(id="Any", ctx=ast.Load()))

        return ast.Subscript(
            value=ast.Name(id="tuple", ctx=ast.Load()),
            slice=ast.Tuple(elts=elts, ctx=ast.Load()),
            ctx=ast.Load(),
        )
