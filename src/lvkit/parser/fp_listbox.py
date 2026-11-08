"""A front-panel listbox's own table settings, read from its ``ddo``: how many
rows are frozen headers, the column headers and their widths, and the flags.
Format facts: ``docs/_internal/design/labview-binary-format.md`` "Front panel
structure"."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import ParsedFPListbox
from .utils import heap_color, int_or_none, safe_int, strip_surrounding_quotes

# The attribute entry that applies to every cell: row and column -2.
_ALL_CELLS = -2
_DEFAULT_COLUMN_WIDTH = 51


def read_listbox(ddo: ET.Element) -> ParsedFPListbox:
    grid = ddo.find("partsList/SL__arrayElement[@class='grid']")
    headers = tuple(
        strip_surrounding_quotes(item.text or "")
        for item in ddo.findall("columnHeaders/SL__arrayElement")
    )
    sizes = [
        int_or_none(item.text)
        for item in ddo.findall(
            "baseListboxItemStrings/multiDimArraySizes/SL__arrayElement"
        )
    ]
    columns = sizes[1] if len(sizes) == 2 and sizes[1] is not None else len(headers)
    return ParsedFPListbox(
        frozen_rows=safe_int(grid.find("numFrozenRows")) if grid is not None else 0,
        label_pos_row=safe_int(grid.find("labelPosRow")) if grid is not None else 0,
        column_headers=headers,
        column_widths=_column_widths(ddo, columns),
        flags=safe_int(ddo.find("listboxFlags")),
        border_color=heap_color(ddo.findtext("borderColor")),
    )


def _column_widths(ddo: ET.Element, columns: int) -> tuple[int, ...]:
    """Each column's width: its own attribute entry's ``width`` (row -2, column
    ``n``), else the all-cells entry's width."""
    default = _DEFAULT_COLUMN_WIDTH
    own: dict[int, int] = {}
    for attr in ddo.findall("attributeList/SL__arrayElement"):
        row = int_or_none(attr.findtext("cellPosRow"))
        col = int_or_none(attr.findtext("cellPosCol"))
        width = int_or_none(attr.findtext("width"))
        if row != _ALL_CELLS or col is None or width is None:
            continue
        if col == _ALL_CELLS:
            default = width
        else:
            own[col] = width
    return tuple(own.get(col, default) for col in range(columns))
