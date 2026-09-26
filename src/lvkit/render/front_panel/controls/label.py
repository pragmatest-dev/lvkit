"""A top-level control's synthesized caption."""

from __future__ import annotations

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...glyph import fit_label
from ...style import Theme
from .base import find_part, part_rect_of

# Font size of every front-panel label: a control's own caption and each cluster
# field's name. Chosen against the issue #101 reference screenshot.
LABEL_SIZE = 11.0

_LABEL_PART_ID = 16
_CAPTION_PART_ID = 82


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


def _label_part_id(ctrl: ParsedFPControl) -> int:
    """The part whose text is shown: the label (16) when it is the visible
    text, else the caption (82)."""
    return _LABEL_PART_ID if ctrl.label_visible else _CAPTION_PART_ID


def draw_label(
    ctrl: ParsedFPControl,
    control_box: Rect,
    value_box: Rect,
    backend: Backend,
    theme: Theme,
) -> None:
    """A TOP-LEVEL control's shown text. It draws at the text part's own rect
    (relative to the control's ``control_box`` origin) -- where LabVIEW puts it,
    inside the control's box. A control with no such part gets the text
    synthesized just above its ``value_box``.

    Only called once per top-level control (see ``compose.draw_front_panel``)
    -- a nested cluster FIELD's caption is drawn by ``ClusterConstantGlyph``
    itself, at the field's own real heap rect, so it is never drawn twice."""
    name = shown_text(ctrl)
    if not name:
        return
    part = find_part(ctrl, _label_part_id(ctrl))
    if part is not None:
        ox, oy = control_box[0], control_box[1]
        x1, y1, _x2, y2 = part_rect_of(part)
        backend.text(
            ox + x1 + 1.0,
            oy + (y1 + y2) / 2 + LABEL_SIZE * 0.34,
            name,
            LABEL_SIZE,
            fill=theme.text,
            anchor="start",
        )
        return
    vx1, vy1, vx2, _vy2 = value_box
    label_y2 = vy1 - 2
    text = fit_label(name, vx2 - vx1, backend, LABEL_SIZE)
    backend.text((vx1 + vx2) / 2, label_y2 - 3, text, LABEL_SIZE, fill=theme.text)
