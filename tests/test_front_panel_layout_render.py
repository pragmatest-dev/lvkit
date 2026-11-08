"""Rendering a VI's front panel from its object tree: window placement (scroll
origin, splitter panes, tab pages, groupers, the LabVIEW 8.5 rule), draw order,
the window frame, free labels, decorations, class controls, and the ``--view``
choice of ``render`` and the MCP tool, built from tiny hand-made heaps."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

from lvkit import cli, output_cache
from lvkit.cache_paths import LibraryRoots
from lvkit.mcp import server as srv
from lvkit.models import LVType, LVTypeKind
from lvkit.parser.models import (
    ParsedFPControl,
    ParsedFPPane,
    ParsedFPPart,
    ParsedFPTabPage,
    ParsedFrontPanel,
)
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.geometry import (
    FPBox,
    FPSplitterBar,
    FPWindowFrame,
    build_boxes,
    content_bounds,
)
from lvkit.render_view import RenderView

from .test_fp_structure import (
    _cosm,
    _fpdco,
    _grouper,
    _label,
    _pane,
    _panel,
    _plain,
    _splitter,
    _tab,
    _tab_fpdco,
)

_VI = Path(__file__).parent / "corpus" / "issues" / "36" / "bundle-unbundle-names.vi"


def _placed(boxes: list[FPBox]) -> dict[str, tuple[float, ...]]:
    """Every placed control and decoration by uid (the window frame as
    ``frame``, a splitter bar as ``bar``)."""
    out: dict[str, tuple[float, ...]] = {}
    for box in boxes:
        if isinstance(box.obj, FPWindowFrame):
            out["frame"] = box.bounds
        elif isinstance(box.obj, FPSplitterBar):
            out["bar"] = box.bounds
        else:
            out[box.obj.uid] = box.bounds
    return out


def test_scrolled_pane_places_objects_by_origin_and_frames_the_window(
    tmp_path: Path,
) -> None:
    panel = _panel(
        tmp_path,
        _pane(
            "paneHierarchy",
            "(0, 0, 472, 694)",
            _fpdco(5, "(-230, 299, 246, 997)"),
            origin="(-301, 228)",
        ),
    )
    placed = _placed(build_boxes(panel))
    # (x1, y1, x2, y2): left + origin.x, top + origin.y, right + origin.x, ...
    assert placed["5"] == (-2.0, -2.0, 696.0, 474.0)
    assert placed["frame"] == (0.0, 0.0, 694.0, 472.0)


def test_the_pane_s_own_top_left_is_not_part_of_the_mapping(tmp_path: Path) -> None:
    panel = _panel(
        tmp_path,
        _pane(
            "paneHierarchy", "(-3312, 40, -2937, 438)", _fpdco(5, "(10, 20, 30, 40)")
        ),
    )
    placed = _placed(build_boxes(panel))
    assert placed["5"] == (20.0, 10.0, 40.0, 30.0)
    assert placed["frame"] == (0.0, 0.0, 398.0, 375.0)


def test_side_by_side_splitter_offsets_and_clips_the_right_pane(
    tmp_path: Path,
) -> None:
    panel = _panel(
        tmp_path,
        _splitter(
            "paneHierarchy",
            "(-138, 0, 288, 800)",
            "3EEE147B",
            _pane("topOrLeft", "(0, 0, 426, 372)", _fpdco(5, "(1, 2, 3, 4)")),
            _pane("botOrRight", "(0, 374, 426, 800)", _fpdco(6, "(0, 0, 400, 426)")),
        ),
    )
    boxes = build_boxes(panel)
    placed = _placed(boxes)
    assert placed["6"] == (374.0, 0.0, 800.0, 400.0)
    assert placed["5"] == (2.0, 1.0, 4.0, 3.0)
    clips = {
        box.obj.uid: box.clip for box in boxes if isinstance(box.obj, ParsedFPControl)
    }
    assert clips == {"5": (0.0, 0.0, 372.0, 426.0), "6": (374.0, 0.0, 800.0, 426.0)}
    assert placed["bar"] == (372.0, 0.0, 374.0, 426.0)
    assert placed["frame"] == (0.0, 0.0, 800.0, 426.0)


def test_stacked_nested_splitter_adds_each_pane_offset(tmp_path: Path) -> None:
    inner = _splitter(
        "botOrRight",
        "(60, 0, 525, 477)",
        "3F000000",
        _pane("topOrLeft", "(0, 0, 200, 230)"),
        _pane("botOrRight", "(0, 232, 200, 477)", _fpdco(7, "(5, 6, 15, 16)")),
    )
    panel = _panel(
        tmp_path,
        _splitter(
            "paneHierarchy",
            "(92, 0, 617, 477)",
            "3DE24157",
            _pane("topOrLeft", "(0, 0, 58, 477)"),
            inner,
        ),
    )
    placed = _placed(build_boxes(panel))
    # the nested splitter starts at y 60; its right pane at x 232 inside it
    assert placed["7"] == (238.0, 65.0, 248.0, 75.0)
    assert placed["frame"] == (0.0, 0.0, 477.0, 525.0)


def test_tab_control_draws_only_the_shown_page_at_the_tab_top_left(
    tmp_path: Path,
) -> None:
    tab = _tab(
        50,
        ["Inputs", "Status"],
        [_fpdco(61, "(52, 26, 92, 400)"), _fpdco(62, "(60, 30, 80, 90)")],
        shown="Status",
    )
    panel = _plain(tmp_path, _tab_fpdco(50, tab))
    placed = _placed(build_boxes(panel))
    assert "61" not in placed
    # the tab's box starts at (-12, -37)
    assert placed["50"] == (-12.0, -37.0, 506.0, 384.0)
    assert placed["62"] == (18.0, 23.0, 78.0, 43.0)


def test_page_children_of_a_vi_saved_before_labview_9_stay_where_stored(
    tmp_path: Path,
) -> None:
    tab = _tab(50, ["A"], [_fpdco(61, "(52, 26, 92, 400)")], shown="A")
    panel = _plain(tmp_path, _tab_fpdco(50, tab))
    panel.lv_version = "8.5.0"
    assert _placed(build_boxes(panel))["61"] == (26.0, 52.0, 400.0, 92.0)
    panel.lv_version = "9.0.0"
    assert _placed(build_boxes(panel))["61"] == (14.0, 15.0, 388.0, 55.0)
    panel.lv_version = None
    assert _placed(build_boxes(panel))["61"] == (14.0, 15.0, 388.0, 55.0)


def test_grouper_children_sit_at_the_grouper_top_left(tmp_path: Path) -> None:
    panel = _plain(
        tmp_path,
        _grouper(9, "(10, 20, 50, 80)", _fpdco(5, "(5, 5, 15, 15)")),
        origin="(3, 4)",
    )
    placed = _placed(build_boxes(panel))
    assert placed["5"] == (28.0, 19.0, 38.0, 29.0)
    panel.lv_version = "8.5.0"
    assert _placed(build_boxes(panel))["5"] == (8.0, 9.0, 18.0, 19.0)


def test_objects_draw_last_to_first_and_the_frame_last(tmp_path: Path) -> None:
    panel = _plain(
        tmp_path,
        _fpdco(5, "(0, 0, 10, 10)"),
        _cosm(6, "(0, 0, 20, 20)"),
        _fpdco(7, "(0, 0, 30, 30)"),
    )
    order = [
        "frame" if isinstance(b.obj, FPWindowFrame) else b.obj.uid
        for b in build_boxes(panel)
    ]
    assert order == ["7", "6", "5", "frame"]


def test_canvas_is_the_union_of_every_object_and_the_window_padded(
    tmp_path: Path,
) -> None:
    panel = _plain(tmp_path, _fpdco(5, "(-20, 380, 10, 450)"))
    bounds = content_bounds(build_boxes(panel))
    assert bounds == (-12.0, -32.0, 462.0, 312.0)


def test_a_clipped_object_does_not_widen_the_canvas(tmp_path: Path) -> None:
    panel = _panel(
        tmp_path,
        _splitter(
            "paneHierarchy",
            "(0, 0, 100, 200)",
            "3F000000",
            _pane("topOrLeft", "(0, 0, 100, 98)", _cosm(6, "(0, 0, 100, 900)")),
            _pane("botOrRight", "(0, 100, 100, 200)"),
        ),
    )
    assert content_bounds(build_boxes(panel)) == (-12.0, -12.0, 212.0, 112.0)


def test_a_panel_without_an_object_tree_draws_controls_at_stored_bounds() -> None:
    ctrl = ParsedFPControl(
        uid="1", name="N", control_type="stdNum", bounds=(10, 20, 30, 60)
    )
    boxes = build_boxes(ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 9, 9)))
    assert [(b.obj, b.bounds, b.clip) for b in boxes] == [
        (ctrl, (20.0, 10.0, 60.0, 30.0), None)
    ]


def _rect_fills(svg: str) -> list[str]:
    return re.findall(r'<rect [^>]*fill="([^"]*)"', svg)


def test_free_label_text_is_drawn_per_line_in_its_colour(tmp_path: Path) -> None:
    panel = _plain(
        tmp_path,
        _label(6, "(10, 20, 40, 220)", "First&#x0A;Second"),
    )
    svg = render_front_panel_svg(panel)
    assert ">First<" in svg
    assert ">Second<" in svg
    assert 'fill="#112233"' in svg


def test_decoration_shapes_come_from_the_table(tmp_path: Path) -> None:
    panel = _plain(
        tmp_path,
        _cosm(6, "(10, 10, 100, 100)", image=-412),
        _cosm(7, "(10, 120, 100, 200)", image=-9999),
    )
    svg = render_front_panel_svg(panel)
    # -412: the filled backdrop, in the decoration's own recorded fill colour
    assert (
        '<rect x="10.0" y="10.0" width="90.0" height="90.0" fill="#AABBCC"/>' in svg
    )
    # an id the evidence does not settle: its box outline, no fill
    assert (
        '<rect x="120.5" y="10.5" width="79.0" height="89.0" fill="none" '
        'stroke="#1e1e1e" stroke-width="1.0"/>'
    ) in svg


def test_a_class_control_draws_the_class_glyph_not_the_placeholder() -> None:
    ctrl = ParsedFPControl(
        uid="1",
        name="Obj",
        control_type="udClassDDO",
        bounds=(0, 0, 80, 100),
        lv_type=LVType(
            kind=LVTypeKind.CLASS, underlying_type="Refnum", classname="Device.lvclass"
        ),
    )
    svg = render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 100, 100))
    )
    assert "udClassDDO" not in svg
    assert "stroke-dasharray" not in svg
    assert "Device.lvclass" in svg


def test_tab_control_draws_its_names_with_the_shown_page_joined(
    tmp_path: Path,
) -> None:
    tab = _tab(50, ["Inputs", "Status"], [_fpdco(61, "(1, 1, 2, 2)")] * 2, "Status")
    svg = render_front_panel_svg(_plain(tmp_path, _tab_fpdco(50, tab)))
    assert "tabControl" not in svg
    assert "stroke-dasharray" not in svg


def _tab_with_row() -> ParsedFPControl:
    """A tab control shaped like Line Configuration Dialog.vi's: the frame is the
    control's box and the tab row lies above it."""
    row = ParsedFPPart(part_id=93, part_class="cosm", bounds=(-25, 0, 4, 120))
    return ParsedFPControl(
        uid="9",
        name="Tabs",
        control_type="tabControl",
        bounds=(100, 50, 200, 250),
        parts=[
            ParsedFPPart(part_id=28, part_class="multiCosm", bounds=(0, 0, 100, 200)),
            ParsedFPPart(
                part_id=8018,
                part_class="tabArray",
                bounds=(0, 0, 100, 200),
                parts=[row],
            ),
        ],
        tab_pages=[
            ParsedFPTabPage("One", (5, 5, 95, 195), []),
            ParsedFPTabPage("Two", (5, 5, 95, 195), []),
        ],
        tab_selected=1,
    )


def test_tab_control_draws_every_name_in_a_row_above_its_frame() -> None:
    tab = _tab_with_row()
    panel = ParsedFrontPanel(
        controls=[tab],
        panel_bounds=(0, 0, 400, 400),
        layout=ParsedFPPane(bounds=(0, 0, 400, 400), origin=(0, 0), objects=[tab]),
    )
    svg = render_front_panel_svg(panel)
    assert ">One<" in svg
    assert ">Two<" in svg
    assert "tabControl" not in svg
    # the page frame is the control's box, the row is 25 px above it
    assert '<rect x="50.5" y="100.5" width="199.0" height="99.0"' in svg
    # the canvas reaches the row above the control's own bounds
    box = build_boxes(panel)[0]
    assert box.extent == (50.0, 75.0, 250.0, 200.0)
    assert content_bounds([box])[1] == 63.0


# --- render --view -------------------------------------------------------


def _render(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", "render", *args])
    return cli.main()


def test_view_front_panel_writes_the_panel_not_the_diagram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "t.svg"
    assert _render(monkeypatch, str(_VI), "--view", "front-panel", "-o", str(out)) == 0
    svg = out.read_text(encoding="utf-8")
    assert ">Elements in array<" in svg
    assert "data-node" not in svg


def test_view_both_writes_two_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "t.svg"
    assert _render(monkeypatch, str(_VI), "--view", "both", "-o", str(out)) == 0
    diagram = (tmp_path / "t.block-diagram.svg").read_text(encoding="utf-8")
    panel = (tmp_path / "t.front-panel.svg").read_text(encoding="utf-8")
    assert not out.exists()
    assert "data-node" in diagram
    assert "data-node" not in panel
    assert ">Elements in array<" in panel


def test_each_view_has_its_own_cache_slot_and_options_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vi = tmp_path / "Names.vi"
    vi.write_bytes(_VI.read_bytes())
    assert _render(monkeypatch, str(vi), "--view", "both") == 0
    diagram_slot = output_cache.render_slot(
        vi, "svg", LibraryRoots(), RenderView.BLOCK_DIAGRAM
    )
    panel_slot = output_cache.render_slot(
        vi, "svg", LibraryRoots(), RenderView.FRONT_PANEL
    )
    assert diagram_slot != panel_slot
    assert diagram_slot.read_text(encoding="utf-8") != panel_slot.read_text(
        encoding="utf-8"
    )
    assert output_cache.render_options_tag(
        "svg", "light", None, view=RenderView.BLOCK_DIAGRAM
    ) != output_cache.render_options_tag(
        "svg", "light", None, view=RenderView.FRONT_PANEL
    )


def test_a_folder_render_with_both_views_writes_both_pictures_per_vi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Names.vi").write_bytes(_VI.read_bytes())
    out = tmp_path / "out"
    assert _render(monkeypatch, str(src), "--view", "both", "-o", str(out)) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "Names.block-diagram.svg",
        "Names.front-panel.svg",
    ]


def test_a_control_has_no_block_diagram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ctl = tmp_path / "C.ctl"
    ctl.write_bytes(b"not read")
    code = _render(monkeypatch, str(ctl), "--view", "block-diagram")
    assert code == 2
    assert "only a front panel" in capsys.readouterr().err


def test_mcp_render_both_returns_both_paths() -> None:
    result = asyncio.run(srv.render(str(_VI), view="both"))
    paths = result["render_paths"]
    assert set(paths) == {"block_diagram", "front_panel"}
    assert result["render_path"] == paths["block_diagram"]
    assert ">Elements in array<" in Path(paths["front_panel"]).read_text("utf-8")
    assert "data-node" in Path(paths["block_diagram"]).read_text("utf-8")


def test_mcp_render_rejects_a_bad_view() -> None:
    with pytest.raises(ValueError, match="view must be one of"):
        asyncio.run(srv.render(str(_VI), view="side"))
