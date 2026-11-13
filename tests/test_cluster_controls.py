"""A cluster control's Controls[] (``codegen/panel_clusters.py``,
``panels_module.Field``): a ``Control`` for each field of the cluster in field
order, with the field's label, the cluster's indicator flag and the field of the
cluster's value as its value. Each case is a handmade diagram whose generated
package is imported and run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import INT, Project, pane, wire
from tests.test_control_references import (
    REFNUM,
    REFNUMS,
    property_node,
)
from tests.test_front_panel_reference import ERROR, STRING

CLUSTER_CONTROLS = (104102914,)

INNER = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[ClusterField(name="x", type=INT), ClusterField(name="y", type=STRING)],
)
ANONYMOUS = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField(name="count", type=INT),
        ClusterField(name="inner", type=INNER),
    ],
)
NAMED = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    typedef_name="Config",
    fields=[
        ClusterField(name="Rate Hz", type=INT),
        ClusterField(name="name", type=STRING),
        ClusterField(name="Name", type=STRING),
    ],
)
OPTIONAL = ParsedWiringRule.RECOMMENDED


def target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI with an anonymous cluster control, a named (typedef) cluster control, a
    named cluster indicator and a plain control, in that tabbing order."""
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "anon", ANONYMOUS, "anon", 0, OPTIONAL, tab_index=0),
                pane(vi, "input", "cfg", NAMED, "cfg", 1, OPTIONAL, tab_index=1),
                pane(vi, "input", "n", INT, "n", 2, OPTIONAL, tab_index=3),
            ],
            outputs=[pane(vi, "output", "out", NAMED, "out", 3, tab_index=2)],
            data_flow=[wire(vi, "cfg", "out")],
        ),
        [],
    )


