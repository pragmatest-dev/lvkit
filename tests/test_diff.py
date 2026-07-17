"""Tests for lvkit diff — comparing two VI versions."""

from __future__ import annotations

import subprocess
from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import diff_structured, diff_text, diff_uid
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import Constant
from lvkit.models import (
    CaseFrame,
    CaseOperation,
    LVType,
    SelectorRange,
    SequenceFrame,
    SequenceOperation,
)
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
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=layout)
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
    def __init__(
        self,
        constants: list[Constant],
        layout: Layout | None = None,
        operations: list | None = None,
    ):
        self._constants = constants
        self._layout = layout
        self._operations = operations if operations is not None else []

    def resolve_vi_name(self, vi_name: str) -> str:
        return vi_name

    def get_operations(self, _vi: str) -> list:
        return self._operations

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
        # lookups (bounds/bounds_before) -- exercise that against real geometry,
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
        # after bounds for the after-pane, before bounds for the before-pane
        assert c.bounds == (1.0, 2.0, 3.0, 4.0)
        assert c.bounds_before == (5.0, 6.0, 7.0, 8.0)

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


# ── Locality stamping: container_uid / frame_path ───────────────────────
#
# Every ElementChange records WHERE it lives: the innermost enclosing Case/
# stacked-Sequence structure (container_uid) and the full frame-addressing
# chain (frame_path), formatted exactly like render/draw.py's
# ``encode_frame_path`` token baked into the SVG's ``<g class="lv-frame"
# data-path="...">`` attribute, so a downstream viewer can correlate a change
# straight onto the rendered frame group with no reconciliation.


class TestLocalityStamping:
    def test_node_inside_added_case_carries_its_container(self):
        # run_base/run_head (see TestWireChanges' docstring for the full
        # anatomy): head wraps several existing nodes in a NEW case structure
        # (uid 3870). Node 4117 ("addSkipped.vi") is a genuinely NEW node
        # placed INSIDE that new case.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"), layout=True)
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"), layout=True)
        cmap = diff_uid(ga, gb, na, nb)

        by_uid = {c.uid: c for c in cmap.changes if c.kind == "node"}
        inside_new_case = by_uid["4117"]
        assert inside_new_case.container_uid == "3870"
        assert inside_new_case.frame_path is not None
        assert inside_new_case.frame_path.endswith("3870=True")

        # A DIFFERENT added node (1065) sits in an existing, UNRELATED case
        # (753) -- not the new one -- proving container_uid is the actual
        # innermost enclosing structure, not just "some truthy value".
        elsewhere = by_uid["1065"]
        assert elsewhere.container_uid == "753"
        assert elsewhere.container_uid != inside_new_case.container_uid

    def test_top_level_change_has_no_container(self):
        # run_base/run_head happens to have every change nested inside some
        # case, so the "top-level" side of this property needs a different
        # real fixture: VI_A/VI_B (used throughout this file) diffs almost
        # entirely at the top level. "Tick Count (ms)" (509) is added at the
        # very top of VI_B's diagram, outside any structure.
        ga, na = _load(VI_A, layout=True)
        gb, nb = _load(VI_B, layout=True)
        cmap = diff_uid(ga, gb, na, nb)

        by_uid = {c.uid: c for c in cmap.changes if c.kind == "node"}
        top_level = by_uid["509"]
        assert top_level.label == "Tick Count (ms)"
        assert top_level.container_uid is None
        assert top_level.frame_path is None

    def test_to_dict_serializes_locality_fields(self):
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"), layout=True)
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"), layout=True)
        d = diff_uid(ga, gb, na, nb).to_dict()

        entry = next(c for c in d["changes"] if c["uid"] == "4117")
        assert entry["container_uid"] == "3870"
        assert entry["frame_path"] is not None


# ── Frame set diff: added/removed/value-changed frames ──────────────────
#
# We cannot author .vi files (no LabVIEW), so these drive a synthetic
# CaseOperation/SequenceOperation straight through diff_uid via a stub graph
# -- the lightest-weight route, mirroring TestModifiedConstant's _StubGraph
# pattern above.


