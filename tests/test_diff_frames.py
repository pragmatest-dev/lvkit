"""Diffing frames of two versions of a handmade VI: constants are matched
through the frame pairing (a frame can change its value, become the Default,
change its event label, or sit in a structure with a new uid), every matched
structure records its frame pairs, and event and Diagram Disable frames show up
in the JSON and the text tree by their own values."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.diff import diff_to_dict, diff_uid, format_diff
from lvkit.graph.diff_frames import FramePairs
from tests.case_structure_fixture import FrameSpec, _default_xml, _selector_xml
from tests.structure_diagrams import (
    BOOL,
    EVENT_TIMEOUT,
    EVENT_VALUE_CHANGE,
    I32,
    Views,
    constant,
    dco,
    diag,
    disable_structure,
    event_structure,
    face,
    frame_diagram,
    increment,
    load,
    root_diagram,
    srn,
    term,
    wire,
)

_UNKNOWN_EVENT = 999  # an event type code lvkit does not name


def _case_vi(
    case_uid: int,
    frames: list[tuple[FrameSpec, list[str]]],
    nodes: dict[int, str] | None = None,
    default_frame: int | None = None,
) -> str:
    """An integer case structure whose selector a root constant 703 feeds; each
    frame holds the given diagram constants, and frame ``n`` also ``nodes[n]``.
    ``default_frame`` is the Default frame's position (None: no Default)."""
    faces = [case_uid * 10 + i for i in range(len(frames))]
    selector = dco("caseSel", case_uid + 2, type_id=I32, faces=[*faces, case_uid + 1])
    diagrams = "".join(
        diag(
            case_uid * 100 + i,
            nodes=[
                srn(case_uid * 100 + 50 + i, [face(faces[i], case_uid + 2), *items]),
                *([nodes[i]] if nodes and i in nodes else []),
            ],
        )
        for i, (_, items) in enumerate(frames)
    )
    case = (
        f'<SL__arrayElement class="caseStruct" uid="{case_uid}">'
        f'<termList elements="1">{term(case_uid + 1, selector)}</termList>'
        f'<diagramList elements="{len(frames)}">{diagrams}</diagramList>'
        f"{_selector_xml('integer', [spec for spec, _ in frames], True)}"
        f"{_default_xml(default_frame)}"
        "</SL__arrayElement>"
    )
    return root_diagram(
        [case],
        [srn(800, [constant(703, I32, 1)])],
        [wire(901, 703, case_uid + 1)],
    )


def _two(tmp_path: Path, before: str, after: str) -> tuple[Views, Views]:
    views = []
    for side, xml in (("before", before), ("after", after)):
        folder = tmp_path / side
        folder.mkdir()
        views.append(load(folder, xml))
    return views[0], views[1]


def _diff(before: Views, after: Views) -> dict:
    return diff_to_dict(before.graph, after.graph, before.vi, after.vi)


def _changes(data: dict) -> list[tuple[str, str, str]]:
    return [(c["uid"], c["kind"], c["change"]) for c in data["changes"]]


ONE = FrameSpec(ranges=((1, 1),))
TWO = FrameSpec(ranges=((2, 2),))


def test_a_constant_with_a_new_uid_stays_when_its_frame_gains_a_value(
    tmp_path: Path,
) -> None:
    before, after = _two(
        tmp_path,
        _case_vi(600, [(ONE, []), (TWO, [constant(640, I32, 5)])]),
        _case_vi(
            600, [(ONE, []), (FrameSpec(ranges=((2, 3),)), [constant(645, I32, 5)])]
        ),
    )
    data = _diff(before, after)
    assert _changes(data) == [("~2", "value", "modified")]


def test_a_constant_with_a_new_uid_stays_when_its_frame_becomes_the_default(
    tmp_path: Path,
) -> None:
    # The frame keeps Increment 4400, so the diff pairs it by its contents.
    kept = {1: increment(4400)}
    before, after = _two(
        tmp_path,
        _case_vi(600, [(ONE, []), (TWO, [constant(640, I32, 5)])], kept),
        _case_vi(600, [(ONE, []), (FrameSpec(), [constant(645, I32, 5)])], kept, 1),
    )
    data = _diff(before, after)
    assert data["frame_pairs"] == [
        {"before": "600", "after": "600", "pairs": [[0, 0], [1, 1]]}
    ]
    assert [(c["kind"], c["change"], c["detail"]) for c in data["changes"]] == [
        ("value", "modified", "2 → Default")
    ]


def test_a_constant_with_a_new_uid_stays_in_a_structure_with_a_new_uid(
    tmp_path: Path,
) -> None:
    before, after = _two(
        tmp_path,
        _case_vi(600, [(ONE, []), (TWO, [constant(640, I32, 5)])]),
        _case_vi(605, [(ONE, []), (TWO, [constant(645, I32, 5)])]),
    )
    cmap = diff_uid(before.graph, after.graph, before.vi, after.vi)
    assert cmap.frame_pairs == [FramePairs("600", "605", [(0, 0), (1, 1)])]
    assert [c for c in cmap.changes if c.kind == "constant"] == []


