"""The rules Format Into String follows to write text: the format string's
specifiers (``%5.2f``, ``%2$s``, ``%02x``, ...), its backslash codes, and how each
value is written.

This module is the one implementation of those rules. The functions named
``_lv_*`` and the class ``_LvSpecifier`` are plain standard-library Python that
calls only each other: the code generator uses them while generating, and their
source is copied into a generated module (``helper_sources``) for a format string
that is wired at run time, so the text comes out the same either way. A value is
described to them by its kind: ``string``, ``path``, ``bool``, ``int`` (with its
bit width), ``float``, ``complex`` or ``enum`` (with its items' labels); any other
kind is an input they have no conversion for.
"""

from __future__ import annotations

import inspect
import locale
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _LvSpecifier:
    """One ``%...`` specifier of a format string, taken apart."""

    text: str
    position: int
    flags: str
    width: int
    precision: int | None
    bare_precision: bool
    significant: int | None
    unit: str
    embedded: str
    code: str

    @property
    def left(self) -> bool:
        return "-" in self.flags

    @property
    def plus(self) -> bool:
        return "+" in self.flags

    @property
    def zero(self) -> bool:
        return "0" in self.flags and "-" not in self.flags

    @property
    def localization(self) -> bool:
        """A localization code (``%,;``, ``%.;``, ``%;``): it takes no input and
        writes nothing, and sets the decimal separator of what follows."""
        return self.code in (",;", ".;", ";")


