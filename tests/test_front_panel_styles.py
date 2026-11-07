"""Front-panel controls drawn in their own look (Modern, System, Silver) from the
picture ids and flags of their parts: the style decision, then each drawing's
colors and shapes, built from tiny hand-made part lists."""

from __future__ import annotations

import base64
import re

from lvkit.parser.models import (
    ParsedFPControl,
    ParsedFPListbox,
    ParsedFPPane,
    ParsedFPPart,
    ParsedFPPartState,
    ParsedFrontPanel,
)
from lvkit.render.backend import SvgBackend
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.display_text import path_text, string_text
from lvkit.render.front_panel.controls.style import (
    BooleanKind,
    ControlStyle,
    boolean_kind,
    has_dropdown,
    numeric_style,
    string_style,
)
from lvkit.render.style import DEFAULT_THEME

_SHOWN = 0x936
_HIDDEN = 0x93E


def _part(part_id, cls, bounds, image_ids=(), **kw) -> ParsedFPPart:
    return ParsedFPPart(part_id, cls, bounds, image_ids=tuple(image_ids), **kw)


def _control(ctype, parts, bounds=(0, 0, 60, 200), **kw) -> ParsedFPControl:
    return ParsedFPControl(
        uid="1", name="Value", control_type=ctype, bounds=bounds, parts=parts, **kw
    )


def _svg(ctrl: ParsedFPControl) -> str:
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 100, 300)),
        window_frame=False,
    )


def _modern_numeric(
    indicator: bool, arrows: int, text: str = "42", bg: str | None = None
) -> ParsedFPControl:
    return _control(
        "stdNum",
        [
            _part(16, "label", (0, 11, 17, 64)),
            _part(10, "numLabel", (21, 15, 38, 61), (-239,), bg_color=bg),
            _part(2, "bigMultiCosm", (16, 0, 28, 12), (-413,), flags=arrows),
            _part(3, "bigMultiCosm", (30, 0, 42, 12), (-414,), flags=arrows),
            _part(9, "cosm", (17, 11, 42, 65), (-412,)),
        ],
        default_value=text,
        shows_as_indicator=indicator,
    )


# ---- the style decision ------------------------------------------------------


def test_numeric_style_from_the_frame_id() -> None:
    for frame_id, style in (
        (-412, ControlStyle.MODERN),
        (-301, ControlStyle.SYSTEM),
        (-766, ControlStyle.SILVER),
        (-9, ControlStyle.OTHER),
    ):
        ctrl = _control("stdNum", [_part(9, "cosm", (0, 0, 5, 5), (frame_id,))])
        assert numeric_style(ctrl) == style
    assert numeric_style(_control("stdNum", [])) == ControlStyle.OTHER


def test_string_style_from_the_field_then_the_frame() -> None:
    system = _control("stdString", [_part(11, "label", (0, 0, 5, 5), (-302,))])
    modern = _control(
        "stdString",
        [
            _part(11, "label", (0, 0, 5, 5), (-239,)),
            _part(9, "cosm", (0, 0, 5, 5), (-412,)),
        ],
    )
    silver = _control("stdPath", [_part(9, "cosm", (0, 0, 5, 5), (-766,))])
    assert string_style(system) == ControlStyle.SYSTEM
    assert string_style(modern) == ControlStyle.MODERN
    assert string_style(silver) == ControlStyle.SILVER


def test_boolean_kind_from_the_body_ids() -> None:
    expected = {
        -407: BooleanKind.SLIDE_SWITCH,
        -404: BooleanKind.ROUND_LED,
        -535: BooleanKind.MODERN_BUTTON,
        -536: BooleanKind.MODERN_BUTTON,
        -272: BooleanKind.SYSTEM_BUTTON,
        -684: BooleanKind.SYSTEM_BUTTON,
        -296: BooleanKind.CHECK_BOX,
        -686: BooleanKind.CHECK_BOX,
        -292: BooleanKind.RADIO,
        -688: BooleanKind.RADIO,
        -441: BooleanKind.SQUARE_LED,
        -3087: BooleanKind.OTHER,
    }
    for image_id, kind in expected.items():
        body = _part(21, "multiCosm", (0, 0, 13, 13), (image_id,))
        ctrl = _control("stdBool", [body])
        assert boolean_kind(ctrl) == kind
    assert boolean_kind(_control("stdBool", [])) == BooleanKind.OTHER


