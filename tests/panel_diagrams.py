"""Small VIs for the front-panel tests and the demo: a While Loop that counts
every 100 ms until its Stop control is set."""

from __future__ import annotations

from tests.error_diagrams import BOOL, I32, REQUIRED, STR, Vi, increment

ADD = 1050
WAIT_MS = 1302

NAME = "Panel Demo.vi"


def counting_vi(name: str = NAME, *, with_text: bool = False) -> Vi:
    """``Stop`` and ``Offset`` are controls read on every pass; ``Count`` shows the
    counter plus ``Offset`` on every pass. With ``with_text``, a string control
    ``Message`` inside the loop is wired to the string indicator ``Echo`` inside
    it (so ``Echo`` shows ``Message`` on every pass), and the Boolean indicator
    ``Ready`` outside the loop is True."""
    vi = Vi(name)
    stop = vi.control(
        "Stop", BOOL, REQUIRED, ddo_class="stdBool", bounds=(40, 20, 70, 120)
    )
    offset = vi.control("Offset", I32, REQUIRED, bounds=(110, 20, 132, 160))
    count = vi.indicator("Count", I32, bounds=(40, 160, 62, 300))
    zero = vi.int_constant(0)
    loop = vi.loop("whileLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(zero, register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
        total = vi.primitive(
            ADD, [("sum", 0, I32, True), ("y", 1, I32, False), ("x", 2, I32, False)]
        )
        period = vi.int_constant(100)
        wait = vi.primitive(
            WAIT_MS, [("elapsed", 0, I32, True), ("milliseconds", 1, I32, False)]
        )
    for terminal in (stop, offset, count):
        vi.place_inside(terminal, loop.scopes[0])
    vi.wire(register.left_face, inc["in"])
    vi.wire(inc["out"], register.right_face, total["x"])
    vi.wire(offset, total["y"])
    vi.wire(total["sum"], count)
    vi.wire(period, wait["milliseconds"])
    vi.wire(stop, loop.faces[0])
    if with_text:
        message = vi.control(
            "Message", STR, REQUIRED, ddo_class="stdString", bounds=(180, 20, 202, 260)
        )
        echo = vi.indicator(
            "Echo", STR, ddo_class="stdString", bounds=(180, 300, 202, 540)
        )
        ready = vi.indicator(
            "Ready", BOOL, ddo_class="stdBool", bounds=(40, 340, 70, 440)
        )
        vi.place_inside(message, loop.scopes[0])
        vi.place_inside(echo, loop.scopes[0])
        vi.wire(message, echo)
        vi.wire(vi.bool_constant(True), ready)
    return vi


def value_property(
    vi: Vi, pane_term: int, rows: list[str], *, inner: bool = False
) -> list[int]:
    """A Property Node on the front-panel control or indicator whose terminal is
    ``pane_term``, with one row per name in ``rows`` (``"Value"``, ``"Visible"``).
    Returns the terminal of each row (a write when wired as a sink, a read when
    wired as a source). With ``inner`` the node is bound to an object inside the
    control (its label part), as for a type-defined control, not to the control's
    own object."""
    pane = next(p for p in vi.panes if vi.fp_terms[p.uid] == pane_term)
    uid = vi.next_uid(1 + 3 * len(rows))
    terms = ""
    dcos = ""
    terminals: list[int] = []
    for i, name in enumerate(rows):
        term = uid + 1 + 3 * i
        terminals.append(term)
        terms += (
            f'<SL__arrayElement class="term" uid="{term}">'
            f'<dco class="propItem" uid="{term + 1}"><objFlags>1</objFlags>'
            '<propList elements="1">'
            f'<SL__arrayElement class="propItemInfo" uid="{term + 2}">'
            f'<PropItemName>"{name}"</PropItemName><PropItemCode>{i}</PropItemCode>'
            "</SL__arrayElement></propList></dco></SL__arrayElement>"
        )
        dcos += f'<SL__arrayElement uid="{term + 1}" />'
    vi.scope.nodes.append(
        f'<SL__arrayElement class="propNode" uid="{uid}">'
        f'<ddo uid="{pane.uid}{2 if inner else 1}" />'
        f'<termList elements="{len(rows)}">{terms}</termList>'
        f'<dcoList elements="{len(rows)}">{dcos}</dcoList>'
        '<permDCOList elements="0"></permDCOList>'
        f'<nodeName>"{pane.label}"</nodeName></SL__arrayElement>'
    )
    return terminals


def cluster_type(vi: Vi, field_types: list[int]) -> int:
    """The type of an anonymous cluster of the fields of ``field_types``, added to
    ``vi``."""
    fields = "".join(f'<TypeDesc TypeID="{t}" />' for t in field_types)
    return vi.add_type(f'<TypeDesc Type="Cluster">{fields}</TypeDesc>')


def cluster_fields(vi: Vi, fields: list[tuple[str, str, int]]) -> str:
    """The heap xml of a cluster control's fields, ``(label, ddo class, type id)``
    each, in cluster order: what goes in ``inner`` of the cluster's pane."""
    uids = [vi.next_uid(3) for _ in fields]
    order = "".join(f'<SL__arrayElement uid="{uid}" />' for uid in uids)
    controls = "".join(
        f'<SL__arrayElement class="{ddo_class}" uid="{uid}">'
        f"<typeDesc>TypeID({type_id})</typeDesc><partsList>"
        f'<SL__arrayElement class="label" uid="{uid + 1}">'
        f'<textRec class="textHair"><text>"{label}"</text></textRec>'
        "</SL__arrayElement></partsList></SL__arrayElement>"
        for uid, (label, ddo_class, type_id) in zip(uids, fields, strict=True)
    )
    return (
        f"<ddoList>{order}</ddoList>"
        f'<paneHierarchy class="pane" uid="{vi.next_uid()}">'
        f"<zPlaneList>{controls}</zPlaneList></paneHierarchy>"
    )
