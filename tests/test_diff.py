"""Tests for lvkit diff — comparing two VI versions."""

from __future__ import annotations

import subprocess
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


def _load(vi_path: Path, *, layout: bool = False) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), expand_subvis=False, layout=layout)
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

    def get_wires(self, _vi: str, include_internal: bool = False) -> list:
        return []

    def get_constants(self, _vi: str) -> list[Constant]:
        return self._constants

    def get_layout(self, _vi: str) -> Layout | None:
        return self._layout

    def get_inputs(self, _vi: str, *, public_only: bool = True) -> list:
        return []

    def get_outputs(self, _vi: str, *, public_only: bool = True) -> list:
        return []


def _const(uid: str, value, underlying: str = "NumInt32") -> Constant:
    return Constant(
        id=f"vi::{uid}",
        value=value,
        lv_type=LVType(kind="primitive", underlying_type=underlying),
    )


class TestSelfDiffIsEmpty:
    """Reflexivity invariant: diffing a VI against an independent reload of the
    SAME bytes must yield an empty change-map. A metamorphic guard (no oracle
    needed) against phantom changes, unstable node ordering, and any hashseed
    nondeterminism in the matcher — it holds for every VI, so it's a free
    property test over the corpus. Wire changes (#10) are included: they're
    just entries in the same ``changes`` list, keyed on ``kind == "wire"``."""

    def _selfdiff(self, vi_path: Path, *, layout: bool = False):
        ga, na = _load(vi_path, layout=layout)
        gb, nb = _load(vi_path, layout=layout)  # 2nd, independent parse
        return diff_uid(ga, gb, na, nb)

    def test_daq_vi_self_diff_empty(self):
        assert self._selfdiff(VI_A).changes == []

    def test_easyxml_vi_self_diff_empty(self):
        assert self._selfdiff(VI_B).changes == []

    def test_self_diff_common_nodes_nonempty(self):
        # sanity: it's empty because everything MATCHED, not because nothing
        # was compared.
        cmap = self._selfdiff(VI_A)
        assert cmap.common_node_uids, "self-diff compared nothing — vacuous pass"

    def test_self_diff_with_layout_wires_empty(self):
        # With layout=True the wire-diff path also does terminal-center
        # lookups (bounds/bounds_base) -- exercise that against real geometry,
        # not just the no-layout stub path.
        cmap = self._selfdiff(VI_A, layout=True)
        assert cmap.changes == []
        assert [c for c in cmap.changes if c.kind == "wire"] == []


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


# ── Wire endpoint diff (#10) ────────────────────────────────────────────


class TestWireChanges:
    """run.vi base/head is a real corpus pair (see outputs/vi-diff/) whose
    VALIDATED residual (matching .tmp/probe_wirediff.py's 13->2->1 pressure
    test) is EXACTLY ONE genuine wire change. A "Not" primitive (node 1781)
    has, in base, its input fed by a "Bundle/Unbundle By Name" node (1489,
    matched/unchanged across versions); in head, a NEW "Bundle/Unbundle By
    Name" (1065, genuinely added) feeds it instead. Node-attribution already
    reports 1065 as added, so this is the pure "removed" story of 1781 losing
    its one real (unchanged-node) producer -- anchored on 1781's sole input
    terminal, heap uid 1820 (task #10: "anchor on the sink terminal").

    Everything else is an ENCLOSURE artifact suppressed by the three sieves:
    5 sinks (addError.vi 1314/1317/1326, Clear All Errors 1684, addSuccess.vi
    1821) merely wrapped in the new case 3870, plus a bogus identical-source
    rewire on sink 936 -- all dropped by the containment sieve. If this test
    ever sees > 1 wire change, a sieve has regressed and enclosure noise is
    leaking (founding law: wrapping unchanged nodes in a new structure is NOT
    a change)."""

    def test_run_vi_residual_is_exactly_one_removal(self):
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"), layout=True)
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"), layout=True)
        cmap = diff_uid(ga, gb, na, nb)

        wire_changes = [c for c in cmap.changes if c.kind == "wire"]
        assert len(wire_changes) == 1, (
            f"expected exactly one wire change (enclosure noise must be "
            f"suppressed), got {[(c.uid, c.change, c.detail) for c in wire_changes]}"
        )
        change = wire_changes[0]
        assert change.uid == "1820"
        assert change.kind == "wire"
        assert change.change == "removed"
        assert change.label == "x"
        assert change.detail == "(was ← Bundle/Unbundle By Name)"
        # deleted wire -> both anchors come from the BASE layout: the sink it
        # used to reach (bounds) and the source it lost (bounds_base).
        assert change.bounds is not None
        assert change.bounds_base is not None


class TestConnectorPaneRequalification:
    """The VI's own connector-pane/self node id is the qualified-name STRING
    itself (not "{vi}::{uid}"), which can flip on library requalification
    (e.g. "…lvlib:Foo.vi" -> "Foo.vi"). Naively that breaks cross-version
    identity for every wire touching the connector pane (every FP-terminal
    wire looks like a rewire). Before the fix this real pair floods 8/+8
    phantom wire changes; after the fix (canonicalizing the self node to a
    fixed sentinel), it must be exactly 0."""

    REPO = "samples/JKI-VI-Tester"
    FILE_PATH = "source/Project API/Launch VI Tester.vi"
    BASE_REF = "92be264"
    HEAD_REF = "5bc7205"

    def _extract(self, tmp_path: Path, ref: str, out_name: str) -> Path:
        result = subprocess.run(
            ["git", "-C", self.REPO, "show", f"{ref}:{self.FILE_PATH}"],
            capture_output=True, check=True,
        )
        out = tmp_path / out_name
        out.write_bytes(result.stdout)
        return out

    def test_requalification_produces_zero_wire_changes(self, tmp_path: Path):
        base_vi = self._extract(tmp_path, self.BASE_REF, "cp_base.vi")
        head_vi = self._extract(tmp_path, self.HEAD_REF, "cp_head.vi")
        ga, na = _load(base_vi, layout=True)
        gb, nb = _load(head_vi, layout=True)
        cmap = diff_uid(ga, gb, na, nb)

        wire_changes = [c for c in cmap.changes if c.kind == "wire"]
        assert wire_changes == []