def test_a_ring_with_a_dropdown_part_is_a_dropdown() -> None:
    drop = _control("stdRing", [_part(28, "cosm", (0, 0, 5, 5), (-466,))])
    spin = _control("stdRing", [_part(28, "cosm", (0, 0, 5, 5), (-704,))])
    assert has_dropdown(drop)
    assert not has_dropdown(spin)


# ---- Modern numeric / ring ---------------------------------------------------


def test_modern_control_numeric_has_no_outline_and_a_spinner() -> None:
    svg = _svg(_modern_numeric(indicator=False, arrows=_SHOWN, bg="#FAFAFA"))
    assert 'fill="#FAFAFA"' in svg
    assert "linearGradient" in svg
    assert svg.count('fill="#9A9A9A"') == 2  # the two arrows
    assert svg.count("clip-path") == 2  # one oval, cut by the gap between its halves
    assert svg.count('rx="6.0" ry="13.0"') == 2  # an ellipse, drawn once per half
    assert f'stroke="{DEFAULT_THEME.struct_border}"' not in svg
    assert ">42<" in svg


def test_modern_indicator_numeric_draws_no_spinner_and_the_indicator_fill() -> None:
    svg = _svg(_modern_numeric(indicator=True, arrows=_HIDDEN))
    assert 'fill="#D2D2D2"' in svg
    assert "#9A9A9A" not in svg
    assert "#D5D5D5" not in svg


def test_modern_numeric_uses_the_readouts_stored_fill() -> None:
    svg = _svg(_modern_numeric(indicator=True, arrows=_HIDDEN, bg="#FFFFFF"))
    assert not any(
        line.startswith("<rect") and 'fill="#D2D2D2"' in line
        for line in svg.splitlines()
    )
    assert 'fill="#FFFFFF"' in svg


def test_modern_ring_shows_its_selected_item_with_a_spinner() -> None:
    ctrl = _control(
        "stdRing",
        [
            _part(12, "multiLabel", (20, 15, 37, 117), (-239,)),
            _part(2, "bigMultiCosm", (15, 0, 27, 12), (-413,), flags=_SHOWN),
            _part(3, "bigMultiCosm", (29, 0, 41, 12), (-414,), flags=_SHOWN),
            _part(9, "cosm", (16, 11, 41, 121), (-412,)),
        ],
        default_value="1",
        enum_values=["Cat", "Dog"],
    )
    svg = _svg(ctrl)
    assert ">Dog<" in svg
    assert svg.count('fill="#9A9A9A"') == 2


def test_dropdown_ring_keeps_its_chevron_and_outline() -> None:
    ctrl = _control(
        "stdRing",
        [
            _part(12, "multiLabel", (20, 15, 37, 117), (-239,)),
            _part(28, "cosm", (16, 11, 41, 121), (-466,)),
            _part(9, "cosm", (16, 11, 41, 121), (-412,)),
        ],
        default_value="0",
        enum_values=["Cat"],
    )
    svg = _svg(ctrl)
    assert "#3C3C3C" not in svg
    assert "<polygon" in svg


def test_other_numeric_whose_arrows_are_hidden_draws_no_spinner() -> None:
    parts = [
        _part(10, "numLabel", (21, 15, 38, 61)),
        _part(2, "bigMultiCosm", (16, 0, 28, 12), flags=_HIDDEN),
        _part(3, "bigMultiCosm", (30, 0, 42, 12), flags=_HIDDEN),
        _part(9, "cosm", (17, 11, 42, 65), (-9,)),
    ]
    with_arrows = [
        parts[0],
        _part(2, "bigMultiCosm", (16, 0, 28, 12), flags=_SHOWN),
        _part(3, "bigMultiCosm", (30, 0, 42, 12), flags=_SHOWN),
        parts[3],
    ]
    hidden = _svg(_control("stdNum", parts, default_value="1"))
    shown = _svg(_control("stdNum", with_arrows, default_value="1"))
    assert hidden.count("<polygon") < shown.count("<polygon")


