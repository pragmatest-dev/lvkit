"""What the string-to-number functions (Decimal, Hexadecimal and Fract/Exp String
To Number) share: each reads a number from a string starting at ``offset`` and
gives it with ``offset past number``. The scanning is a translator function written
into the generated module; this builds the call, the conversion to the output's
type and the output bindings."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_string_type_name

from ...ast_utils import build_assign, build_multi_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ._flat_node import terminal
from ._numeric import FormatKind, format_kind
from ._regex_node import declare_translator, input_expression


def integer_range(kind: FormatKind) -> tuple[int, int]:
    """The lowest and highest value of the integer type ``kind``."""
    if kind.signed:
        return -(2 ** (kind.bits - 1)), 2 ** (kind.bits - 1) - 1
    return 0, 2**kind.bits - 1


def string_to_number_fragment(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    name: str,
    translator_name: str,
    translator: Callable[..., Any],
    indices: tuple[int, int, int],
    arguments: Callable[[FormatKind], str],
) -> CodeFragment:
    """The statements of the string-to-number node ``node`` named ``name``.

    ``indices`` are its input terminals ``(default, offset, string)``;
    ``arguments`` gives the text of the translator's arguments after
    ``string, offset, default``, from the kind of the output ``number``."""
    default_index, offset_index, string_index = indices
    string_term = terminal(node, "input", string_index)
    number_term = terminal(node, "output", 0)
    past_term = terminal(node, "output", 1)
    if (
        string_term is None
        or string_term.lv_type is None
        or not is_string_type_name(string_term.lv_type.underlying_type)
    ):
        raise ConversionGap(
            f"{name} converts a string; an array or cluster of strings has no code "
            "generation yet",
            node,
        )
    kind = format_kind(number_term.lv_type if number_term is not None else None)
    if kind.kind not in ("int", "float"):
        raise ConversionGap(f"{name} into {kind.kind} has no code generation yet", node)
    string = input_expression(ctx, node, string_index) or "''"
    offset = input_expression(ctx, node, offset_index) or "0"
    default = input_expression(ctx, node, default_index) or "0"
    function = declare_translator(ctx, translator_name, translator)
    number = ctx.make_output_var(
        "number", node.id, terminal_id=number_term.id if number_term else None
    )
    past = ctx.make_output_var(
        "offset_past_number", node.id, terminal_id=past_term.id if past_term else None
    )
    call = f"{function}({string}, {offset}, {default}, {arguments(kind)})"
    statements: list[ast.stmt] = [build_multi_assign([number, past], parse_expr(call))]
    if kind.kind == "float":
        statements.append(build_assign(number, parse_expr(f"float({number})")))
    bindings: dict[str, str] = {}
    if number_term is not None:
        bindings[number_term.id] = number
    if past_term is not None:
        bindings[past_term.id] = past
    return CodeFragment(statements=statements, bindings=bindings)