class TestFrameSetChanges:
    def test_case_structure_frame_added_removed_and_value_changed(self):
        # One CaseOperation (uid "500"), matched across versions by its own
        # (common) uid, whose FRAME SET differs by exactly:
        #   - frame "1": base-only -> REMOVED
        #   - frame "2": present both sides, key-matched by FALLBACK (its
        #     CaseFrame.uid is None -- true of every real corpus case frame
        #     today, see _frame_key's docstring); selector_ranges gained a
        #     value (LabVIEW's "add value to this case") -> VALUE changed,
        #     "2 → 2, 3".
        #   - frame uid="900": present both sides, matched by its OWN real
        #     uid (LabVIEW's "make this the default case" -- selector_value
        #     AND is_default change while the frame's identity is preserved)
        #     -> VALUE changed, "2 → Default".
        #   - frame "4": head-only -> ADDED
        case_a = CaseOperation(
            id="vi::500", name="Case", labels=["CaseStructure"],
            node_type="caseStructure",
            frames=[
                CaseFrame(selector_value="1"),
                CaseFrame(
                    selector_value="2",
                    selector_ranges=[SelectorRange(start=2, end=2)],
                ),
                CaseFrame(uid="900", selector_value="2", is_default=False),
            ],
        )
        case_b = CaseOperation(
            id="vi::500", name="Case", labels=["CaseStructure"],
            node_type="caseStructure",
            frames=[
                CaseFrame(
                    selector_value="2",
                    selector_ranges=[
                        SelectorRange(start=2, end=2),
                        SelectorRange(start=3, end=3),
                    ],
                ),
                CaseFrame(uid="900", selector_value="Default", is_default=True),
                CaseFrame(selector_value="4"),
            ],
        )
        ga = _StubGraph([], operations=[case_a])
        gb = _StubGraph([], operations=[case_b])
        cmap = diff_uid(ga, gb, "vi", "vi")

        frame_changes = {
            c.uid: c for c in cmap.changes if c.kind in ("frame", "value")
        }
        assert set(frame_changes) == {"~1", "~2", "900", "~4"}

        removed = frame_changes["~1"]
        assert removed.kind == "frame"
        assert removed.change == "removed"
        assert removed.label == "1"
        assert removed.container_uid == "500"
        assert removed.frame_path == "500=1"

        added = frame_changes["~4"]
        assert added.kind == "frame"
        assert added.change == "added"
        assert added.label == "4"
        assert added.container_uid == "500"
        assert added.frame_path == "500=4"

        value_by_fallback = frame_changes["~2"]
        assert value_by_fallback.kind == "value"
        assert value_by_fallback.change == "modified"
        assert value_by_fallback.detail == "2 → 2, 3"
        assert value_by_fallback.container_uid == "500"
        assert value_by_fallback.frame_path == "500=2"

        value_by_uid = frame_changes["900"]
        assert value_by_uid.kind == "value"
        assert value_by_uid.change == "modified"
        assert value_by_uid.detail == "2 → Default"
        assert value_by_uid.container_uid == "500"
        assert value_by_uid.frame_path == "500=Default"

    def test_unchanged_case_frame_set_has_no_frame_changes(self):
        case_a = CaseOperation(
            id="vi::500", name="Case", labels=["CaseStructure"],
            node_type="caseStructure",
            frames=[CaseFrame(selector_value="1"), CaseFrame(selector_value="2")],
        )
        case_b = CaseOperation(
            id="vi::500", name="Case", labels=["CaseStructure"],
            node_type="caseStructure",
            frames=[CaseFrame(selector_value="1"), CaseFrame(selector_value="2")],
        )
        ga = _StubGraph([], operations=[case_a])
        gb = _StubGraph([], operations=[case_b])
        cmap = diff_uid(ga, gb, "vi", "vi")
        assert [c for c in cmap.changes if c.kind in ("frame", "value")] == []

    def test_stacked_sequence_frame_reorder_by_real_uid(self):
        # SequenceFrame DOES get a real, stable uid from the parser (see
        # parser/nodes/sequence.py) -- unlike CaseFrame. A stacked sequence
        # (node_type != "flatSequence") is INTERACTIVE (render/scene.py wraps
        # it in a togglable lv-frame group), so its frame changes extend the
        # frame_path with their own segment.
        seq_a = SequenceOperation(
            id="vi::700", name="Sequence", labels=["StackedSequence"],
            node_type="stackedSequence",
            frames=[
                SequenceFrame(uid="10", index=0),
                SequenceFrame(uid="11", index=1),
            ],
        )
        seq_b = SequenceOperation(
            id="vi::700", name="Sequence", labels=["StackedSequence"],
            node_type="stackedSequence",
            frames=[
                SequenceFrame(uid="11", index=0),
                SequenceFrame(uid="12", index=1),
            ],
        )
        ga = _StubGraph([], operations=[seq_a])
        gb = _StubGraph([], operations=[seq_b])
        cmap = diff_uid(ga, gb, "vi", "vi")

        frame_changes = {
            c.uid: c for c in cmap.changes if c.kind in ("frame", "value")
        }
        assert set(frame_changes) == {"10", "11", "12"}
        assert frame_changes["10"].kind == "frame"
        assert frame_changes["10"].change == "removed"
        assert frame_changes["12"].kind == "frame"
        assert frame_changes["12"].change == "added"

        reordered = frame_changes["11"]
        assert reordered.kind == "value"
        assert reordered.change == "modified"
        assert reordered.detail == "1 → 0"
        assert reordered.container_uid == "700"
        assert reordered.frame_path == "700=0"

    def test_flat_sequence_frame_change_gets_no_new_frame_path_segment(self):
        # A FLAT sequence shows every frame simultaneously (film-strip) --
        # never hidden -- so render/scene.py never wraps it in a togglable
        # lv-frame group (_is_interactive_struct is False for it). Its frame
        # changes still get a real container_uid (the structure exists) but
        # must NOT gain a frame_path segment nothing in the SVG would match.
        seq_a = SequenceOperation(
            id="vi::800", name="Flat Sequence", labels=["FlatSequence"],
            node_type="flatSequence",
            frames=[SequenceFrame(uid="20", index=0)],
        )
        seq_b = SequenceOperation(
            id="vi::800", name="Flat Sequence", labels=["FlatSequence"],
            node_type="flatSequence",
            frames=[
                SequenceFrame(uid="20", index=0),
                SequenceFrame(uid="21", index=1),
            ],
        )
        ga = _StubGraph([], operations=[seq_a])
        gb = _StubGraph([], operations=[seq_b])
        cmap = diff_uid(ga, gb, "vi", "vi")

        frame_changes = [c for c in cmap.changes if c.kind in ("frame", "value")]
        assert len(frame_changes) == 1
        added = frame_changes[0]
        assert added.uid == "21"
        assert added.change == "added"
        assert added.container_uid == "800"
        assert added.frame_path is None  # non-interactive: no new segment


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
        # deleted wire -> both anchors come from the BEFORE layout: the sink it
        # used to reach (bounds) and the source it lost (bounds_before).
        assert change.bounds is not None
        assert change.bounds_before is not None

    def test_run_vi_removed_wire_has_faithful_path(self):
        # Increment 2a: the removed wire carries the FAITHFUL drawn polyline
        # (source center -> recorded bends -> sink center), from the BEFORE
        # layout (the version the removed wire lives in). path_before is None
        # (only "modified" wires carry an old-routing polyline).
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"), layout=True)
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"), layout=True)
        change = [c for c in diff_uid(ga, gb, na, nb).changes
                  if c.kind == "wire"][0]

        assert change.path is not None
        assert len(change.path) >= 2
        # exact faithful routing for sink 1820 (verified via wire_by_uid):
        # source center -> two bends -> sink center.
        assert change.path == [
            (1430.0, 507.5), (1414.0, 507.5), (1414.0, 707.0), (1906.5, 707.0),
        ]
        assert change.path[0] == (1430.0, 507.5)   # source-terminal center
        assert change.path[-1] == (1906.5, 707.0)  # sink-terminal center
        assert change.path_before is None

        # the new geometry survives JSON-ready serialization (lists, not tuples)
        d = diff_uid(ga, gb, na, nb).to_dict()
        wire_dict = next(c for c in d["changes"] if c["uid"] == "1820")
        assert wire_dict["path"] == [
            [1430.0, 507.5], [1414.0, 507.5], [1414.0, 707.0], [1906.5, 707.0],
        ]
        assert wire_dict["path_before"] is None

    def test_run_vi_added_node_has_chain_paths(self):
        # Increment 2a: an added node carries its wire "chain" — the polylines
        # of every wire incident to it — so the viewer draws them in its color.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"), layout=True)
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"), layout=True)
        cmap = diff_uid(ga, gb, na, nb)

        added_nodes = [c for c in cmap.changes
                       if c.kind == "node" and c.change == "added"]
        assert added_nodes, "expected added nodes on the run.vi pair"
        with_chains = [c for c in added_nodes if c.chain_paths]
        assert with_chains, "at least one added node must carry chain_paths"
        for c in with_chains:
            assert c.chain_paths is not None
            for poly in c.chain_paths:
                assert len(poly) >= 2  # every chain wire is a real polyline

    def test_no_layout_means_no_paths(self):
        # Without layout=True there is no geometry, so no path/chain overlay.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        cmap = diff_uid(ga, gb, na, nb)
        for c in cmap.changes:
            assert c.path is None
            assert c.path_before is None
            assert c.chain_paths is None


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
