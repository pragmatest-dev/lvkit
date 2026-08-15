"""Tests for the connector-geometry terminal auditor (Phase 0 + Phase 1).

Phase 0 (``lvkit.connector_geometry``): pure Slot extraction + geometry
ranking, exercised on a small synthetic 2-in/2-out node.

Phase 1 (``lvkit.tools.connector_geometry_profile.audit_primitive``): the
pure JSON-vs-corpus-profile diff, exercised on a synthetic entry/profile
modeled on the real "Clear Errors" bug (commit 2f71ff2): an I32 INPUT
registered at an index the corpus geometry shows is actually a Boolean
OUTPUT — both the direction mismatch and the type mismatch must be flagged.
"""

from __future__ import annotations

from lvkit.connector_geometry import extract_slots, rank_slots_by_geometry
from lvkit.parser.models import ParsedTerminalInfo, ParsedType
from lvkit.tools.connector_geometry_profile import (
    FLAG_KINDS,
    PrimEntry,
    PrimProfile,
    PrimTerminalEntry,
    SlotProfile,
    audit_primitive,
)


def _term(
    uid: str,
    index: int,
    is_output: bool,
    type_name: str | None,
) -> ParsedTerminalInfo:
    parsed_type = (
        ParsedType(kind="primitive", type_name=type_name) if type_name else None
    )
    return ParsedTerminalInfo(
        uid=uid,
        parent_uid="node0",
        index=index,
        is_output=is_output,
        parsed_type=parsed_type,
        name=uid,
    )


# ---------------------------------------------------------------------------
# Phase 0 — Slot extraction + geometry ranking
# ---------------------------------------------------------------------------
def test_extract_slots_sorted_by_index_with_direction_and_type():
    # A synthetic 2-in / 2-out node laid out as a connector-pane 2x2 grid:
    # inputs on the left (idx 2/3), outputs on the right (idx 0/1). Rect is
    # (x1, y1, x2, y2); y grows DOWNWARD (top row has the smaller y).
    terms = [
        _term("tA", index=3, is_output=False, type_name=None),  # top-left, unwired
        _term("tB", index=2, is_output=False, type_name="Boolean"),  # bottom-left
        _term("tC", index=1, is_output=True, type_name="NumInt32"),  # top-right
        _term("tD", index=0, is_output=True, type_name="NumInt32"),  # bottom-right
    ]
    bounds = {
        "tA": (0.0, 0.0, 10.0, 10.0),
        "tB": (0.0, 20.0, 10.0, 30.0),
        "tC": (50.0, 0.0, 60.0, 10.0),
        "tD": (50.0, 20.0, 60.0, 30.0),
    }

    slots = extract_slots(terms, bounds)

    assert [s.index for s in slots] == [0, 1, 2, 3]
    by_index = {s.index: s for s in slots}
    assert by_index[0].direction == "out"
    assert by_index[0].observed_type == "NumInt32"
    assert by_index[1].direction == "out"
    assert by_index[2].direction == "in"
    assert by_index[2].observed_type == "Boolean"
    assert by_index[3].direction == "in"
    assert by_index[3].observed_type is None  # unwired -- never invented


def test_rank_slots_by_geometry_right_to_left_bottom_to_top():
    terms = [
        _term("tA", index=3, is_output=False, type_name=None),
        _term("tB", index=2, is_output=False, type_name="Boolean"),
        _term("tC", index=1, is_output=True, type_name="NumInt32"),
        _term("tD", index=0, is_output=True, type_name="NumInt32"),
    ]
    bounds = {
        "tA": (0.0, 0.0, 10.0, 10.0),  # top-left
        "tB": (0.0, 20.0, 10.0, 30.0),  # bottom-left
        "tC": (50.0, 0.0, 60.0, 10.0),  # top-right
        "tD": (50.0, 20.0, 60.0, 30.0),  # bottom-right
    }
    slots = extract_slots(terms, bounds)
    ranked = rank_slots_by_geometry(slots)
    by_index = {s.index: s for s in ranked}

    # idx0 = tD = bottom-right -> col_rank 0 (rightmost), row_rank 0 (bottommost)
    assert (by_index[0].col_rank, by_index[0].row_rank) == (0, 0)
    # idx1 = tC = top-right -> same column, higher row (top)
    assert (by_index[1].col_rank, by_index[1].row_rank) == (0, 1)
    # idx2 = tB = bottom-left -> left column, bottom row
    assert (by_index[2].col_rank, by_index[2].row_rank) == (1, 0)
    # idx3 = tA = top-left -> left column, top row
    assert (by_index[3].col_rank, by_index[3].row_rank) == (1, 1)


