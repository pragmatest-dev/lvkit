"""Tests for the `lvkit diff` CLI subcommand — the --format {text,json,html}
output-projection surface (roadmap #24, increment 3 of the vi-diff-action
plan).

Mirrors ``tests/test_diff.py``'s conventions: the real sample pair under
``outputs/vi-diff/`` (skipped if absent), and in-process graph loads that
mirror exactly what ``cmd_diff`` does, so CLI output can be compared against
the underlying library functions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import diff_to_dict, diff_uid, format_diff
from lvkit.graph.loading import LoadMode

BASE_VI = Path("outputs/vi-diff/run_base.vi")
HEAD_VI = Path("outputs/vi-diff/run_head.vi")


def _require_pair() -> None:
    if not BASE_VI.exists() or not HEAD_VI.exists():
        pytest.skip(f"sample VI pair not available: {BASE_VI}, {HEAD_VI}")


def _load(vi_path: Path, *, layout: bool) -> tuple[InMemoryVIGraph, str]:
    """Mirror cmd_diff's own loading: LoadMode.MINIMAL (its default) and the
    SAME auto-detected project-root search paths a bare `lvkit diff a b`
    resolves (#36) — so CLI output matches this in-process reference."""
    from lvkit.cli import _auto_search_paths

    graph = InMemoryVIGraph()
    search_paths = _auto_search_paths([], vi_path, vi_path)
    graph.load_vi(
        str(vi_path), LoadMode.MINIMAL, search_paths=search_paths, layout=layout,
    )
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _run_diff(*args: str) -> subprocess.CompletedProcess:
    """Run `lvkit diff` in a subprocess.

    The suite must NEVER launch a real browser: the ``--open`` tests exercise
    argument RESOLUTION (that `--open` implies `--format html` rather than
    erroring), not the desktop handoff. Left unguarded, every `pytest` run pops a
    Chrome window on the developer's machine. Python's ``webbrowser`` honours
    ``$BROWSER``, so point it at a no-op command — the CLI's open path still runs.
    """
    env = {**os.environ, "BROWSER": "true"}
    return subprocess.run(
        [sys.executable, "-m", "lvkit.cli", "diff", str(BASE_VI), str(HEAD_VI), *args],
        capture_output=True, text=True, env=env,
    )


# ── --format json ────────────────────────────────────────────────────────


class TestFormatJson:
    def test_json_matches_diff_to_dict(self) -> None:
        # --format json emits diff_to_dict -- today just diff_uid(...).to_dict():
        # connector-pane/property changes are ordinary kind="connector_pane"/
        # "property" entries inside "changes" now, not separate top-level
        # sections. VI health is never diffed at all.
        _require_pair()
        result = _run_diff("--format", "json")
        assert result.returncode == 0, result.stderr

        got = json.loads(result.stdout)

        ga, na = _load(BASE_VI, layout=True)
        gb, nb = _load(HEAD_VI, layout=True)
        expected = diff_to_dict(ga, gb, na, nb)

        assert got == expected

    def test_json_output_flag_writes_file(self, tmp_path: Path) -> None:
        _require_pair()
        out = tmp_path / "diff.json"
        result = _run_diff("--format", "json", "-o", str(out))
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""  # went to the file, not stdout

        written = json.loads(out.read_text())

        ga, na = _load(BASE_VI, layout=True)
        gb, nb = _load(HEAD_VI, layout=True)
        expected = diff_to_dict(ga, gb, na, nb)

        assert written == expected


# ── --format html ────────────────────────────────────────────────────────


class TestFormatHtml:
    def test_html_writes_self_contained_file(self, tmp_path: Path) -> None:
        _require_pair()
        out = tmp_path / "cli_check.html"
        result = _run_diff("--format", "html", "-o", str(out))
        assert result.returncode == 0, result.stderr
        assert f"Wrote {out}" in result.stdout
        assert out.is_file()

        html = out.read_text()
        assert html.startswith("<!doctype html>")

        ga, na = _load(BASE_VI, layout=True)
        gb, nb = _load(HEAD_VI, layout=True)
        cmap = diff_uid(ga, gb, na, nb)
        # the change-map JSON is embedded
        for change in cmap.changes:
            assert f'"uid": "{change.uid}"' in html
            break

    def test_open_with_default_format_resolves_to_html(
        self, tmp_path: Path,
    ) -> None:
        _require_pair()
        out = tmp_path / "opened.html"
        # --open with no explicit --format must resolve to html, not error.
        result = _run_diff("--open", "-o", str(out))
        assert result.returncode == 0, result.stderr
        assert out.is_file()
        assert out.read_text().startswith("<!doctype html>")

    def test_open_with_format_json_errors(self) -> None:
        _require_pair()
        result = _run_diff("--open", "--format", "json")
        assert result.returncode == 1
        assert "--open requires --format html" in result.stderr

    def test_open_with_format_text_errors(self) -> None:
        _require_pair()
        result = _run_diff("--open", "--format", "text")
        assert result.returncode == 1
        assert "--open requires --format html" in result.stderr


# ── --format text (default) ──────────────────────────────────────────────


class TestFormatText:
    def test_default_format_matches_concise_format_diff(self) -> None:
        _require_pair()
        result = _run_diff()  # no flags at all — today's default behavior
        assert result.returncode == 0, result.stderr

        ga, na = _load(BASE_VI, layout=False)
        gb, nb = _load(HEAD_VI, layout=False)
        report = format_diff(ga, gb, na, nb)
        expected = "No changes detected." if not report else report

        assert result.stdout.rstrip("\n") == expected.rstrip("\n")

    def test_explicit_format_text_matches_default(self) -> None:
        _require_pair()
        default_result = _run_diff()
        explicit_result = _run_diff("--format", "text")
        assert explicit_result.returncode == 0, explicit_result.stderr
        assert explicit_result.stdout == default_result.stdout

    def test_verbose_and_long_produce_equal_detailed_reports(self) -> None:
        _require_pair()
        verbose_result = _run_diff("--format", "text", "-v")
        long_result = _run_diff("--long")
        assert verbose_result.returncode == 0, verbose_result.stderr
        assert long_result.returncode == 0, long_result.stderr

        ga, na = _load(BASE_VI, layout=False)
        gb, nb = _load(HEAD_VI, layout=False)
        report = format_diff(ga, gb, na, nb, verbose=True)
        expected = "No changes detected." if not report else report

        assert verbose_result.stdout == long_result.stdout
        assert verbose_result.stdout.rstrip("\n") == expected.rstrip("\n")

    def test_verbose_report_differs_from_concise_default(self) -> None:
        # The depth axis must actually add something (Signature/containment/
        # detail/tally) -- otherwise -v would be a silent no-op.
        _require_pair()
        default_result = _run_diff()
        verbose_result = _run_diff("-v")
        assert default_result.returncode == 0, default_result.stderr
        assert verbose_result.returncode == 0, verbose_result.stderr
        assert default_result.stdout != verbose_result.stdout


# ── #36: auto-detect project root for SubVI resolution ──────────────────────


class TestAutoDiffSearchPaths:
    """`lvkit diff a b` should resolve SubVIs from each VI's project root
    (nearest enclosing .lvkit/) without the user repeating --search-path."""

    def test_detects_shared_root_for_both_sides(self, tmp_path: Path) -> None:
        from lvkit.cli import _auto_search_paths

        (tmp_path / ".lvkit").mkdir()
        (tmp_path / "sub").mkdir()
        a = tmp_path / "sub" / "a.vi"
        b = tmp_path / "sub" / "b.vi"

        paths = _auto_search_paths([], a, b)

        # One shared root, added once (both VIs are in the same project).
        assert [p.resolve() for p in paths] == [tmp_path.resolve()]

    def test_detects_both_roots_across_two_projects(self, tmp_path: Path) -> None:
        from lvkit.cli import _auto_search_paths

        proj_a = tmp_path / "old"
        proj_b = tmp_path / "new"
        for proj in (proj_a, proj_b):
            (proj / ".lvkit").mkdir(parents=True)
        a = proj_a / "a.vi"
        b = proj_b / "b.vi"

        paths = {p.resolve() for p in _auto_search_paths([], a, b)}

        assert paths == {proj_a.resolve(), proj_b.resolve()}

    def test_explicit_paths_preserved_and_deduped(self, tmp_path: Path) -> None:
        from lvkit.cli import _auto_search_paths

        (tmp_path / ".lvkit").mkdir()
        a = tmp_path / "a.vi"
        b = tmp_path / "b.vi"
        # Explicitly passing the root must not duplicate the auto-detected one.
        explicit = [str(tmp_path)]

        paths = [p.resolve() for p in _auto_search_paths(explicit, a, b)]

        assert paths == [tmp_path.resolve()]

    def test_no_project_store_yields_no_paths(self, tmp_path: Path) -> None:
        from lvkit.cli import _auto_search_paths

        # A .git marker without .lvkit stops the walk (no store) -> nothing to
        # add; load_vi still searches each VI's own dir via source_dir.
        (tmp_path / ".git").mkdir()
        a = tmp_path / "a.vi"
        b = tmp_path / "b.vi"

        assert _auto_search_paths([], a, b) == []
