"""End-to-end CLI tests for the render/diff OUTPUT cache (0.5.7).

These run ``python -m lvkit.cli`` in a subprocess (inheriting the hermetic
``LVKIT_CACHE_DIR`` the conftest fixture sets), so they exercise the real
``cmd_render``/``cmd_diff`` wiring — emit paths, ``-o`` semantics, directory
warming, ``--no-cache`` — not just the ``output_cache`` module in isolation.

HIT DETECTION without timing: after a cold run, overwrite the cache slot's BODY
with a sentinel (leaving its ``.meta.json`` intact, so it still validates as
fresh). A subsequent run that returns the sentinel PROVES it served from cache
rather than rebuilding; ``--no-cache`` returning real output proves the bypass.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from lvkit import output_cache

_SAMPLE_A = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/VI Tester About.vi"
)
_SAMPLE_B = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
)

_POISON = "POISON-SENTINEL-not-real-html"


def _require(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"sample VI not available: {sample}")


def _project_vi(tmp_path: Path, sample: Path, name: str = "Sample.vi") -> Path:
    """Copy ``sample`` into a git-marked tmp project (so it path-addresses to
    ``render/projects/…`` — one stable slot per VI)."""
    (tmp_path / "repo" / ".git").mkdir(parents=True, exist_ok=True)
    dst = tmp_path / "repo" / "src" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(sample.read_bytes())
    return dst


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "lvkit.cli", *args],
        capture_output=True,
        text=True,
    )


def _render(*args: str) -> subprocess.CompletedProcess:
    res = _run("render", *args)
    assert res.returncode == 0, res.stderr
    return res


# ── render: cold builds, warm serves the cached body ────────────────────────


class TestRenderCacheCLI:
    def test_warm_run_serves_cached_body(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        vi = _project_vi(tmp_path, _SAMPLE_A)
        out1 = tmp_path / "o1.html"
        _render(str(vi), "--format", "html", "-o", str(out1))
        slot = output_cache.render_slot(vi, "html")
        assert slot.exists(), "cold render should populate the cache slot"

        # Poison the slot body (meta stays fresh) -> a hit must return it.
        slot.write_text(_POISON, encoding="utf-8")
        out2 = tmp_path / "o2.html"
        _render(str(vi), "--format", "html", "-o", str(out2))
        assert out2.read_text() == _POISON, "warm run did not read from cache"

    def test_cold_and_warm_output_byte_identical(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        vi = _project_vi(tmp_path, _SAMPLE_A)
        o1, o2 = tmp_path / "a.html", tmp_path / "b.html"
        _render(str(vi), "--format", "html", "-o", str(o1))  # cold
        _render(str(vi), "--format", "html", "-o", str(o2))  # warm (hit)
        assert o1.read_bytes() == o2.read_bytes()
        assert b"<" in o1.read_bytes()  # real HTML, not empty

    def test_no_cache_bypasses_and_refreshes(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        vi = _project_vi(tmp_path, _SAMPLE_A)
        _render(str(vi), "--format", "html", "-o", str(tmp_path / "x.html"))
        slot = output_cache.render_slot(vi, "html")
        slot.write_text(_POISON, encoding="utf-8")

        out = tmp_path / "nc.html"
        _render(str(vi), "--format", "html", "--no-cache", "-o", str(out))
        assert out.read_text() != _POISON, "--no-cache should rebuild, not read cache"
        assert b"<" in out.read_bytes()
        # and it refreshed the slot with the real rebuild
        assert slot.read_text() != _POISON

    def test_edited_vi_is_a_miss(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        vi = _project_vi(tmp_path, _SAMPLE_A)
        _render(str(vi), "--format", "html", "-o", str(tmp_path / "x.html"))
        slot = output_cache.render_slot(vi, "html")
        slot.write_text(_POISON, encoding="utf-8")
        # Edit the VI -> content hash changes -> the poisoned slot is stale.
        vi.write_bytes(vi.read_bytes() + b"\x00")
        out = tmp_path / "e.html"
        _render(str(vi), "--format", "html", "-o", str(out))
        assert out.read_text() != _POISON, "edited VI must not hit the old slot"

    def test_two_vis_do_not_cross_serve(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        _require(_SAMPLE_B)
        vi_a = _project_vi(tmp_path, _SAMPLE_A, "A.vi")
        vi_b = _project_vi(tmp_path, _SAMPLE_B, "B.vi")
        _render(str(vi_a), "--format", "html", "-o", str(tmp_path / "a.html"))
        # Poison A's slot; rendering B must NOT return A's (poisoned) body.
        output_cache.render_slot(vi_a, "html").write_text(_POISON, encoding="utf-8")
        out_b = tmp_path / "b.html"
        _render(str(vi_b), "--format", "html", "-o", str(out_b))
        assert out_b.read_text() != _POISON

    def test_no_output_flag_warms_cache_and_reports_slot(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        vi = _project_vi(tmp_path, _SAMPLE_A)
        res = _render(str(vi), "--format", "html")  # no -o
        assert "cached" in res.stdout
        slot = output_cache.render_slot(vi, "html")
        assert slot.exists() and str(slot) in res.stdout


# ── render a DIRECTORY: batch warm, then incremental ────────────────────────


class TestDirectoryWarm:
    def test_directory_warms_then_all_fresh(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        _require(_SAMPLE_B)
        d = tmp_path / "repo" / "vis"
        (tmp_path / "repo" / ".git").mkdir(parents=True, exist_ok=True)
        d.mkdir(parents=True)
        (d / "A.vi").write_bytes(_SAMPLE_A.read_bytes())
        (d / "B.vi").write_bytes(_SAMPLE_B.read_bytes())

        first = _render(str(d), "--format", "html")
        assert "2 rendered, 0 already fresh" in first.stdout
        second = _render(str(d), "--format", "html")
        assert "0 rendered, 2 already fresh" in second.stdout


# ── diff: cold builds, warm serves the cached body ──────────────────────────


class TestDiffCacheCLI:
    def test_warm_diff_serves_cached_body(self, tmp_path: Path) -> None:
        _require(_SAMPLE_A)
        _require(_SAMPLE_B)
        # before/after can be any two VIs; the diff renders both sides.
        before = tmp_path / "before.vi"
        before.write_bytes(_SAMPLE_A.read_bytes())
        after = _project_vi(tmp_path, _SAMPLE_B, "After.vi")
        out1 = tmp_path / "d1.html"
        res = _run("diff", str(before), str(after), "--format", "html", "-o", str(out1))
        assert res.returncode == 0, res.stderr
        slot = output_cache.diff_slot(before, after, "html")
        assert slot.exists()

        slot.write_text(_POISON, encoding="utf-8")
        out2 = tmp_path / "d2.html"
        res2 = _run(
            "diff", str(before), str(after), "--format", "html", "-o", str(out2)
        )
        assert res2.returncode == 0, res2.stderr
        assert out2.read_text() == _POISON, "warm diff did not read from cache"
