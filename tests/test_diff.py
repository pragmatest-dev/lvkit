"""Tests for lvkit diff — comparing two VI versions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import diff_uid, format_diff
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import Constant, VIContext
from lvkit.models import (
    CaseFrame,
    CaseOperation,
    LVType,
    PrimitiveOperation,
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
# Every test here diffs real VIs from the local-only corpus.
pytestmark = pytest.mark.needs_samples

VI_A = Path(".lvkit/cache/samples/lv-flex-channel-examples/DAQmx AO/DAQ AO.vi")
VI_B = Path(
    ".lvkit/cache/samples/JKI-EasyXML/Source/Fast Parser/"
    "test TCX read (installed 71).vi"
)


def _load(vi_path: Path, *, layout: bool = False) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=layout)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _depth(line: str) -> int:
    """Nesting depth of one netlist-diff line: gutter is column 0
    (``f"{g} {'  '*depth}{content}"``), so depth is the leading-space count
    AFTER the "<gutter><space>" prefix, halved (2 spaces/level)."""
    rest = line[2:]
    return (len(rest) - len(rest.lstrip(" "))) // 2


# ── Diff TEXT report: ONE recursive netlist-form tree, concise default +
# --verbose (both project the SAME UID-keyed ChangeMap AND the SAME
# containment tree -- see diff.py's ``format_diff``/``_netlist_diff``)
# ──────────────────────────────────────────────────────────────────────


class TestFormatDiffConcise:
    """The default (non-verbose) tier: the recursive composition tree,
    changes only -- no Signature section, no unchanged-node tally."""

    def test_identical_vi_produces_empty_report(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_A)
        assert format_diff(ga, gb, na, nb) == ""

    def test_different_vis_detect_operation_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = format_diff(ga, gb, na, nb)
        assert result != ""
        # VI_A has DAQmx Write.vi, VI_B doesn't.
        assert "DAQmx Write.vi" in result

    def test_different_vis_detect_structure_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = format_diff(ga, gb, na, nb)
        # VI_A has a While Loop, VI_B has a Flat Sequence -- structures render
        # as netlist scope headers (locked syntax), not the diagram's own name.
        assert "sequence:" in result or "while (" in result

    def test_concise_omits_signature_section(self):
        # Signature is a --verbose-only depth add (a distinct concern from
        # the UID-keyed ChangeMap -- see format_diff's docstring).
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = format_diff(ga, gb, na, nb)
        assert "Signature:" not in result

    def test_concise_omits_unchanged_node_tally(self):
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        result = format_diff(ga, gb, na, nb)
        assert "unchanged" not in result

    def test_concise_is_already_the_full_tree(self):
        # The depth axis (--verbose) no longer gates NESTING -- both tiers
        # project the SAME netlist-diff tree (see format_diff's docstring).
        # run_base/run_head wraps several existing nodes in a NEW case (uid
        # 3870, node_type "select" -- a structure must never be named after
        # a node nested in its own frame, see SelectHandler) containing the
        # real subVI call "addSkipped.vi" (node 4117). The new case's
        # selector is itself fed THROUGH its own input tunnel from a
        # sibling "Bundle/Unbundle By Name" node -- _resolve_source hops
        # through the tunnel to that real producer instead of naming the
        # case after whatever it finds sitting at the tunnel terminal.
        # scope_header line, a nested frame sub-header for "True", and the
        # new node's instance line beneath it, all in the DEFAULT report.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        result = format_diff(ga, gb, na, nb)
        assert "+ " in result
        assert "case (Bundle/Unbundle By Name#2" in result
        assert '"True":' in result
        assert "addSkipped.vi" in result.split('"True":', 1)[1]
        # nested one level deeper than the structure's own header line
        lines = result.splitlines()
        struct_i = next(
            i for i, ln in enumerate(lines)
            if ln.startswith("+ ") and "case (Bundle/Unbundle By Name#2" in ln
        )
        frame_i = next(
            i for i, ln in enumerate(lines[struct_i:], struct_i) if '"True":' in ln
        )
        node_i = next(
            i for i, ln in enumerate(lines[frame_i:], frame_i)
            if "addSkipped.vi" in ln and i != struct_i
        )
        assert _depth(lines[struct_i]) < _depth(lines[frame_i]) < _depth(
            lines[node_i]
        )

    def test_unchanged_container_gets_no_header_but_still_nests(self):
        # The OTHER added node (1065) sits in an UNCHANGED case (753) --
        # unlike case 3870 above, 753's own scope_header line has a SPACE
        # gutter (it didn't change itself), but its frame still docks 1065
        # under a quoted frame sub-header rather than showing it flat.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        cmap = diff_uid(ga, gb, na, nb)
        by_uid = {c.uid: c for c in cmap.changes if c.kind == "node"}
        assert by_uid["1065"].container_uid == "753"
        result = format_diff(ga, gb, na, nb)
        assert "+ " in result
        assert "Bundle/Unbundle By Name#2" in result
        lines = result.splitlines()
        node_i = next(
            i for i, ln in enumerate(lines)
            if ln.startswith("+ ") and "Bundle/Unbundle By Name#2" in ln
        )
        assert node_i > 0
        # its line is indented under SOME quoted frame header above it
        assert any('":' in lines[j] for j in range(node_i))
        # 753's own case header is SPACE-gutter context, not a +/-/~ change
        struct_i = next(
            i for i, ln in enumerate(lines) if "case (Bundle/Unbundle By Name#1" in ln
        )
        assert lines[struct_i].startswith("  ")  # space gutter, not +/-/~

    def test_gutter_tag_applies_to_both_struct_and_node_lines(self):
        # The "+"/"-"/"~" gutter is orthogonal to WHAT changed (a structure's
        # scope_header vs. a node's instance_line) -- the same tag prefixes
        # both kinds of content.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        result = format_diff(ga, gb, na, nb)
        assert "+ " in result
        assert "case (Bundle/Unbundle By Name#2" in result
        assert "addSkipped.vi#2" in result
        lines = result.splitlines()
        struct_line = next(
            ln for ln in lines
            if ln.startswith("+ ") and "case (Bundle/Unbundle By Name#2" in ln
        )
        node_line = next(ln for ln in lines if "addSkipped.vi#2" in ln)
        assert struct_line.startswith("+ ")
        assert node_line.startswith("+ ")

    def test_removed_wire_not_suppressed_by_sibling_added_node_change(self):
        # The removed wire's sink shares a containment path with an added
        # "Bundle/Unbundle By Name" node, but the two are DIFFERENT changes
        # (removed vs. added) -- a deletion must always show, even when an
        # unrelated addition happens to live at the same path. Only a wire
        # sharing its OWN change value (added wire + added node) is
        # redundant with that node's instance_line and gets suppressed.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        wire_line = next(ln for ln in lines if "x = Bundle/Unbundle" in ln)
        assert wire_line.startswith("- ")

    def test_no_section_headers(self):
        # #31: no Operations:/Wiring:/Structures: sections -- every change
        # reads inline in the ONE composition tree.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        result = format_diff(ga, gb, na, nb)
        assert "Operations:" not in result
        assert "Wiring:" not in result
        assert "Structures:" not in result


class TestFormatDiffVerbose:
    """--verbose: the SAME composition tree, PLUS a Signature section,
    modified-value old->new detail, and a trailing unchanged-node tally --
    same ChangeMap, more depth, never a different shape."""

    def test_identical_vi_produces_empty_report(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_A)
        assert format_diff(ga, gb, na, nb, verbose=True) == ""

    def test_different_vis_detect_operation_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = format_diff(ga, gb, na, nb, verbose=True)
        assert "DAQmx Write.vi" in result

    def test_different_vis_detect_structure_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = format_diff(ga, gb, na, nb, verbose=True)
        assert "sequence:" in result or "while (" in result

    def test_different_vis_detect_signature_changes(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = format_diff(ga, gb, na, nb, verbose=True)
        # VI_B has a 'Tree' input that VI_A doesn't.
        assert "Signature:" in result
        assert "+ input: Tree" in result

    def test_format_produces_readable_output(self):
        ga, na = _load(VI_A)
        gb, nb = _load(VI_B)
        result = format_diff(ga, gb, na, nb, verbose=True)
        assert "Signature:" in result
        assert "DAQmx Write.vi" in result

    def test_verbose_tree_matches_concise_tree(self):
        # The node/structure/wire tree is IDENTICAL between tiers. Verbose adds
        # Signature (before) and the unchanged-node tally (after), and gives each
        # CONSTANT row its value -- concise omits constant values so a long
        # string can't bloat a row (the value still lives in the JSON and the
        # diagram panes). This pair has exactly one (added, float 0.0) constant.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        concise = format_diff(ga, gb, na, nb)
        verbose = format_diff(ga, gb, na, nb, verbose=True)
        common = len(diff_uid(ga, gb, na, nb).common_node_uids)
        assert "○ float constant" in concise
        expected = (
            concise.replace("○ float constant", "○ float constant = 0.0")
            + f"\n\n({common} unchanged nodes)"
        )
        assert verbose == expected

    def test_unchanged_node_tally_present(self):
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        cmap = diff_uid(ga, gb, na, nb)
        result = format_diff(ga, gb, na, nb, verbose=True)
        assert f"({len(cmap.common_node_uids)} unchanged nodes)" in result

    def test_no_duplicated_wiring_lines(self):
        # The one genuine REMOVED wire for this pair (see TestWireChanges)
        # still renders exactly once, as its own "- " deletion line -- it is
        # not duplicated by, nor hidden behind, the added sibling node's own
        # instance_line at the same containment path (see
        # test_removed_wire_not_suppressed_by_sibling_added_node_change).
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"))
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"))
        result = format_diff(ga, gb, na, nb, verbose=True)
        lines = result.splitlines()
        assert sum(1 for ln in lines if "x = Bundle/Unbundle" in ln) == 1
        wire_line = next(ln for ln in lines if "x = Bundle/Unbundle" in ln)
        assert wire_line.startswith("- ")


# ── netlist-form TEXT diff on the real JKI VI-Tester run.vi pair ───────
#
# Mirrors tests/test_netlist.py's load-and-skip-if-absent convention: these
# VIs are staged in the scratchpad, not the repo's sample corpus, so the
# tests degrade gracefully when they're not present in a given environment.

_SCRATCHPAD = Path(
    "/tmp/claude-1000/-home-ryanf-repos-lvkit/3a7f874f-386b-432d-9712-edf3bc6c995e"
    "/scratchpad"
)
_RUN_OLD = _SCRATCHPAD / "run_OLD.vi"
_RUN_NEW = _SCRATCHPAD / "run_NEW.vi"
_DEMO_SEARCH_PATH = Path(__file__).resolve().parent.parent / ".tmp" / "vi-tester-demo"


def _load_jki(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[_DEMO_SEARCH_PATH], layout=False)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _require_jki_vis() -> None:
    if not _RUN_OLD.exists() or not _RUN_NEW.exists():
        pytest.skip("JKI run_OLD.vi/run_NEW.vi pair not staged in scratchpad")


class TestNetlistFormDiffOnJKIPair:
    """The netlist-form TEXT diff (Phase 2) exercised against a real,
    non-trivial VI pair -- proves the format on genuine data, not just the
    synthetic run_base/run_head fixture above."""

    def test_default_report(self):
        _require_jki_vis()
        ga, na = _load_jki(_RUN_OLD)
        gb, nb = _load_jki(_RUN_NEW)
        result = format_diff(ga, gb, na, nb)

        assert "case (" in result
        assert any(ln.startswith("+ ") for ln in result.splitlines())
        assert "addSkipped" in result
        assert result.isascii()
        assert "⬚" not in result
        assert "◻" not in result
        assert "↔" not in result
        # The removed wire (base uid 1820) must always show as a deletion,
        # even though it shares a containment path with an added node --
        # a deletion is never suppressed by an unrelated addition. Its
        # source is an nMux (Bundle/Unbundle By Name) output, so the label
        # is the resolved FIELD net (``isSkipped``, via
        # ``Terminal.nmux_field_index``/``op_walk._nmux_raw_field_name``),
        # not the generic node name.
        wire_line = next(ln for ln in result.splitlines() if "x = isSkipped" in ln)
        assert wire_line.startswith("- ")

    def test_verbose_report(self):
        _require_jki_vis()
        ga, na = _load_jki(_RUN_OLD)
        gb, nb = _load_jki(_RUN_NEW)
        result = format_diff(ga, gb, na, nb, verbose=True)

        assert "case (" in result
        assert any(ln.startswith("+ ") for ln in result.splitlines())
        assert "addSkipped" in result
        assert result.isascii()
        assert "⬚" not in result
        assert "◻" not in result
        assert "↔" not in result
        wire_line = next(ln for ln in result.splitlines() if "x = isSkipped" in ln)
        assert wire_line.startswith("- ")

    def test_deterministic_across_hash_seeds(self):
        _require_jki_vis()

        script = (
            "import hashlib\n"
            "from pathlib import Path\n"
            "from lvkit.graph.core import InMemoryVIGraph\n"
            "from lvkit.graph.diff import format_diff\n"
            "def _load(p):\n"
            "    g = InMemoryVIGraph()\n"
            f"    g.load_vi(str(p), search_paths=[Path({str(_DEMO_SEARCH_PATH)!r})],"
            " layout=False)\n"
            "    return g, g.resolve_vi_name(p.name)\n"
            f"ga, na = _load(Path({str(_RUN_OLD)!r}))\n"
            f"gb, nb = _load(Path({str(_RUN_NEW)!r}))\n"
            "out = format_diff(ga, gb, na, nb, verbose=True)\n"
            "assert out.isascii()\n"
            "print(hashlib.sha256(out.encode()).hexdigest())\n"
        )

        digests = []
        for seed in ("0", "1234567"):
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True, text=True, timeout=60,
            )
            assert result.returncode == 0, result.stderr
            digests.append(result.stdout.strip())
        assert digests[0] == digests[1]


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

    def get_vi_context(self, vi_name: str) -> VIContext:
        # Minimal VIContext — just enough for build_netlist (used by
        # diff.py's tree-order pass, see _reorder_by_tree) to walk this
        # stub's operations/constants without touching a real graph.
        return VIContext(
            name=vi_name,
            inputs=self.get_inputs(vi_name),
            outputs=self.get_outputs(vi_name),
            constants=self.get_constants(vi_name),
            operations=self.get_operations(vi_name),
        )

    def incoming_edges(self, _terminal_id: str) -> list:
        # No wires in this stub (get_wires is always []), so no terminal
        # ever has an incoming edge either.
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
        assert c.kind == "constant"
        assert c.uid == "100"
        # detail carries the old→new transition (JSON + --verbose text); the
        # viewer LIST omits it, but the data keeps it.
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
        # A string value change's detail shows the strings as-is (no repr quote
        # noise). The value lives in detail (JSON + --verbose); the viewer list
        # omits it, and the type label never carries the value.
        ga = _StubGraph([_const("7", "old", "String")])
        gb = _StubGraph([_const("7", "new", "String")])
        c = diff_uid(ga, gb, "vi", "vi").changes[0]
        assert c.change == "modified"
        assert c.kind == "constant"
        assert c.detail == "old → new"
        assert c.label == "str constant"


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

    def test_case_frame_value_rename_is_one_modification_via_content(self):
        # A case frame's selector value changes 1 -> 0 while its CONTENTS stay
        # (a node with a stable UID). CaseFrames carry no per-frame uid and are
        # keyed by value, so "1" and "0" don't key-match -- but they hold the
        # SAME node, so content-overlap pairs them into ONE value modification
        # ("1 -> 0"), NOT remove "1" + add "0". (The reality-grounded MainUI
        # 36:1->0 case; the shared node itself stays unchanged, not reported.)
        node = PrimitiveOperation(
            id="vi::700", name="Add", labels=["Add"], node_type="prim",
        )
        case_a = CaseOperation(
            id="vi::500", name="Case", labels=["CaseStructure"],
            node_type="caseStructure",
            frames=[CaseFrame(selector_value="1", operations=[node])],
        )
        case_b = CaseOperation(
            id="vi::500", name="Case", labels=["CaseStructure"],
            node_type="caseStructure",
            frames=[CaseFrame(selector_value="0", operations=[node])],
        )
        cmap = diff_uid(
            _StubGraph([], operations=[case_a]),
            _StubGraph([], operations=[case_b]), "vi", "vi",
        )
        fc = [c for c in cmap.changes if c.kind in ("frame", "value")]
        assert len(fc) == 1, [(c.kind, c.change, c.label) for c in fc]
        c = fc[0]
        assert (c.kind, c.change) == ("value", "modified")
        assert c.detail == "1 → 0"
        assert c.container_uid == "500"
        # after pane addresses the frame as 500=0; before pane as 500=1 — the
        # viewer drives each pane from its own side so both reveal the frame.
        assert c.frame_path == "500=0"
        assert c.frame_path_before == "500=1"

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
        assert change.detail == "← Bundle/Unbundle By Name"
        # deleted wire -> both anchors come from the BEFORE layout: the sink it
        # used to reach (bounds) and the source it lost (bounds_before).
        assert change.bounds is not None
        assert change.bounds_before is not None

    def test_run_vi_removed_wire_endpoints_translated_to_other_pane(self):
        # The viewer's revealFrame(c) needs to open the case frame in the
        # OTHER (head/after) pane that contains this removed wire's
        # surviving endpoint -- its own frame_path only ever addresses the
        # BASE pane it lives in. ``endpoints`` carries the wire's source +
        # sink node uids, resolved as they'd appear in HEAD's SVG:
        #   - sink (the "Not" node, uid 1781) keeps the SAME uid in both
        #     versions (a common/identical uid needs no translation).
        #   - source (the "Bundle/Unbundle By Name" node, base uid 1489) got
        #     a NEW uid in head (it's fuzzy-matched, not identical), so it
        #     translates through the same exact/fuzzy match map _match_elements
        #     computed for node identity -- to head uid 5207.
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"), layout=True)
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"), layout=True)
        cmap = diff_uid(ga, gb, na, nb)
        change = [c for c in cmap.changes if c.kind == "wire"][0]

        assert change.change == "removed"
        assert change.endpoints == ["5207", "1781"]

        # survives JSON-ready serialization verbatim.
        d = cmap.to_dict()
        wire_dict = next(c for c in d["changes"] if c["uid"] == "1820")
        assert wire_dict["endpoints"] == ["5207", "1781"]

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

    def test_structure_change_has_no_chain_paths(self):
        # #27: chain_paths is a NODE's incident-wire geometry; a structure's
        # "chain" would be every wire nested inside it -- noise in the map and
        # never used for a structure highlight. So a structure change carries
        # None even WITH layout (test_no_layout_means_no_paths only covers the
        # no-geometry case). run_base->run_head adds a Case structure (uid 3870).
        ga, na = _load(Path("outputs/vi-diff/run_base.vi"), layout=True)
        gb, nb = _load(Path("outputs/vi-diff/run_head.vi"), layout=True)
        cmap = diff_uid(ga, gb, na, nb)
        structs = [c for c in cmap.changes if c.kind == "structure"]
        assert structs, "expected a structure change on the run.vi pair"
        for c in structs:
            assert c.chain_paths is None

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

    REPO = ".lvkit/cache/samples/JKI-VI-Tester"
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


class TestDisableStructureInnerChange:
    """A change INSIDE a Diagram/Conditional-Disable structure's frame must be
    reported by the diff, exactly as the netlist renders that body. A disable
    structure carries its body in ``.frames[].operations`` (like a case), so
    ``_collect_elements`` has to recurse frames for it, not just ``inner_nodes``.
    Regression guard for the diff/netlist disagreement fixed by adding
    ``DisableStructureOperation`` to the frame-recursion branch."""

    def _xml(self, extra_inner: str) -> str:
        return f"""<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList>
      <SL__arrayElement class="commentNode" uid="100">
        <termList></termList>
        <diagramList>
          <SL__arrayElement class="diag" uid="110">
            <nodeList>
              <SL__arrayElement class="prim" uid="200">
                <primResID>1</primResID>
                <termList></termList>
              </SL__arrayElement>
              {extra_inner}
            </nodeList>
          </SL__arrayElement>
          <SL__arrayElement class="diag" uid="111">
            <nodeList></nodeList>
          </SL__arrayElement>
        </diagramList>
        <selString class="selLabel">
          <textRec class="textHair"><text>" Disabled "</text></textRec>
        </selString>
        <activeDiag>01</activeDiag>
      </SL__arrayElement>
    </zPlaneList>
  </root>
</SL__rootObject>
"""

    def _load_xml(self, tmp_path: Path, text: str, name: str) -> tuple[
        InMemoryVIGraph, str,
    ]:
        p = tmp_path / f"{name}_BDHb.xml"
        p.write_text(text)
        graph = InMemoryVIGraph()
        graph.load_vi(str(p))
        return graph, graph.list_vis()[0]

    def test_inner_added_node_is_reported(self, tmp_path: Path):
        extra = (
            '<SL__arrayElement class="prim" uid="201">'
            "<primResID>2</primResID><termList></termList></SL__arrayElement>"
        )
        ga, na = self._load_xml(tmp_path, self._xml(""), "DisA")
        gb, nb = self._load_xml(tmp_path, self._xml(extra), "DisB")

        cmap = diff_uid(ga, gb, na, nb)
        added = [c for c in cmap.changes if c.change == "added"]
        # The node lives only in the disable structure's enabled frame; it is
        # collected (and reported) only because the diff recurses disable frames.
        assert any(c.uid == "201" for c in added)
        assert "unknown_primitive_2" in format_diff(ga, gb, na, nb)
