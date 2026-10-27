"""Case frame labels for Boolean and error selectors (#187).

A Boolean case and a two-frame error case are fully described -- every value
has a frame -- so each frame is labelled by what it stores and never takes a
Default marker. An error case with error-code frames (LabVIEW 2019+) has one
frame that stores no value: that frame is the true Default.
"""

from __future__ import annotations

from pathlib import Path

from tests.case_structure_fixture import CaseViews, FrameSpec, case_structure_views


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def _json_frame_labels(views: CaseViews) -> list[str]:
    scope = next(item for item in views.json["body"] if item["kind"] == "scope")
    return [frame["label"] for frame in scope["frames"]]


def _assert_lines(
    views: CaseViews,
    *,
    describe: list[str],
    lvnet: list[str],
    json_labels: list[str],
) -> None:
    describe_lines = _lines(views.describe)
    lvnet_lines = _lines(views.lvnet)
    for line in describe:
        assert line in describe_lines, f"missing describe line {line!r}"
    for line in lvnet:
        assert line in lvnet_lines, f"missing lvnet line {line!r}"
    assert _json_frame_labels(views) == json_labels


NO_ERROR = FrameSpec(ranges=((0, 0),))
ERROR = FrameSpec(ranges=((1, 1),))
VALUELESS = FrameSpec()


def test_two_frame_error_default_on_error_frame_is_not_marked(tmp_path: Path) -> None:
    views = case_structure_views(tmp_path, "error", [NO_ERROR, ERROR], default_frame=1)
    _assert_lines(
        views,
        describe=['Frame "No Error":', 'Frame "Error":'],
        lvnet=['frame "No Error" :', 'frame "Error", default :'],
        json_labels=["No Error", "Error"],
    )
    assert "(default)" not in views.describe


def test_two_frame_error_with_stored_values_and_no_default_reads_no_error_then_error(
    tmp_path: Path,
) -> None:
    views = case_structure_views(
        tmp_path, "error", [NO_ERROR, ERROR], default_frame=None
    )
    _assert_lines(
        views,
        describe=['Frame "No Error":', 'Frame "Error":'],
        lvnet=['frame "No Error" :', 'frame "Error" :'],
        json_labels=["No Error", "Error"],
    )
    assert "default" not in views.lvnet


def test_error_codes_with_valueless_default_between_no_error_and_code(
    tmp_path: Path,
) -> None:
    code = FrameSpec(ranges=((-1012, -1012),))
    views = case_structure_views(
        tmp_path, "error", [NO_ERROR, VALUELESS, code], default_frame=1
    )
    _assert_lines(
        views,
        describe=['Frame "No Error":', 'Frame "Default":', 'Frame "Error -1012":'],
        lvnet=['frame "No Error" :', "frame default :", 'frame "Error -1012" :'],
        json_labels=["No Error", "Default", "Error -1012"],
    )


def test_valueless_default_with_error_code_and_no_no_error_frame(
    tmp_path: Path,
) -> None:
    code = FrameSpec(ranges=((-1012, -1012),))
    views = case_structure_views(tmp_path, "error", [VALUELESS, code], default_frame=0)
    _assert_lines(
        views,
        describe=['Frame "Default":', 'Frame "Error -1012":'],
        lvnet=["frame default :", 'frame "Error -1012" :'],
        json_labels=["Default", "Error -1012"],
    )


def test_default_error_frame_storing_status_one_reads_error(tmp_path: Path) -> None:
    code = FrameSpec(ranges=((-1012, -1012),))
    views = case_structure_views(tmp_path, "error", [code, ERROR], default_frame=1)
    _assert_lines(
        views,
        describe=['Frame "Error -1012":', 'Frame "Error":'],
        lvnet=['frame "Error -1012" :', 'frame "Error", default :'],
        json_labels=["Error -1012", "Error"],
    )
    assert "(default)" not in views.describe


def test_labview_8_9_two_frame_error_reads_no_error_then_error(
    tmp_path: Path,
) -> None:
    views = case_structure_views(
        tmp_path,
        "error",
        [VALUELESS, VALUELESS],
        default_frame=None,
        stored_values=False,
    )
    _assert_lines(
        views,
        describe=['Frame "No Error":', 'Frame "Error":'],
        lvnet=['frame "No Error" :', 'frame "Error" :'],
        json_labels=["No Error", "Error"],
    )
    assert "(default)" not in views.describe


