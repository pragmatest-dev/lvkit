"""Resolve a load target to a project root + its VI file list.

The project root is the index's identity: the store (``store.py``) is keyed
by ``_slug(project_root)``, and a full index build walks every ``.vi`` under
this root (``build.py``). A single-file target (``.lvproj``/``.lvlib``/
``.lvclass``/``.vi``) is NOT indexed alone — its enclosing project is, so the
index always covers the whole repo an agent is working in.
"""

from __future__ import annotations

from pathlib import Path

from ..cache_paths import _project_root_for


def resolve_project(target: Path | str) -> tuple[Path, list[Path]]:
    """Resolve ``target`` to ``(project_root, sorted vi_paths)``.

    - A directory IS the project root (the caller's chosen index scope) —
      every ``.vi`` under it is enumerated, exactly like ``load_directory``.
    - A single file (``.lvproj``/``.lvlib``/``.lvclass``/``.vi``) resolves to
      its enclosing project root via ``cache_paths._project_root_for`` (the
      nearest ancestor holding a ``.lvkit/`` store, a ``.git`` root, or a
      ``*.lvproj`` — same rule the extraction cache uses), falling back to the
      file's own parent directory when no such ancestor exists.
    """
    resolved = Path(target).resolve()

    if resolved.is_dir():
        root = resolved
    else:
        root = _project_root_for(resolved) or resolved.parent

    vi_paths = sorted(root.rglob("*.vi"))
    return root, vi_paths
