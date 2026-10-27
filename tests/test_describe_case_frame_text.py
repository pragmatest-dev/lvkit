"""describe's case frame headers quote a selector value the way lvnet does (#186).

A string selector quotes and escapes each of its values with lvnet's literal
token, so a string frame that is also the default reads ``"TestCase", Default``
rather than being quoted twice. Every other label is one quoted text, and a
control character in a string value is escaped rather than printed raw.
"""

from __future__ import annotations

from pathlib import Path

from tests.case_structure_fixture import CaseViews, FrameSpec, case_structure_views


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def _assert_describe_and_lvnet(
    views: CaseViews, *, describe: list[str], lvnet: list[str]
) -> None:
    describe_lines = _lines(views.describe)
    lvnet_lines = _lines(views.lvnet)
    for line in describe:
        assert line in describe_lines, f"missing describe line {line!r}"
    for line in lvnet:
        assert line in lvnet_lines, f"missing lvnet line {line!r}"


def test_string_default_frame_quotes_each_value_once(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "string",
        [FrameSpec(strings=("TestCase",)), FrameSpec(strings=("other",))],
        default_frame=0,
    )
    _assert_describe_and_lvnet(
        views,
        describe=['Frame "TestCase", Default:', 'Frame "other":'],
        lvnet=['frame "TestCase", default :', 'frame "other" :'],
    )


def test_empty_string_default_frame_reads_empty_then_default(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "string",
        [FrameSpec(strings=("",)), FrameSpec(strings=("x",))],
        default_frame=0,
    )
    _assert_describe_and_lvnet(
        views,
        describe=['Frame "", Default:', 'Frame "x":'],
        lvnet=['frame "", default :', 'frame "x" :'],
    )


def test_multi_value_string_frame_reads_as_before(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "string",
        [FrameSpec(strings=("<vi.lib>", "<vilib>")), FrameSpec(strings=("x",))],
        default_frame=1,
    )
    _assert_describe_and_lvnet(
        views,
        describe=['Frame "<vi.lib>", "<vilib>":', 'Frame "x", Default:'],
        lvnet=['frame "<vi.lib>", "<vilib>" :', 'frame "x", default :'],
    )


def test_integer_frame_label_is_unchanged(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "integer",
        [FrameSpec(ranges=((-1012, -1012),)), FrameSpec(ranges=((1, 3),))],
        default_frame=None,
    )
    _assert_describe_and_lvnet(
        views,
        describe=['Frame "-1012":', 'Frame "1..3":'],
        lvnet=['frame "-1012" :', 'frame "1..3" :'],
    )


def test_control_characters_in_string_values_are_escaped(tmp_path: Path) -> None:
    views = case_structure_views(
        tmp_path,
        "string",
        [FrameSpec(strings=("\x00\x00",)), FrameSpec(strings=("a\r\nb",))],
        default_frame=0,
    )
    _assert_describe_and_lvnet(
        views,
        describe=[r'Frame "\x00\x00", Default:', r'Frame "a\r\nb":'],
        lvnet=[r'frame "\x00\x00", default :', r'frame "a\r\nb" :'],
    )