# ---- strings and paths -------------------------------------------------------


def _system_string(indicator: bool, value: str | None) -> ParsedFPControl:
    return _control(
        "stdString",
        [
            _part(16, "label", (0, 2, 17, 113)),
            _part(11, "label", (19, 0, 40, 374), (-302,)),
        ],
        bounds=(0, 0, 40, 374),
        default_value=value,
        shows_as_indicator=indicator,
    )


def test_system_string_is_a_bordered_field_filled_by_control_or_indicator() -> None:
    indicator = _svg(_system_string(True, None))
    control = _svg(_system_string(False, None))
    assert 'fill="#F0F0F0" stroke="#ABADB3"' in indicator
    assert 'fill="#FFFFFF" stroke="#ABADB3"' in control


def test_system_string_label_shows_above_its_field() -> None:
    svg = _svg(_system_string(False, None))
    assert ">Value<" in svg
    label = re.search(r'<text x="[\d.]+" y="([\d.]+)"[^>]*>Value<', svg)
    assert label is not None
    label_y = float(label.group(1))
    field = re.search(r'<rect x="[\d.]+" y="([\d.]+)"[^>]*stroke="#ABADB3"', svg)
    assert field is not None
    assert label_y < float(field.group(1))


def test_a_string_shows_without_quotes() -> None:
    svg = _svg(_system_string(False, "'Empty'"))
    assert ">Empty<" in svg
    assert "'Empty'" not in svg and "&#x27;" not in svg


def test_a_path_shows_its_text_not_a_path_call() -> None:
    ctrl = _control(
        "stdPath",
        [_part(11, "label", (19, 0, 40, 374), (-302,))],
        bounds=(0, 0, 40, 374),
        default_value='Path("/C/Users/me/Documents")',
    )
    svg = _svg(ctrl)
    assert "C:\\Users\\me\\Documents" in svg
    assert "Path(" not in svg


def test_modern_string_has_the_shadow_strip_and_no_outline() -> None:
    ctrl = _control(
        "stdString",
        [
            _part(11, "label", (23, 4, 40, 140), (-239,), bg_color="#D2D2D2"),
            _part(9, "cosm", (19, 0, 44, 144), (-412,)),
        ],
        bounds=(0, 0, 44, 144),
        default_value="'hi'",
        shows_as_indicator=True,
    )
    svg = _svg(ctrl)
    assert 'fill="#D2D2D2"' in svg
    assert "<polygon" in svg  # the shadow strips
    assert 'stroke="#ABADB3"' not in svg
    assert f'stroke="{DEFAULT_THEME.struct_border}"' not in svg


def test_silver_string_draws_the_stored_bevel_back_to_front() -> None:
    ctrl = _control(
        "stdString",
        [
            _part(11, "label", (33, 10, 61, 339), (-239,), bg_color="#D2D2D2"),
            _part(119, "cosm", (32, 9, 62, 340), (-11,), bg_color="#CCCCCC"),
            _part(119, "cosm", (31, 8, 62, 341), (-11,), bg_color="#7A7A7A"),
            _part(119, "cosm", (31, 8, 63, 341), (-11,), bg_color="#FFFFFF"),
            _part(
                9, "cosm", (24, 0, 72, 352), (-766,),
                fg_color="#646464", bg_color="#CCCCCC",
            ),
        ],
        bounds=(0, 0, 72, 352),
        default_value="'x'",
        shows_as_indicator=True,
    )
    svg = _svg(ctrl)
    order = [svg.index(f'fill="{c}"') for c in ("#FFFFFF", "#7A7A7A", "#D2D2D2")]
    assert order == sorted(order)
    assert 'stroke="#646464"' in svg


# ---- Booleans ----------------------------------------------------------------


def _boolean(image_id: int, on: bool, states=(), extra=(), texts=()) -> ParsedFPControl:
    body_cls = "bigMultiCosm" if states else "multiCosm"
    return _control(
        "stdBool",
        [
            *extra,
            _part(22, "multiLabel", (0, 0, 17, 30), texts=tuple(texts), flags=0),
            _part(21, body_cls, (0, 0, 21, 50), (image_id,), states=tuple(states)),
        ],
        bounds=(0, 0, 21, 50),
        default_value="True" if on else "False",
    )


