"""An Event Data Node's outputs are named by the event-data field each one
stores an index for -- not by their position -- in describe, lvnet and JSON."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.op_walk import stamp_nmux_lane_names
from lvkit.load_mode import LoadMode
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from tests.helpers import register_nodes

_SAMPLES = Path(".lvkit/cache/samples")
_VI = "events.vi"


def _prim(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _event_data_node(node_type: str) -> PrimitiveNode:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="Source", type=_prim("U32")),
            ClusterField(name="Type", type=_prim("U32")),
            ClusterField(name="Origin", type=_prim("String")),
            ClusterField(name="Module ID", type=_prim("I32")),
        ],
    )
    # The node lists its outputs out of field order, as the VI stores them.
    return PrimitiveNode(
        id=f"{_VI}::1",
        vi_path=_VI,
        node_type=node_type,
        terminals=[
            Terminal(
                id=f"{_VI}::2",
                index=0,
                direction="input",
                lv_type=cluster,
                nmux_role="agg",
            ),
            Terminal(
                id=f"{_VI}::3",
                index=1,
                direction="output",
                nmux_role="list",
                nmux_field_index=3,
            ),
            Terminal(
                id=f"{_VI}::4",
                index=2,
                direction="output",
                nmux_role="list",
                nmux_field_index=2,
            ),
        ],
    )


def _stamped_names(node_type: str) -> list[str | None]:
    graph = InMemoryVIGraph()
    node = _event_data_node(node_type)
    register_nodes(graph, [node], vi_name=_VI)
    stamp_nmux_lane_names(graph)
    return [t.display_name for t in node.terminals if t.nmux_role == "list"]


def test_event_data_node_outputs_are_named_by_their_stored_field_index() -> None:
    assert _stamped_names("eventDataNode") == ["Module ID", "Origin"]


def test_positional_unbundle_outputs_stay_unnamed() -> None:
    assert _stamped_names("demux") == [None, None]


def _graph(root: Path, relative: str) -> tuple[InMemoryVIGraph, str]:
    vi = root / relative
    if not vi.exists():
        pytest.skip(f"{vi} not present")
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi), LoadMode.MINIMAL, search_paths=[root], layout=False)
    return graph, graph.resolve_vi_name(vi.name)


def _event_data_outputs(graph: InMemoryVIGraph, vi_name: str) -> list[list[str | None]]:
    return [
        [
            t.display_name
            for t in sorted(node.terminals, key=lambda t: t.index)
            if t.nmux_role == "list"
        ]
        for node in graph.iter_nodes(vi_name)
        if node.node_type == "eventDataNode"
    ]


@pytest.mark.needs_samples
def test_timeout_and_value_change_frames_name_their_fields() -> None:
    graph, name = _graph(
        _SAMPLES / "JKI-VI-Tester",
        "source/LabVIEW Project Plugin/Support/Debug Interface.vi",
    )
    outputs = _event_data_outputs(graph, name)
    assert ["Type", "Time"] in outputs  # Timeout: Source's index is not stored
    assert ["Source", "Type", "Time", "CtlRef", "OldVal", "NewVal"] in outputs
    lvnet = render_lvnet(build_netlist_from_graph(graph, name))
    assert "out  Type : lveventtype" in lvnet
    assert "out  NewVal : TF" in lvnet
    assert "out  1 :" not in lvnet


@pytest.mark.needs_samples
def test_user_event_frames_name_fields_not_positions() -> None:
    graph, name = _graph(
        _SAMPLES / "configurable-dqmh-example",
        "source/LabVIEW Data/DQMH Module Templates/Source/Delacor/"
        "ConfigurableQMH/Main.vi",
    )
    outputs = _event_data_outputs(graph, name)
    # Listed in the node's own order, which is not the cluster's field order.
    assert ["Module ID", "Wait for Module to stop?", "Origin"] in outputs
    assert ["Module ID", "Fully Qualified Name", "Wait Notifier"] in outputs
    assert all(n for names in outputs for n in names)


@pytest.mark.needs_samples
def test_selector_from_an_event_data_node_names_the_field_in_every_view() -> None:
    graph, name = _graph(_SAMPLES / "lv-flex-channel-examples", "WaveGen/WaveGen.vi")
    text = describe_vi(graph, name)
    assert "gated on Event Data Node.Closed" in text
    assert "gated on Event Data Node.Continuous" in text
    assert "Event Data Node output" not in text
    lvnet = render_lvnet(build_netlist_from_graph(graph, name))
    assert "case Event_Data_Node_3146::Closed" in lvnet
    as_json = str(netlist_to_dict(build_netlist_from_graph(graph, name)))
    assert "'Continuous'" in as_json
