"""The names of the rows an Event Structure's dynamic terminal carries.

Register For Events copies the label of each user event wired into a row into the
type of the registration it outputs, so the type of the structure's dynamic
terminal names the rows wherever the registration came from. A registration's
rows are numbered from 1; a cluster of registrations numbers its rows across its
fields in field order and names each ``Field.Label``.
"""

from __future__ import annotations

from dataclasses import replace

from ..models import EventFrame, EventRegRow, FrameEvent, LVType, LVTypeKind
from ..parser.nodes.event import USER_EVENT_TYPE_CODE

_EVENT_REG = "EventReg"


def registration_rows(lv_type: LVType | None) -> list[EventRegRow]:
    """The rows of the registration ``lv_type`` is, in dynIndex order (row k is
    element k - 1), each named as a frame labels it. A plain registration's rows
    keep their own labels; a cluster of registrations prefixes each with its
    field's name. Empty for a type with no rows, and for a cluster holding anything
    but registrations (how its rows are counted is not recorded)."""
    if lv_type is None:
        return []
    if lv_type.ref_type == _EVENT_REG:
        return list(lv_type.event_rows or ())
    if lv_type.kind != LVTypeKind.CLUSTER or not lv_type.fields:
        return []
    rows: list[EventRegRow] = []
    for field in lv_type.fields:
        if field.type is None or field.type.ref_type != _EVENT_REG:
            return []
        for row in field.type.event_rows or ():
            name = f"{field.name}.{row.name}" if row.name else None
            rows.append(replace(row, name=name))
    return rows


def with_dynamic_names(
    frame: EventFrame,
    events: list[FrameEvent],
    rows: list[EventRegRow],
    displayed_frame: int | None,
) -> EventFrame:
    """``frame`` with each of its dynamic User Events named after its row and, when
    the frame's own label names its last event, that label as LabVIEW writes it
    (``[2] <Action.Event Data>: User Event``). The frame LabVIEW last displayed
    keeps the label the file stores."""
    named: list[FrameEvent] = []
    for event in events:
        row = _row_of(event, rows)
        named.append(
            event.model_copy(update={"dynamic_name": row.name if row else None})
        )
    update: dict[str, object] = {"events": named}
    last = named[-1] if named else None
    if last is not None and last.dynamic_name and frame.index != displayed_frame:
        update["event_label"] = f"[{frame.index}] <{last.dynamic_name}>: User Event"
    return frame.model_copy(update=update)


def _row_of(event: FrameEvent, rows: list[EventRegRow]) -> EventRegRow | None:
    """The row a dynamic User Event is, when the type has it as a User Event row."""
    if event.type_code != USER_EVENT_TYPE_CODE or not event.dyn_index:
        return None
    if event.dyn_index > len(rows):
        return None
    row = rows[event.dyn_index - 1]
    return row if row.event_type == USER_EVENT_TYPE_CODE else None
