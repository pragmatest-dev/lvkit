"""A free label on a front panel: a text object that is not a control's own
label."""

from __future__ import annotations

from ....parser.layout import Rect
from ....parser.models import ParsedFPLabel
from ...backend import Backend
from ...style import Theme
from ...text_style import TextStyle, draw_styled_text, text_style

_LINE_GAP = 2.0


def draw_free_label(
    label: ParsedFPLabel, bounds: Rect, backend: Backend, theme: Theme
) -> None:
    """The label's text from the top-left of its ``bounds``, one line per line
    of the text and each line broken at words to the width of ``bounds``. The
    text is drawn in the font and colour of the label's first run (the whole
    text, when it has several), else the label's own colour, else the theme's."""
    if label.hidden or not label.text:
        return
    run_colour = label.font_runs[0].color if label.font_runs else None
    style = text_style(label.font, run_colour or label.fg_color)
    x1, y1 = bounds[0], bounds[1]
    width = bounds[2] - bounds[0] - 2.0
    lines = [
        wrapped
        for line in label.text.splitlines()
        for wrapped in _wrap(line, width, backend, style)
    ]
    for index, line in enumerate(lines):
        draw_styled_text(
            backend,
            x1 + 1.0,
            y1 + 1.0 + style.size * 0.8 + index * (style.size + _LINE_GAP),
            line,
            style,
            theme.text,
        )


def _wrap(
    line: str, width: float, backend: Backend, style: TextStyle
) -> list[str]:
    """``line`` broken at spaces into lines that fit ``width``; a single word
    wider than that stays whole."""
    lines: list[str] = []
    current = ""
    for word in line.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and backend.measure_text(candidate, style.size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines
