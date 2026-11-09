"""A Subpanel control (``stdSubPanel``) on the front panel: read into the
object tree with its parts, and drawn from them, built from tiny hand-made heaps
shaped like JKI VI Tester's ``EXE Test.vi``."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.cache_paths import LibraryRoots
from lvkit.extractor import resolve_extracted
from lvkit.parser.models import ParsedFPGrouper, ParsedFPPane, ParsedFPSubPanel
from lvkit.parser.vi import parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.geometry import build_boxes

from .conftest import SAMPLES_ROOT
from .test_fp_structure import _cosm, _fpdco, _grouper, _label, _plain


def _part(
    uid: int, part_id: int, bounds: str, image: int, color: str | None = None
) -> str:
    colour = f"<fgColor>{color}</fgColor><bgColor>{color}</bgColor>" if color else ""
    return (
        f'<SL__arrayElement class="cosm" uid="{uid}"><objFlags>2355</objFlags>'
        f"<partID>{part_id}</partID><bounds>{bounds}</bounds>"
        f"<image><ImageResID>{image}</ImageResID></image>{colour}"
        "</SL__arrayElement>"
    )


def _subpanel(frame_image: int = -412) -> str:
    label = _label(213, "(0, 0, 15, 50)", "Sub Panel").replace(
        "<objFlags>0</objFlags>", "<objFlags>0</objFlags><partID>16</partID>"
    )
    parts = (
        label
        + _part(214, 28, "(20, 3, 614, 933)", -239, "00B3B3B3")
        + _part(215, 39, "(20, 933, 614, 933)", -9)
        + _part(216, 38, "(614, 3, 614, 933)", -9)
        + _part(217, 9, "(17, 0, 617, 936)", frame_image)
        + '<SL__arrayElement class="annex" uid="218"><partID>66</partID>'
        "<bounds>(0, 0, 10, 10)</bounds></SL__arrayElement>"
    )
    return (
        '<SL__arrayElement class="stdSubPanel" uid="211"><howGrow>240</howGrow>'
        f'<bounds>(0, 0, 617, 936)</bounds><partsList elements="6">{parts}'
        "</partsList></SL__arrayElement>"
    )


def _panel_with_subpanel(tmp_path: Path, frame_image: int = -412):  # noqa: ANN202
    return _plain(tmp_path, _grouper(9, "(141, 60, 758, 996)", _subpanel(frame_image)))


def test_a_subpanel_is_read_with_its_label_and_parts(tmp_path: Path) -> None:
    panel = _panel_with_subpanel(tmp_path)
    assert panel.unread_objects == []
    layout = panel.layout
    assert isinstance(layout, ParsedFPPane)
    (grouper,) = layout.objects
    assert isinstance(grouper, ParsedFPGrouper)
    (subpanel,) = grouper.objects
    assert isinstance(subpanel, ParsedFPSubPanel)
    assert subpanel.uid == "211"
    assert subpanel.bounds == (0, 0, 617, 936)
    assert subpanel.label is not None
    assert subpanel.label.text == "Sub Panel"
    assert subpanel.label.bounds == (0, 0, 15, 50)
    assert [p.part_id for p in subpanel.parts] == [16, 28, 39, 38, 9, 66]
    frame = subpanel.parts[4]
    assert frame.bounds == (17, 0, 617, 936)
    assert frame.image_ids == (-412,)
    assert subpanel.parts[1].bg_color == "#B3B3B3"


def test_a_subpanel_is_placed_at_its_grouper_top_left(tmp_path: Path) -> None:
    boxes = build_boxes(_panel_with_subpanel(tmp_path))
    placed = {b.obj.uid: b.bounds for b in boxes if hasattr(b.obj, "uid")}
    assert placed["211"] == (60.0, 141.0, 996.0, 758.0)


def test_a_modern_subpanel_draws_its_content_area_frame_and_label(
    tmp_path: Path,
) -> None:
    svg = render_front_panel_svg(_panel_with_subpanel(tmp_path))
    # the content area: part 28 (stored top 20, left 3, bottom 614, right 933)
    # at the grouper's left 60 / top 141, in its own stored colour
    assert (
        '<rect x="63.0" y="161.0" width="930.0" height="594.0" fill="#B3B3B3"/>' in svg
    )
    # the frame's bevel starts at the frame part's top-left (left 0, top 17)
    assert (
        '<polygon points="60.0,158.0 996.0,158.0 995.0,159.0 61.0,159.0" '
        'fill="rgba(0,0,0,0.14)"/>'
    ) in svg
    # the caption, at its part's left and vertical middle
    assert (
        '<text x="61.0" y="152.3" font-size="11.25" text-anchor="start" '
        'fill="#112233">Sub Panel</text>'
    ) in svg


def test_a_subpanel_frame_not_in_the_style_table_is_a_plain_box(
    tmp_path: Path,
) -> None:
    svg = render_front_panel_svg(_panel_with_subpanel(tmp_path, frame_image=-33))
    assert (
        '<rect x="63.0" y="161.0" width="930.0" height="594.0" fill="#B3B3B3"/>' in svg
    )
    assert '<rect x="60.5" y="158.5" width="935.0" height="599.0"' in svg
    assert ">Sub Panel<" in svg
    assert "rgba(0,0,0" not in svg.split("</style>")[1]


def test_a_neighbouring_decoration_and_control_draw_as_before(
    tmp_path: Path,
) -> None:
    objects = (_cosm(6, "(10, 10, 100, 100)", image=-412), _fpdco(5, "(0, 0, 10, 10)"))
    plain = render_front_panel_svg(_plain(tmp_path, *objects))
    with_subpanel = render_front_panel_svg(_plain(tmp_path, *objects, _subpanel()))
    drawn = [
        line
        for line in plain.split("</style>")[1].splitlines()
        if line.startswith("<") and line != "</svg>"
    ]
    # skip the canvas background (its size follows the content)
    assert all(line in with_subpanel for line in drawn[1:])


_EXE_TEST = SAMPLES_ROOT / "JKI-VI-Tester/source/User Interfaces/EXE Test.vi"


@pytest.mark.slow
def test_exe_test_front_panel_loads_with_its_subpanel() -> None:
    if not _EXE_TEST.is_file():
        pytest.skip("JKI-VI-Tester sample not pulled")
    bd_xml, fp_xml, main_xml = resolve_extracted(_EXE_TEST, roots=LibraryRoots())
    panel = parse_vi(bd_xml=bd_xml, fp_xml=fp_xml, main_xml=main_xml).front_panel
    assert panel.unread_objects == []
