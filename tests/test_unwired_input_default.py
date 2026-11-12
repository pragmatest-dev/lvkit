"""An input left unwired holds its type's default inside the function, for every
type whose ``def`` default is ``None``: an array, a cluster, a path. The function
is generated, imported and called."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import INT, Project, pane, wire

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
ARRAY = LVType(kind=LVTypeKind.ARRAY, underlying_type="NumInt32", element_type=INT)
FIELDS = [ClusterField(name="Count", type=INT), ClusterField(name="Text", type=STRING)]
ANONYMOUS = LVType(kind=LVTypeKind.CLUSTER, fields=FIELDS)
NAMED = LVType(kind=LVTypeKind.CLUSTER, fields=FIELDS, typedef_name="Pair.ctl")
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(
            name="status",
            type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
        ),
        ClusterField(name="code", type=INT),
        ClusterField(name="source", type=STRING),
    ],
)


def echo(lv_type: LVType, rule: ParsedWiringRule):
    """A VI whose one input (wiring rule ``rule``) is its one output."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "c", lv_type, "c", 0, rule)],
                outputs=[pane(vi, "output", "out", lv_type, "out", 1)],
                data_flow=[wire(vi, "c", "out")],
            ),
            [],
        )

    return build


def generated(tmp_path: Path, lv_type: LVType, rule: ParsedWiringRule):
    project = Project(tmp_path)
    project.add_vi("Echo", echo(lv_type, rule))
    package = project.generate()
    assert package.gaps == []
    return package


def test_an_unwired_anonymous_cluster_is_the_cluster_default(tmp_path: Path) -> None:
    package = generated(tmp_path, ANONYMOUS, ParsedWiringRule.RECOMMENDED)
    assert "    if c is None:\n        c = (0, '')\n" in package.sources["echo"]
    with package.use() as module:
        echo_vi = module("echo").echo
        assert echo_vi().out == (0, "")
        assert echo_vi(c=(3, "x")).out == (3, "x")


def test_an_unwired_named_cluster_is_the_cluster_default(tmp_path: Path) -> None:
    package = generated(tmp_path, NAMED, ParsedWiringRule.RECOMMENDED)
    source = package.sources["echo"]
    assert "    if c is None:\n        c = " in source
    with package.use() as module:
        echo_vi = module("echo").echo
        default = echo_vi().out
        assert (default.count, default.text) == (0, "")
        passed = echo_vi().out.__class__(count=4, text="y")
        assert echo_vi(c=passed).out is passed


def test_an_unwired_path_is_the_empty_path_and_none_stays_not_a_path(
    tmp_path: Path,
) -> None:
    """An unwired path is the type's default, the empty path, written as the
    ``def`` default (a ``Path`` cannot change, so the call shares it safely);
    ``None`` is Not a Path, so a passed ``None`` is never replaced."""
    package = generated(tmp_path, PATH, ParsedWiringRule.RECOMMENDED)
    assert "is None" not in package.sources["echo"]
    with package.use() as module:
        echo_vi = module("echo").echo
        assert echo_vi().out == Path("")
        assert echo_vi(c=None).out is None
        assert echo_vi(c=Path("a/b")).out == Path("a/b")


def test_an_unwired_array_is_an_empty_array_and_a_passed_one_is_kept(
    tmp_path: Path,
) -> None:
    package = generated(tmp_path, ARRAY, ParsedWiringRule.OPTIONAL)
    assert "    if c is None:\n        c = []\n" in package.sources["echo"]
    with package.use() as module:
        echo_vi = module("echo").echo
        assert echo_vi().out == []
        passed: list[int] = []
        assert echo_vi(c=passed).out is passed
        assert echo_vi(c=[1, 2]).out == [1, 2]


def test_an_unwired_error_in_has_no_default_line(tmp_path: Path) -> None:
    package = generated(tmp_path, ERROR, ParsedWiringRule.RECOMMENDED)
    assert "is None" not in package.sources["echo"]
    with package.use() as module:
        assert module("echo").echo().out is None


def test_a_required_scalar_input_has_no_default_line(tmp_path: Path) -> None:
    package = generated(tmp_path, INT, ParsedWiringRule.REQUIRED)
    assert "is None" not in package.sources["echo"]