def cluster_reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that returns the Controls[] of the cluster reference it is given."""
    node = property_node(
        vi, "clust", "Clust", [("controls", CLUSTER_CONTROLS, "output", REFNUMS)]
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "controls", REFNUMS, "controls", 1),
                pane(vi, "output", "err", ERROR, "error out", 2),
            ],
            data_flow=[
                wire(vi, "ctl", "clust.ref"),
                wire(vi, "clust.controls", "controls"),
                wire(vi, "clust.err_out", "err"),
            ],
        ),
        [node],
    )


def project_with_reader(tmp_path: Path) -> tuple[Project, list[Gap]]:
    project = Project(tmp_path)
    project.add_vi("Target", target)
    project.add_vi("Reader", cluster_reader)
    return project, []


def test_the_fields_are_described_in_field_order(tmp_path: Path) -> None:
    project, gaps = project_with_reader(tmp_path)
    package = project.generate(gaps)
    assert gaps == []
    source = package.sources["target"]
    assert (
        "clusters={'anon': (('count', 0), ('inner', 1, (('x', 0), ('y', 1)))), "
        "'cfg': (('Rate Hz', 'rate_hz'), ('name', 'name'), ('Name', 'name_2')), "
        "'out': (('Rate Hz', 'rate_hz'), ('name', 'name'), ('Name', 'name_2'))}"
    ) in source
    assert "as_control(ctl).controls" in package.sources["reader"]
    # a control that is not a cluster has no entry
    assert "'n':" not in source.split("clusters=")[1].split("}")[0]


def test_a_field_reads_and_writes_its_field_of_an_anonymous_cluster(
    tmp_path: Path,
) -> None:
    project, gaps = project_with_reader(tmp_path)
    with project.generate(gaps).use() as module:
        panel = module("target").target.panel
        panel.set("anon", (3, (4, "four")))
        by_label = {c.label: c for c in panel.controls}
        fields = module("reader").reader(ctl=by_label["anon"]).controls
        assert [(f.label, f.indicator) for f in fields] == [
            ("count", False),
            ("inner", False),
        ]
        assert [f.value for f in fields] == [3, (4, "four")]
        # the same objects each time
        assert module("reader").reader(ctl=by_label["anon"]).controls == fields
        before = panel.get("anon")
        fields[0].value = 9
        assert panel.get("anon") == (9, (4, "four"))
        assert before == (3, (4, "four"))
        # a nested cluster has its own controls, and a write reaches the panel
        x, y = fields[1].controls
        assert (x.label, y.label, x.value, y.value) == ("x", "y", 4, "four")
        y.value = "five"
        assert panel.get("anon") == (9, (4, "five"))
        assert fields[1].value == (4, "five")


def test_a_field_reads_and_writes_its_field_of_a_named_cluster(
    tmp_path: Path,
) -> None:
    project, gaps = project_with_reader(tmp_path)
    with project.generate(gaps).use() as module:
        panel = module("target").target.panel
        old = SimpleNamespace(rate_hz=10, name="a", name_2="b")
        panel.set("cfg", old)
        by_label = {c.label: c for c in panel.controls}
        fields = module("reader").reader(ctl=by_label["cfg"]).controls
        assert [f.label for f in fields] == ["Rate Hz", "name", "Name"]
        assert [f.value for f in fields] == [10, "a", "b"]
        fields[2].value = "c"
        assert panel.get("cfg") == SimpleNamespace(rate_hz=10, name="a", name_2="c")
        # the value that was read earlier is not changed
        assert old == SimpleNamespace(rate_hz=10, name="a", name_2="b")
        assert panel.get("cfg") is not old


def test_the_indicator_of_a_field_is_the_clusters(tmp_path: Path) -> None:
    project, gaps = project_with_reader(tmp_path)
    with project.generate(gaps).use() as module:
        panel = module("target").target.panel
        by_label = {c.label: c for c in panel.controls}
        fields = module("reader").reader(ctl=by_label["out"]).controls
        assert [f.indicator for f in fields] == [True, True, True]
        # a cluster with no value yet reads None and cannot be written
        assert fields[0].value is None
        with pytest.raises(module("panels").PanelError):
            fields[0].value = 1


def test_controls_of_a_control_that_is_not_a_cluster_is_error_1026(
    tmp_path: Path,
) -> None:
    project, gaps = project_with_reader(tmp_path)
    with project.generate(gaps).use() as module:
        panel = module("target").target.panel
        plain = next(c for c in panel.controls if c.label == "n")
        result = module("reader").reader(ctl=plain)
        assert result.error_out.code == 1026
        assert result.controls == []


def test_the_panel_controls_are_unchanged(tmp_path: Path) -> None:
    project, gaps = project_with_reader(tmp_path)
    with project.generate(gaps).use() as module:
        panel = module("target").target.panel
        assert [(c.label, c.indicator) for c in panel.controls] == [
            ("anon", False),
            ("cfg", False),
            ("out", True),
            ("n", False),
        ]
        assert panel.controls == panel.controls


def test_an_unset_cluster_control_holds_its_default(tmp_path: Path) -> None:
    """An optional cluster control's parameter default is None (the function
    builds the cluster); the panel holds the cluster's default instead, a tuple
    for an anonymous cluster and a namespace for a named one."""
    project, gaps = project_with_reader(tmp_path)
    package = project.generate(gaps)
    assert "'anon': lambda: (0, (0, ''))" in package.sources["target"]
    with package.use() as module:
        panel = module("target").target.panel
        assert panel.get("anon") == (0, (0, ""))
        assert panel.get("cfg") == SimpleNamespace(rate_hz=0, name="", name_2="")
        assert panel.get("n") == 0


def test_a_field_of_an_unset_cluster_reads_its_default_and_writes_only_it(
    tmp_path: Path,
) -> None:
    project, gaps = project_with_reader(tmp_path)
    with project.generate(gaps).use() as module:
        panel = module("target").target.panel
        by_label = {c.label: c for c in panel.controls}
        fields = module("reader").reader(ctl=by_label["anon"]).controls
        assert [f.value for f in fields] == [0, (0, "")]
        fields[0].value = 7
        assert panel.get("anon") == (7, (0, ""))
        named = module("reader").reader(ctl=by_label["cfg"]).controls
        assert [f.value for f in named] == [0, "", ""]
        named[1].value = "x"
        assert panel.get("cfg") == SimpleNamespace(rate_hz=0, name="x", name_2="")


def echo(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI returning its optional cluster control unchanged."""
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "c", NAMED, "c", 0, OPTIONAL)],
            outputs=[pane(vi, "output", "d", NAMED, "d", 1)],
            data_flow=[wire(vi, "c", "d")],
        ),
        [],
    )


def test_a_run_passes_the_unset_cluster_control_its_default(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Echo", echo)
    with project.generate().use() as module:
        panel = module("echo").echo.panel
        panel.run()
        assert panel.get("d") == SimpleNamespace(rate_hz=0, name="", name_2="")
