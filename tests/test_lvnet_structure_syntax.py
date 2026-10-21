"""Synthetic round-trip checks for the lvnet structure syntax (#160, #169,
#176-#181): every form parses back and reconstructs to the same model -- which
node, terminal, tunnel, frame, and wired-versus-default -- not only to the same
bytes. Corpus-backed checks live in ``test_lvnet_identity.py`` /
``test_lvnet_reconstruct.py`` / ``test_lvnet_roundtrip.py``."""

from __future__ import annotations

import pytest

from lvkit.graph.interface_order import WiringRequirement
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.lvnet_scalar import lvnet_typed_scalar
from lvkit.graph.models import Constant
from lvkit.graph.netlist import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    NetlistBoundaryInput,
    NetlistItem,
    NetlistModule,
    NetRef,
    render_lvnet,
)
from lvkit.graph.render_lvnet import _lvnet_const_value_str, lvnet_literal_token
from lvkit.models import LVType, LVTypeKind

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
