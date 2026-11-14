"""Each front-panel text is drawn in its first run's font: size, weight, family
and literal colour. The font table is the ``FTAB`` of ``vi.xml``."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.parser.fp_fonts import read_font_table
from lvkit.parser.models import (
    ParsedFPFont,
    ParsedFPFontRun,
    ParsedFPLabel,
    ParsedFPPane,
    ParsedFrontPanel,
)
from lvkit.parser.vi import _parse_ddo
from lvkit.render.front_panel import render_front_panel_svg
from tests.corpus_graphs import load_corpus_vi

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_PCO = (
    _SAMPLES
    / "LabVIEW-DAQ/Intrinsic Optical Signal/PCO Edge 5.5/PCO Examples"
    / "Turner_PCO_CamExample.vi"
)

def _row(size: int, weight: int, name: str) -> str:
    return (
        f'<Font Prop2="{size}" Prop3="1024" Prop4="{weight}" Prop6="15" '
        f'Prop7="217" Prop8="212"><Name>{name}</Name></Font>'
    )


_FTAB = (
    '<RSRC><FTAB><Section Index="128" Prop1="0x10002" Prop3="3">'
    + _row(15, 0, "Segoe UI") * 3
    + _row(32768, 32768, "0")
    + _row(24, 1000, "0")
    + _row(30, 0, "Calibri")
    + "</Section></FTAB></RSRC>"
)


def _table() -> tuple[ParsedFPFont, ...]:
    return read_font_table(ET.fromstring(_FTAB))


def test_font_table_keeps_the_settled_props() -> None:
    plain, system, dialog, default, big_bold, calibri = _table()
    assert plain == ParsedFPFont("Segoe UI", 0, 15, False)
    assert system.predefined == 1 and not system.bold
    # the third predefined font is the Dialog font, drawn bold
    assert dialog == ParsedFPFont("Segoe UI", 2, 15, True)
    # a row that names a predefined font has no name of its own; the default
    # size is None
    assert default == ParsedFPFont(None, 0, None, False)
    assert big_bold == ParsedFPFont(None, 0, 24, True)
    assert calibri == ParsedFPFont("Calibri", None, 30, False)


def test_a_vi_with_no_font_table_has_none() -> None:
    assert read_font_table(ET.fromstring("<RSRC/>")) == ()


def _label_ddo(runs: str) -> ET.Element:
    return ET.fromstring(
        '<ddo class="stdNum" uid="1"><bounds>(10,20,50,120)</bounds>'
        "<objFlags>0</objFlags><partsList>"
        '<SL__arrayElement class="label" uid="2"><objFlags>0</objFlags>'
        "<partID>16</partID><bounds>(0,0,17,60)</bounds>"
        f'<textRec class="textHair"><text>"Name"</text><fr>{runs}</fr></textRec>'
        "</SL__arrayElement></partsList></ddo>"
    )


def _run(font_id: int | None = None, color: str | None = None) -> str:
    body = f"<fontid>{font_id}</fontid>" if font_id is not None else ""
    body += f"<fontcolor>{color}</fontcolor>" if color else ""
    return f'<SL__arrayElement class="fontRun">{body}</SL__arrayElement>'


def _label_svg(runs: str) -> str:
    ctrl = _parse_ddo(_label_ddo(runs), "1", set(), fonts=_table())
    assert ctrl is not None
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 400, 600))
    )


def _name_text(svg: str) -> str:
    return re.search(r"<text [^>]*>Name</text>", svg).group(0)  # type: ignore[union-attr]


def test_a_label_with_no_run_is_plain_at_the_default_size() -> None:
    text = _name_text(_label_svg(""))
    assert 'font-size="11.25"' in text
    assert "font-weight" not in text and "font-family" not in text


def test_a_label_in_the_dialog_font_is_bold() -> None:
    text = _name_text(_label_svg(_run(2)))
    assert 'font-weight="bold"' in text
    assert 'font-size="11.25"' in text
    assert "'Segoe UI', Tahoma, sans-serif" in text


def test_a_large_bold_white_label() -> None:
    text = _name_text(_label_svg(_run(4, "00FFFFFF")))
    assert 'font-size="18.0"' in text
    assert 'font-weight="bold"' in text
    assert 'fill="#FFFFFF"' in text


def test_a_named_font_leads_the_family() -> None:
    text = _name_text(_label_svg(_run(5)))
    assert 'font-size="22.5"' in text
    assert "font-family=\"'Calibri', 'Segoe UI', Tahoma, sans-serif\"" in text


def test_a_colour_only_run_keeps_the_default_font() -> None:
    text = _name_text(_label_svg(_run(None, "00FAFAFA")))
    assert 'font-size="11.25"' in text
    assert 'fill="#FAFAFA"' in text
    assert "font-weight" not in text


def test_a_system_colour_keeps_the_theme() -> None:
    text = _name_text(_label_svg(_run(None, "0100000C")))
    assert "fill=\"#FAFAFA\"" not in text


def test_a_label_with_two_runs_is_drawn_in_the_first_runs_font() -> None:
    text = _name_text(_label_svg(_run(4) + _run(1)))
    assert 'font-size="18.0"' in text and 'font-weight="bold"' in text


def test_a_free_label_is_drawn_in_its_runs_font() -> None:
    label = ParsedFPLabel(
        uid="3",
        bounds=(0, 0, 30, 200),
        text="Heading",
        fg_color=None,
        bg_color=None,
        hidden=False,
        font_runs=[ParsedFPFontRun(0, 4, "#FFFFFF")],
        font=_table()[4],
    )

    pane = ParsedFPPane(
        bounds=(0, 0, 100, 300), origin=(0, 0), objects=[label], doc_bounds=None
    )
    svg = render_front_panel_svg(
        ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 100, 300), layout=pane)
    )
    text = re.search(r"<text [^>]*>Heading</text>", svg).group(0)  # type: ignore[union-attr]
    assert 'font-size="18.0"' in text and 'font-weight="bold"' in text
    assert 'fill="#FFFFFF"' in text


@pytest.mark.needs_samples
def test_pco_title_is_large_bold_and_white_and_roi_is_plain() -> None:
    graph, key = load_corpus_vi(_PCO)
    front_panel = graph.get_vi_front_panel(key)
    assert front_panel is not None
    assert len(front_panel.fonts) == 11
    svg = render_front_panel_svg(front_panel)
    title = re.search(r"<text [^>]*>Turner PCO Example</text>", svg).group(0)  # type: ignore[union-attr]
    assert 'font-size="18.0"' in title
    assert 'font-weight="bold"' in title
    assert 'fill="#FFFFFF"' in title
    heading = re.search(r"<text [^>]*>Time \(min\)</text>", svg).group(0)  # type: ignore[union-attr]
    assert 'font-weight="bold"' in heading
    roi = re.search(r"<text [^>]*>roi</text>", svg).group(0)  # type: ignore[union-attr]
    assert "font-weight" not in roi and 'font-size="11.25"' in roi
