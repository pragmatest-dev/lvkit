"""A wired input whose source is a gap constant reads as ``None`` in every
generator, so the VI generates with the one gap instead of stopping."""

from __future__ import annotations

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import ValueNotStored
from tests.native_vi import array, generate, node, scalar

I32 = scalar("NumInt32")
ARRAY = array("NumInt32")


def _index_array():
    return node(
        "ia",
        None,
        [(0, "input", ARRAY), (1, "output", I32), (2, "input", I32)],
        node_type="aIndx",
    )



def test_index_array_with_a_gap_constant_index_generates_with_one_gap() -> None:
    gaps: list[Gap] = []
    generated = generate(
        {"values": ARRAY},
        {"element": I32},
        [_index_array()],
        [("fp:values", "ia:0"), ("c1", "ia:2"), ("ia:1", "fp:element")],
        constants={"c1": (ValueNotStored(), I32)},
        gaps=gaps,
    )
    (gap,) = gaps
    assert "uid c1" in gap.reason
    body = generated.source[generated.source.index("def native_test") :]
    assert body.splitlines()[1].startswith(
        "    raise NotImplementedError('The constant"
    )
    with pytest.raises(NotImplementedError, match="uid c1"):
        generated.function([5, 6, 7])


def test_index_array_with_an_unwired_index_takes_the_first_element() -> None:
    gaps: list[Gap] = []
    generated = generate(
        {"values": ARRAY},
        {"element": I32},
        [_index_array()],
        [("fp:values", "ia:0"), ("ia:1", "fp:element")],
        gaps=gaps,
    )
    assert gaps == []
    assert generated.function([5, 6, 7]).element == 5


def test_index_array_with_a_wired_index_indexes_it() -> None:
    generated = generate(
        {"values": ARRAY, "i": I32},
        {"element": I32},
        [_index_array()],
        [("fp:values", "ia:0"), ("fp:i", "ia:2"), ("ia:1", "fp:element")],
    )
    assert generated.function([5, 6, 7], 2).element == 7


def _wired_context(*, bound: str | None):
    """A context where terminal ``dst`` of a node is wired from ``src``; ``src``
    is bound to ``bound`` when that is not None."""
    from lvkit.graph.models import WireEnd
    from tests.helpers import make_ctx

    context = make_ctx("src", "dst", "loose")
    graph = context.graph
    graph._graph.add_edge(
        graph._term_to_node["src"],
        graph._term_to_node["dst"],
        source=WireEnd(node_id=graph._term_to_node["src"], terminal_id="src"),
        dest=WireEnd(node_id=graph._term_to_node["dst"], terminal_id="dst"),
    )
    if bound is not None:
        context.bind("src", bound)
    return context


def _reader():
    return node(
        "reader",
        None,
        [(0, "input", I32)],
    )


def test_wired_input_reads_the_variable_none_for_unwired_and_none_for_a_gap() -> None:
    reader = _reader()
    reader.terminals[0].id = "dst"
    assert _wired_context(bound="x").wired_input("dst", reader) == "x"
    assert _wired_context(bound="None").wired_input("dst", reader) == "None"
    assert _wired_context(bound="x").wired_input("loose", reader) is None


def test_wired_input_with_no_variable_is_an_error_naming_the_input() -> None:
    from lvkit.codegen.errors import CodeGenError

    reader = _reader()
    reader.terminals[0].id = "dst"
    context = _wired_context(bound=None)
    with pytest.raises(CodeGenError, match="wired input 0 of 'reader' has no variable"):
        context.wired_input("dst", reader)