def _lv_decode_escapes(text):
    """The text with its backslash codes replaced by the characters they stand
    for: ``\\00``-``\\FF`` (hex, uppercase) a character, ``\\b`` ``\\f`` ``\\n``
    ``\\r`` ``\\t`` ``\\s`` and ``\\\\`` their characters. A backslash followed by
    one hex digit reads it as ``0`` and that digit; a backslash followed by
    anything else is dropped and the character after it stays."""
    named = {
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "s": " ",
        "\\": "\\",
    }
    digits = "0123456789ABCDEF"
    out = []
    i = 0
    while i < len(text):
        char = text[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        following = text[i + 1 : i + 2]
        after = text[i + 2 : i + 3]
        if following and following in digits and after and after in digits:
            out.append(chr(int(text[i + 1 : i + 3], 16)))
            i += 3
        elif following and following in digits:
            out.append(chr(int(following, 16)))
            i += 2
        elif following in named:
            out.append(named[following])
            i += 2
        else:
            i += 1
    return "".join(out)


def _lv_parse(format_text):
    """The pieces of a format string, in order: literal text (backslash codes
    still undecoded, ``%%`` already ``%``) and ``_LvSpecifier`` objects. A
    specifier without a ``N$`` takes the input after the one the previous
    specifier took."""
    pattern = re.compile(
        r"%(?:(\d+)\$)?([-+#^0]*)(\d*)(\.\d*)?(_\d+)?(\{[^}]*\})?(<[^>]*>)?(.?)",
        re.DOTALL,
    )
    localization = re.compile(r"%([,.]?;)")
    pieces = []
    literal = ""
    position = 0
    index = 0
    while index < len(format_text):
        if format_text[index] != "%":
            literal += format_text[index]
            index += 1
            continue
        if format_text[index + 1 : index + 2] == "%":
            literal += "%"
            index += 2
            continue
        local = localization.match(format_text, index)
        if local:
            match_text = local.group(0)
            fields = ("", "", "0", None, None, "", "", local.group(1))
            end = local.end()
        else:
            match = pattern.match(format_text, index)
            match_text = match.group(0)
            fields = match.groups()
            end = match.end()
            if fields[0]:
                position = int(fields[0]) - 1
        point = fields[3]
        if literal:
            pieces.append(literal)
        literal = ""
        pieces.append(
            _LvSpecifier(
                text=match_text,
                position=position,
                flags=fields[1] or "",
                width=int(fields[2] or 0),
                precision=int(point[1:]) if point and point[1:] else None,
                bare_precision=point == ".",
                significant=int(fields[4][1:]) if fields[4] else None,
                unit=fields[5] or "",
                embedded=fields[6] or "",
                code=fields[7] or "",
            )
        )
        if not local:
            position += 1
        index = end
    if literal:
        pieces.append(literal)
    return pieces


def _lv_unsupported(spec, kind):
    """Why ``spec`` cannot be converted for an input of ``kind`` (None for no
    input at all), or None when it can."""
    if kind is None:
        return f"Format Into String specifier {spec.text!r} has no input to take"
    part = None
    if "#" in spec.flags:
        part = "'#' modifier"
    elif "^" in spec.flags:
        part = "'^' modifier"
    elif spec.significant is not None:
        part = "significant digits"
    elif spec.unit:
        part = "unit"
    elif spec.embedded:
        part = "embedded time format"
    elif spec.bare_precision:
        part = "precision without digits"
    elif spec.code not in tuple("sduxobfe"):
        part = f"conversion code {spec.code!r}"
    elif spec.code in tuple("uxob") and spec.plus:
        part = f"'+' flag with code {spec.code!r}"
    elif spec.code in tuple("duxob") and spec.precision is not None:
        part = f"precision with code {spec.code!r}"
    elif spec.code == "s":
        if kind in ("int", "float", "complex"):
            part = f"'s' with a numeric ({kind}) input"
        elif kind not in ("string", "path", "bool", "enum"):
            part = f"{kind} input"
    elif kind in ("string", "path"):
        part = f"code {spec.code!r} with a {kind} input"
    elif kind not in ("int", "float", "complex", "bool", "enum"):
        part = f"{kind} input"
    if part is None:
        return None
    return f"Format Into String specifier {spec.text!r} ({part}) has no conversion"


def _lv_negative_float_reason(spec):
    """Why ``spec`` (``u``, ``x``, ``o`` or ``b``) cannot write a negative
    floating-point value: NI's docs do not say how many bits it is written in."""
    return (
        f"Format Into String specifier {spec.text!r} (a negative floating-point "
        "value) has no conversion: NI's docs do not say how many bits it is "
        "written in"
    )


def _lv_pad(text, width, left, zero):
    """The text in a field ``width`` wide: left-justified, or right-justified
    with spaces or (``zero``) with zeros after the sign."""
    if left:
        return text.ljust(width)
    if zero:
        return text.zfill(width)
    return text.rjust(width)


def _lv_round_int(value):
    """An integer from a number: a float is rounded half to even into a signed
    64-bit integer; NaN and anything past the largest becomes the largest."""
    if isinstance(value, int):
        return value
    if value != value or value >= 2**63:
        return 2**63 - 1
    if value <= -(2**63):
        return -(2**63)
    return round(value)


def _lv_exponent(value, precision, plus):
    """The number in scientific notation: ``precision`` digits after the point,
    an uppercase ``E``, the exponent's sign and no leading zeros (``1.267E+1``)."""
    text = format(value, f"{'+' if plus else ''}.{precision}e")
    if "e" not in text:
        return text
    mantissa, exponent = text.split("e")
    return mantissa + "E" + exponent[0] + (exponent[1:].lstrip("0") or "0")


def _lv_scientific(value, precision, plus, width, left, zero):
    """The number in scientific notation, in a field ``width`` wide."""
    return _lv_pad(_lv_exponent(value, precision, plus), width, left, zero)


def _lv_format_value(spec, value, kind, bits, labels, point=None):
    """The text of one value written for ``spec`` (one ``_lv_unsupported`` has
    accepted for this kind). ``labels`` maps an enum's values to its items'
    labels: ``%s`` writes the label. ``point`` is the decimal separator a
    floating-point value is written with (None: the period)."""
    code = spec.code
    if code == "s":
        if kind == "bool":
            text = "TRUE" if value else "FALSE"
        elif kind == "enum":
            text = labels[int(value)]
        else:
            text = str(value)
        if spec.precision is not None:
            text = text[: spec.precision]
        return _lv_pad(text, spec.width, spec.left, False)
    if kind == "bool" or kind == "enum":
        number = int(value)
    elif kind == "complex":
        number = value.real
    else:
        number = value
    precision = 6 if spec.precision is None else spec.precision
    if code == "e":
        text = _lv_scientific(
            number, precision, spec.plus, spec.width, spec.left, spec.zero
        )
        return text if point is None else text.replace(".", point)
    if code == "f":
        text = format(number, f"{'+' if spec.plus else ''}.{precision}f")
        if point is not None:
            text = text.replace(".", point)
    else:
        whole = _lv_round_int(number)
        if code == "d":
            text = format(whole, "+d" if spec.plus else "d")
        else:
            if whole < 0 and kind != "int":
                raise NotImplementedError(_lv_negative_float_reason(spec))
            if whole < 0:
                whole &= (1 << bits) - 1
            text = format(whole, {"u": "d", "x": "X", "o": "o", "b": "b"}[code])
    return _lv_pad(text, spec.width, spec.left, spec.zero)


def _lv_format_into_string(format_string, initial, args):
    """What Format Into String writes: ``initial`` followed by the format string
    with each specifier replaced by the value it takes from ``args``, a list of
    ``(value, kind, bits, labels)`` (``labels``: an enum's values to labels)."""
    text = initial
    point = None
    for piece in _lv_parse(format_string):
        if isinstance(piece, str):
            text += _lv_decode_escapes(piece)
            continue
        if piece.localization:
            point = (
                locale.localeconv()["decimal_point"]
                if piece.code == ";"
                else piece.code[0]
            )
            continue
        if piece.position < len(args):
            value, kind, bits, labels = args[piece.position]
        else:
            value, kind, bits, labels = None, None, 0, {}
        reason = _lv_unsupported(piece, kind)
        if reason:
            raise NotImplementedError(reason)
        text += _lv_format_value(piece, value, kind, bits, labels, point)
    return text


# What each piece calls, so the source copied into a module is complete.
_DEPENDENCIES: dict[object, tuple[object, ...]] = {
    _LvSpecifier: (),
    _lv_decode_escapes: (),
    _lv_parse: (_LvSpecifier,),
    _lv_unsupported: (),
    _lv_pad: (),
    _lv_round_int: (),
    _lv_exponent: (),
    _lv_scientific: (_lv_pad, _lv_exponent),
    _lv_negative_float_reason: (),
    _lv_format_value: (
        _lv_pad,
        _lv_round_int,
        _lv_scientific,
        _lv_negative_float_reason,
    ),
    _lv_format_into_string: (
        _lv_decode_escapes,
        _lv_parse,
        _lv_unsupported,
        _lv_format_value,
    ),
}


def helper_sources(*roots: object) -> list[str]:
    """The source of each of ``roots`` and of everything they call, a piece
    after the pieces it needs."""
    ordered: list[object] = []

    def add(piece: object) -> None:
        for needed in _DEPENDENCIES[piece]:
            add(needed)
        if piece not in ordered:
            ordered.append(piece)

    for root in roots:
        add(root)
    return [inspect.getsource(piece) for piece in ordered]  # type: ignore[arg-type]
