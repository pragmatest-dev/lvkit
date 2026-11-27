"""Convert Unit (the ``uCast`` node, VI Server class UnitCast, property Unit).

NI, "Convert Unit" (labview-api-ref, ``functions/convert-unit.html``): "If the
input is a pure number (a number with no units), the output is a physical number
with the specified unit. If the input is a physical number, then the value will be
converted to the specified unit but the output will be a pure number." A pure
number is given "in the base unit (or a combination of base units) for that class
of measurement": "if x is 10 and you enter min (minutes) as the unit ..., y is 600
seconds"; "if x is 37 meters (m) ... and you enter ft (feet) ..., y is 121.36 with
no associated units". So a physical number is held in base units, and the node
multiplies a pure number by the unit's factor to the base unit, or divides a
physical number by it.

The unit string is a product or quotient of units with integer exponents
(``m/s^2``); a unit is a name of ``_UNITS`` or an SI prefix and a prefixable unit.
A unit this table lacks, or an input that is not a floating-point number, is a gap
naming it.

Terminals: in 0 = the number; out 1 = the converted number.
"""

from __future__ import annotations

import re

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment

# The factor of each unit to the base unit of its class (NI's base units: m, kg, s,
# A, K, mol, cd, rad, sr). Definitions, not measurements.
_UNITS: dict[str, float] = {
    "m": 1.0,
    "kg": 1.0,
    "s": 1.0,
    "A": 1.0,
    "K": 1.0,
    "mol": 1.0,
    "cd": 1.0,
    "rad": 1.0,
    "sr": 1.0,
    "g": 1e-3,
    "min": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "in": 0.0254,
    "ft": 0.3048,
    "yd": 0.9144,
    "mi": 1609.344,
    "lb": 0.45359237,
}

# The SI prefixes, and the units they may stand before (``kg`` is a gram with a
# prefix, and is the base unit by itself).
_PREFIXES: dict[str, float] = {
    "Y": 1e24,
    "Z": 1e21,
    "E": 1e18,
    "P": 1e15,
    "T": 1e12,
    "G": 1e9,
    "M": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
    "a": 1e-18,
}
_PREFIXABLE = ("m", "s", "A", "K", "mol", "cd", "rad", "sr")
_PHYSICAL = ("UnitFloat32", "UnitFloat64", "UnitFloatExt")
_PURE = ("NumFloat32", "NumFloat64", "NumFloatExt")
_TERM = re.compile(r"\s*([*/])?\s*([A-Za-z]+)(?:\^(-?\d+))?\s*")


def unit_factor(unit: str) -> float | None:
    """The factor of ``unit`` to its base units, None when its text is not a unit
    this table knows."""
    factor = 1.0
    position = 0
    while position < len(unit):
        match = _TERM.match(unit, position)
        if match is None or match.end() == position:
            return None
        operator, name, exponent = match.groups()
        if (operator is None) != (position == 0):
            return None
        scale = _name_factor(name)
        if scale is None:
            return None
        power = int(exponent) if exponent else 1
        factor *= scale ** (-power if operator == "/" else power)
        position = match.end()
    return factor if unit.strip() else None


def _name_factor(name: str) -> float | None:
    if name in _UNITS:
        return _UNITS[name]
    for prefix, scale in _PREFIXES.items():
        base = name[len(prefix) :]
        if name.startswith(prefix) and base in _PREFIXABLE:
            return scale * _UNITS[base]
    return None


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    number_term = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 0), None
    )
    result_term = next((t for t in node.terminals if t.direction == "output"), None)
    if number_term is None or result_term is None:
        raise ConversionGap("Convert Unit has no number input or no output", node)
    factor = unit_factor(node.unit) if node.unit else None
    if factor is None:
        raise ConversionGap(
            f"Convert Unit: the unit {node.unit!r} is not one this conversion "
            "knows (SI base units and their prefixes, min, h, d, in, ft, yd, mi, "
            "lb, g, with * / and ^ exponents)",
            node,
        )
    kind = number_term.lv_type.underlying_type if number_term.lv_type else None
    if kind not in _PHYSICAL and kind not in _PURE:
        raise ConversionGap(
            f"Convert Unit: the input is {kind}, not a floating-point number", node
        )
    value = ctx.wired_input(number_term.id, node)
    if value is None:
        raise ConversionGap("Convert Unit has no number wired", node)
    operator = "/" if kind in _PHYSICAL else "*"
    expression = value if factor == 1.0 else f"({value}) {operator} {factor!r}"
    name = ctx.make_output_var("converted", node.id, terminal_id=result_term.id)
    return CodeFragment(
        statements=[build_assign(name, parse_expr(expression))],
        bindings={result_term.id: name},
    )
