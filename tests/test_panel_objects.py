"""The objects of a front panel (``codegen/panel_layout.py``,
``codegen/panel_objects_module.py``, ``codegen/nodes/geometry_rows.py``): Panes[],
Decorations[] and All Objects[] of a panel, Controls[] and Origin of a pane, Decos[]
and AllObjs[] of a cluster, Position and Bounds of every object, a decoration's
Visible and Owning VI. The panel is a tiny front-panel heap (a splitter of two panes,
a decoration, a cluster with a field and a hidden decoration) read by the parser; each
case is a handmade diagram whose generated package is imported and run."""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from lvkit.codegen.gaps import Gap
from lvkit.codegen.panel_layout import layout_expression
from lvkit.graph.models import ConstantNode, GraphNode, VIContext
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.parser.fp_structure import read_panel_layout
from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel
from tests.class_graphs import INT, Project, pane, wire
from tests.test_control_references import REFNUM, REFNUMS, property_node
from tests.test_front_panel_reference import BOOL, ERROR

PANEL_DECORATIONS = (104105986,)
PANEL_ALL_OBJECTS = (104105987,)
PANEL_PANES = (104105990,)
PANE_CONTROLS = (404121601,)
CLUSTER_DECOS = (104102915,)
CLUSTER_ALL_OBJS = (104102916,)
POSITION = (103983104,)
LEFT = (103983105,)
TOP = (103983106,)
BOUNDS = (103983107,)
AREA_WIDTH = (103983108,)
OWNING_VI = (103970818,)
OWNER = (103970822,)
PANE_ORIGIN = (404121605,)
DECORATION_VISIBLE = (103992326,)

POSITION_TYPE = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[ClusterField(name="Left", type=INT), ClusterField(name="Top", type=INT)],
)
ORIGIN_TYPE = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    typedef_name="Origin",
    fields=[
        ClusterField(name="Horizontal", type=INT),
        ClusterField(name="Vertical", type=INT),
    ],
)
CLUSTER = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[ClusterField(name="a", type=INT)],
)

# A splitter of two panes side by side. The first holds the number x, a decoration
# and the cluster c (whose own pane holds its field a and a hidden decoration); the
# second holds the indicator out.
HEAP = """
<heap><root>
<paneHierarchy class="splitter" uid="1">
  <bounds>(0, 0, 200, 400)</bounds>
  <ratio>3F000000</ratio>
  <topOrLeft class="pane" uid="2">
    <bounds>(0, 0, 200, 200)</bounds>
    <zPlaneList elements="3">
      <SL__arrayElement class="fPDCO" uid="10"><ddo class="stdNum" uid="11"/>
      </SL__arrayElement>
      <SL__arrayElement class="cosm" uid="12">
        <bounds>(150, 10, 190, 90)</bounds><objFlags>0</objFlags>
        <image><ImageResID>-100</ImageResID></image>
      </SL__arrayElement>
      <SL__arrayElement class="fPDCO" uid="20">
        <ddo class="stdClust" uid="21">
          <paneHierarchy class="pane" uid="22"><zPlaneList elements="2">
            <SL__arrayElement class="stdNum" uid="30"/>
            <SL__arrayElement class="cosm" uid="31">
              <bounds>(1, 2, 3, 4)</bounds><objFlags>8</objFlags>
              <image><ImageResID>-100</ImageResID></image>
            </SL__arrayElement>
          </zPlaneList></paneHierarchy>
        </ddo>
      </SL__arrayElement>
    </zPlaneList>
  </topOrLeft>
  <botOrRight class="pane" uid="3">
    <bounds>(0, 200, 200, 400)</bounds>
    <zPlaneList elements="1">
      <SL__arrayElement class="fPDCO" uid="40"><ddo class="stdNum" uid="41"/>
      </SL__arrayElement>
    </zPlaneList>
  </botOrRight>
</paneHierarchy>
</root></heap>
"""

NAMES = {"10": "x", "20": "c", "40": "out"}


