from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _CPD_ARITH_SYMBOL, draw_split_box


@dataclass(frozen=True)
class CompoundArithGlyph:
    """The Compound Arithmetic node (``node_type == "cpdArith"``): a plain
    rectangle (sharp corners — block-diagram objects aren't rounded, unlike
    the borderless arithmetic triangle), split like LabVIEW's real glyph:

    - a narrower RIGHT cell holding the operator symbol, spanning the full
      node height (the output side);
    - a wider LEFT area divided into ``num_inputs`` horizontal rows by
      ``num_inputs - 1`` evenly-spaced lines — one row per input terminal,
      where each input wire lands.

    N-input growth is free — the glyph carries no intrinsic size, it scales
    to whatever heap ``bounds`` the node has."""

    operation: str
    num_inputs: int = 1
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        draw_split_box(
            backend,
            bounds,
            theme,
            symbol=_CPD_ARITH_SYMBOL.get(self.operation, "?"),
            num_cells=self.num_inputs,
            symbol_side="right",
            fill_attr=self.fill_attr,
            stroke_attr=self.stroke_attr,
            text_attr=self.text_attr,
        )
