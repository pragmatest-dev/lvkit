"""A file found in a folder a load has found files in stands only when no other
search folder has a file ending the same way."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.errors import AmbiguousVIReferenceError
from lvkit.graph.search_path import SearchPath
from lvkit.library_folders import FOUND_VI


def _touch(root: Path, rel: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _search(found: Path, *folders: Path) -> SearchPath:
    return SearchPath(
        entries=(FOUND_VI, *(str(f) for f in folders)),
        pseudo={},
        found_folders={found: None},
    )


def test_a_found_folder_file_that_another_folder_ties_is_ambiguous(
    tmp_path: Path,
) -> None:
    v1 = _touch(tmp_path, "v1/RPC Messages/x.ctl")
    v2 = _touch(tmp_path, "v2/RPC Messages/x.ctl")
    tail = ["v2", "Client", "RPC Messages", "x.ctl"]
    with pytest.raises(AmbiguousVIReferenceError) as caught:
        _search(v1.parent, tmp_path).find(tail, lambda _p: True)
    assert sorted(caught.value.candidates) == sorted([v1, v2])


def test_a_found_folder_file_no_other_folder_ties_is_found(tmp_path: Path) -> None:
    v1 = _touch(tmp_path, "v1/RPC Messages/x.ctl")
    _touch(tmp_path, "v2/Other/x.ctl")
    tail = ["Client", "RPC Messages", "x.ctl"]
    assert _search(v1.parent, tmp_path).find(tail, lambda _p: True) == v1


def test_a_found_folder_file_is_found_when_its_copy_is_the_same_file(
    tmp_path: Path,
) -> None:
    v1 = _touch(tmp_path, "v1/RPC Messages/x.ctl")
    tail = ["RPC Messages", "x.ctl"]
    assert _search(v1.parent, tmp_path).find(tail, lambda _p: True) == v1