_LED_STATES = (
    ParsedFPPartState((0, 0, 17, 17), "#1E4B00", "#1E4B00", -404),
    ParsedFPPartState((0, 0, 17, 17), "#64FF00", "#64FF00", -404),
)


def test_round_led_uses_the_stored_state_color_and_a_halo() -> None:
    halo = _part(87, "bigMultiCosm", (0, 0, 21, 21))
    svg = _svg(_boolean(-404, True, _LED_STATES, extra=[halo]))
    assert "#EFEFEF" in svg and "#DEDEDE" in svg
    assert "<circle" in svg
    assert "linearGradient" in svg
    off = _svg(_boolean(-404, False, _LED_STATES, extra=[halo]))
    assert "#64FF00" not in off


def test_slide_switch_thumb_takes_the_state_color() -> None:
    thumb_states = (
        ParsedFPPartState((0, 0, 11, 15), "#1E4B00", "#1E4B00", -406),
        ParsedFPPartState((0, 0, 11, 15), "#64FF00", "#64FF00", -406),
    )
    thumb = _part(86, "bigMultiCosm", (3, 30, 18, 41), states=thumb_states)
    svg = _svg(_boolean(-407, True, _LED_STATES, extra=[thumb]))
    assert 'stroke="#848484"' in svg
    assert re.search(r'<polygon[^>]*fill="#64FF00"', svg)


def test_modern_button_has_its_gradient_edges_and_caption_color() -> None:
    svg = _svg(
        _boolean(-535, False, texts=("STOP",), extra=[])
    )
    assert "#6A6A6A" in svg and "#929292" in svg and "#C7C7C7" in svg
    assert ">STOP<" in svg


def test_system_button_is_a_flat_face_with_a_border() -> None:
    svg = _svg(_boolean(-272, False, texts=("Go",)))
    assert 'fill="#E1E1E1" stroke="#ADADAD"' in svg
    assert ">Go<" in svg


def test_check_box_shows_a_check_mark_only_when_on() -> None:
    on = _svg(_boolean(-296, True))
    off = _svg(_boolean(-296, False))
    assert 'stroke="#333333"' in on and 'fill="#FFFFFF"' in on
    assert "<path" in on
    assert "<path" not in off


def test_radio_button_has_a_dot_only_when_on() -> None:
    on = _svg(_boolean(-292, True))
    off = _svg(_boolean(-292, False))
    assert on.count("<circle") == off.count("<circle") + 1


def test_square_led_has_a_vertical_gloss_in_the_state_color() -> None:
    svg = _svg(_boolean(-441, True, _LED_STATES))
    assert "linearGradient" in svg
    assert 'rx="6.0"' in svg


def test_unmeasured_boolean_keeps_the_generic_pill() -> None:
    svg = _svg(_boolean(-3087, True))
    assert f'stroke="{DEFAULT_THEME.struct_border}"' in svg
    assert "linearGradient" not in svg


# ---- panel, cluster and listbox ----------------------------------------------


def test_panel_background_is_the_first_panes_color() -> None:
    pane = ParsedFPPane(
        bounds=(0, 0, 100, 200), origin=(0, 0), objects=[], background="#006698"
    )
    svg = render_front_panel_svg(
        ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 100, 200), layout=pane)
    )
    assert 'fill="#006698"' in svg
    assert DEFAULT_THEME.fp_panel not in svg


def test_panel_without_a_recorded_color_uses_the_theme_canvas() -> None:
    pane = ParsedFPPane(bounds=(0, 0, 100, 200), origin=(0, 0), objects=[])
    svg = render_front_panel_svg(
        ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 100, 200), layout=pane)
    )
    assert f'fill="{DEFAULT_THEME.fp_panel}"' in svg


def test_a_hidden_cluster_field_is_not_drawn() -> None:
    def field(name: str, hidden: bool) -> ParsedFPControl:
        return ParsedFPControl(
            uid=name, name=name, control_type="stdNum", bounds=(0, 0, 20, 80),
            default_value="1", hidden=hidden,
        )

    cluster = ParsedFPControl(
        uid="c", name="Rates", control_type="stdClust", bounds=(0, 0, 90, 216),
        children=[field("Shown", False), field("Hidden", True)],
    )
    svg = _svg(cluster)
    assert ">Shown<" in svg
    assert ">Hidden<" not in svg


