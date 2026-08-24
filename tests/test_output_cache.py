"""Unit tests for the render/diff OUTPUT cache (:mod:`lvkit.output_cache`).

The autouse ``_hermetic_cache`` fixture (conftest) points ``LVKIT_CACHE_DIR`` at
a per-test tmp dir, so every ``global_cache_root()`` here is that tmp dir.

These drive the cache directly (no graph/render) — the whole point is that a hit
returns the stored body without any build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit import cache_paths, output_cache

V = "0.5.7"
OPT = "html|auto"


def _vi(path: Path, content: bytes = b"vi-bytes") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _project_vi(
    tmp_path: Path, content: bytes = b"vi-bytes", name: str = "Foo.vi"
) -> Path:
    """A VI inside a git-marked project (so it path-addresses to projects/)."""
    (tmp_path / "repo" / ".git").mkdir(parents=True, exist_ok=True)
    return _vi(tmp_path / "repo" / "src" / name, content)


# ── render: miss -> store -> hit, and every invalidation axis ────────────────


class TestRenderCache:
    def test_miss_then_store_then_hit(self, tmp_path: Path) -> None:
        vi = _project_vi(tmp_path)
        assert output_cache.lookup_render(vi, "html", OPT, V) is None
        slot = output_cache.store_render(vi, "html", OPT, V, "<html>BODY</html>")
        assert output_cache.lookup_render(vi, "html", OPT, V) == "<html>BODY</html>"
        # Path-addressed under projects/<slug>/render (project-first), named by
        # the VI stem.
        assert slot.name == "Foo.html"
        parents = slot.parents
        assert (cache_paths.global_cache_root() / "projects") in parents
        assert "render" in {p.name for p in parents}

    def test_content_edit_invalidates(self, tmp_path: Path) -> None:
        vi = _project_vi(tmp_path, b"original")
        output_cache.store_render(vi, "html", OPT, V, "OUT")
        assert output_cache.lookup_render(vi, "html", OPT, V) == "OUT"
        vi.write_bytes(b"changed bytes now")  # edit the VI
        assert output_cache.lookup_render(vi, "html", OPT, V) is None

    def test_version_bump_invalidates(self, tmp_path: Path) -> None:
        vi = _project_vi(tmp_path)
        output_cache.store_render(vi, "html", OPT, V, "OUT")
        assert output_cache.lookup_render(vi, "html", OPT, "0.5.8") is None

    def test_options_change_invalidates(self, tmp_path: Path) -> None:
        vi = _project_vi(tmp_path)
        output_cache.store_render(vi, "html", OPT, V, "OUT")
        assert output_cache.lookup_render(vi, "html", "svg|dark", V) is None

    def test_source_fingerprint_change_invalidates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An lvkit CODE change (renderer/graph/parser/data) must bust the render
        cache with no version bump — via the SHARED cache_paths.source_fingerprint
        (the same hash that invalidates the SQLite index). Stored under one
        fingerprint, a different fingerprint (= edited source) is a miss."""
        vi = _project_vi(tmp_path)
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "fp-A")
        output_cache.store_render(vi, "html", OPT, V, "OUT")
        assert output_cache.lookup_render(vi, "html", OPT, V) == "OUT"  # same code
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "fp-B")
        assert output_cache.lookup_render(vi, "html", OPT, V) is None  # edited code

    def test_text_encoding_change_invalidates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vi = _project_vi(tmp_path)
        monkeypatch.setattr(output_cache, "labview_text_encoding", lambda: "gbk")
        output_cache.store_render(vi, "html", OPT, V, "OUT")
        monkeypatch.setattr(
            output_cache,
            "labview_text_encoding",
            lambda: "cp1252",
        )

        assert output_cache.lookup_render(vi, "html", OPT, V) is None

    def test_overwrite_in_place_one_slot(self, tmp_path: Path) -> None:
        """A path-addressed VI has exactly ONE render slot; a re-store after an
        edit overwrites it (no accumulation)."""
        vi = _project_vi(tmp_path, b"v1")
        s1 = output_cache.store_render(vi, "html", OPT, V, "one")
        vi.write_bytes(b"v2")
        s2 = output_cache.store_render(vi, "html", OPT, V, "two")
        assert s1 == s2
        assert output_cache.lookup_render(vi, "html", OPT, V) == "two"


