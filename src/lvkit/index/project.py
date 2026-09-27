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


def resolve_project_files(target: Path | str) -> tuple[Path, list[Path], list[Path]]:
    """Resolve ``target`` to ``(project_root, sorted vi_paths, sorted ctl_paths)``
    in ONE walk of the tree.

    - A directory IS the project root (the caller's chosen index scope) --
      every ``.vi`` and ``.ctl`` under it is enumerated, exactly like
      ``load_directory``.
    - A single file (``.lvproj``/``.lvlib``/``.lvclass``/``.vi``) resolves to
      its enclosing project root via ``cache_paths._project_root_for`` (the
      nearest ancestor holding a ``.lvkit/`` store, a ``.git`` root, or a
      ``*.lvproj`` — same rule the extraction cache uses), falling back to the
      file's own parent directory when no such ancestor exists.

    The ``.ctl`` controls are indexed alongside the VIs as facts about the
    controls, not as VIs.
    """
    resolved = Path(target).resolve()

    if resolved.is_dir():
        root = resolved
    else:
        root = _project_root_for(resolved) or resolved.parent

    vi_paths: list[Path] = []
    ctl_paths: list[Path] = []
    for p in root.rglob("*"):
        if p.suffix == ".vi":
            vi_paths.append(p)
        elif p.suffix == ".ctl" and p.is_file():
            ctl_paths.append(p)
    return root, sorted(vi_paths), sorted(ctl_paths)


def resolve_project(target: Path | str) -> tuple[Path, list[Path]]:
    """Resolve ``target`` to ``(project_root, sorted vi_paths)`` -- see
    :func:`resolve_project_files`."""
    root, vi_paths, _ = resolve_project_files(target)
    return root, vi_paths
