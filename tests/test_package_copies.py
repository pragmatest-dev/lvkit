"""Each place in a generated package holds one copied file: the same file named
twice is copied once, two different files for one place stop generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.recorded_paths import walk_relative
from lvkit.module_layout import ModuleLayout
from lvkit.package_copies import CopyCollisionError, PackageCopies


def _copies(
    tmp_path: Path, recorded: dict[str, str], *keys: str
) -> tuple[PackageCopies, Path]:
    graph = InMemoryVIGraph()
    for key in keys:
        graph._vi_nodes[key] = set()  # noqa: SLF001
    layout = ModuleLayout(graph, list(keys), tmp_path / "proj", recorded=recorded)
    out = tmp_path / "out"
    return PackageCopies(graph, layout, out), out


def _file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_two_copies_of_a_vi_placed_by_one_recorded_path_stop_generation(
    tmp_path: Path,
) -> None:
    p = str(tmp_path / "copy1" / "A" / "P.vi")
    q = str(tmp_path / "copy2" / "A" / "Q.vi")
    copies, _ = _copies(tmp_path, {p: "<vilib>/A/P.vi", q: "<vilib>/A/Q.vi"}, p, q)
    first = _file(tmp_path / "copy1" / "A" / "x.dll", b"one")
    second = _file(tmp_path / "copy2" / "A" / "x.dll", b"two")
    copies.add(p, ["", "x.dll"], ("x.dll",), first)
    copies.add(q, ["", "x.dll"], ("x.dll",), second)
    with pytest.raises(CopyCollisionError) as caught:
        copies.check()
    message = str(caught.value)
    assert "'vilib/a/x.dll'" in message
    assert "P.vi" in message and "Q.vi" in message
    assert str(tmp_path) not in message


def test_two_folders_whose_names_transform_alike_stop_generation(
    tmp_path: Path,
) -> None:
    vi = str(tmp_path / "proj" / "V.vi")
    copies, _ = _copies(tmp_path, {}, vi)
    first = _file(tmp_path / "proj" / "My Libs" / "x.dll", b"one")
    second = _file(tmp_path / "proj" / "my_libs" / "x.dll", b"two")
    copies.add(vi, ["", "My Libs", "x.dll"], ("x.dll",), first)
    copies.add(vi, ["", "my_libs", "x.dll"], ("x.dll",), second)
    with pytest.raises(CopyCollisionError, match="'my_libs/x.dll'"):
        copies.check()


def test_the_same_file_named_twice_is_copied_once(tmp_path: Path) -> None:
    p = str(tmp_path / "proj" / "A" / "P.vi")
    q = str(tmp_path / "proj" / "A" / "Q.vi")
    copies, out = _copies(tmp_path, {}, p, q)
    library = _file(tmp_path / "proj" / "A" / "x.dll", b"one")
    copies.add(p, ["", "x.dll"], ("x.dll",), library)
    copies.add(q, ["", "x.dll"], ("x.dll",), library)
    copies.check()
    copies.write()
    assert (out / "a" / "x.dll").read_bytes() == b"one"


@pytest.mark.parametrize(
    ("recorded", "walked"),
    [
        (["", "x.dll"], ["d", "e", "x.dll"]),
        (["", "a", "..", "b", "x.dll"], ["d", "e", "b", "x.dll"]),
        (["", "", "x.dll"], ["d", "x.dll"]),
        (["", ".", "x.dll"], ["d", "e", "x.dll"]),
        (["", "..", "..", "x.dll"], None),  # above the folder the walk keeps
        (["x.dll"], None),  # not relative to the file
    ],
)
def test_a_relative_path_walks_from_its_file(
    recorded: list[str], walked: list[str] | None
) -> None:
    assert walk_relative(["d", "e", "F.vi"], recorded, keep=1) == walked