def test_a_matched_structure_whose_frames_all_changed_has_no_pairs(
    tmp_path: Path,
) -> None:
    before, after = _two(
        tmp_path,
        _case_vi(600, [(ONE, [constant(640, I32, 5)])]),
        _case_vi(600, [(TWO, [constant(645, I32, 6)])]),
    )
    cmap = diff_uid(before.graph, after.graph, before.vi, after.vi)
    assert cmap.frame_pairs == [FramePairs("600", "600", [])]
    assert sorted((c.uid, c.change) for c in cmap.changes) == [
        ("640", "removed"),
        ("645", "added"),
        ("~1", "removed"),
        ("~2", "added"),
    ]


def _event_vi(second_type: int, constant_uid: int, value: int) -> str:
    frames = [
        frame_diagram(4101, 4102, []),
        frame_diagram(4201, 4202, [constant(constant_uid, I32, value)]),
    ]
    return root_diagram(
        [event_structure(4000, [], frames, [EVENT_TIMEOUT, second_type])], [], []
    )


def test_a_constant_with_a_new_uid_stays_when_its_event_frame_label_changes(
    tmp_path: Path,
) -> None:
    before, after = _two(
        tmp_path,
        _event_vi(EVENT_VALUE_CHANGE, 4050, 3),
        _event_vi(_UNKNOWN_EVENT, 4055, 3),
    )
    data = _diff(before, after)
    assert data["frame_pairs"] == [
        {"before": "4000", "after": "4000", "pairs": [[0, 0], [1, 1]]}
    ]
    assert _changes(data) == []


def test_an_event_frame_change_in_json_and_text(tmp_path: Path) -> None:
    before, after = _two(
        tmp_path,
        _event_vi(EVENT_VALUE_CHANGE, 4050, 3),
        _event_vi(EVENT_VALUE_CHANGE, 4050, 4),
    )
    data = _diff(before, after)
    (change,) = data["changes"]
    label = after.graph.get_graph_node(f"{after.vi}::4000").frames[1].event_label
    step = {"structure": "4000", "frame": 1, "value": [label]}
    assert (change["uid"], change["change"]) == ("4050", "modified")
    assert change["location_before"] == [step]
    assert change["location_after"] == [step]
    text = format_diff(before.graph, after.graph, before.vi, after.vi)
    assert text.splitlines()[:2] == ["  event:", f'    "{label}":']
    assert text.splitlines()[2].startswith("~     ○ ")


def _disable_vi(constant_uid: int, value: int) -> str:
    frames = [
        frame_diagram(3101, 3102, []),
        frame_diagram(3201, 3202, [constant(constant_uid, I32, value)]),
    ]
    return root_diagram([disable_structure(3000, [], frames)], [], [])


def test_a_diagram_disable_frame_change_in_json_and_text(tmp_path: Path) -> None:
    before, after = _two(tmp_path, _disable_vi(3030, 3), _disable_vi(3030, 4))
    data = _diff(before, after)
    assert data["frame_pairs"] == [
        {"before": "3000", "after": "3000", "pairs": [[0, 0], [1, 1]]}
    ]
    (change,) = data["changes"]
    step = {"structure": "3000", "frame": 1, "value": ["Disabled"]}
    assert (change["uid"], change["change"]) == ("3030", "modified")
    assert change["location_before"] == [step]
    assert change["location_after"] == [step]
    text = format_diff(before.graph, after.graph, before.vi, after.vi)
    assert text.splitlines()[:2] == ["  disabled:", '    "Disabled":']


def test_a_constant_only_frame_that_becomes_the_default_is_the_same_frame(
    tmp_path: Path,
) -> None:
    """A constant keeps its uid across saves, so a frame holding only constant
    640 is matched by its contents when its value changes to Default."""
    before, after = _two(
        tmp_path,
        _case_vi(600, [(ONE, []), (TWO, [constant(640, I32, 5)])]),
        _case_vi(600, [(ONE, []), (FrameSpec(), [constant(640, I32, 5)])], None, 1),
    )
    data = _diff(before, after)
    assert data["frame_pairs"] == [
        {"before": "600", "after": "600", "pairs": [[0, 0], [1, 1]]}
    ]
    assert [(c["kind"], c["change"], c["detail"]) for c in data["changes"]] == [
        ("value", "modified", "2 → Default")
    ]


def test_a_constant_that_keeps_its_uid_and_changes_type_is_modified(
    tmp_path: Path,
) -> None:
    before, after = _two(
        tmp_path,
        root_diagram([], [srn(800, [constant(703, I32, 1)])], []),
        root_diagram([], [srn(800, [constant(703, BOOL, "True")])], []),
    )
    data = _diff(before, after)
    assert _changes(data) == [("703", "constant", "modified")]
