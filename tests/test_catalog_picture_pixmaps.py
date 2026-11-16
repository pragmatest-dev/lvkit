"""The pixmap, image data and graphics-format picture VIs, run on small images
(``codegen/picture_values.py`` is the representation): image data <-> Pillow round
trips at each depth, the mask, subsets, and PNG, BMP and JPEG files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.vilib_caller import BOOL, I32, PATH, STRING, run_catalog_call

PIL_Image = pytest.importorskip("PIL.Image")

PICTURE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Picture")
CLUSTER = LVType(kind=LVTypeKind.CLUSTER, fields=[ClusterField(name="a", type=I32)])
ARRAY = LVType(kind=LVTypeKind.ARRAY)
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)

PICT = ["<vilib>", "picture", "picture.llb"]
UTIL = ["<vilib>", "picture", "pictutil.llb"]
PIXMAP = ["<vilib>", "picture", "pixmap.llb"]

RED, GREEN, BLUE, WHITE = 0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF


def _call(tmp_path, monkeypatch, recorded, pane, inputs, outputs, arguments) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        recorded,
        pane,
        inputs,
        outputs,
        arguments,
        runtime_not_implemented=True,
    )
    return result


def _rgba(color: int, alpha: int = 255) -> tuple[int, int, int, int]:
    return (color >> 16 & 255, color >> 8 & 255, color & 255, alpha)


def _picture(rows: list[list[int]]) -> Any:
    image = PIL_Image.new("RGBA", (len(rows[0]), len(rows)))
    for y, row in enumerate(rows):
        for x, color in enumerate(row):
            image.putpixel((x, y), _rgba(color))
    return image


PIXMAP_PANE_OUT = [
    (0, "output", ARRAY),
    (1, "output", ARRAY),
    (2, "output", ARRAY),
    (3, "output", ARRAY),
    (4, "output", ARRAY),
    (5, "output", CLUSTER),
    (6, "output", ARRAY),
    (10, "input", CLUSTER),
]
UNFLATTEN_OUT = {
    "pixmap_1bit": (0, ARRAY),
    "pixmap_4bit": (1, ARRAY),
    "pixmap_8bit": (2, ARRAY),
    "pixmap_24bit": (3, ARRAY),
    "colors": (4, ARRAY),
    "top_left": (5, CLUSTER),
    "mask": (6, ARRAY),
}
FLATTEN_PANE = [
    (2, "output", CLUSTER),
    (4, "input", ARRAY),
    (6, "input", ARRAY),
    (7, "input", CLUSTER),
    (8, "input", ARRAY),
    (9, "input", ARRAY),
    (10, "input", ARRAY),
    (11, "input", ARRAY),
]
FLATTEN_IN = {
    "pixmap_1bit": (8, ARRAY),
    "pixmap_4bit": (9, ARRAY),
    "pixmap_8bit": (10, ARRAY),
    "pixmap_24bit": (11, ARRAY),
    "colors": (6, ARRAY),
    "top_left": (7, CLUSTER),
}


def _flatten(tmp_path, monkeypatch, **arguments) -> Any:
    inputs = {name: slot for name, slot in FLATTEN_IN.items() if name in arguments}
    return _call(
        tmp_path,
        monkeypatch,
        [*PIXMAP, "Flatten Pixmap.vi"],
        FLATTEN_PANE,
        inputs,
        {"image_data": (2, CLUSTER)},
        arguments,
    ).image_data


def _unflatten(tmp_path, monkeypatch, image_data) -> Any:
    return _call(
        tmp_path,
        monkeypatch,
        [*PIXMAP, "Unflatten Pixmap.vi"],
        PIXMAP_PANE_OUT,
        {"image_data": (10, CLUSTER)},
        UNFLATTEN_OUT,
        {"image_data": image_data},
    )


# --- Flatten / Unflatten Pixmap ------------------------------------------------


def test_flatten_24_bit_pads_each_row_to_two_bytes_and_stores_red_first(
    tmp_path, monkeypatch
) -> None:
    kind, depth, image, mask, colors, rect = _flatten(
        tmp_path, monkeypatch, pixmap_24bit=[[RED, GREEN, BLUE]], top_left=(2, 5)
    )
    assert depth == 24
    assert rect == (2, 5, 5, 6)
    assert image == [255, 0, 0, 0, 255, 0, 0, 0, 255, 0]
    assert mask == [] and colors == []


def test_unflatten_returns_only_the_array_of_the_images_depth(
    tmp_path, monkeypatch
) -> None:
    data = _flatten(tmp_path, monkeypatch, pixmap_24bit=[[RED, GREEN], [BLUE, WHITE]])
    result = _unflatten(tmp_path, monkeypatch, data)
    assert result.pixmap_24bit == [[RED, GREEN], [BLUE, WHITE]]
    assert result.pixmap_8bit == [] and result.pixmap_4bit == []
    assert result.pixmap_1bit == []
    assert result.top_left == (0, 0)


@pytest.mark.parametrize(
    ("name", "pixmap"),
    [
        ("pixmap_8bit", [[0, 1, 2], [2, 1, 0]]),
        ("pixmap_4bit", [[0, 15, 7], [1, 2, 3]]),
    ],
)
def test_palette_depths_round_trip_with_their_colors(
    tmp_path, monkeypatch, name, pixmap
) -> None:
    colors = [RED, GREEN, BLUE] + [WHITE] * 13
    data = _flatten(tmp_path, monkeypatch, **{name: pixmap, "colors": colors})
    assert data[1] == int(name[7:-3])
    result = _unflatten(tmp_path, monkeypatch, data)
    assert getattr(result, name) == pixmap
    assert result.colors == colors


def test_one_bit_round_trips_and_packs_the_first_pixel_in_the_high_bit(
    tmp_path, monkeypatch
) -> None:
    pixmap = [[True, False, True, True, False, False, False, False, True]]
    data = _flatten(tmp_path, monkeypatch, pixmap_1bit=pixmap, colors=[0, WHITE])
    assert data[1] == 1
    assert data[2] == [0b10110000, 0b10000000]
    assert _unflatten(tmp_path, monkeypatch, data).pixmap_1bit == pixmap


# --- Draw Flattened Pixmap, Picture to Pixmap -----------------------------------

DRAW_FLAT_PANE = [
    (3, "output", PICTURE),
    (8, "input", CLUSTER),
    (9, "input", PICTURE),
]


def _draw_flat(tmp_path, monkeypatch, data, picture=None) -> Any:
    inputs: dict[str, Any] = {"image_data": (8, CLUSTER)}
    arguments: dict[str, Any] = {"image_data": data}
    if picture is not None:
        inputs["picture"] = (9, PICTURE)
        arguments["picture"] = picture
    return _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Draw Flattened Pixmap.vi"],
        DRAW_FLAT_PANE,
        inputs,
        {"new_picture": (3, PICTURE)},
        arguments,
    ).new_picture


def test_draw_flattened_pixmap_draws_each_pixel_at_the_rectangle(
    tmp_path, monkeypatch
) -> None:
    data = _flatten(tmp_path, monkeypatch, pixmap_24bit=[[RED, GREEN]], top_left=(1, 1))
    image = _draw_flat(tmp_path, monkeypatch, data)
    assert image.size == (3, 2)
    assert image.getpixel((1, 1)) == _rgba(RED)
    assert image.getpixel((2, 1)) == _rgba(GREEN)
    assert image.getpixel((0, 0)) == (0, 0, 0, 0)


def test_draw_flattened_pixmap_leaves_masked_pixels_to_the_picture(
    tmp_path, monkeypatch
) -> None:
    kind, depth, image, _, colors, rect = _flatten(
        tmp_path, monkeypatch, pixmap_24bit=[[RED, GREEN]]
    )
    given = _picture([[BLUE, BLUE]])
    drawn = _draw_flat(
        tmp_path, monkeypatch, (kind, depth, image, [0b01000000], colors, rect), given
    )
    assert drawn.getpixel((0, 0)) == _rgba(BLUE)
    assert drawn.getpixel((1, 0)) == _rgba(GREEN)


def test_draw_flattened_pixmap_of_a_palette_without_colors_raises(
    tmp_path, monkeypatch
) -> None:
    with pytest.raises(NotImplementedError, match="8-bit"):
        _draw_flat(tmp_path, monkeypatch, (0, 8, [0, 0], [], [], (0, 0, 2, 1)))


PICTURE_TO_PIXMAP_PANE = [
    (2, "output", CLUSTER),
    (3, "output", PICTURE),
    (8, "input", I32),
    (9, "input", I32),
    (10, "input", CLUSTER),
    (11, "input", PICTURE),
]


def _to_pixmap(tmp_path, monkeypatch, picture, depth=None, rect=None, bg=WHITE) -> Any:
    inputs: dict[str, Any] = {"picture": (11, PICTURE), "background_color": (8, I32)}
    arguments: dict[str, Any] = {"picture": picture, "background_color": bg}
    if depth is not None:
        inputs["depth"] = (9, I32)
        arguments["depth"] = depth
    if rect is not None:
        inputs["rect"] = (10, CLUSTER)
        arguments["rect"] = rect
    return _call(
        tmp_path,
        monkeypatch,
        [*UTIL, "Picture to Pixmap.vi"],
        PICTURE_TO_PIXMAP_PANE,
        inputs,
        {"image_data": (2, CLUSTER), "new_picture": (3, PICTURE)},
        arguments,
    )


def test_picture_to_pixmap_is_24_bit_of_the_entire_picture_by_default(
    tmp_path, monkeypatch
) -> None:
    picture = _picture([[RED, GREEN, BLUE], [WHITE, RED, GREEN]])
    result = _to_pixmap(tmp_path, monkeypatch, picture)
    kind, depth, image, mask, colors, rect = result.image_data
    assert depth == 24 and rect == (0, 0, 3, 2) and mask == [] and colors == []
    assert image[:9] == [255, 0, 0, 0, 255, 0, 0, 0, 255]
    assert result.new_picture is picture
    assert _unflatten(tmp_path, monkeypatch, result.image_data).pixmap_24bit == [
        [RED, GREEN, BLUE],
        [WHITE, RED, GREEN],
    ]


def test_picture_to_pixmap_rect_crops_and_transparent_pixels_take_the_background(
    tmp_path, monkeypatch
) -> None:
    picture = _picture([[RED, GREEN, BLUE]])
    picture.putpixel((1, 0), (0, 0, 0, 0))
    result = _to_pixmap(tmp_path, monkeypatch, picture, rect=(1, 0, 3, 1), bg=0x123456)
    assert result.image_data[5] == (1, 0, 3, 1)
    assert _unflatten(tmp_path, monkeypatch, result.image_data).pixmap_24bit == [
        [0x123456, BLUE]
    ]


@pytest.mark.parametrize("depth", [1, 4, 8])
def test_picture_to_pixmap_at_a_palette_depth_keeps_the_colors(
    tmp_path, monkeypatch, depth
) -> None:
    picture = _picture([[RED, GREEN], [RED, RED]])
    data = _to_pixmap(tmp_path, monkeypatch, picture, depth=depth).image_data
    assert data[1] == depth
    drawn = _draw_flat(tmp_path, monkeypatch, data)
    expected = [[RED, GREEN], [RED, RED]] if depth > 1 else None
    if expected is not None:
        assert [[drawn.getpixel((x, y)) for x in range(2)] for y in range(2)] == [
            [_rgba(c) for c in row] for row in expected
        ]
    else:
        assert drawn.size == (2, 2)


def test_picture_to_pixmap_of_an_unsupported_depth_raises(
    tmp_path, monkeypatch
) -> None:
    with pytest.raises(NotImplementedError, match="16"):
        _to_pixmap(tmp_path, monkeypatch, _picture([[RED]]), depth=16)


# --- Draw True-Color Pixmap -----------------------------------------------------


def test_draw_true_color_pixmap_places_the_array_at_top_left(
    tmp_path, monkeypatch
) -> None:
    pane = [
        (0, "input", PICTURE),
        (2, "input", CLUSTER),
        (3, "input", ARRAY),
        (4, "input", ARRAY),
        (6, "output", PICTURE),
    ]
    image = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Draw True-Color Pixmap.vi"],
        pane,
        {"top_left": (2, CLUSTER), "data": (3, ARRAY), "mask": (4, ARRAY)},
        {"new_picture": (6, PICTURE)},
        {"top_left": (1, 0), "data": [[RED, GREEN]], "mask": [0b10000000]},
    ).new_picture
    assert image.size == (3, 1)
    assert image.getpixel((1, 0)) == _rgba(RED)
    assert image.getpixel((2, 0)) == (0, 0, 0, 0)


# --- Get Image Subset, Create Mask ----------------------------------------------

SUBSET_PANE = [
    (0, "output", CLUSTER),
    (2, "output", CLUSTER),
    (4, "input", CLUSTER),
    (6, "input", CLUSTER),
]


def test_get_image_subset_returns_the_pixels_of_the_rectangle(
    tmp_path, monkeypatch
) -> None:
    data = _flatten(
        tmp_path,
        monkeypatch,
        pixmap_24bit=[[RED, GREEN, BLUE], [BLUE, RED, GREEN], [WHITE, WHITE, RED]],
    )
    result = _call(
        tmp_path,
        monkeypatch,
        [*UTIL, "Get Image Subset.vi"],
        SUBSET_PANE,
        {"subset_rect": (4, CLUSTER), "image_data": (6, CLUSTER)},
        {"image_subset": (2, CLUSTER), "true_subset_rect": (0, CLUSTER)},
        {"subset_rect": (1, 1, 3, 3), "image_data": data},
    )
    assert result.true_subset_rect == (1, 1, 3, 3)
    assert _unflatten(tmp_path, monkeypatch, result.image_subset).pixmap_24bit == [
        [RED, GREEN],
        [WHITE, RED],
    ]


def test_get_image_subset_coerces_a_rectangle_outside_the_image(
    tmp_path, monkeypatch
) -> None:
    data = _flatten(tmp_path, monkeypatch, pixmap_24bit=[[RED, GREEN], [BLUE, WHITE]])
    result = _call(
        tmp_path,
        monkeypatch,
        [*UTIL, "Get Image Subset.vi"],
        SUBSET_PANE,
        {"subset_rect": (4, CLUSTER), "image_data": (6, CLUSTER)},
        {"image_subset": (2, CLUSTER), "true_subset_rect": (0, CLUSTER)},
        {"subset_rect": (-5, -5, 1, 9), "image_data": data},
    )
    assert result.true_subset_rect == (0, 0, 1, 2)


CREATE_MASK_PANE = [
    (3, "output", CLUSTER),
    (8, "input", BOOL),
    (9, "input", BOOL),
    (10, "input", I32),
    (11, "input", CLUSTER),
]


def _create_mask(tmp_path, monkeypatch, data, **arguments) -> Any:
    slots = {
        "combine_masks": (8, BOOL),
        "mask_value_1bit": (9, BOOL),
        "mask_color": (10, I32),
    }
    inputs = {"image_data": (11, CLUSTER)}
    inputs.update({n: s for n, s in slots.items() if n in arguments})
    return _call(
        tmp_path,
        monkeypatch,
        [*UTIL, "Create Mask.vi"],
        CREATE_MASK_PANE,
        inputs,
        {"image_data_out": (3, CLUSTER)},
        {"image_data": data, **arguments},
    ).image_data_out


def test_create_mask_makes_the_mask_color_transparent(tmp_path, monkeypatch) -> None:
    data = _flatten(tmp_path, monkeypatch, pixmap_24bit=[[RED, GREEN, RED]])
    masked = _create_mask(tmp_path, monkeypatch, data, mask_color=RED)
    assert masked[3] == [0b01000000]
    assert masked[2] == data[2]
    drawn = _draw_flat(tmp_path, monkeypatch, masked, _picture([[BLUE, BLUE, BLUE]]))
    assert [drawn.getpixel((x, 0)) for x in range(3)] == [
        _rgba(BLUE),
        _rgba(GREEN),
        _rgba(BLUE),
    ]


def test_create_mask_combines_with_the_existing_mask_only_when_asked(
    tmp_path, monkeypatch
) -> None:
    once = _create_mask(
        tmp_path,
        monkeypatch,
        _flatten(tmp_path, monkeypatch, pixmap_24bit=[[RED, GREEN, BLUE]]),
        mask_color=RED,
    )
    replaced = _create_mask(tmp_path, monkeypatch, once, mask_color=GREEN)
    combined = _create_mask(
        tmp_path, monkeypatch, once, mask_color=GREEN, combine_masks=True
    )
    assert replaced[3] == [0b10100000]
    assert combined[3] == [0b00100000]


def test_create_mask_of_a_one_bit_image_hides_the_value_named(
    tmp_path, monkeypatch
) -> None:
    data = _flatten(
        tmp_path,
        monkeypatch,
        pixmap_1bit=[[True, False]],
        colors=[0, WHITE],
    )
    assert _create_mask(tmp_path, monkeypatch, data)[3] == [0b01000000]
    assert _create_mask(tmp_path, monkeypatch, data, mask_value_1bit=False)[3] == [
        0b10000000
    ]


def test_bit_array_to_byte_array_packs_rows_high_bit_first(
    tmp_path, monkeypatch
) -> None:
    result = _call(
        tmp_path,
        monkeypatch,
        [*UTIL, "Bit-array To Byte-array.vi"],
        [(0, "output", ARRAY), (1, "input", ARRAY)],
        {"bit_array": (1, ARRAY)},
        {"byte_array": (0, ARRAY)},
        {"bit_array": [[True, False, True], [False] * 8 + [True]]},
    )
    assert result.byte_array == [0b10100000, 0, 0b10000000]


# --- PNG, BMP and JPEG files --------------------------------------------------

READ_PANE = [
    (0, "output", ERROR),
    (2, "output", CLUSTER),
    (3, "output", PATH),
    (8, "input", ERROR),
    (11, "input", PATH),
]
WRITE_PANE = [
    (0, "output", ERROR),
    (3, "output", PATH),
    (8, "input", ERROR),
    (10, "input", CLUSTER),
    (11, "input", PATH),
]


def _write(tmp_path, monkeypatch, library, name, data, extra=None, pane_extra=()):
    path = tmp_path / name
    inputs = {
        "image_data": (10, CLUSTER),
        f"path_to_{Path(name).suffix[1:]}_file": (11, PATH),
    }
    arguments = {"image_data": data, f"path_to_{Path(name).suffix[1:]}_file": path}
    if extra:
        for key, (slot, value) in extra.items():
            inputs[key] = (slot, I32)
            arguments[key] = value
    result = _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "picture", library, name_to_vi("Write", name)],
        [*WRITE_PANE, *pane_extra],
        inputs,
        {"path": (3, PATH)},
        arguments,
    )
    assert result.path == path
    return path


def name_to_vi(verb: str, name: str) -> str:
    return f"{verb} {Path(name).suffix[1:].upper()} File.vi"


def _read(tmp_path, monkeypatch, library, path, extra=None, pane_extra=()) -> Any:
    kind = path.suffix[1:]
    inputs = {f"path_to_{kind}_file": (11, PATH)}
    arguments = {f"path_to_{kind}_file": path}
    for key, (slot, value) in (extra or {}).items():
        inputs[key] = (slot, I32)
        arguments[key] = value
    return _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "picture", library, name_to_vi("Read", path.name)],
        [*READ_PANE, *pane_extra],
        inputs,
        {"image_data": (2, CLUSTER), "path": (3, PATH)},
        arguments,
    )


PIXELS = [[RED, GREEN, BLUE], [WHITE, 0x000000, RED]]


def _image_data(tmp_path, monkeypatch) -> Any:
    return _flatten(tmp_path, monkeypatch, pixmap_24bit=PIXELS)


def test_png_round_trips_the_pixels(tmp_path, monkeypatch) -> None:
    data = _image_data(tmp_path, monkeypatch)
    path = _write(
        tmp_path,
        monkeypatch,
        "png.llb",
        "image.png",
        data,
        pane_extra=[(5, "input", I32)],
    )
    image = PIL_Image.open(path)
    assert image.format == "PNG" and image.size == (3, 2)
    read = _read(
        tmp_path,
        monkeypatch,
        "png.llb",
        path,
        {"transparency_thresh": (6, 128)},
        [(6, "input", I32)],
    )
    assert read.path == path
    assert _unflatten(tmp_path, monkeypatch, read.image_data).pixmap_24bit == PIXELS
    assert read.image_data[3] == []


def test_png_alpha_below_the_threshold_becomes_the_mask(tmp_path, monkeypatch) -> None:
    path = tmp_path / "alpha.png"
    image = PIL_Image.new("RGBA", (2, 1), _rgba(RED))
    image.putpixel((1, 0), _rgba(GREEN, 100))
    image.save(path)
    read = _read(
        tmp_path,
        monkeypatch,
        "png.llb",
        path,
        {"transparency_thresh": (6, 128)},
        [(6, "input", I32)],
    )
    assert read.image_data[3] == [0b10000000]
    read = _read(
        tmp_path,
        monkeypatch,
        "png.llb",
        path,
        {"transparency_thresh": (6, 100)},
        [(6, "input", I32)],
    )
    assert read.image_data[3] == []


def test_bmp_round_trips_the_pixels(tmp_path, monkeypatch) -> None:
    data = _image_data(tmp_path, monkeypatch)
    path = _write(tmp_path, monkeypatch, "bmp.llb", "image.bmp", data)
    assert PIL_Image.open(path).format == "BMP"
    read = _read(tmp_path, monkeypatch, "bmp.llb", path)
    assert _unflatten(tmp_path, monkeypatch, read.image_data).pixmap_24bit == PIXELS


def test_jpeg_writes_a_jpeg_of_the_size_and_reads_it_back(
    tmp_path, monkeypatch
) -> None:
    data = _flatten(tmp_path, monkeypatch, pixmap_24bit=[[RED] * 16 for _ in range(16)])
    path = _write(
        tmp_path,
        monkeypatch,
        "jpeg.llb",
        "image.jpeg",
        data,
        {"quality": (5, 95)},
        [(5, "input", I32)],
    )
    assert PIL_Image.open(path).format == "JPEG"
    read = _read(tmp_path, monkeypatch, "jpeg.llb", path, None, [(6, "input", BOOL)])
    assert read.image_data[5] == (0, 0, 16, 16)
    red = _unflatten(tmp_path, monkeypatch, read.image_data).pixmap_24bit[8][8]
    assert red >> 16 > 240 and red >> 8 & 255 < 16


def test_a_missing_file_is_the_file_not_found_error_out(tmp_path, monkeypatch) -> None:
    path = tmp_path / "missing.png"
    pane = [*READ_PANE, (6, "input", I32)]
    result = _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "picture", "png.llb", "Read PNG File.vi"],
        pane,
        {"path_to_png_file": (11, PATH), "transparency_thresh": (6, I32)},
        {"error_out": (0, ERROR)},
        {"path_to_png_file": path, "transparency_thresh": 128},
    )
    assert result.error_out.status is True
    assert result.error_out.code == 7


def test_a_file_vi_with_no_path_wired_is_a_gap(tmp_path, monkeypatch) -> None:
    gaps: list[Any] = []
    with pytest.raises(NotImplementedError, match="file dialog"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            ["<vilib>", "picture", "png.llb", "Read PNG File.vi"],
            [*READ_PANE, (6, "input", I32)],
            {"transparency_thresh": (6, I32)},
            {"image_data": (2, CLUSTER)},
            {"transparency_thresh": 128},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert any("file dialog" in gap.message for gap in gaps)


def test_a_picture_vi_the_catalog_does_not_describe_stays_a_gap(
    tmp_path, monkeypatch
) -> None:
    gaps: list[Any] = []
    with pytest.raises(NotImplementedError):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*PICT, "Draw 1-Bit Pixmap.vi"],
            [(0, "input", PICTURE), (6, "output", PICTURE)],
            {"picture": (0, PICTURE)},
            {"new_picture": (6, PICTURE)},
            {"picture": None},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert gaps


def test_draw_8_bit_pixmap_looks_each_index_up_in_the_color_table(
    tmp_path, monkeypatch
) -> None:
    pane = [
        (0, "input", PICTURE),
        (2, "input", CLUSTER),
        (3, "input", ARRAY),
        (4, "input", ARRAY),
        (6, "output", PICTURE),
        (8, "input", ARRAY),
    ]
    image = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Draw 8-Bit Pixmap.vi"],
        pane,
        {"data": (3, ARRAY), "color_table": (4, ARRAY)},
        {"new_picture": (6, PICTURE)},
        {"data": [[1, 0], [0, 1]], "color_table": [RED, GREEN]},
    ).new_picture
    assert image.size == (2, 2)
    assert image.getpixel((0, 0)) == _rgba(GREEN)
    assert image.getpixel((1, 0)) == _rgba(RED)
    with pytest.raises(NotImplementedError, match="8-bit"):
        _call(
            tmp_path,
            monkeypatch,
            [*PICT, "Draw 8-Bit Pixmap.vi"],
            pane,
            {"data": (3, ARRAY)},
            {"new_picture": (6, PICTURE)},
            {"data": [[1]]},
        )
