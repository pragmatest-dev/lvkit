"""Connector-pane terminal slot geometry — pure, corpus-agnostic core.

This is Phase 0 of the connector-geometry terminal auditor. It answers one
question with no I/O and no corpus knowledge: given a single parsed node's
terminals (their ``ParsedTerminalInfo`` and heap geometry), what does each
connector-pane SLOT actually look like — index, direction, observed type,
position?

Motivation: entries in ``src/lvkit/data/primitives.json`` map
``index -> {name, type, direction}`` for each primResID. Some of those
mappings were hand-entered from NI-doc LISTING order rather than from the
connector-pane's real GEOMETRY, so a name landed on the wrong slot (e.g.
"Clear Errors": an I32 input was registered at an index that geometry shows
is actually a Boolean OUTPUT — fixed in commit 2f71ff2). This module is the
reusable extraction step both the live resolver and the offline corpus
auditor (``lvkit.tools.connector_geometry_profile`` / see also
``scripts/connector_geometry_audit.py``) build on. It stays pure — no file
or corpus access — so it can be reused wherever a node's terminals need
geometry-grounded slot facts.

Terminology:
- ``index``: the connector-pane slot index in LabVIEW's OWN numbering —
  Right->Left, Bottom->Top; idx0 = bottom-right (project reference #38).
  It comes straight from ``ParsedTerminalInfo.index`` and is ALREADY in the
  same index space ``primitives.json`` keys its ``terminals[].index``
  entries by — callers compare corpus-observed slots against a JSON entry
  index-for-index, with no re-derivation needed.
- ``position``: the terminal's absolute heap-pixel bounding rect, from
  ``ParsedVI.layout.node_bounds`` (parser/layout.py) — y grows DOWNWARD.
- ``col_rank`` / ``row_rank``: a purely geometric R->L / B->T dense rank
  over a node's OWN slots, filled by ``rank_slots_by_geometry``. Not needed
  for the index-based direction/type diff in Phase 1 (which trusts
  ``index`` directly) — kept for a LATER phase that must disambiguate two
  same-type terminals sharing a type family (e.g. Insert Menu Items'
  ``item_names`` vs ``item_tags``, both Array — Phase 1 cannot resolve
  that class; see ``lvkit.tools.connector_geometry_profile`` docstring).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from .parser.layout import Rect
from .parser.models import ParsedTerminalInfo


@dataclass(frozen=True)
class Slot:
    """One connector-pane terminal slot, observed on a single parsed node
    instance.

    ``observed_type`` is the LabVIEW type NAME (``ParsedType.type_name``,
    e.g. ``"NumInt32"``, ``"Boolean"``, ``"Array"``, ``"Cluster"``) — the
    same naming convention ``primitives.json`` terminal entries use for
    their own ``"type"`` field, so the two compare directly with no
    translation. ``None`` means the terminal's type could not be resolved
    for this instance (typically: unwired), never a guess.
    """

    index: int
    direction: str  # "in" | "out" -- derived from ParsedTerminalInfo.is_output
    observed_type: str | None
    position: Rect | None = None
    # Filled only by rank_slots_by_geometry(); None otherwise.
    col_rank: int | None = None
    row_rank: int | None = None


def slot_from_terminal(terminal: ParsedTerminalInfo, position: Rect | None) -> Slot:
    """Build one ``Slot`` from a parsed terminal + its (optional) geometry."""
    observed_type = (
        terminal.parsed_type.type_name if terminal.parsed_type is not None else None
    )
    return Slot(
        index=terminal.index,
        direction="out" if terminal.is_output else "in",
        observed_type=observed_type,
        position=position,
    )


def extract_slots(
    terminals: Iterable[ParsedTerminalInfo],
    node_bounds: Mapping[str, Rect],
) -> list[Slot]:
    """Extract every terminal of ONE node into ``Slot`` records, sorted by
    connector-pane ``index``.

    ``terminals`` should already be filtered to one node's own
    ``ParsedTerminalInfo`` entries (e.g. every value of
    ``block_diagram.terminal_info`` whose ``parent_uid`` matches the node's
    uid). ``node_bounds`` is ``ParsedVI.layout.node_bounds`` (or ``{}`` if
    layout wasn't decoded) — a terminal absent from it gets ``position=None``.
    """
    slots = [slot_from_terminal(t, node_bounds.get(t.uid)) for t in terminals]
    slots.sort(key=lambda s: s.index)
    return slots


def _rank_by_tolerance(
    values: Sequence[float], tol: float, *, reverse: bool
) -> dict[int, int]:
    """Dense-rank the positions in ``values`` (by original list index),
    merging values within ``tol`` of their immediate neighbor in sorted
    order into the same rank.

    ``reverse=True`` ranks descending (rank 0 = the largest value) — used
    for both axes here: R->L means rank 0 = rightmost = largest x; B->T
    means rank 0 = bottommost = largest y (heap y grows downward).

    This is a simple neighbor-chaining cluster (not a global k-means-style
    grouping): a run of values each within ``tol`` of the next collapses to
    one rank even if the run's overall span exceeds ``tol``. Adequate for
    connector-pane columns/rows, which are tightly grouped in practice; not
    used by the Phase 1 index-based diff, only by the geometry-disambiguation
    helper below.
    """
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=reverse)
    ranks: dict[int, int] = {}
    current_rank = -1
    last_value: float | None = None
    for i in order:
        v = values[i]
        if last_value is None or abs(v - last_value) > tol:
            current_rank += 1
        ranks[i] = current_rank
        last_value = v
    return ranks


def rank_slots_by_geometry(slots: Sequence[Slot], tol: float = 8.0) -> list[Slot]:
    """Return a copy of ``slots`` with ``col_rank``/``row_rank`` filled from
    ``position`` — a purely geometric Right->Left (col) / Bottom->Top (row)
    dense rank over THIS node's own slots (reference #38 ordering).

    Slots with no ``position`` are returned unranked (``col_rank`` /
    ``row_rank`` stay ``None``) and sorted last. Included for a later
    same-type-disambiguation phase (see module docstring) — NOT consumed by
    the index-based Phase 1 diff, which needs no geometry at all.
    """
    positioned = [s for s in slots if s.position is not None]
    unpositioned = [s for s in slots if s.position is None]

    # positioned excludes None above; capture the narrowed rect per slot so the
    # centers stay index-aligned with `positioned` (a trailing `if s.position`
    # filter here would silently misalign col_ranks[i] if it ever dropped one).
    centers_x: list[float] = []
    centers_y: list[float] = []
    for s in positioned:
        pos = s.position
        assert pos is not None
        centers_x.append((pos[0] + pos[2]) / 2)
        centers_y.append((pos[1] + pos[3]) / 2)
    col_ranks = _rank_by_tolerance(centers_x, tol, reverse=True)
    row_ranks = _rank_by_tolerance(centers_y, tol, reverse=True)

    ranked = [
        replace(s, col_rank=col_ranks[i], row_rank=row_ranks[i])
        for i, s in enumerate(positioned)
    ]
    result = ranked + list(unpositioned)
    result.sort(key=lambda s: s.index)
    return result
