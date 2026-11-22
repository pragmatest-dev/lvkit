"""The image data cluster of the picture VIs has one representation per cluster
type: a typedef cluster is an attribute object (read by name), an anonymous
cluster a tuple (``codegen/nodes/picture.py``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.test_catalog_picture_pixmaps import (
    ARRAY,
    DRAW_FLAT_PANE,
    FLATTEN_IN,
    FLATTEN_PANE,
    PICT,
    PICTURE,
    PIXMAP,
    PIXMAP_PANE_OUT,
    RED,
    UNFLATTEN_OUT,
    _call,
)
from tests.vilib_caller import I32

PIL_Image = pytest.importorskip("PIL.Image")

RECT = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[ClusterField(name=name, type=I32) for name in "abcd"],
)
_NAMES = ("Image Type", "Image Depth", "Image", "Mask", "Colors", "Rectangle")
_TYPES = (I32, I32, ARRAY, ARRAY, ARRAY, RECT)


def _image_data(typedef_name: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name=typedef_name,
        fields=[ClusterField(name=n, type=t) for n, t in zip(_NAMES, _TYPES)],
    )


def _flatten(tmp_path, monkeypatch, image_type: LVType) -> Any:
    pane = [(2, "output", image_type), *FLATTEN_PANE[1:]]
    inputs = {name: FLATTEN_IN[name] for name in ("pixmap_24bit", "top_left")}
    return _call(
        tmp_path,
        monkeypatch,
        [*PIXMAP, "Flatten Pixmap.vi"],
        pane,
        inputs,
        {"image_data": (2, image_type)},
        {"pixmap_24bit": [[RED, RED]], "top_left": (1, 2)},
    ).image_data


def test_flatten_into_a_typedef_makes_the_attribute_object(
    tmp_path, monkeypatch
) -> None:
    data = _flatten(tmp_path, monkeypatch, _image_data("ImageData.ctl"))
    assert data.image_depth == 24
    assert data.rectangle == (1, 2, 3, 3)
    assert data.image == [255, 0, 0, 255, 0, 0]


def test_flatten_into_a_plain_cluster_makes_a_tuple(tmp_path, monkeypatch) -> None:
    data = _flatten(tmp_path, monkeypatch, _image_data(None))
    assert isinstance(data, tuple)
    assert data[1] == 24 and data[5] == (1, 2, 3, 3)


@pytest.mark.parametrize("typedef_name", ["ImageData.ctl", None])
def test_unflatten_and_draw_read_either_representation(
    tmp_path, monkeypatch, typedef_name
) -> None:
    image_type = _image_data(typedef_name)
    made = _flatten(tmp_path, monkeypatch, image_type)
    unflatten_pane = [*PIXMAP_PANE_OUT[:-1], (10, "input", image_type)]
    result = _call(
        tmp_path,
        monkeypatch,
        [*PIXMAP, "Unflatten Pixmap.vi"],
        unflatten_pane,
        {"image_data": (10, image_type)},
        UNFLATTEN_OUT,
        {"image_data": made},
    )
    assert result.pixmap_24bit == [[RED, RED]]
    assert result.top_left == (1, 2)
    draw_pane = [DRAW_FLAT_PANE[0], (8, "input", image_type), DRAW_FLAT_PANE[2]]
    picture = _call(
        tmp_path,
        monkeypatch,
        [*PICT, "Draw Flattened Pixmap.vi"],
        draw_pane,
        {"image_data": (8, image_type)},
        {"new_picture": (3, PICTURE)},
        {"image_data": made},
    ).new_picture
    assert picture.getpixel((1, 2))[:3] == (255, 0, 0)


def test_a_typedef_made_by_hand_is_read_by_name(tmp_path, monkeypatch) -> None:
    image_type = _image_data("ImageData.ctl")
    handmade = SimpleNamespace(
        image_type=0,
        image_depth=24,
        image=[0, 255, 0, 0],
        mask=[],
        colors=[],
        rectangle=(0, 0, 1, 1),
    )
    result = _call(
        tmp_path,
        monkeypatch,
        [*PIXMAP, "Unflatten Pixmap.vi"],
        [*PIXMAP_PANE_OUT[:-1], (10, "input", image_type)],
        {"image_data": (10, image_type)},
        UNFLATTEN_OUT,
        {"image_data": handmade},
    )
    assert result.pixmap_24bit == [[0x00FF00]]
