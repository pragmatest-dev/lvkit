"""A wired input that has no variable is an lvkit error, an unwired input takes
its default, and the loop and node cases that used to leave a wire without a
variable now carry one or are gaps."""

from __future__ import annotations

import pytest

from lvkit.codegen.errors import CodeGenError
from lvkit.codegen.gaps import Gap
from tests.native_vi import generate, node, scalar
from tests.test_match_pattern import STRING

I32 = scalar("NumInt32")


def _add(node_id: str):
    return node(
        node_id,
        1050,
        [(0, "output", I32), (1, "input", I32), (2, "input", I32)],
    )


def test_unwired_inputs_take_their_defaults() -> None:
    generated = generate(
        {"x": I32},
        {"r": I32},
        [_add("ad")],
        [("fp:x", "ad:2"), ("ad:0", "fp:r")],
    )
    assert generated.function(5).r == 5
    assert "r=0 + x" in generated.source


def test_wired_input_with_no_variable_is_an_error() -> None:
    silent = node("mg", 2147, [(0, "output", I32)])
    with pytest.raises(CodeGenError, match="'y' of 'ad' has no variable"):
        generate(
            {"x": I32},
            {"r": I32},
            [silent, _add("ad")],
            [("fp:x", "ad:2"), ("mg:0", "ad:1"), ("ad:0", "fp:r")],
        )


def _loop_with_counter(loop_type: str):
    from lvkit.graph.models import LoopNode
    from lvkit.models import Terminal

    loop = LoopNode(
        id="lp",
        vi_path="Native Test.vi",
        name="lp",
        node_type=loop_type,
        loop_type=loop_type,
        stop_condition_terminal="lp:c" if loop_type == "whileLoop" else None,
        terminals=[
            Terminal(id="lp:i", index=0, direction="output", lv_type=I32),
            Terminal(
                id="lp:c",
                index=1,
                direction="input",
                lv_type=scalar("Boolean"),
            ),
        ],
    )
    return loop


def test_while_loop_iteration_terminal_counts_from_zero() -> None:
    loop = _loop_with_counter("whileLoop")
    ge = node(
        "ge",
        1103,
        [
            (0, "output", scalar("Boolean")),
            (1, "input", I32),
            (2, "input", I32),
        ],
    )
    ge.parent = "lp"
    generated = generate(
        {},
        {},
        [loop, ge],
        [("lp:i", "ge:2"), ("c2", "ge:1"), ("ge:0", "lp:c")],
        constants={"c2": (2, I32)},
    )
    assert "    i = 0\n    while True:\n" in generated.source
    assert (
        "    while True:\n        pass\n        if i >= 2:\n            break\n"
        "        i += 1"
    ) in generated.source
    generated.function()


def test_for_loop_count_read_inside_the_loop_is_the_wired_count() -> None:
    from lvkit.models import TunnelTerminal

    loop = _loop_with_counter("forLoop")
    loop.terminals.extend(
        [
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
        ]
    )
    plus = _add("ad")
    plus.parent = "lp"
    plus.terminals[0].lv_type = I32
    generated = generate(
        {"n": I32},
        {"r": I32},
        [loop, plus],
        [("fp:n", "lp:n_out"), ("lp:n_in", "ad:2"), ("ad:0", "fp:r")],
    )
    assert "    count = n\n    for i in range(n):\n" in generated.source
    assert "0 + count" in generated.source
    generated.function(3)


def test_node_class_without_a_generator_is_a_gap_when_a_wire_leaves_it() -> None:
    scan = node(
        "sc",
        None,
        [(1, "input", STRING), (8, "output", I32)],
        node_type="scanf",
    )
    gaps: list[Gap] = []
    generated = generate(
        {"s": STRING},
        {"r": I32},
        [scan],
        [("fp:s", "sc:1"), ("sc:8", "fp:r")],
        gaps=gaps,
    )
    assert [g.node_id for g in gaps] == ["sc"]
    with pytest.raises(NotImplementedError):
        generated.function("1")


def test_reference_naming_no_control_is_one_gap_and_its_consumers_generate() -> None:
    from lvkit.graph.models import LocalVariableNode
    from lvkit.models import Terminal
    from tests.test_match_pattern import _OUTPUT_WIRES, _OUTPUTS, _node

    reference = LocalVariableNode(
        id="ref",
        vi_path="Native Test.vi",
        name="This Application",
        node_type="ctlRefConst",
        control_name="This Application",
        terminals=[
            Terminal(
                id="ref:0",
                index=0,
                direction="output",
                name="This Application",
                lv_type=STRING,
            )
        ],
    )
    length = node("ln", 1502, [(0, "output", I32), (1, "input", STRING)])
    gaps: list[Gap] = []
    run = generate(
        {},
        {**_OUTPUTS, "n": I32},
        [reference, length, _node()],
        [
            ("ref:0", "ln:1"),
            ("ln:0", "fp:n"),
            ("ref:0", "mp:0"),
            ("c1", "mp:1"),
            *_OUTPUT_WIRES,
        ],
        constants={"c1": ("a", STRING)},
        gaps=gaps,
    )
    (gap,) = gaps
    assert gap.reason == (
        "The reference 'This Application' names no front-panel control, so there "
        "is no value to read"
    )
    text = run.source[run.source.index("def native_test") :]
    assert "this_application = None\n" in text
    assert "len(this_application)" in text
    assert "re.search('a', this_application)" in text
    with pytest.raises(NotImplementedError, match="This Application"):
        run.function()
