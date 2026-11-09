"""User event nodes for the handmade VIs of ``tests.error_diagrams``: Create, Register
For Events, Generate, Unregister and Destroy, and a While Loop around an Event
Structure that handles the user events of a registration."""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.error_diagrams import BOOL, ERR, I32, Vi
from tests.event_diagrams import (
    _U32,
    USER_EVENT,
    EventStructure,
    FrameSpec,
    event_structure,
    timeout_frame,
)

CREATE, GENERATE, DESTROY, UNREGISTER = 2073, 2074, 2075, 2076

_USER_EVENT_REF = '<TypeDesc Type="Refnum" RefType="UserEvent" Label="{label}" />'
_BOOL_FIELD = '<TypeDesc Type="Boolean" Label="{label}" />'
_I32_FIELD = '<TypeDesc Type="NumInt32" Label="{label}" />'
FIELD_TYPES = {BOOL: _BOOL_FIELD, I32: _I32_FIELD}


def user_event_of(vi: Vi, name: str, data: int) -> int:
    """A User Event refnum type labelled ``name`` whose data has the type ``data``."""
    return vi.add_type(
        f'<TypeDesc Type="Refnum" RefType="UserEvent" Label="{name}">'
        f'<TypeDesc TypeID="{data}" /></TypeDesc>'
    )


def create_user_event(vi: Vi, user_event: int, data: int) -> dict[str, int]:
    """Create User Event: terminals ``data type``, ``error in``, ``user event``,
    ``error out``."""
    return vi.primitive(
        CREATE,
        [
            ("data type", 0, data, False),
            ("error in", 3, ERR, False),
            ("user event", 8, user_event, True),
            ("error out", 11, ERR, True),
        ],
    )


def generate_user_event(
    vi: Vi, user_event: int, data: int, *, priority: bool = False
) -> dict[str, int]:
    """Generate User Event: terminals ``user event``, ``data``, ``error in``,
    ``priority`` (only when asked for), ``user event out``, ``error out``."""
    slots = [
        ("user event", 0, user_event, False),
        ("data", 1, data, False),
        ("error in", 3, ERR, False),
    ]
    if priority:
        slots.append(("priority", 4, I32, False))
    slots += [("user event out", 8, user_event, True), ("error out", 11, ERR, True)]
    return vi.primitive(GENERATE, slots)


def unregister(vi: Vi, registration: int) -> dict[str, int]:
    """Unregister For Events: terminals ``registration``, ``error in``,
    ``error out``."""
    return vi.primitive(
        UNREGISTER,
        [
            ("registration", 0, registration, False),
            ("error in", 3, ERR, False),
            ("error out", 7, ERR, True),
        ],
    )


def destroy(vi: Vi, user_event: int) -> dict[str, int]:
    """Destroy User Event: terminals ``user event``, ``error in``, ``error out``."""
    return vi.primitive(
        DESTROY,
        [
            ("user event", 0, user_event, False),
            ("error in", 3, ERR, False),
            ("error out", 7, ERR, True),
        ],
    )


def register_node(
    vi: Vi,
    registration: int,
    rows: list[int],
    *,
    registration_in: int | None = None,
) -> dict[str, int]:
    """Register For Events with one row for each type in ``rows`` (``row 1``,
    ``row 2``, ...), in the layout of a real file: the four fixed terminals, then
    the rows, each a ``eventRegItem``. Terminals: ``registration in``,
    ``registration out``, ``error in``, ``error out``, ``row k``."""
    in_type = registration if registration_in is None else registration_in
    fixed = [
        ("registration in", 65536, in_type),
        ("registration out", 262145, registration),
        ("error in", 131072, ERR),
        ("error out", 524289, ERR),
    ]
    uids: dict[str, int] = {}
    xml = ""
    for name, flags, type_id in fixed:
        uid = vi.next_uid(2)
        uids[name] = uid
        xml += (
            f'<SL__arrayElement class="term" uid="{uid}"><objFlags>4096</objFlags>'
            f'<dco class="hGrowCItem" uid="{uid + 1}"><objFlags>{flags}</objFlags>'
            f"<typeDesc>TypeID({type_id})</typeDesc></dco></SL__arrayElement>"
        )
    row_dcos = []
    for number, type_id in enumerate(rows, start=1):
        uid = vi.next_uid(2)
        uids[f"row {number}"] = uid
        row_dcos.append(uid + 1)
        xml += (
            f'<SL__arrayElement class="term" uid="{uid}"><objFlags>36864</objFlags>'
            f'<dco class="eventRegItem" uid="{uid + 1}">'
            f"<typeDesc>TypeID({type_id})</typeDesc><code>03E8</code></dco>"
            "</SL__arrayElement>"
        )
    listed = "".join(f'<SL__arrayElement uid="{d}" />' for d in row_dcos)
    node = vi.next_uid()
    vi.scope.nodes.append(
        f'<SL__arrayElement class="eventRegNode" uid="{node}">'
        "<objFlags>131072</objFlags>"
        f'<termList elements="{len(fixed) + len(rows)}">{xml}</termList>'
        f'<dcoList elements="{len(rows)}">{listed}</dcoList>'
        '<nodeName>"Reg Events"</nodeName><oMId>0044</oMId></SL__arrayElement>'
    )
    return uids


