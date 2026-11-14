"""A member of a loaded class that the vi.lib catalog gives Python for is no
method of its class: every call of it uses the catalog's Python, as a call of any
other VI the catalog gives Python for does (ARCHITECTURE.md, "The catalog wins"),
and the class module writes no method, result class or run-time lookup entry for
it. A member the catalog has no entry for stays a method."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit import catalogs
from lvkit.graph.models import GraphNode, VIContext
from lvkit.vilib_resolver import VILibResolver
from tests.class_graphs import INT, Project, call, pane, wire
from tests.test_class_codegen import COUNT, static_make

RECORDED = ["<vilib>", "Test", "Make Value.vi"]
_ENTRY = {
    "name": "Make Value",
    "vi_path": "Test/Make Value.vi",
    "category": "Test",
    "description": "Doubles its seed.",
    "terminals": [
        {"name": "seed", "index": 0, "direction": "input", "python_param": "seed"},
        {"name": "value", "index": 1, "direction": "output", "python_param": "value"},
    ],
    "status": "complete",
    "python_code": "{value} = {seed} * 2",
    "inline": True,
    "imports": [],
}


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "data"
    vilib = root / "vilib"
    vilib.mkdir(parents=True)
    (vilib / "_index.json").write_text(
        json.dumps({"categories": {"Test": "test.json"}}), encoding="utf-8"
    )
    (vilib / "test.json").write_text(
        json.dumps({"entries": [_ENTRY]}), encoding="utf-8"
    )
    monkeypatch.setitem(catalogs._vilib, None, ((), VILibResolver(data_dir=root)))


def _project(tmp_path: Path, recorded: list[str] | None) -> tuple[Project, str]:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    make_key = project.add_method("Base", "Make Value", static_make, static=True)
    if recorded is not None:
        project.graph._recorded.record(make_key, None, recorded)  # noqa: SLF001

    def use(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "x", INT, "x", 0)],
                outputs=[pane(vi, "output", "y", INT, "y", 1)],
                data_flow=[wire(vi, "x", "c.seed"), wire(vi, "c.value", "y")],
            ),
            [call(vi, "c", make_key, "iUse", [("seed", 0, INT)], [("value", 1, INT)])],
        )

    project.add_vi("Use", use)
    return project, make_key


@pytest.mark.usefixtures("catalog")
def test_a_member_the_catalog_has_python_for_is_called_by_that_python(
    tmp_path: Path,
) -> None:
    project, _ = _project(tmp_path, RECORDED)
    package = project.generate()
    assert package.gaps == []
    assert "x * 2" in package.sources["use"]
    assert "Base.make_value" not in package.sources["use"]
    with package.use() as module:
        assert module("use").use(x=4).y == 8


@pytest.mark.usefixtures("catalog")
def test_the_class_module_writes_nothing_for_that_member(tmp_path: Path) -> None:
    project, _ = _project(tmp_path, RECORDED)
    source = project.generate().sources["base"]
    assert "make_value" not in source
    assert "MakeValueResult" not in source


@pytest.mark.usefixtures("catalog")
def test_a_member_the_catalog_has_no_entry_for_stays_a_method(tmp_path: Path) -> None:
    project, _ = _project(tmp_path, ["<vilib>", "Test", "Other Value.vi"])
    package = project.generate()
    assert "Base.make_value(seed=x)" in package.sources["use"]
    assert "def make_value(seed" in package.sources["base"]
    with package.use() as module:
        assert module("use").use(x=4).y == 4
        assert module("base").Base.make_value(seed=7).value == 7


@pytest.mark.usefixtures("catalog")
def test_a_member_with_no_recorded_path_stays_a_method(tmp_path: Path) -> None:
    project, _ = _project(tmp_path, None)
    package = project.generate()
    assert "Base.make_value(seed=x)" in package.sources["use"]