def front_panel() -> ParsedFrontPanel:
    """The tiny panel, read as the parser reads a heap."""
    field = ParsedFPControl(
        uid="30", name="a", control_type="stdNum", bounds=(5, 6, 25, 106)
    )
    controls = [
        ParsedFPControl(
            uid="10", name="x", control_type="stdNum", bounds=(10, 20, 40, 120)
        ),
        ParsedFPControl(
            uid="20",
            name="c",
            control_type="stdClust",
            bounds=(50, 20, 150, 220),
            children=[field],
        ),
        ParsedFPControl(
            uid="40", name="out", control_type="stdNum", bounds=(8, 8, 30, 90)
        ),
    ]
    by_uid = {c.uid: c for c in [*controls, field]}
    layout, unread = read_panel_layout(ET.fromstring(HEAP), by_uid)
    assert unread == []
    return ParsedFrontPanel(
        controls=controls, panel_bounds=(0, 0, 200, 400), layout=layout
    )


def test_the_layout_is_written_from_the_panels_object_tree() -> None:
    expression = layout_expression(front_panel(), NAMES)
    assert expression is not None
    assert ast.literal_eval(expression) == {
        "panes": (
            (
                ("c", "x", 10, 20, 40, 120),
                ("d", 150, 10, 190, 90, False),
                ("c", "c", 50, 20, 150, 220),
            ),
            (("c", "out", 8, 8, 30, 90),),
        ),
        "clusters": {
            "c": ((("f", 0, 5, 6, 25, 106), ("d", 1, 2, 3, 4, True)), (None,))
        },
    }


def test_a_panel_with_no_object_tree_has_no_layout() -> None:
    assert layout_expression(None, NAMES) is None
    assert (
        layout_expression(
            ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 0, 0)), NAMES
        )
        is None
    )


def target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """The VI whose panel is ``front_panel``: the number x, the cluster c and the
    indicator out, with a Property Node bound to x reading its Left and one bound to
    c reading its Decos[]."""
    x = pane(vi, "input", "x", INT, "x", 0, tab_index=0, control_type="stdNum")
    cluster = pane(vi, "input", "c", CLUSTER, "c", 1, tab_index=1)
    out = pane(vi, "output", "out", INT, "out", 2, tab_index=2, control_type="stdNum")
    left = property_node(vi, "l", "Num", [("left", LEFT, "output", INT)]).model_copy(
        update={"bound_control_uid": "x-uid", "bound_control_dco_uid": "10"}
    )
    decos = property_node(
        vi, "d", "Clust", [("decos", CLUSTER_DECOS, "output", REFNUMS)]
    ).model_copy(update={"bound_control_uid": "c-uid", "bound_control_dco_uid": "20"})
    owner = property_node(
        vi, "w", "Num", [("owner", OWNER, "output", REFNUM)]
    ).model_copy(update={"bound_control_uid": "x-uid", "bound_control_dco_uid": "10"})
    return (
        VIContext(
            name=vi,
            inputs=[
                x.model_copy(update={"fp_dco_uid": "10"}),
                cluster.model_copy(update={"fp_dco_uid": "20"}),
            ],
            outputs=[
                out.model_copy(update={"fp_dco_uid": "40"}),
                pane(vi, "output", "bound_left", INT, "bound left", 3),
                pane(vi, "output", "bound_decos", REFNUMS, "bound decos", 4),
                pane(vi, "output", "bound_owner", REFNUM, "bound owner", 5),
            ],
            data_flow=[
                wire(vi, "x", "out"),
                wire(vi, "l.left", "bound_left"),
                wire(vi, "d.decos", "bound_decos"),
                wire(vi, "w.owner", "bound_owner"),
            ],
        ),
        [left, decos, owner],
    )


def reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """Reads the rows of the object reference ``ctl`` (a control or decoration)."""
    node = property_node(
        vi,
        "o",
        "Decor",
        [
            ("position", POSITION, "output", POSITION_TYPE),
            ("left", LEFT, "output", INT),
            ("top", TOP, "output", INT),
            ("bounds", BOUNDS, "output", POSITION_TYPE),
            ("width", AREA_WIDTH, "output", INT),
            ("owner", OWNING_VI, "output", REFNUM),
        ],
    )
    names = ["position", "left", "top", "bounds", "width", "owner"]
    types = [POSITION_TYPE, INT, INT, POSITION_TYPE, INT, REFNUM]
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                *(
                    pane(vi, "output", name, kind, name, 1 + i)
                    for i, (name, kind) in enumerate(zip(names, types, strict=True))
                ),
                pane(vi, "output", "err", ERROR, "error out", 7),
            ],
            data_flow=[
                wire(vi, "ctl", "o.ref"),
                *(wire(vi, f"o.{name}", name) for name in names),
                wire(vi, "o.err_out", "err"),
            ],
        ),
        [node],
    )


