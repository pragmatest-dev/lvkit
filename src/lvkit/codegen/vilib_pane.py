"""A vi.lib catalog entry checked against the connector pane of the VI it describes.

When the VI a call names is loaded, its connector pane (its controls' labels, slot
numbers, types and saved defaults) is the ground truth for the catalog entry:

- a catalog terminal with no index takes the slot (and, when it has none, the
  direction) of the pane control whose label equals the catalog name exactly;
- an input the catalog gives no default for takes the control's saved default;
- a catalog terminal whose index or type disagrees with the pane is reported, never
  used.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.lv_type_codes import (
    INTEGER_TYPE_NAMES,
    TD_FULL_TYPE,
    is_boolean_type_name,
    is_numeric_type_name,
    is_string_type_name,
)
from lvkit.models import LVType, LVTypeKind, Terminal, control_type_to_lvtype
from lvkit.vilib_resolver import VIEntry, VITerminal, get_resolver

from .ast_utils import param_default_expr

_DIRECTION = {"in": "input", "out": "output"}
_CATALOG_DIRECTION = {pane: catalog for catalog, pane in _DIRECTION.items()}


@dataclass(frozen=True)
class PaneCheck:
    """The catalog entry completed from the pane, and every way it disagrees."""

    entry: VIEntry
    mismatches: list[str]


def _family(type_name: str) -> str | None:
    """The kind of value a primitive type name holds (``Boolean``, ``String``,
    ``Path``, ``Numeric``), in either vocabulary the catalog uses (``NumInt32``,
    ``stdNum``); None for a name that is not a primitive."""
    mapped = control_type_to_lvtype(type_name)
    underlying = mapped.underlying_type if mapped is not None else type_name
    if is_boolean_type_name(underlying):
        return "Boolean"
    if is_string_type_name(underlying):
        return "String"
    if underlying == TD_FULL_TYPE.Path.name:
        return "Path"
    if is_numeric_type_name(underlying):
        return "Numeric"
    return None


def literal_default(text: str | None, lv_type: LVType | None) -> str | None:
    """``text`` as Python source when it is a literal of ``lv_type`` (a boolean, a
    string, an integer or a float, or the integer of an enum or ring); None for
    anything else, such as the prose a catalog description gives ("value is
    30000") or a value of another type."""
    if text is None or lv_type is None:
        return None
    try:
        value = ast.literal_eval(text.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    underlying = lv_type.underlying_type
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        fits = type(value) is int
    elif underlying is None:
        fits = False
    elif is_boolean_type_name(underlying):
        fits = type(value) is bool
    elif is_string_type_name(underlying):
        fits = type(value) is str
    elif underlying in INTEGER_TYPE_NAMES:
        fits = type(value) is int
    elif is_numeric_type_name(underlying):
        fits = type(value) in (int, float)
    else:
        fits = False
    return ast.unparse(ast.Constant(value=value)) if fits else None


def _type_disagrees(catalog: VITerminal, pane: Terminal) -> bool:
    """Whether the catalog's type for a terminal is not the pane control's type.
    A type the catalog leaves out, or one that is not a primitive on both sides,
    is not a disagreement."""
    lv_type = pane.lv_type
    if catalog.type is None or lv_type is None:
        return False
    if get_resolver().resolve_type(catalog.type) is not None:
        return lv_type.typedef_name is not None and catalog.type != lv_type.typedef_name
    if lv_type.kind is not LVTypeKind.PRIMITIVE or lv_type.underlying_type is None:
        return False
    wanted, held = _family(catalog.type), _family(lv_type.underlying_type)
    return wanted is not None and held is not None and wanted != held


def check_against_pane(entry: VIEntry, pane: list[Terminal]) -> PaneCheck:
    """``entry`` with indices and defaults taken from ``pane`` (the connector-pane
    controls and indicators of the loaded VI), and the disagreements found."""
    terminals: list[VITerminal] = []
    mismatches: list[str] = []
    for catalog in entry.terminals:
        wanted = _DIRECTION.get(catalog.direction or "")
        same_label = [
            p
            for p in pane
            if p.name == catalog.name and (wanted is None or p.direction == wanted)
        ]
        match: Terminal | None = None
        if catalog.index is None:
            if len(same_label) == 1:
                match = same_label[0]
        else:
            at_index = [
                p
                for p in pane
                if p.index == catalog.index
                and (wanted is None or p.direction == wanted)
            ]
            if same_label and not any(p.index == catalog.index for p in same_label):
                mismatches.append(
                    f"the catalog gives '{catalog.name}' index {catalog.index}, but "
                    f"the loaded VI's control '{catalog.name}' is at index "
                    f"{same_label[0].index}"
                )
            elif at_index:
                match = at_index[0]
        if match is not None and _type_disagrees(catalog, match):
            mismatches.append(
                f"the catalog gives '{catalog.name}' type {catalog.type}, but the "
                f"loaded VI's control at index {match.index} is "
                f"{match.type_descriptor()}"
            )
            match = None
        update: dict[str, object] = {}
        if match is not None:
            if catalog.index is None:
                update["index"] = match.index
            if catalog.direction is None:
                update["direction"] = _CATALOG_DIRECTION[match.direction]
            if (
                catalog.default_value is None
                and match.direction == "input"
                and match.default_value is not None
            ):
                saved = param_default_expr(match)
                if not (isinstance(saved, ast.Constant) and saved.value is None):
                    update["default_value"] = ast.unparse(saved)
        terminals.append(catalog.model_copy(update=update) if update else catalog)
    return PaneCheck(entry.model_copy(update={"terminals": terminals}), mismatches)
