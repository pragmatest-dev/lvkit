"""Connector-pane pattern geometry — load the bundled pattern table and
resolve a ``conId`` to laid-out terminal cells.

LabVIEW does not persist a connector pane's per-cell grid in the VI file; it
stores only the ``conId`` (the pattern number) and redraws the grid. This
module reads ``data/connector_pane_patterns.json`` — the clean-room table of
cell grids + per-cell terminal indices, read from the public LabVIEW-Wiki
pattern images (see that file's ``_meta.provenance``) — and turns a pattern
into normalized ``PaneCell`` rectangles the renderer/diff can draw directly.

Pure geometry: a cell knows its ``index`` and its rect, nothing about whether
the terminal is an input or an output (that comes from the VI's own
``ParsedConnectorPaneSlot.is_output``, not the pattern).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .._data import data_dir


@dataclass(frozen=True)
class PaneCell:
    """One connector-pane cell, laid out in a normalized unit square.

    ``x``/``y`` are the top-left corner; ``y`` grows DOWNWARD (screen/SVG
    convention). All four are fractions of the pane in ``[0, 1]``.
    """

    index: int
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class PanePattern:
    """A laid-out connector-pane pattern: its identity plus every cell."""

    con_id: int
    name: str
    terminal_count: int
    #: grid track counts, for proportional sizing (cols wide, rows tall)
    cols: int
    rows: int
    #: cells sorted by terminal index (0 .. terminal_count-1)
    cells: tuple[PaneCell, ...]

    def cell_by_index(self) -> dict[int, PaneCell]:
        return {c.index: c for c in self.cells}


def _cells_from_columns(columns: list[list[int]]) -> list[PaneCell]:
    """Lay out a column-form pattern. Each column is equal width (1/ncols);
    each column splits ITS OWN height equally over its cells, so a column with
    fewer cells gets taller cells — reproducing the vertically-centered look of
    the short inner columns in patterns like 4-2-2-4."""
    ncols = len(columns)
    col_w = 1.0 / ncols
    cells: list[PaneCell] = []
    for c, indices in enumerate(columns):
        nrows = len(indices)
        if nrows == 0:
            continue
        cell_h = 1.0 / nrows
        for r, index in enumerate(indices):
            cells.append(
                PaneCell(index=index, x=c * col_w, y=r * cell_h, w=col_w, h=cell_h)
            )
    return cells


def _cells_from_grid(grid: dict, raw_cells: list[dict]) -> list[PaneCell]:
    """Lay out a grid-form pattern (irregular, with column-spanning cells)."""
    cols = int(grid["cols"])
    rows = int(grid["rows"])
    col_w = 1.0 / cols
    row_h = 1.0 / rows
    cells: list[PaneCell] = []
    for rc in raw_cells:
        cells.append(
            PaneCell(
                index=int(rc["index"]),
                x=int(rc["col"]) * col_w,
                y=int(rc["row"]) * row_h,
                w=int(rc.get("colspan", 1)) * col_w,
                h=int(rc.get("rowspan", 1)) * row_h,
            )
        )
    return cells


@lru_cache(maxsize=1)
def _patterns() -> dict[int, PanePattern]:
    raw = json.loads(
        (data_dir() / "connector_pane_patterns.json").read_text(encoding="utf-8")
    )
    out: dict[int, PanePattern] = {}
    for con_id_str, spec in raw["patterns"].items():
        con_id = int(con_id_str)
        if "columns" in spec:
            columns = spec["columns"]
            cells = _cells_from_columns(columns)
            cols = len(columns)
            rows = max((len(c) for c in columns), default=1)
        else:
            cells = _cells_from_grid(spec["grid"], spec["cells"])
            cols = int(spec["grid"]["cols"])
            rows = int(spec["grid"]["rows"])
        cells.sort(key=lambda c: c.index)
        out[con_id] = PanePattern(
            con_id=con_id,
            name=spec["name"],
            terminal_count=int(spec["terminal_count"]),
            cols=cols,
            rows=rows,
            cells=tuple(cells),
        )
    return out


def get_pattern(con_id: int) -> PanePattern | None:
    """Return the laid-out pattern for ``con_id``, or ``None`` if the conId is
    not in the table (a rarer pattern the caller should handle gracefully)."""
    return _patterns().get(con_id)


def known_con_ids() -> frozenset[int]:
    """Every conId the bundled table encodes."""
    return frozenset(_patterns())
