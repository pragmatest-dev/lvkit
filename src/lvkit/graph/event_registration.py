"""The names of the rows an Event Structure's dynamic terminal carries.

Register For Events copies the label of each user event wired into a row into the
type of the registration it outputs, so the type of the structure's dynamic
terminal names the rows wherever the registration came from. A registration's
rows are numbered from 1; a cluster of registrations numbers its rows across its
fields in field order and names each ``Field.Label``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    EventFrame,
    EventRegRow,
    FrameEvent,
    LVType,
    LVTypeKind,
    Terminal,
    TerminalDirection,
    TunnelTerminal,
)
from ..parser.constants import TUNNEL_CLASS_EVENT_DYNAMIC
from ..parser.nodes.event import USER_EVENT_TYPE_CODE, frame_label

_EVENT_REG = "EventReg"


def dynamic_terminal(terminals: list[Terminal]) -> TunnelTerminal | None:
    """The outer input terminal of the Event Structure's dynamic registration."""
    for terminal in terminals:
        if (
            isinstance(terminal, TunnelTerminal)
            and terminal.tunnel_type == TUNNEL_CLASS_EVENT_DYNAMIC
            and terminal.boundary == "outer"
            and terminal.direction == TerminalDirection.INPUT
        ):
            return terminal
    return None


def dynamic_terminal_type(terminals: list[Terminal]) -> LVType | None:
    """The type of the registration wired to the Event Structure's dynamic
    terminal; None when nothing is wired to it."""
    terminal = dynamic_terminal(terminals)
    return terminal.lv_type if terminal is not None else None


def registration_rows(lv_type: LVType | None) -> list[EventRegRow]:
    """The events of the registration ``lv_type`` is, one for each index a frame's
    ``dynIndex`` can name (index k is element k - 1). Every row of a registration
    takes the next index, whatever it registers; a row that is a cluster is followed
    by its elements, in order and to any depth. An element is named
    ``Cluster.Element`` and a row with no label that is an array ``#k``. A cluster of
    registrations prefixes each name with its field's name and numbers across the
    fields. Only the events of User Event rows carry the User Event type; the others
    keep their own, so their frames keep the label they had. Empty for a type with
    no rows, and for a cluster holding anything but registrations (how its rows are
    counted is not recorded)."""
    if lv_type is None:
        return []
    if lv_type.ref_type == _EVENT_REG:
        return _numbered(_registration_events(lv_type))
    if lv_type.kind != LVTypeKind.CLUSTER or not lv_type.fields:
        return []
    events: list[_Event] = []
    for field in lv_type.fields:
        if field.type is None or field.type.ref_type != _EVENT_REG:
            return []
        events += _registration_events(field.type, prefix=f"{field.name}.")
    return _numbered(events)


@dataclass(frozen=True)
class _Event:
    """One index of a registration: its name (None for an array, numbered once
    every index is known) and its event type."""

    name: str | None
    event_type: int | None
    anonymous: bool = False
    row_type: LVType | None = None


def locate_row(lv_type: LVType | None, dyn_index: int) -> tuple[int | None, int] | None:
    """Where the row ``dyn_index`` (a frame's ``dynIndex``) sits: the position of
    the registration among the fields of a cluster of registrations (None for a
    single registration) and its index within that registration; None when the
    type has no such row."""
    if lv_type is None or dyn_index < 1:
        return None
    if lv_type.ref_type == _EVENT_REG:
        count = len(_registration_events(lv_type))
        return (None, dyn_index) if dyn_index <= count else None
    if lv_type.kind != LVTypeKind.CLUSTER or not lv_type.fields:
        return None
    left = dyn_index
    for position, field in enumerate(lv_type.fields):
        if field.type is None or field.type.ref_type != _EVENT_REG:
            return None
        count = len(_registration_events(field.type))
        if left <= count:
            return (position, left)
        left -= count
    return None


def _numbered(events: list[_Event]) -> list[EventRegRow]:
    return [
        EventRegRow(
            f"#{index}" if event.anonymous else event.name,
            event.event_type,
            event.row_type,
        )
        for index, event in enumerate(events, start=1)
    ]


def _registration_events(registration: LVType, prefix: str = "") -> list[_Event]:
    events: list[_Event] = []
    for row in registration.event_rows or ():
        events += _row_events(row.name, row.type, row.event_type, prefix)
    return events


def _row_events(
    name: str | None, row_type: LVType | None, event_type: int | None, prefix: str
) -> list[_Event]:
    """The indices one row (or one element of a cluster row) takes. A cluster's
    own index is not an event; its elements follow it."""
    if row_type is not None and row_type.fields is not None:
        label = f"{prefix}{name}." if name else prefix
        events = [_Event(None, None)]
        for member in row_type.fields:
            events += _row_events(member.name, member.type, event_type, label)
        return events
    anonymous = row_type is not None and row_type.kind == LVTypeKind.ARRAY
    if (
        row_type is not None
        and not anonymous
        and row_type.ref_type != "UserEvent"
        and event_type == USER_EVENT_TYPE_CODE
    ):
        event_type = None
    return [
        _Event(f"{prefix}{name}" if name else None, event_type, anonymous, row_type)
    ]


def with_dynamic_names(
    frame: EventFrame,
    events: list[FrameEvent],
    rows: list[EventRegRow],
    displayed_frame: int | None,
) -> EventFrame:
    """``frame`` with each of its dynamic User Events named after its row, its part
    of the label written as LabVIEW writes it (``<Action.Event Data>: User
    Event``), and the frame's label rebuilt from every event's part
    (``[2] <Start>: User Event, "Stop": Value Change``). The frame LabVIEW last
    displayed keeps the label the file stores."""
    named: list[FrameEvent] = []
    for event in events:
        row = _row_of(event, rows)
        update_event: dict[str, object] = {"dynamic_name": row.name if row else None}
        if row is not None and row.name:
            update_event["label"] = f"<{row.name}>: User Event"
        named.append(event.model_copy(update=update_event))
    update: dict[str, object] = {"events": named}
    if any(event.dynamic_name for event in named) and frame.index != displayed_frame:
        update["event_label"] = frame_label(
            frame.index, [event.label for event in named]
        )
    return frame.model_copy(update=update)


def _row_of(event: FrameEvent, rows: list[EventRegRow]) -> EventRegRow | None:
    """The row a dynamic User Event is, when the type has it as a User Event row."""
    if event.type_code != USER_EVENT_TYPE_CODE or not event.dyn_index:
        return None
    if event.dyn_index > len(rows):
        return None
    row = rows[event.dyn_index - 1]
    return row if row.event_type == USER_EVENT_TYPE_CODE else None