def test_a_bundle_into_a_waveform_with_no_cluster_wired_in_builds_the_value() -> None:
    from lvkit.models import LVType, LVTypeKind, Terminal

    waveform = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="MeasureData",
        measure_flavor="Float64Waveform",
    )
    bundle = node(
        "bd",
        None,
        [(0, "output", waveform), (1, "input", waveform)],
        node_type="nMux",
    )
    bundle.terminals[0].nmux_role = "agg"
    bundle.terminals[1].nmux_role = "agg"
    bundle.terminals.append(
        Terminal(
            id="bd:2",
            index=2,
            direction="input",
            nmux_role="list",
            nmux_field_index=1,
            lv_type=scalar("NumFloat64"),
        )
    )
    generated = generate(
        {"dt": scalar("NumFloat64")},
        {"wf": waveform},
        [bundle],
        [("fp:dt", "bd:2"), ("bd:0", "fp:wf")],
    )
    result = generated.function(2.5).wf
    assert (result.dt, result.y, result.attributes) == (2.5, [], None)
    assert not hasattr(result, "error")


def _loop_adding_one_to_an_indicator(gaps: list | None = None):
    """A For Loop of 3 iterations that adds 1 to the indicator ``count`` through a
    Local Variable read and writes the sum back to the indicator."""
    from lvkit.graph.models import LocalVariableNode, LoopNode
    from lvkit.models import Terminal, TunnelTerminal

    loop = LoopNode(
        id="lp",
        vi_path="Native Test.vi",
        name="lp",
        node_type="forLoop",
        loop_type="forLoop",
        terminals=[
            Terminal(id="lp:i", index=0, direction="output", lv_type=I32),
            TunnelTerminal(
                id="lp:n_out",
                index=2,
                direction="input",
                lv_type=I32,
                tunnel_type="lMax",
                boundary="outer",
                paired_id="lp:n_in",
            ),
            TunnelTerminal(
                id="lp:n_in",
                index=3,
                direction="output",
                lv_type=I32,
                tunnel_type="lMax",
                boundary="inner",
                paired_id="lp:n_out",
            ),
        ],
    )
    read = LocalVariableNode(
        id="lv",
        vi_path="Native Test.vi",
        name="count",
        node_type="gRef",
        control_name="count",
        control_terminal_id="fp:count",
        is_write=False,
        terminals=[Terminal(id="lv:0", index=0, direction="output", lv_type=I32)],
    )
    plus = node(
        "ad",
        1050,
        [(0, "output", I32), (1, "input", I32), (2, "input", I32)],
    )
    for inner in (read, plus):
        inner.parent = "lp"
    wires = [
        ("fp:n", "lp:n_out"),
        ("fp:count", "lv:0"),
        ("lv:0", "ad:1"),
        ("c1", "ad:2"),
        ("ad:0", "fp:count"),
    ]
    return generate(
        {"n": I32},
        {"count": I32},
        [loop, read, plus],
        wires,
        constants={"c1": (1, I32)},
        gaps=gaps,
    )


def test_a_local_variable_reads_the_indicator_an_earlier_iteration_wrote() -> None:
    gaps: list[Gap] = []
    generated = _loop_adding_one_to_an_indicator(gaps)
    assert gaps == []  # a loop with a tunnel and an indicator is not a cycle
    assert "count = 0\n" in generated.source
    assert generated.function(3).count == 3
    assert generated.function(0).count == 0


def _unbundle_selecting(index: int, element: object):
    from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal

    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="status", type=scalar("Boolean")),
            ClusterField(name="count", type=I32),
        ],
    )
    unbundle = node("ub", None, [], node_type="nMux")
    unbundle.terminals = [
        Terminal(
            id="ub:0", index=0, direction="input", nmux_role="agg", lv_type=cluster
        ),
        Terminal(
            id="ub:1",
            index=1,
            direction="output",
            nmux_role="list",
            nmux_field_index=index,
            lv_type=element,
        ),
    ]
    return cluster, unbundle


def _and_of_an_unbundled_element(index: int, gaps: list):
    cluster, unbundle = _unbundle_selecting(index, scalar("Boolean"))
    both = node(
        "an",
        1061,
        [
            (0, "output", scalar("Boolean")),
            (1, "input", scalar("Boolean")),
            (2, "input", scalar("Boolean")),
        ],
    )
    return generate(
        {"cluster": cluster, "x": scalar("Boolean")},
        {"r": scalar("Boolean")},
        [unbundle, both],
        [
            ("fp:cluster", "ub:0"),
            ("ub:1", "an:1"),
            ("fp:x", "an:2"),
            ("an:0", "fp:r"),
        ],
        gaps=gaps,
    )


