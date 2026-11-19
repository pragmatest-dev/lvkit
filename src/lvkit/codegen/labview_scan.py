"""The rules Scan From String follows to read text: the format string's
specifiers (``%d``, ``%f``, ``%x``, ``%s``, ``%[a-z]``, ``%3d``), its literal
text and spaces, and how each scanned piece becomes the value of its output.

A format string written on the diagram is turned into one ``re`` pattern while
generating (``scan_plan``): the pattern is written into the generated module and
each output is a conversion of its group, so the generated code needs nothing
else. The specifiers are taken apart by ``labview_format._lv_parse``, the parser
Format Into String uses; only ``%[...]`` (a set of characters, which that parser
reads as a conversion code) is read here.

NI's rules (Scan From String; Format Specifier Syntax): "A space in format
string matches any amount of white space"; a numeric specifier ignores white
space in the input; ``%s`` "matches only up to the next white-space character";
``%[...]`` matches only the characters in the set (``-`` between two characters
is a range, ``^`` first negates it, case sensitive); a width is "the maximum
character field width"; a string that does not fit a numeric type reads as "the
largest number that fits into that data type"; "If the format code is %s or
unwired, the function reads Y, T, TRUE, ON, or YES and any lowercase versions of
those words as TRUE Boolean values. The function reads F, FALSE, OFF, or NO and
any lowercase versions of those words as FALSE"; with a numeric code "any numeric
value greater than 0.5" is TRUE.

An enum output (Scan From String: "If you wire an enumerated type to default 1,
the function finds substrings matching the string values in the enumerated type
and returns the corresponding numeric value of the enumerated type"; Format
Specifier Syntax gives ``%s`` for an enum, the item's label) reads the label of
one item at the scan position. NI forum reports (forums.ni.com, "Scan From
String Into Enum -- Exact Match Otherwise Error", t5/LabVIEW/td-p/3333885; "Find
Enum Item from String Input", ta-p/3490889): text after the label is ignored
("Oneasdf" gives One), text before it is an error, no matching item is error 85
(the scan's usual failure here), and NI's knowledge base says the match is case
sensitive. When one item's label starts another's NI's pages do not say which
item the scan returns; the forum posts found say it "matches the longest string
in the enum" (a reply on the "Exact Match" thread) and that the position of the
item in the list made no difference in a test ("ThisIs" and "ThisIsnt"), while
the original poster only feared the order might matter. The longest label is
the recorded choice. An empty label is a gap (a forum report says an enum whose
first item is empty fails to scan back).

A complex output reads one real number into its real part, the imaginary part 0
(a recorded deduction, the maintainer's decision, 2026-11-17: NI's Format Into
String page: "For complex numeric data types, this function converts only the real
component", and a scan reads what a format writes; an NI engineer on
forums.ni.com, "Odd behavior with Error In for Scan From String function",
m-p/3134009: "2+2" does not parse as 2+0i, the scan stops after "2"). With no
format string it reads ``%f``, the code Format Into String writes it with.

The pattern reads as far as the input matches: each specifier and the text
before it sit in a group that is optional given the one before, so the number of
specifiers scanned is the last group that matched, and a scan that stops early
leaves the later outputs at their defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .labview_format import (
    _lv_decode_escapes,
    _lv_default_code,
    _lv_parse,
    _LvSpecifier,
)
from .labview_scan_time import time_scan

if TYPE_CHECKING:
    from .labview_format import FormatKind

# The conversion codes a scan reads: signed decimal, floating point (``f``, ``g``
# and ``e`` read the same text), hexadecimal and string.
_CODES = "dfgexsp"
# The SI prefix letters ``%p`` reads and the power of ten each stands for (NI's
# pages do not list them; the letters are the SI prefixes, with ``u`` for micro as
# NI forum posts show).
_SI_EXPONENTS = {
    "y": -24,
    "z": -21,
    "a": -18,
    "f": -15,
    "p": -12,
    "n": -9,
    "u": -6,
    "m": -3,
    "k": 3,
    "M": 6,
    "G": 9,
    "T": 12,
    "P": 15,
    "E": 18,
    "Z": 21,
    "Y": 24,
}
_SI_LETTERS = "".join(_SI_EXPONENTS)
_SET = re.compile(r"%(\d*)\[(\^?\]?[^\]]*)\]")
_TRUE_WORDS = ("Y", "T", "TRUE", "ON", "YES", "y", "t", "true", "on", "yes")
_FALSE_WORDS = ("F", "FALSE", "OFF", "NO", "f", "false", "off", "no")
# What a Scan From String with no format string reads for each kind of output: a
# string, a Boolean and an enum as ``%s``, an integer as ``%d``, a floating-point
# number as ``%f`` (see ``default_format``).
_DEFAULT_CODES = {
    "string": "%s",
    "bool": "%s",
    "enum": "%s",
    "int": "%d",
    "float": "%f",
    "complex": _lv_default_code("complex"),
}


@dataclass(frozen=True)
class ScanSet:
    """One ``%[...]`` of a format string: the characters between the brackets
    (backslash codes decoded), and its width."""

    chars: str
    width: int
    text: str


@dataclass(frozen=True)
class ScanSpec:
    """One specifier of a format string, with the decimal separator the
    ``%.;`` or ``%,;`` before it set."""

    spec: _LvSpecifier
    decimal: str


@dataclass(frozen=True)
class ScanField:
    """What one specifier reads and how its text becomes its output: the regular
    expression of the text, and the Python expression of the value, in which
    ``{g}`` stands for the text."""

    pattern: str
    value: str
    skips_white_space: bool
    imports: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanPlan:
    """A format string compiled for the outputs it fills: the ``re`` pattern
    (one group per specifier) and each output's value expression over its group's
    text (``{g}``)."""

    pattern: str
    values: tuple[str, ...]
    imports: tuple[str, ...] = ()


def scan_pieces(format_text: str) -> list[str | ScanSpec | ScanSet]:
    """The pieces of a format string, in order: literal text (backslash codes
    still undecoded), specifiers and character sets. ``%.;``, ``%,;`` and ``%;``
    set the decimal separator of the specifiers after them and are not pieces."""
    chunks: list[str | ScanSet] = []
    chunk = ""
    index = 0
    while index < len(format_text):
        if format_text.startswith("%%", index):
            chunk += "%%"
            index += 2
            continue
        found = _SET.match(format_text, index)
        if found is None:
            chunk += format_text[index]
            index += 1
            continue
        if chunk:
            chunks.append(chunk)
        chunk = ""
        chunks.append(
            ScanSet(
                chars=_lv_decode_escapes(found.group(2)),
                width=int(found.group(1) or 0),
                text=found.group(0),
            )
        )
        index = found.end()
    if chunk:
        chunks.append(chunk)
    pieces: list[str | ScanSpec | ScanSet] = []
    decimal = "."
    for chunk_piece in chunks:
        if isinstance(chunk_piece, ScanSet):
            pieces.append(chunk_piece)
            continue
        for piece in _lv_parse(chunk_piece):
            if isinstance(piece, str):
                pieces.append(piece)
            elif piece.code in (",;", ".;"):
                decimal = piece.code[0]
            elif piece.code == ";":
                decimal = ""
            else:
                pieces.append(ScanSpec(piece, decimal))
    return pieces


def default_format(kinds: list[FormatKind]) -> str:
    """The format string used when none is wired: one code per output, by the
    output's own type, joined by a space (which matches any white space). NI:
    "The default is to scan the string according to the default format for the
    data types of the outputs."; the code of each type is the one its Format
    Specifier Syntax page gives. A type with no code is a ``ValueError`` naming
    it."""
    codes = []
    for kind in kinds:
        code = _DEFAULT_CODES.get(kind.kind)
        if code is None:
            raise ValueError(
                "Scan From String has no format string, and its default format "
                f"has no conversion for a {kind.kind} output"
            )
        codes.append(code)
    return " ".join(codes)


def field_count(pieces: list[str | ScanSpec | ScanSet]) -> int:
    """How many outputs the pieces fill."""
    return sum(not isinstance(piece, str) for piece in pieces)


def scan_plan(
    pieces: list[str | ScanSpec | ScanSet], kinds: list[FormatKind]
) -> ScanPlan:
    """The pattern and the value of each output for the pieces of a format
    string. ``kinds`` is the kind of each output, in order. A specifier with no
    conversion for its output is a ``ValueError`` giving the reason."""
    segments: list[str] = []
    values: list[str] = []
    imports: list[str] = []
    literal = ""
    fields = iter(kinds)
    for piece in pieces:
        if isinstance(piece, str):
            literal += _lv_decode_escapes(piece)
            continue
        kind = next(fields)
        field = _field(piece, kind)
        prefix = _literal_pattern(literal)
        literal = ""
        white = r"\s*" if field.skips_white_space else ""
        segments.append(f"{prefix}{white}({field.pattern})")
        values.append(field.value)
        imports.extend(field.imports)
    pattern = ""
    tail = _literal_pattern(literal)
    if tail:
        pattern = f"(?:{tail})?"
    for segment in reversed(segments):
        pattern = f"(?:{segment}{pattern})?"
    return ScanPlan(pattern, tuple(values), tuple(dict.fromkeys(imports)))


def _literal_pattern(text: str) -> str:
    """The pattern of literal text: its characters as they are, and each run of
    spaces any amount of white space."""
    out = ""
    for char in text:
        if char == " ":
            if not out.endswith(r"\s*"):
                out += r"\s*"
        else:
            out += re.escape(char)
    return out


def _field(piece: ScanSpec | ScanSet, kind: FormatKind) -> ScanField:
    if isinstance(piece, ScanSet):
        return _string_field(piece.text, _set_pattern(piece), piece.width, kind)
    spec = piece.spec
    reason = _unsupported(piece)
    if reason:
        raise ValueError(reason)
    width = spec.width
    if spec.code == "T":
        return _time_field(piece, kind)
    if spec.code == "s":
        if kind.kind == "bool":
            words = "|".join((*_TRUE_WORDS, *_FALSE_WORDS))
            return ScanField(
                f"(?:{words})(?![^\\s])", f"{{g}} in {_TRUE_WORDS!r}", False
            )
        if kind.kind == "enum":
            return _enum_field(spec.text, width, kind)
        return _string_field(spec.text, r"\S", width, kind)
    if spec.code == "d":
        text = _decimal_pattern(width)
        number = "int({g})"
    elif spec.code == "x":
        text = "[0-9A-Fa-f]" + (f"{{1,{width}}}" if width else "+")
        number = "int({g}, 16)"
    else:
        if width:
            raise ValueError(
                f"Scan From String specifier {spec.text!r} (a width with a "
                "floating-point code) has no conversion"
            )
        if not piece.decimal:
            raise ValueError(
                f"Scan From String specifier {spec.text!r} (floating-point after "
                "'%;', the system decimal separator) has no conversion"
            )
        text = _float_pattern(piece.decimal)
        number = (
            'float({g}.replace(",", "."))' if piece.decimal == "," else "float({g})"
        )
        if spec.code == "p":
            text += f"(?:[{_SI_LETTERS}](?![A-Za-z]))?"
            number = _si_number(piece.decimal)
    return ScanField(text, _as_kind(number, spec, kind), True)


def _unsupported(piece: ScanSpec) -> str | None:
    """Why the specifier cannot be read (None when it can)."""
    spec = piece.spec
    part = None
    if spec.code == "T":
        part = _time_unsupported(spec)
    elif spec.code == ";":
        part = "the system decimal separator"
    elif spec.flags:
        part = "a modifier (NI: scanning uses %[Width]Conversion Code, no modifiers)"
    elif spec.precision is not None or spec.bare_precision:
        part = "a precision"
    elif spec.significant is not None:
        part = "significant digits"
    elif spec.unit:
        part = "a unit"
    elif spec.embedded:
        part = "an embedded time format"
    elif spec.code not in _CODES:
        part = f"conversion code {spec.code!r}"
    if part is None:
        return None
    return f"Scan From String specifier {spec.text!r} ({part}) has no conversion"


def _time_unsupported(spec: _LvSpecifier) -> str | None:
    """Why a ``T`` specifier cannot be read (None when it can): only the ``^``
    flag and the embedded time format are read."""
    if set(spec.flags) - {"^"}:
        return "a flag"
    if spec.width or spec.precision is not None or spec.bare_precision:
        return "a width or precision"
    if spec.significant is not None or spec.unit:
        return "significant digits or a unit"
    if not spec.embedded:
        return "no embedded time format"
    return None


def _time_field(piece: ScanSpec, kind: FormatKind) -> ScanField:
    """The text of a ``%<...>T`` and the value it reads: a time stamp output gets
    the time stamp, a floating-point output the time stamp as seconds since
    1904-01-01 UTC (NI, Format Specifier Syntax: "T can interpret time stamps and
    numerics"; a time stamp is read as a number of seconds), whose default is
    likewise a number of seconds."""
    spec = piece.spec
    if kind.kind not in ("timestamp", "float"):
        raise ValueError(
            f"Scan From String specifier {spec.text!r} (a time into a "
            f"{kind.kind} output) has no conversion"
        )
    scan = time_scan(spec.text, spec.embedded, "^" in spec.flags, piece.decimal)
    value = scan.value
    if kind.kind == "float":
        epoch = "hightime.datetime(1904, 1, 1, tzinfo=UTC)"
        value = value.replace(
            "{d}", f"({epoch} + hightime.timedelta(seconds={{d}}))"
        )
        value = f"({value} - {epoch}).total_seconds()"
    return ScanField(scan.pattern, value, False, scan.imports)


def _si_number(decimal: str) -> str:
    """The expression of the number an SI-notation text (``%p``) reads: the
    number, scaled by its prefix letter when it ends in one (the text of a prefix
    followed by another letter, as in ``10.0kHz``, is not taken as a prefix: NI
    forum reports, "Bug with Scan from String using SI (%p) format code" and "SI
    Notation in Scan Value and Scan from String issue": "10.0k" reads 10000,
    "10.0kHz" reads 10, "12uA" reads 12)."""
    plain = '{g}.replace(",", ".")' if decimal == "," else "{g}"
    exponents = ", ".join(f'"{c}": "{e}"' for c, e in _SI_EXPONENTS.items())
    return (
        f"(float({plain}[:-1] + 'e' + {{{{{exponents}}}}}[{{g}}[-1]]) "
        f"if {{g}}[-1:] in {_SI_LETTERS!r} else float({plain}))"
    )


def _decimal_pattern(width: int) -> str:
    """Optional sign and decimal digits, in at most ``width`` characters."""
    if not width:
        return "[+-]?[0-9]+"
    if width == 1:
        return "[0-9]"
    return f"(?:[+-][0-9]{{1,{width - 1}}}|[0-9]{{1,{width}}})"


def _float_pattern(decimal: str) -> str:
    """A floating-point number with fractional or scientific notation."""
    point = re.escape(decimal)
    return f"[+-]?(?:[0-9]+{point}?[0-9]*|{point}[0-9]+)(?:[eE][+-]?[0-9]+)?"


def _set_pattern(piece: ScanSet) -> str:
    """The character class of a set, ranges and ``^`` as the set gives them."""
    chars = piece.chars
    negate = chars.startswith("^")
    body = chars[1:] if negate else chars
    out = ""
    for char in body:
        out += "\\" + char if char in "\\]^[" else char
    return f"[{'^' if negate else ''}{out}]"


def _enum_field(text: str, width: int, kind: FormatKind) -> ScanField:
    """The label of one item of an enum output, read as its value."""
    if width:
        raise ValueError(
            f"Scan From String specifier {text!r} (a width with an enum output) "
            "has no conversion"
        )
    labels = [label for _, label in kind.labels]
    if not labels or "" in labels:
        raise ValueError(
            f"Scan From String specifier {text!r} (an enum output with no item "
            "labels, or an empty one) has no conversion"
        )
    longest_first = sorted(labels, key=lambda label: -len(label))
    pattern = "|".join(re.escape(label) for label in longest_first)
    values = repr({label: value for value, label in kind.labels})
    # The value is a template in which ``{g}`` stands for the text.
    template = values.replace("{", "{{").replace("}", "}}") + "[{g}]"
    return ScanField(f"(?:{pattern})", template, False)


def _string_field(text: str, char: str, width: int, kind: FormatKind) -> ScanField:
    if kind.kind != "string":
        raise ValueError(
            f"Scan From String specifier {text!r} (a string into a "
            f"{kind.kind} output) has no conversion"
        )
    count = f"{{1,{width}}}" if width else "+"
    return ScanField(f"{char}{count}", "{g}", False)


def _as_kind(number: str, spec: _LvSpecifier, kind: FormatKind) -> str:
    """The expression that turns the number ``number`` a numeric code reads into
    the value of an output of ``kind``."""
    if kind.kind == "float":
        return number if spec.code in "fgep" else f"float({number})"
    if kind.kind == "bool":
        return f"{number} > 0.5"
    if kind.kind == "complex":
        # A recorded deduction (the maintainer's decision, 2026-11-17): a
        # complex output reads one real number, the same text the specifier
        # reads for a real output, into the real part; the imaginary part is 0.
        # Evidence: NI's Format Into String page says "For complex numeric data
        # types, this function converts only the real component" (a scan reads
        # what a format writes), and an NI engineer on forums.ni.com ("Odd
        # behavior with Error In for Scan From String function",
        # m-p/3134009) says "2+2" does not parse as 2+0i: the scan stops after
        # "2". NI's "Valid Format Specifiers for Numeric Data Types" lets a
        # complex type use f, e, g, p, # and ^; f, e, g and p are read here.
        if spec.code in "fgep":
            return f"complex({number}, 0.0)"
        raise ValueError(
            f"Scan From String specifier {spec.text!r} (code {spec.code!r} into "
            "a complex output) has no conversion"
        )
    if kind.kind == "int" and spec.code in "dx":
        low, high = _range(kind)
        return f"max({low}, min({high}, {number}))"
    if kind.kind == "string":
        raise ValueError(
            f"Scan From String specifier {spec.text!r} (a number into a string "
            "output) has no conversion"
        )
    raise ValueError(
        f"Scan From String specifier {spec.text!r} (into a {kind.kind} output) "
        "has no conversion"
    )


def _range(kind: FormatKind) -> tuple[int, int]:
    """The smallest and largest value an integer output holds."""
    if kind.signed:
        return -(2 ** (kind.bits - 1)), 2 ** (kind.bits - 1) - 1
    return 0, 2**kind.bits - 1
