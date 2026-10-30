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
    TD_FULL_TYPE,
    is_boolean_type_name,
    is_numeric_type_name,
    is_string_type_name,
)
from lvkit.models import LVTypeKind, Terminal, control_type_to_lvtype
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
                update["default_value"] = ast.unparse(param_default_expr(match))
        terminals.append(catalog.model_copy(update=update) if update else catalog)
    return PaneCheck(entry.model_copy(update={"terminals": terminals}), mismatches)
