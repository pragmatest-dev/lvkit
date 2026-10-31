"""What a VI lists as its dependencies and how each is named and typed.

- Its class and typedef dependencies are the files its link-table records
  name, by recorded path; the record of its own class or library is not one.
- A typedef type is named by every name part it records, owners first.
- A call names the VI it runs: a polymorphic call its instance.
- A catalog-only dependency's ports take their types from what the call
  records, and show no type where it records none.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import type_file_records
from lvkit.graph.models import VINode
from lvkit.graph.op_walk import ComponentPort, _render_ports, _subvi_ports
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.parser.models import ParsedDependencyRef
from lvkit.parser.type_mapping import typedef_qualified_name
from lvkit.vilib_resolver import VIEntry, VITerminal, get_resolver


def _ref(record: str, path: list[str], name: str | None = None) -> ParsedDependencyRef:
    return ParsedDependencyRef(
        name=path[-1], path_tokens=path, qualified_name=name, record=record
    )


def test_every_class_and_typedef_record_is_a_dependency_by_path() -> None:
    refs = [
        _ref("VILB", ["", "Own.lvclass"], "Own.lvclass"),
        _ref("FPPI", ["", "Own.lvclass"], "Own.lvclass"),
        _ref("TDCC", ["", "", "cfg", "Line.ctl"], "Cfg.lvclass:Line.ctl"),
        _ref("TDCC", ["", "Map.ctl"]),
        _ref("FPPI", ["<vilib>", "x", "Chan.lvclass"]),
        _ref("DDPI", ["<vilib>", "x", "Chan.lvclass"], "Chan.lvclass"),
        _ref("IUVI", ["", "Do.vi"], "Do.vi"),
    ]

    records = type_file_records(refs)

    assert sorted(records) == [
        "Cfg.lvclass:Line.ctl",
        "Chan.lvclass",
        "Map.ctl",
        "Own.lvclass",
    ]
    assert records["Own.lvclass"].record == "FPPI"  # its type, not its VILB
    assert records["Chan.lvclass"].record == "DDPI"  # the record that names it
    assert records["Map.ctl"].path_tokens == ["", "Map.ctl"]


def test_a_typedef_type_is_named_by_every_part_it_records() -> None:
    td = ET.fromstring(
        '<TypeDesc Type="TypeDef">'
        '<TypeDesc Type="Cluster" Nested="True" />'
        '<Label Text="Lib.lvlib" /><Label Text="Cfg.lvclass" />'
        '<Label Text="Line.ctl" /></TypeDesc>'
    )
    bare = ET.fromstring('<TypeDesc Type="TypeDef"><Label Text="Map.ctl" /></TypeDesc>')
    none = ET.fromstring('<TypeDesc Type="TypeDef"><Label Text="x" /></TypeDesc>')

    assert typedef_qualified_name(td) == "Lib.lvlib:Cfg.lvclass:Line.ctl"
    assert typedef_qualified_name(bare) == "Map.ctl"
    assert typedef_qualified_name(none) is None


def test_a_polymorphic_call_is_named_by_the_instance_it_runs() -> None:
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node("/p/Get.vi", qname="Get.vi")  # noqa: SLF001
    graph._dep_graph.add_node(  # noqa: SLF001
        "/p/Get (DBL).vi", qname="Get (DBL).vi"
    )
    poly = VINode(
        id="c1",
        vi_path="caller.vi",
        name="Get (DBL).vi",
        node_type="polyIUse",
        callee_key="/p/Get.vi",
        variant_key="/p/Get (DBL).vi",
    )
    plain = VINode(id="c2", vi_path="caller.vi", name="Get.vi", callee_key="/p/Get.vi")

    assert graph.call_qualified_name(poly) == "Get (DBL).vi"
    assert graph.call_qualified_name(plain) == "Get.vi"


_ERROR = LVType(kind=LVTypeKind.CLUSTER, underlying_type="Cluster")
_VOID = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Void")
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _catalog_ports(
    monkeypatch: pytest.MonkeyPatch, call: VINode, terminals: list[VITerminal]
) -> tuple[list[ComponentPort], list[ComponentPort]] | None:
    graph = InMemoryVIGraph()
    monkeypatch.setattr(graph, "loaded_callee_key", lambda node: None)
    monkeypatch.setattr(
        get_resolver(),
        "resolve_recorded",
        lambda path: VIEntry(name="Clear Errors", terminals=terminals),
    )
    return _subvi_ports(graph, call)


def test_a_catalog_call_is_typed_by_what_the_call_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = VINode(
        id="c",
        vi_path="caller.vi",
        name="Clear Errors.vi",
        qualified_path="<vilib>/Utility/error.llb/Clear Errors.vi",
        terminals=[
            FPTerminal(id="t0", index=0, direction="output", lv_type=_ERROR),
            FPTerminal(id="t4", index=4, direction="input", lv_type=_ERROR),
            FPTerminal(id="t7", index=7, direction="input", lv_type=_VOID),
            FPTerminal(id="t9", index=9, direction="input", lv_type=_I32),
        ],
    )
    terminals = [
        VITerminal(name="code to clear", index=7, direction="input"),
        VITerminal(name="error in", index=4, direction="input", type="Cluster"),
        VITerminal(name="cleared?", index=3, direction="output", type="Boolean"),
        VITerminal(name="error out", index=0),  # no direction: the call's
        VITerminal(name="unplaced"),  # no position: not shown
    ]

    ports = _catalog_ports(monkeypatch, call, terminals)

    assert ports is not None
    assert _render_ports(*ports) == (
        f"(error in: {_ERROR.type_descriptor()}, code to clear, 9: I32) -> "
        f"(error out: {_ERROR.type_descriptor()}, cleared?)"
    )
    assert "None" not in _render_ports(*ports)
