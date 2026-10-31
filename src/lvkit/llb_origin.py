"""Where a VI extracted from an ``.llb`` came from.

An LLB is extracted into a cache folder whose sentinel file records the archive and
the members that could not be extracted. Anything that shows a user such a VI
labels it by the archive's path, never by the cache folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LLB_SENTINEL = ".extracted"


def read_llb_meta(cache_dir: Path) -> dict[str, Any] | None:
    """What the LLB's cache folder records about its extraction (its sentinel
    file): the archive it came from and the members that could not be extracted.
    None when the folder is not an lvkit LLB extraction."""
    try:
        meta = json.loads((cache_dir / LLB_SENTINEL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and "source" in meta else None


def llb_container_of(vi_path: Path) -> Path | None:
    """The ``.llb`` a VI file was extracted from, when it sits in an LLB's cache
    folder (the folder's sentinel records its archive), else None."""
    meta = read_llb_meta(vi_path.parent)
    return Path(meta["source"]) if meta is not None else None