class TestAdhocContentAddressed:
    def test_same_content_different_paths_hit(self, tmp_path: Path) -> None:
        """An adhoc input (outside any project) is content-addressed: the SAME
        bytes at a DIFFERENT throwaway path reuse the slot — the git-blob /
        temp-file case."""
        a = _vi(tmp_path / "tmpA" / "blob.vi", b"identical blob")
        output_cache.store_render(a, "html", OPT, V, "RENDERED")
        # A different temp dir, same bytes (a fresh mkdtemp of the same commit).
        b = _vi(tmp_path / "tmpB" / "blob.vi", b"identical blob")
        assert output_cache.lookup_render(b, "html", OPT, V) == "RENDERED"
        # It lives in the flat adhoc/render pool, named by content hash.
        slot, _, is_adhoc = output_cache._render_paths(b, "html")
        assert is_adhoc
        assert (
            slot.parent
            == cache_paths.global_cache_root()
            / "adhoc"
            / "render"
            / cache_paths.kind_fingerprint("render")
        )


# ── diff: keyed by after-path + before-content ──────────────────────────────


class TestDiffCache:
    def test_store_then_hit(self, tmp_path: Path) -> None:
        before = _vi(tmp_path / "t" / "old.vi", b"BEFORE")
        after = _project_vi(tmp_path, b"AFTER")
        assert output_cache.lookup_diff(before, after, "html", OPT, V) is None
        slot = output_cache.store_diff(before, after, "html", OPT, V, "<diff/>")
        assert output_cache.lookup_diff(before, after, "html", OPT, V) == "<diff/>"
        # Named by the after-VI stem + the before-content hash prefix.
        assert slot.name.startswith("Foo.")
        assert slot.suffix == ".html"

    def test_before_version_change_is_new_slot(self, tmp_path: Path) -> None:
        after = _project_vi(tmp_path, b"AFTER")
        before1 = _vi(tmp_path / "t" / "b1.vi", b"BEFORE-1")
        before2 = _vi(tmp_path / "t" / "b2.vi", b"BEFORE-2")
        output_cache.store_diff(before1, after, "html", OPT, V, "D1")
        # A different before (history moved) is a different key -> miss, not D1.
        assert output_cache.lookup_diff(before2, after, "html", OPT, V) is None
        output_cache.store_diff(before2, after, "html", OPT, V, "D2")
        assert output_cache.lookup_diff(before1, after, "html", OPT, V) == "D1"
        assert output_cache.lookup_diff(before2, after, "html", OPT, V) == "D2"

    def test_after_edit_invalidates(self, tmp_path: Path) -> None:
        before = _vi(tmp_path / "t" / "old.vi", b"BEFORE")
        after = _project_vi(tmp_path, b"AFTER")
        output_cache.store_diff(before, after, "html", OPT, V, "D")
        after.write_bytes(b"AFTER-EDITED")
        assert output_cache.lookup_diff(before, after, "html", OPT, V) is None


# ── compatibility: coexist, never clobber (the anti-thrash property) ─────────


class TestCompatibilityCoexistence:
    """The headline properties of the ``<fp>``-in-path design: two lvkit builds
    never clobber each other, and switching BACK to an old build is a hit, not a
    rebuild (the thrash the fix removes)."""

    def test_two_builds_coexist_no_clobber(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vi = _project_vi(tmp_path)
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "buildAAA")
        a_slot = output_cache.store_render(vi, "html", OPT, V, "A")
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "buildBBB")
        b_slot = output_cache.store_render(vi, "html", OPT, V, "B")
        assert a_slot != b_slot
        assert a_slot.exists() and b_slot.exists()  # neither overwrote the other

    def test_switch_back_to_old_build_hits_not_rebuilds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vi = _project_vi(tmp_path)
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "buildAAA")
        output_cache.store_render(vi, "html", OPT, V, "A")
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "buildBBB")
        assert output_cache.lookup_render(vi, "html", OPT, V) is None  # B: cold
        output_cache.store_render(vi, "html", OPT, V, "B")
        # Back to build A -> still cached, NOT rebuilt (its slot was never touched).
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "buildAAA")
        assert output_cache.lookup_render(vi, "html", OPT, V) == "A"


# ── TTL sweep: whole-<fp>-dir retirement, extract stickier ───────────────────


def _age_dir(d: Path, days: float) -> None:
    import os
    import time

    old = time.time() - days * 86400
    for p in d.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))


