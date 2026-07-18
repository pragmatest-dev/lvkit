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
        assert "added 1" in html
        assert "removed 1" in html
        assert "modified 1" in html
        assert "unchanged 3" in html
        assert '"uid": "42"' in html
        assert '"label": "Added Node"' in html
        assert '"detail": "1 \\u2192 2"' in html or "1 → 2" in html


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
