"""Front-panel controls and indicators that are not wired to the connector pane
(issue #145): they are off-pane in the graph, in describe, in lvnet and JSON,
and in generated Python -- never fabricated connector-pane slots.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import load_vi_by_path
from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.load_mode import LoadMode
from lvkit.models import FPTerminal
from tests.test_lvnet_identity import _assert_graph_identity

_ISSUES = Path(__file__).resolve().parent / "corpus" / "issues"
_SAMPLES = Path(".lvkit/cache/samples")

# The six issue reproductions whose connector pane is parsed but has no
# control or indicator wired to it.
_UNWIRED_PANE_VIS = [
    pytest.param(_ISSUES / "33" / "coercion-dot-consistency.vi", id="33"),
    pytest.param(_ISSUES / "35" / "objects-hidden-by-structures.vi", id="35"),
    pytest.param(_ISSUES / "36" / "bundle-unbundle-names.vi", id="36"),
    pytest.param(_ISSUES / "37" / "structure-tunnel-wire-bends.vi", id="37"),
    pytest.param(_ISSUES / "38" / "auto-concatenating-tunnel.vi", id="38"),
    pytest.param(_ISSUES / "39" / "zorder-not-respected.vi", id="39"),
]

_ZORDER_VI = _ISSUES / "39" / "zorder-not-respected.vi"


def _load(vi: Path, mode: LoadMode = LoadMode.MINIMAL) -> tuple[InMemoryVIGraph, str]:
    return load_vi_by_path(vi, mode, search_paths=[vi.parent], layout=False)


@pytest.mark.parametrize("vi", _UNWIRED_PANE_VIS)
def test_unwired_pane_puts_no_terminal_on_the_connector_pane(vi: Path) -> None:
    graph, key = _load(vi)
    ctx = graph.get_vi_context(key)
    assert ctx.inputs == []
    assert ctx.outputs == []
    front_panel = [
        t
        for t in graph.get_inputs(key, public_only=False)
        + graph.get_outputs(key, public_only=False)
    ]
    assert front_panel, "the VI has controls or indicators on its front panel"
    for t in front_panel:
        assert isinstance(t, FPTerminal)
        assert not t.is_public
        assert t.index is None


@pytest.mark.parametrize("vi", _UNWIRED_PANE_VIS)
def test_unwired_pane_lvnet_has_no_pane_slot_rows(vi: Path) -> None:
    graph, key = _load(vi)
    module = build_netlist_from_graph(graph, key)
    assert module.inputs == []
    assert module.outputs == []
    assert all(t.index is None for t in module.connector_pane.terminals)
    for verbose in (False, True):
        text = render_lvnet(module, display_name=vi.name, verbose=verbose)
        assert "@0" not in text
        assert " @" not in text.split("block-diagram :")[0]
    payload = netlist_to_dict(module)
    assert all(t["index"] is None for t in payload["connector_pane"]["terminals"])


@pytest.mark.parametrize("vi", _UNWIRED_PANE_VIS)
def test_unwired_pane_describe_lists_the_front_panel(vi: Path) -> None:
    graph, key = _load(vi)
    text = describe_vi(graph, key)
    assert "## Inputs\n  (none)" in text
    assert "## Outputs\n  (none)" in text
    assert "## Front Panel (not on the connector pane)" in text


def test_describe_front_panel_section_uses_the_pane_line_format() -> None:
    graph, key = _load(_ZORDER_VI)
    text = describe_vi(graph, key)
    section = text.split("## Front Panel (not on the connector pane)\n")[1]
    section = section.split("\n\n")[0]
    assert section.splitlines() == [
        "  Controls",
        "    Numeric: I32",
        "    String in: String",
        "    y: I32",
        "  Indicators",
        "    String out: String",
        "    x+y: I32",
    ]


def test_unwired_pane_wired_indicators_get_drive_lines() -> None:
    graph, key = _load(_ZORDER_VI)
    module = build_netlist_from_graph(graph, key)
    text = render_lvnet(module, display_name=_ZORDER_VI.name)
    block = text.split("block-diagram :")[1]
    assert "String out = String in" in block
    assert "x+y = Add_" in block
    outputs = netlist_to_dict(module)["outputs"]
    assert {o["name"] for o in outputs} == {"String out", "x+y"}
    assert all(o["source"] is not None for o in outputs)


@pytest.mark.parametrize("vi", _UNWIRED_PANE_VIS)
def test_unwired_pane_round_trips_by_bytes_and_identity(vi: Path) -> None:
    graph, key = _load(vi)
    module = build_netlist_from_graph(graph, key)
    text = render_lvnet(module, display_name=vi.name, verbose=True)
    rebuilt = reconstruct_module(parse_lvnet(text))
    assert render_lvnet(rebuilt, display_name=vi.name, verbose=True) == text
    _assert_graph_identity(module, rebuilt, vi.name)


def test_slot_zero_keeps_its_wiring_rule() -> None:
    """`error out` sits on connector-pane slot 0; its wiring rule (1) is read
    like every other slot's."""
    vi = _ISSUES / "133" / "Run_after.vi"
    graph, key = _load(vi)
    by_name = {t.name: t for t in graph.get_outputs(key)}
    assert by_name["error out"].index == 0
    assert by_name["error out"].wiring_rule == 1
    module = build_netlist_from_graph(graph, key)
    text = render_lvnet(module, display_name=vi.name, verbose=True)
    assert "recommended @0" in text


