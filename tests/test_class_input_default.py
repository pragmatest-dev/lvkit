"""An unwired class input is the class's default object: a new object of its
class every call, made in the function's body for the ``None`` that means "not
wired". Each case is a handmade project (``tests/class_graphs.py``) generated into
a package that is imported and run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import INT, Project, Recipe, class_type, pane, wire

BASE = class_type("Base")
GHOST = class_type("Ghost")
ROOT = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="UDClassInst",
    classname="LabVIEW Object",
)
COUNT = ClusterField(name="Count", type=INT)


def take(owner: LVType, rule: ParsedWiringRule) -> Recipe:
    """A VI whose one class input (wiring rule ``rule``) is its one output."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", owner, "obj", 0, rule)],
                outputs=[pane(vi, "output", "out", owner, "out", 1)],
                data_flow=[wire(vi, "obj", "out")],
            ),
            [],
        )

    return build


def test_an_unwired_class_input_is_a_new_default_object_each_call(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Take", take(BASE, ParsedWiringRule.RECOMMENDED))
    package = project.generate()
    assert package.gaps == []
    source = package.sources["take"]
    assert "def take(*, obj: Base | None=None) -> TakeResult:" in source
    assert "    if obj is None:\n        obj = Base()\n" in source
    with package.use() as module:
        base_class, take_vi = module("base").Base, module("take").take
        first, second = take_vi().out, take_vi().out
        assert type(first) is base_class and type(second) is base_class
        assert first is not second
        first.touched = True
        assert not hasattr(take_vi().out, "touched")
        passed = base_class()
        assert take_vi(obj=passed).out is passed


def test_an_optional_class_input_is_a_default_object_too(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Take", take(BASE, ParsedWiringRule.OPTIONAL))
    package = project.generate()
    assert "    if obj is None:\n        obj = Base()\n" in package.sources["take"]


def test_an_unwired_labview_object_input_is_a_new_object(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Take", take(ROOT, ParsedWiringRule.RECOMMENDED))
    package = project.generate()
    assert package.gaps == []
    source = package.sources["take"]
    assert "    if obj is None:\n        obj = object()\n" in source
    with package.use() as module:
        first, second = module("take").take().out, module("take").take().out
        assert type(first) is object and first is not second
        kept = object()
        assert module("take").take(obj=kept).out is kept


def test_a_required_class_input_has_no_default_branch(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Take", take(BASE, ParsedWiringRule.REQUIRED))
    package = project.generate()
    source = package.sources["take"]
    assert "def take(obj: Base) -> TakeResult:" in source
    assert "is None" not in source


def test_a_class_not_loaded_is_a_gap_only_when_the_input_is_unwired(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Take", take(GHOST, ParsedWiringRule.RECOMMENDED))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    reason = (
        "The default object of the unwired input 'obj' of Ghost.lvclass names a "
        "class that is not loaded; add it to the project or a --search-path"
    )
    assert [g.reason for g in gaps] == [reason]
    assert [g.kind for g in gaps] == ["unsupported_vi"]
    with package.use() as module:
        kept = object()
        assert module("take").take(obj=kept).out is kept
        with pytest.raises(NotImplementedError, match="Ghost.lvclass") as raised:
            module("take").take()
        assert str(raised.value).startswith(reason)


def test_the_object_a_method_is_called_on_is_not_given_a_default(
    tmp_path: Path,
) -> None:
    """The class input a method is called on is the object itself, so it has no
    default; a second class input of the method is a default object."""

    def method(vi: str) -> tuple[VIContext, list[GraphNode]]:
        rule = ParsedWiringRule.RECOMMENDED
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "in", BASE, "Self", 0, rule),
                    pane(vi, "input", "other", BASE, "other", 2, rule),
                ],
                outputs=[
                    pane(vi, "output", "out", BASE, "Self out", 1),
                    pane(vi, "output", "other_out", BASE, "other out", 3),
                ],
                data_flow=[wire(vi, "in", "out"), wire(vi, "other", "other_out")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Pair", method)
    package = project.generate()
    source = package.sources["base"]
    assert "self is None" not in source
    assert "        if other is None:\n            other = Base()\n" in source
    with package.use() as module:
        base = module("base").Base()
        first, second = base.pair(), base.pair()

        assert type(first.other_out) is module("base").Base
        assert first.other_out is not second.other_out
        wired = module("base").Base()
        assert base.pair(other=wired).other_out is wired
