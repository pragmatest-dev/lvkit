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
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import LvnetReconstructError, reconstruct_module
from lvkit.graph.models import VIContext
from lvkit.graph.netlist import (
    NetlistModule,
    NetRef,
    build_netlist_from_graph,
    render_lvnet,
)
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.netlist_models import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    NetlistBoundaryInput,
    WiringRequirement,
)
from lvkit.load_mode import LoadMode
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.parser.models import ParsedWiringRule
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
    return load_vi_by_path(vi, mode, layout=False)


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
    """`error out` sits on connector-pane slot 0; its wiring rule (Recommended)
    is read like every other slot's."""
    vi = _ISSUES / "133" / "Run_after.vi"
    graph, key = _load(vi)
    by_name = {t.name: t for t in graph.get_outputs(key)}
    assert by_name["error out"].index == 0
    assert by_name["error out"].wiring_rule == ParsedWiringRule.RECOMMENDED
    module = build_netlist_from_graph(graph, key)
    text = render_lvnet(module, display_name=vi.name, verbose=True)
    assert "recommended @0" in text


@pytest.mark.xfail(
    strict=True,
    reason="Open/Create/Replace File's advisory locking default is not in the "
    "catalog yet, so the VI generates a gap there",
)
def test_unwired_pane_codegen_takes_its_controls_as_local_variables() -> None:
    graph, key = _load(_ZORDER_VI, LoadMode.FULL)
    code = build_module(graph.get_vi_context(key), key, graph=graph)
    assert "def zorder_not_respected() -> None:" in code
    assert "numeric = 0" in code
    assert "Front panel, not on the connector pane:" in code
    namespace: dict[str, object] = {}
    exec(compile(code, "<zorder>", "exec"), namespace)  # noqa: S102
    function = namespace["zorder_not_respected"]
    assert callable(function)
    # No control is on the connector pane: no parameter, no result.
    assert function() is None


@pytest.mark.needs_samples
def test_partly_wired_vi_names_off_pane_net_by_its_label() -> None:
    vi = (
        _SAMPLES
        / "JKI-EasyXML/Source/Tests/EasyXMLTest"
        / "testReadArrayFromFileAutoWrapInCluster.vi"
    )
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(vi, LoadMode.MINIMAL, layout=False)
    module = build_netlist_from_graph(graph, key)
    text = render_lvnet(module, display_name=vi.name)
    assert "::/" not in text
    assert "= Data to Write" in text


@pytest.mark.needs_samples
def test_partly_wired_vi_drives_its_off_pane_indicator() -> None:
    vi = _SAMPLES / "DCAF-DAQModule/source/testing/Add Remove Test.vi"
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(vi, LoadMode.MINIMAL, layout=False)
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
    graph, key = load_vi_by_path(vi, LoadMode.FULL, layout=False)
    code = build_module(graph.get_vi_context(key), key, graph=graph)
    assert "task_" not in code.split("):", 1)[0]
    assert "task_ = ''" in code
    namespace: dict[str, object] = {}
    exec(compile(code, "<failUnlessError>", "exec"), namespace)  # noqa: S102
    function = namespace["failunlesserror"]
    assert callable(function)

    class Recorder:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def fail(self, message_: str) -> None:
            self.messages.append(message_)

    default_case, named_case = Recorder(), Recorder()
    function(testcase_in=default_case)
    # The control is not on the connector pane, so a caller cannot set it: it
    # holds its saved default, the empty string.
    assert default_case.messages == ["no error when expected"]
    with pytest.raises(TypeError):
        function(testcase_in=named_case, task_="parsing")


def _fp(tid: str, name: str, direction: str, index: int | None, default=None):
    return FPTerminal(
        id=tid,
        index=index,
        direction=direction,
        name=name,
        lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
        is_indicator=direction == "output",
        is_public=index is not None,
        default_value=default,
    )


def test_colliding_control_names_get_distinct_parameters_and_fields() -> None:
    """A connector-pane control and off-pane controls whose labels normalize to
    one Python name each get their own name (the parameter, then the local
    variables); same for the connector-pane result fields."""
    context = VIContext(
        name="Collide.vi",
        inputs=[_fp("c1", "x+y", "input", 0, "1")],
        outputs=[_fp("o1", "out", "output", 1)],
        off_pane_inputs=[
            _fp("c2", "xy", "input", None, "2"),
            _fp("c3", "X Y", "input", None, "3"),
        ],
        off_pane_outputs=[_fp("o2", "Out", "output", None)],
    )
    code = build_module(context, "Collide.vi")
    assert "def collide(*, xy: int=1)" in code
    assert "    xy_2: int = 2\n    x_y: int = 3\n" in code
    assert "    out: int\n" in code
    namespace: dict[str, object] = {}
    exec(compile(code, "<collide>", "exec"), namespace)  # noqa: S102
    function = namespace["collide"]
    assert callable(function)
    assert function() is not None
    assert function(xy=7) is not None
    # The off-pane indicator is not a result field.
    assert list(namespace["CollideResult"]._fields) == ["out"]


def _duplicate_indicator_module(wired: tuple[bool, bool]) -> NetlistModule:
    """Two off-pane indicators sharing the label `Result`, optionally driven."""
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    source = NetRef(node=None, terminal="seed", occurrence=None, bare="seed")
    return NetlistModule(
        vi_name="Dup.vi",
        inputs=[],
        outputs=[],
        off_pane_outputs=[
            BoundaryOutput(
                name="Result",
                type_descriptor="String",
                source=source if is_wired else None,
                lv_type=string,
            )
            for is_wired in wired
        ],
        connector_pane=ConnectorPane(
            pattern_id=None,
            terminals=[
                ConnectorPaneTerminal(
                    name="Result",
                    type="String",
                    direction="output",
                    index=None,
                    wiring_requirement=WiringRequirement.UNKNOWN,
                    default=None,
                    lv_type=string,
                )
                for _ in wired
            ],
        ),
    )


