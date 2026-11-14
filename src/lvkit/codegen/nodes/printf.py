"""Code generator for Format Into String (printf) nodes.

The node's inputs are the format string (index 0), the initial string (index 1)
and the values to write (index 5 and up); its result is the initial string
followed by the formatted text. The rules for the text are in
``codegen/labview_format.py``: a format string written on the diagram is turned
into one f-string while generating, and one wired at run time calls a copy of
those rules written into the generated module.
"""

from __future__ import annotations

import ast
from dataclasses import replace

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..gaps import gap_of, raise_statement
from ..labview_format import (
    _lv_decode_escapes,
    _lv_format_into_string,
    _lv_negative_float_reason,
    _lv_parse,
    _lv_round_int,
    _lv_scientific,
    _lv_unsupported,
    _LvSpecifier,
    helper_sources,
)
from ..output_binding import bind_output
from .ops._numeric import FormatKind, format_kind

_FORMAT_INDEX = 0
_INITIAL_INDEX = 1
_FIRST_VALUE_INDEX = 5
# What a Format Into String with no format string writes for each kind of input
# (see ``_default_format``): a string, a path, an enum (its item label) and a
# Boolean (TRUE or FALSE) as ``%s``, an integer as ``%d``, a floating-point
# number and the real part of a complex one as ``%f``.
_DEFAULT_CODES = {
    "string": "%s",
    "path": "%s",
    "enum": "%s",
    "bool": "%s",
    "int": "%d",
    "float": "%f",
    "complex": "%f",
    "timestamp": "%<%.3X %x>T",
}


def literal_text(source: str) -> str | None:
    """The text of the string literal ``source`` (its escapes decoded), or None
    when it is not a string literal: a value known only at run time."""
    try:
        value = ast.literal_eval(source)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _default_format(node: PrimitiveNode, kinds: list[FormatKind]) -> str:
    """The format string LabVIEW uses when none is wired: one code per value, by
    the value's own type, joined by one space. NI: "Defaults match the data type
    of the input arguments" (Format Into String); the code of each type is the one
    its Format Specifier Syntax page gives (``%d`` signed decimal integer, ``%f``
    floating point with six digits, ``%s`` for a string, with a Boolean as TRUE or
    FALSE); complex "converts only the real component"; a timestamp is written as
    absolute time, with the example format of that page's ``%T`` entry,
    ``%<%.3X %x>T`` (Format Specifier Syntax: a time stamp formatted as anything
    other than time is an error; codes ``%X`` locale time, ``%x`` locale date,
    "Format Codes for the Time Format"). The space between the
    values is what LabVIEW writes, confirmed by NI on the forum thread "Feature or
    Bug: Format Into String Adds Spaces". A kind with no code is a gap."""
    codes: list[str] = []
    for kind in kinds:
        code = _DEFAULT_CODES.get(kind.kind)
        if code is None:
            raise ConversionGap(
                "Format Into String has no format string, and its default "
                f"format has no conversion for a {kind.kind} input",
                node,
            )
        codes.append(code)
    return " ".join(codes)


def _add_helpers(ctx: CodeGenContext, *roots: object) -> None:
    """Write the source of ``roots`` (and what they call) into the generated
    module, once. They call each other by name, so each must keep its name."""
    for source in helper_sources(*roots):
        (definition,) = ast.parse(source).body
        assert isinstance(definition, (ast.FunctionDef, ast.ClassDef))
        if ctx.add_module_function(definition) != definition.name:
            raise CodeGenError(
                f"The module-level name {definition.name} is taken by an import"
            )


def _call(name: str, *args: ast.expr) -> ast.expr:
    return ast.Call(
        func=ast.Name(id=name, ctx=ast.Load()), args=list(args), keywords=[]
    )


def _number(value: int | bool) -> ast.expr:
    return ast.Constant(value=value)


def _python_spec(spec: _LvSpecifier, kind_char: str, *, text: bool) -> str:
    """The Python format spec that writes a value the way ``spec`` says: the
    justification, sign, zero padding, width and precision, then ``kind_char``."""
    out = ""
    if spec.left:
        out += "<"
    elif text and (spec.width or spec.precision is not None):
        out += ">"
    if not text and spec.plus:
        out += "+"
    if not text and spec.zero and spec.width:
        out += "0"
    if spec.width:
        out += str(spec.width)
    if spec.precision is not None:
        out += f".{spec.precision}"
    return out + kind_char