# ---------------------------------------------------------------------------
# Phase 1 — auditor diff (Clear-Errors-shaped synthetic case)
# ---------------------------------------------------------------------------
def _clear_errors_shaped_entry() -> PrimEntry:
    """JSON as it looked BEFORE the 2f71ff2 fix: idx3 wrongly declared as
    the I32 "specific error code to clear" INPUT, when corpus geometry (see
    below) shows idx3 is actually the Boolean "specific error cleared?"
    OUTPUT."""
    return PrimEntry(
        prim_res_id=9999,
        name="Fake Clear Errors",
        terminals=(
            PrimTerminalEntry(
                index=0,
                direction="out",
                name="error_out",
                type="Cluster",
            ),
            PrimTerminalEntry(
                index=3,
                direction="in",
                name="specific error code to clear",
                type="NumInt32",
            ),
            PrimTerminalEntry(index=4, direction="in", name="error_in", type="Cluster"),
        ),
    )


def _clear_errors_shaped_profile() -> PrimProfile:
    """Corpus geometry: idx3 is consistently observed as a Boolean OUTPUT
    across every gathered instance -- contradicting the JSON above on BOTH
    direction and type."""
    common = {
        "instance_count": 3,
        "wired_instance_count": 3,
        "direction_disagreement": False,
        "type_disagreement": False,
        "sources": ("a.xml", "b.xml", "c.xml"),
    }
    return PrimProfile(
        prim_res_id=9999,
        slots={
            0: SlotProfile(
                index=0,
                directions=frozenset({"out"}),
                observed_types=frozenset({"Cluster"}),
                **common,
            ),
            3: SlotProfile(
                index=3,
                directions=frozenset({"out"}),
                observed_types=frozenset({"Boolean"}),
                **common,
            ),
            4: SlotProfile(
                index=4,
                directions=frozenset({"in"}),
                observed_types=frozenset({"Cluster"}),
                **common,
            ),
        },
        instances_found=3,
        files_considered=("a.xml", "b.xml", "c.xml"),
        files_parsed_ok=3,
        instance_count=3,
    )


def test_auditor_flags_direction_and_type_mismatch_clear_errors_shape():
    entry = _clear_errors_shaped_entry()
    profile = _clear_errors_shaped_profile()

    findings = audit_primitive(entry, profile)

    idx3_findings = [f for f in findings if f.index == 3]
    kinds_at_3 = {f.kind for f in idx3_findings}
    assert "DIRECTION_MISMATCH" in kinds_at_3
    assert "TYPE_MISMATCH" in kinds_at_3
    for f in idx3_findings:
        assert f.kind in FLAG_KINDS

    direction_finding = next(f for f in idx3_findings if f.kind == "DIRECTION_MISMATCH")
    assert direction_finding.json_says.startswith("in")
    assert direction_finding.corpus_shows == "out"

    type_finding = next(f for f in idx3_findings if f.kind == "TYPE_MISMATCH")
    assert "NumInt32" in type_finding.json_says
    assert type_finding.corpus_shows == "Boolean"

    # The correctly-registered indices (0, 4) must NOT be flagged.
    assert not [f for f in findings if f.index in (0, 4) and f.kind in FLAG_KINDS]


def test_auditor_direction_disagreement_flagged_not_silently_pooled():
    """Two instances disagreeing on direction at the same index is a mis-ID
    signal (direction is structurally fixed) -- must be flagged as
    DISAGREEMENT, never averaged away."""
    entry = PrimEntry(
        prim_res_id=8888,
        name="Fake Ambiguous Prim",
        terminals=(
            PrimTerminalEntry(index=1, direction="in", name="x", type="NumInt32"),
        ),
    )
    profile = PrimProfile(
        prim_res_id=8888,
        slots={
            1: SlotProfile(
                index=1,
                directions=frozenset({"in", "out"}),
                observed_types=frozenset({"NumInt32"}),
                instance_count=2,
                wired_instance_count=2,
                direction_disagreement=True,
                type_disagreement=False,
                sources=("a.xml", "b.xml"),
            ),
        },
        instances_found=2,
        files_considered=("a.xml", "b.xml"),
        files_parsed_ok=2,
        instance_count=2,
    )

    findings = audit_primitive(entry, profile)
    assert any(f.kind == "DISAGREEMENT" for f in findings)


def test_auditor_unobserved_and_missing_from_entry_are_notes_not_flags():
    entry = PrimEntry(
        prim_res_id=7777,
        name="Fake Sparse Prim",
        terminals=(
            PrimTerminalEntry(
                index=5,
                direction="in",
                name="never_wired",
                type="String",
            ),
        ),
    )
    profile = PrimProfile(
        prim_res_id=7777,
        slots={
            # index 5 (declared in JSON) never appears -- UNOBSERVED.
            2: SlotProfile(
                index=2,
                directions=frozenset({"out"}),
                observed_types=frozenset({"Boolean"}),
                instance_count=1,
                wired_instance_count=1,
                direction_disagreement=False,
                type_disagreement=False,
                sources=("a.xml",),
            ),
        },
        instances_found=1,
        files_considered=("a.xml",),
        files_parsed_ok=1,
        instance_count=1,
    )

    findings = audit_primitive(entry, profile)
    kinds = {f.kind for f in findings}
    assert "UNOBSERVED" in kinds
    assert "MISSING_FROM_ENTRY" in kinds
    assert not (kinds & FLAG_KINDS)
