"""A User Event's name (the label of its data type) is part of its type: it
shows in describe, in lvnet, in the lvnet type comparison, and survives the
strict lvnet read-back."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph import load_vi_by_path
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import (
    _lvnet_ambiguous_named_types,
    _lvnet_type_inline,
    render_lvnet,
)
from lvkit.load_mode import LoadMode
from lvkit.models import LVType, LVTypeKind
from tests.test_lvnet_identity import _assert_graph_identity

_SAMPLES = Path(".lvkit/cache/samples")
_AF_ROOT = _SAMPLES / "configurable-af-example" / "source"
_PRE_LAUNCH_INIT = _AF_ROOT / "ConfigurableActor" / "Pre Launch Init.vi"
_JKI_ROOT = _SAMPLES / "JKI-VI-Tester" / "source"
_JKI_NEW = _JKI_ROOT / "Tests" / "Static TestSuite" / "New.vi"

_CASES = [
    pytest.param(_PRE_LAUNCH_INIT, "stopped: TF", id="plain_data_type"),
    pytest.param(
        _JKI_NEW,
        "suiteStatusChanged: suiteStatusChanged--Cluster",
        id="typedef",
    ),
]


def _user_event(name: str | None, element: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UserEvent",
        element_type=element,
        user_event_name=name,
    )


def _dbl() -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")


@pytest.mark.parametrize(
    "name",
    ["stopped", "Drive Status", 'odd : "name", {x}'],
)
def test_lvnet_inline_name_reads_back_exactly(name: str) -> None:
    inline = _lvnet_type_inline(_user_event(name, _dbl()))
    from lvkit.graph.lvnet_parse import _parsed_type_ref_shape
    from lvkit.graph.lvnet_reconstruct import _reconstruct_type_ref

    shape = _parsed_type_ref_shape(inline, {})
    assert shape[0] == "refnum" and shape[3] == name
    rebuilt = _reconstruct_type_ref(inline, {}, {})
    assert rebuilt.user_event_name == name


def test_unnamed_user_event_renders_without_a_name() -> None:
    assert _lvnet_type_inline(_user_event(None, _dbl())) == "UserEvent refnum{DBL}"


@pytest.mark.needs_samples
@pytest.mark.parametrize("vi_path,expected", _CASES)
def test_describe_shows_the_event_name(vi_path: Path, expected: str) -> None:
    if not vi_path.exists():
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvkit.cli",
            "describe",
            "-v",
            str(vi_path),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "BROWSER": "true"},
        check=True,
    ).stdout
    assert f"UserEvent refnum{{{expected}" in out


@pytest.mark.needs_samples
@pytest.mark.parametrize("vi_path,expected", _CASES)
def test_strict_lvnet_round_trip(vi_path: Path, expected: str) -> None:
    if not vi_path.exists():
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    graph, vi_name = load_vi_by_path(vi_path, LoadMode.MINIMAL, layout=False)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name=vi_path.name, verbose=True)
    assert "UserEvent refnum{" in text
    parsed = parse_lvnet(text)
    ambiguous = _lvnet_ambiguous_named_types(module)
    assert netlist_signature(module, ambiguous) == netlist_signature(parsed, ambiguous)
    reconstructed = reconstruct_module(parsed)
    _assert_graph_identity(module, reconstructed, vi_path.name)
    assert render_lvnet(reconstructed, display_name=vi_path.name, verbose=True) == text
