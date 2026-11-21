"""A typedef cluster wired to a cluster saved without its typedef: the wire is one
value, so a Bundle's cluster terminal takes the typedef, and so does a call of a
malleable VI (its instance adapts to the wired type). An ordinary subVI call's
terminal keeps the type the called VI declares."""

from __future__ import annotations

import networkx as nx

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, VINode, WireEnd
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
VI = "Caller.vi"


def _cluster(typedef_name: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name=typedef_name,
        fields=[
            ClusterField(name="Label", type=STRING),
            ClusterField(name="Gain", type=DBL),
        ],
    )


def _wired_consumers() -> dict[str, Terminal]:
    """One typedef-cluster source wired to a Bundle, an ordinary call and a
    malleable-VI call, each taking a cluster saved without the typedef."""
    g = nx.MultiDiGraph()
    source = Terminal(
        id="src_t", index=0, direction="output", lv_type=_cluster("Setting.ctl")
    )
    g.add_node(
        f"{VI}::src",
        node=PrimitiveNode(id=f"{VI}::src", vi_path=VI, terminals=[source]),
    )
    consumers: dict[str, Terminal] = {}
    for name, make in {
        "bundle": lambda nid, t: PrimitiveNode(
            id=nid, vi_path=VI, node_type="nMux", terminals=[t]
        ),
        "call": lambda nid, t: VINode(id=nid, vi_path=VI, terminals=[t]),
        "malleable": lambda nid, t: VINode(
            id=nid, vi_path=VI, terminals=[t], is_embedded_instance=True
        ),
    }.items():
        nid = f"{VI}::{name}"
        term = Terminal(
            id=f"{name}_t", index=0, direction="input", lv_type=_cluster(None)
        )
        consumers[name] = term
        g.add_node(nid, node=make(nid, term))
        g.add_edge(
            f"{VI}::src",
            nid,
            source=WireEnd(terminal_id="src_t", node_id=f"{VI}::src"),
            dest=WireEnd(terminal_id=term.id, node_id=nid),
        )
    InMemoryVIGraph()._propagate_types_and_rematch(g, set(g.nodes))
    return consumers


def test_a_bundle_terminal_takes_the_wired_typedef() -> None:
    assert _wired_consumers()["bundle"].lv_type.typedef_name == "Setting.ctl"


def test_a_malleable_vi_call_terminal_takes_the_wired_typedef() -> None:
    assert _wired_consumers()["malleable"].lv_type.typedef_name == "Setting.ctl"


def test_an_ordinary_call_terminal_keeps_the_declared_cluster() -> None:
    declared = _wired_consumers()["call"].lv_type
    assert declared.typedef_name is None
    assert [f.name for f in declared.fields] == ["Label", "Gain"]
