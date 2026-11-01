"""A VI is saved as ``.vi``, ``.vim`` (malleable VI) or ``.vit`` (VI template):
one binary layout, three extensions. Every place that asks "is this a VI file?"
accepts all three, and a ``.vim``/``.vit`` loads exactly like a ``.vi``.

These tests use a hand-written block diagram in place of the extraction step so
they need no sample corpus; ``test_vi_file_types_corpus.py`` covers real files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.graph.describe import describe_vi
from lvkit.graph.node_kinds import EdgeRel, NodeType
from lvkit.index.project import resolve_project_files
from lvkit.structure import parse_lvclass
from lvkit.vi_files import (
    glob_vi_files,
    is_vi_file,
    rglob_vi_files,
    strip_vi_extension,
)

_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList></zPlaneList>
  </root>
</SL__rootObject>
"""


@pytest.fixture
def fake_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every VI file "extracts" to one empty block diagram, so loading a
    placeholder file by any extension needs no LabVIEW binary."""
    bd_xml = tmp_path / "Tiny_BDHb.xml"
    bd_xml.write_text(_BD_XML)
    monkeypatch.setattr(
        "lvkit.graph.loading.extract_vi_xml", lambda _path: (bd_xml, None, None)
    )
    return tmp_path


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Foo.vi", True),
        ("Foo.vim", True),
        ("Foo.vit", True),
        ("Foo.VIM", True),
        ("dir/Foo.vit", True),
        ("Foo.ctl", False),
        ("Foo.lvlib", False),
        ("Foo.vib", False),
        ("Foo", False),
    ],
)
def test_is_vi_file(name: str, expected: bool) -> None:
    assert is_vi_file(name) is expected
    assert is_vi_file(Path(name)) is expected


def test_strip_vi_extension_removes_only_a_trailing_vi_extension() -> None:
    assert strip_vi_extension("Create Annotations.vim") == "Create Annotations"
    assert strip_vi_extension("testExample.vit") == "testExample"
    assert strip_vi_extension("Run.vi") == "Run"
    assert strip_vi_extension("Run.VI") == "Run"
    assert strip_vi_extension("Lib.lvlib:Run.vim") == "Lib.lvlib:Run"
    assert strip_vi_extension("Params.ctl") == "Params.ctl"
    assert strip_vi_extension("a.vi.bak") == "a.vi.bak"


def test_vi_file_listing_finds_all_three_kinds_sorted(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    for rel in ("b.vim", "a.vi", "sub/c.vit", "sub/d.ctl", "e.txt"):
        (tmp_path / rel).write_bytes(b"")
    assert [p.name for p in glob_vi_files(tmp_path)] == ["a.vi", "b.vim"]
    assert [p.relative_to(tmp_path).as_posix() for p in rglob_vi_files(tmp_path)] == [
        "a.vi",
        "b.vim",
        "sub/c.vit",
    ]


@pytest.mark.parametrize("name", ["A.vim", "B.vit", "C.vi"])
def test_load_vi_accepts_every_vi_file_kind(fake_extraction: Path, name: str) -> None:
    path = fake_extraction / name
    path.write_bytes(b"placeholder")
    graph = InMemoryVIGraph()

    key = graph.load_vi(path, LoadMode.NONE)

    # Identity is the resolved path for every kind.
    assert key == str(path.resolve())
    assert graph.list_vis() == [key]
    assert graph.vi_display_name(key) == name
    stem = name.split(".")[0].lower()
    assert describe_vi(graph, key).splitlines()[:3] == [
        f"# {name}",
        "",
        f"  {stem}() -> None",
    ]


def test_load_vi_still_rejects_other_files(fake_extraction: Path) -> None:
    other = fake_extraction / "notes.txt"
    other.write_bytes(b"x")
    with pytest.raises(ValueError, match=r"Expected a VI file \(\.vi, \.vim, \.vit\)"):
        InMemoryVIGraph().load_vi(other, LoadMode.NONE)


def test_load_directory_loads_vim_and_vit(fake_extraction: Path) -> None:
    folder = fake_extraction / "vis"
    folder.mkdir()
    for name in ("a.vi", "b.vim", "c.vit", "d.txt"):
        (folder / name).write_bytes(b"x")
    graph = InMemoryVIGraph()

    graph.load_directory(folder, LoadMode.NONE)

    assert [Path(k).name for k in graph.list_vis()] == ["a.vi", "b.vim", "c.vit"]


_LVLIB_XML = """<?xml version='1.0' encoding='UTF-8'?>
<Library LVVersion="25008000">
\t<Property Name="NI.Lib.Version" Type="Str">1.0.0.0</Property>
\t<Item Name="Adapt.vim" Type="VI" URL="Adapt.vim"/>
\t<Item Name="Template.vit" Type="VI" URL="Template.vit"/>
\t<Item Name="Plain.vi" Type="VI" URL="Plain.vi"/>
\t<Item Name="Missing.vim" Type="VI" URL="Missing.vim"/>
</Library>
"""


def test_lvlib_loads_vim_and_vit_members_as_vi_nodes(fake_extraction: Path) -> None:
    lvlib = fake_extraction / "Lib.lvlib"
    lvlib.write_text(_LVLIB_XML, encoding="utf-8")
    for name in ("Adapt.vim", "Template.vit", "Plain.vi"):
        (fake_extraction / name).write_bytes(b"x")
    graph = InMemoryVIGraph()

    lib_key = graph.load_lvlib(lvlib, LoadMode.NONE)

    owned = {
        Path(target).name: target
        for _, target, data in graph._dep_graph.out_edges(lib_key, data=True)
        if data.get("rel") == EdgeRel.OWNS
    }
    assert sorted(owned) == ["Adapt.vim", "Missing.vim", "Plain.vi", "Template.vit"]
    for name in ("Adapt.vim", "Template.vit", "Plain.vi"):
        key = str((fake_extraction / name).resolve())
        assert owned[name] == key
        assert key in graph.list_vis()
        assert key not in graph._stubs
    # An absent member is a stub keyed by its intended path, like an absent .vi.
    missing = str((fake_extraction / "Missing.vim").resolve())
    assert owned["Missing.vim"] == missing
    assert missing in graph._stubs
    assert graph._dep_graph.nodes[missing]["node_type"] == NodeType.VI


def test_project_discovery_indexes_vim_and_vit(tmp_path: Path) -> None:
    for rel in ("a.vi", "sub/b.vim", "sub/c.vit", "sub/d.ctl", "e.txt"):
        (tmp_path / rel).parent.mkdir(exist_ok=True)
        (tmp_path / rel).write_bytes(b"")

    _, vi_paths, ctl_paths = resolve_project_files(tmp_path)

    assert [p.relative_to(tmp_path).as_posix() for p in vi_paths] == [
        "a.vi",
        "sub/b.vim",
        "sub/c.vit",
    ]
    assert [p.name for p in ctl_paths] == ["d.ctl"]


_LVCLASS_XML = """<?xml version='1.0' encoding='UTF-8'?>
<LVClass Name="Thing.lvclass" LVVersion="25008000">
\t<Item Name="Adapt.vim" Type="VI" URL="../Adapt.vim"/>
\t<Item Name="Run.vi" Type="VI" URL="../Run.vi"/>
</LVClass>
"""


def test_class_methods_include_vim(tmp_path: Path, monkeypatch) -> None:
    class_path = tmp_path / "Thing.lvclass"
    class_path.write_text(_LVCLASS_XML, encoding="utf-8")
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))

    methods = parse_lvclass(class_path).methods

    assert [m.name for m in methods] == ["Adapt", "Run"]
