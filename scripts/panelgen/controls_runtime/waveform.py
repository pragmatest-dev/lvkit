"""The LabVIEW waveform/graph indicator analog.

Not currently wired up: no ``control_type`` in ``scripts/panelgen/strategies.py``
maps to it yet (there is no waveform-graph front-panel control type resolved
there), so no generated panel calls it. It stays here as the documented
analogue used by ``examples/waveform-nicegui`` (which single-sources this
runtime), awaiting a waveform-graph control type to wire it up to.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui


def waveform_indicator(
    state: Any, field: str, *, label: str = "", height: int = 160
) -> Callable[[], None]:
    """Read-only waveform / graph indicator: renders ``state.<field>`` (a list of
    y-values, or ``[x, y]`` pairs) as a line chart via ``ui.echart`` -- a
    maintained charting library (adopt-first; we own only the mapping). This is
    the LabVIEW waveform-graph analogue. Returns a refresh callable the Run
    handler calls after writing new samples.
    """

    def _data() -> list:
        vals = list(getattr(state, field) or [])
        if vals and isinstance(vals[0], list | tuple):
            return [list(pt) for pt in vals]  # already [x, y] pairs
        return [[i, v] for i, v in enumerate(vals)]  # y-only -> index on x

    if label:
        ui.label(label).classes(
            "text-xs font-semibold text-gray-500 shrink-0 truncate leading-none"
        )
    chart = (
        ui.echart(
            {
                "grid": {"left": 44, "right": 14, "top": 12, "bottom": 26},
                "xAxis": {"type": "value"},
                "yAxis": {"type": "value"},
                "series": [
                    {"type": "line", "showSymbol": False, "data": _data()}
                ],
                "animation": False,
            }
        )
        .classes("w-full")
        .style(f"height:{height}px")
    )

    def refresh() -> None:
        chart.options["series"][0]["data"] = _data()
        chart.update()

    return refresh
