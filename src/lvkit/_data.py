"""Single source of truth for the bundled data directory.

lvkit ships JSON mappings (primitives, vi.lib, openg, drivers, enums,
error codes) as package data under ``src/lvkit/data/``. This module is
the one place where that location is computed — every resolver and
loader imports ``data_dir()`` instead of recomputing the path.

Centralizing this means a future move (rename, restructure, or
switch to importlib.resources) only touches one file.
"""

from __future__ import annotations

import json
from pathlib import Path

# The bundled data directory lives next to this module inside the
# installed package, so a single ``Path(__file__).parent`` works for
# both editable installs (``pip install -e``) and wheel installs.
_DATA_DIR = Path(__file__).parent / "data"


def data_dir() -> Path:
    """Return the bundled data directory path."""
    return _DATA_DIR


def load_primitives(path: Path | None = None) -> dict:
    """Load the primitive catalog as one merged ``{metadata, primitives,
    node_types}`` dict.

    The shipped catalog is split into per-NI-category files under
    ``data/primitives/`` (kept small so any one file loads into context);
    this merges them. ``path`` may be that directory, or a single JSON file
    (a project-local ``.lvkit/primitives.json`` override, or a legacy
    monolith). Returns empty sections when nothing is found.
    """
    if path is None:
        path = _DATA_DIR / "primitives"
    metadata: dict = {}
    primitives: dict = {}
    node_types: dict = {}
    files: list[Path]
    if path.is_dir():
        files = sorted(path.glob("*.json"))
    elif path.exists():
        files = [path]
    else:
        files = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        metadata.update(d.get("metadata", {}))
        primitives.update(d.get("primitives", {}))
        node_types.update(d.get("node_types", {}))
    return {"metadata": metadata, "primitives": primitives, "node_types": node_types}