def _formatted(value: ast.expr, spec_text: str) -> ast.FormattedValue:
    return ast.FormattedValue(
        value=value,
        conversion=-1,
        format_spec=ast.JoinedStr(values=[ast.Constant(value=spec_text)])
        if spec_text
        else None,
    )


def _labels(kind: FormatKind) -> ast.Dict:
    """An enum's items as a dict literal from value to label."""
    return ast.Dict(
        keys=[ast.Constant(value=value) for value, _ in kind.labels],
        values=[ast.Constant(value=label) for _, label in kind.labels],
    )


def _string_value(
    spec: _LvSpecifier, arg: ast.expr, kind: FormatKind
) -> ast.FormattedValue:
    if kind.kind == "bool":
        value: ast.expr = ast.IfExp(
            test=arg,
            body=ast.Constant(value="TRUE"),
            orelse=ast.Constant(value="FALSE"),
        )
    elif kind.kind == "enum":
        # NI: with %s, "Enums: Interprets the string label of the enum item".
        value = ast.Subscript(
            value=_labels(kind), slice=_call("int", arg), ctx=ast.Load()
        )
    elif kind.kind == "path":
        value = _call("str", arg)
    else:
        value = arg
    return _formatted(value, _python_spec(spec, "", text=True))


def _number_value(
    spec: _LvSpecifier,
    arg: ast.expr,
    kind: FormatKind,
    ctx: CodeGenContext,
    negative_gaps: list[tuple[ast.expr, str]],
) -> ast.FormattedValue:
    """The value written for a number code. A floating-point value with ``u``,
    ``x``, ``o`` or ``b`` adds to ``negative_gaps`` the integer it rounds to and
    the reason a negative one has no conversion."""
    if kind.kind in ("bool", "enum"):
        base: ast.expr = _call("int", arg)
    elif kind.kind == "complex":
        base = ast.Attribute(value=arg, attr="real", ctx=ast.Load())
    else:
        base = arg
    precision = 6 if spec.precision is None else spec.precision
    if spec.code == "e":
        _add_helpers(ctx, _lv_scientific)
        return _formatted(
            _call(
                "_lv_scientific",
                base,
                _number(precision),
                _number(spec.plus),
                _number(spec.width),
                _number(spec.left),
                _number(spec.zero),
            ),
            "",
        )
    if spec.code == "f":
        return _formatted(
            base,
            _python_spec(replace(spec, precision=precision), "f", text=False),
        )
    whole = base
    if kind.kind in ("float", "complex"):
        _add_helpers(ctx, _lv_round_int)
        whole = _call("_lv_round_int", base)
    if spec.code == "d":
        return _formatted(whole, _python_spec(spec, "d", text=False))
    if kind.kind in ("float", "complex"):
        negative_gaps.append((whole, _lv_negative_float_reason(spec)))
    if kind.kind == "int" and kind.signed:
        whole = ast.BinOp(
            left=whole,
            op=ast.BitAnd(),
            right=ast.Constant(value=(1 << kind.bits) - 1),
        )
    char = {"u": "d", "x": "X", "o": "o", "b": "b"}[spec.code]
    return _formatted(whole, _python_spec(spec, char, text=False))


def _with_point(value: ast.FormattedValue, point: ast.expr) -> ast.FormattedValue:
    """``value`` (a floating-point number written as text) with its period
    replaced by the decimal separator ``point``."""
    written = ast.JoinedStr(values=[value])
    replaced = ast.Call(
        func=ast.Attribute(value=written, attr="replace", ctx=ast.Load()),
        args=[ast.Constant(value="."), point],
        keywords=[],
    )
    return ast.FormattedValue(value=replaced, conversion=-1)


def _decimal_point(code: str, ctx: CodeGenContext) -> ast.expr | None:
    """The expression of the decimal separator a localization code sets: None for
    the period (``%.;``), a comma, or the system's (``%;``)."""
    if code == ".;":
        return None
    if code == ",;":
        return ast.Constant(value=",")
    ctx.add_import("import locale")
    return parse_expr("locale.localeconv()['decimal_point']")


