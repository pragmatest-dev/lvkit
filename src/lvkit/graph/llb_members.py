"""A VI inside a ``.llb`` archive is the file at ``<llb path>/<member>``: the path
LabVIEW records for it, as if the archive were a folder. That path is its
identity (its graph key, the folder its relative recorded paths start from, its
placement); its bytes are in the member the archive is extracted to
(``extractor.extract_llb``), which is never its identity."""

from __future__ import annotations

from pathlib import Path

from ..extractor import extract_llb


def llb_member_copy(path: Path) -> Path | None:
    """The extracted copy of the ``.llb`` member ``path`` names (a path through
    a ``.llb`` file), None when ``path`` goes through no ``.llb`` file, the
    archive cannot be extracted or has no such member."""
    parts = path.parts
    for i, part in enumerate(parts[:-1]):
        if not part.lower().endswith(".llb"):
            continue
        llb_path = Path(*parts[: i + 1])
        if not llb_path.is_file():
            continue
        try:
            cache_dir = extract_llb(llb_path)
        except RuntimeError:
            return None
        member = cache_dir / Path(*parts[i + 1 :])
        return member if member.is_file() else None
    return None


def vi_bytes_file(path: Path) -> Path:
    """The file holding the bytes of the VI at ``path``: the file itself, or
    the extracted copy of a ``.llb`` member."""
    if path.exists():
        return path
    return llb_member_copy(path) or path


def file_is_present(path: Path) -> bool:
    """Whether the VI at ``path`` can be read: a file, or a ``.llb`` member."""
    return path.is_file() or llb_member_copy(path) is not None
