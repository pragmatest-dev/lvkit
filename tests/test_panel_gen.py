"""Unit tests for the NiceGUI front-panel generator (``scripts/panelgen``).

Corpus-independent: they build a synthetic ``ParsedFrontPanel`` and drive the
string generators directly, so they run on a fresh clone. They guard two things
that the codegen functional suite can't see, because both live in the generated
*panel* files, not the logic:

1. ``state.py`` must import cleanly — an array field is a ``list``, and a
   dataclass rejects a bare mutable default, so the generator must route it
   through ``field(default_factory=...)``. This test execs the emitted source.
2. An array control/indicator must render via the composed ``array_control``
   (native NiceGUI), not the string-input fallback, and an array *indicator*
   must be refreshed after Run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit.parser.models import ParsedFPControl, ParsedFPPart, ParsedFrontPanel

# panelgen lives under scripts/, alongside gen_panel.py's own path shim.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from panelgen.panel_gen import build_panel_module  # noqa: E402
from panelgen.state_gen import build_state_module  # noqa: E402


def _array_parts() -> list[ParsedFPPart]:
    """The real array-control parts a VI records: an index display (partID 8002)
    and an element cell (the element ddo, part_id None), in control-local coords
    — element cell 26px tall starting at x=47, so index column = 47 wide."""
    return [
        ParsedFPPart(8002, "stdNum", (14, 2, 44, 43)),
        ParsedFPPart(
            None, "stdNum", (21, 47, 47, 112),
            {"StdNumMin": "-2147483648", "StdNumMax": "2147483647", "StdNumInc": "0"},
        ),
    ]


def _array_panel() -> ParsedFrontPanel:
    """array in, indices in, reordered array out — the Reorder shape."""
    return ParsedFrontPanel(
        controls=[
            ParsedFPControl(
                uid="1", name="array", control_type="indArr",
                bounds=(16, 16, 67, 132), is_indicator=False, parts=_array_parts(),
            ),
            ParsedFPControl(
                uid="2", name="indices", control_type="indArr",
                bounds=(87, 16, 140, 132), is_indicator=False, parts=_array_parts(),
            ),
            ParsedFPControl(
                uid="3", name="reordered array", control_type="indArr",
                bounds=(20, 269, 70, 386), is_indicator=True, parts=_array_parts(),
            ),
        ],
        panel_bounds=(0, 0, 200, 400),
    )


def test_state_is_bindable_and_lists_are_independent() -> None:
    """State is a @binding.bindable_dataclass; a list field must use
    default_factory (a bare ``[]`` default crashes) so instances don't share a
    list. String-check always; exec only when NiceGUI is installed."""
    src = build_state_module(_array_panel())
    assert "@binding.bindable_dataclass" in src
    assert "from nicegui import binding" in src
    assert "field(default_factory=list)" in src
    assert ": list = []" not in src
    pytest.importorskip("nicegui")  # exec needs the binding module
    ns: dict = {}
    exec(compile(src, "<state>", "exec"), ns)  # noqa: S102
    state = ns["State"]()
    assert state.array == []
    other = ns["State"]()
    state.array.append(1)
    assert other.array == []  # independent lists, and bindable


def test_panel_uses_array_control_and_refreshes_indicator() -> None:
    """Array controls render via the composed array_control; the array indicator
    is refreshed after Run (it isn't bind_value-driven)."""
    src = build_panel_module(
        _array_panel(),
        logic_module_stem="logic",
        logic_func_name="reorder",
        param_names=["array", "indices"],
        result_fields=["reordered_array"],
    )
    # No string-input fallback for the arrays.
    assert "ui.input" not in src
    assert "array_control" in src
    # Inputs are editable, the indicator is read-only.
    assert "array_control(state, 'array', readonly=False" in src
    assert "array_control(state, 'reordered_array', readonly=True" in src
    # The indicator array is refreshed after Run writes the new list.
    assert "_w_reordered_array()" in src
    # Execution goes through the VI toolbar + RunController, not a lone button.
    assert "toolbar(controller)" in src
    assert "RunController(compute)" in src
    # It's syntactically valid Python.
    compile(src, "<panel>", "exec")


def test_array_wrappers_positioned_from_bounds_and_typed() -> None:
    """An array renders as an AG Grid sized to its own content (data rows + the
    pinned add row), so its wrapper is POSITIONED from the FP bounds (caption
    lifted above) but not clipped to the tiny box. The grid is sized + typed
    from the real parsed part geometry + properties."""
    src = build_panel_module(
        _array_panel(),
        logic_module_stem="logic",
        logic_func_name="reorder",
        param_names=["array", "indices"],
        result_fields=["reordered_array"],
    )
    # Array wrappers position from bounds (array top 16, caption-lifted to 0),
    # width 116 -- and do NOT clip (the grid provides its own height/scroll).
    assert "left:16px;top:0px;width:116px;" in src
    assert "overflow:hidden" not in src  # every control here is an array
    assert "min-height" not in src
    # Sized + typed from the real element geometry + properties.
    assert "cell_h=26" in src
    assert "element_type='stdNum'" in src
    assert "integer=True" in src
