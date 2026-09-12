"""Loads generated panel modules from a directory of co-located VIs.

Every generated module is named after its VI (``<vi>.py`` / ``<vi>_panel.py``),
so many VIs share one flat directory without name collisions (unlike the old
per-VI folders of identically-named files). Loading a panel is then just an
import by its unique module name, with the panels directory on ``sys.path`` so
its sibling imports (``from <vi> import ...``, ``from controls import ...``)
resolve. The one shared ``controls`` runtime is imported once and reused.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType


def _ensure_on_path(panels_dir: Path) -> None:
    p = str(Path(panels_dir).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)


def load_module(panels_dir: Path, stem: str) -> ModuleType:
    """Import ``panels_dir/<stem>.py`` by its (VI-unique) module name, with
    ``panels_dir`` on ``sys.path`` so its sibling imports resolve."""
    _ensure_on_path(panels_dir)
    return importlib.import_module(stem)


def load_build_panel(panels_dir: Path, panel_stem: str) -> Callable[[], None]:
    """Import ``panels_dir/<panel_stem>.py`` and return its ``build_panel``."""
    module = load_module(panels_dir, panel_stem)
    return module.build_panel  # type: ignore[no-any-return]
