"""An XNode (e.g. Error Ring) is one node on the block diagram; the
implementation diagram it stores internally is not part of the VI's diagram, so
its nodes are not collected as the enclosing structure's own."""

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
from lvkit.parser.vi import _iter_diagram_elements
from tests.test_lvnet_identity import _assert_graph_identity

_DQMH = Path(".lvkit/cache/samples/configurable-dqmh-example")
_MAIN_VI = (
    _DQMH
    / "source"
    / "LabVIEW Data"
    / "DQMH Module Templates"
    / "Source"
    / "Delacor"
    / "ConfigurableQMH"
    / "Main.vi"
)


def test_iteration_yields_the_xnode_but_not_its_interior() -> None:
    root = ET.fromstring(
        """<r>
          <a class="before" uid="1"/>
          <x class="xNode" uid="2">
            <diagramList><d class="diag" uid="3">
              <nodeList><p class="printf" uid="4"/></nodeList></d></diagramList>
          </x>
          <a class="after" uid="5"/>
        </r>"""
    )
    uids = [e.get("uid") for e in _iter_diagram_elements(root) if e.get("uid")]
    assert uids == ["1", "2", "5"]


@pytest.mark.needs_samples
def test_error_ring_interior_does_not_break_the_strict_round_trip() -> None:
    if not _MAIN_VI.exists():
        pytest.skip(f"sample corpus VI not present: {_MAIN_VI}")
    graph, vi_name = load_vi_by_path(
        _MAIN_VI, LoadMode.MINIMAL, layout=False
    )
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name=_MAIN_VI.name, verbose=True)
    parsed = parse_lvnet(text)
    ambiguous = _lvnet_ambiguous_named_types(module)
    assert netlist_signature(module, ambiguous) == netlist_signature(
        parsed, ambiguous
    )
    reconstructed = reconstruct_module(parsed)
    _assert_graph_identity(module, reconstructed, _MAIN_VI.name)
    assert (
        render_lvnet(reconstructed, display_name=_MAIN_VI.name, verbose=True) == text
    )
