"""A Compound Arithmetic node's mode (and/or/add/multiply/xor) is the only thing
that tells one node from another -- every mode shares the display name
"Compound Arithmetic". The lvnet header carries it as the node's ``mode``
setting, ``Compound Arithmetic (mode=or)``, and reading the text back keeps it.

Hand-built model, so the test does not read the sample corpus.
"""

from __future__ import annotations

import pytest

from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.netlist import (
    NetlistInstance,
    NetlistModule,
    NetlistOutput,
    NetlistTerminalBinding,
    NetRef,
)
from lvkit.graph.netlist_models import CompoundMode, FunctionSettings
from lvkit.graph.render_lvnet import render_lvnet

from .test_lvnet_structure_syntax import _round_trip


def _module(
    mode: CompoundMode | None, name: str = "Compound Arithmetic"
) -> NetlistModule:
    ref = NetRef(node=None, terminal="a", occurrence=None, bare="a")
    inst = NetlistInstance(
        uid="12",
        name=name,
        occurrence=1,
        inputs=[
            NetlistTerminalBinding(terminal="1", type="TF", net=ref),
            NetlistTerminalBinding(terminal="2", type="TF", net=ref),
        ],
        outputs=[
            NetlistOutput(
                net=NetRef(node=name, terminal="0", occurrence=1, bare=f"{name}#1.0"),
                type="TF",
            )
        ],
        settings=None if mode is None else FunctionSettings(mode),
    )
    return NetlistModule(vi_name="c.vi", inputs=[], outputs=[], body=[inst])


@pytest.mark.parametrize("mode", list(CompoundMode))
def test_header_names_the_mode(mode: CompoundMode) -> None:
    text = render_lvnet(_module(mode))

    header = next(line for line in text.splitlines() if "Compound_Arithmetic" in line)
    assert header.endswith(f"Compound Arithmetic (mode={mode.value})")


def test_a_node_without_a_mode_has_no_annotation() -> None:
    text = render_lvnet(_module(None, name="Not"))

    assert "mode=" not in text


@pytest.mark.parametrize("mode", list(CompoundMode))
def test_the_annotated_text_still_parses(mode: CompoundMode) -> None:
    parse_lvnet(render_lvnet(_module(mode)))
    _, rebuilt = _round_trip(_module(mode))

    assert [
        item.settings for item in rebuilt.body if isinstance(item, NetlistInstance)
    ] == [FunctionSettings(mode)]
