"""A catalog entry that names the third-party packages its code imports
(``requires``) gets a ``requirements.txt`` at the generated package root: each
package once, sorted, written only when a node needs one."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.catalogs import primitive_catalog_for
from lvkit.codegen.package_requirements import PackageRequirements
from tests.error_diagrams import (
    I32,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Add Two.vi"
LOCAL_PRIMITIVE = 99001


def _catalog_entry(monkeypatch: pytest.MonkeyPatch, requires: list[str]) -> None:
    """A catalog primitive that adds its inputs, with the packages it requires."""
    entry: dict[str, object] = {
        "name": "Plain Sum",
        "python_code": {"total": "in_0 + in_1"},
        "terminals": [
            {"index": 0, "direction": "input", "name": "x", "type": "NumInt32"},
            {"index": 1, "direction": "input", "name": "y", "type": "NumInt32"},
            {"index": 2, "direction": "output", "name": "total", "type": "NumInt32"},
        ],
    }
    if requires:
        entry["requires"] = requires
    monkeypatch.setitem(primitive_catalog_for(None)._by_id, str(LOCAL_PRIMITIVE), entry)


def _sum_vi(project: Project, nodes: int) -> None:  # noqa: F811
    """``nodes`` Plain Sum nodes in a row over the controls x and y."""
    vi = Vi(NAME)
    x = vi.control("x", I32)
    y = vi.control("y", I32)
    total = vi.indicator("total", I32)
    last = x
    for _ in range(nodes):
        node = vi.primitive(
            LOCAL_PRIMITIVE,
            [("x", 0, I32, False), ("y", 1, I32, False), ("total", 2, I32, True)],
        )
        vi.wire(last, node["x"])
        vi.wire(y, node["y"])
        last = node["total"]
    vi.wire(last, total)
    project.add(vi)


def test_a_package_with_a_requiring_node_gets_requirements_txt(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_entry(monkeypatch, ["zzz-pkg", "npTDMS"])
    _sum_vi(project, nodes=2)
    package = project.generate(NAME)
    assert (package.root / "requirements.txt").read_text() == "npTDMS\nzzz-pkg\n"
    assert package.function(2, 3).total == 8


def test_a_package_without_a_requiring_node_has_no_requirements_txt(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_entry(monkeypatch, [])
    _sum_vi(project, nodes=1)
    package = project.generate(NAME)
    assert not (package.root / "requirements.txt").exists()
    assert package.function(2, 3).total == 5


def test_the_file_is_written_only_when_there_are_packages(tmp_path: Path) -> None:
    empty = PackageRequirements()
    assert empty.write(tmp_path) is None
    assert not (tmp_path / "requirements.txt").exists()
    filled = PackageRequirements()
    filled.add(["b", "a"])
    filled.add(["a"])
    assert filled.build() == "a\nb\n"
