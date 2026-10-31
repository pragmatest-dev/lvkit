"""A VI saved with the structure's tunnels listed in its own ``<tunnelList>``
(border terminals holding a bare ``<dco uid=.../>``) reads exactly like one that
defines each tunnel on its border terminal."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.graph import load_vi_by_path
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import _lvnet_ambiguous_named_types, render_lvnet
from lvkit.load_mode import LoadMode
from lvkit.parser.nodes.loop import extract_loops
from lvkit.parser.vi import _inline_dco_definitions
from tests.test_lvnet_identity import _assert_graph_identity

_JKI = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_NEW_VI = _JKI / "Classes" / "TestSuite" / "New.vi"


def test_bare_back_reference_takes_the_listed_tunnel_definition() -> None:
    root = ET.fromstring(
        """<r><s class="select" uid="1">
          <tunnelList>
            <SL__arrayElement class="selTun" uid="7" index="0">
              <typeDesc>TypeID(20)</typeDesc>
              <termList><SL__arrayElement uid="8"/><SL__arrayElement uid="9"/>
              </termList>
            </SL__arrayElement>
          </tunnelList>
          <termList>
            <SL__arrayElement class="term" uid="9"><dco uid="7"/></SL__arrayElement>
            <SL__arrayElement class="term" uid="10">
              <dco class="selTun" uid="11"><typeDesc>TypeID(3)</typeDesc></dco>
            </SL__arrayElement>
          </termList></s></r>"""
    )
    _inline_dco_definitions(root)
    terms = root.findall("s/termList/SL__arrayElement")
    listed = terms[0].find("dco")
    assert listed is not None
    assert listed.get("class") == "selTun"
    assert listed.findtext("typeDesc") == "TypeID(20)"
    assert [e.get("uid") for e in listed.findall("termList/SL__arrayElement")] == [
        "8",
        "9",
    ]
    inline = terms[1].find("dco")
    assert inline is not None and inline.findtext("typeDesc") == "TypeID(3)"


@pytest.mark.needs_samples
def test_listed_tunnels_round_trip_strictly() -> None:
    if not _NEW_VI.exists():
        pytest.skip(f"sample corpus VI not present: {_NEW_VI}")
    graph, vi_name = load_vi_by_path(
        _NEW_VI, LoadMode.MINIMAL, layout=False
    )
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name=_NEW_VI.name, verbose=True)
    parsed = parse_lvnet(text)
    ambiguous = _lvnet_ambiguous_named_types(module)
    assert netlist_signature(module, ambiguous) == netlist_signature(parsed, ambiguous)
    reconstructed = reconstruct_module(parsed)
    _assert_graph_identity(module, reconstructed, _NEW_VI.name)
    assert render_lvnet(reconstructed, display_name=_NEW_VI.name, verbose=True) == text


_LOOP_WITH_RIGHT_REGISTER = """<r><n class="{kind}" uid="1">
          <srDCOList>
            <SL__arrayElement class="rSR" uid="30" index="0">
              <lsrDCOList><SL__arrayElement uid="20"/></lsrDCOList>
              <typeDesc>TypeID(5)</typeDesc>
              <termList><SL__arrayElement uid="31"/><SL__arrayElement uid="32"/>
              </termList>
            </SL__arrayElement>
            {extra}
          </srDCOList>
          <termList>
            <SL__arrayElement class="term" uid="22">
              <dco class="lSR" uid="20">
                <typeDesc>TypeID(5)</typeDesc>
                <termList><SL__arrayElement uid="21"/><SL__arrayElement uid="22"/>
                </termList>
                <rsrDCO uid="30"/>
              </dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="32"><dco uid="30"/></SL__arrayElement>
          </termList></n></r>"""


@pytest.mark.parametrize("kind", ["forLoop", "whileLoop"])
def test_a_loops_right_shift_register_listed_in_the_loop_is_a_tunnel(
    kind: str,
) -> None:
    root = ET.fromstring(_LOOP_WITH_RIGHT_REGISTER.format(kind=kind, extra=""))
    _inline_dco_definitions(root)
    (loop,) = extract_loops(root)
    pairs = {
        (t.tunnel_type, t.inner_terminal_uid, t.outer_terminal_uid)
        for t in loop.tunnels
    }
    assert ("rSR", "31", "32") in pairs
    assert ("lSR", "21", "22") in pairs


def test_a_bare_reference_does_not_replace_the_definition_with_its_uid() -> None:
    root = ET.fromstring(
        _LOOP_WITH_RIGHT_REGISTER.format(
            kind="forLoop", extra='<SL__arrayElement uid="30"/>'
        )
    )
    _inline_dco_definitions(root)
    (loop,) = extract_loops(root)
    assert ("rSR", "31", "32") in {
        (t.tunnel_type, t.inner_terminal_uid, t.outer_terminal_uid)
        for t in loop.tunnels
    }