def owner_of(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """Reads Owner of the reference ``ctl`` (a control, a field, a decoration, a pane
    or the panel)."""
    node = property_node(vi, "o", "GObj", [("owner", OWNER, "output", REFNUM)])
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "owner", REFNUM, "owner", 1),
                pane(vi, "output", "err", ERROR, "error out", 2),
            ],
            data_flow=[
                wire(vi, "ctl", "o.ref"),
                wire(vi, "o.owner", "owner"),
                wire(vi, "o.err_out", "err"),
            ],
        ),
        [node],
    )


def mover(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """Writes Position:Left of the reference ``ctl`` from ``left``."""
    node = property_node(vi, "o", "Ctl", [("left", LEFT, "input", INT)])
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                pane(vi, "input", "left", INT, "left", 1),
            ],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
            data_flow=[
                wire(vi, "ctl", "o.ref"),
                wire(vi, "left", "o.left"),
                wire(vi, "o.err_out", "err"),
            ],
        ),
        [node],
    )


def hider(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """Writes Visible FALSE to the decoration reference ``ctl``."""
    node = property_node(
        vi, "o", "Decor", [("visible", DECORATION_VISIBLE, "input", BOOL)]
    )
    false = ConstantNode(
        id=f"{vi}:false",
        vi_path=vi,
        name="false",
        value="False",
        lv_type=BOOL,
        terminals=[
            Terminal(id=f"{vi}:false", index=0, direction="output", lv_type=BOOL)
        ],
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 1)],
            data_flow=[
                wire(vi, "ctl", "o.ref"),
                wire(vi, "false", "o.visible"),
                wire(vi, "o.err_out", "err"),
            ],
        ),
        [node, false],
    )


def lister(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """Reads the Decorations[], All Objects[] and Panes[] of the panel ``ctl`` and
    the Controls[] of the pane ``pane``."""
    panel = property_node(
        vi,
        "p",
        "Pnl",
        [
            ("decorations", PANEL_DECORATIONS, "output", REFNUMS),
            ("everything", PANEL_ALL_OBJECTS, "output", REFNUMS),
            ("panes", PANEL_PANES, "output", REFNUMS),
        ],
    )
    inside = property_node(
        vi, "q", "Pane", [("controls", PANE_CONTROLS, "output", REFNUMS)]
    )
    origin = property_node(
        vi, "r", "Pane", [("origin", PANE_ORIGIN, "output", ORIGIN_TYPE)]
    )
    names = ["decorations", "everything", "panes"]
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "panel", REFNUM, "panel", 0),
                pane(vi, "input", "pane", REFNUM, "pane", 1),
            ],
            outputs=[
                *(
                    pane(vi, "output", name, REFNUMS, name, 2 + i)
                    for i, name in enumerate(names)
                ),
                pane(vi, "output", "controls", REFNUMS, "controls", 5),
                pane(vi, "output", "origin", ORIGIN_TYPE, "origin", 6),
                pane(vi, "output", "err", ERROR, "error out", 7),
            ],
            data_flow=[
                wire(vi, "panel", "p.ref"),
                wire(vi, "pane", "q.ref"),
                wire(vi, "pane", "r.ref"),
                *(wire(vi, f"p.{name}", name) for name in names),
                wire(vi, "q.controls", "controls"),
                wire(vi, "r.origin", "origin"),
                wire(vi, "p.err_out", "err"),
            ],
        ),
        [panel, inside, origin],
    )


