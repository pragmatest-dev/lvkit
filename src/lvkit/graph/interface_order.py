"""Canonical ordering of a VI's interface terminals.

The order a sane function signature and a readable describe listing want is
maintainer-defined (NI's own doc order can't be derived — the doc pages don't
expose connector-pane geometry). Per direction group (inputs and outputs sorted
SEPARATELY), the sort key is disposition-OUTER / geometry-INNER — a full
column-major pass within each disposition group, then the next group:

1. **error cluster last** — an error in/out sorts after everything else.
2. **requirement level** — Required(0) -> Recommended(1) -> Optional(2). Optional
   is soft-deprecated (hidden from Context Help), so it sinks to the bottom of a
   direction's non-error terminals, mirroring "check these last". Required is
   inputs-only (a required input must be wired); Dynamic Dispatch folds in by
   direction (DD input = Required, DD output = Recommended).
3. **geometry** — the connector-pane reading order WITHIN a level: inputs
   column-major LEFT->RIGHT (`cell.x` asc, then `cell.y`), outputs column-major
   RIGHT->LEFT (`cell.x` desc, then `cell.y`) — computed from the pattern's cell
   geometry, unless the pattern carries a `terminal_order` override (for outlier
   patterns), in which case the slot's position in that sequence is used.

Direction is NEVER inferred from geometry; callers pass it. The order is total
and deterministic (fallback: slot index).
"""
from __future__ import annotations

from ..connector_pane_geometry import get_pattern
from ..models import Terminal
from ..parser.models import ParsedWiringRule

# Requirement rank: Required -> Recommended -> Optional. Unknown (unresolved
# wiring rule) is treated as Recommended -- the common default, and never
# demoted below a genuinely-authored Optional.
_RANK_REQUIRED = 0
_RANK_RECOMMENDED = 1
_RANK_OPTIONAL = 2

_UNPLACED = 10**9  # sort key for a terminal with no resolvable geometric slot


def requirement_rank(terminal: Terminal, direction: str) -> int:
    """0=Required, 1=Recommended, 2=Optional.

    Required is INPUTS-ONLY: an output can't compel the caller (LabVIEW still
    lets you SET the Required wiring rule on an output, but it's meaningless), so
    a Required/Dynamic-Dispatch output is treated as Recommended. Optional (a
    soft deprecation) applies to both directions. Recommended and unknown
    (unresolved wiring rule) both map to Recommended.
    """
    rule = terminal.wiring_rule
    if rule in (ParsedWiringRule.RECOMMENDED, ParsedWiringRule.INVALID):
        return _RANK_RECOMMENDED  # INVALID = unresolved wiring rule
    if rule == ParsedWiringRule.OPTIONAL:
        return _RANK_OPTIONAL
    if direction == "input" and rule in (
        ParsedWiringRule.REQUIRED,
        ParsedWiringRule.DYNAMIC_DISPATCH,
    ):
        return _RANK_REQUIRED
    return _RANK_RECOMMENDED  # a (moot) Required/DD output, or any unexpected value


def is_required(terminal: Terminal, direction: str) -> bool:
    """A terminal the caller MUST wire (used by presentation to mark Required)."""
    return requirement_rank(terminal, direction) == 0


def ordered_interface(
    terminals: list[Terminal], direction: str, pattern_id: int | None
) -> list[Terminal]:
    """Order one direction group of interface terminals by the canonical key."""
    pattern = get_pattern(pattern_id) if pattern_id is not None else None
    override = pattern.terminal_order if pattern else None
    cell_by_index = pattern.cell_by_index() if pattern else {}

    def geometry_key(t: Terminal) -> tuple[int, float, float]:
        idx = t.index
        if override is not None:
            # Explicit outlier order: rank by position in the sequence.
            if idx in override:
                return (0, float(override.index(idx)), 0.0)
            return (1, float(idx if idx is not None and idx >= 0 else _UNPLACED), 0.0)
        cell = cell_by_index.get(idx) if idx is not None else None
        if cell is None:
            return (1, float(idx if idx is not None and idx >= 0 else _UNPLACED), 0.0)
        # inputs read left->right (x asc); outputs right->left (x desc).
        primary = cell.x if direction == "input" else -cell.x
        return (0, round(primary, 4), round(cell.y, 4))

    def sort_key(t: Terminal) -> tuple:
        return (
            1 if t.is_error_cluster else 0,       # error cluster last
            requirement_rank(t, direction),        # Required -> Recommended -> Optional
            geometry_key(t),                       # then reading order within level
        )

    return sorted(terminals, key=sort_key)
