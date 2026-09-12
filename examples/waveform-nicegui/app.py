"""Runnable waveform panel.

    uv run --with nicegui python app.py    # then open http://localhost:8080

Single-sources the control library from scripts/panelgen/controls_runtime.py
(aliased as ``controls``) instead of copying it, so this committed example
never drifts from the generator's controls.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))  # local logic/state/panel
sys.path.insert(0, str(_HERE.parents[1] / "scripts" / "panelgen"))

import controls_runtime as controls  # noqa: E402

sys.modules["controls"] = controls  # panel.py does `from controls import ...`

from nicegui import ui  # noqa: E402
from panel import build_panel  # noqa: E402

build_panel()
ui.run(title="Waveform (NiceGUI)", reload=False, port=8080, show=False)
