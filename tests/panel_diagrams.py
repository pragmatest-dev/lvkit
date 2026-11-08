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
