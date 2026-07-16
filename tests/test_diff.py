"""Tests for lvkit diff — comparing two VI versions."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import diff_structured, diff_text, diff_uid
from lvkit.graph.models import Constant
from lvkit.models import LVType
from lvkit.parser.layout import Layout

# Two structurally different, real permissively-licensed VIs (no relation to
# each other) stand in for the old In.vi/Out.vi pair — genuinely different
# signature, operations, and structures, verified by hand:
#   VI_A: DAQmx AO/DAQ AO.vi (MIT, illuminated-g/lv-flex-channel-examples) —
#       has "DAQmx Write.vi" and a "While Loop" structure.
#   VI_B: JKI-EasyXML's "test TCX read (installed 71).vi" (BSD-3-Clause) —
#       has a "Flat Sequence" structure and adds "Tree"/"tag index"/
#       "child name"/"text" inputs VI_A doesn't have.
VI_A = Path("samples/lv-flex-channel-examples/DAQmx AO/DAQ AO.vi")
VI_B = Path(
    "samples/JKI-EasyXML/Source/Fast Parser/"
    "test TCX read (installed 71).vi"
)


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), expand_subvis=False)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


# ── Text diff ─────────────────────────────────────────────────────────


class TestDiffText:
    def test_identical_vi_produces_empty_diff(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_A)
        result = diff_text(ga, gb, na, nb)
        assert result == ""

    def test_different_vis_produce_nonempty_diff(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = diff_text(
            ga, gb, na, nb, label_a="DAQ AO.vi", label_b="test TCX read.vi"
        )
        assert "---" in result
        assert "+++" in result
        assert "DAQ AO.vi" in result
        assert "test TCX read.vi" in result

    def test_diff_contains_operation_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = diff_text(ga, gb, na, nb)
        # VI_A has DAQmx Write.vi, VI_B doesn't
        assert "Write" in result


# ── Structured diff ──────────────────────────────────────────────────


class TestDiffStructured:
    def test_identical_vi_produces_empty_report(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_A)
        report = diff_structured(ga, gb, na, nb)
        assert report.is_empty()

    def test_different_vis_detect_operation_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        report = diff_structured(ga, gb, na, nb)
        assert not report.is_empty()

        op_names = {c.name for c in report.operations}
        # VI_A has DAQmx Write, VI_B doesn't
        assert "DAQmx Write.vi" in op_names

    def test_different_vis_detect_structure_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        report = diff_structured(ga, gb, na, nb)

        struct_names = {c.name for c in report.structures}
        # VI_A has a While Loop, VI_B has a Flat Sequence
        assert "Flat Sequence" in struct_names or "While Loop" in struct_names

    def test_different_vis_detect_signature_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        report = diff_structured(ga, gb, na, nb)

        # VI_B has a 'Tree' input that VI_A doesn't
        added_inputs = [
            c for c in report.signature
            if c.category == "added" and c.direction == "input"
        ]
        assert any(c.name == "Tree" for c in added_inputs)

    def test_format_produces_readable_output(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        report = diff_structured(ga, gb, na, nb)
        output = report.format()
        assert "Signature:" in output
        assert "Operations:" in output

    def test_empty_report_format(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_A)
        report = diff_structured(ga, gb, na, nb)
        assert report.format() == ""


# ── UID change-map: constant "modified" (#11) ─────────────────────────
#
# A minimal stub graph exposes only the five accessors ``diff_uid`` reads, so we
# can drive the modified-constant path directly — no binary fixture, no licence
# entanglement. Mirrors the real corpus case (JKI-VI-Tester build.vi, a Path
# constant edited in place at a stable UID).


class _StubGraph:
    def __init__(self, constants: list[Constant], layout: Layout | None = None):
        self._constants = constants
        self._layout = layout

    def resolve_vi_name(self, vi_name: str) -> str:
        return vi_name

    def get_operations(self, _vi: str) -> list:
        return []

    def get_wires(self, _vi: str) -> list:
        return []

    def get_constants(self, _vi: str) -> list[Constant]:
        return self._constants

    def get_layout(self, _vi: str) -> Layout | None:
        return self._layout


def _const(uid: str, value, underlying: str = "NumInt32") -> Constant:
    return Constant(
        id=f"vi::{uid}",
        value=value,
        lv_type=LVType(kind="primitive", underlying_type=underlying),
    )


class TestModifiedConstant:
    def test_value_change_at_stable_uid_is_modified(self):
        ga = _StubGraph(
            [_const("100", 5)],
            layout=Layout(node_bounds={"100": (5.0, 6.0, 7.0, 8.0)}),
        )
        gb = _StubGraph(
            [_const("100", 10)],
            layout=Layout(node_bounds={"100": (1.0, 2.0, 3.0, 4.0)}),
        )
        cmap = diff_uid(ga, gb, "vi", "vi")
        assert len(cmap.changes) == 1
        c = cmap.changes[0]
        assert c.change == "modified"
        assert c.uid == "100"
        assert c.detail == "5 → 10"
        # head bounds for the after-pane, base bounds for the before-pane
        assert c.bounds == (1.0, 2.0, 3.0, 4.0)
        assert c.bounds_base == (5.0, 6.0, 7.0, 8.0)

    def test_unchanged_value_is_no_change(self):
        ga = _StubGraph([_const("100", 5)])
        gb = _StubGraph([_const("100", 5)])
        assert diff_uid(ga, gb, "vi", "vi").changes == []

    def test_type_change_is_not_a_modification(self):
        # Same UID but a different type is a LabVIEW UID recycle, not an in-place
        # edit — must NOT be reported as modified.
        ga = _StubGraph([_const("100", 5, "NumInt32")])
        gb = _StubGraph([_const("100", "hi", "String")])
        assert diff_uid(ga, gb, "vi", "vi").changes == []

    def test_string_detail_has_no_repr_quotes(self):
        ga = _StubGraph([_const("7", "old", "String")])
        gb = _StubGraph([_const("7", "new", "String")])
        cmap = diff_uid(ga, gb, "vi", "vi")
        assert cmap.changes[0].detail == "old → new"
