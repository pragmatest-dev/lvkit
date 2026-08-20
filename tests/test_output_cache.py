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
        slot, _meta, is_adhoc = output_cache._render_paths(b, "html")
        assert is_adhoc
        assert slot.parent == cache_paths.global_cache_root() / "adhoc" / "render"


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


# ── TTL sweep ───────────────────────────────────────────────────────────────


class TestTTLSweep:
    def test_stale_diff_swept_fresh_kept_slots_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        import time

        monkeypatch.setenv("LVKIT_CACHE_TTL_DAYS", "7")

        # A path-addressed render slot (must SURVIVE — never swept).
        vi = _project_vi(tmp_path, name="Render.vi")
        render_slot = output_cache.store_render(vi, "html", OPT, V, "KEEP")

        # A diff entry, aged well past the TTL.
        before = _vi(tmp_path / "t" / "old.vi", b"B")
        after = _project_vi(tmp_path, b"A2", name="After.vi")
        diff_slot = output_cache.store_diff(before, after, "html", OPT, V, "OLD-DIFF")
        old = time.time() - 30 * 86400
        os.utime(diff_slot, (old, old))

        # Force a sweep on the next write (it runs once per process).
        monkeypatch.setattr(output_cache, "_swept", False)
        fresh_before = _vi(tmp_path / "t" / "new.vi", b"NB")
        output_cache.store_diff(fresh_before, after, "html", OPT, V, "NEW-DIFF")

        assert not diff_slot.exists(), "stale diff should be swept"
        assert render_slot.exists(), "path-addressed render slot must never be swept"
        assert output_cache.lookup_render(vi, "html", OPT, V) == "KEEP"
