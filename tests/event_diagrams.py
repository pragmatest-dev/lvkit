"""Event Structures for the handmade VIs of ``tests.error_diagrams``: the border
tunnels, one frame per ``FrameSpec`` with its Event Data Node (and Event Filter
Node for a Panel Close?), and the events the file lists for each frame."""

from __future__ import annotations

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
    Data Node cluster, ``(name, type desc)``. ``read`` lists the fields the frame's
    Event Data Node outputs, ``(name, cluster index)``; ``filter`` gives the frame
    an Event Filter Node with a Discard? input."""

    type_code: int
    fields: list[tuple[str, str]]
    sources: list[int] = field(default_factory=list)
    read: list[tuple[str, int]] = field(default_factory=list)
    filter: bool = False
    # Each further event the frame handles besides the first.
    more: list[tuple[int, int | None]] = field(default_factory=list)


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


def timeout_frame(*, read: bool = False) -> FrameSpec:
    return FrameSpec(
        TIMEOUT,
        [("Source", _U32), ("Type", _U32), ("Time", _U32)],
        read=[("Type", 1), ("Time", 2)] if read else [],
    )


def value_change_frame(
    sources: list[int], value_type: str, *, old: bool = True, new: bool = True
) -> FrameSpec:
    """A Value Change frame on the controls ``sources``; ``value_type`` is the
    ``<TypeDesc>`` of the controls' value."""
    read = ([("OldVal", 5)] if old else []) + ([("NewVal", 6)] if new else [])
    return FrameSpec(
        VALUE_CHANGE,
        [
            ("Source", _U32),
            ("Type", _U32),
            ("Time", _U32),
            ("CtlRef", _U32),
            ("", _U32),
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
) -> EventStructure:
    """An Event Structure with ``frames``; ``outputs`` are the type ids of its
    output tunnels. Returns the timeout input terminal, each frame's terminals
    and the output tunnels."""
    uid = vi.next_uid()
    scopes = [vi._new_scope() for _ in frames]
    timeout, timeout_xml = vi.tunnel_terminal("eventTimeOut", 1, scopes)
    borders = [timeout_xml]
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
        for code, source in events:
            ddo = _ddo_uid(vi, source) if source is not None else 0
            count += 1
            specs += (
                '<SL__arrayElement class="EventSpec">'
                f"<diagramIdx>{index}</diagramIdx><ddoUID>{ddo}</ddoUID>"
                f"<type>{code}</type></SL__arrayElement>"
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
        timeout.outer, [t[0] for t in terms], made, structure
    )


def event_loop_vi(
    *,
    timeout: int | None = 50,
    discard: bool = False,
    read_gain: bool = False,
    extra: FrameSpec | None = None,
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
                value_change_frame([gain, offset], _I32),
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
    for name, index in spec.read:
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
    return FrameSpec(
        MOUSE_DOWN, [("Source", _U32), ("Type", _U32), ("Time", _U32)]
    )
