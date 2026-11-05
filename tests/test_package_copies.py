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


def _module(
    copies: PackageCopies,
    vi: str,
    recorded: list[str],
    below: tuple[str, ...],
    source: Path,
) -> None:
    """One module that names ``source`` beside its VI and is written."""
    module = copies.module()
    module.register(vi, recorded, below, source)
    module.commit()


def _file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The test folder is the home folder: no folder above it is searched."""
    home = tmp_path.resolve()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


def test_two_copies_of_a_vi_placed_by_one_recorded_path_stop_generation(
    tmp_path: Path,
) -> None:
    """Two unmarked trees: both files are labelled ``x.dll`` (each its own
    folder's), so the message numbers them in the order they were kept."""
    p = str(tmp_path / "copy1" / "A" / "P.vi")
    q = str(tmp_path / "copy2" / "A" / "Q.vi")
    copies, _ = _copies(tmp_path, {p: "<vilib>/A/P.vi", q: "<vilib>/A/Q.vi"}, p, q)
    first = _file(tmp_path / "copy1" / "A" / "x.dll", b"one")
    second = _file(tmp_path / "copy2" / "A" / "x.dll", b"two")
    _module(copies, p, ["", "x.dll"], ("x.dll",), first)
    _module(copies, q, ["", "x.dll"], ("x.dll",), second)
    with pytest.raises(CopyCollisionError) as caught:
        copies.check()
    message = str(caught.value)
    assert "'vilib/a/x.dll'" in message
    assert "P.vi" in message and "Q.vi" in message
    assert "'x.dll' (1st copy, named by 'P.vi')" in message
    assert "'x.dll' (2nd copy, named by 'Q.vi')" in message
    assert str(tmp_path) not in message and tmp_path.name not in message


def test_two_folders_whose_names_transform_alike_stop_generation(
    tmp_path: Path,
) -> None:
    vi = str(tmp_path / "proj" / "V.vi")
    copies, _ = _copies(tmp_path, {}, vi)
    first = _file(tmp_path / "proj" / "My Libs" / "x.dll", b"one")
    second = _file(tmp_path / "proj" / "my_libs" / "x.dll", b"two")
    _module(copies, vi, ["", "My Libs", "x.dll"], ("x.dll",), first)
    _module(copies, vi, ["", "my_libs", "x.dll"], ("x.dll",), second)
    with pytest.raises(CopyCollisionError, match="'my_libs/x.dll'"):
        copies.check()


def test_the_same_file_named_twice_is_copied_once(tmp_path: Path) -> None:
    p = str(tmp_path / "proj" / "A" / "P.vi")
    q = str(tmp_path / "proj" / "A" / "Q.vi")
    copies, out = _copies(tmp_path, {}, p, q)
    library = _file(tmp_path / "proj" / "A" / "x.dll", b"one")
    _module(copies, p, ["", "x.dll"], ("x.dll",), library)
    _module(copies, q, ["", "x.dll"], ("x.dll",), library)
    copies.check()
    copies.write()
    assert (out / "a" / "x.dll").read_bytes() == b"one"


def test_places_that_differ_only_in_case_are_one_place(tmp_path: Path) -> None:
    """Two different files (in two folders) whose places in the package differ
    only in case are one place on a Windows or macOS file system: a clash."""
    p = str(tmp_path / "c1" / "A" / "P.vi")
    q = str(tmp_path / "c2" / "A" / "Q.vi")
    copies, _ = _copies(tmp_path, {p: "<vilib>/A/P.vi", q: "<vilib>/A/Q.vi"}, p, q)
    upper = _file(tmp_path / "c1" / "A" / "Lib.dll", b"one")
    lower = _file(tmp_path / "c2" / "A" / "lib.dll", b"two")
    _module(copies, p, ["", "Lib.dll"], ("Lib.dll",), upper)
    _module(copies, q, ["", "lib.dll"], ("lib.dll",), lower)
    with pytest.raises(CopyCollisionError) as caught:
        copies.check()
    assert "'Lib.dll'" in str(caught.value) and "'lib.dll'" in str(caught.value)


def test_one_file_named_two_ways_is_one_copy(tmp_path: Path) -> None:
    """The same file reached by two spellings (a link here, a name in another
    case on a file system that ignores case) is copied once, no clash."""
    vi = str(tmp_path / "proj" / "V.vi")
    copies, out = _copies(tmp_path, {}, vi)
    library = _file(tmp_path / "proj" / "x.dll", b"one")
    other = tmp_path / "proj" / "y.dll"
    try:
        other.symlink_to(library)
    except OSError:
        pytest.skip("this file system makes no links")
    _module(copies, vi, ["", "x.dll"], ("x.dll",), library)
    module = copies.module()
    module.register(vi, ["", "x.dll"], ("x.dll",), other)
    module.commit()
    copies.check()


def test_a_vi_lib_file_and_a_project_copy_are_named_by_their_labels(
    tmp_path: Path,
) -> None:
    """A clash names each file by the label every view gives it: the vi.lib file
    by its ``<vilib>`` path, the project's by its project-relative path; never
    the folders above them."""
    vilib = tmp_path / "opt" / "vi.lib"
    proj = tmp_path / "home" / "someone" / "proj"
    (proj / ".git").mkdir(parents=True)
    p = str(vilib / "A" / "P.vi")
    q = str(proj / "A" / "Q.vi")
    graph = InMemoryVIGraph()
    graph.set_library_roots(vilib_root=vilib)
    for key in (p, q):
        graph._vi_nodes[key] = set()  # noqa: SLF001
    layout = ModuleLayout(graph, [p, q], proj, recorded={q: "<vilib>/A/Q.vi"})
    copies = PackageCopies(graph, layout, tmp_path / "out")
    _module(copies, p, ["", "x.dll"], ("x.dll",), _file(vilib / "A" / "x.dll", b"1"))
    _module(copies, q, ["", "x.dll"], ("x.dll",), _file(proj / "A" / "x.dll", b"2"))
    with pytest.raises(CopyCollisionError) as caught:
        copies.check()
    message = str(caught.value)
    assert "'<vilib>/A/x.dll'" in message and "'A/x.dll'" in message
    assert "someone" not in message and "opt" not in message
    assert str(tmp_path) not in message


def test_a_vi_and_library_directly_in_the_home_folder_show_no_home_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly in the home folder, or in a folder beside it (another user's home,
    even one kept in git), with no project marker: the module is
    ``external.helper``, the library ``external/x.dll``, and a clash shows no
    home folder name."""
    users = tmp_path.resolve() / "home"
    home, beside = users / "me", users / "bob"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (beside / ".git").mkdir(parents=True)  # another user's home: never a project
    vi = str(home / "Helper.vi")
    other = str(beside / "Other.vi")
    copies, _ = _copies(tmp_path, {}, vi, other)
    assert copies.layout.places[vi].parts == ("external", "helper")
    assert copies.layout.places[other].parts == ("external", "other")
    _module(copies, vi, ["", "x.dll"], ("x.dll",), _file(home / "x.dll", b"1"))
    _module(copies, other, ["", "x.dll"], ("x.dll",), _file(beside / "x.dll", b"2"))
    with pytest.raises(CopyCollisionError) as caught:
        copies.check()
    message = str(caught.value)
    assert "(1st copy, named by 'Helper.vi')" in message
    assert "(2nd copy, named by 'Other.vi')" in message
    assert "'external/x.dll'" in message
    for name in ("me", "bob", str(users)):
        assert f"{name}/" not in message and f"'{name}" not in message


def test_a_module_that_is_not_written_copies_nothing(tmp_path: Path) -> None:
    """What a module registers is kept only when the module is written: a
    module that fails copies no file and makes no clash."""
    vi = str(tmp_path / "proj" / "V.vi")
    copies, out = _copies(tmp_path, {}, vi)
    kept = _file(tmp_path / "proj" / "a" / "x.dll", b"one")
    failed = _file(tmp_path / "proj" / "b" / "x.dll", b"two")
    _module(copies, vi, ["", "a", "x.dll"], ("x.dll",), kept)
    # A module that fails is never committed: what it registered is dropped.
    copies.module().register(vi, ["", "a", "x.dll"], ("x.dll",), failed)
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
