"""The front panel's object tree (``ParsedFrontPanel.layout``): panes,
splitters, tab pages, groupers, free labels and decorations, built from tiny
hand-made heaps; plus the real VIs the format facts were read from."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.extractor import resolve_extracted
from lvkit.parser.fp_structure import read_panel_layout
from lvkit.parser.models import (
    ParsedBlockDiagram,
    ParsedFPControl,
    ParsedFPDecoration,
    ParsedFPFontRun,
    ParsedFPGrouper,
    ParsedFPLabel,
    ParsedFPPane,
    ParsedFPSplitter,
    ParsedFrontPanel,
)
from lvkit.parser.vi import _parse_front_panel, parse_vi
from lvkit.parser.xml_trees import parsed_xml

from .conftest import SAMPLES_ROOT

_EMPTY_BD = ParsedBlockDiagram(nodes=[], constants=[], wires=[])


def _fpdco(uid: int, bounds: str, ddo_class: str = "stdBool", extra: str = "") -> str:
    return (
        f'<SL__arrayElement class="fPDCO" uid="{uid}">{extra}'
        f'<ddo class="{ddo_class}" uid="{uid + 1000}">'
        f"<bounds>{bounds}</bounds></ddo></SL__arrayElement>"
    )


def _label(uid: int, bounds: str, text: str, flags: int = 0, runs: str = "") -> str:
    return (
        f'<SL__arrayElement class="label" uid="{uid}"><objFlags>{flags}</objFlags>'
        f"<bounds>{bounds}</bounds><fgColor>00112233</fgColor>"
        f"<bgColor>01000000</bgColor>"
        f'<textRec class="textHair"><text>"{text}"</text>'
        f'<fr elements="2">{runs}</fr></textRec></SL__arrayElement>'
    )


def _run(font_id: int, offset: str = "", color: str = "") -> str:
    ofst = f"<fontofst>{offset}</fontofst>" if offset else ""
    col = f"<fontcolor>{color}</fontcolor>" if color else ""
    return (
        f'<SL__arrayElement class="fontRun"><fontid>{font_id}</fontid>'
        f"{ofst}{col}</SL__arrayElement>"
    )


def _cosm(uid: int, bounds: str, image: int = -412) -> str:
    return (
        f'<SL__arrayElement class="cosm" uid="{uid}"><bounds>{bounds}</bounds>'
        f"<image><ImageResID>{image}</ImageResID></image>"
        f"<bgColor>00AABBCC</bgColor></SL__arrayElement>"
    )


def _grouper(uid: int, bounds: str, *children: str) -> str:
    return (
        f'<SL__arrayElement class="grouper" uid="{uid}"><bounds>{bounds}</bounds>'
        f'<partsList elements="{len(children)}">{"".join(children)}</partsList>'
        "</SL__arrayElement>"
    )


def _pane(
    tag: str,
    bounds: str,
    *objects: str,
    origin: str = "",
    attrs: str = 'class="pane"',
) -> str:
    org = f"<origin>{origin}</origin>" if origin else ""
    return (
        f"<{tag} {attrs}><bounds>{bounds}</bounds>{org}"
        f'<zPlaneList elements="{len(objects)}">{"".join(objects)}</zPlaneList>'
        f"</{tag}>"
    )


def _splitter(tag: str, bounds: str, ratio: str, first: str, second: str) -> str:
    return (
        f'<{tag} class="splitter"><bounds>{bounds}</bounds>{first}{second}'
        f"<splitterFlags>01</splitterFlags><ratio>{ratio}</ratio></{tag}>"
    )


def _tab(uid: int, captions: list[str], pages: list[str], shown: str | None) -> str:
    infos = "".join(
        f'<SL__arrayElement class="PageInfoItem"><caption>{c.encode().hex()}'
        "</caption></SL__arrayElement>"
        for c in captions
    )
    page_xml = "".join(
        f'<SL__arrayElement class="page" uid="{uid + 10 + i}">'
        f"<bounds>(52, 26, 397, 400)</bounds>"
        f'<zPlaneList elements="1">{page}</zPlaneList></SL__arrayElement>'
        for i, page in enumerate(pages)
    )
    label = (
        ""
        if shown is None
        else (
            '<partsList><SL__arrayElement class="tabArray"><partsList>'
            '<SL__arrayElement class="label"><textRec class="textHair">'
            f'<text>"{shown}"</text></textRec></SL__arrayElement>'
            "</partsList></SL__arrayElement></partsList>"
        )
    )
    return (
        f'<ddo class="tabControl" uid="{uid + 1000}">'
        f"<bounds>(-37, -12, 384, 506)</bounds>{label}"
        f"<pageList>{page_xml}</pageList>"
        f"<tabControlPageInfoArray>{infos}</tabControlPageInfoArray></ddo>"
    )


def _tab_fpdco(uid: int, tab: str, extra: str = "") -> str:
    return (
        f'<SL__arrayElement class="fPDCO" uid="{uid}">{extra}{tab}</SL__arrayElement>'
    )


def _panel(tmp_path: Path, hierarchy: str) -> ParsedFrontPanel:
    path = tmp_path / "vi_FPHb.xml"
    path.write_text(
        '<SL__rootObject class="oHExt" uid="1"><root class="supC" uid="10">'
        f"{hierarchy}</root></SL__rootObject>"
    )
    return _parse_front_panel(path, _EMPTY_BD)


def _plain(tmp_path: Path, *objects: str, origin: str = "") -> ParsedFrontPanel:
    return _panel(
        tmp_path,
        _pane("paneHierarchy", "(0, 0, 300, 400)", *objects, origin=origin),
    )


def _control_summary(panel: ParsedFrontPanel) -> list[tuple[str, str, tuple]]:
    return [(c.uid, c.control_type, c.bounds) for c in panel.controls]


def test_plain_pane_keeps_order_origin_and_the_same_control_objects(
    tmp_path: Path,
) -> None:
    panel = _plain(
        tmp_path,
        _fpdco(5, "(10, 20, 30, 40)"),
        _label(
            6,
            "(1, 2, 3, 4)",
            "Hello",
            flags=0x8,
            runs=_run(1) + _run(3, "000A", "001400DC"),
        ),
        _cosm(7, "(0, 0, 300, 400)"),
        _fpdco(8, "(50, 60, 70, 80)"),
        origin="(-2, -1)",
    )
    layout = panel.layout
    assert isinstance(layout, ParsedFPPane)
    assert layout.bounds == (0, 0, 300, 400)
    assert layout.origin == (-2, -1)
    first, label, deco, last = layout.objects
    assert first is panel.controls[0]
    assert last is panel.controls[1]
    assert label == ParsedFPLabel(
        uid="6",
        bounds=(1, 2, 3, 4),
        text="Hello",
        fg_color="#112233",
        bg_color=None,
        hidden=True,
        font_runs=[
            ParsedFPFontRun(offset=0, font_id=1, color=None),
            ParsedFPFontRun(offset=10, font_id=3, color="#1400DC"),
        ],
    )
    assert deco == ParsedFPDecoration(
        uid="7",
        bounds=(0, 0, 300, 400),
        image_res_id=-412,
        fg_color=None,
        bg_color="#AABBCC",
    )
    assert _control_summary(panel) == [
        ("5", "stdBool", (10, 20, 30, 40)),
        ("8", "stdBool", (50, 60, 70, 80)),
    ]


def test_a_pane_without_origin_has_origin_zero(tmp_path: Path) -> None:
    layout = _plain(tmp_path).layout
    assert isinstance(layout, ParsedFPPane)
    assert layout.origin == (0, 0)
    assert layout.objects == []


def test_splitter_side_by_side(tmp_path: Path) -> None:
    hierarchy = _splitter(
        "paneHierarchy",
        "(-138, 0, 262, 800)",
        "3EEE147B",
        _pane("topOrLeft", "(0, 0, 400, 372)", _fpdco(5, "(1, 2, 3, 4)")),
        _pane("botOrRight", "(0, 374, 400, 800)", _cosm(6, "(0, 0, 9, 9)")),
    )
    panel = _panel(tmp_path, hierarchy)
    layout = panel.layout
    assert isinstance(layout, ParsedFPSplitter)
    assert layout.bounds == (-138, 0, 262, 800)
    assert layout.ratio == pytest.approx(0.465, abs=1e-4)
    assert layout.side_by_side is True
    assert isinstance(layout.first, ParsedFPPane)
    assert isinstance(layout.second, ParsedFPPane)
    assert layout.first.bounds == (0, 0, 400, 372)
    assert layout.second.bounds == (0, 374, 400, 800)
    assert layout.first.objects == [panel.controls[0]]
    assert layout.first.objects[0] is panel.controls[0]
    assert [type(o) for o in layout.second.objects] == [ParsedFPDecoration]


def test_splitter_stacked_and_nested(tmp_path: Path) -> None:
    inner = _splitter(
        "botOrRight",
        "(60, 0, 525, 477)",
        "3F000000",
        _pane("topOrLeft", "(0, 0, 200, 230)"),
        _pane("botOrRight", "(0, 232, 200, 477)"),
    )
    hierarchy = _splitter(
        "paneHierarchy",
        "(92, 0, 617, 477)",
        "3DE24157",
        _pane("topOrLeft", "(0, 0, 58, 477)"),
        inner,
    )
    layout = _panel(tmp_path, hierarchy).layout
    assert isinstance(layout, ParsedFPSplitter)
    assert layout.side_by_side is False
    assert layout.ratio == pytest.approx(0.1105, abs=1e-4)
    nested = layout.second
    assert isinstance(nested, ParsedFPSplitter)
    assert nested.ratio == 0.5
    assert nested.side_by_side is True
    assert isinstance(nested.first, ParsedFPPane)
    assert nested.first.bounds == (0, 0, 200, 230)


def test_tab_control_pages_names_and_shown_page(tmp_path: Path) -> None:
    tab = _tab(
        50,
        ["Inputs", "Status"],
        [_fpdco(61, "(52, 26, 92, 400)"), _fpdco(62, "(60, 30, 80, 90)")],
        shown="Status",
    )
    panel = _plain(tmp_path, _tab_fpdco(50, tab))
    (tab_control,) = [c for c in panel.controls if c.control_type == "tabControl"]
    layout = panel.layout
    assert isinstance(layout, ParsedFPPane)
    assert layout.objects == [tab_control]
    assert tab_control.tab_selected == 1
    assert [p.name for p in tab_control.tab_pages] == ["Inputs", "Status"]
    assert [p.bounds for p in tab_control.tab_pages] == [(52, 26, 397, 400)] * 2
    by_uid = {c.uid: c for c in panel.controls}
    assert tab_control.tab_pages[0].objects == [by_uid["61"]]
    assert tab_control.tab_pages[0].objects[0] is by_uid["61"]
    assert tab_control.tab_pages[1].objects[0] is by_uid["62"]
    assert len(panel.controls) == 3


def test_tab_control_default_data_gives_the_shown_page(tmp_path: Path) -> None:
    tab = _tab(
        50,
        ["Templates", "VI", "Text Tool"],
        [
            _fpdco(61, "(1, 1, 2, 2)"),
            _fpdco(62, "(1, 1, 2, 2)"),
            _fpdco(63, "(1, 1, 2, 2)"),
        ],
        shown="Templates",
    )
    default = '<DefaultData>"&amp;#x00;&amp;#x00;&amp;#x00;&amp;#x02;"</DefaultData>'
    panel = _plain(tmp_path, _tab_fpdco(50, tab, extra=default))
    (tab_control,) = [c for c in panel.controls if c.control_type == "tabControl"]
    assert tab_control.tab_selected == 2
    assert [p.name for p in tab_control.tab_pages] == ["Templates", "VI", "Text Tool"]


def test_tab_control_with_no_shown_text_has_no_selected_page(tmp_path: Path) -> None:
    tab = _tab(50, ["A"], [_fpdco(61, "(1, 1, 2, 2)")], shown=None)
    panel = _plain(tmp_path, _tab_fpdco(50, tab))
    (tab_control,) = [c for c in panel.controls if c.control_type == "tabControl"]
    assert tab_control.tab_selected is None


def test_tab_pages_without_captions_get_empty_names(tmp_path: Path) -> None:
    tab = _tab(
        50, ["Only"], [_fpdco(61, "(1, 1, 2, 2)"), _fpdco(62, "(1, 1, 2, 2)")], "Only"
    )
    panel = _plain(tmp_path, _tab_fpdco(50, tab))
    (tab_control,) = [c for c in panel.controls if c.control_type == "tabControl"]
    assert [p.name for p in tab_control.tab_pages] == ["Only", ""]


def test_grouper_holds_a_control_and_a_decoration(tmp_path: Path) -> None:
    panel = _plain(
        tmp_path,
        _grouper(
            9,
            "(8, 153, 97, 506)",
            _fpdco(5, "(0, 0, 41, 183)", "stdRing"),
            _cosm(6, "(0, 0, 89, 353)", -33),
        ),
    )
    layout = panel.layout
    assert isinstance(layout, ParsedFPPane)
    (grouper,) = layout.objects
    assert isinstance(grouper, ParsedFPGrouper)
    assert grouper.bounds == (8, 153, 97, 506)
    control, deco = grouper.objects
    assert control is panel.controls[0]
    assert control.bounds == (0, 0, 41, 183)
    assert isinstance(deco, ParsedFPDecoration)
    assert deco.image_res_id == -33
    assert len(panel.controls) == 1


def test_a_panel_without_pane_hierarchy_has_no_layout(tmp_path: Path) -> None:
    path = tmp_path / "vi_FPHb.xml"
    path.write_text(
        '<SL__rootObject class="oHExt"><root class="supC"></root></SL__rootObject>'
    )
    panel = _parse_front_panel(path, _EMPTY_BD)
    assert panel.layout is None


def test_unknown_object_classes_and_missing_bounds_are_reported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "vi_FPHb.xml"
    path.write_text(
        '<SL__rootObject class="oHExt"><root class="supC">'
        + _pane(
            "paneHierarchy",
            "(0, 0, 10, 10)",
            '<SL__arrayElement class="mystery" uid="3"><bounds>(0, 0, 1, 1)</bounds>'
            "</SL__arrayElement>",
            '<SL__arrayElement class="cosm" uid="4"><image><ImageResID>1</ImageResID>'
            "</image></SL__arrayElement>",
        )
        + "</root></SL__rootObject>"
    )
    skipped: list[str] = []
    layout = read_panel_layout(parsed_xml(path), {}, skipped)
    assert isinstance(layout, ParsedFPPane)
    assert layout.objects == []
    assert skipped == ["mystery", "cosm (no bounds)"]


def test_layout_does_not_change_the_flat_controls(tmp_path: Path) -> None:
    """The flat list is what the fPDCO walk always produced: every fPDCO, in
    document order, with its stored bounds -- tab-page and grouper controls
    included."""
    tab = _tab(
        50, ["A", "B"], [_fpdco(61, "(1, 1, 2, 2)"), _fpdco(62, "(3, 3, 4, 4)")], "A"
    )
    panel = _plain(
        tmp_path,
        _fpdco(5, "(10, 20, 30, 40)"),
        _tab_fpdco(50, tab),
        _grouper(9, "(0, 0, 9, 9)", _fpdco(7, "(0, 0, 5, 5)")),
    )
    assert _control_summary(panel) == [
        ("5", "stdBool", (10, 20, 30, 40)),
        ("50", "tabControl", (-37, -12, 384, 506)),
        ("61", "stdBool", (1, 1, 2, 2)),
        ("62", "stdBool", (3, 3, 4, 4)),
        ("7", "stdBool", (0, 0, 5, 5)),
    ]


# --- real VIs ------------------------------------------------------------

_FLEX = SAMPLES_ROOT / "lv-flex-channel-examples/DAQ AO SFP/Main.vi"
_CLIENT = (
    SAMPLES_ROOT / "measurement-plugin-labview/Source/Generator"
    "/Create Measurement Client.vi"
)
_LINE_CONFIG = (
    SAMPLES_ROOT / "DCAF-DAQModule/source/editor node/Line Configuration Dialog.vi"
)
_RUNNER = (
    SAMPLES_ROOT / "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner"
    "/Graphical Test Runner - Main UI - .vi"
)


def _real_panel(path: Path) -> tuple[ParsedFrontPanel, list[str]]:
    bd_xml, fp_xml, main_xml = resolve_extracted(path)
    vi = parse_vi(bd_xml=bd_xml, fp_xml=fp_xml, main_xml=main_xml)
    skipped: list[str] = []
    assert fp_xml is not None
    again = read_panel_layout(
        parsed_xml(fp_xml), {c.uid: c for c in vi.front_panel.controls}, skipped
    )
    assert again is not None
    return vi.front_panel, skipped


def _tree_objects(layout) -> list:
    if isinstance(layout, ParsedFPSplitter):
        return _tree_objects(layout.first) + _tree_objects(layout.second)
    return _object_list(layout.objects)


def _object_list(objects) -> list:
    found: list = []
    for obj in objects:
        found.append(obj)
        if isinstance(obj, ParsedFPGrouper):
            found.extend(_object_list(obj.objects))
        if isinstance(obj, ParsedFPControl):
            for page in obj.tab_pages:
                found.extend(_object_list(page.objects))
    return found


def _leaf_panes(layout) -> list[ParsedFPPane]:
    if isinstance(layout, ParsedFPSplitter):
        return _leaf_panes(layout.first) + _leaf_panes(layout.second)
    return [layout]


def _assert_every_control_once(panel: ParsedFrontPanel, skipped: list[str]) -> None:
    found = [o for o in _tree_objects(panel.layout) if isinstance(o, ParsedFPControl)]
    assert sorted(id(c) for c in found) == sorted(id(c) for c in panel.controls)
    assert skipped == []


@pytest.mark.needs_samples
def test_real_splitter_panel() -> None:
    panel, skipped = _real_panel(_FLEX)
    _assert_every_control_once(panel, skipped)
    layout = panel.layout
    assert isinstance(layout, ParsedFPSplitter)
    assert layout.ratio == pytest.approx(0.465, abs=1e-4)
    assert layout.side_by_side is True
    left, right = _leaf_panes(layout)
    assert [type(o) for o in left.objects].count(ParsedFPDecoration) == 3
    assert [type(o) for o in left.objects].count(ParsedFPControl) == 14
    assert [type(o) for o in right.objects] == [ParsedFPControl]
    assert len(panel.controls) == 15


@pytest.mark.needs_samples
def test_real_tab_with_two_pages() -> None:
    panel, skipped = _real_panel(_CLIENT)
    _assert_every_control_once(panel, skipped)
    (tab,) = [c for c in panel.controls if c.tab_pages]
    assert [p.name for p in tab.tab_pages] == ["Inputs", "Status"]
    assert tab.tab_selected == 0
    assert sum(len(p.objects) for p in tab.tab_pages) == 9
    assert (
        len(
            [
                o
                for p in tab.tab_pages
                for o in p.objects
                if isinstance(o, ParsedFPControl)
            ]
        )
        == 8
    )


@pytest.mark.needs_samples
def test_real_tab_with_seven_pages_and_a_grouper() -> None:
    panel, skipped = _real_panel(_LINE_CONFIG)
    _assert_every_control_once(panel, skipped)
    (tab,) = [c for c in panel.controls if c.tab_pages]
    assert len(tab.tab_pages) == 7
    assert tab.tab_pages[2].name == "Current"
    assert tab.tab_selected == 2
    assert any(isinstance(o, ParsedFPGrouper) for o in tab.tab_pages[2].objects)


@pytest.mark.needs_samples
def test_real_deeply_nested_splitter() -> None:
    panel, skipped = _real_panel(_RUNNER)
    _assert_every_control_once(panel, skipped)
    assert isinstance(panel.layout, ParsedFPSplitter)
    assert len(_leaf_panes(panel.layout)) == 12


def _depth(layout) -> int:
    if isinstance(layout, ParsedFPSplitter):
        return 1 + max(_depth(layout.first), _depth(layout.second))
    return 0


@pytest.mark.needs_samples
def test_real_nested_splitter_depth() -> None:
    panel, _ = _real_panel(_RUNNER)
    assert _depth(panel.layout) == 9
