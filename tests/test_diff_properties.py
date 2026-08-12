"""Tests for lvkit diff surfacing VI-level Properties/Structure CHANGES
(#18) -- ``_diff_vi_properties``/``_diff_vi_structure`` in
``lvkit.graph.diff``, wired into ``format_diff`` (text, both tiers) and
``netlist_diff_rows`` (the HTML viewer's Tree).

Real paired VIs differing ONLY in a VI Property are rare, so these tests
load one real VI (twice, into ``graph_a``/``graph_b``) and then mutate the
``_vi_properties``/``_vi_structure`` facets directly -- the same dict
assignment ``graph/loading.py`` itself uses (see
``InMemoryVIGraph._vi_properties[vi_name] = ...``) -- to construct the
scenario deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import diff_to_dict, format_diff, netlist_diff_rows
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import (
    ExecSystem,
    ExecutionProps,
    LockState,
    Priority,
    Reentrancy,
    TypedefStatus,
    VIProperties,
    VIStructure,
)

# Same real, permissively-licensed VI ``test_diff.py`` uses as VI_A -- any
# loadable VI works here since we overwrite its properties/structure facets
# directly rather than relying on real differences between two files.
pytestmark = pytest.mark.needs_samples

VI_PATH = Path(".lvkit/cache/samples/lv-flex-channel-examples/DAQmx AO/DAQ AO.vi")


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=False)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _pair() -> tuple[InMemoryVIGraph, InMemoryVIGraph, str, str]:
    """Load the same real VI twice and reset BOTH sides' Properties/Structure
    facets to clean dataclass defaults -- the real file's own parsed values
    (e.g. this particular VI is ``source_only=True``) would otherwise leak
    into the "baseline" side and produce a spurious diff whenever a test
    overwrites only the OTHER side with a partial ``VIProperties(...)``/
    ``VIStructure(...)`` (unset fields on a fresh dataclass instance revert
    to their defaults). Individual tests then only need to override the
    side(s) relevant to the scenario being tested."""
    ga, na = _load(VI_PATH)
    gb, nb = _load(VI_PATH)
    ga._vi_properties[na] = VIProperties()
    ga._vi_structure[na] = VIStructure()
    gb._vi_properties[nb] = VIProperties()
    gb._vi_structure[nb] = VIStructure()
    return ga, gb, na, nb


class TestPropertiesTextSection:
    def test_lock_state_change_renders_enum_transition(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(lock_state=LockState.PASSWORD_PROTECTED)
        result = format_diff(ga, gb, na, nb)
        assert "Properties:" in result
        assert "~ lock: unlocked -> password_protected" in result

    def test_reentrancy_enum_transition_on(self):
        # Properties/Structure are a FIXED schema -- every VI always has a
        # reentrancy value, so a change renders as a VALUE transition (~),
        # never as an added (+) row -- exactly like lock_state.
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            execution=ExecutionProps(reentrancy=Reentrancy.SHARED_CLONE)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "  ~ reentrancy: non_reentrant -> shared_clone" in lines
        assert "+ reentrancy" not in result
        assert "- reentrancy" not in result

    def test_reentrancy_enum_transition_off(self):
        ga, gb, na, nb = _pair()
        ga._vi_properties[na] = VIProperties(
            execution=ExecutionProps(reentrancy=Reentrancy.PREALLOCATED_CLONE)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "  ~ reentrancy: preallocated_clone -> non_reentrant" in lines
        assert "+ reentrancy" not in result
        assert "- reentrancy" not in result

    def test_priority_and_run_on_open(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            execution=ExecutionProps(
                priority=Priority.SUBROUTINE, run_when_opened=True,
            )
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "  ~ priority: normal -> subroutine" in lines
        assert "  ~ run-on-open: false -> true" in lines

    def test_exec_system_enum_transition(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            execution=ExecutionProps(exec_system=ExecSystem.STANDARD)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "  ~ exec_system: same_as_caller -> standard" in lines

    def test_lv_version_change_is_suppressed(self):
        # lv_version bumps on every save -- pure noise, never diffed.
        ga, gb, na, nb = _pair()
        ga._vi_properties[na] = VIProperties(lv_version="20.0.0")
        gb._vi_properties[nb] = VIProperties(lv_version="21.0.0")
        result = format_diff(ga, gb, na, nb)
        assert result == ""

    def test_vi_type_and_window_cosmetics_are_suppressed(self):
        ga, gb, na, nb = _pair()
        ga._vi_properties[na] = VIProperties(vi_type="Control")
        gb._vi_properties[nb] = VIProperties(vi_type="VI")
        result = format_diff(ga, gb, na, nb)
        assert result == ""

    def test_shown_in_concise_tier_not_just_verbose(self):
        # Unlike Signature (verbose-only), a lock/reentrancy change is
        # high-signal enough to always show.
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(lock_state=LockState.LOCKED)
        concise = format_diff(ga, gb, na, nb)
        verbose = format_diff(ga, gb, na, nb, verbose=True)
        assert "Properties:" in concise
        assert "~ lock: unlocked -> locked" in concise
        assert "Properties:" in verbose
        assert "~ lock: unlocked -> locked" in verbose


class TestStructureTextSection:
    def test_broken_flag_turned_on(self):
        ga, gb, na, nb = _pair()
        gb._vi_structure[nb] = VIStructure(bad_compile=True)
        result = format_diff(ga, gb, na, nb)
        assert "Structure:" in result
        lines = result.splitlines()
        assert "  ~ broken: false -> true" in lines
        assert "+ broken" not in result
        assert "- broken" not in result

    def test_typedef_status_enum_transition(self):
        ga, gb, na, nb = _pair()
        gb._vi_structure[nb] = VIStructure(typedef_status=TypedefStatus.STRICT_TYPEDEF)
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "  ~ typedef_status: not_a_typedef -> strict_typedef" in lines
        assert "+ typedef_status" not in result
        assert "- typedef_status" not in result

    def test_dynamic_dispatch_source_only_and_no_block_diagram_flags(self):
        ga, gb, na, nb = _pair()
        gb._vi_structure[nb] = VIStructure(
            dynamic_dispatch=True, source_only=True, has_no_block_diagram=True,
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "  ~ dynamic-dispatch: false -> true" in lines
        assert "  ~ source-only: false -> true" in lines
        assert "  ~ no-block-diagram: false -> true" in lines

    def test_instance_vi_flag_turned_off(self):
        ga, gb, na, nb = _pair()
        ga._vi_structure[na] = VIStructure(is_instance_vi=True)
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "  ~ instance-vi: true -> false" in lines
        assert "+ instance-vi" not in result
        assert "- instance-vi" not in result

    def test_broken_shown_in_concise_tier_not_just_verbose(self):
        ga, gb, na, nb = _pair()
        gb._vi_structure[nb] = VIStructure(bad_node=True)
        concise = format_diff(ga, gb, na, nb)
        verbose = format_diff(ga, gb, na, nb, verbose=True)
        assert "  ~ broken: false -> true" in concise.splitlines()
        assert "  ~ broken: false -> true" in verbose.splitlines()

    def test_no_plus_minus_gutters_anywhere_in_metadata_sections(self):
        # Every Properties/Structure row is a value transition (~) -- the
        # schema is fixed, so a field can never be "added"/"removed". Flipping
        # several fields at once touches no diagram node, so the WHOLE report
        # is just the Properties:/Structure: sections -- every content line
        # must be a "  ~ " row.
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            lock_state=LockState.LOCKED,
            execution=ExecutionProps(
                reentrancy=Reentrancy.SHARED_CLONE, priority=Priority.SUBROUTINE,
            ),
        )
        gb._vi_structure[nb] = VIStructure(
            bad_compile=True, typedef_status=TypedefStatus.TYPEDEF,
            dynamic_dispatch=True,
        )
        result = format_diff(ga, gb, na, nb)
        for line in result.splitlines():
            if not line or line in ("Properties:", "Structure:"):
                continue
            assert line.startswith("  ~ "), line


class TestNetlistDiffRows:
    def test_property_change_row_present(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(lock_state=LockState.PASSWORD_PROTECTED)
        rows = netlist_diff_rows(ga, gb, na, nb)
        prop_rows = [r for r in rows if r.kind == "property"]
        assert len(prop_rows) == 1
        row = prop_rows[0]
        assert row.change == "modified"
        assert row.depth == 0
        assert row.uid is None
        assert row.text == "lock: unlocked -> password_protected"

    def test_structure_change_row_present(self):
        ga, gb, na, nb = _pair()
        gb._vi_structure[nb] = VIStructure(bad_compile=True)
        rows = netlist_diff_rows(ga, gb, na, nb)
        struct_rows = [r for r in rows if r.kind == "structure"]
        assert len(struct_rows) == 1
        row = struct_rows[0]
        assert row.change == "modified"
        assert row.depth == 0
        assert row.uid is None
        assert row.text == "broken: false -> true"

    def test_no_property_or_structure_rows_when_unchanged(self):
        ga, gb, na, nb = _pair()
        rows = netlist_diff_rows(ga, gb, na, nb)
        assert not [r for r in rows if r.kind in ("property", "structure")]

    def test_no_added_or_removed_property_or_structure_rows(self):
        # Fixed schema -- a property/structure row's ``change`` is ALWAYS
        # "modified", never "added"/"removed" (those tags are reserved for
        # genuine diagram elements that can appear/disappear).
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            lock_state=LockState.LOCKED,
            execution=ExecutionProps(reentrancy=Reentrancy.SHARED_CLONE),
        )
        gb._vi_structure[nb] = VIStructure(
            bad_compile=True, typedef_status=TypedefStatus.TYPEDEF,
        )
        rows = netlist_diff_rows(ga, gb, na, nb)
        meta_rows = [r for r in rows if r.kind in ("property", "structure")]
        assert len(meta_rows) == 4
        assert all(r.change == "modified" for r in meta_rows)


class TestJsonDiff:
    """--format json (diff_to_dict) must carry the same Properties/Structure
    sections the text report does -- they used to be text/viewer-Tree only."""

    def test_diff_to_dict_includes_property_and_structure_changes(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            lock_state=LockState.PASSWORD_PROTECTED
        )
        gb._vi_structure[nb] = VIStructure(bad_node=True)
        d = diff_to_dict(ga, gb, na, nb)
        assert {"changes", "signature", "properties", "structure"} <= set(d)
        assert {
            "field": "lock", "old": "unlocked", "new": "password_protected",
        } in d["properties"]
        assert {"field": "broken", "old": "false", "new": "true"} in d["structure"]

    def test_diff_to_dict_no_metadata_change_is_empty_sections(self):
        ga, gb, na, nb = _pair()
        d = diff_to_dict(ga, gb, na, nb)
        assert d["properties"] == []
        assert d["structure"] == []
