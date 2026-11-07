"""A free label on a front panel: a text object that is not a control's own
label."""

from __future__ import annotations

from ....parser.layout import Rect
from ....parser.models import ParsedFPLabel
from ...backend import Backend
from ...style import Theme
from .label import LABEL_SIZE

_LINE_HEIGHT = LABEL_SIZE + 2.0


def draw_free_label(
    label: ParsedFPLabel, bounds: Rect, backend: Backend, theme: Theme
) -> None:
    """The label's text from the top-left of its ``bounds``, one line per line
    of the text and each line broken at words to the width of ``bounds``. The
    text takes the label's literal colour when the file records one. Font sizes
    are not decoded, so the size is the one every front-panel label uses."""
    if label.hidden or not label.text:
        return
    colour = label.fg_color or theme.text
    x1, y1 = bounds[0], bounds[1]
    width = bounds[2] - bounds[0] - 2.0
    lines = [
        wrapped
        for line in label.text.splitlines()
        for wrapped in _wrap(line, width, backend)
    ]
    for index, line in enumerate(lines):
        backend.text(
            x1 + 1.0,
            y1 + 1.0 + LABEL_SIZE * 0.8 + index * _LINE_HEIGHT,
            line,
            LABEL_SIZE,
            fill=colour,
            anchor="start",
        )


def _wrap(line: str, width: float, backend: Backend) -> list[str]:
    """``line`` broken at spaces into lines that fit ``width``; a single word
    wider than that stays whole."""
    lines: list[str] = []
    current = ""
    for word in line.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and backend.measure_text(candidate, LABEL_SIZE) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines
