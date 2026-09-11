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
        ParsedFPPart(None, "stdNum", (21, 47, 47, 112)),
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


def test_state_array_fields_use_default_factory() -> None:
    """A list field with a bare ``[]`` default crashes at class definition; the
    generator must emit ``field(default_factory=list)``. Exec proves it."""
    src = build_state_module(_array_panel())
    assert "field(default_factory=list)" in src
    assert ": list = []" not in src
    ns: dict = {}
    exec(compile(src, "<state>", "exec"), ns)  # noqa: S102
    state = ns["State"]()
    assert state.array == []
    # Independent instances must not share the same list object.
    other = ns["State"]()
    state.array.append(1)
    assert other.array == []


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
    assert "from controls import array_control" in src
    # Inputs are editable, the indicator is read-only.
    assert "array_control(state, 'array', readonly=False" in src
    assert "array_control(state, 'reordered_array', readonly=True" in src
    # The indicator array is refreshed after Run writes the new list.
    assert "_w_reordered_array()" in src
    # It's syntactically valid Python.
    compile(src, "<panel>", "exec")


def test_wrappers_pin_bounds_and_clip_overflow() -> None:
    """Each control wrapper is pinned to its FP bounds height with
    overflow:hidden — the array control is an index-driven fixed viewport, never
    a box that grows past its bounds. It must NOT use min-height (which grows).
    An array's caption sits above the data box, so its wrapper is the bounds
    height PLUS the caption strip (see _CAPTION_H)."""
    src = build_panel_module(
        _array_panel(),
        logic_module_stem="logic",
        logic_func_name="reorder",
        param_names=["array", "indices"],
        result_fields=["reordered_array"],
    )
    assert "min-height" not in src
    # The array box is 51px tall (bounds 16..67); wrapper = 51 + 16 caption.
    assert "height:67px;overflow:hidden;" in src
    # The array control is sized from the REAL parsed part geometry: element cell
    # 26px tall at x=47 -> cell_h=26, index_width=47, visible=(51-21)//26=1.
    assert "cell_h=26" in src
    assert "index_width=47" in src
    assert "element_type='stdNum'" in src
