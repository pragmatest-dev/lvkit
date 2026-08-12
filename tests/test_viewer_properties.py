"""Tests for task #19 — VI properties + structure surfaced in the render
viewer, SVG-embedded (a single-glyph toolbar button + collapsible popover; no
header chips — the popover is the sole properties surface) — plus its
diff-viewer counterpart (a vi-node-properties follow-up):

  - the SAME shared popover (grouped VALUES: Version/Execution/Window/
    Toolbar/Instance/Structure), sourced from the AFTER pane's dataset, with
    rows that differ from the BEFORE pane highlighted amber;
  - the SAME properties button, ringed amber when >=1 shown value changed;
  - VI-Properties/VIStructure changes as first-class entries in the diff's
    CHANGES list (Flat AND Tree), counted in the header's "modified" tally.

Mirrors ``tests/test_render_viewer.py``'s patterns: a pure-unit test with a
stub SVG (no sample VI needed) proving the button/panel markup + JS get wired
into ``build_render_viewer``'s output, plus an end-to-end test over the
JKI-VI-Tester corpus's ``VITester_Item_Init.vi`` (password-protected +
reentrant — the exact "notable properties" sample task #19 calls for) proving
the root ``<svg>`` actually carries the expected ``data-lv-properties``/
``data-lv-structure`` JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import ChangeMap, diff_uid
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import ExecutionProps, LockState, VIProperties, VIStructure
from lvkit.render import render_vi
from lvkit.render.diff_viewer import build_diff_viewer
from lvkit.render.properties_panel import PROPERTIES_GLYPH
from lvkit.render.render_viewer import build_render_viewer

# The content-rich VI the vi-node-properties diff sample uses (a real class
# method, NOT the gutted VITester built project) — same VI loaded twice with
# flipped property/structure facets, mirroring outputs/diff_properties_sample.
_RUN_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestCase/run.vi"
)


def _require_run_vi() -> Path:
    if not _RUN_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not available")
    return _RUN_VI


def _load_run_vi() -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(_RUN_VI), mode=LoadMode.NONE, layout=True)
    return graph, graph.resolve_vi_name(_RUN_VI.name)


def _flipped_run_vi_graphs() -> tuple[
    InMemoryVIGraph, str, InMemoryVIGraph, str,
]:
    """Two loads of ``run.vi``, facets reset to clean defaults then the AFTER
    side flipped to notable values — the exact scenario
    ``outputs/diff_properties_sample.html`` builds from."""
    graph_a, vi_a = _load_run_vi()
    graph_b, vi_b = _load_run_vi()
    graph_a._vi_properties[vi_a] = VIProperties()
    graph_a._vi_structure[vi_a] = VIStructure()
    graph_b._vi_properties[vi_b] = VIProperties(
        lock_state=LockState.PASSWORD_PROTECTED,
        execution=ExecutionProps(reentrant=True, is_subroutine=True),
    )
    graph_b._vi_structure[vi_b] = VIStructure(bad_node=True, is_typedef=True)
    return graph_a, vi_a, graph_b, vi_b


# Both copies in the JKI-VI-Tester sample corpus are password-protected +
# reentrant (same VI, project-linked twice) — pick whichever is present.
_VITESTER_ITEM_INIT_CANDIDATES = [
    Path(
        ".lvkit/cache/samples/JKI-VI-Tester/source/Built Project Integration"
        "/VITester_Item_Init.vi"
    ),
    Path(
        ".lvkit/cache/samples/JKI-VI-Tester/source/LabVIEW Project Plugin"
        "/VITester_Item_Init.vi"
    ),
]


def _find_vitester_item_init() -> Path | None:
    for p in _VITESTER_ITEM_INIT_CANDIDATES:
        if p.exists():
            return p
    return None


def _require_vitester_item_init() -> Path:
    vi = _find_vitester_item_init()
    if vi is None:
        pytest.skip("JKI-VI-Tester sample corpus not available")
    return vi


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=True)
    return graph, graph.resolve_vi_name(vi_path.name)


class TestSvgCarriesPropertiesData:
    """Part A: the root <svg> is the single carrier for VIProperties/
    VIStructure — both the viewer chrome AND a host that only sees the raw
    SVG (e.g. the VS Code extension) read from it."""

    def test_password_protected_reentrant_vi_embeds_expected_json(self):
        vi_path = _require_vitester_item_init()
        graph, vi = _load(vi_path)
        svg = render_vi(graph, vi)
        assert svg is not None

        assert "data-lv-properties=" in svg
        assert "data-lv-structure=" in svg

        # Pull the JSON back out of the attribute and parse it for real,
        # rather than substring-matching — proves it's valid, well-formed
        # JSON, not just a string that happens to contain the right text.
        props_json = _extract_attr(svg, "data-lv-properties")
        struct_json = _extract_attr(svg, "data-lv-structure")
        props = json.loads(props_json)
        struct = json.loads(struct_json)

        assert props["lock_state"] == "password_protected"
        assert props["execution"]["reentrant"] is True
        assert struct["is_broken"] is False
        assert struct["is_typedef"] is False

    def test_unloaded_vi_gets_all_default_properties_never_raises(self):
        """A VI the graph doesn't know about (or hasn't parsed the LVSR block
        for) degrades to the all-defaults VIContext — render must never raise
        just because properties/structure are unavailable."""
        vi_path = _require_vitester_item_init()
        graph, vi = _load(vi_path)
        svg = render_vi(graph, vi)
        assert svg is not None
        # Sanity: still valid JSON even for the common (mostly-default) case.
        json.loads(_extract_attr(svg, "data-lv-properties"))
        json.loads(_extract_attr(svg, "data-lv-structure"))


def _extract_attr(svg: str, attr: str) -> str:
    """Pull a single-quoted ``attr='...'`` value out of the root <svg> tag."""
    head = svg.split(">", 1)[0]
    marker = f"{attr}='"
    start = head.index(marker) + len(marker)
    end = head.index("'", start)
    return head[start:end]


class TestBuildRenderViewerPropertiesChrome:
    """Part B: the single-glyph button + collapsible popover are wired into
    build_render_viewer's output — pure unit test, no sample VI needed (stub
    SVG mirrors test_render_viewer.py's TestBuildRenderViewerPureUnit). No
    header chips: the popover is the SOLE properties surface (design change —
    header space is tight, and the popover is one click away)."""

    def _html(self) -> str:
        svg = (
            "<svg id='lv-stub' data-lv-properties='{\"lock_state\":"
            "\"password_protected\"}' data-lv-structure='{\"is_broken\":"
            "false}'>DIAGRAM-MARKER</svg>"
        )
        return build_render_viewer(svg, title="Stub VI")

    def test_toggle_button_and_panel_present(self):
        html = self._html()
        assert 'id="lvkitPropsBtn"' in html
        assert 'id="lvkitPropsPanel"' in html
        # Plain unicode glyph, shared toolbar-button styling — no drawn SVG
        # icon, no chip container (dropped: the popover is the sole
        # properties surface).
        assert 'id="lvkitPropsBtn" type="button" class="lvkit-theme-btn"' in html
        assert f">{PROPERTIES_GLYPH}</button>" in html
        assert "<svg" not in html.split("lvkitPropsBtn")[1].split("</button>")[0]
        assert 'id="lvkitChips"' not in html

    def test_button_lives_in_titlerow_not_controls(self):
        """The button sits on the title row (right-justified next to the VI
        name), NOT in the zoom/theme .controls group."""
        html = self._html()
        title_start = html.index('<div class="titlerow">')
        title_row = html[title_start:html.index("</div>", title_start)]
        assert 'id="lvkitPropsBtn"' in title_row
        assert "Stub VI" in title_row  # __TITLE__ landed in the SAME row
        controls_start = html.index('<div class="controls">')
        controls = html[controls_start:html.index("</div>", controls_start)]
        assert 'id="lvkitPropsBtn"' not in controls

    def test_script_reads_dataset_not_re_deriving_state(self):
        """The panel script must read straight off the embedded SVG's
        dataset (the single source of truth) — never recompute state some
        other way."""
        html = self._html()
        assert "dataset.lvProperties" in html
        assert "dataset.lvStructure" in html
        # The panel groups these fields straight from the dataset (no
        # separate hand-typed chip-condition list anymore).
        assert "lock_state" in html
        assert "is_broken" in html

    def test_panel_collapsed_by_default(self):
        html = self._html()
        assert '<div class="lvkit-props-panel" id="lvkitPropsPanel" hidden>' in html

    def test_no_before_side_so_no_change_highlight_class_ever_applied(self):
        """The single-VI panel shares _PANEL_BODY_JS with the diff panel, but
        never supplies a "before" dataset -- so `changed` is always false and
        `.lvkit-prop-changed` never gets applied. The button also never wears
        the diff-only ring class."""
        html = self._html()
        assert "beforeProps = null" in html
        assert "lvkit-props-changed" not in html

    def test_chrome_css_follows_system_theme_not_data_theme(self):
        """DESIGN LAW: the panel is chrome — plain
        @media(prefers-color-scheme) tokens only, NEVER a rule keyed off
        [data-theme] (that toggle is diagram-only)."""
        html = self._html()
        assert ".lvkit-props-panel{" in html
        # No properties-chrome selector reacts to data-theme.
        start = html.index(".lvkit-props-panel{")
        end = html.index("</style>", start)
        chrome_css = html[start:end]
        assert "data-theme" not in chrome_css

    def test_every_placeholder_substituted(self):
        html = self._html()
        for marker in ("__PROPERTIES_BTN__", "__PROPERTIES_PANEL__"):
            assert marker not in html


class TestBuildRenderViewerPropertiesEndToEnd:
    def test_real_password_protected_vi_html_carries_data_attrs(self):
        vi_path = _require_vitester_item_init()
        graph, vi = _load(vi_path)
        svg = render_vi(graph, vi, theme_mode="auto")
        assert svg is not None

        html = build_render_viewer(svg, title=vi)
        assert "password_protected" in html
        assert 'id="lvkitPropsBtn"' in html
        assert 'id="lvkitChips"' not in html


class TestBuildDiffViewerPropertiesChrome:
    """The diff viewer's properties button + popover: pure-unit tests with
    stub before/after SVGs (mirrors TestBuildRenderViewerPropertiesChrome
    above), proving the button, the shared value-popover, and the
    dual-dataset JS get wired into build_diff_viewer's output."""

    def _html(self, before_props: str = '{"lock_state":"unlocked"}',
               after_props: str = '{"lock_state":"password_protected"}') -> str:
        before_svg = (
            f"<svg id='lv-before' data-lv-properties='{before_props}' "
            "data-lv-structure='{}'>BEFORE-MARKER</svg>"
        )
        after_svg = (
            f"<svg id='lv-after' data-lv-properties='{after_props}' "
            "data-lv-structure='{}'>AFTER-MARKER</svg>"
        )
        cmap = ChangeMap(changes=[], common_node_uids=[])
        return build_diff_viewer(
            cmap, before_svg, after_svg,
            title="Stub diff", before_label="before", after_label="after",
        )

    def test_properties_button_present(self):
        html = self._html()
        assert 'id="lvkitDiffPropsBtn"' in html
        assert f">{PROPERTIES_GLYPH}</button>" in html
        assert 'id="lvkitDiffPropsPanel"' in html
        # No chips in the diff viewer either.
        assert "lvkit-chip" not in html

    def test_button_lives_in_titles_row_not_controls(self):
        """The button sits right-justified on the title row (the VI name(s)
        row), NOT the zoom/hl/theme .controls.right cluster."""
        html = self._html()
        start = html.index('<div class="titles">')
        end = html.index("</div>", start)
        titles_row = html[start:end]
        assert 'id="lvkitDiffPropsBtn"' in titles_row
        assert "Stub diff" in titles_row  # __TITLE__ landed in the SAME row
        # Exactly one occurrence, and it sits BEFORE both the mode-toggle
        # controls group and the zoom/hl/theme .controls.right cluster --
        # i.e. inside .titles, not relocated-but-duplicated.
        assert html.count('id="lvkitDiffPropsBtn"') == 1
        btn_i = html.index('id="lvkitDiffPropsBtn"')
        assert btn_i < html.index('<div class="controls grow">')
        assert btn_i < html.index('class="controls right"')

    def test_shared_popover_not_a_bespoke_changes_list(self):
        """The diff panel reuses the SAME grouped-VALUE rendering as the
        single-VI panel (Version/Execution/Window/Toolbar/Instance/Structure
        groups via the shared _PANEL_BODY_JS) -- not a separate changes-only
        list. Both panels must therefore share the SAME group titles/markup
        vocabulary in their emitted script."""
        single_html = build_render_viewer(
            "<svg id='lv-stub' data-lv-properties='{}' "
            "data-lv-structure='{}'>M</svg>", title="Stub VI",
        )
        diff_html = self._html()
        markers = ('"Version"', '"Execution"', '"Structure"', "function group(")
        for marker in markers:
            assert marker in single_html
            assert marker in diff_html

    def test_script_reads_both_panes_datasets(self):
        html = self._html()
        assert "beforePane" in html and "afterPane" in html
        # AFTER supplies the shown values ("props"/"struct", like the
        # single-VI panel); BEFORE supplies the comparison-only side
        # ("beforeProps"/"beforeStruct") used purely for highlighting.
        assert "afterSvg.dataset.lvProperties" in html
        assert "beforeSvg.dataset.lvProperties" in html
        assert "afterSvg.dataset.lvStructure" in html
        assert "beforeSvg.dataset.lvStructure" in html
        assert "beforeProps" in html and "beforeStruct" in html

    def test_ring_and_highlight_css_key_off_mod(self):
        """The ring/changed-row CSS keys off --mod (the SAME amber the
        "modified" legend swatch uses)."""
        html = self._html()
        assert "lvkit-props-changed" in html
        assert "lvkit-prop-changed" in html
        assert "var(--mod)" in html

    def test_every_placeholder_substituted(self):
        html = self._html()
        for marker in ("__DIFF_PROPERTIES_BTN__", "__DIFF_PROPERTIES_PANEL__"):
            assert marker not in html


