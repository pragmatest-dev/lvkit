"""Event Structures for the handmade VIs of ``tests.error_diagrams``: the border
tunnels, one frame per ``FrameSpec`` with its Event Data Node (and Event Filter
Node for a Panel Close?), and the events the file lists for each frame."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tests.error_diagrams import BOOL, I32, REQUIRED, Scope, Structure, Tunnel, Vi

TIMEOUT = 1073741825
VALUE_CHANGE = 1073741826
PANEL_CLOSE = -2147483645
MOUSE_DOWN = 1073741842

_U32 = '<TypeDesc Type="NumUInt32" Label="{label}" />'
_I32 = '<TypeDesc Type="NumInt32" Label="{label}" />'


@dataclass
class FrameSpec:
    """One frame: the events it handles (``sources`` are the diagram terminals of
    the controls, none for an event with no control) and the fields of its Event
    Data Node cluster, ``(name, type desc)``. ``read`` names the fields the frame's
    Event Data Node outputs; ``filter`` gives the frame
    an Event Filter Node with a Discard? input."""

    type_code: int
    fields: list[tuple[str, str]]
    sources: list[int] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    filter: bool = False
    # Each further event the frame handles besides the first.
    more: list[tuple[int, int | None]] = field(default_factory=list)
    # The row of the registration on the dynamic terminal the first event is
    # (``dynIndex``); 0 for a static event.
    dyn_index: int = 0


@dataclass
class EventFrameTerms:
    """The terminals a test wires inside one frame."""

    scope: Scope
    data: dict[str, int] = field(default_factory=dict)
    discard: int | None = None


@dataclass
class EventStructure:
    """An Event Structure under construction."""

    timeout: int
    frames: list[EventFrameTerms]
    outputs: list[Tunnel]
    structure: Structure
    dynamic: int | None = None


def timeout_frame(*, read: bool = False) -> FrameSpec:
    return FrameSpec(
        TIMEOUT,
        [("Source", _U32), ("Type", _U32), ("Time", _U32)],
        read=["Type", "Time"] if read else [],
    )


def value_change_frame(
    sources: list[int],
    value_type: str,
    *,
    old: bool = True,
    new: bool = True,
    hidden_field: bool = True,
) -> FrameSpec:
    """A Value Change frame on the controls ``sources``; ``value_type`` is the
    ``<TypeDesc>`` of the controls' value; without ``hidden_field`` the cluster has
    no unnamed field before OldVal (the index of OldVal and NewVal differs)."""
    read = (["OldVal"] if old else []) + (["NewVal"] if new else [])
    return FrameSpec(
        VALUE_CHANGE,
        [
            ("Source", _U32),
            ("Type", _U32),
            ("Time", _U32),
            ("CtlRef", _U32),
            *([("", _U32)] if hidden_field else []),
            ("OldVal", value_type),
            ("NewVal", value_type),
        ],
        sources=sources,
        read=read,
    )


def panel_close_frame() -> FrameSpec:
    return FrameSpec(
        PANEL_CLOSE,
        [
            ("Source", _U32),
            ("Type", _U32),
            ("Time", _U32),
            ("VIRef", _U32),
            ("Discard?", '<TypeDesc Type="Boolean" Label="{label}" />'),
        ],
        filter=True,
    )


def _label(template: str, name: str) -> str:
    if name:
        return template.format(label=name)
    return template.replace(' Label="{label}"', "")


def event_structure(
    vi: Vi,
    frames: list[FrameSpec],
    *,
    outputs: list[int] = (),  # type: ignore[assignment]
    dynamic: int | None = None,
) -> EventStructure:
    """An Event Structure with ``frames``; ``outputs`` are the type ids of its
    output tunnels; ``dynamic`` is the type id of the registration on its dynamic
    terminal. Returns the timeout input terminal, each frame's terminals
    and the output tunnels."""
    uid = vi.next_uid()
    scopes = [vi._new_scope() for _ in frames]
    timeout, timeout_xml = vi.tunnel_terminal("eventTimeOut", 1, scopes)
    borders = [timeout_xml]
    dynamic_outer = None
    if dynamic is not None:
        dyn_tunnel, dyn_xml = vi.tunnel_terminal("eventDynDCO", dynamic, scopes)
        dynamic_outer = dyn_tunnel.outer
        borders.append(dyn_xml)
    made: list[Tunnel] = []
    for type_id in outputs:
        tunnel, xml = vi.tunnel_terminal("selTun", type_id, scopes, output=True)
        made.append(tunnel)
        borders.append(xml)
    terms = [_frame_nodes(vi, spec, scope) for spec, scope in zip(frames, scopes)]
    data_uids = [t[1] for t in terms]
    filter_uids = [t[2] for t in terms]
    specs = ""
    count = 0
    for index, spec in enumerate(frames):
        events: list[tuple[int, int | None]] = [
            *((spec.type_code, source) for source in spec.sources or [None]),
            *spec.more,
        ]
        for position, (code, source) in enumerate(events):
            ddo = _ddo_uid(vi, source) if source is not None else 0
            dyn = f"<dynIndex>{spec.dyn_index}</dynIndex>" if position == 0 else ""
            count += 1
            specs += (
                '<SL__arrayElement class="EventSpec">'
                f"<diagramIdx>{index}</diagramIdx><ddoUID>{ddo}</ddoUID>"
                f"<type>{code}</type>{dyn}</SL__arrayElement>"
            )

    def listed(uids: list[int]) -> str:
        return "".join(f'<SL__arrayElement uid="{u}" />' for u in uids)

    def xml() -> str:
        diagrams = "".join(vi._diagram_xml(sc) for sc in scopes)
        return (
            f'<SL__arrayElement class="eventStruct" uid="{uid}">'
            f'<termList elements="{len(borders)}">{"".join(borders)}</termList>'
            f'<diagramList elements="{len(scopes)}">{diagrams}</diagramList>'
            f'<EventNodeEvents elements="{count}">{specs}</EventNodeEvents>'
            f'<dataNodeList elements="{len(scopes)}">{listed(data_uids)}'
            "</dataNodeList>"
            f'<filterNodeList elements="{len(scopes)}">{listed(filter_uids)}'
            "</filterNodeList></SL__arrayElement>"
        )

    structure = Structure(timeout.outer, [], scopes, made, xml=xml)
    vi._place(structure)
    return EventStructure(
        timeout.outer, [t[0] for t in terms], made, structure, dynamic_outer
    )


def event_loop_vi(
    *,
    timeout: int | None = 50,
    discard: bool = False,
    read_gain: bool = False,
    extra: FrameSpec | None = None,
    hidden_field: bool = True,
) -> Vi:
    """A While Loop around an Event Structure with three frames: Timeout (sets
    ``Timed Out`` and ends the loop), Value Change on ``Gain`` and ``Offset`` (shows
    the event's NewVal and OldVal in ``Last New`` and ``Last Old``, and carries on)
    and Panel Close? (sets ``Closed``; its Event Filter Node's Discard? is ``discard``,
    and the loop ends unless it is). With ``timeout`` None the Timeout terminal is
    unwired. With ``read_gain`` the Value Change frame also reads the ``Gain``
    control's terminal and shows it in ``Seen Gain``. ``extra`` adds a fourth
    frame."""
    vi = Vi("Events.vi")
    gain = vi.control("Gain", I32, REQUIRED, bounds=(40, 20, 62, 160))
    offset = vi.control("Offset", I32, REQUIRED, bounds=(80, 20, 102, 160))
    last_new = vi.indicator("Last New", I32, bounds=(40, 200, 62, 340))
    last_old = vi.indicator("Last Old", I32, bounds=(80, 200, 102, 340))
    timed_out = vi.indicator(
        "Timed Out", BOOL, ddo_class="stdBool", bounds=(120, 200, 150, 320)
    )
    closed = vi.indicator(
        "Closed", BOOL, ddo_class="stdBool", bounds=(160, 200, 190, 320)
    )
    seen = (
        vi.indicator("Seen Gain", I32, bounds=(200, 200, 222, 340))
        if read_gain
        else None
    )
    loop = vi.loop("whileLoop")
    scope = loop.scopes[0]
    with vi.inside(scope):
        events = event_structure(
            vi,
            [
                timeout_frame(),
                value_change_frame([gain, offset], _I32, hidden_field=hidden_field),
                panel_close_frame(),
                *([extra] if extra else []),
            ],
            outputs=[BOOL],
        )
        wait = vi.int_constant(timeout) if timeout is not None else None
    stop = events.outputs[0]
    if wait is not None:
        vi.wire(wait, events.timeout)
    vi.wire(stop.outer, loop.faces[0])
    timeout_terms, change_terms, close_terms, *_ = events.frames
    placements = [
        (timeout_terms, [timed_out]),
        (change_terms, [last_new, last_old, *([seen, gain] if seen else [])]),
        (close_terms, [closed]),
    ]
    for terms, panes in placements:
        for pane in panes:
            vi.place_inside(pane, terms.scope)
    vi.wire(change_terms.data["NewVal"], last_new)
    vi.wire(change_terms.data["OldVal"], last_old)
    if seen is not None:
        vi.wire(gain, seen)
    ends = [(timeout_terms, True), (change_terms, False), (close_terms, not discard)]
    for index, (terms, value) in enumerate(ends):
        with vi.inside(terms.scope):
            vi.wire(vi.bool_constant(value), stop.faces[index])
    with vi.inside(timeout_terms.scope):
        vi.wire(vi.bool_constant(True), timed_out)
    with vi.inside(close_terms.scope):
        vi.wire(vi.bool_constant(True), closed)
        assert close_terms.discard is not None
        vi.wire(vi.bool_constant(discard), close_terms.discard)
    return vi


USER_EVENT = 1000


def user_event_frame(dyn_index: int) -> FrameSpec:
    """A frame handling the dynamic User Event that is row ``dyn_index`` of the
    registration on the structure's dynamic terminal."""
    return FrameSpec(USER_EVENT, [("Source", _U32)], dyn_index=dyn_index)


def user_event_type(vi: Vi, name: str) -> int:
    """A User Event refnum type labelled ``name`` (the row name a registration
    stores), carrying an I32."""
    return vi.add_type(
        f'<TypeDesc Type="Refnum" RefType="UserEvent" Label="{name}">'
        f'<TypeDesc TypeID="{I32}" /></TypeDesc>'
    )


def registration_type(vi: Vi, rows: list[tuple[str, int]], label: str = "") -> int:
    """An Event Registration refnum type with one row for each ``(name, event type
    code)``; a row's user event type is labelled ``name``."""
    members = "".join(
        f'<TypeDesc TypeID="{user_event_type(vi, name)}" CField0="0x0001" '
        f'CField2="0x0000" CField4="0x{code:04X}" />'
        for name, code in rows
    )
    tag = f' Label="{label}"' if label else ""
    return vi.add_type(
        f'<TypeDesc Type="Refnum" RefType="EventReg"{tag}>{members}</TypeDesc>'
    )


def registrations_cluster(vi: Vi, fields: list[tuple[str, list[str]]]) -> int:
    """A cluster of registrations: each ``(field name, user event names)`` is a
    registration of those user events."""
    ids = [
        registration_type(vi, [(name, USER_EVENT) for name in names], label)
        for label, names in fields
    ]
    members = "".join(f'<TypeDesc TypeID="{type_id}" />' for type_id in ids)
    return vi.add_type(f'<TypeDesc Type="Cluster">{members}</TypeDesc>')


def dynamic_events_vi(
    make_registration: Callable[[Vi], int],
    frames: list[FrameSpec],
    *,
    in_loop: bool = False,
) -> Vi:
    """An Event Structure whose dynamic terminal is wired from a control of the
    type ``make_registration`` adds to the VI: straight, or through a While Loop's
    input tunnel."""
    vi = Vi("Dynamic.vi")
    registration = make_registration(vi)
    control = vi.control(
        "Registration", registration, REQUIRED, bounds=(40, 20, 62, 160)
    )
    if not in_loop:
        events = event_structure(vi, [timeout_frame(), *frames], dynamic=registration)
        vi.wire(control, events.dynamic)
        return vi
    loop = vi.loop("whileLoop", tunnels=[(registration, False)])
    with vi.inside(loop.scopes[0]):
        events = event_structure(vi, [timeout_frame(), *frames], dynamic=registration)
    vi.wire(control, loop.tunnels[0].outer)
    vi.wire(loop.tunnels[0].faces[0], events.dynamic)
    return vi


def _ddo_uid(vi: Vi, pane_term: int) -> str:
    pane = next(p for p in vi.panes if vi.fp_terms[p.uid] == pane_term)
    return f"{pane.uid}1"


def _frame_nodes(
    vi: Vi, spec: FrameSpec, scope: Scope
) -> tuple[EventFrameTerms, int, int]:
    """Add the Event Data Node (and Event Filter Node) of ``spec`` to ``scope``;
    the terminals, the data node's uid and the filter node's (0 for none)."""
    field_ids = [
        vi.add_type(_label(type_desc, name)) for name, type_desc in spec.fields
    ]
    children = "".join(f'<TypeDesc TypeID="{t}" />' for t in field_ids)
    cluster = vi.add_type(f'<TypeDesc Type="Cluster">{children}</TypeDesc>')
    terms = EventFrameTerms(scope)
    data_uid = vi.next_uid()
    agg = vi.next_uid(2)
    body = (
        f'<SL__arrayElement class="term" uid="{agg}"><dco class="nmxDCO" '
        f'uid="{agg + 1}"><typeDesc>TypeID({cluster})</typeDesc></dco>'
        "</SL__arrayElement>"
    )
    drawers = []
    for name in spec.read:
        index = [n for n, _ in spec.fields].index(name)
        t = vi.next_uid(2)
        terms.data[name] = t
        drawers.append(t + 1)
        order = f"<i>{index}</i>" if index else ""
        body += (
            f'<SL__arrayElement class="term" uid="{t}"><dco class="nmxDCO" '
            f'uid="{t + 1}"><objFlags>1</objFlags>'
            f"<typeDesc>TypeID({field_ids[index]})</typeDesc>{order}</dco>"
            "</SL__arrayElement>"
        )
    listed = "".join(f'<SL__arrayElement uid="{d}" />' for d in drawers)
    scope.nodes.append(
        f'<SL__arrayElement class="eventDataNode" uid="{data_uid}">'
        f'<termList elements="{len(spec.read) + 1}">{body}</termList>'
        f'<dcoList elements="{len(drawers)}">{listed}</dcoList>'
        f'<dcoAgg uid="{agg + 1}" /></SL__arrayElement>'
    )
    filter_uid = 0
    if spec.filter:
        filter_uid = vi.next_uid()
        discard_index = next(
            i for i, (name, _) in enumerate(spec.fields) if name == "Discard?"
        )
        out_term, in_term, discard, agg_dco, discard_dco = (
            vi.next_uid(),
            vi.next_uid(),
            vi.next_uid(),
            vi.next_uid(),
            vi.next_uid(),
        )
        terms.discard = discard
        scope.nodes.append(
            f'<SL__arrayElement class="eventDataNode" uid="{filter_uid}">'
            '<termList elements="3">'
            f'<SL__arrayElement class="term" uid="{out_term}">'
            "<objFlags>8392768</objFlags>"
            f'<dco class="nmxDCO" uid="{agg_dco}"><objFlags>1</objFlags>'
            f'<termList elements="2"><SL__arrayElement uid="{out_term}" />'
            f'<SL__arrayElement uid="{in_term}" /></termList>'
            f"<typeDesc>TypeID({cluster})</typeDesc></dco></SL__arrayElement>"
            f'<SL__arrayElement class="term" uid="{in_term}">'
            f'<objFlags>8523776</objFlags><dco uid="{agg_dco}" />'
            "</SL__arrayElement>"
            f'<SL__arrayElement class="term" uid="{discard}">'
            "<objFlags>36928</objFlags>"
            f'<dco class="nmxDCO" uid="{discard_dco}">'
            f"<typeDesc>TypeID({field_ids[discard_index]})</typeDesc>"
            f"<i>{discard_index}</i></dco></SL__arrayElement></termList>"
            f'<dcoList elements="1"><SL__arrayElement uid="{discard_dco}" />'
            f'</dcoList><dcoAgg uid="{agg_dco}" /></SL__arrayElement>'
        )
    return terms, data_uid, filter_uid


def mouse_down_frame() -> FrameSpec:
    """A frame of an event lvkit does not generate code for."""
    return FrameSpec(MOUSE_DOWN, [("Source", _U32), ("Type", _U32), ("Time", _U32)])