def test_unwired_pane_codegen_takes_its_controls_as_keyword_only_parameters() -> None:
    graph, key = _load(_ZORDER_VI, LoadMode.FULL)
    code = build_module(graph.get_vi_context(key), key, graph=graph)
    assert "def zorder_not_respected(*, numeric: int=0, string_in: str=''" in code
    namespace: dict[str, object] = {}
    exec(compile(code, "<zorder>", "exec"), namespace)  # noqa: S102
    function = namespace["zorder_not_respected"]
    assert callable(function)
    result = function(numeric=2, string_in="hello", y=3)
    assert result.string_out == "hello"
    assert result.xy == 5
    # An off-pane control is not a connector-pane terminal: no positional slot.
    with pytest.raises(TypeError):
        function(2, "hello", 3)
    # Left unset, a control holds its saved default.
    assert function().xy == 0


@pytest.mark.needs_samples
def test_partly_wired_vi_names_off_pane_net_by_its_label() -> None:
    vi = (
        _SAMPLES
        / "JKI-EasyXML/Source/Tests/EasyXMLTest"
        / "testReadArrayFromFileAutoWrapInCluster.vi"
    )
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(
        vi, LoadMode.MINIMAL, search_paths=[_SAMPLES / "JKI-EasyXML"], layout=False
    )
    module = build_netlist_from_graph(graph, key)
    text = render_lvnet(module, display_name=vi.name)
    assert "::/" not in text
    assert "= Data to Write" in text


@pytest.mark.needs_samples
def test_partly_wired_vi_drives_its_off_pane_indicator() -> None:
    vi = _SAMPLES / "DCAF-DAQModule/source/testing/Add Remove Test.vi"
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(
        vi, LoadMode.MINIMAL, search_paths=[vi.parent.parent], layout=False
    )
    module = build_netlist_from_graph(graph, key)
    assert [o.name for o in module.off_pane_outputs] == ["Deleted Line"]
    text = render_lvnet(module, display_name=vi.name, verbose=True)
    assert "Deleted Line = Get_Line_Name_" in text
    payload = json.loads(json.dumps(netlist_to_dict(module)))
    assert "Deleted Line" in [o["name"] for o in payload["outputs"]]
    rebuilt = reconstruct_module(parse_lvnet(text))
    assert render_lvnet(rebuilt, display_name=vi.name, verbose=True) == text
    _assert_graph_identity(module, rebuilt, vi.name)


@pytest.mark.needs_samples
def test_partly_wired_vi_reads_its_off_pane_control_instead_of_none() -> None:
    root = _SAMPLES / "JKI-VI-Tester/source"
    vi = root / "Classes/TestCase/failUnlessError.vi"
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(vi, LoadMode.FULL, search_paths=[root], layout=False)
    code = build_module(graph.get_vi_context(key), key, graph=graph)
    assert "*, task_: str=''" in code
    namespace: dict[str, object] = {}
    exec(compile(code, "<failUnlessError>", "exec"), namespace)  # noqa: S102
    function = namespace["failunlesserror"]
    assert callable(function)

    class Recorder:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def fail(self, message: str) -> None:
            self.messages.append(message)

    default_case, named_case = Recorder(), Recorder()
    function(testcase_in=default_case)
    function(testcase_in=named_case, task_="parsing")
    # The control's saved default is the empty string; a caller may set it.
    assert default_case.messages == ["no error when expected"]
    assert named_case.messages == ["no error during parsing"]
