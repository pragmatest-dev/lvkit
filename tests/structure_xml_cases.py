"""The diagrams ``test_lvnet_structures_xml.py`` and ``test_lvnet_placement_xml.py``
read: one tiny VI per structure kind, each with an input tunnel nothing reads, an
output tunnel one frame drives, and a reader of that output. Constants are the
sources of the wires."""

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
    frame_diagram,
    increment,
    loop,
    root_diagram,
    srn,
    stacked_sequence,
    term,
    tunnel,
    wire,
)

BOOLEAN_FRAMES = [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))]
READER = 810


def _root(*constants: str) -> str:
    return srn(800, list(constants))


def case_xml(*, wire_selector: bool = True, read_input: bool = False) -> str:
    """A Boolean Case (uid 600): selector 601, an input tunnel (outer 620, wired
    from 7; ``read_input`` has an Increment in frame True read it) and an output
    tunnel (outer 630): frame True drives it with 1, frame False leaves it
    unwired. Increment_810 reads the output."""
    selector = dco("caseSel", 602, type_id=BOOL, faces=[611, 612, 601])
    outer = (
        term(601, selector)
        + tunnel("csTun", 620, 621, [622, 623])
        + tunnel("csTun", 630, 631, [632, 633])
    )
    in_frame = (
        diag(
            710,
            nodes=[
                srn(
                    660,
                    [
                        face(612, 602),
                        face(623, 621),
                        face(633, 631),
                        constant(640, I32, 1),
                    ],
                ),
                '<SL__arrayElement uid="820" />',
            ],
            structures=[increment(820)],
        )
        if read_input
        else frame_diagram(
            710,
            660,
            [face(612, 602), face(623, 621), face(633, 631), constant(640, I32, 1)],
        )
    )
    false_frame = frame_diagram(
        700, 650, [face(611, 602), face(622, 621), face(632, 631)]
    )
    case = (
        '<SL__arrayElement class="caseStruct" uid="600">'
        f'<termList elements="3">{outer}</termList>'
        f'<diagramList elements="2">{false_frame}{in_frame}</diagramList>'
        f'{_selector_xml("boolean", BOOLEAN_FRAMES, True)}</SL__arrayElement>'
    )
    wires = [wire(901, 701, 620), wire(902, 640, 633), wire(904, 630, READER + 3)]
    if wire_selector:
        wires.append(wire(903, 703, 601))
    if read_input:
        wires.append(wire(905, 623, 823))
    return root_diagram(
        [case, increment(READER)],
        [_root(constant(701, I32, 7), constant(703, BOOL, True))],
        wires,
    )


def loop_xml(kind: str, *, stop_if_true: bool = True) -> str:
    """A For Loop (count 5, N = outer 410) or While Loop (stop wired from False)
    with an input tunnel (outer 420, wired from 7) nothing in the body reads."""
    border = [tunnel("lpTun", 420, 421, [422])]
    faces = [face(422, 421)]
    extra = ""
    if kind == "forLoop":
        border.insert(0, tunnel("lMax", 410, 411, [412]))
        faces.insert(0, face(412, 411))
    else:
        flags = 65536 if stop_if_true else 0
        extra = (
            f'<loopTestDCO class="lTst" uid="430"><objFlags>{flags}</objFlags>'
            '<termList elements="1"><SL__arrayElement uid="431"/></termList>'
            "</loopTestDCO>"
        )
        faces.append(term(431, '<dco uid="430"/>'))
    inner = frame_diagram(450, 460, faces)
    wires = [wire(901, 703, 420)]
    constants = [
        constant(701, I32, 5),
        constant(703, I32, 7),
        constant(705, BOOL, False),
    ]
    wires.append(wire(902, 701, 410) if kind == "forLoop" else wire(902, 705, 431))
    return root_diagram(
        [loop(kind, 400, border, inner, extra)], [_root(*constants)], wires
    )


def flat_sequence_xml() -> str:
    """A Flat Sequence (1000): frame 0 has an input edge tunnel (outer 1010, wired
    from 7, nothing reads it); frame 1 has an output edge tunnel (outer 1020) driven
    by 3 and read by Increment_810."""
    f0 = (
        1100,
        [tunnel("seqTun", 1010, 1012, [1011])],
        frame_diagram(1101, 1102, [face(1011, 1012)]),
    )
    f1 = (
        1200,
        [tunnel("seqTun", 1020, 1022, [1021])],
        frame_diagram(1201, 1202, [face(1021, 1022), constant(1030, I32, 3)]),
    )
    return root_diagram(
        [flat_sequence(1000, [f0, f1]), increment(READER)],
        [_root(constant(701, I32, 7))],
        [wire(901, 701, 1010), wire(902, 1030, 1021), wire(903, 1020, READER + 3)],
    )


def stacked_sequence_xml() -> str:
    """A Stacked Sequence (2000): two input tunnels (outer 2010 read by an
    Increment in frame 0, outer 2014 read by nothing) and an output tunnel (outer
    2020) frame 1 drives with 3; Increment_810 reads the output."""
    border = [
        tunnel("seqTun", 2010, 2013, [2011, 2012]),
        tunnel("seqTun", 2014, 2016, [2015, 2017]),
        tunnel("seqTun", 2020, 2023, [2021, 2022]),
    ]
    f0 = diag(
        2101,
        nodes=[
            srn(2102, [face(2011, 2013), face(2015, 2016), face(2021, 2023)]),
            '<SL__arrayElement uid="820" />',
        ],
        structures=[increment(820)],
    )
    f1 = frame_diagram(
        2201,
        2202,
        [face(2012, 2013), face(2017, 2016), face(2022, 2023), constant(2030, I32, 3)],
    )
    return root_diagram(
        [stacked_sequence(2000, border, [f0, f1]), increment(READER)],
        [_root(constant(701, I32, 7), constant(703, I32, 9))],
        [
            wire(901, 701, 2010),
            wire(902, 703, 2014),
            wire(903, 2011, 823),
            wire(904, 2030, 2022),
            wire(905, 2020, READER + 3),
        ],
    )


