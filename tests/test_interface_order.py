"""Canonical connector-pane ordering + the signature facts it exposes.

Covers the maintainer-defined order (``graph.interface_order``): per direction
group, error cluster last, then requirement level (Required -> Recommended ->
Optional), then pane geometry (inputs column-major left->right, outputs
right->left). Also covers the facts surfaced from it -- ``describe``'s terse/
verbose connector-pane lines and the netlist ``connector_pane`` facet.
"""

from __future__ import annotations

from lvkit.graph.describe import _default_suffix, _pane_terminal_line
from lvkit.graph.diff import _pane_terminal_detail
from lvkit.graph.interface_order import (
    is_required,
    ordered_interface,
    requirement_rank,
)
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.parser.models import ParsedWiringRule as W

_DOMINANT = 4815  # 4-2-2-4 pane: leftmost col = idx 11,10,9,8; rightmost = 3,2,1,0


def _term(
    name: str,
    *,
    index: int,
    rule: W = W.RECOMMENDED,
    direction: str = "input",
    default: object = None,
    error: bool = False,
    type_name: str = "I32",
) -> Terminal:
    lv = (
        LVType(kind=LVTypeKind.TYPEDEF_REF, typedef_name="Error Cluster")
        if error
        else LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=type_name)
    )
    return Terminal(
        id=name,
        name=name,
        direction=direction,
        index=index,
        wiring_rule=int(rule),
        default_value=default,  # type: ignore[arg-type]
        lv_type=lv,
    )


def _names(terms: list[Terminal]) -> list[str]:
    return [t.name or "" for t in terms]


# --- requirement rank / is_required -------------------------------------


def test_requirement_rank_three_levels_inputs():
    assert requirement_rank(_term("a", index=0, rule=W.REQUIRED), "input") == 0
    assert requirement_rank(_term("b", index=0, rule=W.RECOMMENDED), "input") == 1
    assert requirement_rank(_term("c", index=0, rule=W.OPTIONAL), "input") == 2
    # Unresolved wiring rule defaults to Recommended, never below a real Optional.
    assert requirement_rank(_term("d", index=0, rule=W.INVALID), "input") == 1


def test_output_is_never_required():
    # LabVIEW lets you SET Required on an output, but it can't compel a caller,
    # so an output is never ranked Required and is never marked ``(required)``.
    out = _term("x", index=0, rule=W.REQUIRED, direction="output")
    assert requirement_rank(out, "output") == 1
    assert is_required(out, "output") is False


def test_dynamic_dispatch_folds_by_direction():
    dd_in = _term("self", index=0, rule=W.DYNAMIC_DISPATCH, direction="input")
    dd_out = _term("self", index=0, rule=W.DYNAMIC_DISPATCH, direction="output")
    assert is_required(dd_in, "input") is True
    assert is_required(dd_out, "output") is False


# --- ordering: disposition + error-last ---------------------------------


def test_disposition_is_outer_key():
    # Given in a deliberately scrambled order; expect Required, Recommended,
    # Optional -- a full pass by disposition regardless of slot index.
    terms = [
        _term("opt", index=0, rule=W.OPTIONAL),
        _term("req", index=1, rule=W.REQUIRED),
        _term("rec", index=2, rule=W.RECOMMENDED),
    ]
    assert _names(ordered_interface(terms, "input", None)) == ["req", "rec", "opt"]


def test_error_cluster_sorts_last_even_if_required():
    terms = [
        _term("err", index=1, rule=W.REQUIRED, error=True),
        _term("data", index=0, rule=W.OPTIONAL),
    ]
    # Error last beats disposition: the Optional non-error still precedes the
    # (nominally Required) error cluster.
    assert _names(ordered_interface(terms, "input", None)) == ["data", "err"]


# --- ordering: geometry --------------------------------------------------


def test_geometry_inputs_left_to_right():
    # idx 11 is the leftmost column, idx 0 the rightmost. Inputs read L->R.
    terms = [_term("right", index=0), _term("left", index=11)]
    assert _names(ordered_interface(terms, "input", _DOMINANT)) == ["left", "right"]


def test_geometry_outputs_right_to_left():
    terms = [_term("right", index=0), _term("left", index=11)]
    assert _names(ordered_interface(terms, "output", _DOMINANT)) == ["right", "left"]


def test_no_pattern_falls_back_to_slot_index():
    terms = [_term("b", index=5), _term("a", index=2)]
    assert _names(ordered_interface(terms, "input", None)) == ["a", "b"]


def test_ordering_is_deterministic():
    terms = [
        _term("err", index=8, error=True),
        _term("right", index=0, rule=W.OPTIONAL),
        _term("left_req", index=11, rule=W.REQUIRED),
        _term("mid", index=6),
    ]
    once = _names(ordered_interface(terms, "input", _DOMINANT))
    twice = _names(ordered_interface(list(reversed(terms)), "input", _DOMINANT))
    assert once == twice
    # Required first, then Recommended, then Optional, error last.
    assert once == ["left_req", "mid", "right", "err"]


# --- describe: terse/verbose connector-pane lines -----------------------


def test_default_suffix():
    assert _default_suffix(None) == ""
    assert _default_suffix(",") == ' = ","'
    assert _default_suffix(0) == " = 0"
    assert _default_suffix(True) == " = True"


def test_pane_terminal_line_annotates_only_exceptions():
    rec = _term("delimiter", index=9, rule=W.RECOMMENDED, default=",")
    # Recommended is the unmarked baseline; only the default annotates.
    assert _pane_terminal_line(rec, "input", verbose=False) == 'delimiter: I32 = ","'
    # Verbose appends the pane slot.
    verbose = _pane_terminal_line(rec, "input", verbose=True)
    assert verbose == 'delimiter: I32 = "," [idx 9]'


def test_pane_terminal_line_marks_required():
    req = _term("count", index=11, rule=W.REQUIRED)
    assert _pane_terminal_line(req, "input", verbose=False) == "count: I32 (required)"


# --- diff: authored connector-pane contract changes ---------------------


def test_pane_diff_ignores_pure_reorder():
    # Same terminal, only its slot-index UNCHANGED but position in the list
    # differs -> _pane_terminal_detail sees identical facets, so no change.
    t = _term("x", index=5, rule=W.RECOMMENDED, default="a")
    assert _pane_terminal_detail(t, t) is None


def test_pane_diff_disposition_change():
    a = _term("x", index=5, rule=W.RECOMMENDED)
    b = _term("x", index=5, rule=W.OPTIONAL)
    assert _pane_terminal_detail(a, b) == "Recommended → Optional"


def test_pane_diff_combines_facets():
    a = _term("x", index=5, rule=W.RECOMMENDED, default=None, type_name="I32")
    b = _term("x", index=7, rule=W.REQUIRED, default=3, type_name="DBL")
    detail = _pane_terminal_detail(a, b)
    assert detail is not None
    # type, disposition, default, slot -- all four, joined.
    assert "I32 → DBL" in detail
    assert "Recommended → Required" in detail
    assert "default None → default 3" in detail
    assert "slot 5 → slot 7" in detail
