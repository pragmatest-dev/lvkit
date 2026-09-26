"""A top-level control's synthesized caption."""

from __future__ import annotations

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...glyph import fit_label
from ...style import Theme


def shown_text(ctrl: ParsedFPControl) -> str | None:
    """The one text a control shows above itself: its label when the label is
    visible, otherwise its caption when the caption is visible, otherwise
    nothing. The two are alternatives (no control shows both), and a hidden
    label draws no text at all."""
    if ctrl.label_visible:
        return ctrl.name
    if ctrl.caption_visible:
        return ctrl.caption
    return None


def draw_label(
    ctrl: ParsedFPControl, bounds: Rect, backend: Backend, theme: Theme
) -> None:
    """A TOP-LEVEL control's own caption, synthesized just above its box.

    Only called once per top-level control (see ``compose.draw_front_panel``)
    -- a nested cluster FIELD's caption is drawn by ``ClusterConstantGlyph``
    itself, at the field's own real heap rect, so it is never drawn twice."""
    name = shown_text(ctrl)
    if not name:
        return
    x1, y1, x2, _y2 = bounds
    label_y2 = y1 - 2
    text = fit_label(name, x2 - x1, backend, 11.0)
    backend.text((x1 + x2) / 2, label_y2 - 3, text, 11.0, fill=theme.text)
