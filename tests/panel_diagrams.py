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


# The property code a Property Node row stores for the properties the tests name.
PROPERTY_CODES = {
    "Visible": 104013824,
    "Disabled": 104013825,
    "Key Focus": 104013826,
    "Value": 104013842,
    "Value (Signaling)": 104013860,
    "DisabledEnum": 104013885,
    "Minimum": 104050693,
    "Maximum": 104050694,
}


def value_property(
    vi: Vi,
    pane_term: int,
    rows: list[str | tuple[str, int] | tuple[tuple[str, ...], tuple[int, ...]]],
    *,
    inner: bool = False,
    class_name: str | None = None,
) -> list[int]:
    """A Property Node on the front-panel control or indicator whose terminal is
    ``pane_term``, with one row per entry of ``rows``: a property name
    (``"Value"``, ``"Visible"``) with its code in ``PROPERTY_CODES``, a
    ``(stored name, code)`` pair, or a row of several path segments as
    ``(stored names, codes)`` tuples. Returns the terminal of each row (a write
    when wired as a sink, a read when wired as a source). With ``inner`` the node
    is bound to an object inside the control (its label part), as for a
    type-defined control, not to the control's own object. ``class_name`` is the
    class the node stores (``"IntGraph (strict)"``); the control's label when
    omitted."""
    pane = next(p for p in vi.panes if vi.fp_terms[p.uid] == pane_term)
    stride = 2 + max(
        len(r[1]) if isinstance(r, tuple) and isinstance(r[1], tuple) else 1
        for r in rows
    )
    uid = vi.next_uid(1 + stride * len(rows))
    terms = ""
    dcos = ""
    terminals: list[int] = []
    for i, row in enumerate(rows):
        name, code = row if isinstance(row, tuple) else (row, PROPERTY_CODES[row])
        names = name if isinstance(name, tuple) else (name,)
        codes = code if isinstance(code, tuple) else (code,)
        term = uid + 1 + stride * i
        terminals.append(term)
        segments = "".join(
            f'<SL__arrayElement class="propItemInfo" uid="{term + 2 + s}">'
            f'<PropItemName>"{n}"</PropItemName><PropItemCode>{c}</PropItemCode>'
            "</SL__arrayElement>"
            for s, (n, c) in enumerate(zip(names, codes, strict=True))
        )
        terms += (
            f'<SL__arrayElement class="term" uid="{term}">'
            f'<dco class="propItem" uid="{term + 1}"><objFlags>1</objFlags>'
            f'<propList elements="{len(codes)}">{segments}'
            "</propList></dco></SL__arrayElement>"
        )
        dcos += f'<SL__arrayElement uid="{term + 1}" />'
    vi.scope.nodes.append(
        f'<SL__arrayElement class="propNode" uid="{uid}">'
        f'<ddo uid="{pane.uid}{2 if inner else 1}" />'
        f'<termList elements="{len(rows)}">{terms}</termList>'
        f'<dcoList elements="{len(rows)}">{dcos}</dcoList>'
        '<permDCOList elements="0"></permDCOList>'
        f'<nodeName>"{class_name or pane.label}"</nodeName></SL__arrayElement>'
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


def array_type(vi: Vi, element_type: int) -> int:
    """The type of a one-dimensional array of ``element_type``, added to ``vi``."""
    return vi.add_type(
        '<TypeDesc Type="Array" Format="inline">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        f'<TypeDesc TypeID="{element_type}" /></TypeDesc>'
    )


def array_element(vi: Vi, ddo_class: str, type_id: int, inner: str = "") -> str:
    """The heap xml of an array control's element, its class and type, with the
    element's own heap xml ``inner`` (a cluster's fields): what goes in ``inner``
    of the array's pane."""
    return (
        f'<ddo class="{ddo_class}" uid="{vi.next_uid()}">'
        "<bounds>(0, 0, 22, 80)</bounds>"
        f"<typeDesc>TypeID({type_id})</typeDesc>{inner}</ddo>"
    )


def shared_label_vi(name: str = "Shared Label.vi") -> Vi:
    """Two numeric controls both labelled ``Value`` and an indicator ``Sum`` that
    shows their sum."""
    vi = Vi(name)
    first = vi.control("Value", I32, REQUIRED, bounds=(40, 20, 62, 160))
    second = vi.control("Value", I32, REQUIRED, bounds=(110, 20, 132, 160))
    total = vi.indicator("Sum", I32, bounds=(40, 220, 62, 360))
    add = vi.primitive(
        ADD, [("sum", 0, I32, True), ("y", 1, I32, False), ("x", 2, I32, False)]
    )
    vi.wire(first, add["x"])
    vi.wire(second, add["y"])
    vi.wire(add["sum"], total)
    return vi
