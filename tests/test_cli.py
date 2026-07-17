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
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.diff import diff_structured, diff_text, diff_uid
from lvkit.graph.loading import LoadMode

BASE_VI = Path("outputs/vi-diff/run_base.vi")
HEAD_VI = Path("outputs/vi-diff/run_head.vi")


def _require_pair() -> None:
    if not BASE_VI.exists() or not HEAD_VI.exists():
        pytest.skip(f"sample VI pair not available: {BASE_VI}, {HEAD_VI}")


def _load(vi_path: Path, *, layout: bool) -> tuple[InMemoryVIGraph, str]:
    """Mirror cmd_diff's own loading: LoadMode.MINIMAL (its default), no
    explicit search paths — same as a bare `lvkit diff a b` invocation."""
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), LoadMode.MINIMAL, search_paths=[], layout=layout)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _run_diff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "lvkit.cli", "diff", str(BASE_VI), str(HEAD_VI), *args],
        capture_output=True, text=True,
    )


# ── --format json ────────────────────────────────────────────────────────


class TestFormatJson:
    def test_json_matches_diff_uid_to_dict(self) -> None:
        _require_pair()
        result = _run_diff("--format", "json")
        assert result.returncode == 0, result.stderr

        got = json.loads(result.stdout)

        ga, na = _load(BASE_VI, layout=True)
        gb, nb = _load(HEAD_VI, layout=True)
        expected = diff_uid(ga, gb, na, nb).to_dict()

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
        expected = diff_uid(ga, gb, na, nb).to_dict()

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
    def test_default_format_matches_diff_text(self) -> None:
        _require_pair()
        result = _run_diff()  # no flags at all — today's default behavior
        assert result.returncode == 0, result.stderr

        ga, na = _load(BASE_VI, layout=False)
        gb, nb = _load(HEAD_VI, layout=False)
        expected = diff_text(
            ga, gb, na, nb, label_a=str(BASE_VI), label_b=str(HEAD_VI),
        )
        assert result.stdout.rstrip("\n") == expected.rstrip("\n")

    def test_explicit_format_text_matches_default(self) -> None:
        _require_pair()
        default_result = _run_diff()
        explicit_result = _run_diff("--format", "text")
        assert explicit_result.returncode == 0, explicit_result.stderr
        assert explicit_result.stdout == default_result.stdout

    def test_verbose_and_long_produce_equal_structured_reports(self) -> None:
        _require_pair()
        verbose_result = _run_diff("--format", "text", "-v")
        long_result = _run_diff("--long")
        assert verbose_result.returncode == 0, verbose_result.stderr
        assert long_result.returncode == 0, long_result.stderr

        ga, na = _load(BASE_VI, layout=False)
        gb, nb = _load(HEAD_VI, layout=False)
        report = diff_structured(ga, gb, na, nb)
        expected = "No changes detected.\n" if report.is_empty() else (
            report.format() + "\n"
        )

        assert verbose_result.stdout == long_result.stdout
        assert verbose_result.stdout == expected