class TestBuildDiffViewerPropertiesEndToEnd:
    """Real before/after graphs (same VI loaded twice, facets reset then
    flipped on the AFTER side) -- proves the two panes actually embed
    DIFFERING data-lv-properties/data-lv-structure JSON, so the client-side
    diff the pure-unit tests above exercise statically has real changed data
    to compute over, AND that build_diff_viewer folds the metadata changes
    into the CHANGES list + "modified" tally."""

    def test_before_after_panes_embed_differing_property_json(self):
        _require_run_vi()
        graph_a, vi_a, graph_b, vi_b = _flipped_run_vi_graphs()

        before_svg = render_vi(graph_a, vi_a, interactive=False, theme_mode="auto")
        after_svg = render_vi(graph_b, vi_b, interactive=False, theme_mode="auto")
        assert before_svg is not None and after_svg is not None
        assert "password_protected" not in before_svg
        assert "password_protected" in after_svg

        cmap = diff_uid(graph_a, graph_b, vi_a, vi_b)
        html = build_diff_viewer(
            cmap, before_svg, after_svg,
            title="run.vi", before_label="before", after_label="after",
        )
        assert 'id="lvkitDiffPropsBtn"' in html
        assert before_svg in html and after_svg in html

    def test_metadata_changes_appear_in_changes_list_and_modified_tally(self):
        """diff.py stays untouched -- build_diff_viewer derives the metadata
        changes itself, straight from the two SVGs' embedded data-lv-*
        JSON, and folds them into __CHANGES__/__MOD__ so the Flat list AND
        the header tally include them (not just the Tree's netlist_rows)."""
        _require_run_vi()
        graph_a, vi_a, graph_b, vi_b = _flipped_run_vi_graphs()

        before_svg = render_vi(graph_a, vi_a, interactive=False, theme_mode="auto")
        after_svg = render_vi(graph_b, vi_b, interactive=False, theme_mode="auto")
        assert before_svg is not None and after_svg is not None

        cmap = diff_uid(graph_a, graph_b, vi_a, vi_b)
        html = build_diff_viewer(
            cmap, before_svg, after_svg,
            title="run.vi", before_label="before", after_label="after",
        )

        # Five curated flags changed: lock, reentrant, is_subroutine (props),
        # is_broken, is_typedef (structure) -- see _flipped_run_vi_graphs.
        assert '"kind": "property"' in html
        assert '"kind": "structure"' in html
        assert '"label": "lock: unlocked -> password_protected"' in html
        assert '"label": "reentrant: false -> true"' in html
        assert '"label": "typedef: false -> true"' in html
        assert '"change": "modified"' in html
        assert "modified 5" in html  # __MOD__ substituted, includes metadata

    def test_metadata_changes_param_overrides_auto_derivation(self):
        """An explicit (even empty) metadata_changes list skips
        auto-derivation entirely -- the escape hatch callers/tests can use."""
        _require_run_vi()
        graph_a, vi_a, graph_b, vi_b = _flipped_run_vi_graphs()
        before_svg = render_vi(graph_a, vi_a, interactive=False, theme_mode="auto")
        after_svg = render_vi(graph_b, vi_b, interactive=False, theme_mode="auto")
        assert before_svg is not None and after_svg is not None
        cmap = diff_uid(graph_a, graph_b, vi_a, vi_b)

        html = build_diff_viewer(
            cmap, before_svg, after_svg,
            title="run.vi", before_label="before", after_label="after",
            metadata_changes=[],
        )
        assert '"kind": "property"' not in html
        assert "modified 0" in html


