"""Synthetic round-trip checks for the lvnet structure syntax (#160, #169,
#176-#181): every form parses back and reconstructs to the same model -- which
node, terminal, tunnel, frame, and wired-versus-default -- not only to the same
bytes. Corpus-backed checks live in ``test_lvnet_identity.py`` /
``test_lvnet_reconstruct.py`` / ``test_lvnet_roundtrip.py``."""

from __future__ import annotations

import pytest

from lvkit.graph.interface_order import WiringRequirement
from lvkit.graph.lvnet_parse import LvnetParseError, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.lvnet_scalar import lvnet_typed_scalar
from lvkit.graph.models import Constant
from lvkit.graph.netlist import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    DefaultValue,
    MuMerge,
    NetlistBoundaryInput,
    NetlistFeedback,
    NetlistFrame,
    NetlistItem,
    NetlistModule,
    NetlistScope,
    NetRef,
    render_lvnet,
)
from lvkit.graph.netlist_models import LoopCondition, ScopeOutput
from lvkit.graph.render_lvnet import _lvnet_const_value_str, lvnet_literal_token
from lvkit.models import LVType, LVTypeKind

from .test_lvnet_identity import _assert_graph_identity

_BOOLEAN = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _control_ref(name: str) -> NetRef:
    return NetRef(node=None, terminal=name, occurrence=None, bare=name, boundary=True)


def _pane(name: str, direction: str, index: int) -> ConnectorPaneTerminal:
    return ConnectorPaneTerminal(
        name=name,
        type="TF",
        direction=direction,
        index=index,
        wiring_requirement=WiringRequirement.UNKNOWN,
        default=None,
        lv_type=_BOOLEAN,
    )


def _module(
    controls: list[str], body: list[NetlistItem] | None = None
) -> NetlistModule:
    """A VI whose Boolean controls each drive an indicator named ``out <i>``."""
    inputs = [
        NetlistBoundaryInput(name=n, type_descriptor="TF", lv_type=_BOOLEAN)
        for n in controls
    ]
    outputs = [
        BoundaryOutput(
            name=f"out {i}",
            type_descriptor="TF",
            source=_control_ref(n),
            lv_type=_BOOLEAN,
        )
        for i, n in enumerate(controls)
    ]
    panes = [_pane(n, "input", i) for i, n in enumerate(controls)] + [
        _pane(f"out {i}", "output", len(controls) + i) for i in range(len(controls))
    ]
    return NetlistModule(
        vi_name="Synthetic.vi",
        inputs=inputs,
        outputs=outputs,
        body=body or [],
        connector_pane=ConnectorPane(pattern_id=None, terminals=panes),
    )


def _round_trip(module: NetlistModule) -> tuple[str, NetlistModule]:
    text = render_lvnet(module, verbose=True)
    rebuilt = reconstruct_module(parse_lvnet(text))
    assert render_lvnet(rebuilt, verbose=True) == text
    return text, rebuilt


_RESERVED_CONTROL_NAMES = [
    "True",
    "False",
    "5",
    "-1.5",
    "NotAPath",
    'Path("x")',
    "default",
    "?",
    "read x",
    "condition",
    "tunnel t",
    "shift-register s",
    "(default TF)",
    "case_951::out1",
    "loop_146::i",
    "fb0",
    "a::b",
    "case x",
    "constant c",
]


_QUOTED_CONTROL_NAMES = [
    n for n in _RESERVED_CONTROL_NAMES if n not in {"condition", "case x", "constant c"}
]


@pytest.mark.parametrize("name", _RESERVED_CONTROL_NAMES)
def test_a_control_named_like_a_reserved_form_is_its_own_net(name: str) -> None:
    """A control named like a literal or reserved form renders quoted, and the
    indicator it drives resolves back to that control, not to a literal."""
    module = _module([name])
    text, rebuilt = _round_trip(module)
    if name in _QUOTED_CONTROL_NAMES:
        assert f"= {lvnet_literal_token(name)}" in text
    source = rebuilt.outputs[0].source
    assert source is not None
    assert source.boundary and source.bare == name
    assert rebuilt.inputs[0].name == name


