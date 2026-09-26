"""A ``.ctl`` typedef keeps its ROOT type on the graph node (a cluster, an enum,
a scalar/refnum, ...), and a class whose private data is a separate control
registers that control as a typedef node.

Backed by the sample corpus (``needs_samples``): real controls of each root kind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.models import LVTypeKind

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_ENUM_CTL = _SAMPLES / "DCAF-DAQModule/source/editor node/Permissions Enum.ctl"
_CLUSTER_CTL = (
    _SAMPLES / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/API_Test Settings.ctl"
)
_REFNUM_CTL = (
    _SAMPLES / "JKI-VI-Tester/source/Classes/TestCase/private/testMethod.ctl"
)
_PQ_DIR = _SAMPLES / "actor-framework/Core/ActorFramework/Message Priority Queue"
_PQ_CLASS = _PQ_DIR / "Message Priority Queue.lvclass"


def _typedef_node(path: Path) -> dict:
    g = InMemoryVIGraph()
    key = g.load_typedef(path)
    return g._dep_graph.nodes[key]


@pytest.mark.needs_samples
@pytest.mark.skipif(not _ENUM_CTL.exists(), reason="DCAF sample absent")
def test_enum_control_keeps_its_root_type_and_values():
    node = _typedef_node(_ENUM_CTL)
    assert node["root_type"].kind == LVTypeKind.ENUM
    assert node["root_type"].values  # its items survive
    assert node["fields"] is None  # an enum has no cluster fields


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_cluster_control_root_type_carries_its_fields():
    node = _typedef_node(_CLUSTER_CTL)
    assert node["root_type"].kind == LVTypeKind.CLUSTER
    assert node["fields"] == node["root_type"].fields
    assert {"Font", "Size", "Text color"} <= {f.name for f in node["fields"]}


@pytest.mark.needs_samples
@pytest.mark.skipif(not _REFNUM_CTL.exists(), reason="JKI sample absent")
def test_scalar_control_keeps_a_root_type_and_no_fields():
    node = _typedef_node(_REFNUM_CTL)
    assert node["root_type"] is not None
    assert not node["fields"]


@pytest.mark.needs_samples
@pytest.mark.skipif(not _PQ_CLASS.exists(), reason="actor-framework sample absent")
def test_class_private_data_control_becomes_a_typedef_node():
    g = InMemoryVIGraph()
    cls_key = g.load_lvclass(_PQ_CLASS, LoadMode.MINIMAL)
    edges = [
        (target, data)
        for _, target, data in g._dep_graph.out_edges(cls_key, data=True)
        if data.get("rel") == "private_data"
    ]
    assert len(edges) == 1
    ctl_key = edges[0][0]
    assert Path(ctl_key).name == "DVR Contents.ctl"
    assert g._dep_graph.nodes[ctl_key]["node_type"] == "typedef"
    assert not g.is_stub(ctl_key)
    # the class's own fields are that control's fields
    nodes = g._dep_graph.nodes
    assert nodes[cls_key]["fields"] == nodes[ctl_key]["fields"]


def test_absent_control_is_a_stub(tmp_path: Path):
    g = InMemoryVIGraph()
    key = g.load_typedef(tmp_path / "Missing.ctl")
    assert g.is_stub(key)
    assert g._dep_graph.nodes[key]["node_type"] == "typedef"