class TestMetadataChangesHelper:
    """Unit tests for diff_viewer.py's own SVG-embedded-JSON re-derivation
    (_metadata_changes/_parse_lv_data_attrs) -- pure string-level, no graph
    or sample VI needed."""

    def test_parses_curated_property_and_structure_changes(self):
        from lvkit.render.diff_viewer import _metadata_changes

        before_svg = (
            "<svg data-lv-properties='{\"lock_state\":\"unlocked\","
            "\"execution\":{\"reentrant\":false}}' "
            "data-lv-structure='{\"is_typedef\":false}'>M</svg>"
        )
        after_svg = (
            "<svg data-lv-properties='{\"lock_state\":\"locked\","
            "\"execution\":{\"reentrant\":true}}' "
            "data-lv-structure='{\"is_typedef\":true}'>M</svg>"
        )
        changes = _metadata_changes(before_svg, after_svg)
        by_kind = {(c["kind"], c["label"]) for c in changes}
        assert ("property", "lock: unlocked -> locked") in by_kind
        assert ("property", "reentrant: false -> true") in by_kind
        assert ("structure", "typedef: false -> true") in by_kind
        for c in changes:
            assert c["change"] == "modified"
            assert c["uid"] is None
            assert c["bounds"] is None

    def test_field_is_the_raw_dataclass_field_not_the_curated_label(self):
        """`field` is what the click-to-reveal JS matches against the
        popover's `data-key` (see properties_panel.py's row()) -- it MUST be
        the raw VIProperties/VIStructure field name, which differs from the
        curated display label for several flags (e.g. "subroutine" (label)
        vs "is_subroutine" (field); "broken" vs "is_broken"; "lock" vs
        "lock_state")."""
        from lvkit.render.diff_viewer import _metadata_changes

        before_svg = (
            "<svg data-lv-properties='{\"lock_state\":\"unlocked\","
            "\"execution\":{\"is_subroutine\":false}}' "
            "data-lv-structure='{\"is_broken\":false}'>M</svg>"
        )
        after_svg = (
            "<svg data-lv-properties='{\"lock_state\":\"locked\","
            "\"execution\":{\"is_subroutine\":true}}' "
            "data-lv-structure='{\"is_broken\":true}'>M</svg>"
        )
        changes = _metadata_changes(before_svg, after_svg)
        by_field = {c["field"]: c["label"] for c in changes}
        assert by_field["lock_state"].startswith("lock:")
        assert by_field["is_subroutine"].startswith("subroutine:")
        assert by_field["is_broken"].startswith("broken:")

    def test_no_metadata_when_identical(self):
        from lvkit.render.diff_viewer import _metadata_changes

        svg = (
            "<svg data-lv-properties='{\"lock_state\":\"unlocked\"}' "
            "data-lv-structure='{}'>M</svg>"
        )
        assert _metadata_changes(svg, svg) == []

    def test_missing_data_attrs_degrades_to_no_changes(self):
        from lvkit.render.diff_viewer import _metadata_changes

        assert _metadata_changes("<svg>M</svg>", "<svg>M</svg>") == []


