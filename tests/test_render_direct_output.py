"""``render -o FILE --no-cache`` writes the file directly: no cache entry, no
staging folder (#131)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from lvkit import cache_paths, cli, extractor, output_cache, staging

_VI = (
    Path(__file__).parent
    / "corpus"
    / "issues"
    / "30"
    / "visible-frame-case-sequence.vi"
)


def _no_staging(*_a: object, **_k: object) -> None:
    raise AssertionError("a staging folder was claimed")


@pytest.fixture
def vi(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    dst = src / "Case.vi"
    shutil.copy(_VI, dst)
    extractor.extract_vi_xml(dst)  # extraction stages by design; do it up front
    staging.release_all()
    return dst


def _render(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", "render", *args])
    return cli.main()


@pytest.mark.parametrize("fmt", ["svg", "html"])
def test_no_cache_with_output_writes_one_file_and_no_cache_entry(
    vi: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fmt: str,
) -> None:
    monkeypatch.setattr(staging, "_claim_run", _no_staging)
    out = tmp_path / "out" / f"x.{fmt}"
    args = (str(vi), "--format", fmt, "--no-cache", "-o", str(out))
    assert _render(monkeypatch, *args) == 0
    assert out.read_text(encoding="utf-8").lstrip().startswith(("<svg", "<!"))
    assert not output_cache.render_slot(vi, fmt).exists()
    leftovers = staging.staging_root(cache_paths.global_cache_root()).glob("r-*")
    assert not list(leftovers)


def test_cached_render_still_fills_the_slot(
    vi: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "x.svg"
    assert _render(monkeypatch, str(vi), "--format", "svg", "-o", str(out)) == 0
    assert output_cache.render_slot(vi, "svg").exists()