def test_all_reserved_names_together_keep_their_own_indicator() -> None:
    module = _module(_RESERVED_CONTROL_NAMES)
    _, rebuilt = _round_trip(module)
    assert [o.source.bare for o in rebuilt.outputs if o.source] == (
        _RESERVED_CONTROL_NAMES
    )


def test_boolean_and_numeric_text_is_read_back_by_its_type() -> None:
    assert lvnet_typed_scalar("True", _BOOLEAN) is True
    assert lvnet_typed_scalar("False", _BOOLEAN) is False
    assert lvnet_typed_scalar("-1", _I32) == -1
    assert lvnet_typed_scalar("hello", None) == "hello"
    assert lvnet_typed_scalar("not a number", _I32) == "not a number"


def test_a_boolean_constant_renders_as_a_boolean_not_a_string() -> None:
    const = Constant(id="vi::5", value="True", lv_type=_BOOLEAN)
    assert _lvnet_const_value_str(const) == "True"
    off = Constant(id="vi::6", value="False", lv_type=_BOOLEAN)
    assert _lvnet_const_value_str(off) == "False"


# ---------------------------------------------------------------------------
# Loops: conditional terminal, count wire, uninitialized registers (#178)
# ---------------------------------------------------------------------------


def _loop(
    uid: str,
    kind: str,
    *,
    mode: LoopCondition | None,
    selector: NetRef | None = None,
    count: NetRef | None = None,
    outputs: list[ScopeOutput] | None = None,
) -> NetlistScope:
    return NetlistScope(
        uid=uid,
        kind=kind,
        selector=selector,
        frames=[NetlistFrame(label="", value="", is_default=False, body=[])],
        outputs=outputs or [],
        condition_mode=mode,
        count=count,
    )


def _identity_round_trip(module: NetlistModule) -> tuple[str, NetlistModule]:
    text, rebuilt = _round_trip(module)
    _assert_graph_identity(module, rebuilt, "synthetic")
    return text, rebuilt


@pytest.mark.parametrize(
    "mode", [LoopCondition.STOP_IF_TRUE, LoopCondition.CONTINUE_IF_TRUE]
)
def test_a_while_loop_header_says_how_its_condition_reads(mode: LoopCondition) -> None:
    loop = _loop("43", "while", mode=mode, selector=_control_ref("stop"))
    text, rebuilt = _identity_round_trip(_module(["stop"], [loop]))
    assert f"while-loop ({mode.value}) (id 43) :" in text
    assert "loop_43::condition = stop" in text
    scope = rebuilt.body[0]
    assert isinstance(scope, NetlistScope)
    assert scope.condition_mode is mode
    assert scope.selector is not None and scope.selector.bare == "stop"


def test_a_for_loop_without_a_conditional_terminal_has_no_setting() -> None:
    loop = _loop("7", "for", mode=None, count=_control_ref("n"))
    text, rebuilt = _identity_round_trip(_module(["n"], [loop]))
    assert "for-loop (id 7) :" in text
    assert "loop_7::N = n" in text
    assert "condition" not in text
    scope = rebuilt.body[0]
    assert isinstance(scope, NetlistScope)
    assert scope.condition_mode is None and scope.selector is None
    assert scope.count is not None and scope.count.bare == "n"


def test_a_for_loop_with_a_conditional_terminal_carries_the_setting() -> None:
    loop = _loop(
        "8",
        "for",
        mode=LoopCondition.STOP_IF_TRUE,
        selector=_control_ref("done"),
        count=_control_ref("n"),
    )
    text, rebuilt = _identity_round_trip(_module(["n", "done"], [loop]))
    assert "for-loop (stop if true) (id 8) :" in text
    scope = rebuilt.body[0]
    assert isinstance(scope, NetlistScope)
    assert scope.condition_mode is LoopCondition.STOP_IF_TRUE
    assert scope.count is not None and scope.selector is not None


def test_an_unwired_conditional_terminal_is_a_visible_gap() -> None:
    loop = _loop("9", "while", mode=LoopCondition.STOP_IF_TRUE, selector=None)
    text, rebuilt = _identity_round_trip(_module([], [loop]))
    assert "loop_9::condition = ?" in text
    scope = rebuilt.body[0]
    assert isinstance(scope, NetlistScope)
    assert scope.selector is None and scope.condition_mode is not None


