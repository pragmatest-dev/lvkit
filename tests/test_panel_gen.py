"""Unit tests for the NiceGUI front-panel generator (``scripts/panelgen``).

Corpus-independent: they build a synthetic ``ParsedFrontPanel`` and drive the
string generators directly, so they run on a fresh clone. They guard three
things that the codegen functional suite can't see, because all live in the
generated *panel* file, not the logic:

1. The bindable ``State`` classes must exec cleanly — an array field is a
   ``list``, and a dataclass rejects a bare mutable default, so the generator
   must route it through ``field(default_factory=...)``. This test execs it.
2. An array control/indicator must render via the composed ``array_control``
   (native NiceGUI), not the string-input fallback, and an array *indicator*
   must be refreshed after Run.
3. The panel module is self-contained: the ``State`` view-model is inlined (no
   separate ``state`` import) and a guarded ``__main__`` runner serves it.
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


def _scalar_panel() -> ParsedFrontPanel:
    """A scalar numeric input + a scalar path indicator — to check that a scalar's
    label renders as a caption ABOVE the box (like an array's), not inside it."""
    return ParsedFrontPanel(
        controls=[
            ParsedFPControl(
                uid="1", name="threshold", control_type="stdNum",
                bounds=(20, 20, 60, 150), is_indicator=False,
            ),
            ParsedFPControl(
                uid="2", name="out path", control_type="stdPath",
                bounds=(20, 170, 60, 320), is_indicator=True,
            ),
        ],
        panel_bounds=(0, 0, 90, 340),
    )


def test_scalar_label_is_a_caption_above_the_box() -> None:
    """A scalar renders its label as a caption above the box (consistent with the
    array control's caption, faithful to the VI's label-above-box geometry), and
    the widget itself carries no internal (floating) label."""
    src = build_panel_module(
        _scalar_panel(),
        logic_module_stem="f",
        logic_func_name="f",
        param_names=["threshold"],
        result_fields=["out_path"],
    )
    # Caption label above, in a column wrapper.
    assert "with ui.column().classes('w-full gap-1 no-wrap'):" in src
    assert "ui.label('threshold')" in src
    # The number widget has NO internal floating label.
    assert "ui.number()" in src
    assert "ui.number(label=" not in src
    # The path control is given no label (the caption is external).
    assert "path_control(state, 'out_path', readonly=True)" in src
    assert "path_control(state, 'out_path', readonly=True, label=" not in src
    compile(src, "<panel>", "exec")


def _cluster_array_panel() -> ParsedFrontPanel:
    """An array whose element is a cluster (source:str, code:num, status:bool) —
    the error-array shape. The parser exposes the element cluster's fields as the
    array control's ``children``."""
    return ParsedFrontPanel(
        controls=[
            ParsedFPControl(
                uid="1", name="errors", control_type="indArr",
                bounds=(16, 16, 140, 186), is_indicator=False, parts=_array_parts(),
                children=[
                    ParsedFPControl(uid="1a", name="source",
                                    control_type="stdString", bounds=(0, 0, 17, 60),
                                    is_indicator=False),
                    ParsedFPControl(uid="1b", name="code", control_type="stdNum",
                                    bounds=(0, 0, 17, 60), is_indicator=False),
                    ParsedFPControl(uid="1c", name="status", control_type="stdBool",
                                    bounds=(0, 0, 17, 60), is_indicator=False),
                ],
            ),
        ],
        panel_bounds=(0, 0, 200, 300),
    )


def test_array_of_clusters_emits_typed_columns() -> None:
    """An array of clusters renders one typed AG Grid column per cluster field
    (via ArrayField), not a single scalar value column."""
    src = build_panel_module(
        _cluster_array_panel(),
        logic_module_stem="f",
        logic_func_name="f",
        param_names=["errors"],
        result_fields=None,
    )
    assert "fields=[ArrayField(" in src
    assert "ArrayField('source', 'source', 'stdString')" in src
    # H1 fix: a numeric cluster-array field carries its real range/
    # representation (here None/None/True -- the synthetic control has no
    # parsed StdNumMin/StdNumMax part), the same as a scalar array's element.
    assert (
        "ArrayField('code', 'code', 'stdNum', "
        "integer=True, num_min=None, num_max=None)"
    ) in src
    assert "ArrayField('status', 'status', 'stdBool')" in src
    # ArrayField is imported, and this array is NOT emitted as a scalar column.
    assert "ArrayField" in src.split("def build_panel")[0]  # in the imports
    assert "element_type=" not in src
    compile(src, "<panel>", "exec")


def test_array_of_clusters_enum_field_emits_values() -> None:
    """An enum/ring cluster field carries its options through as ArrayField
    values, so the grid gives a dropdown (agSelectCellEditor) like ui.select."""
    panel = ParsedFrontPanel(
        controls=[
            ParsedFPControl(
                uid="1", name="items", control_type="indArr",
                bounds=(16, 16, 140, 186), is_indicator=False, parts=_array_parts(),
                children=[
                    ParsedFPControl(uid="1a", name="name",
                                    control_type="stdString", bounds=(0, 0, 17, 60),
                                    is_indicator=False),
                    ParsedFPControl(uid="1b", name="mode", control_type="stdEnum",
                                    bounds=(0, 0, 17, 60), is_indicator=False,
                                    enum_values=["No Op", "Increment", "Reset"]),
                ],
            ),
        ],
        panel_bounds=(0, 0, 200, 300),
    )
    src = build_panel_module(
        panel, logic_module_stem="f", logic_func_name="f",
        param_names=["items"], result_fields=None,
    )
    assert (
        "ArrayField('mode', 'mode', 'stdEnum', "
        "values=['No Op', 'Increment', 'Reset'])"
    ) in src
    compile(src, "<panel>", "exec")


def test_array_of_nested_clusters_emits_column_group() -> None:
    """A cluster field that is itself a cluster, inside an array-of-clusters,
    emits a NESTED ArrayField(..., 'stdClust', fields=[...]) — a column group —
    by strategy composition, not a flat text cell."""
    addr = ParsedFPControl(
        uid="a", name="addr", control_type="stdClust", bounds=(0, 0, 40, 80),
        is_indicator=False,
        children=[
            ParsedFPControl(uid="s", name="street", control_type="stdString",
                            bounds=(0, 0, 17, 40), is_indicator=False),
            ParsedFPControl(uid="z", name="zip", control_type="stdNum",
                            bounds=(0, 0, 17, 40), is_indicator=False),
        ],
    )
    arr = ParsedFPControl(
        uid="1", name="people", control_type="indArr", bounds=(16, 16, 140, 186),
        is_indicator=False, parts=_array_parts(),
        children=[
            ParsedFPControl(uid="n", name="name", control_type="stdString",
                            bounds=(0, 0, 17, 40), is_indicator=False),
            addr,
        ],
    )
    src = build_panel_module(
        ParsedFrontPanel(controls=[arr], panel_bounds=(0, 0, 200, 300)),
        logic_module_stem="f", logic_func_name="f",
        param_names=["people"], result_fields=None,
    )
    assert "ArrayField('addr', 'addr', 'stdClust', fields=[" in src
    assert "ArrayField('street', 'street', 'stdString')" in src
    compile(src, "<panel>", "exec")


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
    # The State view-model is inlined (no separate state module), and the file
    # serves itself via a guarded runner.
    assert "@binding.bindable_dataclass" in src
    assert "class State:" in src
    assert "from state import" not in src
    assert 'if __name__ in {"__main__", "__mp_main__"}:' in src
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
