"""Tests for lvkit diff — comparing two VI versions."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import diff_structured, diff_text

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
