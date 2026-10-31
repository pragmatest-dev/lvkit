"""Where a VI extracted from an ``.llb`` came from.

A VI inside an LLB is the file at ``<llb path>/<member>``; its bytes are read from
a cache folder the archive is extracted into, whose sentinel file records the
archive and the members that could not be extracted. Anything that shows a user
such a VI labels it by the archive's path, never by the cache folder.
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
    """The ``.llb`` a VI is a member of: the archive its path goes through
    (``<llb path>/<member>``, the member's identity), or the one its extracted
    copy came from (the copy's cache folder sentinel records its archive); else
    None."""
    parent = vi_path.parent
    if parent.suffix.lower() == ".llb" and parent.is_file():
        return parent
    meta = read_llb_meta(parent)
    return Path(meta["source"]) if meta is not None else None
