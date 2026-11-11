"""A generated LabVIEW class compares by value: ``==`` is true for two objects of
one class whose private data is equal at every level of the class hierarchy, and
objects of different classes are never equal. The cases generate small projects of
classes and run the result."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit.models import ClusterField
from tests.class_graphs import INT, Package, Project

COUNT = ClusterField(name="Count", type=INT)
EXTRA = ClusterField(name="Extra", type=INT)
OTHER = ClusterField(name="Other", type=INT)


def family(tmp_path: Path) -> Package:
    """Base(Count), Child(Base)(Extra), Sibling(Base)(Other), and a Grand(Child)."""
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [EXTRA], parent="Base")
    project.add_class("Sibling", [OTHER], parent="Base")
    project.add_class("Grand", [OTHER], parent="Child")
    return project.generate()


def test_objects_of_one_class_compare_by_value(tmp_path: Path) -> None:
    package = family(tmp_path)
    with package.use() as module:
        base = module("base").Base
        a, b = base(), base()
        assert a == b
        assert not a != b
        b._Base__count = 3
        assert a != b
        assert not a == b
        a._Base__count = 3
        assert a == b
        assert a == a


def test_every_level_of_the_hierarchy_takes_part(tmp_path: Path) -> None:
    package = family(tmp_path)
    with package.use() as module:
        child = module("child").Child
        a, b = child(), child()
        assert a == b
        b._Child__extra = 1
        assert a != b
        b._Child__extra = 0
        assert a == b
        b._Base__count = 1
        assert a != b
        assert not a == b


def test_objects_of_different_classes_are_never_equal(tmp_path: Path) -> None:
    package = family(tmp_path)
    with package.use() as module:
        base, child = module("base").Base(), module("child").Child()
        sibling, grand = module("sibling").Sibling(), module("grand").Grand()
        assert vars(base).items() <= vars(child).items()
        for one, other in [(base, child), (child, base), (child, sibling)]:
            assert one != other
            assert not one == other
        assert child != grand
        assert grand != child
        assert base != 0


def test_only_the_root_class_defines_eq(tmp_path: Path) -> None:
    sources = family(tmp_path).sources
    assert len(re.findall(r"def __eq__\(", sources["base"])) == 1
    for name in ("child", "sibling", "grand"):
        assert "__eq__" not in sources[name]
    assert "type(self) is type(other) and vars(self) == vars(other)" in sources["base"]


def test_an_object_is_not_hashable(tmp_path: Path) -> None:
    package = family(tmp_path)
    with package.use() as module:
        for obj in (module("base").Base(), module("child").Child()):
            with pytest.raises(TypeError):
                hash(obj)


def test_a_class_whose_parent_was_not_found_defines_eq(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Orphan", [COUNT], parent="Missing", parent_found=False)
    package = project.generate([])
    assert "def __eq__(" in package.sources["orphan"]