def cluster_lister(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """Reads the Decos[] and AllObjs[] of the cluster reference ``ctl``."""
    node = property_node(
        vi,
        "c",
        "Clust",
        [
            ("decos", CLUSTER_DECOS, "output", REFNUMS),
            ("everything", CLUSTER_ALL_OBJS, "output", REFNUMS),
        ],
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "decos", REFNUMS, "decos", 1),
                pane(vi, "output", "everything", REFNUMS, "everything", 2),
                pane(vi, "output", "err", ERROR, "error out", 3),
            ],
            data_flow=[
                wire(vi, "ctl", "c.ref"),
                wire(vi, "c.decos", "decos"),
                wire(vi, "c.everything", "everything"),
                wire(vi, "c.err_out", "err"),
            ],
        ),
        [node],
    )


def generated(tmp_path: Path, gaps: list[Gap]) -> Any:
    project = Project(tmp_path)
    key = project.add_vi("Target", target)
    project.graph._graph.nodes[key]["node"].front_panel = front_panel()  # noqa: SLF001
    project.add_vi("Reader", reader)
    project.add_vi("Mover", mover)
    project.add_vi("OwnerOf", owner_of)
    project.add_vi("Hider", hider)
    project.add_vi("Lister", lister)
    project.add_vi("ClusterLister", cluster_lister)
    return project.generate(gaps)


class Views:
    """A view that records what the panel tells it about objects."""

    def __init__(self) -> None:
        self.log: list[tuple[Any, ...]] = []

    def geometry_changed(self, name: str, prop: str, value: Any) -> None:
        self.log.append(("geometry", name, prop, value))

    def property_changed(self, name: str, prop: str, value: Any) -> None:
        self.log.append(("property", name, prop, value))