class TestTTLSweep:
    def test_idle_build_dir_retired_active_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A whole ``<fp>`` dir idle past the TTL is retired; a fresh sibling
        ``<fp>`` dir (a different build) is untouched."""
        monkeypatch.setenv("LVKIT_CACHE_TTL_DAYS", "7")
        monkeypatch.setenv("LVKIT_SWEEP_INTERVAL_HOURS", "0")  # never skip the walk
        vi = _project_vi(tmp_path, name="R.vi")
        slug = cache_paths._slug((tmp_path / "repo").resolve())
        root = cache_paths.global_cache_root()

        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "oldbuild")
        output_cache.store_render(vi, "html", OPT, V, "OLD")
        old_dir = root / "projects" / slug / "render" / "oldbuild"
        assert old_dir.is_dir()
        _age_dir(old_dir, 30)

        # A store under a NEW build triggers the sweep from a DIFFERENT <fp> dir,
        # so the old one stays idle and is retired whole.
        monkeypatch.setattr(cache_paths, "source_fingerprint", lambda: "newbuild")
        monkeypatch.setattr(output_cache, "_swept", False)
        output_cache.store_render(vi, "html", OPT, V, "NEW")

        assert not old_dir.exists()  # idle build reclaimed
        assert (root / "projects" / slug / "render" / "newbuild").is_dir()

    def test_no_within_fingerprint_aging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OLD file inside an ACTIVE ``<fp>`` dir survives — retirement is
        whole-dir only, never per-file within a live fingerprint."""
        import os
        import time

        monkeypatch.setenv("LVKIT_CACHE_TTL_DAYS", "7")
        monkeypatch.setenv("LVKIT_SWEEP_INTERVAL_HOURS", "0")  # never skip the walk
        after = _project_vi(tmp_path, b"A", name="After.vi")
        b1 = _vi(tmp_path / "t" / "b1.vi", b"B1")
        old = output_cache.store_diff(b1, after, "html", OPT, V, "D1")
        aged = time.time() - 30 * 86400
        os.utime(old, (aged, aged))
        # A fresh diff in the SAME <fp> dir keeps the whole dir active.
        monkeypatch.setattr(output_cache, "_swept", False)
        b2 = _vi(tmp_path / "t" / "b2.vi", b"B2")
        output_cache.store_diff(b2, after, "html", OPT, V, "D2")
        assert old.exists()  # kept: the fingerprint is still in use

    def test_extract_stickier_than_render(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At the SAME age (30d) a render ``<fp>`` dir past its 7d TTL is retired,
        but an extract ``<fp>`` dir within its 60d TTL survives."""
        monkeypatch.setenv("LVKIT_CACHE_TTL_DAYS", "7")
        monkeypatch.setenv("LVKIT_SWEEP_INTERVAL_HOURS", "0")  # never skip the walk
        monkeypatch.setenv("LVKIT_EXTRACT_TTL_DAYS", "60")
        root = cache_paths.global_cache_root()
        fslug = "proj-deadbeef"
        aged_render = root / "projects" / fslug / "render" / "aged1234"
        aged_extract = root / "projects" / fslug / "extract" / "aged5678"
        for d in (aged_render, aged_extract):
            d.mkdir(parents=True)
            (d / "f").write_text("x", encoding="utf-8")
        _age_dir(root / "projects" / fslug, 30)

        vi = _project_vi(tmp_path)
        monkeypatch.setattr(output_cache, "_swept", False)
        output_cache.store_render(vi, "html", OPT, V, "TRIGGER")

        assert not aged_render.exists()  # past its 7d render TTL
        assert aged_extract.exists()  # within its 60d extract TTL

    def test_sweep_rate_limited_by_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh ``<cache>/_last_sweep`` stamp makes the walk skip entirely — so
        the whole-cache scan is not a per-command cost. (Default 24h interval.)"""
        monkeypatch.setenv("LVKIT_CACHE_TTL_DAYS", "7")
        root = cache_paths.global_cache_root()
        # An aged build dir that WOULD be retired if the walk ran.
        aged = root / "projects" / "p-deadbeef" / "render" / "old00000"
        aged.mkdir(parents=True)
        (aged / "f").write_text("x", encoding="utf-8")
        _age_dir(root / "projects" / "p-deadbeef", 30)
        # A fresh sweep stamp (mtime == now) -> the walk must be skipped.
        root.mkdir(parents=True, exist_ok=True)
        (root / "_last_sweep").write_text("", encoding="utf-8")

        vi = _project_vi(tmp_path)
        monkeypatch.setattr(output_cache, "_swept", False)
        output_cache.store_render(vi, "html", OPT, V, "X")

        assert aged.exists()  # swept recently -> not retired this run