def _literal_format(
    text: str,
    arguments: list[tuple[ast.expr, FormatKind]],
    initial: ast.expr | None,
    node: PrimitiveNode,
    ctx: CodeGenContext,
    negative_gaps: list[tuple[ast.expr, str]],
) -> ast.expr:
    """The initial string followed by ``text`` with each specifier written for
    its value, as one f-string."""
    parts: list[ast.expr] = []
    if initial is not None:
        parts.append(ast.FormattedValue(value=initial, conversion=-1))
    point: ast.expr | None = None
    for piece in _lv_parse(text):
        if isinstance(piece, str):
            parts.append(ast.Constant(value=_lv_decode_escapes(piece)))
            continue
        if piece.localization:
            point = _decimal_point(piece.code, ctx)
            continue
        argument = (
            arguments[piece.position] if piece.position < len(arguments) else None
        )
        reason = _lv_unsupported(piece, argument[1].kind if argument else None)
        if reason:
            raise ConversionGap(reason, node)
        assert argument is not None
        arg, kind = argument
        if piece.code == "s":
            parts.append(_string_value(piece, arg, kind))
        else:
            number = _number_value(piece, arg, kind, ctx, negative_gaps)
            if point is not None and piece.code in ("e", "f"):
                number = _with_point(number, point)
            parts.append(number)
    if not any(isinstance(p, ast.FormattedValue) for p in parts):
        return ast.Constant(
            value="".join(str(p.value) for p in parts if isinstance(p, ast.Constant))
        )
    return ast.JoinedStr(values=parts)


def _runtime_format(
    format_expr: str,
    arguments: list[tuple[ast.expr, FormatKind]],
    initial: ast.expr | None,
    ctx: CodeGenContext,
) -> ast.expr:
    """A call of the copied rules for a format string known only at run time."""
    ctx.add_import("import locale")
    ctx.add_import("import re")
    ctx.add_import("from dataclasses import dataclass")
    _add_helpers(ctx, _lv_format_into_string)
    values = ast.List(
        elts=[
            ast.Tuple(
                elts=[
                    arg,
                    ast.Constant(value=kind.kind),
                    ast.Constant(value=kind.bits),
                    _labels(kind),
                ],
                ctx=ast.Load(),
            )
            for arg, kind in arguments
        ],
        ctx=ast.Load(),
    )
    return _call(
        "_lv_format_into_string",
        parse_expr(format_expr),
        initial if initial is not None else ast.Constant(value=""),
        values,
    )


def _input(node: PrimitiveNode, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a printf (Format Into String) node."""
    outputs = [t for t in node.terminals if t.direction == "output"]
    if not outputs:
        return CodeFragment.empty()

    def wired(terminal: Terminal | None) -> str | None:
        if terminal is None or not ctx.is_wired(terminal.id):
            return None
        return ctx.wired_input(terminal.id, node) or "None"

    format_value = wired(_input(node, _FORMAT_INDEX))
    initial_value = wired(_input(node, _INITIAL_INDEX))
    initial = parse_expr(initial_value) if initial_value is not None else None
    arguments: list[tuple[ast.expr, FormatKind]] = []
    for terminal in sorted(
        (
            t
            for t in node.terminals
            if t.direction == "input" and t.index >= _FIRST_VALUE_INDEX
        ),
        key=lambda t: t.index,
    ):
        value = wired(terminal)
        if value is not None:
            arguments.append((parse_expr(value), format_kind(terminal.lv_type)))

    text = None if format_value is None else literal_text(format_value)
    negative_gaps: list[tuple[ast.expr, str]] = []
    expr: ast.expr
    if format_value is not None and text is None:
        expr = _runtime_format(format_value, arguments, initial, ctx)
    else:
        if format_value is None:
            text = _default_format(node, [kind for _, kind in arguments])
        assert text is not None
        expr = _literal_format(text, arguments, initial, node, ctx, negative_gaps)

    out_term = outputs[0]
    base_name = out_term.name or "formatted"
    fragment = bind_output(node, out_term.id, base_name, expr, ctx)
    fragment.statements[:0] = _negative_guards(negative_gaps, node, ctx)
    return fragment


def _negative_guards(
    negative_gaps: list[tuple[ast.expr, str]],
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> list[ast.stmt]:
    """For each floating-point value a number code writes, the gap of a negative
    one: recorded (the warning), and raised where the value turns out negative."""
    guards: list[ast.stmt] = []
    for whole, reason in negative_gaps:
        gap = gap_of(ConversionGap(reason, node), ctx.vi_name or "", node)
        ctx.gaps.append(gap)
        guards.append(
            ast.If(
                test=ast.Compare(
                    left=whole, ops=[ast.Lt()], comparators=[ast.Constant(value=0)]
                ),
                body=[raise_statement(gap.message)],
                orelse=[],
            )
        )
    return guards