def test_a_terse_loop_still_recovers_its_uid_from_its_lines() -> None:
    loop = _loop("43", "while", mode=LoopCondition.STOP_IF_TRUE)
    module = _module([], [loop])
    text = render_lvnet(module)  # terse: no "(id 43)"
    assert "(id 43)" not in text
    scope = reconstruct_module(parse_lvnet(text)).body[0]
    assert isinstance(scope, NetlistScope) and scope.uid == "43"


def test_loop_header_errors_are_loud() -> None:
    base = "vi V.vi :\n  block-diagram :\n"
    with pytest.raises(LvnetParseError):
        parse_lvnet(base + "    while-loop :\n")
    with pytest.raises(LvnetParseError):
        parse_lvnet(base + "    while-loop (sometimes) :\n")


def _shift(init: NetRef | DefaultValue, each: NetRef | None) -> MuMerge:
    return MuMerge(net="loop_5.shift0", init=init, recur=each)


def test_an_uninitialized_shift_register_has_no_init_line() -> None:
    loop = _loop(
        "5",
        "while",
        mode=LoopCondition.STOP_IF_TRUE,
        outputs=[_shift(DefaultValue("[]", "[String]"), _control_ref("x"))],
    )
    text, rebuilt = _identity_round_trip(_module(["x"], [loop]))
    assert "shift-register loop_5::shift0 (uninitialized) :" in text
    assert "init =" not in text
    assert "each = x" in text
    scope = rebuilt.body[0]
    assert isinstance(scope, NetlistScope)
    register = scope.outputs[0]
    assert isinstance(register, MuMerge) and isinstance(register.init, DefaultValue)


def test_an_initialized_shift_register_keeps_its_init_line() -> None:
    loop = _loop(
        "5",
        "while",
        mode=LoopCondition.STOP_IF_TRUE,
        outputs=[_shift(_control_ref("seed"), _control_ref("x"))],
    )
    text, rebuilt = _identity_round_trip(_module(["seed", "x"], [loop]))
    assert "shift-register loop_5::shift0 :" in text
    assert "init = seed" in text
    scope = rebuilt.body[0]
    assert isinstance(scope, NetlistScope)
    register = scope.outputs[0]
    assert isinstance(register, MuMerge) and isinstance(register.init, NetRef)


def test_an_uninitialized_register_with_an_init_line_is_a_parse_error() -> None:
    text = (
        "vi V.vi :\n  block-diagram :\n"
        "    while-loop (stop if true) :\n"
        "      loop_1::condition = ?\n"
        "      shift-register loop_1::shift0 (uninitialized) :\n"
        "        init = 0\n"
    )
    with pytest.raises(LvnetParseError):
        parse_lvnet(text)


def test_an_initialized_register_without_an_init_line_is_a_parse_error() -> None:
    text = (
        "vi V.vi :\n  block-diagram :\n"
        "    while-loop (stop if true) :\n"
        "      loop_1::condition = ?\n"
        "      shift-register loop_1::shift0 :\n"
        "        each = 1\n"
    )
    with pytest.raises(LvnetParseError):
        parse_lvnet(text)


@pytest.mark.parametrize("uninitialized", [True, False])
def test_a_feedback_node_marks_an_unwired_initializer(uninitialized: bool) -> None:
    feedback = NetlistFeedback(
        uid="12",
        net="fb0",
        init=(
            DefaultValue("0.0", "DBL") if uninitialized else _control_ref("seed")
        ),
        recur=_control_ref("x"),
        delay=1,
    )
    text, rebuilt = _identity_round_trip(_module(["seed", "x"], [feedback]))
    header = "feedback-node fb0 (1 iteration, uninitialized) :"
    if uninitialized:
        assert header in text and "init =" not in text
    else:
        assert "feedback-node fb0 (1 iteration) :" in text and "init = seed" in text
    rebuilt_fb = rebuilt.body[0]
    assert isinstance(rebuilt_fb, NetlistFeedback)
    assert isinstance(rebuilt_fb.init, DefaultValue) is uninitialized
