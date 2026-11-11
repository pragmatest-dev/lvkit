"""A copy of a LabVIEW value is one level: a new object holding the same field
values, so a field that holds a reference keeps the same object, and a write that
goes through a nested cluster copies it first.

Each case is a handmade VI (``tests/class_graphs.py``) generated into a package
that is imported and run on the fork of a named cluster: a Bundle By Name on one
branch, an Unbundle By Name on the other."""

from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.class_graphs import INT, Project, mux, pane, wire

HANDLE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="Queue")


def _named(name: str, fields: list[ClusterField]) -> LVType:
    return LVType(kind=LVTypeKind.CLUSTER, typedef_name=name, fields=fields)


def _fork_and_set(
    tmp_path: Path, data: LVType, field: LVType, field_index: int
) -> Project:
    """``edit(data, value)``: the field ``field_index`` (a depth-first index) of
    ``data`` is set to ``value`` on one branch of the wire while another branch
    reads the whole value."""
    project = Project(tmp_path)

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "data", data, "data", 0),
                    pane(vi, "input", "value", field, "value", 1),
                ],
                outputs=[
                    pane(vi, "output", "out", data, "out", 2),
                    pane(vi, "output", "seen", data, "seen", 3),
                ],
                data_flow=[
                    wire(vi, "data", "set.agg"),
                    wire(vi, "data", "seen"),
                    wire(vi, "value", "set.field"),
                    wire(vi, "set.agg_out", "out"),
                ],
            ),
            [mux(vi, "set", True, data, field, field_index)],
        )

    project.add_vi("Edit", build)
    return project


def test_a_copy_of_a_cluster_keeps_the_object_a_reference_field_holds(
    tmp_path: Path,
) -> None:
    data = _named(
        "Handle.ctl",
        [ClusterField(name="q", type=HANDLE), ClusterField(name="x", type=INT)],
    )
    package = _fork_and_set(tmp_path, data, INT, 1).generate()
    with package.use() as module:
        original = SimpleNamespace(q=queue.Queue(), x=1)
        result = module("edit").edit(data=original, value=9)
        assert result.out is not original
        assert result.out.q is original.q
        assert (original.x, result.out.x) == (1, 9)


def test_an_integer_field_holding_an_object_keeps_that_object_in_the_copy(
    tmp_path: Path,
) -> None:
    data = _named(
        "Timer.ctl",
        [ClusterField(name="ref", type=INT), ClusterField(name="x", type=INT)],
    )
    package = _fork_and_set(tmp_path, data, INT, 1).generate()
    with package.use() as module:
        shared = [0]
        original = SimpleNamespace(ref=shared, x=1)
        result = module("edit").edit(data=original, value=9)
        assert result.out.ref is shared
        shared[0] = 5
        assert result.out.ref == [5]
        assert (original.x, result.out.x) == (1, 9)


def test_a_write_into_a_nested_cluster_leaves_the_original_nested_value(
    tmp_path: Path,
) -> None:
    inner = _named("Inner.ctl", [ClusterField(name="x", type=INT)])
    outer = _named(
        "Outer.ctl",
        [ClusterField(name="inner", type=inner), ClusterField(name="y", type=INT)],
    )
    # Depth-first fields of Outer: inner, inner.x, y.
    package = _fork_and_set(tmp_path, outer, INT, 1).generate()
    assert "copy.copy(data_copy.inner)" in package.sources["edit"]
    with package.use() as module:
        original = SimpleNamespace(inner=SimpleNamespace(x=1), y=2)
        result = module("edit").edit(data=original, value=9)
        assert original.inner.x == 1
        assert result.out.inner.x == 9
        assert result.out.inner is not original.inner
        assert (original.y, result.out.y) == (2, 2)


def test_a_write_of_one_field_copies_no_nested_cluster(tmp_path: Path) -> None:
    inner = _named("Inner.ctl", [ClusterField(name="x", type=INT)])
    outer = _named(
        "Outer.ctl",
        [ClusterField(name="inner", type=inner), ClusterField(name="y", type=INT)],
    )
    package = _fork_and_set(tmp_path, outer, INT, 2).generate()
    assert "copy.copy(data_copy.inner)" not in package.sources["edit"]
    with package.use() as module:
        original = SimpleNamespace(inner=SimpleNamespace(x=1), y=2)
        result = module("edit").edit(data=original, value=9)
        assert result.out.inner is original.inner
        assert (original.y, result.out.y) == (2, 9)
