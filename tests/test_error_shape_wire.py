"""A cluster of the error cluster's shape (a Boolean, an I32 and a String, each
label the expected one or blank) is the error cluster only where a wire joins it
to the error cluster, and the identity follows the wires. Joined to plain data it
stays a plain cluster."""

from __future__ import annotations

import networkx as nx

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, VINode, WireEnd
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal

BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
VI = "Caller.vi"


def _cluster(
    names: tuple[str | None, str | None, str | None] = ("status", "code", None),
) -> LVType:
    """A status/code/source cluster; a ``None`` name is a field with no label."""
    types = (BOOL, I32, STRING)
    return LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(
                name=name or f"field_{i}", type=types[i], labelled=name is not None
            )
            for i, name in enumerate(names)
        ],
    )


def _error_cluster() -> LVType:
    return _cluster(("status", "code", "source"))


def _run(
    source: LVType,
    consumers: dict[str, tuple[str, LVType]],
) -> dict[str, Terminal]:
    """One control terminal of type ``source`` wired to each consumer: a node
    kind (``call`` an ordinary subVI call, ``unbundle`` an Unbundle, ``data`` a
    node of no particular kind) and the type its terminal has."""
    g = nx.MultiDiGraph()
    control = Terminal(id="control_t", index=0, direction="output", lv_type=source)
    g.add_node(
        f"{VI}::control",
        node=PrimitiveNode(id=f"{VI}::control", vi_path=VI, terminals=[control]),
    )
    terms: dict[str, Terminal] = {"control": control}
    for name, (kind, lv_type) in consumers.items():
        nid = f"{VI}::{name}"
        term = Terminal(id=f"{name}_t", index=0, direction="input", lv_type=lv_type)
        terms[name] = term
        if kind == "call":
            node = VINode(id=nid, vi_path=VI, terminals=[term])
        else:
            node = PrimitiveNode(
                id=nid,
                vi_path=VI,
                node_type="nMux" if kind == "unbundle" else "prim",
                terminals=[term],
            )
        g.add_node(nid, node=node)
        g.add_edge(
            f"{VI}::control",
            nid,
            source=WireEnd(terminal_id="control_t", node_id=f"{VI}::control"),
            dest=WireEnd(terminal_id=term.id, node_id=nid),
        )
    InMemoryVIGraph()._propagate_types_and_rematch(g, set(g.nodes))
    return terms


def test_a_shape_match_control_wired_to_an_error_in_is_the_error_cluster() -> None:
    terms = _run(_cluster(), {"node": ("data", _error_cluster())})
    assert terms["control"].is_error_cluster
    assert [f.name for f in terms["control"].lv_type.fields] == [
        "status",
        "code",
        "source",
    ]


def test_the_identity_follows_the_wire_beyond_it() -> None:
    terms = _run(
        _cluster(),
        {
            "node": ("data", _error_cluster()),
            "beyond": ("unbundle", _cluster((None, None, None))),
        },
    )
    assert terms["control"].is_error_cluster
    assert terms["beyond"].is_error_cluster


def test_a_shape_match_wired_to_a_subvi_error_in_takes_the_declared_error() -> None:
    terms = _run(_cluster(), {"call": ("call", _error_cluster())})
    assert terms["control"].is_error_cluster
    assert terms["call"].is_error_cluster


def test_a_shape_match_on_a_subvi_pane_keeps_its_declared_type() -> None:
    terms = _run(_error_cluster(), {"call": ("call", _cluster())})
    assert not terms["call"].is_error_cluster
    assert terms["control"].is_error_cluster


def test_a_shape_match_wired_only_to_plain_data_stays_a_plain_cluster() -> None:
    terms = _run(_cluster(), {"node": ("unbundle", _cluster((None, None, None)))})
    assert not terms["control"].is_error_cluster
    assert not terms["node"].is_error_cluster


def test_a_non_blank_different_label_stays_a_plain_cluster() -> None:
    terms = _run(
        _cluster(("status", "number", None)), {"node": ("data", _error_cluster())}
    )
    assert not terms["control"].is_error_cluster


def test_a_lone_nul_label_is_blank() -> None:
    terms = _run(
        _cluster(("status", "code", "\x00")), {"node": ("data", _error_cluster())}
    )
    assert terms["control"].is_error_cluster


def test_a_cluster_of_other_field_types_stays_a_plain_cluster() -> None:
    other = _cluster()
    other.fields[1].type = STRING
    terms = _run(other, {"node": ("data", _error_cluster())})
    assert not terms["control"].is_error_cluster


def test_an_exact_error_cluster_is_unchanged() -> None:
    control = _error_cluster()
    terms = _run(control, {"node": ("data", _error_cluster())})
    assert terms["control"].lv_type is control
    assert terms["control"].is_error_cluster
