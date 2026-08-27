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
   RIGHT->LEFT (`cell.x` desc, then `cell.y`) — computed from the pattern's
   image-transcribed cell placement. Because it sorts by a cell's POSITION (not
   by assuming index==reading-order), a pattern whose numbering zig-zags
   (4816-4825, 4833-4835) still reads correctly, with no per-pattern override.

Direction is NEVER inferred from geometry; callers pass it. The order is total
and deterministic (fallback: slot index).
"""

from __future__ import annotations

from enum import Enum

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


class WiringRequirement(Enum):
    """A connector-pane terminal's full wiring-requirement state -- lvnet
    §5's "three-state connector (``wiring_rule``)", which in practice is
    four states: the three real LabVIEW dispositions plus UNKNOWN for an
    unresolved wiring rule. An axis independent of whether the terminal is
    actually wired (a terminal can be REQUIRED and unwired -- a broken
    wire -- or OPTIONAL and wired).

    Bare ``Enum``, not ``(str, Enum)``: this value space is fully owned --
    built once here from ``requirement_rank``/``ParsedWiringRule`` and never
    compared against a raw wiring-rule int/string anywhere else. ``.value``
    is read only for lvnet-text/JSON display.
    """

    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    UNKNOWN = "unknown"


_RANK_TO_REQUIREMENT = {
    _RANK_REQUIRED: WiringRequirement.REQUIRED,
    _RANK_RECOMMENDED: WiringRequirement.RECOMMENDED,
    _RANK_OPTIONAL: WiringRequirement.OPTIONAL,
}


def requirement_state(terminal: Terminal, direction: str) -> WiringRequirement:
    """The full §5 requirement state for display -- ``requirement_rank``'s
    same REQUIRED/RECOMMENDED/OPTIONAL fold (including the Dynamic-Dispatch
    and error-direction rules), EXCEPT an unresolved wiring rule
    (``ParsedWiringRule.INVALID``) reports UNKNOWN here instead of the
    rank's ordering-only fold into RECOMMENDED. Sorting wants an unresolved
    rule to sit at the neutral (Recommended) position; display must not
    claim a resolved state the VI never authored.
    """
    if terminal.wiring_rule == ParsedWiringRule.INVALID:
        return WiringRequirement.UNKNOWN
    return _RANK_TO_REQUIREMENT[requirement_rank(terminal, direction)]


def ordered_interface(
    terminals: list[Terminal], direction: str, pattern_id: int | None
) -> list[Terminal]:
    """Order one direction group of interface terminals by the canonical key."""
    pattern = get_pattern(pattern_id) if pattern_id is not None else None
    cell_by_index = pattern.cell_by_index() if pattern else {}

    def geometry_key(t: Terminal) -> tuple[int, float, float]:
        idx = t.index
        cell = cell_by_index.get(idx) if idx is not None else None
        if cell is None:
            return (1, float(idx if idx is not None and idx >= 0 else _UNPLACED), 0.0)
        # inputs read left->right (x asc); outputs right->left (x desc).
        primary = cell.x if direction == "input" else -cell.x
        return (0, round(primary, 4), round(cell.y, 4))

    def sort_key(t: Terminal) -> tuple:
        return (
            1 if t.is_error_cluster else 0,  # error cluster last
            requirement_rank(t, direction),  # Required -> Recommended -> Optional
            geometry_key(t),  # then reading order within level
        )

    return sorted(terminals, key=sort_key)
