"""String To IP (1711) and IP To String (1710).

The block diagram below is String To IP wired into IP To String's net address,
both other inputs left unwired: the current machine's address, as its host name.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.primitive_resolver import get_resolver
from lvkit.runtime import tcp
from tests.helpers import build_graph

_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="0" />
    <nodeList elements="2">
      <SL__arrayElement class="prim" uid="10">
        <primResID>1711</primResID>
        <termList elements="2">
          <SL__arrayElement class="term" uid="11">
            <objFlags>1</objFlags>
            <dco class="parm" uid="12"><objFlags>1</objFlags></dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="13">
            <dco class="parm" uid="14"><parmIndex>1</parmIndex></dco>
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="prim" uid="20">
        <primResID>1710</primResID>
        <termList elements="3">
          <SL__arrayElement class="term" uid="21">
            <objFlags>1</objFlags>
            <dco class="parm" uid="22"><objFlags>1</objFlags></dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="23">
            <dco class="parm" uid="24"><parmIndex>1</parmIndex></dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="25">
            <dco class="parm" uid="26"><parmIndex>2</parmIndex></dco>
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
    </nodeList>
    <signalList elements="1">
      <SL__arrayElement class="signal" uid="30">
        <termList elements="2">
          <SL__arrayElement uid="11" />
          <SL__arrayElement uid="25" />
        </termList>
      </SL__arrayElement>
    </signalList>
  </root>
</SL__rootObject>
"""


def _load(tmp_path: Path) -> tuple[InMemoryVIGraph, str]:
    bd_xml = tmp_path / "IP_BDHb.xml"
    bd_xml.write_text(_BD_XML)
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    return graph, graph.list_vis()[0]


def test_resolver_terminals() -> None:
    resolver = get_resolver()
    to_ip = resolver.resolve(prim_id=1711)
    to_string = resolver.resolve(prim_id=1710)
    assert to_ip is not None and to_string is not None
    assert [(t.index, t.direction, t.name, t.type) for t in to_ip.terminals] == [
        (0, "out", "net_address", "NumUInt32"),
        (1, "in", "name", "String"),
    ]
    assert [(t.index, t.direction, t.name, t.type) for t in to_string.terminals] == [
        (0, "out", "name", "String"),
        (1, "in", "dot_notation", "Boolean"),
        (2, "in", "net_address", "NumUInt32"),
    ]


def test_describe_names_both_functions(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    text = describe_vi(graph, vi_name)
    assert text.endswith(
        "## Operations\nString To IP [prim 1711]\nIP To String [prim 1710]"
    )


def test_lvnet_wires_the_address_into_ip_to_string(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    text = render_lvnet(build_netlist_from_graph(graph, vi_name))
    assert text.endswith(
        "    function String_To_IP_10 : String To IP\n"
        "      in   name        : ? default\n"
        "      out  net_address : ?\n"
        "    function IP_To_String_20 : IP To String\n"
        "      in   dot_notation : ? default\n"
        "      in   net_address  : ? = String_To_IP_10::net_address\n"
        "      out  name         : ?"
    )


def _generated(dot_notation_wired: bool):
    """A VI: control ``host`` -> String To IP -> IP To String -> indicator
    ``address``; control ``dotted`` wired to dot notation? when asked."""
    text = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    flag = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    u32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32")
    host = FPTerminal(
        id="fp:host",
        index=0,
        direction="input",
        name="host",
        is_public=True,
        is_indicator=False,
        lv_type=text,
    )
    dotted = FPTerminal(
        id="fp:dotted",
        index=1,
        direction="input",
        name="dotted",
        is_public=True,
        is_indicator=False,
        lv_type=flag,
    )
    address = FPTerminal(
        id="fp:address",
        index=0,
        direction="output",
        name="address",
        is_public=True,
        is_indicator=True,
        lv_type=text,
    )
    to_ip = PrimitiveNode(
        id="to_ip",
        vi_path="IP Test.vi",
        name="String To IP",
        prim_id=1711,
        terminals=[
            Terminal(id="to_ip:out", index=0, direction="output", lv_type=u32),
            Terminal(id="to_ip:name", index=1, direction="input", lv_type=text),
        ],
    )
    to_string = PrimitiveNode(
        id="to_string",
        vi_path="IP Test.vi",
        name="IP To String",
        prim_id=1710,
        terminals=[
            Terminal(id="to_string:out", index=0, direction="output", lv_type=text),
            Terminal(id="to_string:dot", index=1, direction="input", lv_type=flag),
            Terminal(id="to_string:addr", index=2, direction="input", lv_type=u32),
        ],
    )
    wires = [
        Wire.from_terminals(from_terminal_id=host.id, to_terminal_id="to_ip:name"),
        Wire.from_terminals(
            from_terminal_id="to_ip:out", to_terminal_id="to_string:addr"
        ),
        Wire.from_terminals(
            from_terminal_id="to_string:out", to_terminal_id=address.id
        ),
    ]
    inputs: list[Terminal] = [host]
    if dot_notation_wired:
        inputs.append(dotted)
        wires.append(
            Wire.from_terminals(
                from_terminal_id=dotted.id, to_terminal_id="to_string:dot"
            )
        )
    context = VIContext(
        name="IP Test.vi", inputs=inputs, outputs=[address], data_flow=wires
    )
    graph = build_graph(context, context.name, [to_ip, to_string])
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec(compile(source, context.name, "exec"), namespace)  # noqa: S102
    return source, namespace["ip_test"]


def test_generated_python_unwired_dot_notation_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, ip_test = _generated(dot_notation_wired=False)
    assert "net_address = _tcp.string_to_ip(host)\n" in source
    assert "name = _tcp.ip_to_string(net_address, False)\n" in source
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "10.0.0.7")
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("here.lan", [], [ip]))
    assert ip_test("here").address == "here.lan"


def test_generated_python_wired_dot_notation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, ip_test = _generated(dot_notation_wired=True)
    assert "net_address = _tcp.string_to_ip(host)\n" in source
    assert "name = _tcp.ip_to_string(net_address, dotted)\n" in source
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "10.0.0.7")
    assert ip_test("here", True).address == "10.0.0.7"


def test_string_to_ip_dot_notation() -> None:
    assert tcp.string_to_ip("128.0.0.25") == 0x80000019


def test_string_to_ip_empty_name_is_the_current_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looked_up: list[str] = []
    monkeypatch.setattr(socket, "gethostname", lambda: "here")

    def resolve(host: str) -> str:
        looked_up.append(host)
        return "10.0.0.7"

    monkeypatch.setattr(socket, "gethostbyname", resolve)
    assert tcp.string_to_ip("") == 0x0A000007
    assert looked_up == ["here"]


def test_string_to_ip_unresolved_name_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(host: str) -> str:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "gethostbyname", fail)
    assert tcp.string_to_ip("nowhere") == 0


def test_ip_to_string_dot_notation() -> None:
    assert tcp.ip_to_string(0x80000019, True) == "128.0.0.25"


def test_ip_to_string_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("here.lan", [], [ip]))
    assert tcp.ip_to_string(0x0A000007) == "here.lan"


def test_ip_to_string_without_a_host_name_is_dot_notation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(ip: str) -> tuple[str, list[str], list[str]]:
        raise socket.herror("unknown host")

    monkeypatch.setattr(socket, "gethostbyaddr", fail)
    assert tcp.ip_to_string(0x0A000007) == "10.0.0.7"
