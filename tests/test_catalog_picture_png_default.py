"""Read PNG File's Transparency Thresh when unwired."""

from __future__ import annotations

import pytest

from tests.test_catalog_picture_pixmaps import (
    CLUSTER,
    ERROR,
    I32,
    PATH,
    _call,
)

PIL_Image = pytest.importorskip("PIL.Image")


def test_an_unwired_transparency_thresh_is_127(tmp_path, monkeypatch) -> None:
    path = tmp_path / "alpha.png"
    image = PIL_Image.new("RGBA", (2, 1), (255, 0, 0, 127))
    image.putpixel((1, 0), (0, 255, 0, 126))
    image.save(path)
    result = _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "picture", "png.llb", "Read PNG File.vi"],
        [
            (0, "output", ERROR),
            (2, "output", CLUSTER),
            (3, "output", PATH),
            (6, "input", I32),
            (8, "input", ERROR),
            (11, "input", PATH),
        ],
        {"path_to_png_file": (11, PATH)},
        {"image_data": (2, CLUSTER)},
        {"path_to_png_file": path},
    )
    assert result.image_data[3] == [0b10000000]
