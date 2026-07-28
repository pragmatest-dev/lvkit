"""Tests for lvkit.render.diff_viewer — the lifted diff-viewer builder
(roadmap #24, increment 2 of the vi-diff-action plan).

Mirrors ``tests/test_diff.py``'s patterns: the ``_load`` helper, the
``outputs/vi-diff/run_base.vi``/``run_head.vi`` pair (skip if absent), and
the ``_StubGraph``/``_const`` stub for a pure unit test that needs no sample
VIs at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import (
    ChangeMap,
    ElementChange,
    diff_uid,
    netlist_diff_rows,
    rows_to_json,
)
from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi
from lvkit.render.diff_viewer import build_diff_viewer

BASE_VI = Path("outputs/vi-diff/run_base.vi")
HEAD_VI = Path("outputs/vi-diff/run_head.vi")

# The JKI VI-Tester run.vi pair (mirrors tests/test_netlist.py's/test_diff.py's
# scratchpad convention: not part of the repo's sample corpus, so Phase 3
# tests that need REAL netlist-diff rows degrade gracefully when absent).
_SCRATCHPAD = Path(
    "/tmp/claude-1000/-home-ryanf-repos-lvkit/3a7f874f-386b-432d-9712-edf3bc6c995e"
    "/scratchpad"
)
_RUN_OLD = _SCRATCHPAD / "run_OLD.vi"
_RUN_NEW = _SCRATCHPAD / "run_NEW.vi"
_DEMO_SEARCH_PATH = Path(__file__).resolve().parent.parent / ".tmp" / "vi-tester-demo"


def _load_jki(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[_DEMO_SEARCH_PATH], layout=False)
    return graph, graph.resolve_vi_name(vi_path.name)


def _require_jki_vis() -> None:
    if not _RUN_OLD.exists() or not _RUN_NEW.exists():
        pytest.skip("JKI run_OLD.vi/run_NEW.vi pair not staged in scratchpad")


def _load(vi_path: Path, *, layout: bool = True) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=layout)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _require_pair() -> None:
    if not BASE_VI.exists() or not HEAD_VI.exists():
        pytest.skip(f"sample VI pair not available: {BASE_VI}, {HEAD_VI}")


class TestBuildDiffViewerEndToEnd:
    def test_html_embeds_svgs_changes_and_counts(self):
        _require_pair()
        ga, na = _load(BASE_VI)
        gb, nb = _load(HEAD_VI)

        before_svg = render_vi(ga, na, interactive=False)
        after_svg = render_vi(gb, nb, interactive=False)
        assert before_svg is not None and after_svg is not None

        cmap = diff_uid(ga, gb, na, nb)
        html = build_diff_viewer(
            cmap, before_svg, after_svg,
            title="run.vi", before_label="before", after_label="after",
        )

        assert html.startswith("<!doctype html>")
        assert before_svg in html
        assert after_svg in html

        # change-list JSON present: a "kind" key and (if any changes exist) a
        # known uid from the change-map show up verbatim in the embedded JSON.
        assert '"kind"' in html or cmap.changes == []
        for change in cmap.changes:
            assert f'"uid": "{change.uid}"' in html
            break

        added = sum(1 for c in cmap.changes if c.change == "added")
        removed = sum(1 for c in cmap.changes if c.change == "removed")
        modified = sum(1 for c in cmap.changes if c.change == "modified")
        assert f"added {added}" in html
        assert f"removed {removed}" in html
        assert f"modified {modified}" in html

        # inlined SVGs are script-less (increment 1) -- confirm the viewer
        # doesn't accidentally inline interactive copies.
        assert "<script" not in before_svg
        assert "<script" not in after_svg

    def test_labels_and_title_appear(self):
        _require_pair()
        ga, na = _load(BASE_VI)
        gb, nb = _load(HEAD_VI)
        before_svg = render_vi(ga, na, interactive=False)
        after_svg = render_vi(gb, nb, interactive=False)
        assert before_svg is not None and after_svg is not None
        cmap = diff_uid(ga, gb, na, nb)

        html = build_diff_viewer(
            cmap, before_svg, after_svg,
            title="run.vi diff", before_label="v1", after_label="v2",
        )
        assert "run.vi diff" in html
        assert "v1" in html and "v2" in html
        # placeholders fully substituted -- no leftover markers.
        for marker in (
            "__TITLE__", "__BEFORE_LABEL__", "__AFTER_LABEL__", "__BEFORE_SVG__",
            "__AFTER_SVG__", "__CHANGES__", "__NETLIST_TREE__", "__ADD__",
            "__DEL__", "__MOD__", "__COMMON__",
        ):
            assert marker not in html


class TestBuildDiffViewerPureUnit:
    """A tiny hand-built ChangeMap, no sample VIs needed -- proves the
    substitution logic (counts, JSON, labels) in isolation."""

    def test_counts_and_changes_substituted(self):
        cmap = ChangeMap(
            changes=[
                ElementChange(
                    uid="42", full_id="vi::42", kind="node", change="added",
                    label="Added Node", bounds=(1.0, 2.0, 3.0, 4.0),
                ),
                ElementChange(
                    uid="7", full_id="vi::7", kind="node", change="removed",
                    label="Removed Node", bounds=(5.0, 6.0, 7.0, 8.0),
                ),
                ElementChange(
                    uid="9", full_id="vi::9", kind="node", change="modified",
                    label="Modified Node", detail="1 → 2",
                    bounds=(0.0, 0.0, 1.0, 1.0),
                ),
            ],
            common_node_uids=["1", "2", "3"],
        )
        before_svg = "<svg id='b'>BEFORE-MARKER</svg>"
        after_svg = "<svg id='h'>AFTER-MARKER</svg>"

        html = build_diff_viewer(
            cmap, before_svg, after_svg,
            title="Stub VI", before_label="rev-a", after_label="rev-b",
        )

        assert html.startswith("<!doctype html>")
        assert "Stub VI" in html
        assert "rev-a" in html and "rev-b" in html
        assert "BEFORE-MARKER" in html and "AFTER-MARKER" in html
        # the shared diagram-theme control (button + its persistence script) is
        # now in the toolbar, next to the zoom group.
        assert 'id="lvkitThemeBtn"' in html
        assert "lvkitDiagramTheme" in html
        assert "added 1" in html
        assert "removed 1" in html
        assert "modified 1" in html
        assert "unchanged 3" in html
        assert '"uid": "42"' in html
        assert '"label": "Added Node"' in html
        assert '"detail": "1 \\u2192 2"' in html or "1 → 2" in html


class TestThemeAdaptiveChrome:
    """Part B: the viewer chrome is DEFAULT LIGHT with a
    ``@media (prefers-color-scheme: dark)`` override, so the standalone page is
    light in a light viewer and dark in a dark one — no hardcoded-dark
    ``:root`` that breaks light mode."""

    def _html(self) -> str:
        cmap = ChangeMap(
            changes=[
                ElementChange(
                    uid="42", full_id="vi::42", kind="node", change="added",
                    label="Added Node", bounds=(1.0, 2.0, 3.0, 4.0),
                ),
            ],
            common_node_uids=["1"],
        )
        return build_diff_viewer(
            cmap, "<svg id='b'>B</svg>", "<svg id='h'>A</svg>",
            title="Stub VI", before_label="a", after_label="b",
        )

    def test_dark_is_media_queried_not_the_root_default(self):
        html = self._html()
        assert "@media (prefers-color-scheme: dark)" in html
        # Light GitHub-ish defaults live at :root; the dark #0d1117 bg is now
        # only inside the media query, not the unconditional default.
        assert "--bg:#ffffff" in html
        assert "--bg:#0d1117" in html            # still present (dark override)
        # The SVG canvas is themed, not a hardcoded white.
        assert "background:var(--canvas)" in html
        assert "--canvas:#ffffff" in html and "--canvas:#1b1c1e" in html

    def test_toggle_themes_diagrams_only_never_chrome(self):
        """DESIGN LAW: the ◐/☀/☾ button is the DIAGRAM theme control — it
        re-themes only the embedded diagram SVGs (plus their --canvas
        backdrop). The chrome follows the system/editor theme via plain
        @media and must NEVER react to data-theme."""
        html = self._html()
        # The ONLY data-theme-driven chrome property is the diagram backdrop.
        assert ':root[data-theme="dark"]{--canvas:#1b1c1e}' in html
        assert ':root[data-theme="light"]{--canvas:#ffffff}' in html
        # (stub SVGs carry no CSS, so every data-theme selector here is the
        # viewer's own — exactly one dark rule, the backdrop.)
        assert html.count(':root[data-theme="dark"]') == 1
        # auto mode: backdrop follows the system scheme unless pinned…
        assert ":not([data-theme]){--canvas:#1b1c1e}" in html
        # …while the chrome palette is never guarded on / overridden by it.
        assert ":not([data-theme]){--bg" not in html
        assert '[data-theme="dark"]{--bg' not in html


class TestPaneRegistrationAndBlendReveal:
    """Overlay/Split coordinate registration + blend auto-reveal (browser
    behaviour verified with Playwright when built; these pin the mechanisms in
    the emitted HTML so they can't be silently dropped)."""

    def _html(self) -> str:
        cmap = ChangeMap(changes=[], common_node_uids=[])
        return build_diff_viewer(
            cmap, "<svg id='b'>B</svg>", "<svg id='h'>A</svg>",
            title="Stub VI", before_label="a", after_label="b",
        )

    def test_pane_svg_is_block_and_union_registered(self):
        html = self._html()
        # Block flow: the diagram svg must never hop up beside the in-flow
        # sticky pane label (that's what broke Overlay after a Split visit).
        assert ".pane > svg{display:block}" in html
        # Union-viewBox registration: shared scale + per-pane margin offsets
        # map identical LV coordinates to identical screen points.
        assert "function vbUnion()" in html
        assert "marginLeft" in html and "marginTop" in html
        # Deterministic initial layout — applyZoom runs at load, not first at
        # the first mode switch.
        assert "applyZoom();    // deterministic initial layout" in html

    def test_blend_auto_reveal_is_eased_and_respects_user(self):
        html = self._html()
        assert "function revealBlend(" in html
        assert "revealBlend(c);" in html            # wired into jump()
        # Eased (shared easeInOutCubic) + reduced-motion instant path.
        assert html.count("easeInOutCubic") >= 2
        assert "if(!ANIMATE){ op.value=target; applyBlend(); return; }" in html

    def test_frame_dot_skips_self_added_removed_and_is_colour_classed(self):
        """A structure that is ITSELF added/removed gets no per-value dot on its
        dropdown rows (its dashed outline already covers everything inside);
        remaining dots are coloured by the change grammar, not hardcoded yellow.
        The aggregate 'frame set changed' indicator is the numbered .hl-num badge
        above the selector — the same badge every other change gets."""
        html = self._html()
        assert "selfAddRem" in html
        assert "selfAddRem.has(String(c.container_uid))" in html
        assert ".frame-opt-dot.added{fill:var(--add)}" in html
        assert ".frame-opt-dot.removed{fill:var(--del)}" in html
        assert "'frame-opt-dot '+cls" in html
        # frame/value changes are numbered with the plain .hl-num badge
        assert "t.setAttribute('class','hl-num '+cls); t.textContent=i+1;" in html

    def test_picking_a_case_value_closes_the_dropdown(self):
        html = self._html()
        # the lv-option branch must hide its own menu after setFrame()
        assert "Picking a value CLOSES the dropdown" in html

    def test_highlight_toggle_is_icon_button_with_working_nav(self):
        """Highlight toggle: an icon BUTTON (◉/○, sized like the other
        buttons, grouped with the theme control in the legend), hiding via
        visibility (not display) so focus()/navigation geometry survives and
        the display-based .sel wire reveals can't leak through."""
        html = self._html()
        assert '<input id="hlToggle"' not in html      # no checkbox anymore
        assert '<button id="hlToggle"' in html
        assert "◉" in html
        assert ".hide-hl .hl,.hide-hl .hl-num{visibility:hidden}" in html
        assert "display:none" not in html.split(".hide-hl .hl,")[1].split("}")[0]

    def test_split_is_the_default_mode(self):
        html = self._html()
        assert '<div class="stage split mode-split" id="stage">' in html
        assert "mode='split'" in html
        # Split button listed first and active; fader starts hidden (overlay-only).
        assert html.index('data-mode="split"') < html.index('data-mode="overlay"')
        assert '<button data-mode="split" class="active"' in html
        assert '<span id="opWrap" hidden>' in html

    def test_value_change_reveals_each_pane_from_its_own_frame_side(self):
        """A frame VALUE change ("1"->"0") addresses its frame differently in
        each pane (before=...=1, after=...=0). revealFrame must drive the OTHER
        pane from frame_path_before, else the before pane is told to select a
        value it doesn't have and the renamed frame never appears in both panes
        at once — the same before/after linkage a modified node gets via
        bounds_before."""
        html = self._html()
        # revealFrame drives the other pane from the before-side addressing when
        # present (a value change), falling back to frame_path (same value both).
        assert "c.frame_path_before||c.frame_path" in html

    def test_zoom_buttons_disable_at_the_limits(self):
        """+ is disabled at ZMAX; − is disabled at the dynamic fit-both floor
        zMin() (whole VI in view — width AND height), re-evaluated in applyZoom
        on every zoom change."""
        html = self._html()
        assert "const ZMAX=8" in html
        assert "function zMin()" in html
        assert "document.getElementById('zoomOut').disabled = zoom<=zMin()+1e-6" in html
        assert "document.getElementById('zoomIn').disabled = zoom>=ZMAX" in html
        assert "button:disabled{opacity:.45;cursor:default}" in html


class TestNetlistTreeInViewer:
    """Phase 3: the Tree view renders diff.py's own structured netlist-diff
    rows (``NetlistDiffRow`` via ``netlist_diff_rows``/``rows_to_json``), not
    a client-side reconstruction of ``CHANGES`` -- so ``build_diff_viewer``'s
    ``netlist_rows`` embeds the IDENTICAL rows ``format_diff`` renders to
    text (see ``.tmp/netlist-spec.md`` Phase 3). No browser needed -- every
    assertion here is on the emitted HTML/JSON string."""

    def test_netlist_tree_embedded_with_known_row(self):
        _require_jki_vis()
        ga, na = _load_jki(_RUN_OLD)
        gb, nb = _load_jki(_RUN_NEW)
        cmap = diff_uid(ga, gb, na, nb)
        rows = netlist_diff_rows(ga, gb, na, nb)
        assert rows, "expected real changes on the JKI run_OLD/run_NEW pair"
        assert all(r.text.isascii() for r in rows)

        html = build_diff_viewer(
            cmap, "<svg id='b'>BEFORE-MARKER</svg>", "<svg id='h'>AFTER-MARKER</svg>",
            title="run.vi", before_label="before", after_label="after",
            netlist_rows=rows_to_json(rows),
        )

        assert "const NETLIST_TREE = [" in html
        assert "__NETLIST_TREE__" not in html
        # A known real netlist-diff row -- the SAME addSkipped instance line
        # Phase 2's TEXT-report test (test_diff.py::TestNetlistFormDiffOnJKIPair)
        # asserts on -- proves Tree gets the identical content, not a
        # different (client-rebuilt) rendering of it.
        assert any(
            r.kind == "node" and "addSkipped" in r.text for r in rows
        )
        assert "addSkipped" in html

    def test_omitted_netlist_rows_render_empty_tree(self):
        """No ``netlist_rows`` passed (an older/unaware caller) -> an empty,
        valid tree, never a leftover placeholder."""
        cmap = ChangeMap(changes=[], common_node_uids=[])
        html = build_diff_viewer(
            cmap, "<svg></svg>", "<svg></svg>",
            title="t", before_label="a", after_label="b",
        )
        assert "const NETLIST_TREE = [];" in html
        assert "__NETLIST_TREE__" not in html

    def test_deterministic_across_hash_seeds(self):
        _require_jki_vis()

        script = (
            "import hashlib\n"
            "from pathlib import Path\n"
            "from lvkit.graph.core import InMemoryVIGraph\n"
            "from lvkit.graph.diff import diff_uid, netlist_diff_rows, rows_to_json\n"
            "from lvkit.render.diff_viewer import build_diff_viewer\n"
            "def _load(p):\n"
            "    g = InMemoryVIGraph()\n"
            f"    g.load_vi(str(p), search_paths=[Path({str(_DEMO_SEARCH_PATH)!r})],"
            " layout=False)\n"
            "    return g, g.resolve_vi_name(p.name)\n"
            f"ga, na = _load(Path({str(_RUN_OLD)!r}))\n"
            f"gb, nb = _load(Path({str(_RUN_NEW)!r}))\n"
            "cmap = diff_uid(ga, gb, na, nb)\n"
            "rows = netlist_diff_rows(ga, gb, na, nb)\n"
            "html = build_diff_viewer(\n"
            "    cmap, '<svg></svg>', '<svg></svg>',\n"
            "    title='t', before_label='a', after_label='b',\n"
            "    netlist_rows=rows_to_json(rows),\n"
            ")\n"
            "assert all(r.text.isascii() for r in rows)\n"
            "print(hashlib.sha256(html.encode()).hexdigest())\n"
        )

        digests = []
        for seed in ("0", "1234567"):
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True, text=True, timeout=60,
            )
            assert result.returncode == 0, result.stderr
            digests.append(result.stdout.strip())
        assert digests[0] == digests[1]
