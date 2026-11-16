"""vi.lib Picture, pixmap and graphics-format VIs: each caller diagram is generated
from the catalog entry the recorded path names (the connector pane numbers are the
ones the corpus callers record) and its Python is run on small images, checking
pixels. A Picture is a Pillow image (``codegen/picture_values.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.vilib_caller import BOOL, I32, STRING, run_catalog_call

PIL_Image = pytest.importorskip("PIL.Image")

PICTURE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Picture")
CLUSTER = LVType(kind=LVTypeKind.CLUSTER, fields=[ClusterField(name="a", type=I32)])
ARRAY = LVType(kind=LVTypeKind.ARRAY)
ERROR_CLUSTER = LVType(kind=LVTypeKind.CLUSTER)

PICT = ["<vilib>", "picture", "picture.llb"]
UTIL = ["<vilib>", "picture", "pictutil.llb"]
PIXMAP = ["<vilib>", "picture", "pixmap.llb"]

BLACK = (0, 0, 0, 255)
RED = (255, 0, 0, 255)
CLEAR = (0, 0, 0, 0)


def _call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[str],
    pane: list[Any],
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    arguments: dict[str, Any],
) -> Any:
    _, result = run_catalog_call(
        tmp_path, monkeypatch, recorded, pane, inputs, outputs, arguments
    )
    return result


def _image(width: int, height: int, color: tuple[int, ...] = CLEAR) -> Any:
    return PIL_Image.new("RGBA", (width, height), color)


# --- Empty Picture ----------------------------------------------------------


def test_empty_picture_is_an_image_of_no_pixels(tmp_path, monkeypatch) -> None:
    result = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Empty Picture"],
        [(0, "output", PICTURE)],
        {},
        {"picture": (0, PICTURE)},
        {},
    )
    assert result.picture.size == (0, 0)


# --- Draw Rect / Draw Oval ----------------------------------------------------

SHAPE_PANE = [
    (0, "input", PICTURE),
    (2, "input", CLUSTER),
    (3, "input", BOOL),
    (4, "input", I32),
    (6, "output", PICTURE),
    (8, "input", CLUSTER),
]


def _shape(tmp_path, monkeypatch, name, picture, rect, fill, color=None, pen=None):
    inputs: dict[str, Any] = {"rect": (2, CLUSTER), "fill": (3, BOOL)}
    arguments: dict[str, Any] = {"rect": rect, "fill": fill}
    if picture is not None:
        inputs["picture"] = (0, PICTURE)
        arguments["picture"] = picture
    if color is not None:
        inputs["color"] = (4, I32)
        arguments["color"] = color
    if pen is not None:
        inputs["pen"] = (8, CLUSTER)
        arguments["pen"] = pen
    return _call(
        tmp_path,
        monkeypatch,
        [*PICT, name],
        SHAPE_PANE,
        inputs,
        {"new_picture": (6, PICTURE)},
        arguments,
    ).new_picture


def test_draw_rect_fill_paints_left_top_to_right_bottom_exclusive(
    tmp_path, monkeypatch
) -> None:
    image = _shape(tmp_path, monkeypatch, "Draw Rect.vi", None, (1, 2, 4, 5), True)
    assert image.size == (4, 5)
    painted = {
        (x, y) for x in range(4) for y in range(5) if image.getpixel((x, y)) == BLACK
    }
    assert painted == {(x, y) for x in range(1, 4) for y in range(2, 5)}


def test_draw_rect_color_is_the_color_box_number(tmp_path, monkeypatch) -> None:
    image = _shape(
        tmp_path, monkeypatch, "Draw Rect.vi", None, (0, 0, 2, 2), True, color=0xFF0000
    )
    assert image.getpixel((1, 1)) == RED


def test_draw_rect_outline_leaves_the_interior(tmp_path, monkeypatch) -> None:
    image = _shape(tmp_path, monkeypatch, "Draw Rect.vi", None, (0, 0, 5, 5), False)
    assert image.getpixel((0, 2)) == BLACK
    assert image.getpixel((4, 4)) == BLACK
    assert image.getpixel((2, 2)) == CLEAR


def test_draw_rect_keeps_the_picture_given_and_grows_it(tmp_path, monkeypatch) -> None:
    given = _image(6, 2, RED)
    image = _shape(tmp_path, monkeypatch, "Draw Rect.vi", given, (0, 0, 2, 4), True)
    assert given.size == (6, 2)
    assert image.size == (6, 4)
    assert image.getpixel((5, 1)) == RED
    assert image.getpixel((1, 3)) == BLACK


def test_draw_rectangle_has_the_same_pane(tmp_path, monkeypatch) -> None:
    image = _shape(tmp_path, monkeypatch, "Draw Rectangle.vi", None, (0, 0, 2, 2), True)
    assert image.getpixel((1, 1)) == BLACK


def test_draw_oval_fills_the_ellipse_not_the_corners(tmp_path, monkeypatch) -> None:
    image = _shape(tmp_path, monkeypatch, "Draw Oval.vi", None, (0, 0, 9, 9), True)
    assert image.getpixel((4, 4)) == BLACK
    assert image.getpixel((0, 0)) == CLEAR


# --- Draw Point, Draw Line, Move Pen --------------------------------------------


def test_draw_point_sets_one_pixel_and_sizes_the_picture(tmp_path, monkeypatch) -> None:
    pane = [
        (0, "input", PICTURE),
        (2, "input", CLUSTER),
        (4, "input", I32),
        (6, "output", PICTURE),
        (8, "input", CLUSTER),
    ]
    image = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Draw Point.vi"],
        pane,
        {"point": (2, CLUSTER), "color": (4, I32)},
        {"new_picture": (6, PICTURE)},
        {"point": (3, 1), "color": 0x0000FF},
    ).new_picture
    assert image.size == (4, 2)
    assert image.getpixel((3, 1)) == (0, 0, 255, 255)
    assert image.getpixel((2, 1)) == CLEAR


def _line(tmp_path, monkeypatch, pen, end, absolute):
    given = _image(8, 8)
    if pen is not None:
        given.info["pen"] = pen
    pane = [
        (0, "input", PICTURE),
        (2, "input", CLUSTER),
        (3, "input", BOOL),
        (4, "input", I32),
        (6, "output", PICTURE),
        (8, "input", CLUSTER),
    ]
    return _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Draw Line.vi"],
        pane,
        {
            "picture": (0, PICTURE),
            "end_point": (2, CLUSTER),
            "absolute_coordinates": (3, BOOL),
        },
        {"new_picture": (6, PICTURE)},
        {"picture": given, "end_point": end, "absolute_coordinates": absolute},
    ).new_picture


def test_draw_line_runs_from_the_pen_to_the_end_point(tmp_path, monkeypatch) -> None:
    image = _line(tmp_path, monkeypatch, (0, 2), (3, 2), True)
    assert [image.getpixel((x, 2)) for x in range(5)] == [BLACK] * 4 + [CLEAR]
    assert image.getpixel((0, 1)) == CLEAR
    assert image.info["pen"] == (3, 2)


def test_draw_line_relative_end_point_adds_to_the_pen(tmp_path, monkeypatch) -> None:
    image = _line(tmp_path, monkeypatch, (1, 1), (2, 3), False)
    assert image.info["pen"] == (3, 4)
    assert image.getpixel((3, 4)) == BLACK


def test_draw_line_with_no_pen_starts_at_the_origin(tmp_path, monkeypatch) -> None:
    image = _line(tmp_path, monkeypatch, None, (0, 3), True)
    assert [image.getpixel((0, y)) for y in range(4)] == [BLACK] * 4


def test_move_pen_changes_only_the_pen(tmp_path, monkeypatch) -> None:
    given = _image(3, 3, RED)
    given.info["pen"] = (1, 1)
    pane = [
        (2, "output", PICTURE),
        (3, "input", BOOL),
        (4, "input", CLUSTER),
        (5, "input", PICTURE),
    ]
    inputs = {"new_position": (4, CLUSTER), "picture": (5, PICTURE)}
    outputs = {"new_picture": (2, PICTURE)}
    absolute = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Move Pen.vi"],
        pane,
        inputs,
        outputs,
        {"new_position": (5, 6), "picture": given},
    ).new_picture
    assert absolute.info["pen"] == (5, 6)
    assert absolute.size == (3, 3)
    assert absolute.getpixel((0, 0)) == RED
    assert given.info["pen"] == (1, 1)
    relative = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Move Pen.vi"],
        pane,
        {**inputs, "absolute_coordinates": (3, BOOL)},
        outputs,
        {"new_position": (5, 6), "picture": given, "absolute_coordinates": False},
    ).new_picture
    assert relative.info["pen"] == (6, 7)


# --- Text -------------------------------------------------------------------

TEXT_PANE = [
    (2, "output", CLUSTER),
    (3, "output", PICTURE),
    (8, "input", STRING),
    (10, "input", CLUSTER),
    (11, "input", PICTURE),
    (9, "input", CLUSTER),
    (0, "input", I32),
    (4, "input", I32),
    (5, "input", CLUSTER),
    (6, "input", I32),
    (7, "input", I32),
]


def _text(tmp_path, monkeypatch, text, origin, alignment=None, orientation=None):
    inputs: dict[str, Any] = {"text": (8, STRING), "origin": (10, CLUSTER)}
    arguments: dict[str, Any] = {"text": text, "origin": origin}
    if alignment is not None:
        inputs["alignment"] = (9, CLUSTER)
        arguments["alignment"] = alignment
    if orientation is not None:
        inputs["text_orientation"] = (0, I32)
        arguments["text_orientation"] = orientation
    return _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Draw Text at Point.vi"],
        TEXT_PANE,
        inputs,
        {"new_picture": (3, PICTURE), "text_rect": (2, CLUSTER)},
        arguments,
    )


def test_draw_text_draws_dark_pixels_in_its_rect(tmp_path, monkeypatch) -> None:
    result = _text(tmp_path, monkeypatch, "Hi", (2, 3))
    left, top, right, bottom = result.text_rect
    assert (left, top) == (2, 3)
    assert right > left and bottom > top
    assert result.new_picture.size == (right, bottom)
    raw = result.new_picture.tobytes()
    assert any(
        raw[i + 3] > 0 and raw[i : i + 3] == bytes(3) for i in range(0, len(raw), 4)
    )
    assert result.new_picture.getpixel((0, 0)) == CLEAR


def test_draw_text_right_bottom_alignment_ends_at_the_origin(
    tmp_path, monkeypatch
) -> None:
    left_top = _text(tmp_path, monkeypatch, "Hi", (30, 30)).text_rect
    right_bottom = _text(tmp_path, monkeypatch, "Hi", (30, 30), (2, 2)).text_rect
    assert right_bottom[2:] == (30, 30)
    assert right_bottom[0] == 30 - (left_top[2] - left_top[0])


def test_draw_text_clockwise_swaps_width_and_height(tmp_path, monkeypatch) -> None:
    normal = _text(tmp_path, monkeypatch, "Hello", (0, 0)).text_rect
    turned = _text(tmp_path, monkeypatch, "Hello", (0, 0), None, 2).text_rect
    assert (turned[2], turned[3]) == (normal[3], normal[2])


def test_get_text_rect_is_the_rect_draw_text_returns(tmp_path, monkeypatch) -> None:
    drawn = _text(tmp_path, monkeypatch, "Hello", (0, 0)).text_rect
    pane = [
        (0, "input", CLUSTER),
        (1, "input", I32),
        (2, "input", CLUSTER),
        (3, "input", STRING),
        (4, "input", I32),
        (5, "input", CLUSTER),
        (7, "output", CLUSTER),
    ]
    rect = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Get Text Rect.vi"],
        pane,
        {"text": (3, STRING)},
        {"rect": (7, CLUSTER)},
        {"text": "Hello"},
    ).rect
    assert rect == drawn