def disable_xml() -> str:
    """A Diagram Disable Structure (3000): an input tunnel (outer 3010, wired from
    7, unread) and an output tunnel (outer 3020) the Enabled frame drives with 3,
    the Disabled frame leaves unwired."""
    border = [
        tunnel("commentTun", 3010, 3013, [3011, 3012]),
        tunnel("commentTun", 3020, 3023, [3021, 3022]),
    ]
    enabled = frame_diagram(
        3101, 3102, [face(3011, 3013), face(3021, 3023), constant(3030, I32, 3)]
    )
    disabled = frame_diagram(3201, 3202, [face(3012, 3013), face(3022, 3023)])
    return root_diagram(
        [disable_structure(3000, border, [enabled, disabled]), increment(READER)],
        [_root(constant(701, I32, 7))],
        [wire(901, 701, 3010), wire(902, 3030, 3021), wire(903, 3020, READER + 3)],
    )


def event_xml() -> str:
    """An Event Structure (4000): a timeout wired from -1, the dynamic pair (left
    terminal 4010 wired from 5, right terminal 4020 read by Increment_820 and driven
    by 9 in frame [1] only) and an output tunnel (outer 4040) frame [1] drives with
    3; Increment_810 reads it."""
    border = [
        tunnel("eventTimeOut", 4030, 4033, [4031, 4032]),
        tunnel("eventDynDCO", 4010, 4013, [4011, 4012]),
        tunnel("eventDynDCO", 4020, 4023, [4021, 4022], output=True),
        tunnel("selTun", 4040, 4043, [4041, 4042], output=True),
    ]
    f0 = frame_diagram(
        4101,
        4102,
        [face(4031, 4033), face(4011, 4013), face(4021, 4023), face(4041, 4043)],
    )
    f1 = frame_diagram(
        4201,
        4202,
        [
            face(4032, 4033),
            face(4012, 4013),
            face(4022, 4023),
            face(4042, 4043),
            constant(4050, I32, 3),
            constant(4052, I32, 9),
        ],
    )
    return root_diagram(
        [
            event_structure(
                4000, border, [f0, f1], [EVENT_TIMEOUT, EVENT_VALUE_CHANGE]
            ),
            increment(READER),
            increment(820),
        ],
        [_root(constant(701, I32, -1), constant(703, I32, 5))],
        [
            wire(901, 701, 4030),
            wire(902, 703, 4010),
            wire(903, 4050, 4042),
            wire(904, 4052, 4022),
            wire(905, 4040, READER + 3),
            wire(906, 4020, 823),
        ],
    )


def conditional_disable_xml(*, type_specialization: bool = False) -> str:
    """The Disable Structure of ``disable_xml`` as a Conditional Disable (frame 0
    is ``TARGET==a"b``, frame 1 the Default) or, with ``type_specialization``, a
    Type Specialization Structure."""
    border = [
        tunnel("commentTun", 3020, 3023, [3021, 3022]),
    ]
    first = frame_diagram(3101, 3102, [face(3021, 3023), constant(3030, I32, 3)])
    default = frame_diagram(3201, 3202, [face(3022, 3023)])
    structure = disable_structure(
        3000,
        border,
        [first, default],
        conditions=None if type_specialization else [["TARGET", 'a"b'], []],
        type_specialization=type_specialization,
    )
    return root_diagram(
        [structure, increment(READER)],
        [_root(constant(701, I32, 7))],
        [wire(902, 3030, 3021), wire(903, 3020, READER + 3)],
    )


def event_on_control_xml() -> str:
    """An Event Structure whose second frame is a Value Change of the control
    ``stop`` (front-panel control 10, ddo 101), so its label has quotes."""
    first = frame_diagram(4101, 4102, [])
    second = frame_diagram(4201, 4202, [])
    return root_diagram(
        [
            event_structure(
                4000,
                [],
                [first, second],
                [EVENT_TIMEOUT, EVENT_VALUE_CHANGE],
                sources=[0, 101],
            )
        ],
        [],
        [],
    )


def flat_sequence_hand_over_xml(*, read_in_frame: bool = True) -> str:
    """A Flat Sequence (1000) whose frame 0 hands a value to frame 1: frame 0's right
    edge tunnel (outer 1010) is driven by 3 and wired to frame 1's left edge tunnel
    (outer 1020), which an Increment in frame 1 reads (``read_in_frame``) or nothing
    reads."""
    out_edge = tunnel("flatSeqTun", 1010, 1012, [1011], output=True)
    in_edge = tunnel("flatSeqTun", 1020, 1022, [1021])
    f0 = (
        1100,
        [out_edge],
        frame_diagram(1101, 1102, [face(1011, 1012), constant(1030, I32, 3)]),
    )
    inside = (
        diag(
            1201,
            nodes=[srn(1202, [face(1021, 1022)]), '<SL__arrayElement uid="820" />'],
            structures=[increment(820)],
        )
        if read_in_frame
        else frame_diagram(1201, 1202, [face(1021, 1022)])
    )
    f1 = (1200, [in_edge], inside)
    wires = [wire(901, 1030, 1011), wire(902, 1010, 1020)]
    if read_in_frame:
        wires.append(wire(903, 1021, 823))
    return root_diagram([flat_sequence(1000, [f0, f1])], [_root()], wires)