def test_pre_2010_boolean_case_storing_no_default_reads_false_then_true(
    tmp_path: Path,
) -> None:
    views = case_structure_views(
        tmp_path,
        "boolean",
        [VALUELESS, VALUELESS],
        default_frame=None,
        stored_values=False,
    )
    _assert_lines(
        views,
        describe=['Frame "False":', 'Frame "True":'],
        lvnet=['frame "False" :', 'frame "True" :'],
        json_labels=["False", "True"],
    )
    assert "Default" not in views.describe


def test_boolean_default_frame_with_its_value_takes_no_marker(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "boolean",
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=1,
    )
    _assert_lines(
        views,
        describe=['Frame "False":', 'Frame "True":'],
        lvnet=['frame "False" :', 'frame "True", default :'],
        json_labels=["False", "True"],
    )
    assert "Default" not in views.describe


def test_integer_default_with_explicit_value_keeps_default_suffix(
    tmp_path: Path,
) -> None:
    views = case_structure_views(
        tmp_path,
        "integer",
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=0,
    )
    _assert_lines(
        views,
        describe=['Frame "0, Default":', 'Frame "1":'],
        lvnet=['frame "0", default :', 'frame "1" :'],
        json_labels=["0, Default", "1"],
    )


def test_integer_valueless_default_reads_default(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "integer",
        [FrameSpec(ranges=((0, 0),)), VALUELESS],
        default_frame=1,
    )
    _assert_lines(
        views,
        describe=['Frame "0":', 'Frame "Default":'],
        lvnet=['frame "0" :', "frame default :"],
        json_labels=["0", "Default"],
    )


def test_enum_frames_read_their_item_names(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "enum",
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=None,
        enum_items=("Red", "Green"),
    )
    _assert_lines(
        views,
        describe=['Frame "Red":', 'Frame "Green":'],
        lvnet=['frame "Red" :', 'frame "Green" :'],
        json_labels=["Red", "Green"],
    )


def test_enum_default_frame_with_a_value_reads_item_then_default(
    tmp_path: Path,
) -> None:
    views = case_structure_views(
        tmp_path,
        "enum",
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=1,
        enum_items=("Red", "Green"),
    )
    _assert_lines(
        views,
        describe=['Frame "Red":', 'Frame "Green, Default":'],
        lvnet=['frame "Red" :', 'frame "Green", default :'],
        json_labels=["Red", "Green, Default"],
    )


def test_enum_valueless_default_reads_default(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "enum",
        [FrameSpec(ranges=((0, 0),)), VALUELESS],
        default_frame=1,
        enum_items=("Red", "Green"),
    )
    _assert_lines(
        views,
        describe=['Frame "Red":', 'Frame "Default":'],
        lvnet=['frame "Red" :', "frame default :"],
        json_labels=["Red", "Default"],
    )


def test_no_error_frame_listing_a_code_reads_no_error_then_the_code(
    tmp_path: Path,
) -> None:
    no_error_and_code = FrameSpec(ranges=((0, 0), (18, 18)))
    views = case_structure_views(
        tmp_path, "error", [no_error_and_code, VALUELESS], default_frame=1
    )
    _assert_lines(
        views,
        describe=['Frame "No Error, Error 18":', 'Frame "Default":'],
        lvnet=['frame "No Error", "Error 18" :', "frame default :"],
        json_labels=["No Error, Error 18", "Default"],
    )


def test_open_ended_no_error_frame_listing_codes_reads_no_error_then_codes(
    tmp_path: Path,
) -> None:
    no_error_and_codes = FrameSpec(no_error_point=True, ranges=((18, 18), (20, 25)))
    views = case_structure_views(
        tmp_path, "error", [no_error_and_codes, VALUELESS], default_frame=1
    )
    _assert_lines(
        views,
        describe=['Frame "No Error, Error 18, 20..25":', 'Frame "Default":'],
        lvnet=['frame "No Error", "Error 18, 20..25" :', "frame default :"],
        json_labels=["No Error, Error 18, 20..25", "Default"],
    )


def test_open_ended_no_error_frame_alone_still_reads_no_error(
    tmp_path: Path,
) -> None:
    views = case_structure_views(
        tmp_path,
        "error",
        [FrameSpec(no_error_point=True), ERROR],
        default_frame=1,
    )
    _assert_lines(
        views,
        describe=['Frame "No Error":', 'Frame "Error":'],
        lvnet=['frame "No Error" :', 'frame "Error", default :'],
        json_labels=["No Error", "Error"],
    )
