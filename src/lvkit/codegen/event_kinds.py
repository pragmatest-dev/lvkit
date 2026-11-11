"""The events of an Event Structure that generated code handles.

A VI that holds an Event Structure takes a queue of the front panel's events as
``events``. A message is ``(name, new_value, kind, old_value)``: the name of the
control the event comes from (the member of the VI's name enum, empty for an
event with no control), the value it holds after the event, the kind of event
(a member of the VI's event enum, below) and the value before. A filter event
(Panel Close?) has a fifth item, a ``queue.Queue`` the frame answers on with
``(discard, modified_value)``. A Timeout is not a message: it is the queue
staying empty for the time the Timeout terminal gives.

The kinds are named by the event type codes of ``CONFIRMED_EVENT_TYPES``; any
other code is not handled (``unsupported_events``). A user event's message is
``(the user event, its data, "User Event", None)``: the user event itself, the list
of the queues registered for it, takes the name's place.
"""

from __future__ import annotations

from dataclasses import dataclass

from lvkit.models import EventFrame, FrameEvent
from lvkit.parser.nodes.event import CONFIRMED_EVENT_TYPES

TIMEOUT_CODE = 1073741825
VALUE_CHANGE_CODE = 1073741826
PANEL_CLOSE_CODE = -2147483645
USER_EVENT_CODE = 1000

# The kind of a user event's message. Generate User Event writes it as this text,
# so a VI with no Event Structure needs no enum for it.
USER_EVENT_NAME = "User Event"

# The positions of a message.
NAME, NEW_VALUE, KIND, OLD_VALUE, REPLY = range(5)

# The queue of the events and the exception that ends a wait for one.
QUEUE_IMPORT = "from queue import Empty, Queue"


@dataclass(frozen=True)
class MessageKind:
    """A kind of event that arrives as a message: its type code, the member of
    the event enum that names it, whether a frame answers it (a filter event),
    which fields of the event's data cluster the message holds and, for a filter
    event, which field its Event Filter Node reads as Discard?. ``label`` is the
    event's name when ``CONFIRMED_EVENT_TYPES`` (which names frame labels) does
    not hold it.

    ``data`` lists the fields of the Event Data Node's cluster the message
    holds as ``(the field's name in the cluster, position in the message)``. The
    index of a field in the cluster differs with the control (a hidden field
    comes before OldVal on some), so a field is found by the name its cluster
    gives it. ``discard`` is the name of the Discard? field."""

    code: int
    member: str
    filter: bool
    data: tuple[tuple[str, int], ...] = ()
    discard: str | None = None
    label: str | None = None

    @property
    def name(self) -> str:
        """The event's name, the value of its enum member."""
        return self.label or CONFIRMED_EVENT_TYPES[self.code]


MESSAGE_KINDS = (
    MessageKind(
        VALUE_CHANGE_CODE,
        "VALUE_CHANGE",
        filter=False,
        data=(("OldVal", OLD_VALUE), ("NewVal", NEW_VALUE)),
    ),
    MessageKind(PANEL_CLOSE_CODE, "PANEL_CLOSE", filter=True, discard="Discard?"),
    MessageKind(
        USER_EVENT_CODE,
        "USER_EVENT",
        filter=False,
        data=(("UsrEvtRef", NAME),),
        label=USER_EVENT_NAME,
    ),
)


def message_kind(event: FrameEvent) -> MessageKind | None:
    """The message kind of ``event``, None for a Timeout or a kind that is not
    handled."""
    return next((k for k in MESSAGE_KINDS if k.code == event.type_code), None)


def unsupported_events(frames: list[EventFrame]) -> list[str]:
    """What the events of ``frames`` that are not handled are called: their name,
    or their code when the name is not known."""
    found: list[str] = []
    for frame in frames:
        for event in frame.events:
            if event.dyn_index is None and event.type_code == USER_EVENT_CODE:
                pass
            elif event.type_code in (TIMEOUT_CODE, *(k.code for k in MESSAGE_KINDS)):
                continue
            code = event.type_code
            name = CONFIRMED_EVENT_TYPES.get(code) if code is not None else None
            text = name or (
                f"event code 0x{code & 0xFFFFFFFF:08X}" if code is not None else "?"
            )
            if text not in found:
                found.append(text)
    return found
