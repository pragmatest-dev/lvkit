"""Validate the bundled connector-pane pattern table (data/connector_pane_
patterns.json) and its geometry loader.

These are DATA-INTEGRITY invariants — every pattern was hand-transcribed from
the authoritative LabVIEW-Wiki pattern image, so the test guards against a
typo (a repeated/missing index, a cell that overflows the pane, a count that
disagrees with the grid).
"""

from __future__ import annotations

import json

import pytest

from lvkit._data import data_dir
from lvkit.render.connector_pane_geometry import (
    _patterns,
    get_pattern,
    known_con_ids,
)

_EPS = 1e-9


def _raw() -> dict:
    return json.loads(
        (data_dir() / "connector_pane_patterns.json").read_text(encoding="utf-8")
    )


def test_table_loads_and_dominant_pattern_present():
    # 4815 (4-2-2-4) is ~76% of the corpus — it must always be present.
    p = get_pattern(4815)
    assert p is not None
    assert p.terminal_count == 12
    assert p.name == "4-2-2-4"


def test_all_36_published_patterns_present():
    """The complete LabVIEW connector-pane set is conId 4800-4835."""
    assert known_con_ids() == frozenset(range(4800, 4836))


def test_index_origin_differs_per_pattern():
    """The whole reason each map is transcribed, not computed: 4815 numbers
    from the bottom-right, 4834 from the top-left. Guards against anyone
    'simplifying' the table to one universal numbering rule."""
    p4815 = get_pattern(4815)
    p4834 = get_pattern(4834)
    assert p4815 is not None and p4834 is not None
    # 4815 idx0 = bottom-right
    c = p4815.cell_by_index()[0]
    assert c.x + c.w == pytest.approx(1.0) and c.y + c.h == pytest.approx(1.0)
    # 4834 idx0 = top-left
    c = p4834.cell_by_index()[0]
    assert c.x == pytest.approx(0.0) and c.y == pytest.approx(0.0)


@pytest.mark.parametrize("con_id", sorted(known_con_ids()))
def test_indices_are_contiguous_0_to_n(con_id: int):
    p = get_pattern(con_id)
    assert p is not None
    indices = sorted(c.index for c in p.cells)
    assert indices == list(range(p.terminal_count)), (
        f"conId {con_id}: indices {indices} are not 0..{p.terminal_count - 1}"
    )


@pytest.mark.parametrize("con_id", sorted(known_con_ids()))
def test_cell_count_matches_terminal_count(con_id: int):
    p = get_pattern(con_id)
    assert p is not None
    assert len(p.cells) == p.terminal_count


@pytest.mark.parametrize("con_id", sorted(known_con_ids()))
def test_cells_lie_within_unit_pane(con_id: int):
    p = get_pattern(con_id)
    assert p is not None
    for c in p.cells:
        assert -_EPS <= c.x <= 1 + _EPS
        assert -_EPS <= c.y <= 1 + _EPS
        assert c.w > 0 and c.h > 0
        assert c.x + c.w <= 1 + _EPS
        assert c.y + c.h <= 1 + _EPS


@pytest.mark.parametrize("con_id", sorted(known_con_ids()))
def test_cells_do_not_overlap(con_id: int):
    """No two cells may overlap (area-wise) — the pane is a partition."""
    p = get_pattern(con_id)
    assert p is not None
    cells = p.cells
    for i in range(len(cells)):
        a = cells[i]
        for j in range(i + 1, len(cells)):
            b = cells[j]
            overlap_w = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
            overlap_h = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
            assert not (overlap_w > _EPS and overlap_h > _EPS), (
                f"conId {con_id}: cells idx{a.index} and idx{b.index} overlap"
            )


def test_4815_index_0_is_bottom_right():
    """Ground-truth spot check from the authoritative image: 4-2-2-4 numbers
    Right->Left, Bottom->Top, so index 0 is the bottom-right cell and index 11
    is the top-left."""
    p = get_pattern(4815)
    assert p is not None
    by_index = p.cell_by_index()
    c0 = by_index[0]
    assert c0.x + c0.w == pytest.approx(1.0)  # rightmost column
    assert c0.y + c0.h == pytest.approx(1.0)  # bottom row
    c11 = by_index[11]
    assert c11.x == pytest.approx(0.0)  # leftmost column
    assert c11.y == pytest.approx(0.0)  # top row


def test_4820_middle_cells_span_two_columns():
    """The irregular grid pattern: indices 2 and 7 are the wide middle cells."""
    p = get_pattern(4820)
    assert p is not None
    by_index = p.cell_by_index()
    assert by_index[2].w == pytest.approx(0.5)  # 2 of 4 columns
    assert by_index[7].w == pytest.approx(0.5)


def test_meta_documents_provenance_and_convention():
    meta = _raw()["_meta"]
    assert "provenance" in meta
    assert "index_convention" in meta
    # PER-PATTERN, not a universal rule — guards the docstring intent.
    assert "DIFFER PER PATTERN" in meta["index_convention"]


def test_loader_returns_none_for_unknown_conid():
    assert get_pattern(999999) is None


def test_lru_cache_shared_instance():
    assert _patterns() is _patterns()
