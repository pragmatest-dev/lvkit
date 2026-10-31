"""A dependency is found only at the path its caller records for it.

A relative path is joined onto the caller's folder, a ``<vilib>``/``<userlib>``/
``<instrlib>`` path onto the folder configured for that root, an absolute path
is used as it is. Nothing is looked up by file name: a file of the same name
elsewhere is never taken in its place, and a dependency not at its recorded path
is missing and keeps that path. Hermetic: VIs are placeholder files read through
a fake extractor (``tests.test_call_identity._Tree``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.node_kinds import EdgeRel
from lvkit.parser.models import ParsedDependencyRef
from lvkit.pipeline import generate_python
from lvkit.structure import member_url_paths
from tests.test_call_identity import _call, _key, _static, _Tree

_USERLIB_CALL = ["<userlib>", "_OpenG.lib", "string", "Trim__ogtk.vi"]


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    # A relative call to a file at its recorded path.
    built.vi("Sub/callee.vi", "callee.vi", [])
    built.vi(
        "caller_rel.vi",
        "caller_rel.vi",
        [],
        [_static(["callee.vi"], ["", "Sub", "callee.vi"])],
    )
    # A <userlib> call; the file sits under a folder named as user.lib.
    built.vi("user.lib/_OpenG.lib/string/Trim__ogtk.vi", "Trim__ogtk.vi", [])
    built.vi(
        "caller_userlib.vi",
        "caller_userlib.vi",
        [],
        [_static(["Trim__ogtk.vi"], _USERLIB_CALL)],
    )
    # A call whose recorded path holds nothing; a file of its name and owners
    # exists elsewhere.
    built.vi("Elsewhere/moved.vi", "moved.vi", [])
    built.vi(
        "caller_moved.vi",
        "caller_moved.vi",
        [],
        [_static(["moved.vi"], ["", "Recorded", "moved.vi"])],
    )
    return built


def _load(
    tree: _Tree, name: str, userlib: Path | None = None
) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    if userlib is not None:
        graph.set_library_roots(userlib_root=userlib)
    key = graph.load_vi(tree.root / name, LoadMode.MINIMAL)
    assert key is not None
    return graph, key


def test_a_relative_recorded_path_is_joined_onto_the_callers_folder(
    tree: _Tree,
) -> None:
    graph, key = _load(tree, "caller_rel.vi")
    call = _call(graph, key)
    assert call.callee_key == _key(tree, "Sub/callee.vi")
    assert graph.loaded_callee_key(call) == _key(tree, "Sub/callee.vi")


def test_a_userlib_path_is_joined_onto_the_userlib_root(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_userlib.vi", userlib=tree.root / "user.lib")
    target = _key(tree, "user.lib/_OpenG.lib/string/Trim__ogtk.vi")
    assert graph.loaded_callee_key(_call(graph, key)) == target


def test_a_same_named_file_elsewhere_is_not_taken(tree: _Tree) -> None:
    graph, key = _load(tree, "caller_moved.vi")
    call = _call(graph, key)
    recorded = _key(tree, "Recorded/moved.vi")
    assert call.callee_key == recorded
    assert graph.loaded_callee_key(call) is None
    assert graph.is_stub(recorded)
    assert _key(tree, "Elsewhere/moved.vi") not in graph.list_vis()


def test_a_userlib_call_with_no_root_is_missing_and_keeps_its_recorded_path(
    tree: _Tree,
) -> None:
    """With no user.lib named, the file under ``user.lib/`` beside the caller is
    not taken: the call is missing, shown by its ``<userlib>`` path."""
    graph, key = _load(tree, "caller_userlib.vi")
    assert graph.loaded_callee_key(_call(graph, key)) is None
    (stub,) = [d for d in graph.get_vi_dependencies(key) if graph.is_stub(d)]
    assert graph.recorded_path(stub) == "/".join(_USERLIB_CALL)


def test_generate_names_the_missing_call_by_its_recorded_path(
    tree: _Tree, tmp_path: Path
) -> None:
    result = generate_python(tree.root / "caller_userlib.vi", tmp_path / "gen")
    (gap,) = [g for g in result["gaps"] if g.kind == "missing_vi"]
    assert "(<userlib>/_OpenG.lib/string/Trim__ogtk.vi)" in gap.reason
    assert str(tree.root) not in gap.reason and str(tmp_path) not in gap.reason


# ── absolute recorded paths ───────────────────────────────────────────────────


def _absolute(tokens: list[str]) -> ParsedDependencyRef:
    return ParsedDependencyRef(name=tokens[-1], path_tokens=tokens, path_type=0)


def test_an_absolute_path_from_the_drive_root_is_used_as_it_is() -> None:
    ref = _absolute(["", "Program Files", "Pico", "ps4000a.dll"])
    expected = Path(Path.cwd().anchor, "Program Files", "Pico", "ps4000a.dll")
    assert ref.absolute_path() == expected


@pytest.mark.skipif(sys.platform == "win32", reason="drives exist on Windows")
def test_a_drive_letter_path_names_no_file_off_windows() -> None:
    assert _absolute(["C", "Users", "x.vi"]).absolute_path() is None


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters are Windows")
def test_a_drive_letter_path_is_that_drive_on_windows() -> None:
    ref = _absolute(["C", "Users", "x.vi"])
    assert ref.absolute_path() == Path("C:\\Users\\x.vi")


def test_relative_and_symbolic_paths_are_not_absolute() -> None:
    assert (
        ParsedDependencyRef("x.vi", ["", "x.vi"], path_type=1).absolute_path() is None
    )
    assert _absolute(["<vilib>", "Utility", "x.vi"]).absolute_path() is None


def test_an_absolute_recorded_path_that_exists_is_loaded(tree: _Tree) -> None:
    target = tree.vi("Abs/target.vi", "target.vi", [])
    caller = tree.vi(
        "caller_abs.vi",
        "caller_abs.vi",
        [],
        [_static(["target.vi"], ["", *target.resolve().parts[1:]])],
    )
    main = tree.extract(caller)[2]
    main.write_text(main.read_text().replace('TpVal="1"', 'TpVal="0"'))
    graph, key = _load(tree, "caller_abs.vi")
    assert graph.loaded_callee_key(_call(graph, key)) == str(target.resolve())


# ── the file at a recorded path: .llb, member beside its class, letter case ───


def test_a_member_recorded_by_its_class_path_is_the_file_beside_the_class(
    tmp_path: Path,
) -> None:
    cls = tmp_path / "Cls" / "Cls.lvclass"
    cls.parent.mkdir()
    cls.write_text("<LVClass/>")
    graph = InMemoryVIGraph()
    assert graph._locate_recorded(cls, "Data.ctl") is None  # noqa: SLF001
    (cls.parent / "Data.ctl").write_bytes(b"")
    assert graph._locate_recorded(cls, "Data.ctl") == cls.parent / "Data.ctl"  # noqa: SLF001


def test_the_one_file_differing_only_in_case_is_the_recorded_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "Do.vi").write_bytes(b"")
    graph = InMemoryVIGraph()
    assert graph._locate_recorded(tmp_path / "DO.VI", "DO.VI") == tmp_path / "Do.vi"  # noqa: SLF001
    assert graph._locate_recorded(tmp_path / "Gone.vi", "Gone.vi") is None  # noqa: SLF001


def test_a_member_of_an_llb_folder_on_the_recorded_path_is_found(
    tmp_path: Path,
) -> None:
    member = tmp_path / "util.llb" / "x.vi"
    member.parent.mkdir()
    member.write_bytes(b"")
    graph = InMemoryVIGraph()
    assert graph._locate_recorded(member, "x.vi") == member  # noqa: SLF001


# ── library and class members, parent classes ─────────────────────────────────


def test_a_member_url_is_read_beside_its_container_then_in_its_folder(
    tmp_path: Path,
) -> None:
    lib = tmp_path / "Lib" / "Lib.lvlib"
    # LabVIEW 2010+: relative to the library file treated as a folder.
    assert member_url_paths(lib, "../X.vi")[0] == (tmp_path / "Lib" / "X.vi")
    assert member_url_paths(lib, "../../Other/X.vi")[0] == tmp_path / "Other" / "X.vi"
    # LabVIEW 8.x: relative to the library's folder.
    assert member_url_paths(lib, "X.vi")[0] == tmp_path / "Lib" / "X.vi"


_LIB = """<?xml version='1.0' encoding='UTF-8'?>
<Library LVVersion="21008000">
\t<Item Name="Here.vi" Type="VI" URL="../Here.vi"/>
\t<Item Name="Gone.vi" Type="VI" URL="../Gone.vi"/>
</Library>
"""


def test_a_library_member_not_at_its_url_is_a_stub_even_if_a_namesake_exists(
    tree: _Tree,
) -> None:
    tree.vi("Lib/Here.vi", "Lib.lvlib:Here.vi", ["Lib.lvlib"])
    tree.vi("Other/Gone.vi", "Lib.lvlib:Gone.vi", ["Lib.lvlib"])
    lib = tree.root / "Lib" / "Lib.lvlib"
    lib.write_text(_LIB, encoding="utf-8")
    graph = InMemoryVIGraph()
    lib_key = graph.load_lvlib(lib, LoadMode.NONE)
    owned = {
        Path(t).name: t
        for _, t, d in graph._dep_graph.out_edges(lib_key, data=True)  # noqa: SLF001
        if d.get("rel") == EdgeRel.OWNS
    }
    assert owned["Here.vi"] == _key(tree, "Lib/Here.vi")
    assert not graph.is_stub(owned["Here.vi"])
    assert owned["Gone.vi"] == _key(tree, "Lib/Gone.vi")
    assert graph.is_stub(owned["Gone.vi"])
    assert _key(tree, "Other/Gone.vi") not in graph.list_vis()


_CLASS = """<?xml version='1.0' encoding='UTF-8'?>
<LVClass LVVersion="21008000">
{parent}</LVClass>
"""


def test_a_parent_class_is_found_only_at_the_url_its_child_records(
    tmp_path: Path,
) -> None:
    """The child records its parent at ``../../Base/Base.lvclass``; a
    ``Base.lvclass`` beside the child is not taken in its place."""
    child = tmp_path / "Child" / "Child.lvclass"
    child.parent.mkdir()
    child.write_text(
        _CLASS.format(
            parent='\t<Item Name="Base.lvclass" Type="Parent" '
            'URL="../../Base/Base.lvclass"/>\n'
        ),
        encoding="utf-8",
    )
    (tmp_path / "Child" / "Base.lvclass").write_text(_CLASS.format(parent=""))
    graph = InMemoryVIGraph()
    key = graph.load_lvclass(child, LoadMode.MINIMAL)
    parent_key = graph._dep_graph.nodes[key]["parent_key"]  # noqa: SLF001
    assert parent_key == str(tmp_path / "Base" / "Base.lvclass")
    assert graph.is_stub(parent_key)
    real = tmp_path / "Base" / "Base.lvclass"
    real.parent.mkdir()
    real.write_text(_CLASS.format(parent=""), encoding="utf-8")
    graph = InMemoryVIGraph()
    key = graph.load_lvclass(child, LoadMode.MINIMAL)
    assert not graph.is_stub(graph._dep_graph.nodes[key]["parent_key"])  # noqa: SLF001
