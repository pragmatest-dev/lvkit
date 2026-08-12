"""Tests for lvkit diff surfacing VI-level Properties CHANGES (#18) --
``_diff_vi_properties`` in ``lvkit.graph.diff``, wired into ``format_diff``
(text, both tiers) and ``netlist_diff_rows`` (the HTML viewer's Tree).

Property changes are ordinary ``kind="property"`` ``ElementChange`` leaves
inside the UID-keyed ``ChangeMap`` (a vi-node-properties follow-up that
deleted the old bespoke ``MetadataChange``/separate-section format) --
rendered at the ROOT of the netlist tree, LEADING every node/structure
change, with a synthetic ``uid``/``full_id`` (``"property:{raw_field}"`` --
the RAW ``VIProperties``/``KindProps`` field name, e.g. ``"lock_state"``, NOT
the curated display ``label``), a curated ``label`` (e.g. ``"lock"``), and a
``detail`` old->new transition. There is no more ``Properties:`` text
section, and ``diff_to_dict`` no longer carries a separate top-level
``"properties"`` array -- see ``diff.py``'s ``_mk_metadata_change``.

VI HEALTH (``VIHealth``/``is_broken``) is deliberately NOT diffed at all --
it's an emergent characteristic (like file size), not an authored change.
There is no ``kind="health"``, no ``health:`` uid, and no "broken" row
anywhere in ``format_diff``/``diff_to_dict``/``netlist_diff_rows``. Health
still exists as a graph facet and shows up in ``describe``/the index, just
never in the diff -- see ``TestHealthNeverDiffed`` below and the
diff-philosophy note in ``diff_uid``.

Real paired VIs differing ONLY in a VI Property are rare, so these tests
load one real VI (twice, into ``graph_a``/``graph_b``) and then mutate the
``_vi_properties``/``_vi_health`` facets directly -- the same dict
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
    KindProps,
    LockState,
    Priority,
    Reentrancy,
    TypedefStatus,
    VIHealth,
    VIProperties,
)

# Same real, permissively-licensed VI ``test_diff.py`` uses as VI_A -- any
# loadable VI works here since we overwrite its properties/health facets
# directly rather than relying on real differences between two files.
pytestmark = pytest.mark.needs_samples

VI_PATH = Path(".lvkit/cache/samples/lv-flex-channel-examples/DAQmx AO/DAQ AO.vi")


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=False)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _pair() -> tuple[InMemoryVIGraph, InMemoryVIGraph, str, str]:
    """Load the same real VI twice and reset BOTH sides' Properties/Health
    facets to clean dataclass defaults -- the real file's own parsed values
    (e.g. this particular VI is ``source_only=True``, a ``kind`` field)
    would otherwise leak into the "baseline" side and produce a spurious
    diff whenever a test overwrites only the OTHER side with a partial
    ``VIProperties(...)``/``VIHealth(...)`` (unset fields on a fresh
    dataclass instance revert to their defaults). Individual tests then only
    need to override the side(s) relevant to the scenario being tested."""
    ga, na = _load(VI_PATH)
    gb, nb = _load(VI_PATH)
    ga._vi_properties[na] = VIProperties()
    ga._vi_health[na] = VIHealth()
    gb._vi_properties[nb] = VIProperties()
    gb._vi_health[nb] = VIHealth()
    return ga, gb, na, nb


class TestPropertiesTextSection:
    def test_lock_state_change_renders_enum_transition(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(lock_state=LockState.PASSWORD_PROTECTED)
        result = format_diff(ga, gb, na, nb)
        assert "~ ▤ lock: unlocked -> password_protected" in result.splitlines()

    def test_reentrancy_enum_transition_on(self):
        # Properties are a FIXED schema -- every VI always has a reentrancy
        # value, so a change renders as a VALUE transition (~), never as an
        # added (+) row -- exactly like lock_state.
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            execution=ExecutionProps(reentrancy=Reentrancy.SHARED_CLONE)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "~ ▤ reentrancy: non_reentrant -> shared_clone" in lines
        assert "+ reentrancy" not in result
        assert "- reentrancy" not in result

    def test_reentrancy_enum_transition_off(self):
        ga, gb, na, nb = _pair()
        ga._vi_properties[na] = VIProperties(
            execution=ExecutionProps(reentrancy=Reentrancy.PREALLOCATED_CLONE)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "~ ▤ reentrancy: preallocated_clone -> non_reentrant" in lines
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
        assert "~ ▤ priority: normal -> subroutine" in lines
        assert "~ ▤ run-on-open: false -> true" in lines

    def test_typedef_status_enum_transition(self):
        # typedef_status lives on VIProperties.kind -- a sub-struct of
        # Properties, not a separate facet -- so its transition renders as a
        # "property" row, same as lock_state.
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            kind=KindProps(typedef_status=TypedefStatus.STRICT_TYPEDEF)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "~ ▤ typedef_status: not_a_typedef -> strict_typedef" in lines
        assert "+ typedef_status" not in result
        assert "- typedef_status" not in result

    def test_dynamic_dispatch_source_only_and_no_block_diagram_flags(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            kind=KindProps(
                dynamic_dispatch=True, source_only=True,
                has_no_block_diagram=True,
            )
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "~ ▤ dynamic-dispatch: false -> true" in lines
        assert "~ ▤ source-only: false -> true" in lines
        assert "~ ▤ no-block-diagram: false -> true" in lines

    def test_instance_vi_flag_turned_off(self):
        ga, gb, na, nb = _pair()
        ga._vi_properties[na] = VIProperties(
            kind=KindProps(is_instance_vi=True)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "~ ▤ instance-vi: true -> false" in lines
        assert "+ instance-vi" not in result
        assert "- instance-vi" not in result

    def test_exec_system_enum_transition(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            execution=ExecutionProps(exec_system=ExecSystem.STANDARD)
        )
        result = format_diff(ga, gb, na, nb)
        lines = result.splitlines()
        assert "~ ▤ exec_system: same_as_caller -> standard" in lines

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
        # Unlike the connector pane (verbose-only), a lock/reentrancy change
        # is high-signal enough to always show, in both tiers.
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(lock_state=LockState.LOCKED)
        concise = format_diff(ga, gb, na, nb)
        verbose = format_diff(ga, gb, na, nb, verbose=True)
        assert "~ ▤ lock: unlocked -> locked" in concise.splitlines()
        assert "~ ▤ lock: unlocked -> locked" in verbose.splitlines()

    def test_property_row_order_matches_popover_group_order(self):
        # Property rows order the SAME as the properties popover: group order
        # (Version/lock_state=0, then Execution=1, ... then Kind=5), alpha
        # within a group -- see diff.py's _PROPERTY_GROUP_RANK. Flip one field
        # from each group at once and assert lock leads, then the Execution
        # fields alphabetically, then the Kind fields alphabetically.
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            lock_state=LockState.PASSWORD_PROTECTED,
            execution=ExecutionProps(
                reentrancy=Reentrancy.SHARED_CLONE, priority=Priority.SUBROUTINE,
                exec_system=ExecSystem.STANDARD, run_when_opened=True,
            ),
            kind=KindProps(
                typedef_status=TypedefStatus.TYPEDEF, dynamic_dispatch=True,
                source_only=True, has_no_block_diagram=True,
                is_instance_vi=True,
            ),
        )
        d = diff_to_dict(ga, gb, na, nb)
        prop_uids = [c["uid"] for c in d["changes"] if c["kind"] == "property"]
        assert prop_uids == [
            "property:lock_state",
            "property:exec_system",
            "property:priority",
            "property:reentrancy",
            "property:run_when_opened",
            "property:dynamic_dispatch",
            "property:has_no_block_diagram",
            "property:is_instance_vi",
            "property:source_only",
            "property:typedef_status",
        ]


class TestHealthNeverDiffed:
    """VI HEALTH is an emergent characteristic, deliberately omitted from the
    diff entirely -- flipping ``_vi_health`` must produce NO diff output at
    all: no ``kind=="health"`` row, no ``health:``-prefixed uid anywhere, and
    no "broken" text row in ``format_diff`` (either tier)."""

    def test_health_flip_produces_no_diff_output(self):
        ga, gb, na, nb = _pair()
        gb._vi_health[nb] = VIHealth(bad_node=True)

        concise = format_diff(ga, gb, na, nb)
        verbose = format_diff(ga, gb, na, nb, verbose=True)
        assert concise == ""
        assert verbose == ""
        assert "broken" not in concise
        assert "broken" not in verbose

        rows = netlist_diff_rows(ga, gb, na, nb)
        assert not [r for r in rows if r.kind == "health"]
        assert not [r for r in rows if r.uid is not None
                    and str(r.uid).startswith("health")]

        d = diff_to_dict(ga, gb, na, nb)
        assert not [c for c in d["changes"] if c["kind"] == "health"]
        assert not [c for c in d["changes"]
                    if str(c["uid"]).startswith("health")]
        assert d["changes"] == []


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
        assert row.uid == "property:lock_state"
        assert row.text == "▤ lock: unlocked -> password_protected"

    def test_no_property_rows_when_unchanged(self):
        ga, gb, na, nb = _pair()
        rows = netlist_diff_rows(ga, gb, na, nb)
        assert not [r for r in rows if r.kind == "property"]

    def test_no_added_or_removed_property_rows(self):
        # Fixed schema -- a property row's ``change`` is ALWAYS "modified",
        # never "added"/"removed" (those tags are reserved for genuine
        # diagram elements that can appear/disappear).
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            lock_state=LockState.LOCKED,
            execution=ExecutionProps(reentrancy=Reentrancy.SHARED_CLONE),
            kind=KindProps(typedef_status=TypedefStatus.TYPEDEF),
        )
        rows = netlist_diff_rows(ga, gb, na, nb)
        prop_rows = [r for r in rows if r.kind == "property"]
        assert len(prop_rows) == 3
        assert all(r.change == "modified" for r in prop_rows)


class TestJsonDiff:
    """--format json (diff_to_dict) must carry the same Properties changes
    the text report does -- as ordinary ``changes[]`` entries now, not a
    separate top-level ``"properties"`` array."""

    def test_diff_to_dict_includes_property_changes(self):
        ga, gb, na, nb = _pair()
        gb._vi_properties[nb] = VIProperties(
            lock_state=LockState.PASSWORD_PROTECTED
        )
        d = diff_to_dict(ga, gb, na, nb)
        # No separate top-level sections any more -- everything lives in
        # "changes" (plus the unrelated "common_nodes" tally).
        assert set(d) == {"changes", "common_nodes"}

        lock_change = next(
            c for c in d["changes"] if c["uid"] == "property:lock_state"
        )
        assert lock_change["kind"] == "property"
        assert lock_change["change"] == "modified"
        assert lock_change["label"] == "lock"
        assert lock_change["detail"] == "unlocked → password_protected"
        assert "old" not in lock_change and "new" not in lock_change
        assert "field" not in lock_change

    def test_diff_to_dict_no_metadata_change_is_empty_sections(self):
        ga, gb, na, nb = _pair()
        d = diff_to_dict(ga, gb, na, nb)
        assert d["changes"] == []
