"""``describe`` text names case frames, sequences and selectors the way lvnet
and JSON do (#171), and never prints a terminal id for a selector (#162)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import (
    _case_frame_header,
    _frame_count,
    _name_traced_source,
    _resolve_selector,
    describe_vi,
)
from lvkit.graph.diff import _struct_label
from lvkit.graph.models import (
    CaseStructureNode,
    Constant,
    LocalVariableNode,
    LoopNode,
    SequenceNode,
    VIContext,
    VINode,
    WireEnd,
)
from lvkit.graph.models import PrimitiveNode as GraphPrimitiveNode
from lvkit.graph.netlist_build import StructureBorder, TracedSource
from lvkit.load_mode import LoadMode
from lvkit.models import (
    CaseFrame,
    EnumValue,
    LVType,
    LVTypeKind,
    SelectorRange,
    Terminal,
    TunnelTerminal,
)
from tests.helpers import register_nodes

_VI = "test.vi"
_ENUM = LVType(
    kind=LVTypeKind.ENUM,
    values={"Alpha": EnumValue(value=0), "Beta": EnumValue(value=1)},
)
_SAMPLES = Path(".lvkit/cache/samples")


def _case(selector_type: LVType | None, frames: list[CaseFrame]) -> CaseStructureNode:
    return CaseStructureNode(
        id=f"{_VI}::10",
        vi_path=_VI,
        node_type="select",
        terminals=[
            Terminal(id=f"{_VI}::11", index=0, direction="input", lv_type=selector_type)
        ],
        selector_terminal=f"{_VI}::11",
        frames=frames,
    )


def _wire(graph: InMemoryVIGraph, src: WireEnd, dst: WireEnd) -> None:
    graph._graph.add_edge(src.node_id, dst.node_id, source=src, dest=dst)


def _selector_graph(source_node: LocalVariableNode | None, source_id: str):
    """A case structure whose selector is wired from ``source_id`` (a node
    registered in the graph when ``source_node`` is given, else a constant)."""
    graph = InMemoryVIGraph()
    case = _case(None, [])
    nodes = [case] if source_node is None else [case, source_node]
    register_nodes(graph, nodes, vi_name=_VI)
    src_term = f"{source_id}_t"
    _wire(
        graph,
        WireEnd(terminal_id=src_term, node_id=source_id),
        WireEnd(terminal_id=case.selector_terminal or "", node_id=case.id),
    )
    return graph, case


def test_frame_counts_use_singular_for_one() -> None:
    assert _frame_count(1) == "1 frame"
    assert _frame_count(4) == "4 frames"


def test_enum_frame_header_is_the_item_name() -> None:
    frame = CaseFrame(selector_value=1, selector_ranges=[SelectorRange(start=1, end=1)])
    assert _case_frame_header(_case(_ENUM, [frame]), frame) == '"Beta"'


def test_integer_frame_header_lists_every_value() -> None:
    frame = CaseFrame(
        selector_value=0,
        selector_ranges=[
            SelectorRange(start=0, end=0),
            SelectorRange(start=43, end=43),
        ],
    )
    int_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="I32")
    assert _case_frame_header(_case(int_type, [frame]), frame) == '"0, 43"'


def test_default_frame_header_is_marked_once() -> None:
    default = CaseFrame(selector_value="Default", is_default=True)
    assert _case_frame_header(_case(_ENUM, [default]), default) == '"Default"'


def test_stacked_sequence_is_called_stacked() -> None:
    stacked = SequenceNode(id=f"{_VI}::1", vi_path=_VI, is_flat=False)
    flat = SequenceNode(id=f"{_VI}::2", vi_path=_VI, is_flat=True)
    assert _struct_label(stacked) == "Stacked sequence"
    assert _struct_label(flat) == "Flat sequence"
    assert stacked.kind_word == "Stacked"


def test_selector_from_a_constant_is_named_by_its_value() -> None:
    graph, case = _selector_graph(None, f"{_VI}::14945")
    constant = Constant(id=f"{_VI}::14945", value=False)
    ctx = VIContext(name=_VI)
    named = _resolve_selector(
        graph, ctx, _VI, {constant.id: constant}, case.selector_terminal
    )
    assert named is not None
    assert "::" not in named
    assert named == '"False"'


def test_selector_from_a_local_variable_is_named_by_its_control() -> None:
    local = LocalVariableNode(
        id=f"{_VI}::7734",
        vi_path=_VI,
        name="Init",
        control_name="Init",
        terminals=[Terminal(id=f"{_VI}::7734_t", index=0, direction="output")],
    )
    graph, case = _selector_graph(local, local.id)
    ctx = VIContext(name=_VI)
    named = _resolve_selector(graph, ctx, _VI, {}, case.selector_terminal)
    assert named == "Init (local variable)"


def test_unwired_selector_names_nothing() -> None:
    graph = InMemoryVIGraph()
    case = _case(None, [])
    register_nodes(graph, [case], vi_name=_VI)
    assert (
        _resolve_selector(graph, VIContext(name=_VI), _VI, {}, case.selector_terminal)
        is None
    )


def _traced(owner, term, border=None) -> TracedSource:
    return TracedSource(
        WireEnd(terminal_id=term.id, node_id=owner.id),
        owner=owner,
        term=term,
        border=border,
    )


def test_shift_register_source_is_named_in_labview_words() -> None:
    term = TunnelTerminal(
        id=f"{_VI}::21",
        index=0,
        direction="output",
        tunnel_type="lSR",
        boundary="inner",
    )
    loop = LoopNode(
        id=f"{_VI}::20",
        vi_path=_VI,
        node_type="whileLoop",
        loop_type="whileLoop",
        terminals=[term],
    )
    traced = _traced(loop, term, StructureBorder.SHIFT_REGISTER)
    assert _name_traced_source(InMemoryVIGraph(), traced, {}) == (
        "While Loop shift register"
    )


def test_unnamed_output_of_a_one_output_node_is_the_node() -> None:
    term = Terminal(id=f"{_VI}::31", index=0, direction="output")
    node = GraphPrimitiveNode(
        id=f"{_VI}::30", vi_path=_VI, name="Compound Arithmetic", terminals=[term]
    )
    named = _name_traced_source(InMemoryVIGraph(), _traced(node, term), {})
    assert named == "Compound Arithmetic"


def test_named_output_is_node_dot_terminal() -> None:
    term = Terminal(
        id=f"{_VI}::41", index=10, direction="output", display_name="timed_out"
    )
    node = GraphPrimitiveNode(
        id=f"{_VI}::40", vi_path=_VI, name="Wait on Notification", terminals=[term]
    )
    named = _name_traced_source(InMemoryVIGraph(), _traced(node, term), {})
    assert named == "Wait on Notification.timed_out"


def test_subvi_output_keeps_the_call_name_and_terminal() -> None:
    call_id = "Caller.vi::50"
    term = Terminal(
        id=f"{call_id}::51",
        index=2,
        direction="output",
        display_name="Found?",
    )
    call = VINode(
        id=call_id,
        vi_path="Check If Line Exists.vi",
        name="Check If Line Exists.vi",
        terminals=[term],
    )
    named = _name_traced_source(InMemoryVIGraph(), _traced(call, term), {})
    assert named == "Check If Line Exists.vi.Found?"


# --- real VIs ------------------------------------------------------------------


def _describe(source_root: Path, relative: str, search: Path | None = None) -> str:
    vi = source_root / relative
    if not vi.exists():
        pytest.skip(f"{vi} not present")
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi), LoadMode.MINIMAL)
    return describe_vi(graph, graph.resolve_vi_name(vi.name))


@pytest.mark.needs_samples
def test_time_delayed_send_message_core_uses_lvnet_frame_labels() -> None:
    text = _describe(
        _SAMPLES / "actor-framework",
        "Core/ActorFramework/Time-Delayed Send Message/"
        "Time-Delayed Send Message Core.vi",
    )
    for header in (
        'Frame "No Error":',
        'Frame "Error":',
        'Frame "Send next copy now":',
        'Frame "Stop all further copies":',
        'Frame "Skip next scheduled copy":',
    ):
        assert header in text
    assert 'Frame "3":' not in text
    control_flow = text.split("## Control Flow")[1].split("## Operations")[0]
    assert "Select" not in control_flow
    assert "idx=" not in control_flow
    assert "While Loop shift register" in control_flow
    assert "Wait on Notification.timed_out" in control_flow
    assert "Wait on Notification.notification" in control_flow


@pytest.mark.needs_samples
def test_stop_core_stacked_sequence_is_stacked() -> None:
    text = _describe(
        _SAMPLES / "actor-framework", "Core/ActorFramework/Actor/Stop Core.vi"
    )
    assert "Stacked sequence (1 frame)" in text
    assert "\n    Stacked Sequence" in text
    assert "Flat" not in text


@pytest.mark.needs_samples
def test_case_selector_wired_from_a_constant_is_named_by_value() -> None:
    text = _describe(
        _SAMPLES / "JKI-VI-Tester" / "source",
        "User Interfaces/Graphical Test Runner/Graphical Test Runner Support/"
        "Read General Settings.vi",
        _SAMPLES / "JKI-VI-Tester",
    )
    assert 'gated on "False"' in text
    assert "selector:" not in text
    assert ".vi::" not in text


@pytest.mark.needs_samples
def test_case_selector_wired_from_the_vis_own_control_is_named_by_label() -> None:
    text = _describe(
        _SAMPLES / "JKI-VI-Tester" / "source",
        "Utilities/Tree_Control_VIs/Item Indent.vi",
        _SAMPLES / "JKI-VI-Tester" / "source",
    )
    assert "gated on error in (no error)" in text
    assert "Item Indent.vi.error" not in text


@pytest.mark.needs_samples
def test_case_selector_wired_from_a_local_variable_is_named() -> None:
    root = _SAMPLES / "OpenG" / "extracted"
    text = _describe(
        root,
        "File Group 0/user.lib/_OpenG.lib/string/string.llb/"
        "Number to Proper Engl Text__ogtk.vi",
    )
    assert "gated on hasHundredsPlace (local variable)" in text
    assert "selector:" not in text
    assert ".vi::" not in text
