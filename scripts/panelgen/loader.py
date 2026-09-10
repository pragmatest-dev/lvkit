"""Loads one generated panel folder's modules in isolation from every other
panel folder loaded in the same process.

Each panel folder (see ``generate.generate_panel``) is a flat, non-package
directory of plainly-named modules -- ``logic.py``, ``state.py``,
``panel.py``, optional ``_dep_*.py`` siblings (see ``logic_gen``'s module
docstring) -- that import each other by bare name (``from logic import
...``, ``from state import ...``). A gallery that loads many VIs' panel
folders into ONE process collides on those bare names in ``sys.modules``, so
this evicts them before AND after each load: an import resolves against the
one panel directory prepended to ``sys.path`` for the duration of the
import, then the plain names are scrubbed so the next panel folder's
same-named modules don't hit a stale cache. The callable returned by
``load_build_panel`` is unaffected by the post-load scrub -- it already
carries what it needs in its own ``__globals__`` (the now-orphaned module's
``__dict__``, kept alive by that reference).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

_PLAIN_NAMES = ("logic", "state", "panel")


def _evict(panel_dir: Path) -> None:
    dep_stems = {p.stem for p in panel_dir.glob("_dep_*.py")}
    for name in list(sys.modules):
        if name in _PLAIN_NAMES or name in dep_stems:
            del sys.modules[name]


def load_module(panel_dir: Path, stem: str) -> ModuleType:
    """Import ``panel_dir/<stem>.py`` under its bare module name, with
    ``panel_dir`` on ``sys.path`` just long enough to resolve its own
    bare-name sibling imports, then scrub every bare name this panel folder
    could have registered so the next folder starts clean."""
    panel_dir = Path(panel_dir)
    module_path = panel_dir / f"{stem}.py"
    _evict(panel_dir)
    sys.path.insert(0, str(panel_dir))
    try:
        spec = importlib.util.spec_from_file_location(stem, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[stem] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(panel_dir))
        _evict(panel_dir)


def load_build_panel(panel_dir: Path) -> Callable[[], None]:
    """Import ``panel_dir/panel.py`` in isolation and return its
    ``build_panel`` callable."""
    module = load_module(panel_dir, "panel")
    return module.build_panel  # type: ignore[no-any-return]
