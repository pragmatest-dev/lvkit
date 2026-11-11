"""Where each control goes on the panel: grouped into rows and columns from its
stored front-panel bounds, which give the order and the neighbours. The panel
stacks the groups with NiceGUI containers; no position or size is written."""

from __future__ import annotations

from collections.abc import Callable

from .kinds import PanelControl

# Rows top to bottom; each row holds columns left to right; each column holds
# its controls top to bottom.
PanelRows = list[list[list[PanelControl]]]


def drawn(controls: list[PanelControl]) -> list[PanelControl]:
    """The controls the panel draws, in order: all but the hidden ones."""
    return [c for c in controls if not c.hidden]


def _sweep(
    controls: list[PanelControl],
    start: Callable[[PanelControl], tuple[int, int]],
    low: int,
    high: int,
) -> list[list[PanelControl]]:
    """Split ``controls`` into the maximal runs, in the order of ``start``, whose
    ranges (``bounds[low]`` to ``bounds[high]``) overlap the run's running range:
    a control that starts before the run's end joins the run."""
    groups: list[list[PanelControl]] = []
    end = 0
    for control in sorted(controls, key=start):
        if groups and control.bounds[low] < end:
            groups[-1].append(control)
            end = max(end, control.bounds[high])
        else:
            groups.append([control])
            end = control.bounds[high]
    return groups


def arrange(controls: list[PanelControl]) -> PanelRows:
    """The rows of ``controls`` (the drawn ones). Bounds are (top, left, bottom,
    right). A row is a run of controls whose vertical ranges overlap, in order of
    (top, left); within a row, a column is a run whose horizontal ranges overlap,
    in order of (left, top), and its controls are stacked by top."""
    rows = _sweep(controls, lambda c: (c.bounds[0], c.bounds[1]), 0, 2)
    return [
        [
            sorted(column, key=lambda c: (c.bounds[0], c.bounds[1]))
            for column in _sweep(row, lambda c: (c.bounds[1], c.bounds[0]), 1, 3)
        ]
        for row in rows
    ]