class TestClickToRevealPropertyRow:
    """Click-to-reveal for property/structure CHANGES entries: the diagram
    has no element for them, so clicking one opens the properties popover
    and highlights the matching row instead (the property analog of
    "click a node change -> reveal the node"). These are markup/wiring
    assertions on the emitted JS (this repo's established pattern for
    testing viewer JS without a browser -- see e.g.
    TestBuildRenderViewerPropertiesChrome.test_script_reads_dataset_...);
    the actual click/DOM behaviour was verified interactively against a
    real generated page."""

    def _diff_html(self) -> str:
        cmap = ChangeMap(changes=[], common_node_uids=[])
        before_svg = (
            "<svg id='lv-before' data-lv-properties='{\"lock_state\":"
            "\"unlocked\"}' data-lv-structure='{}'>B</svg>"
        )
        after_svg = (
            "<svg id='lv-after' data-lv-properties='{\"lock_state\":"
            "\"password_protected\"}' data-lv-structure='{}'>A</svg>"
        )
        return build_diff_viewer(
            cmap, before_svg, after_svg,
            title="Stub diff", before_label="before", after_label="after",
        )

    def test_metadata_change_carries_field_for_dom_lookup(self):
        html = self._diff_html()
        assert '"field": "lock_state"' in html

    def test_popover_rows_carry_matching_data_key(self):
        """properties_panel.py's row() stamps the SAME raw field name onto
        each dt/dd as data-key -- the join key revealPropertyRow uses."""
        html = self._diff_html()
        assert "dt.dataset.key = key" in html
        assert "dd.dataset.key = key" in html

    def test_reveal_function_present_and_wired_into_jump(self):
        html = self._diff_html()
        assert "function revealPropertyRow(c)" in html
        # Opens the popover if closed, keyed by data-key == c.field.
        assert "panel.hidden=false" in html.replace(" ", "")
        assert "data-key=\"'+c.field+'\"" in html
        # jump() routes property/structure kinds to the popover instead of
        # the diagram spotlight/zoom flow.
        assert "c.kind==='property'||c.kind==='structure'" in html
        assert "revealPropertyRow(c)" in html

    def test_selected_state_css_stronger_than_passive_changed_tint(self):
        """`.lvkit-prop-selected` (click-selected) must be a DISTINCT, VISUALLY
        STRONGER rule than the passive `.lvkit-prop-changed` tint every
        changed row already wears -- both key off --mod, but selected uses a
        higher mix percentage + an outline."""
        html = self._diff_html()
        assert ".lvkit-prop-selected{" in html

        def _mix_pct(rule_selector: str) -> int:
            start = html.index(rule_selector)
            end = html.index("}", start)
            m = re.search(r"var\(--mod\)\s+(\d+)%", html[start:end])
            assert m, f"no color-mix percentage found for {rule_selector}"
            return int(m.group(1))

        changed_pct = _mix_pct(".lvkit-prop-changed{")
        selected_pct = _mix_pct(".lvkit-prop-selected{")
        assert selected_pct > changed_pct
        selected_start = html.index(".lvkit-prop-selected{")
        selected_end = html.index("}", selected_start)
        assert "outline" in html[selected_start:selected_end]

    def test_clear_sel_also_clears_selected_popover_row(self):
        """Re-clicking the selected row (toggle-off) and jumping to an
        unrelated change must both clear any stale .lvkit-prop-selected --
        clearSel() is the shared cleanup both paths already call."""
        html = self._diff_html()
        clear_sel_start = html.index("function clearSel(){")
        clear_sel_end = html.index("\nfunction fit(){", clear_sel_start)
        clear_sel_body = html[clear_sel_start:clear_sel_end]
        assert "lvkit-prop-selected" in clear_sel_body
