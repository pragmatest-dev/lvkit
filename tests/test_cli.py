"""Tests for the `lvkit diff` CLI subcommand — the --format {text,json,html}
output-projection surface (roadmap #24, increment 3 of the vi-diff-action
plan).

Mirrors ``tests/test_diff.py``'s conventions: a sample pair copied into a
temporary folder (skipped if the corpus is absent), and in-process graph loads that
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

from .conftest import ViDiffPair


class _Pair:
    """The VI pair the diff tests compare (see ``vi_diff_pair`` in conftest)."""

    base: Path = Path()
    head: Path = Path()
    root: Path = Path()


PAIR = _Pair()


@pytest.fixture(scope="module", autouse=True)
def _sample_pair(vi_diff_pair: ViDiffPair) -> None:
    PAIR.base, PAIR.head, PAIR.root = (
        vi_diff_pair.base,
        vi_diff_pair.head,
        vi_diff_pair.root,
    )


def _require_pair() -> None:
    if not PAIR.base.is_file() or not PAIR.head.is_file():
        pytest.skip("sample VI pair not available")


def _load(vi_path: Path, *, layout: bool) -> tuple[InMemoryVIGraph, str]:
    """Mirror cmd_diff's own loading: LoadMode.MINIMAL (its default), so CLI
    output matches this in-process reference."""
    graph = InMemoryVIGraph()
    key = graph.load_vi(
        str(vi_path),
        LoadMode.MINIMAL,
        layout=layout,
    )
    assert key is not None
    return graph, key


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
        [
            sys.executable,
            "-m",
            "lvkit.cli",
            "diff",
            str(PAIR.base),
            str(PAIR.head),
            "--no-auto-vilib",
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


# ── diff_vi_files / DiffBody (#114 follow-up) ───────────────────────────────


class TestDiffVIFilesNames:
    def test_returns_a_diffbody_with_both_qualified_names(self) -> None:
        from lvkit.vi_diff import DiffBody, diff_vi_files

        _require_pair()
        ga, na = _load(PAIR.base, layout=True)
        gb, nb = _load(PAIR.head, layout=True)
        expected_before = ga.vi_display_name(na)
        expected_after = gb.vi_display_name(nb)

        for fmt in ("text", "json", "html"):
            result = diff_vi_files(PAIR.base, PAIR.head, fmt=fmt)
            assert isinstance(result, DiffBody)
            assert result.before_name == expected_before
            assert result.after_name == expected_after
            assert isinstance(result.body, str) and result.body


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

        ga, na = _load(PAIR.base, layout=True)
        gb, nb = _load(PAIR.head, layout=True)
        expected = diff_to_dict(ga, gb, na, nb)

        assert got == expected

    def test_json_output_flag_writes_file(self, tmp_path: Path) -> None:
        _require_pair()
        out = tmp_path / "diff.json"
        result = _run_diff("--format", "json", "-o", str(out))
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""  # went to the file, not stdout

        written = json.loads(out.read_text(encoding="utf-8"))

        ga, na = _load(PAIR.base, layout=True)
        gb, nb = _load(PAIR.head, layout=True)
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

        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")

        ga, na = _load(PAIR.base, layout=True)
        gb, nb = _load(PAIR.head, layout=True)
        cmap = diff_uid(ga, gb, na, nb)
        # the change-map JSON is embedded
        for change in cmap.changes:
            assert f'"uid": "{change.uid}"' in html
            break

    def test_open_with_default_format_resolves_to_html(
        self,
        tmp_path: Path,
    ) -> None:
        _require_pair()
        out = tmp_path / "opened.html"
        # --open with no explicit --format must resolve to html, not error.
        result = _run_diff("--open", "-o", str(out))
        assert result.returncode == 0, result.stderr
        assert out.is_file()
        assert out.read_text(encoding="utf-8").startswith("<!doctype html>")

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

        ga, na = _load(PAIR.base, layout=False)
        gb, nb = _load(PAIR.head, layout=False)
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

        ga, na = _load(PAIR.base, layout=False)
        gb, nb = _load(PAIR.head, layout=False)
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


# ── --search-path is gone: dependencies resolve by their recorded path ─────


@pytest.mark.parametrize(
    "command",
    ["describe", "generate", "document", "visualize", "unresolved", "render"],
)
def test_search_path_is_a_usage_error_naming_the_library_roots(
    command: str, tmp_path: Path
) -> None:
    """Every command that took ``--search-path`` rejects it with exit 2 and a
    message that names the library-root flags replacing it."""
    vi = tmp_path / "a.vi"
    vi.write_bytes(b"")
    extra = [str(tmp_path / "out")] if command == "document" else []
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvkit.cli",
            command,
            str(vi),
            *extra,
            "--search-path",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2
    assert "--search-path no longer exists" in result.stderr
    assert "--userlib DIR, --vilib DIR or --instrlib DIR" in result.stderr


def test_diff_search_path_is_a_usage_error(tmp_path: Path) -> None:
    a, b = tmp_path / "a.vi", tmp_path / "b.vi"
    for vi in (a, b):
        vi.write_bytes(b"")
    result = subprocess.run(
        [sys.executable, "-m", "lvkit.cli", "diff", str(a), str(b)]
        + ["--search-path", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2
    assert "--search-path no longer exists" in result.stderr