def test_the_panels_lists_are_the_object_tree(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = generated(tmp_path, gaps)
    assert [str(g) for g in gaps if "gap kind" in str(g)] == []
    assert "layout={'panes': " in package.sources["target"]
    with package.use() as module:
        target_panel = module("target").target.panel
        result = module("lister").lister(panel=target_panel, pane=target_panel.panes[0])
        assert result.error_out is None
        first, second = result.panes
        assert result.panes == target_panel.panes
        (decoration,) = result.decorations
        assert result.everything == [
            *first.controls[:1],
            decoration,
            first.controls[1],
            *second.controls,
        ]
        assert [c.label for c in result.controls] == ["x", "c"]
        assert [c.label for c in second.controls] == ["out"]
        # a pane starts unscrolled
        assert result.origin.horizontal == 0 and result.origin.vertical == 0


def test_a_pane_scrolls_and_the_views_are_told(tmp_path: Path) -> None:
    with generated(tmp_path, []).use() as module:
        panel = module("target").target.panel
        views = Views()
        panel.attach(views)
        panel.panes[1].origin = (4, 9)
        assert panel.panes[1].origin == (4, 9)
        assert panel.panes[0].origin == (0, 0)
        assert views.log == [("geometry", "pane 1", "origin", (4, 9))]


def test_a_control_has_its_stored_rectangle(tmp_path: Path) -> None:
    with generated(tmp_path, []).use() as module:
        panel = module("target").target.panel
        x = next(c for c in panel.controls if c.label == "x")
        result = module("reader").reader(ctl=x)
        assert result.error_out is None
        assert (result.position, result.left, result.top) == ((20, 10), 20, 10)
        assert (result.bounds, result.width) == ((100, 30), 100)
        assert result.owner is module("target").target
        # a write of Left moves the rectangle and keeps its size
        views = Views()
        panel.attach(views)
        assert module("mover").mover(ctl=x, left=35).error_out is None
        assert module("reader").reader(ctl=x).position == (35, 10)
        assert module("reader").reader(ctl=x).bounds == (100, 30)
        assert views.log == [("geometry", "x", "position", (35, 10))]


def test_a_decoration_has_its_rectangle_and_its_visible(tmp_path: Path) -> None:
    with generated(tmp_path, []).use() as module:
        panel = module("target").target.panel
        (decoration,) = panel.decorations
        result = module("reader").reader(ctl=decoration)
        assert (result.position, result.bounds) == ((10, 150), (80, 40))
        assert decoration.get_property("visible") is True
        views = Views()
        panel.attach(views)
        assert module("hider").hider(ctl=decoration).error_out is None
        assert decoration.get_property("visible") is False
        assert views.log == [("property", "decoration 0", "visible", False)]


def test_a_cluster_has_its_own_decorations_and_objects(tmp_path: Path) -> None:
    with generated(tmp_path, []).use() as module:
        panel = module("target").target.panel
        cluster = next(c for c in panel.controls if c.label == "c")
        result = module("clusterlister").clusterlister(ctl=cluster)
        assert result.error_out is None
        (decoration,) = result.decos
        assert decoration.get_property("visible") is False
        field, same = result.everything
        assert (field.label, same) == ("a", decoration)
        assert module("reader").reader(ctl=field).position == (6, 5)
        # a panel's decorations do not include the cluster's
        assert decoration not in panel.decorations
        # a control that is not a cluster is error 1026
        plain = next(c for c in panel.controls if c.label == "x")
        failed = module("clusterlister").clusterlister(ctl=plain)
        assert failed.error_out.code == 1026


def test_an_object_is_owned_by_its_pane_or_cluster_and_a_pane_by_the_panel(
    tmp_path: Path,
) -> None:
    """NI's Owner: the pane holds a control and a decoration, a cluster holds its
    fields and decorations, the panel holds the panes and has no owner itself."""
    gaps: list[Gap] = []
    package = generated(tmp_path, gaps)
    assert [str(g) for g in gaps if "Owner" in str(g)] == []
    with package.use() as module:
        panel = module("target").target.panel
        owner_of = module("ownerof").ownerof
        first, second = panel.panes
        x, cluster = (c for c in panel.controls if c.label in ("x", "c"))
        (out,) = second.controls
        (free,) = panel.decorations
        assert owner_of(ctl=x).owner is first
        assert owner_of(ctl=cluster).owner is first
        assert owner_of(ctl=free).owner is first
        assert owner_of(ctl=out).owner is second
        field, inner = cluster.all_objects
        assert owner_of(ctl=field).owner is cluster
        assert owner_of(ctl=inner).owner is cluster
        assert owner_of(ctl=first).owner is panel
        assert owner_of(ctl=second).owner is panel
        assert owner_of(ctl=panel).owner is None
        assert owner_of(ctl=panel).error_out is None
        assert owner_of(ctl=None).error_out.code == 1026


def test_a_property_node_bound_to_a_control_reads_its_rows(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = generated(tmp_path, gaps)
    assert [str(g) for g in gaps if "Target" in str(g)] == []
    with package.use() as module:
        result = module("target").target(x=3, c=(1,))
        assert result.bound_left == 20
        panel = module("target").target.panel
        cluster = next(c for c in panel.controls if c.label == "c")
        assert result.bound_decos == cluster.decorations
        assert result.bound_owner is panel.panes[0]


def test_bounds_and_the_panels_lists_cannot_be_written(tmp_path: Path) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Target", target)
    project.graph._graph.nodes[key]["node"].front_panel = front_panel()  # noqa: SLF001

    def writer(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(
            vi, "o", "Ctl", [("bounds", BOUNDS, "input", POSITION_TYPE)]
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                    pane(vi, "input", "size", POSITION_TYPE, "size", 1),
                ],
                outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
                data_flow=[wire(vi, "ctl", "o.ref"), wire(vi, "size", "o.bounds")],
            ),
            [node],
        )

    project.add_vi("Writer", writer)
    gaps: list[Gap] = []
    project.generate(gaps)
    assert any("cannot be set while the VI runs" in str(g) for g in gaps)


def test_a_vi_with_no_pane_hierarchy_raises_for_its_panes(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Target", target)
    project.add_vi("Lister", lister)
    with project.generate().use() as module:
        panel = module("target").target.panel
        try:
            panel.panes
        except NotImplementedError as error:
            assert "Panes[]" in str(error)
        else:
            raise AssertionError("expected NotImplementedError")


def test_the_support_module_imports_nothing_from_lvkit() -> None:
    from lvkit.codegen.panels_module import PANELS_MODULE_SOURCE

    assert "lvkit" not in PANELS_MODULE_SOURCE


def test_the_object_classes_are_in_the_panels_module() -> None:
    from lvkit.codegen.panels_module import PANELS_MODULE_SOURCE

    for name in ("class GObject", "class Decoration", "class Pane", "def as_pane"):
        assert name in PANELS_MODULE_SOURCE
    assert FPTerminal  # the terminals the cases use