def test_an_unbundle_element_that_selects_a_field_is_read() -> None:
    gaps: list[Gap] = []
    generated = _and_of_an_unbundled_element(0, gaps)
    assert gaps == []
    assert generated.function((True, 1), True).r is True
    assert generated.function((False, 1), True).r is False


def test_an_unbundle_element_stored_with_index_minus_one_is_a_gap() -> None:
    gaps: list[Gap] = []
    generated = _and_of_an_unbundled_element(-1, gaps)
    (gap,) = gaps
    assert gap.reason == (
        "An element of 'ub' stores its field index as -1, which lvkit does not "
        "decode yet"
    )
    assert gap.reason in generated.source
    with pytest.raises(NotImplementedError, match="does not decode yet"):
        generated.function(None, True)


def test_minus_one_is_kept_by_the_parser_as_the_stored_index() -> None:
    import xml.etree.ElementTree as ET

    from lvkit.parser.node_types import _build_nmux_dco_maps

    elem = ET.fromstring(
        '<SL__arrayElement class="nMux" uid="1"><termList>'
        '<SL__arrayElement class="term" uid="2"><dco class="nmxDCO" uid="3">'
        "<i>-1</i></dco></SL__arrayElement></termList></SL__arrayElement>"
    )
    _, field_index = _build_nmux_dco_maps(elem, ["3"], "i")
    assert field_index == {"3": -1}


def test_a_disable_output_left_unwired_is_the_type_default() -> None:
    from lvkit.graph.models import DisableStructureNode
    from lvkit.models import CaseFrame, TunnelTerminal

    disable = DisableStructureNode(
        id="dis",
        vi_path="Native Test.vi",
        name="Diagram Disable",
        node_type="commentNode",
        active_frame=0,
        frames=[CaseFrame(selector_value="Enabled"), CaseFrame(selector_value="Off")],
        terminals=[
            TunnelTerminal(
                id="dis:out",
                index=0,
                direction="output",
                lv_type=I32,
                tunnel_type="commentTun",
                boundary="outer",
                paired_id="dis:in",
            ),
            TunnelTerminal(
                id="dis:in",
                index=1,
                direction="input",
                lv_type=I32,
                tunnel_type="commentTun",
                boundary="inner",
                paired_id="dis:out",
                frame="Enabled",
            ),
        ],
    )
    plus = _add_one("ad")
    generated = generate(
        {},
        {"r": I32},
        [disable, plus],
        [("c0", "ad:1"), ("dis:out", "ad:2"), ("ad:0", "fp:r")],
        constants={"c0": (0, I32)},
    )
    assert "tunnel_default = 0" in generated.source
    assert "r=0 + tunnel_default" in generated.source
    assert generated.function().r == 0


def _add_one(node_id: str):
    return node(
        node_id, 1050, [(0, "output", I32), (1, "input", I32), (2, "input", I32)]
    )


def _plus(node_id: str):
    return node(
        node_id, 1050, [(0, "output", I32), (1, "input", I32), (2, "input", I32)]
    )


def test_wires_round_a_cycle_make_its_nodes_a_gap_not_a_bystander() -> None:
    gaps: list[Gap] = []
    generated = generate(
        {"x": I32},
        {"r": I32},
        [_plus("a"), _plus("b"), _plus("c")],
        [
            ("fp:x", "a:2"),
            ("a:0", "b:2"),
            ("b:0", "a:1"),
            ("b:0", "c:2"),
            ("c:0", "fp:r"),
            ("c0", "b:1"),
            ("c0", "c:1"),
        ],
        constants={"c0": (0, I32)},
        gaps=gaps,
    )
    reasons = sorted(g.reason for g in gaps)
    assert reasons == [
        "The wires between 'a', 'b' read as a cycle, which a saved, runnable VI "
        "cannot have; lvkit is likely misreading a terminal's direction here"
    ] * 2
    assert {g.node_name for g in gaps} == {"a", "b"}
    with pytest.raises(NotImplementedError, match="read as a cycle"):
        generated.function(1)