def test_system_listbox_header_columns_and_row_lines() -> None:
    listbox = ParsedFPListbox(
        frozen_rows=1,
        label_pos_row=1,
        column_headers=("      Name", "Client"),
        column_widths=(174, 100),
        border_color="#A8ABAE",
    )
    ctrl = _control(
        "listbox",
        [
            _part(100, "grid", (22, 2, 165, 369)),
            _part(
                9, "cosm", (20, 0, 167, 371), (-786,),
                fg_color="#A8ABAE", bg_color="#FFFFFF",
            ),
        ],
        bounds=(0, 0, 167, 371),
        listbox=listbox,
    )
    svg = _svg(ctrl)
    assert 'fill="#C3D3E6"' in svg
    assert ">Client<" in svg
    assert "\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0Name" in svg
    assert svg.count('fill="#A8ABAE"') >= 6
    assert "listbox" not in svg  # not the dashed placeholder


def test_other_listbox_stays_a_placeholder() -> None:
    frame = _part(9, "cosm", (0, 0, 40, 80), (-33,))
    ctrl = _control("listbox", [frame], bounds=(0, 0, 40, 80))
    assert "stroke-dasharray" in _svg(ctrl)


# ---- the pieces --------------------------------------------------------------


def test_gradient_rect_defines_one_gradient_per_distinct_fill() -> None:
    backend = SvgBackend()
    stops = ((0.0, "#111111"), (1.0, "#EEEEEE"))
    backend.gradient_rect(0, 0, 10, 10, stops=stops)
    backend.gradient_rect(20, 0, 30, 10, stops=stops)
    backend.gradient_rect(40, 0, 50, 10, stops=stops, end=(1.0, 0.0))
    svg = backend.render((0, 0, 60, 10), root_id="r")
    assert svg.count("<linearGradient") == 2
    assert 'fill="url(#r-g0)"' in svg and 'fill="url(#r-g1)"' in svg
    assert 'x2="1" y2="0"' in svg
    assert "__LVGRAD" not in svg


def test_display_text_of_strings_and_paths() -> None:
    assert string_text("'a\\nb'") == "a\nb"
    assert string_text(None) == ""
    assert path_text('Path("test.xml")') == "test.xml"
    assert path_text('Path("/C/a/b")') == "C:\\a\\b"
    assert path_text('Path("/")') == ""
    assert path_text("NotAPath") == "<Not A Path>"
    assert path_text('Path("/<vilib>/x.vi")') == "<vilib>\\x.vi"


def test_modern_field_shadow_ends_in_a_lighter_row() -> None:
    from lvkit.render.front_panel.controls import modern

    backend = SvgBackend()
    modern.draw_field(backend, (0, 0, 60, 30), (4, 10, 56, 26), "#FAFAFA")
    svg = backend.render((0, 0, 60, 30), root_id="r")
    # the fade covers the strip down to the last row, which is one flat row
    assert 'y="9.0" width="60.0" height="1.0" fill="#E4E4E4"' in svg


def test_square_led_has_no_white_gloss_at_the_top() -> None:
    from lvkit.render.front_panel.controls import boolean_kinds

    backend = SvgBackend()
    boolean_kinds.draw_square_led(backend, (0, 0, 40, 40), "#64FF00")
    svg = backend.render((0, 0, 40, 40), root_id="r")
    first = re.search(r'<stop offset="0[^"]*" stop-color="(#[0-9A-F]{6})"', svg)
    assert first is not None
    red, green, blue = (int(first.group(1)[i : i + 2], 16) for i in (1, 3, 5))
    assert red <= 0x74 and blue <= 0x30


def test_modern_shadow_strips_meet_in_a_mitred_corner() -> None:
    from lvkit.render.front_panel.controls import modern

    backend = SvgBackend()
    modern.draw_shadow_strips(backend, (0, 0, 60, 30), (4, 4, 56, 26))
    svg = backend.render((0, 0, 60, 30), root_id="r")
    # the first band of each strip starts at the outer corner and slants to the
    # inner corner; the left strip's first band is the same diagonal
    assert 'points="0.0,0.0 60.0,0.0 60.0,4.0 4.0,4.0"' in svg
    assert 'points="0.0,0.0 4.0,4.0 4.0,30.0 0.0,30.0"' in svg


