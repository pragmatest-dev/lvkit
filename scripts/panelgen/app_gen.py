"""Generates ``app.py``: the thin sys.path bootstrap that builds the panel
and serves it. No generated logic here -- everything comes from panel.py.
"""

from __future__ import annotations


def build_app_module(title: str, port: int = 8080) -> str:
    return f'''"""Runnable panel for {title}.

Run it: uv run --with nicegui python app.py
Then open http://localhost:{port}
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nicegui import ui  # noqa: E402
from panel import build_panel  # noqa: E402

build_panel()

ui.run(title={title!r}, reload=False, port={port}, show=False)
'''
