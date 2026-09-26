"""Display a numeric control's value through its own LabVIEW format spec.

Spec syntax (NI's public docs, "Using Format Specifiers in Numeric Strings"):
``%[-][+][#][0][width][.precision | _significantDigits]specifier``. ``#`` hides
trailing zeros; ``_N`` is N significant digits. Implemented: ``f`` (fixed,
``.precision``) and ``g`` (general, significant digits). Any other spec, or a
value that is not a plain number, is shown exactly as decoded.
"""

from __future__ import annotations

import re

_SPEC = re.compile(
    r"^%(?P<flags>[-+#0]*)(?P<width>\d+)?"
    r"(?:\.(?P<precision>\d+)|_(?P<digits>\d*))?(?P<kind>[fg])$"
)
# Significant digits for a ``g`` spec whose ``_`` carries no number. NI's
# docs give no default; the C ``%g`` default is used.
_DEFAULT_SIGNIFICANT_DIGITS = 6


def _general(value: float, digits: int, hide_trailing_zeros: bool) -> str:
    text = format(value, f"{'' if hide_trailing_zeros else '#'}.{digits}g")
    mantissa, marker, exponent = text.partition("e")
    if not marker:
        return text
    sign = "-" if exponent.startswith("-") else "+"
    return f"{mantissa}E{sign}{int(exponent.lstrip('+-'))}"


def format_number(text: str, spec: str | None) -> str:
    """``text`` (a decoded numeric display string) shown per ``spec``."""
    if not spec:
        return text
    match = _SPEC.match(spec)
    if match is None:
        return text
    try:
        value = float(text)
    except ValueError:
        return text
    hide_zeros = "#" in match["flags"]
    if match["kind"] == "f":
        precision = int(match["precision"]) if match["precision"] else 0
        return f"{value:.{precision}f}"
    digits = (
        int(match["precision"] or match["digits"])
        if (match["precision"] or match["digits"])
        else _DEFAULT_SIGNIFICANT_DIGITS
    )
    return _general(value, digits, hide_zeros)
