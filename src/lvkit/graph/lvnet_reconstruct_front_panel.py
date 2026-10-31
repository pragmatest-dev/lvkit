"""Which ``front-panel :`` row a drive line or ``read`` line belongs to.

An indicator's drive line and a control's ``read`` line name their terminal by
its label; a terminal whose label another terminal shares also carries its uid
(verbose text: ``Rate (id 5020) = 3``). A row is claimed by uid when the text has
one, else by label in document order. Terse text cannot say which of two
same-labelled terminals a line inside a structure is for, so that is an error.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from .lvnet_grammar import _LVNET_UNWIRED
from .lvnet_parse import ParsedBoundaryTerminal, ParsedIndicatorDrive
from .lvnet_reconstruct_common import (
    HandleTarget,
    LvnetReconstructError,
    parse_source_token,
    require_netref,
)
from .netlist_models import (
    BoundaryOutput,
    NetlistControlRead,
    NetlistIndicatorDrive,
    NetlistInstance,
    NetlistItem,
    NetlistScope,
)


def indicator_drive_output(
    item: ParsedIndicatorDrive,
    registry: dict[str, HandleTarget],
    *,
    placed: bool,
) -> BoundaryOutput:
    """The indicator a drive line names, with the source it is driven from. Its
    type and caption come from the ``front-panel :`` row it is claimed by."""
    source = (
        None
        if item.source == _LVNET_UNWIRED
        else require_netref(
            parse_source_token(item.source, registry), f"{item.name} drive"
        )
    )
    return BoundaryOutput(
        name=item.name,
        type_descriptor="",
        source=source,
        placed=placed,
        terminal_uid=item.uid,
    )


def walk_items(items: list[NetlistItem]) -> list[NetlistItem]:
    found: list[NetlistItem] = []
    for item in items:
        found.append(item)
        if isinstance(item, NetlistScope):
            for frame in item.frames:
                found.extend(walk_items(frame.body))
    return found


def placed_drives(items: list[NetlistItem]) -> list[BoundaryOutput]:
    """The indicators driven inside a structure, in document order."""
    return [i.output for i in walk_items(items) if isinstance(i, NetlistIndicatorDrive)]


def control_reads(items: list[NetlistItem]) -> list[NetlistControlRead]:
    """The ``read`` lines inside a structure, in document order."""
    return [i for i in walk_items(items) if isinstance(i, NetlistControlRead)]


def _shared(rows: list[ParsedBoundaryTerminal]) -> set[str]:
    counts = Counter(r.name for r in rows)
    return {name for name, n in counts.items() if n > 1}


def claim_indicator_drives(
    rows: list[ParsedBoundaryTerminal], drives: list[BoundaryOutput]
) -> list[BoundaryOutput | None]:
    """For each indicator row, in order, the drive line that is for it (``None``
    when no line is). Every drive must be claimed."""
    unclaimed = list(drives)
    shared = _shared(rows)
    claimed: list[BoundaryOutput | None] = []
    for row in rows:
        if row.uid is None and row.name in shared:
            if any(d.placed and d.name == row.name for d in drives):
                raise LvnetReconstructError(
                    f"{row.name!r} labels several indicators and one is driven "
                    f"inside a structure: terse text cannot say which; "
                    f"use verbose text, which gives each its terminal id"
                )
        at = next(
            (
                i
                for i, d in enumerate(unclaimed)
                if d.name == row.name and d.terminal_uid == row.uid
            ),
            None,
        )
        claimed.append(unclaimed.pop(at) if at is not None else None)
    if unclaimed:
        raise LvnetReconstructError(
            f"indicator drive {unclaimed[0].name!r} matches no front-panel "
            f"indicator row"
        )
    return claimed


def check_control_reads(
    rows: list[ParsedBoundaryTerminal], reads: list[NetlistControlRead]
) -> None:
    """Every ``read`` names one control row, and each row is read once."""
    shared = _shared(rows)
    seen: set[tuple[str, str | None]] = set()
    for read in reads:
        matching = [
            r for r in rows if r.name == read.name and r.uid == read.terminal_uid
        ]
        if not matching:
            raise LvnetReconstructError(
                f"'read {read.name}' names no control of the front panel"
            )
        if read.terminal_uid is None and read.name in shared:
            raise LvnetReconstructError(
                f"{read.name!r} labels several controls and one is read inside a "
                f"structure: terse text cannot say which; use verbose text, "
                f"which gives each its terminal id"
            )
        key = (read.name, read.terminal_uid)
        if key in seen:
            raise LvnetReconstructError(f"'read {read.name}' appears twice")
        seen.add(key)


def mark_read_outputs(
    body: list[NetlistItem], referenced: set[tuple[str, str]]
) -> None:
    """Set ``NetlistOutput.read`` on every instance output the document names as a
    source (``referenced``: producer uid and terminal)."""
    for item in walk_items(body):
        if isinstance(item, NetlistInstance):
            item.outputs = [
                replace(o, read=(item.uid, o.net.terminal) in referenced)
                for o in item.outputs
            ]