def bundle_of(vi: Vi, cluster: int, members: list[int]) -> dict[str, int]:
    """A Bundle of values into the anonymous cluster type ``cluster``: terminals
    ``cluster`` (the output) and ``field 0``, ``field 1``, ... with the types
    ``members``."""
    agg = vi.next_uid(2)
    xml = (
        f'<SL__arrayElement class="term" uid="{agg}"><dco class="nmxDCO" '
        f'uid="{agg + 1}"><objFlags>1</objFlags><typeDesc>TypeID({cluster})'
        "</typeDesc></dco></SL__arrayElement>"
    )
    uids = {"cluster": agg}
    drawers = []
    for index, type_id in enumerate(members):
        uid = vi.next_uid(2)
        uids[f"field {index}"] = uid
        drawers.append(uid + 1)
        order = f"<i>{index}</i>" if index else ""
        xml += (
            f'<SL__arrayElement class="term" uid="{uid}"><dco class="nmxDCO" '
            f'uid="{uid + 1}"><typeDesc>TypeID({type_id})</typeDesc>{order}</dco>'
            "</SL__arrayElement>"
        )
    listed = "".join(f'<SL__arrayElement uid="{d}" />' for d in drawers)
    node = vi.next_uid()
    vi.scope.nodes.append(
        f'<SL__arrayElement class="nMux" uid="{node}">'
        f'<termList elements="{len(members) + 1}">{xml}</termList>'
        f'<dcoList elements="{len(members)}">{listed}</dcoList>'
        f'<dcoAgg uid="{agg + 1}" /></SL__arrayElement>'
    )
    return uids


@dataclass
class ListenFrame:
    """A frame of the Event Structure that handles the user event of the row
    ``dyn_index``: the fields of its Event Data Node after ``UsrEvtRef`` as
    ``(name, type id)`` and the indicators that show some of them,
    ``{field name: indicator terminal}``. ``stop`` ends the loop."""

    dyn_index: int
    data: list[tuple[str, int]] = field(default_factory=list)
    shows: dict[str, int] = field(default_factory=dict)
    stop: bool = True


@dataclass
class Listener:
    """The loop that waits for user events."""

    events: EventStructure
    registration: int  # the outer terminal of the registration's tunnel
    timed_out: int | None = None


def listen(
    vi: Vi,
    registration: int,
    frames: list[ListenFrame],
    *,
    after: list[int] = (),  # type: ignore[assignment]
    timeout: int | None = None,
    timed_out: int | None = None,
    extra: list[FrameSpec] = (),  # type: ignore[assignment]
) -> Listener:
    """A While Loop around an Event Structure whose dynamic terminal carries a
    registration of the type ``registration``: a Timeout frame (``timed_out`` is
    an indicator it sets), a frame for each of ``frames`` and the frames ``extra``
    (which carry on the loop). ``after`` are terminals the loop waits for (each
    wired to an input tunnel of the loop)."""
    loop = vi.loop(
        "whileLoop", tunnels=[(registration, False), *[(ERR, False) for _ in after]]
    )
    specs = [
        FrameSpec(
            USER_EVENT,
            [
                ("Source", _U32),
                ("Type", _U32),
                ("Time", _U32),
                ("UsrEvtRef", _USER_EVENT_REF),
                *[(name, FIELD_TYPES[type_id]) for name, type_id in frame.data],
            ],
            read=list(frame.shows),
            dyn_index=frame.dyn_index,
        )
        for frame in frames
    ]
    with vi.inside(loop.scopes[0]):
        events = event_structure(
            vi,
            [timeout_frame(), *specs, *extra],
            outputs=[BOOL],
            dynamic=registration,
        )
        wait = vi.int_constant(timeout) if timeout is not None else None
    if wait is not None:
        vi.wire(wait, events.timeout)
    vi.wire(loop.tunnels[0].faces[0], events.dynamic)  # type: ignore[arg-type]
    vi.wire(events.outputs[0].outer, loop.faces[0])
    for source, tunnel in zip(after, loop.tunnels[1:]):
        vi.wire(source, tunnel.outer)
    timeout_terms, *user_terms = events.frames
    carried_on = user_terms[len(frames) :]
    user_terms = user_terms[: len(frames)]
    stop = events.outputs[0]
    with vi.inside(timeout_terms.scope):
        vi.wire(vi.bool_constant(True), stop.faces[0])
        if timed_out is not None:
            vi.place_inside(timed_out, timeout_terms.scope)
            vi.wire(vi.bool_constant(True), timed_out)
    for index, (frame, terms) in enumerate(zip(frames, user_terms), start=1):
        with vi.inside(terms.scope):
            vi.wire(vi.bool_constant(frame.stop), stop.faces[index])
        for name, indicator in frame.shows.items():
            vi.place_inside(indicator, terms.scope)
            vi.wire(terms.data[name], indicator)
    for offset, terms in enumerate(carried_on, start=1 + len(frames)):
        with vi.inside(terms.scope):
            vi.wire(vi.bool_constant(False), stop.faces[offset])
    return Listener(events, loop.tunnels[0].outer, timed_out)
