"""An In Place Element structure's border nodes (Unbundle/Bundle Elements) are
named nets inside the structure: a value a border node supplies to the body is
``inplace_<uid>::get<k>``, and a value the body wires into a border node's
input is the drive line ``inplace_<uid>::put<k> = <source>``.

Hand-built model so both faces are pinned independently of the sample corpus:
render, parse, reconstruct, and the JSON projection must all carry them.
"""

from __future__ import annotations

from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import (
    InPlaceBorderDrive,
    NetlistFrame,
    NetlistInstance,
    NetlistModule,
    NetlistOutput,
    NetlistScope,
    NetlistTerminalBinding,
    NetRef,
    netlist_to_dict,
)
from lvkit.graph.render_lvnet import render_lvnet
from tests.test_lvnet_identity import _assert_graph_identity

_IPES_UID = "77"
_READER_UID = "5"


def _module() -> NetlistModule:
    """One In Place Element structure whose body reads the first border-node
    output (``get0``) and writes the result back into the first border-node
    input (``put0``)."""
    read_net = NetRef(
        node=None,
        terminal=f"inplace_{_IPES_UID}.get0",
        occurrence=None,
        bare=f"inplace_{_IPES_UID}.get0",
    )
    result = NetRef(
        node="Increment",
        terminal="out",
        occurrence=None,
        bare="Increment.out",
        producer_uid=_READER_UID,
    )
    reader = NetlistInstance(
        uid=_READER_UID,
        name="Increment",
        occurrence=None,
        inputs=[NetlistTerminalBinding(terminal="x", type="I32", net=read_net)],
        outputs=[NetlistOutput(net=result, type="I32")],
    )
    frame = NetlistFrame(
        label="", value="", is_default=False, body=[reader], passthrough=False
    )
    scope = NetlistScope(
        uid=_IPES_UID,
        kind="inplace",
        selector=None,
        frames=[frame],
        outputs=[InPlaceBorderDrive(net=f"inplace_{_IPES_UID}.put0", value=result)],
    )
    return NetlistModule(vi_name="Synthetic.vi", inputs=[], outputs=[], body=[scope])


def test_both_faces_render_as_structure_scoped_nets() -> None:
    text = render_lvnet(_module(), display_name="Synthetic.vi", verbose=True)
    assert f"in-place-element (id {_IPES_UID}) :" in text
    assert f"= inplace_{_IPES_UID}::get0" in text
    assert f"inplace_{_IPES_UID}::put0 = Increment_{_READER_UID}::out" in text


def test_both_faces_round_trip_with_graph_identity() -> None:
    module = _module()
    text = render_lvnet(module, display_name="Synthetic.vi", verbose=True)
    parsed = parse_lvnet(text)
    assert netlist_signature(module) == netlist_signature(parsed)
    reconstructed = reconstruct_module(parsed)
    _assert_graph_identity(module, reconstructed, "Synthetic.vi")
    rerendered = render_lvnet(reconstructed, display_name="Synthetic.vi", verbose=True)
    assert rerendered == text


def test_terse_text_recovers_the_structure_uid_from_the_drive_net() -> None:
    module = _module()
    text = render_lvnet(module, display_name="Synthetic.vi", verbose=False)
    reconstructed = reconstruct_module(parse_lvnet(text))
    scope = reconstructed.body[0]
    assert isinstance(scope, NetlistScope)
    assert scope.uid == _IPES_UID


def test_json_carries_the_drive_as_a_border_input() -> None:
    scope = netlist_to_dict(_module())["body"][0]
    assert scope["scope_kind"] == "inplace"
    [drive] = scope["outputs"]
    assert drive["net"] == f"inplace_{_IPES_UID}.put0"
    assert drive["kind"] == "border_input"
    assert drive["value"]["bare"] == "Increment.out"
