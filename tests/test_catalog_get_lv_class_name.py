"""``Get LV Class Name.vi``: each generated class carries its qualified LabVIEW name
(``__lv_class_name__``) and the generated caller returns it for the class of the
object it is given: a class in a library, a class alone, an object of a child class
on a parent's wire, and LabVIEW Object (Python's ``object``)."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.type_module import build_type_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.node_kinds import NodeType
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.pipeline import create_import_resolver
from tests.vilib_caller import BOOL, I32, STRING, run_catalog_call

ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)
_OBJECT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
_RECORDED = ["<vilib>", "Utility", "LVClass", "Get LV Class Name.vi"]
_PANE = [
    (0, "output", ERROR),
    (3, "output", STRING),
    (8, "input", ERROR),
    (11, "input", _OBJECT),
]

_PARENT = "/src/Parent.lvclass"
_CHILD = "/src/Child.lvclass"
_ALONE = "/src/Alone.lvclass"


def _classes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The classes generated for a parent in a library, its child (in a nested
    library) and a class alone, imported."""
    graph = InMemoryVIGraph()
    number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    graph._dep_graph.add_node(  # noqa: SLF001
        _PARENT,
        node_type=NodeType.CLASS,
        fields=[ClusterField(name="count", type=number)],
        parent_class="LabVIEW Object",
    )
    graph._dep_graph.add_node(  # noqa: SLF001
        _CHILD,
        node_type=NodeType.CLASS,
        fields=[],
        parent_class="Parent",
        parent_key=_PARENT,
    )
    graph._dep_graph.add_node(  # noqa: SLF001
        _ALONE, node_type=NodeType.CLASS, fields=[], parent_class="LabVIEW Object"
    )
    graph._vi_display_names.update(  # noqa: SLF001
        {
            _PARENT: "Lib.lvlib:Parent.lvclass",
            _CHILD: "Outer.lvlib:Inner.lvlib:Child.lvclass",
            _ALONE: "Alone.lvclass",
        }
    )
    out = tmp_path / "classes"
    out.mkdir()
    (out / "__init__.py").write_text("")
    paths = {
        _PARENT: out / "parent.py",
        _CHILD: out / "child.py",
        _ALONE: out / "alone.py",
    }
    for key, path in paths.items():
        resolver = create_import_resolver(
            "classes", out, paths, path, caller_key=key, graph=graph
        )
        path.write_text(build_type_module(graph, key, resolver))
    monkeypatch.syspath_prepend(str(tmp_path))
    return {
        "Parent": importlib.import_module("classes.parent").Parent,
        "Child": importlib.import_module("classes.child").Child,
        "Alone": importlib.import_module("classes.alone").Alone,
    }


def _name_of(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, obj: object) -> str:
    source, result = run_catalog_call(
        tmp_path / "call",
        monkeypatch,
        _RECORDED,
        _PANE,
        {"object_": (11, _OBJECT)},
        {"class_name": (3, STRING), "error_out": (0, ERROR)},
        {"object_": obj},
    )
    assert "lvkit" not in source
    return result.class_name


def test_a_class_in_a_library_answers_its_qualified_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    classes = _classes(tmp_path, monkeypatch)
    assert classes["Parent"].__lv_class_name__ == "Lib.lvlib:Parent.lvclass"
    assert _name_of(tmp_path, monkeypatch, classes["Parent"]()) == (
        "Lib.lvlib:Parent.lvclass"
    )


def test_a_class_alone_answers_its_file_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    classes = _classes(tmp_path, monkeypatch)
    assert _name_of(tmp_path, monkeypatch, classes["Alone"]()) == "Alone.lvclass"


def test_an_object_of_a_child_class_answers_the_childs_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    classes = _classes(tmp_path, monkeypatch)
    assert issubclass(classes["Child"], classes["Parent"])
    assert _name_of(tmp_path, monkeypatch, classes["Child"]()) == (
        "Outer.lvlib:Inner.lvlib:Child.lvclass"
    )


def test_labview_object_is_named_labview_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _name_of(tmp_path, monkeypatch, object()) == "LabVIEW Object"
