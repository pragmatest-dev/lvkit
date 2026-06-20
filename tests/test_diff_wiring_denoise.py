"""Tests for the diff Wiring-section denoise (the "net design").

Three behaviours:
1. Suppress wires internal to the added/removed subgraph (both endpoints
   new/removed); keep any wire touching a node present in both versions
   (a "splice" — a real rewire of unchanged topology).
2. Relabel unnamed-constant endpoints by type/value, not a raw UID.
3. Drop structure-internal tunnel/selector self-loops from Wiring and
   surface the case selector source in the Structures section instead.

These exercise the pure core (`_wiring_changes` + helpers) with synthetic
Wire/Constant/CaseOperation models — no graph load, no user VIs.
"""

from __future__ import annotations

from lvkit.graph.diff import (
    _const_label_by_id,
    _endpoint_names,
    _is_internal_wire,
    _selector_source,
    _structure_content_summary,
    _wire_key,
    _wiring_changes,
)
from lvkit.graph.models import Constant, Wire, WireEnd
from lvkit.models import (
    CaseFrame,
    CaseOperation,
    ClusterField,
    LVType,
)


def _we(node_id: str, name: str | None = None, term: str | None = None) -> WireEnd:
    return WireEnd(terminal_id=term or node_id, node_id=node_id, name=name)


def _wire(
    src_id: str, src_name: str | None, dst_id: str, dst_name: str | None,
    *, src_term: str | None = None, dst_term: str | None = None,
) -> Wire:
    return Wire(
        source=_we(src_id, src_name, src_term),
        dest=_we(dst_id, dst_name, dst_term),
    )


def _error_cluster_type() -> LVType:
    return LVType(kind="cluster", fields=[
        ClusterField(name="status"),
        ClusterField(name="code"),
        ClusterField(name="source"),
    ])


# ── Internal-edge detection ──────────────────────────────────────────────


class TestInternalWire:
    def test_self_loop_is_internal(self):
        assert _is_internal_wire(_wire("vi::174", "case", "vi::174", "case"))

    def test_distinct_nodes_not_internal(self):
        assert not _is_internal_wire(_wire("vi::1", "Mean.vi", "vi::2", "Gt"))


# ── Splice kept, new-subgraph wires suppressed ───────────────────────────


class TestSpliceVsInternal:
    def test_wrap_in_case_keeps_only_the_splice(self):
        # A: Mean.vi feeds the VI output directly.
        wires_a = [_wire("vi::m", "Mean.vi", "vi::out", "out")]
        # B: a case ("error constant") was inserted. Mean now also feeds a
        # new guard; guard + const feed the new case (all new-internal); the
        # old Mean->out link is unchanged; the case's tunnels are self-loops.
        wires_b = [
            _wire("vi::m", "Mean.vi", "vi::out", "out"),          # unchanged
            _wire("vi::m", "Mean.vi", "vi::g", "Greater Than 0?"),  # splice
            _wire("vi::g", "Greater Than 0?", "vi::c", "error constant"),
            _wire("vi::k", None, "vi::c", "error constant"),       # const -> case
            _wire("vi::c", "error constant", "vi::c", "error constant"),  # tunnel
        ]
        changes = _wiring_changes(wires_a, wires_b, {})
        descs = [(c.category, c.description) for c in changes]
        assert descs == [("added", "Mean.vi -> Greater Than 0?")]

    def test_removed_wire_touching_shared_node_is_kept(self):
        wires_a = [
            _wire("vi::a", "A", "vi::b", "B"),
            _wire("vi::a", "A", "vi::x", "X"),
        ]
        wires_b = [_wire("vi::a", "A", "vi::b", "B")]  # A -> X removed
        changes = _wiring_changes(wires_a, wires_b, {})
        assert [(c.category, c.description) for c in changes] == [
            ("removed", "A -> X"),
        ]


# ── Unnamed-constant relabel ─────────────────────────────────────────────


class TestConstRelabel:
    def test_unnamed_const_endpoint_uses_type_label(self):
        labels = {"vi::261": "error cluster"}
        key = _wire_key(_wire("vi::261", None, "vi::loop", "For Loop"), labels)
        assert key == ("error cluster", "For Loop")

    def test_relabel_in_diff_output(self):
        # For Loop exists in both versions; an unnamed const newly feeds it.
        wires_a = [_wire("vi::loop", "For Loop", "vi::sink", "sink")]
        wires_b = [
            _wire("vi::loop", "For Loop", "vi::sink", "sink"),
            _wire("vi::343", None, "vi::loop", "For Loop"),
        ]
        labels = {"vi::343": "list[float] constant"}
        changes = _wiring_changes(wires_a, wires_b, labels)
        assert [(c.category, c.description) for c in changes] == [
            ("added", "list[float] constant -> For Loop"),
        ]

    def test_identical_unnamed_const_uid_churn_cancels(self):
        # Same type+value const feeds the same node in both versions, only
        # the UID changed -> no net wiring change.
        wires_a = [_wire("vi::388", None, "vi::loop", "For Loop")]
        wires_b = [_wire("vi::474", None, "vi::loop", "For Loop")]
        labels = {"vi::388": "float constant", "vi::474": "float constant"}
        assert _wiring_changes(wires_a, wires_b, labels) == []


# ── Helpers ──────────────────────────────────────────────────────────────


class TestHelpers:
    def test_endpoint_names_collects_named_skips_unnamed(self):
        wires = [_wire("vi::1", "A", "vi::2", None)]
        assert _endpoint_names(wires) == {"A"}

    def test_const_label_by_id_only_unnamed(self):
        consts = [
            Constant(id="vi::1", value="0.0", lv_type=LVType(kind="DBL")),
            Constant(id="vi::2", value="x", name="named", lv_type=LVType(kind="DBL")),
        ]
        labels = _const_label_by_id(consts)
        assert set(labels) == {"vi::1"}


# ── Selector callout in Structures ───────────────────────────────────────


def _case_with_selector() -> CaseOperation:
    return CaseOperation(
        id="vi::174", name="error constant", labels=["CaseStructure"],
        selector_terminal="vi::204",
        frames=[CaseFrame(selector_value="False"), CaseFrame(selector_value="True")],
    )


class TestSelectorCallout:
    def test_selector_source_resolves_feeding_node(self):
        case = _case_with_selector()
        wires = [_wire("vi::g", "Greater Than 0?", "vi::174", "error constant",
                       dst_term="vi::204")]
        assert _selector_source(case, wires, {}) == "Greater Than 0?"

    def test_summary_includes_selector_and_frame_content(self):
        case = _case_with_selector()
        const = Constant(
            id="vi::261", value="{'status': True, 'code': 17, 'source': 'x'}",
            lv_type=_error_cluster_type(), parent="vi::174", frame="True",
        )
        wires = [_wire("vi::g", "Greater Than 0?", "vi::174", "error constant",
                       dst_term="vi::204")]
        summary = _structure_content_summary(case, [const], wires, {})
        assert summary == "selector <- Greater Than 0?; frame True: error cluster"

    def test_summary_without_wires_omits_selector(self):
        case = _case_with_selector()
        const = Constant(
            id="vi::261", value="{'status': True, 'code': 17, 'source': 'x'}",
            lv_type=_error_cluster_type(), parent="vi::174", frame="True",
        )
        assert _structure_content_summary(case, [const]) == "frame True: error cluster"
