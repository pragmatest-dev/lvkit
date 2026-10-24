from __future__ import annotations

from dataclasses import dataclass

from ....graph.models import PrimitiveNode
from ....models import LVType
from ....parser.extfunc_prototype import ExtFuncThread
from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme, type_repr, wire_style

_TEXT_SIZE = 6.5  # a cell's type mnemonic; shrunk to fit a narrow cell
# The mark's bars as (left, right, top), in units of a node 40 wide; they stand on
# the base line at y = _MARK_BASE.
_MARK_BARS = ((8, 14, 9), (17, 23, 4), (26, 32, 6))
_MARK_BASE = 15


@dataclass(frozen=True)
class ExtFuncGlyphRow:
    """One row of a Call Library Function Node: its vertical span as a fraction
    of the node's height, and the type of each side's terminal (None when that
    side has no terminal). A ``fixed`` row is the path or error row."""

    top: float
    bottom: float
    fixed: bool
    input_type: LVType | None = None
    has_input: bool = False
    output_type: LVType | None = None
    has_output: bool = False


@dataclass(frozen=True)
class ExtFuncGlyph:
    """A Call Library Function Node (heap class ``extFunc``), banded orange when it
    runs in the UI thread and pale yellow when it runs in any thread: the path
    and error rows form a top band holding the node's mark; each parameter row below is
    cut in half by a vertical line, an input cell on the left and an output cell
    on the right, each a white cell carrying its type mnemonic (a grey, empty
    cell where the row has no terminal). The return value row has only its right
    cell. The path and error terminals are not drawn: they are wire attach
    points. Row heights come from the terminals'
    own geometry. The node draws no text beyond the type mnemonics -- its name is
    in the tooltip."""

    rows: tuple[ExtFuncGlyphRow, ...] = ()
    fill_attr: str = "ext_func_ui_fill"

    @classmethod
    def from_node(cls, node: PrimitiveNode) -> ExtFuncGlyph:
        """The glyph for ``node``: its drawn rows, with each parameter row's
        terminal types taken from the call's row terminals (the return value
        first, in the order the rows run down the node)."""
        types = {t.id: t.lv_type for t in node.terminals}
        call_rows = iter(node.ext_func.row_terminal_ids if node.ext_func else ())
        rows: list[ExtFuncGlyphRow] = []
        for top, bottom, has_input, has_output, fixed in node.ext_func_rows:
            input_id = output_id = None
            if not fixed:
                input_id, output_id = next(call_rows, (None, None))
            rows.append(
                ExtFuncGlyphRow(
                    top,
                    bottom,
                    fixed,
                    types.get(input_id) if input_id else None,
                    has_input,
                    types.get(output_id) if output_id else None,
                    has_output,
                )
            )
        thread = node.ext_func.thread if node.ext_func else None
        fill = (
            "ext_func_any_fill"
            if thread == ExtFuncThread.ANY_THREAD
            else "ext_func_ui_fill"
            if thread == ExtFuncThread.UI_THREAD
            else cls.fill_attr
        )
        return cls(tuple(rows), fill)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Drawn in LabVIEW's own pixels (1 unit = 1 px at the node's scale), over
        a black ground: a 1-unit border, the orange/yellow band over the path and
        error rows with the mark in it, and below it rows of 8 units (a 1-unit
        black line, then 7 units of cells) split by a 2-unit black divider. The
        bottom border line lies one unit below the last row."""
        x1, y1, x2, y2 = bounds
        width, height = x2 - x1, y2 - y1
        middle = (x1 + x2) / 2
        line = theme.ext_func_line
        backend.rect(x1, y1, x2, y2 + 1, fill=line, stroke="none")
        band = [r for r in self.rows if r.fixed]
        if band:
            band_bottom = y1 + band[-1].bottom * height
            backend.rect(
                x1 + 1,
                y1 + 1,
                x2 - 1,
                band_bottom,
                fill=getattr(theme, self.fill_attr),
                stroke="none",
            )
            self._draw_mark(backend, x1, y1, width / 40, line)
        for row in self.rows:
            if row.fixed:
                continue
            top, bottom = y1 + row.top * height + 1, y1 + row.bottom * height
            for side, cell_type, present in (
                ((x1 + 1, middle - 1), row.input_type, row.has_input),
                ((middle + 1, x2 - 1), row.output_type, row.has_output),
            ):
                self._draw_cell(
                    backend,
                    (side[0], top, side[1], bottom),
                    cell_type,
                    present,
                    theme,
                )

    @staticmethod
    def _draw_cell(
        backend: Backend,
        cell: Rect,
        lv_type: LVType | None,
        present: bool,
        theme: Theme,
    ) -> None:
        """A cell: white with its type's mnemonic in the type's wire colour; grey
        and empty when it has no terminal (or a Void one)."""
        x1, y1, x2, y2 = cell
        carries = present and lv_type is not None and lv_type.underlying_type != "Void"
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=theme.ext_func_cell if carries else theme.ext_func_empty_cell,
            stroke="none",
        )
        text = type_repr(lv_type) if carries else ""
        if not text:
            return
        scalar = lv_type.element_type if lv_type and lv_type.element_type else lv_type
        size = _TEXT_SIZE
        while size > 3.0 and backend.measure_text(text, size) > (x2 - x1) - 2:
            size -= 0.5
        backend.text(
            (x1 + x2) / 2,
            (y1 + y2) / 2 + size * 0.35,
            text,
            size,
            fill=wire_style(scalar, theme).color,
            bold=True,
        )

    @staticmethod
    def _draw_mark(
        backend: Backend, x1: float, y1: float, scale: float, fill: str
    ) -> None:
        """The node's mark in the band, inside x 4..36 and y 3..16 of a node 40
        wide: three bars of different heights standing on a base line."""
        for left, right, top in _MARK_BARS:
            backend.rect(
                x1 + left * scale,
                y1 + top,
                x1 + right * scale,
                y1 + _MARK_BASE,
                fill=fill,
                stroke="none",
            )
        backend.rect(
            x1 + 4 * scale,
            y1 + _MARK_BASE,
            x1 + 36 * scale,
            y1 + _MARK_BASE + 1,
            fill=fill,
            stroke="none",
        )