def test_modern_field_right_and_bottom_edges_follow_the_fill() -> None:
    from lvkit.render.front_panel.controls import modern

    backend = SvgBackend()
    modern.draw_field(backend, (0, 0, 60, 30), (4, 10, 56, 26), "#D2D2D2")
    svg = backend.render((0, 0, 60, 30), root_id="r")
    # the measured indicator edges: #E3 at the right, #DA at the bottom
    assert 'fill="#E3E3E3"' in svg
    assert 'fill="#DADADA"' in svg


# ---- pictures embedded in the VI ---------------------------------------------


def _picture_boolean(on: bool) -> ParsedFPControl:
    """A Boolean whose body names pictures 3 (off) and 4 (on), and whose decal
    part 87 names picture 2, all stored in the VI."""
    ctrl = _boolean(
        3,
        on,
        extra=[_part(87, "cosm", (5, 5, 18, 18), (2,))],
    )
    ctrl.parts[-1] = _part(21, "multiCosm", (0, 0, 32, 32), (3, 4, 3, 4))
    ctrl.bounds = (0, 0, 32, 32)
    ctrl.pictures = {2: b"decal", 3: b"off", 4: b"on"}
    return ctrl


def test_a_boolean_draws_the_pictures_its_parts_embed() -> None:
    svg = _svg(_picture_boolean(on=False))
    hrefs = re.findall(r'<image href="data:image/png;base64,([^"]+)"', svg)
    # the body's picture of the current state first, then the decal
    assert [base64.b64decode(h) for h in hrefs] == [b"off", b"decal"]
    assert 'width="32.0" height="32.0"' in svg
    assert 'x="5.0" y="5.0" width="13.0" height="13.0"' in svg
    on = _svg(_picture_boolean(on=True))
    assert [
        base64.b64decode(h)
        for h in re.findall(r'<image href="data:image/png;base64,([^"]+)"', on)
    ] == [b"on", b"decal"]


def test_a_boolean_whose_picture_is_missing_keeps_its_own_drawing() -> None:
    ctrl = _picture_boolean(on=False)
    ctrl.pictures = {}
    assert "<image" not in _svg(ctrl)


# ---- Modern cluster frame -----------------------------------------------------


def _modern_cluster(frame_fg: str | None = "#B3B3B3") -> ParsedFPControl:
    return _control(
        "stdClust",
        [
            _part(28, "cosm", (4, 4, 40, 70), (0,), bg_color="#A6A6A6"),
            _part(9, "cosm", (0, 0, 44, 74), (-412,), fg_color=frame_fg),
        ],
        bounds=(0, 0, 44, 74),
    )


def test_modern_cluster_frame_is_the_viewport_filled_inside_a_bevel() -> None:
    svg = _svg(_modern_cluster())
    # the viewport (x 4-70, y 4-40) is filled with the frame's own color, no outline
    assert '<rect x="4.0" y="4.0" width="66.0" height="36.0" fill="#B3B3B3"/>' in svg
    assert 'stroke="#B3B3B3"' not in svg
    # four mitred bands per side: dark at the top and left, light at the bottom
    # and right, each growing toward the fill
    assert svg.count("fill=\"rgba(0,0,0,") == 8
    assert svg.count("fill=\"rgba(255,255,255,") == 8
    assert 'points="0.0,0.0 74.0,0.0 73.0,1.0 1.0,1.0" fill="rgba(0,0,0,0.14)"' in svg


def test_a_cluster_without_a_modern_frame_keeps_its_outlined_box() -> None:
    ctrl = _modern_cluster()
    ctrl.parts[-1] = _part(9, "cosm", (0, 0, 44, 74), (-301,), fg_color="#B3B3B3")
    svg = _svg(ctrl)
    assert 'stroke="#B3B3B3" stroke-width="1.5"' in svg
    assert "fill=\"rgba(" not in svg
