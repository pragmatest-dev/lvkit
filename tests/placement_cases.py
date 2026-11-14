"""Diagrams with front-panel terminals inside a structure: control ``Gain`` (read
in the first body), indicator ``Rate`` (driven by 3 in the second) and, on the VI's
own diagram, indicator ``Total`` (driven by 9).

``placement_xml(kind)`` writes the diagram for a structure kind; ``FRONT_PANEL``
is the front-panel heap its terminals refer to (write it to ``T_FPHb.xml``)."""

from __future__ import annotations

from tests.case_structure_fixture import FrameSpec, _selector_xml
from tests.structure_diagrams import (
    BOOL,
    EVENT_TIMEOUT,
    EVENT_VALUE_CHANGE,
    I32,
    constant,
    dco,
    diag,
    disable_structure,
    event_structure,
    face,
    flat_sequence,
    fp_terminal,
    frame_diagram,
    front_panel_xml,
    loop,
    root_diagram,
    srn,
    stacked_sequence,
    term,
    wire,
)

# Rate and Total are on the connector pane (slots 0 and 1); Gain is not.
_BOOLEAN_FRAMES = [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))]
FRONT_PANEL = front_panel_xml(
    {10: "Gain"}, {20: "Rate", 30: "Total"}, pane={0: 20, 1: 30}
)
GAIN, RATE, TOTAL = 5010, 5020, 5030  # the diagram's terminals of the three

FIRST = [fp_terminal(GAIN, 10)]
SECOND = [constant(1040, I32, 3), fp_terminal(RATE, 20)]
_ROOT = [constant(709, I32, 9), fp_terminal(TOTAL, 30)]
_WIRES = [wire(902, 1040, RATE), wire(903, 709, TOTAL)]


def _frames(uid: int, first: list[str], second: list[str]) -> list[str]:
    return [
        frame_diagram(uid + 1, uid + 2, first),
        frame_diagram(uid + 11, uid + 12, second),
    ]


def _diagram(structure: str) -> str:
    return root_diagram([structure], [srn(800, _ROOT)], _WIRES)


def case_in(frames: list[str] | None = None) -> str:
    """A Boolean Case (600) with ``frames`` (default: Gain in False, Rate in True)."""
    selector = dco("caseSel", 602, type_id=BOOL, faces=[611, 612, 601])
    first, second = (
        frames
        or _frames(700, [face(611, 602), *FIRST], [face(612, 602), *SECOND])
    )
    return (
        '<SL__arrayElement class="caseStruct" uid="600">'
        f'<termList elements="1">{term(601, selector)}</termList>'
        f'<diagramList elements="2">{first}{second}</diagramList>'
        f'{_selector_xml("boolean", _BOOLEAN_FRAMES, True)}'
        "</SL__arrayElement>"
    )


def case_in_without_terminals() -> str:
    """The Case of ``case_in`` with no front-panel terminal in either frame (Total
    stays on the diagram)."""
    frames = _frames(700, [face(611, 602)], [face(612, 602)])
    return root_diagram([case_in(frames)], [srn(800, _ROOT)], [_WIRES[1]])


def placement_xml(kind: str) -> str:
    if kind == "case":
        return _diagram(case_in())
    if kind in ("whileLoop", "forLoop"):
        inner = frame_diagram(450, 460, [*FIRST, *SECOND])
        return _diagram(loop(kind, 400, [], inner))
    if kind == "event":
        first, second = _frames(4100, FIRST, SECOND)
        return _diagram(
            event_structure(
                4000, [], [first, second], [EVENT_TIMEOUT, EVENT_VALUE_CHANGE]
            )
        )
    if kind == "flat":
        f0 = (1100, [], frame_diagram(1101, 1102, FIRST))
        f1 = (1200, [], frame_diagram(1201, 1202, SECOND))
        return _diagram(flat_sequence(1000, [f0, f1]))
    if kind == "stacked":
        return _diagram(stacked_sequence(2000, [], _frames(2100, FIRST, SECOND)))
    if kind == "disable":
        return _diagram(disable_structure(3000, [], _frames(3100, FIRST, SECOND)))
    if kind == "inplace":
        inner = frame_diagram(5101, 5102, [*FIRST, *SECOND])
        return _diagram(
            '<SL__arrayElement class="decomposeRecomposeStructure" uid="5000">'
            '<termList elements="0" />'
            f'<diagramList elements="1">{inner}</diagramList></SL__arrayElement>'
        )
    raise ValueError(kind)


def nested_xml() -> str:
    """A While Loop (400) inside frame True of a Case (600); Rate is driven in the
    loop body, Gain is read in frame False."""
    inner = frame_diagram(450, 460, SECOND)
    nested = loop("whileLoop", 400, [], inner)
    outer_false = frame_diagram(711, 712, [face(611, 602), *FIRST])
    outer_true = diag(
        721, nodes=[srn(722, [face(612, 602)])], structures=[nested]
    )
    return _diagram(case_in([outer_false, outer_true]))


def shared_label_xml(*, indicators: bool) -> str:
    """Two terminals labelled ``x`` -- indicators, or controls -- one (dco 30) on the
    VI's own diagram and one (dco 20) in frame True of a Case. An indicator is
    driven (1 on the diagram, 2 in the frame)."""
    in_frame = [fp_terminal(RATE, 20)]
    on_diagram = [fp_terminal(TOTAL, 30)]
    wires = []
    if indicators:
        in_frame.insert(0, constant(1040, I32, 2))
        on_diagram.insert(0, constant(709, I32, 1))
        wires = [wire(902, 1040, RATE), wire(903, 709, TOTAL)]
    frames = _frames(700, [face(611, 602)], [face(612, 602), *in_frame])
    return root_diagram([case_in(frames)], [srn(800, on_diagram)], wires)


def shared_label_front_panel(*, indicators: bool) -> str:
    """The front panel of ``shared_label_xml``: two terminals, both labelled ``x``."""
    pair = {20: "x", 30: "x"}
    pane = {0: 20, 1: 30}
    if indicators:
        return front_panel_xml({}, pair, pane=pane)
    return front_panel_xml(pair, {}, pane=pane)
