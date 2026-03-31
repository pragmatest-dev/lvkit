"""Tests for vipy diff — comparing two VI versions."""

from __future__ import annotations

from pathlib import Path

from vipy.graph.core import InMemoryVIGraph
from vipy.graph.diff import diff_structured, diff_text

SAMPLES = Path("samples/DAQmx-Digital-IO")


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path))
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


# ── Text diff ─────────────────────────────────────────────────────────


class TestDiffText:
    def test_identical_vi_produces_empty_diff(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "In.vi")
        result = diff_text(ga, gb, na, nb)
        assert result == ""

    def test_different_vis_produce_nonempty_diff(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "Out.vi")
        result = diff_text(ga, gb, na, nb, label_a="In.vi", label_b="Out.vi")
        assert "---" in result
        assert "+++" in result
        assert "In.vi" in result
        assert "Out.vi" in result

    def test_diff_contains_operation_changes(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "Out.vi")
        result = diff_text(ga, gb, na, nb)
        # In.vi has Write, Out.vi has Read
        assert "Write" in result or "Read" in result


# ── Structured diff ──────────────────────────────────────────────────


class TestDiffStructured:
    def test_identical_vi_produces_empty_report(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "In.vi")
        report = diff_structured(ga, gb, na, nb)
        assert report.is_empty()

    def test_different_vis_detect_operation_changes(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "Out.vi")
        report = diff_structured(ga, gb, na, nb)
        assert not report.is_empty()

        op_names = {c.name for c in report.operations}
        # In.vi has DAQmx Write, Out.vi doesn't
        assert "DAQmx Write.vi" in op_names

    def test_different_vis_detect_structure_changes(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "Out.vi")
        report = diff_structured(ga, gb, na, nb)

        struct_names = {c.name for c in report.structures}
        # In.vi has Flat Sequence, Out.vi has While Loop
        assert "Flat Sequence" in struct_names or "While Loop" in struct_names

    def test_different_vis_detect_signature_changes(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "Out.vi")
        report = diff_structured(ga, gb, na, nb)

        # Out.vi has a 'stop' input that In.vi doesn't
        added_inputs = [
            c for c in report.signature
            if c.category == "added" and c.direction == "input"
        ]
        assert any(c.name == "stop" for c in added_inputs)

    def test_format_produces_readable_output(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "Out.vi")
        report = diff_structured(ga, gb, na, nb)
        output = report.format()
        assert "Signature:" in output
        assert "Operations:" in output

    def test_empty_report_format(self):
        ga, na = _load(SAMPLES / "In.vi")
        gb, nb = _load(SAMPLES / "In.vi")
        report = diff_structured(ga, gb, na, nb)
        assert report.format() == ""
