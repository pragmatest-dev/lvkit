"""Single source of truth for the bundled data directory.

lvpy ships JSON mappings (primitives, vi.lib, openg, drivers, enums,
error codes) as package data under ``src/lvpy/data/``. This module is
the one place where that location is computed — every resolver and
loader imports ``data_dir()`` instead of recomputing the path.

Centralizing this means a future move (rename, restructure, or
switch to importlib.resources) only touches one file.
"""

from __future__ import annotations

from pathlib import Path

# The bundled data directory lives next to this module inside the
# installed package, so a single ``Path(__file__).parent`` works for
# both editable installs (``pip install -e``) and wheel installs.
_DATA_DIR = Path(__file__).parent / "data"


def data_dir() -> Path:
    """Return the bundled data directory path."""
    return _DATA_DIR
