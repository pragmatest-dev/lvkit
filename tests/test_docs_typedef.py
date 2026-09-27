"""``lvkit docs`` for ``.ctl`` type definitions: page rendering, unique names,
the index, the VI page's Type Definitions section, and ``generate_documents`` on
real controls."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lvkit.docs.generate import _prepare_vi_documentation_data, generate_documents
from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode
from lvkit.graph.node_kinds import NodeType
from lvkit.graph.typedef import TypedefField, TypedefInfo, TypedefRef
from lvkit.models import EnumValue, LVType, LVTypeKind

from .conftest import SAMPLES_ROOT

_ICON_API = SAMPLES_ROOT / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API"
_CLUSTER_CTL = _ICON_API / "API_Test Settings.ctl"
_USER_VI = _ICON_API / "Generate LV Icon Template Layer.vi"

_I32 = LVType(LVTypeKind.PRIMITIVE, underlying_type="I32")


def _generator(tmp_path: Path) -> HTMLDocGenerator:
    return HTMLDocGenerator(tmp_path, "Proj", "directory")


def _info(**kw) -> TypedefInfo:
    base = {
        "key": "/p/Cfg.ctl",
        "name": "Cfg.ctl",
        "root_type": LVType(LVTypeKind.CLUSTER),
        "default": None,
        "fields": (),
        "uses": (),
        "owned_by": (),
        "used_by": (),
    }
    return TypedefInfo(**{**base, **kw})


def test_typedef_filenames_are_unique_by_path() -> None:
    gen = HTMLDocGenerator(Path("."), "P", "directory")
    entries = [
        ("/a/Same.ctl", "Same.ctl"),
        ("/b/Same.ctl", "Same.ctl"),
        ("/c/One.ctl", "One.ctl"),
    ]
    names = gen._typedef_filenames(entries)
    assert names["/c/One.ctl"] == "typedef/One.html"  # a unique name stays plain
    a, b = names["/a/Same.ctl"], names["/b/Same.ctl"]
    assert a != b and a.startswith("typedef/Same-") and b.startswith("typedef/Same-")
    assert names == gen._typedef_filenames(entries[::-1])  # whatever the order


def test_typedef_page_lists_fields_values_and_escapes(tmp_path: Path) -> None:
    gen = _generator(tmp_path)
    gen.register_typedefs([("/p/Cfg.ctl", "Cfg.ctl")])
    fields = (
        TypedefField("<b>Gain</b>", _I32, "3", ()),
        TypedefField(
            "Steps", LVType(LVTypeKind.ARRAY, element_type=_I32), None, (), ("1", "2")
        ),
        TypedefField(
            "Sub",
            LVType(LVTypeKind.CLUSTER),
            None,
            (TypedefField("Inner", _I32, "0", ()),),
        ),
    )
    html = gen._render_typedef_page(_info(fields=fields), "<svg id='fp'></svg>")
    assert "&lt;b&gt;Gain&lt;/b&gt;" in html and "<b>Gain</b>" not in html
    assert "<svg id='fp'></svg>" in html and "[1, 2]" in html
    assert "Inner" in html and 'padding-left:2.5em' in html  # nested one level in
    enum = LVType(LVTypeKind.ENUM, values={"Read": EnumValue(0), "Write": EnumValue(1)})
    values = gen._render_typedef_page(_info(root_type=enum, default="Write"), None)
    assert "<td>1</td><td>Write</td>" in values and "Front Panel" not in values


def test_references_link_only_to_pages_that_exist(tmp_path: Path) -> None:
    gen = _generator(tmp_path)
    gen.register_typedefs([("/p/Cfg.ctl", "Cfg.ctl"), ("/p/Dep.ctl", "Dep.ctl")])
    gen.all_vis.add("/p/use.vi")
    gen.class_pages["Cls.lvclass"] = "Cls_lvclass/Cls.lvclass.html"
    info = _info(
        uses=(TypedefRef(NodeType.TYPEDEF, "Dep.ctl", "/p/Dep.ctl"),),
        owned_by=(TypedefRef(NodeType.CLASS, "Cls.lvclass", "/p/Cls.lvclass"),),
        used_by=(
            TypedefRef(NodeType.VI, "use.vi", "/p/use.vi"),
            TypedefRef(NodeType.VI, "nopage.vi", "/p/nopage.vi"),
        ),
    )
    html = gen._render_typedef_page(info, None)
    assert 'href="Dep.html"' in html  # another typedef page: same directory
    assert 'href="../Cls_lvclass/Cls.lvclass.html"' in html
    assert "nopage.vi</code> (vi)" in html  # no page: listed, not linked
    assert html.count("<a href=") == 4  # breadcrumb + the 3 references with pages


def test_index_and_vi_page_link_to_typedef_pages(tmp_path: Path) -> None:
    gen = _generator(tmp_path)
    gen.register_typedefs([("/p/Cfg.ctl", "Cfg.ctl")])
    index = gen._render_index_page([])
    assert "Type Definitions" in index and 'href="typedef/Cfg.html"' in index
    assert "type definitions: 1" in index
    def link(key: str) -> str | None:
        page = gen.typedef_pages.get(key)
        return "../" + page if page else None

    section = gen._render_type_definitions_section(
        {"/p/Cfg.ctl": "Cfg.ctl", "/p/Gone.ctl": "Gone.ctl"}, link
    )
    assert 'href="../typedef/Cfg.html"' in section and "Gone.ctl</code></li>" in section
    assert gen._render_type_definitions_section({}, link) == ""  # none: no section


def test_a_control_free_index_is_unchanged(tmp_path: Path) -> None:
    index = _generator(tmp_path)._render_index_page([])
    assert "Type Definitions" not in index and "type definitions" not in index


def test_a_lone_unreadable_control_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "Bad.ctl"
    bad.write_bytes(b"not a labview file")
    result = generate_documents(str(bad), str(tmp_path / "out"))
    assert result.startswith("Failed to load any VIs or controls")
    assert "Bad.ctl" in result


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_docs_on_a_control_and_on_a_directory_of_controls(tmp_path):
    single = tmp_path / "single"
    summary = generate_documents(str(_CLUSTER_CTL), str(single))
    assert "Type definitions documented:" in summary  # it, plus what it references
    page = (single / "typedef" / "API_Test_Settings.html").read_text(encoding="utf-8")
    assert "Text color" in page and "Front Panel" in page and "<svg" in page
    assert 'href="typedef/API_Test_Settings.html"' in (single / "index.html").read_text(
        encoding="utf-8"
    )

    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(_CLUSTER_CTL, project / "Foo.ctl")
    generate_documents(str(project), str(tmp_path / "dir_out"))
    assert (tmp_path / "dir_out" / "typedef" / "Foo.html").is_file()


@pytest.mark.needs_samples
@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_a_vi_lists_its_typedefs_apart_from_its_subvis():
    graph, vi = load_vi_by_path(_USER_VI, LoadMode.MINIMAL, search_paths=[_ICON_API])
    data = _prepare_vi_documentation_data(vi, graph, {}, None)
    assert data["type_definitions"], "the VI uses controls"
    typedefs = set(data["type_definitions"])
    assert typedefs.isdisjoint(data["dependencies"])  # not listed as SubVI calls
    assert all(graph.is_typedef(k) for k in typedefs)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_a_corrupt_control_does_not_stop_the_others(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(_CLUSTER_CTL, project / "Good.ctl")
    (project / "Bad.ctl").write_bytes(b"not a labview file")
    summary = generate_documents(str(project), str(tmp_path / "out"))
    assert (tmp_path / "out" / "typedef" / "Good.html").is_file()
    assert "Warnings (1 files skipped)" in summary and "Bad.ctl" in summary
    assert "Type definitions documented:" in summary


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_a_control_loaded_twice_is_documented_once():
    from lvkit.graph.core import InMemoryVIGraph

    g = InMemoryVIGraph()
    first = g.load_typedef(_CLUSTER_CTL)
    assert g.load_typedef(_CLUSTER_CTL) == first
    assert g.list_typedefs().count(first) == 1


_ICON_LVLIB = _ICON_API / "LabVIEW Icon API.lvlib"


@pytest.mark.needs_samples
@pytest.mark.skipif(not _ICON_LVLIB.exists(), reason="icon-editor sample absent")
def test_a_library_lists_its_controls_apart_from_its_vis():
    from lvkit.docs.generate import _collect_library_vis, _split_controls

    vis, controls = _split_controls(_collect_library_vis(_ICON_LVLIB))
    assert vis and controls
    assert all(p.suffix.lower() == ".vi" for p in vis)
    assert all(p.suffix.lower() == ".ctl" and p.is_file() for p in controls)
    assert len(set(controls)) == len(controls)  # each listed control appears once
