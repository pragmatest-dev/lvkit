"""Rendering a ``.ctl`` control's front panel: the dispatcher, the theme modes,
the control viewer, the output cache and the ``lvkit render`` CLI.

Hermetic tests stub the two renderers; the sample-backed ones (``needs_samples``)
render a real control end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit import cli
from lvkit.load_mode import LoadMode
from lvkit.output_cache import cached_render, lookup_render, render_options_tag
from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel
from lvkit.render import body as render_body_module
from lvkit.render.body import render_body
from lvkit.render.ctl import render_ctl_body
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.render_viewer import build_ctl_viewer, build_render_viewer

from .conftest import SAMPLES_ROOT

_CTL = (
    SAMPLES_ROOT
    / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/API_Test Settings.ctl"
)


def _panel() -> ParsedFrontPanel:
    ctrl = ParsedFPControl(
        uid="1", name="Gain", control_type="stdNum", bounds=(10, 10, 40, 110)
    )
    return ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 200, 200))


def test_dispatcher_sends_a_ctl_to_the_control_renderer(monkeypatch) -> None:
    calls: dict[str, dict] = {}
    def stub(kind: str):
        return lambda path, **kw: calls.setdefault(kind, kw)

    monkeypatch.setattr(render_body_module, "render_ctl_body", stub("ctl"))
    monkeypatch.setattr(render_body_module, "render_vi_body", stub("vi"))
    render_body(
        Path("A.CTL"), fmt="svg", vilib_root=Path("/v"), mode=LoadMode.FULL, ref="r"
    )
    assert set(calls) == {"ctl"}  # matched on suffix, case-insensitively
    # a control has no dependencies: the VI load options are not forwarded
    assert set(calls["ctl"]) == {"fmt", "search_paths", "theme_mode", "ref"}
    render_body(Path("A.vi"), fmt="svg", vilib_root=Path("/v"))
    assert calls["vi"]["vilib_root"] == Path("/v")


def test_theme_modes_follow_the_block_diagram_renderer() -> None:
    light = render_front_panel_svg(_panel())
    auto = render_front_panel_svg(_panel(), theme_mode="auto")
    dark = render_front_panel_svg(_panel(), theme_mode="dark")
    assert "var(--lv-" not in light and "prefers-color-scheme" not in light
    assert "var(--lv-" in auto and "prefers-color-scheme" in auto
    assert "var(--lv-" in dark and "prefers-color-scheme" not in dark


def test_control_viewer_has_no_vi_only_panels() -> None:
    from lvkit.render.connector_pane_panel import CONNECTOR_PANE_SCRIPT
    from lvkit.render.properties_panel import PROPERTIES_PANEL

    svg = "<svg id='x'></svg>"
    vi_page = build_render_viewer(svg, title="T")
    ctl_page = build_ctl_viewer(svg, title="T")
    assert PROPERTIES_PANEL in vi_page and CONNECTOR_PANE_SCRIPT in vi_page
    assert PROPERTIES_PANEL not in ctl_page
    assert CONNECTOR_PANE_SCRIPT not in ctl_page
    assert "<svg id='x'></svg>" in ctl_page and "<b>T</b>" in ctl_page
    assert "__PROPERTIES" not in ctl_page and "__CONNECTOR" not in ctl_page


def test_a_missing_control_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_ctl_body(tmp_path / "Nope.ctl", fmt="svg")


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CTL.exists(), reason="icon-editor sample absent")
def test_a_real_control_renders_as_svg_and_html():
    svg = render_ctl_body(_CTL, fmt="svg", theme_mode="light")
    assert svg is not None and svg.startswith("<svg") and ">Text color<" in svg
    page = render_ctl_body(_CTL, fmt="html", ref="abc1234")
    assert page is not None and "<b>API_Test Settings.ctl (abc1234)</b>" in page


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CTL.exists(), reason="icon-editor sample absent")
def test_the_output_cache_serves_a_control_render(tmp_path, monkeypatch):
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "cache"))
    opts = render_options_tag("svg", "light", None)
    first = cached_render(
        _CTL, fmt="svg", options=opts, version="t", theme_mode="light"
    )
    assert first is not None
    assert lookup_render(_CTL, "svg", opts, "t") == first  # now a fresh cache hit


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CTL.exists(), reason="icon-editor sample absent")
def test_render_directory_includes_controls_with_collision_free_names(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "proj"
    src.mkdir()
    (src / "Foo.ctl").write_bytes(_CTL.read_bytes())
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["lvkit", "render", str(src), "-o", str(out)])
    assert cli.main() == 0
    assert (out / "Foo.ctl.svg").is_file()  # keeps .ctl so it can't clash with Foo.vi
    assert "1 files" in capsys.readouterr().out