@pytest.mark.parametrize("wired", [(True, False), (False, True), (True, True)])
def test_duplicate_off_pane_labels_keep_their_drive_lines_distinct(
    wired: tuple[bool, bool],
) -> None:
    module = _duplicate_indicator_module(wired)
    text = render_lvnet(module, display_name="Dup.vi", verbose=True)
    assert text.count("Result = ") == 2
    rebuilt = reconstruct_module(parse_lvnet(text))
    assert render_lvnet(rebuilt, display_name="Dup.vi", verbose=True) == text
    assert [o.source is not None for o in rebuilt.off_pane_outputs] == list(wired)


def test_reconstruct_rejects_a_drive_line_that_cannot_be_matched_to_one_row() -> None:
    text = render_lvnet(
        _duplicate_indicator_module((True, True)),
        display_name="Dup.vi",
        verbose=True,
    )
    lines = text.splitlines()
    lines.remove(next(line for line in lines if line.strip().startswith("Result =")))
    with pytest.raises(LvnetReconstructError, match="cannot be matched"):
        reconstruct_module(parse_lvnet("\n".join(lines)))


@pytest.mark.needs_samples
def test_corpus_vi_with_shared_front_panel_labels_round_trips() -> None:
    vi = (
        _SAMPLES
        / "JKI-EasyXML/Source/Examples/Scalar and Compound Data with Attributes.vi"
    )
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(vi, LoadMode.MINIMAL, layout=False)
    module = build_netlist_from_graph(graph, key)
    labels = [o.name for o in module.off_pane_outputs]
    assert labels.count("XML String") == 6
    text = render_lvnet(module, display_name=vi.name, verbose=True)
    rebuilt = reconstruct_module(parse_lvnet(text))
    assert render_lvnet(rebuilt, display_name=vi.name, verbose=True) == text
    _assert_graph_identity(module, rebuilt, vi.name)


_AWKWARD_LABELS = [
    pytest.param("Current Test\nSucceeded", id="newline"),
    pytest.param("x = y?", id="equals"),
    pytest.param("Image : Type", id="colon"),
    pytest.param("tab\there", id="tab"),
]


def _label_module(label: str, *, on_pane: bool) -> NetlistModule:
    """A control and an indicator both labelled ``label`` (distinct directions)
    on or off the pane, the indicator driven by the control's net."""
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    index = (0, 1) if on_pane else (None, None)

    def pane(direction: str, slot: int | None) -> ConnectorPaneTerminal:
        return ConnectorPaneTerminal(
            name=label,
            type="String",
            direction=direction,
            index=slot,
            wiring_requirement=WiringRequirement.UNKNOWN,
            default=None,
            lv_type=string,
        )

    source = NetRef(
        node=None, terminal=label, occurrence=None, bare=label, boundary=True
    )
    indicator = BoundaryOutput(
        name=label, type_descriptor="String", source=source, lv_type=string
    )
    return NetlistModule(
        vi_name="Names.vi",
        inputs=(
            [NetlistBoundaryInput(name=label, type_descriptor="String", lv_type=string)]
            if on_pane
            else []
        ),
        outputs=[indicator] if on_pane else [],
        off_pane_outputs=[] if on_pane else [indicator],
        connector_pane=ConnectorPane(
            pattern_id=None,
            terminals=[pane("input", index[0]), pane("output", index[1])],
        ),
    )


@pytest.mark.parametrize("on_pane", [True, False])
@pytest.mark.parametrize("label", _AWKWARD_LABELS)
def test_front_panel_labels_with_delimiters_round_trip(
    label: str, on_pane: bool
) -> None:
    module = _label_module(label, on_pane=on_pane)
    text = render_lvnet(module, display_name="Names.vi", verbose=True)
    assert text.count("\n") == len(text.splitlines()) - 1
    parsed = parse_lvnet(text)
    assert [t.name for t in parsed.boundary] == [label, label]
    rebuilt = reconstruct_module(parsed)
    assert render_lvnet(rebuilt, display_name="Names.vi", verbose=True) == text
    _assert_graph_identity(module, rebuilt, "Names.vi")
    drive = (rebuilt.outputs + rebuilt.off_pane_outputs)[0]
    assert drive.name == label
    assert drive.source is not None
    assert drive.source.bare == label


@pytest.mark.needs_samples
@pytest.mark.parametrize(
    "vi,root",
    [
        pytest.param(
            _SAMPLES / "measurement-plugin-labview/Source/Tests/run_tests.vi",
            _SAMPLES / "measurement-plugin-labview",
            id="run_tests",
        ),
        pytest.param(
            _SAMPLES
            / "JKI-EasyXML/Source/Support/Low Level/Date Time"
            / "TEST - Parse XML dateTime.vi",
            _SAMPLES / "JKI-EasyXML",
            id="TEST_Parse_XML_dateTime",
        ),
    ],
)
def test_corpus_vi_with_delimiter_labels_round_trips(vi: Path, root: Path) -> None:
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(vi, LoadMode.MINIMAL, layout=False)
    module = build_netlist_from_graph(graph, key)
    text = render_lvnet(module, display_name=vi.name, verbose=True)
    parsed = parse_lvnet(text)
    assert netlist_signature(module) == netlist_signature(parsed)
    rebuilt = reconstruct_module(parsed)
    assert render_lvnet(rebuilt, display_name=vi.name, verbose=True) == text
    _assert_graph_identity(module, rebuilt, vi.name)
