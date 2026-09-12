"""VI -> NiceGUI front-panel generator.

Turns a parsed LabVIEW front panel + its block-diagram logic into two VI-named,
co-locatable Python files -- ``<vi>.py`` (pure headless logic) and
``<vi>_panel.py`` (the UI: State + build_panel + a __main__ runner) -- plus one
shared ``controls.py`` runtime per directory. See ``generate.generate_panel``.
"""

from __future__ import annotations

from .generate import PanelResult, generate_panel
from .loader import load_build_panel, load_module

__all__ = ["PanelResult", "generate_panel", "load_build_panel", "load_module"]
