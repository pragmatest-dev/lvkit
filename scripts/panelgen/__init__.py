"""VI -> NiceGUI front-panel generator.

Turns a parsed LabVIEW front panel + its block-diagram logic into three
separated, runnable Python files (``logic.py``, ``state.py``, ``panel.py``)
plus a thin ``app.py`` that serves them. See ``generate.generate_panel``.
"""

from __future__ import annotations

from .generate import generate_panel
from .loader import load_build_panel, load_module

__all__ = ["generate_panel", "load_build_panel", "load_module"]
