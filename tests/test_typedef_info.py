"""``get_typedef`` / ``list_typedefs`` / ``load_ctl_by_path`` / ``typedef_to_dict``:
a ``.ctl`` typedef as a graph read model.

Hermetic tests build the dependency graph directly; the sample-backed ones read
real controls (``needs_samples``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import load_ctl_by_path
from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.netlist_json import typedef_to_dict
from lvkit.graph.node_kinds import EdgeRel, NodeType
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel

from .conftest import SAMPLES_ROOT

_ENUM_CTL = SAMPLES_ROOT / "DCAF-DAQModule/source/editor node/Permissions Enum.ctl"
_ICON_API = SAMPLES_ROOT / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API"
_CLUSTER_CTL = _ICON_API / "API_Test Settings.ctl"
_ICON_LVLIB = _ICON_API / "LabVIEW Icon API.lvlib"

_I32 = LVType(LVTypeKind.PRIMITIVE, underlying_type="I32")


def _graph_with_typedef(key: str = "/p/Foo.ctl") -> InMemoryVIGraph:
    g = InMemoryVIGraph()
    g._dep_graph.add_node(
        key, node_type=NodeType.TYPEDEF, fields=None, root_type=_I32, front_panel=None
    )
    return g


# --- hermetic -----------------------------------------------------------------


def test_get_typedef_reads_root_type_and_no_users() -> None:
    g = _graph_with_typedef()
    info = g.get_typedef("/p/Foo.ctl")
    assert info.name == "Foo.ctl" and info.root_type is _I32
    assert info.fields == () and not info.uses and not info.used_by


def test_owners_and_users_are_split_and_self_is_excluded() -> None:
    g = _graph_with_typedef()
    dg = g._dep_graph
    dg.add_node("/p/Lib.lvlib", node_type=NodeType.LIBRARY, qname="Lib.lvlib")
    dg.add_node("/p/Cls.lvclass", node_type=NodeType.CLASS, qname="Cls.lvclass")
    dg.add_node("/p/use.vi", node_type=NodeType.VI, qname="use.vi")
    dg.add_node("/p/Dep.ctl", node_type=NodeType.TYPEDEF, qname="Dep.ctl")
    dg.add_edge("/p/Lib.lvlib", "/p/Foo.ctl", rel=EdgeRel.OWNS)
    dg.add_edge("/p/Cls.lvclass", "/p/Foo.ctl", rel=EdgeRel.PRIVATE_DATA)
    dg.add_edge("/p/use.vi", "/p/Foo.ctl")
    dg.add_edge("/p/Foo.ctl", "/p/Foo.ctl")  # a self-edge is never a user
    dg.add_edge("/p/Foo.ctl", "/p/Dep.ctl")
    info = g.get_typedef("/p/Foo.ctl")
    assert [(r.node_type, r.qualified) for r in info.owned_by] == [
        (NodeType.CLASS, "Cls.lvclass"),
        (NodeType.LIBRARY, "Lib.lvlib"),
    ]
    assert [(r.node_type, r.qualified) for r in info.used_by] == [
        (NodeType.VI, "use.vi")
    ]
    assert [(r.node_type, r.qualified) for r in info.uses] == [
        (NodeType.TYPEDEF, "Dep.ctl")
    ]


def test_display_name_is_qualified_by_the_owning_library() -> None:
    g = _graph_with_typedef()
    g._dep_graph.add_node("/p/Lib.lvlib", node_type=NodeType.LIBRARY, qname="Lib.lvlib")
    g._dep_graph.add_edge("/p/Lib.lvlib", "/p/Foo.ctl", rel=EdgeRel.OWNS)
    assert g.get_typedef("/p/Foo.ctl").name == "Lib.lvlib:Foo.ctl"


def test_an_enum_default_is_shown_as_its_item_name() -> None:
    enum = LVType(
        LVTypeKind.ENUM,
        values={"Read": EnumValue(0), "Write": EnumValue(1)},
    )
    root = ParsedFPControl(
        uid="1", name="Mode", control_type="stdRing", bounds=(0, 0, 20, 80),
        default_value="1",
    )
    g = InMemoryVIGraph()
    g._dep_graph.add_node(
        "/p/Mode.ctl", node_type=NodeType.TYPEDEF, fields=None, root_type=enum,
        front_panel=ParsedFrontPanel(controls=[root], panel_bounds=(0, 0, 100, 100)),
    )
    assert g.get_typedef("/p/Mode.ctl").default == "Write"


def test_cluster_fields_take_their_defaults_by_label() -> None:
    fields = [ClusterField("Gain", _I32), ClusterField("Name", None)]
    children = [
        ParsedFPControl(
            uid="2", name="Gain", control_type="stdNum", bounds=(0, 0, 10, 10),
            default_value="5",
        ),
    ]
    root = ParsedFPControl(
        uid="1", name="Cfg", control_type="stdClust", bounds=(0, 0, 40, 40),
        children=children,
    )
    g = InMemoryVIGraph()
    g._dep_graph.add_node(
        "/p/Cfg.ctl", node_type=NodeType.TYPEDEF, fields=fields,
        root_type=LVType(LVTypeKind.CLUSTER, fields=fields),
        front_panel=ParsedFrontPanel(controls=[root], panel_bounds=(0, 0, 100, 100)),
    )
    info = g.get_typedef("/p/Cfg.ctl")
    assert [(f.name, f.default) for f in info.fields] == [("Gain", "5"), ("Name", None)]


def test_stubs_are_not_typedefs() -> None:
    g = _graph_with_typedef()
    g._dep_graph.add_node("/p/Gone.ctl", node_type=NodeType.TYPEDEF)
    g._stubs.add("/p/Gone.ctl")
    with pytest.raises(ValueError, match="Not a loaded typedef"):
        g.get_typedef("/p/Gone.ctl")
    assert g.list_typedefs() == ["/p/Foo.ctl"]


def test_a_typedef_without_a_root_type_is_an_error() -> None:
    g = InMemoryVIGraph()
    g._dep_graph.add_node("/p/A.ctl", node_type=NodeType.TYPEDEF, root_type=None)
    with pytest.raises(ValueError, match="no resolvable root type"):
        g.get_typedef("/p/A.ctl")


def test_list_typedefs_is_ordered_by_display_name_then_path() -> None:
    g = InMemoryVIGraph()
    for key in ("/z/B.ctl", "/a/B.ctl", "/m/A.ctl"):
        g._dep_graph.add_node(key, node_type=NodeType.TYPEDEF, root_type=_I32)
    assert g.list_typedefs() == ["/m/A.ctl", "/a/B.ctl", "/z/B.ctl"]


def test_a_control_with_no_root_type_is_unreadable(tmp_path: Path, monkeypatch):
    """A control whose XML parses but whose root type can't be resolved is a
    stub, so ``load_ctl_by_path`` raises at load time (not later, at read)."""
    ctl = tmp_path / "NoRoot.ctl"
    ctl.write_bytes(b"x")
    monkeypatch.setattr(
        InMemoryVIGraph, "_ctl_root", lambda self, p: (None, {1: _I32}, None)
    )
    with pytest.raises(ValueError, match="Could not read NoRoot.ctl"):
        load_ctl_by_path(ctl)


def test_load_ctl_by_path_rejects_a_missing_or_unreadable_control(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_ctl_by_path(tmp_path / "Missing.ctl")
    bad = tmp_path / "Bad.ctl"
    bad.write_bytes(b"not a labview file")
    with pytest.raises(ValueError, match="Could not read Bad.ctl"):
        load_ctl_by_path(bad)


def test_typedef_to_dict_shape_and_verbose() -> None:
    g = _graph_with_typedef()
    info = g.get_typedef("/p/Foo.ctl")
    plain = typedef_to_dict(info)
    assert plain["typedef"] == "Foo.ctl" and plain["kind"] == "primitive"
    # a standalone read has no users or owners: those keys are absent, not []
    assert {"used_by", "owned_by", "root_type"}.isdisjoint(plain)
    assert typedef_to_dict(info, verbose=True)["root_type"]["kind"] == "primitive"


# --- real controls --------------------------------------------------------------


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_cluster_control_fields_carry_nested_fields_and_defaults():
    g, key = load_ctl_by_path(_CLUSTER_CTL)
    info = g.get_typedef(key)
    assert info.root_type.kind == LVTypeKind.CLUSTER
    by_name = {f.name: f for f in info.fields}
    assert {"Font", "Size", "Text color"} <= set(by_name)
    assert len(by_name["Text color"].fields) == 4  # the nested cluster
    assert g.get_typedef_front_panel(key) is not None
    assert typedef_to_dict(info)["fields"][0]["name"] == info.fields[0].name


@pytest.mark.needs_samples
@pytest.mark.skipif(not _ENUM_CTL.exists(), reason="DCAF sample absent")
def test_enum_control_without_a_recorded_default_has_none():
    g, key = load_ctl_by_path(_ENUM_CTL)
    info = g.get_typedef(key)
    assert info.root_type.kind == LVTypeKind.ENUM
    assert info.fields == ()
    assert info.default is None  # this control's heap records no DefaultData


@pytest.mark.needs_samples
@pytest.mark.skipif(not _ICON_LVLIB.exists(), reason="icon-editor sample absent")
def test_a_control_loaded_standalone_and_through_its_library():
    """The same file: bare when loaded alone, library-qualified with an owner
    when its library was loaded; and no loaded control points at itself."""
    alone, key = load_ctl_by_path(_CLUSTER_CTL)
    assert alone.get_typedef(key).name == "API_Test Settings.ctl"
    assert not alone.get_typedef(key).owned_by

    g = InMemoryVIGraph()
    g.load_lvlib(_ICON_LVLIB, LoadMode.MINIMAL)
    keys = g.list_typedefs()
    assert key in keys
    info = g.get_typedef(key)
    assert info.name == "LabVIEW Icon API.lvlib:API_Test Settings.ctl"
    assert [r.node_type for r in info.owned_by] == [NodeType.LIBRARY]
    for k in keys:
        assert not g._dep_graph.has_edge(k, k)
        assert k not in [r.path for r in g.get_typedef(k).uses]