def test_a_parallel_branch_assigns_an_indicator_variable_of_the_function() -> None:
    import ast
    import concurrent.futures

    from lvkit.codegen.builder import _generate_parallel_tier
    from tests.helpers import make_ctx
    from tests.native_vi import array

    def index_row(node_id: str):
        return node(
            node_id,
            None,
            [(0, "input", array("NumInt32")), (1, "output", I32), (2, "input", I32)],
            node_type="aIndx",
        )

    ctx = make_ctx("a:0", "a:1", "a:2", "b:0", "b:1", "b:2")
    ctx.local_indicators.variables["fp:ind"] = "indexed_array"
    branches = _generate_parallel_tier([index_row("a"), index_row("b")], ctx)
    wrapper = ast.parse(
        "def outer():\n    indexed_array = None\n    return indexed_array"
    )
    outer = wrapper.body[0]
    assert isinstance(outer, ast.FunctionDef)
    outer.body[1:1] = branches
    namespace: dict = {"concurrent": concurrent}
    exec(compile(ast.fix_missing_locations(wrapper), "branch", "exec"), namespace)
    assert namespace["outer"]() == []


def test_a_control_wired_straight_to_an_indicator_sets_the_variable() -> None:
    from lvkit.graph.models import LocalVariableNode
    from lvkit.models import Terminal

    read = LocalVariableNode(
        id="lv",
        vi_path="Native Test.vi",
        name="ind",
        node_type="gRef",
        control_name="ind",
        control_terminal_id="fp:ind",
        is_write=False,
        terminals=[Terminal(id="lv:0", index=0, direction="output", lv_type=I32)],
    )
    generated = generate(
        {"x": I32},
        {"ind": I32, "r": I32},
        [read, _add_one("ad")],
        [
            ("fp:x", "fp:ind"),
            ("fp:ind", "lv:0"),
            ("lv:0", "ad:1"),
            ("c1", "ad:2"),
            ("ad:0", "fp:r"),
        ],
        constants={"c1": (1, I32)},
    )
    result = generated.function(5)
    assert (result.ind, result.r) == (5, 6)


def test_an_indicator_variable_is_not_shared_with_another_value() -> None:
    from lvkit.graph.models import LocalVariableNode
    from lvkit.models import Terminal

    read = LocalVariableNode(
        id="lv",
        vi_path="Native Test.vi",
        name="result",
        node_type="gRef",
        control_name="result",
        control_terminal_id="fp:result",
        is_write=False,
        terminals=[Terminal(id="lv:0", index=0, direction="output", lv_type=I32)],
    )
    generated = generate(
        {},
        {"result": I32},
        [read, _add_one("ad")],
        [
            ("fp:result", "lv:0"),
            ("lv:0", "ad:1"),
            ("c1", "ad:2"),
            ("ad:0", "fp:result"),
        ],
        constants={"c1": (1, I32)},
    )
    assert generated.source.count("result = 0\n") == 1
    assert generated.function().result == 1


def test_an_in_place_output_port_takes_the_value_of_an_inner_node() -> None:
    from lvkit.graph.models import InPlaceNode
    from lvkit.models import Terminal

    ipe = InPlaceNode(
        id="ipe",
        vi_path="Native Test.vi",
        name="In Place Element",
        node_type="decomposeRecomposeStructure",
        terminals=[
            Terminal(
                id="ipe:o", index=0, direction="output", lv_type=I32, dco_uid="d1"
            )
        ],
    )
    plus = _add_one("ad")
    plus.parent = "ipe"
    plus.terminals[0].dco_uid = "d1"
    generated = generate(
        {"x": I32},
        {"r": I32, "s": I32},
        [ipe, plus],
        [("c0", "ad:1"), ("fp:x", "ad:2"), ("ad:0", "fp:s"), ("ipe:o", "fp:r")],
        constants={"c0": (0, I32)},
    )
    result = generated.function(4)
    assert (result.r, result.s) == (4, 4)
