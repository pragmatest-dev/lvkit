"""A front-panel cluster's fields are placed by their stored positions:
``pane-local = stored + origin + FT + P`` (``layout._cluster_field_geoms``),
clipped to the pane. Hand-made clusters, one per case, plus PCO's clipped
"Sampling Rates" cluster."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.parser.layout import _cluster_field_geoms
from lvkit.parser.models import ParsedFrontPanel
from lvkit.parser.vi import _parse_ddo
from lvkit.render.front_panel import render_front_panel_svg
from tests.corpus_graphs import load_corpus_vi

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_PCO = (
    _SAMPLES
    / "LabVIEW-DAQ/Intrinsic Optical Signal/PCO Edge 5.5/PCO Examples"
    / "Turner_PCO_CamExample.vi"
)


def _field(uid: int, name: str, bounds: str, inner: str = "") -> str:
    return (
        f'<SL__arrayElement class="stdNum" uid="{uid}"><bounds>{bounds}</bounds>'
        "<partsList>"
        '<SL__arrayElement class="label" uid="9"><objFlags>0</objFlags>'
        "<partID>16</partID><bounds>(0, 0, 15, 40)</bounds>"
        f'<textRec class="textHair"><text>"{name}"</text></textRec>'
        "</SL__arrayElement></partsList>"
        f"{inner}</SL__arrayElement>"
    )


def _cluster(
    uid: int,
    bounds: str,
    pane: str,
    origin: str,
    fields: list[tuple[int, str, str]],
    cls: str = "stdClust",
    nested: dict[int, str] | None = None,
) -> str:
    """A cluster ddo; ``nested`` maps a field uid to its own cluster body."""
    zplane = "".join(
        nested[u] if nested and u in nested else _field(u, n, b)
        for u, n, b in fields
    )
    order = "".join(f'<SL__arrayElement uid="{u}"/>' for u, _, _ in fields)
    return (
        f'<{"ddo" if cls == "stdClust" else "SL__arrayElement"} class="{cls}" '
        f'uid="{uid}"><bounds>{bounds}</bounds>'
        '<partsList><SL__arrayElement class="label" uid="8"><objFlags>0</objFlags>'
        "<partID>16</partID><bounds>(0, 0, 15, 40)</bounds>"
        f'<textRec class="textHair"><text>"c{uid}"</text></textRec>'
        f"</SL__arrayElement></partsList><ddoList>{order}</ddoList>"
        f'<paneHierarchy class="pane" uid="{uid + 1}"><bounds>{pane}</bounds>'
        f"<origin>{origin}</origin><zPlaneList>{zplane}</zPlaneList>"
        "</paneHierarchy>"
        f'</{"ddo" if cls == "stdClust" else "SL__arrayElement"}>'
    )


def test_fields_land_at_stored_plus_origin_plus_frame() -> None:
    # cluster top-left (x 200, y 100), pane top-left (4, 4), origin (-4, -4):
    # a field stored at (-200, -100) sits at pane-local (0, 0).
    ddo = ET.fromstring(
        _cluster(
            1, "(100, 200, 160, 300)", "(4, 4, 56, 96)", "(-4, -4)",
            [(10, "a", "(-100, -200, -80, -140)"), (11, "b", "(-76, -200, -56, -140)")],
        )
    )
    geom = _cluster_field_geoms(ddo, (0.0, 0.0))
    assert geom is not None
    assert geom.clip == (4.0, 4.0, 96.0, 56.0)
    assert [f.value_rect for f in geom.fields] == [
        (4.0, 4.0, 64.0, 24.0),
        (4.0, 28.0, 64.0, 48.0),
    ]


def _pco_like() -> ET.Element:
    # a 41 px tall pane of four 45 px fields; only the third is in view
    return ET.fromstring(
        _cluster(
            1, "(100, 200, 160, 300)", "(4, 4, 45, 96)", "(-4, -4)",
            [
                (10, "A", "(-100, -200, -55, -140)"),
                (11, "B", "(-53, -200, -8, -140)"),
                (12, "C", "(-6, -200, 39, -140)"),
                (13, "D", "(41, -200, 86, -140)"),
            ],
        )
    )


def test_clipped_cluster_draws_only_the_field_inside_it() -> None:
    geom = _cluster_field_geoms(_pco_like(), (0.0, 0.0))
    assert geom is not None and geom.clip is not None
    shown = [
        f.name
        for f in geom.fields
        if f.value_rect[1] < geom.clip[3] and f.value_rect[3] > geom.clip[1]
    ]
    assert shown == ["A"]


def _svg_of(ddo: ET.Element) -> str:
    ctrl = _parse_ddo(ddo, "1", set(), frame=(0.0, 0.0))
    assert ctrl is not None
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 400, 600))
    )


def test_a_field_outside_the_pane_is_not_drawn() -> None:
    svg = _svg_of(_pco_like())
    names = re.findall(r">([A-D])</text>", svg)
    assert names == ["A"]


def test_typedef_cluster_sits_at_wrapper_plus_inner_top_left() -> None:
    # wrapper top-left (x 60, y 50) + inner (x 5, y 10) = (65, 60); pane (3, 3),
    # origin (-3, -3): a field stored at (-65, -60) is at pane-local (-3, -3)+...
    inner = _cluster(
        2, "(10, 5, 70, 105)", "(3, 3, 57, 97)", "(-3, -3)",
        [(10, "a", "(-60, -65, -40, -5)")], cls="stdClust",
    ).replace("<ddo ", "<SL__arrayElement ").replace("</ddo>", "</SL__arrayElement>")
    wrapper = ET.fromstring(
        '<ddo class="typeDef" uid="1"><bounds>(50, 60, 130, 170)</bounds>'
        f"<partsList>{inner}</partsList></ddo>"
    )
    ctrl = _parse_ddo(wrapper, "1", set(), frame=(0.0, 0.0))
    assert ctrl is not None and ctrl.cluster_geom is not None
    # local x = -65 - 3 + 65 + 3 = 0, local y = -60 - 3 + 60 + 3 = 0; pane (3, 3)
    assert ctrl.cluster_geom.fields[0].value_rect[:2] == (3.0, 3.0)
    assert ctrl.cluster_geom.clip is not None


def test_nested_cluster_uses_its_parents_frame() -> None:
    inner = _cluster(
        20, "(-100, -200, -60, -100)", "(3, 3, 40, 80)", "(-3, -3)",
        [(30, "x", "(-4, -4, 16, 56)")],
    ).replace("<ddo ", "<SL__arrayElement ").replace("</ddo>", "</SL__arrayElement>")
    outer = ET.fromstring(
        _cluster(
            1, "(100, 200, 200, 400)", "(4, 4, 96, 196)", "(-4, -4)",
            [(20, "inner", "(-100, -200, -60, -100)")],
            nested={20: inner},
        )
    )
    geom = _cluster_field_geoms(outer, (0.0, 0.0))
    assert geom is not None
    assert geom.fields[0].value_rect[:2] == (4.0, 4.0)
    nested = geom.fields[0].nested
    assert nested is not None and nested.clip is not None
    # outer FT + P = (204, 104); inner FT = (-200, -100) + (204, 104) = (4, 4);
    # local = -4 - 3 + 4 + 3 = 0, so the field sits at the inner pane's top-left
    assert nested.fields[0].value_rect[:2] == (3.0, 3.0)
    ctrl = _parse_ddo(outer, "1", set(), frame=(0.0, 0.0))
    assert ctrl is not None
    assert ctrl.children[0].cluster_geom == nested


def test_without_a_frame_the_fields_are_fitted_into_the_pane() -> None:
    ddo = ET.fromstring(
        _cluster(
            1, "(0, 0, 60, 100)", "(3, 3, 57, 97)", "(-248, -2)",
            [(10, "a", "(54, -232, 80, -211)"), (11, "b", "(80, -232, 108, -211)")],
        )
    )
    geom = _cluster_field_geoms(ddo)
    assert geom is not None and geom.clip is None
    assert geom.fields[0].value_rect[:2] == (3.0, 3.0)


@pytest.mark.needs_samples
def test_pco_sampling_rates_draws_one_field() -> None:
    graph, key = load_corpus_vi(_PCO)
    front_panel = graph.get_vi_front_panel(key)
    assert front_panel is not None
    rates = next(c for c in front_panel.controls if c.name == "Sampling Rates")
    assert rates.cluster_geom is not None and rates.cluster_geom.clip is not None
    clip = rates.cluster_geom.clip
    shown = [
        f.name
        for f in rates.cluster_geom.fields
        if f.value_rect[1] < clip[3] and f.value_rect[3] > clip[1]
    ]
    assert shown == ["CBV Cam Fs"]
    svg = render_front_panel_svg(front_panel)
    assert ">CBV Cam Fs</text>" in svg
    assert ">30</text>" in svg
