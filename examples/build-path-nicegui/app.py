"""Runnable vertical: OpenG 'Build Path' VI -> Python logic + NiceGUI panel.

Run it:  uv run --with nicegui python examples/build-path-nicegui/app.py
Then open http://localhost:8080
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nicegui import ui  # noqa: E402
from panel import build_panel  # noqa: E402

build_panel()

ui.run(title="Build Path — lvkit demo", reload=False, port=8080, show=False)
