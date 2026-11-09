"""What the parser keeps of a front-panel part that decides its look: picture
ids (all three storage forms), the part's flags, a ``bigMultiCosm``'s states, a
``multiLabel``'s texts; a control's own indicator bit; a pane's background; a
listbox's table settings."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.parser.fp_listbox import read_listbox
from lvkit.parser.fp_structure import read_panel_layout
from lvkit.parser.models import ParsedFPPane
from lvkit.parser.vi import _parse_fp_parts_list
from tests.corpus_graphs import load_corpus_vi

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_MP = _SAMPLES / "measurement-plugin-labview" / "Source" / "Generator"


def _parts(xml: str):
    ddo = ET.fromstring(f"<ddo><partsList>{xml}</partsList></ddo>")
    return _parse_fp_parts_list(ddo)


def test_single_image_id_of_a_cosm_label_and_numlabel() -> None:
    parts = _parts(
        '<SL__arrayElement class="cosm"><partID>9</partID><bounds>(0,0,5,5)</bounds>'
        "<objFlags>2327</objFlags><image><ImageResID>-412</ImageResID></image>"
        "</SL__arrayElement>"
        '<SL__arrayElement class="label"><partID>11</partID><bounds>(0,0,5,5)</bounds>'
        "<image><ImageResID>-302</ImageResID></image></SL__arrayElement>"
    )
    assert [p.image_ids for p in parts] == [(-412,), (-302,)]
    assert parts[0].flags == 2327
    assert not parts[0].hidden


def test_multicosm_image_ids_one_per_state_in_table_order() -> None:
    (part,) = _parts(
        '<SL__arrayElement class="multiCosm"><partID>21</partID>'
        "<bounds>(0,0,13,13)</bounds><objFlags>6452</objFlags><table elements='3'>"
        '<SL__arrayElement class="Image"><ImageResID>-296</ImageResID>'
        "</SL__arrayElement>"
        '<SL__arrayElement class="Image"><ImageResID>-297</ImageResID>'
        "</SL__arrayElement>"
        '<SL__arrayElement class="Image"><ImageResID>-685</ImageResID>'
        "</SL__arrayElement></table></SL__arrayElement>"
    )
    assert part.image_ids == (-296, -297, -685)
    assert part.states == ()


def test_bigmulticosm_states_carry_bounds_colors_and_image() -> None:
    (part,) = _parts(
        '<SL__arrayElement class="bigMultiCosm"><partID>21</partID>'
        "<bounds>(24,2,41,19)</bounds><objFlags>2324</objFlags><table>"
        '<SL__arrayElement class="SubCosm"><Bounds>(0, 0, 17, 17)</Bounds>'
        "<FGColor>001E4B00</FGColor><BGColor>001E4B00</BGColor>"
        '<Image class="Image"><ImageResID>-404</ImageResID></Image>'
        "</SL__arrayElement>"
        '<SL__arrayElement class="SubCosm"><Bounds>(0, 0, 17, 17)</Bounds>'
        "<FGColor>0064FF00</FGColor><BGColor>0064FF00</BGColor>"
        '<Image class="Image"><ImageResID>-404</ImageResID></Image>'
        "</SL__arrayElement></table></SL__arrayElement>"
    )
    assert part.image_ids == (-404, -404)
    assert [s.bg_color for s in part.states] == ["#1E4B00", "#64FF00"]
    assert part.states[1].bounds == (0, 0, 17, 17)
    assert part.states[1].image_id == -404


def test_hidden_is_the_part_flag_bit_8() -> None:
    shown, hidden = _parts(
        '<SL__arrayElement class="bigMultiCosm"><partID>2</partID>'
        "<bounds>(0,0,12,12)</bounds><objFlags>2358</objFlags></SL__arrayElement>"
        '<SL__arrayElement class="bigMultiCosm"><partID>3</partID>'
        "<bounds>(0,0,12,12)</bounds><objFlags>2366</objFlags></SL__arrayElement>"
    )
    assert not shown.hidden
    assert hidden.hidden


def test_multilabel_texts_are_its_buf_items_and_its_font_color() -> None:
    (part,) = _parts(
        '<SL__arrayElement class="multiLabel"><partID>22</partID>'
        '<bounds>(0,0,12,12)</bounds><textRec><fr><SL__arrayElement class="fontRun">'
        "<fontcolor>00FF0000</fontcolor></SL__arrayElement></fr></textRec>"
        '<buf>(2)"Off""On"</buf></SL__arrayElement>'
    )
    assert part.texts == ("Off", "On")
    assert part.text_color == "#FF0000"
    (system,) = _parts(
        '<SL__arrayElement class="multiLabel"><partID>22</partID>'
        '<bounds>(0,0,12,12)</bounds><textRec><fr><SL__arrayElement class="fontRun">'
        "<fontcolor>0100003D</fontcolor></SL__arrayElement></fr></textRec>"
        '<buf>(1)"x"</buf></SL__arrayElement>'
    )
    assert system.text_color is None


def _pane(parts: str) -> ET.Element:
    return ET.fromstring(
        '<root><root><paneHierarchy class="pane"><bounds>(0,0,100,200)</bounds>'
        f"<partsList>{parts}</partsList><zPlaneList/></paneHierarchy></root></root>"
    )


def _background(color: str, image: str, part_id: str = "28") -> str:
    return (
        f'<SL__arrayElement class="cosm"><partID>{part_id}</partID>'
        f"<fgColor>{color}</fgColor><image><ImageResID>{image}</ImageResID></image>"
        "</SL__arrayElement>"
    )


def test_pane_background_is_the_part_28_cosm_of_image_minus_704() -> None:
    layout, _ = read_panel_layout(_pane(_background("00006698", "-704")), {})
    assert isinstance(layout, ParsedFPPane)
    assert layout.background == "#006698"


def test_pane_background_needs_a_literal_colour_part_28_and_image_minus_704() -> None:
    for parts in (
        _background("01000016", "-704"),
        _background("00006698", "-704", part_id="44"),
        _background("00006698", "-365"),
        "",
    ):
        layout, _ = read_panel_layout(_pane(parts), {})
        assert isinstance(layout, ParsedFPPane)
        assert layout.background is None


def test_listbox_settings() -> None:
    ddo = ET.fromstring(
        '<ddo class="listbox"><partsList><SL__arrayElement class="grid">'
        "<numFrozenRows>1</numFrozenRows><labelPosRow>1</labelPosRow>"
        "</SL__arrayElement></partsList><listboxFlags>4225</listboxFlags>"
        "<borderColor>00A8ABAE</borderColor><attributeList>"
        '<SL__arrayElement class="TableAttribute"><cellPosRow>-2</cellPosRow>'
        "<cellPosCol>-2</cellPosCol><width>51</width></SL__arrayElement>"
        '<SL__arrayElement class="TableAttribute"><cellPosRow>-2</cellPosRow>'
        "<cellPosCol>0</cellPosCol><width>174</width></SL__arrayElement>"
        "</attributeList><columnHeaders>"
        '<SL__arrayElement>"      Measurement Name"</SL__arrayElement>'
        '<SL__arrayElement>"Client Name"</SL__arrayElement></columnHeaders>'
        "<baseListboxItemStrings><multiDimArraySizes>"
        "<SL__arrayElement>0</SL__arrayElement><SL__arrayElement>2</SL__arrayElement>"
        "</multiDimArraySizes></baseListboxItemStrings></ddo>"
    )
    listbox = read_listbox(ddo)
    assert listbox.has_header
    assert listbox.column_headers == ("      Measurement Name", "Client Name")
    assert listbox.column_widths == (174, 51)
    assert listbox.flags == 4225
    assert listbox.border_color == "#A8ABAE"


def test_listbox_without_a_header_row() -> None:
    listbox = read_listbox(ET.fromstring('<ddo class="listbox"/>'))
    assert not listbox.has_header
    assert listbox.column_widths == ()


@pytest.mark.needs_samples
def test_system_push_button_ids_on_create_measurement_client() -> None:
    graph, key = load_corpus_vi(_MP / "Create Measurement Client.vi")
    front_panel = graph.get_vi_front_panel(key)
    assert front_panel is not None
    button = next(c for c in front_panel.controls if c.name == "Create Client")
    body = next(p for p in button.parts if p.part_id == 21)
    assert body.image_ids == (-272, -273, -274, -275, -684, -273)
    string = next(c for c in front_panel.controls if c.name == "Active Project Name")
    field = next(p for p in string.parts if p.part_id == 11)
    assert field.image_ids == (-302,)
    assert string.shows_as_indicator


@pytest.mark.needs_samples
def test_modern_indicator_hides_its_arrows_and_stores_the_indicator_fill() -> None:
    graph, key = load_corpus_vi(
        _MP / "Measurement Plug-In Template" / "UI" / "Measurement UI.vi"
    )
    front_panel = graph.get_vi_front_panel(key)
    assert front_panel is not None
    out = next(c for c in front_panel.controls if c.name == "double out")
    assert out.shows_as_indicator
    arrows = [p for p in out.parts if p.part_id in (2, 3)]
    assert len(arrows) == 2 and all(p.hidden for p in arrows)
    readout = next(p for p in out.parts if p.part_id == 10)
    assert readout.bg_color == "#D2D2D2"
    control = next(c for c in front_panel.controls if c.name == "double in")
    assert not control.shows_as_indicator
    assert not any(p.hidden for p in control.parts if p.part_id in (2, 3))


@pytest.mark.needs_samples
def test_pco_panel_background_is_006698() -> None:
    graph, key = load_corpus_vi(
        _SAMPLES
        / "LabVIEW-DAQ"
        / "Intrinsic Optical Signal"
        / "PCO Edge 5.5"
        / "PCO Examples"
        / "Turner_PCO_CamExample.vi"
    )
    front_panel = graph.get_vi_front_panel(key)
    assert front_panel is not None
    assert isinstance(front_panel.layout, ParsedFPPane)
    assert front_panel.layout.background == "#006698"
