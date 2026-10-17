"""Per-process staging: one directory per run, stale ones cleaned, no leftovers.

The antivirus-facing property (#131): an lvkit run creates ONE staging directory
under ``<cache>/.staging`` and one rename per extracted VI, instead of a
random-named directory plus a rename per output file for every VI. A run killed
mid-extraction leaves its directory behind; the next run removes it, but never a
directory whose owning process is still alive.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lvkit import cache_paths, extractor, staging


def _root() -> Path:
    return cache_paths.global_cache_root()


def _run_dirs() -> list[Path]:
    root = staging.staging_root(_root())
    return sorted(p for p in root.iterdir() if p.is_dir())


class TestRunDirectory:
    def test_work_dirs_share_one_run_directory(self) -> None:
        a = staging.work_dir(_root())
        b = staging.work_dir(_root())
        t = staging.temp_path(_root())
        assert a != b
        assert a.parent == b.parent == t.parent
        assert len(_run_dirs()) == 1

    def test_release_removes_directory_and_lock(self) -> None:
        staging.work_dir(_root())
        staging.release_all()
        assert list(staging.staging_root(_root()).iterdir()) == []

    def test_lock_file_is_held_while_the_run_is_alive(self) -> None:
        staging.work_dir(_root())
        locks = list(staging.staging_root(_root()).glob("*.lock"))
        assert len(locks) == 1
        assert not staging._owner_gone(locks[0])


class TestStaleCleanup:
    def _fake_run(self, name: str) -> tuple[Path, Path]:
        root = staging.staging_root(_root())
        root.mkdir(parents=True, exist_ok=True)
        directory = root / name
        directory.mkdir()
        (directory / "0").mkdir()
        lock = root / f"{name}.lock"
        lock.write_bytes(b"")
        return directory, lock

    def test_dead_owner_is_removed_live_owner_is_kept(self) -> None:
        dead_dir, dead_lock = self._fake_run("r-1-deadbeef")
        live_dir, live_lock = self._fake_run("r-2-cafef00d")
        with open(live_lock, "r+b") as holder:
            assert staging._try_lock(holder)  # a live owner holds its lock
            staging.work_dir(_root())  # a new run starts and sweeps
            assert not dead_dir.exists()
            assert not dead_lock.exists()
            assert live_dir.exists()
            assert live_lock.exists()

    def test_legacy_unlocked_directory_removed_only_when_old(self) -> None:
        root = staging.staging_root(_root())
        root.mkdir(parents=True, exist_ok=True)
        old = root / ".x-oldoldol"
        young = root / ".x-youngyou"
        old.mkdir()
        young.mkdir()
        two_hours_ago = time.time() - 7200
        os.utime(old, (two_hours_ago, two_hours_ago))
        staging.work_dir(_root())
        assert not old.exists()
        assert young.exists()

    def test_own_run_is_never_swept(self) -> None:
        first = staging.work_dir(_root())
        second = staging.work_dir(_root())
        assert first.exists() and second.exists()


def _fake_extract(vi: Path, out: Path, stem: str) -> None:
    for name in (f"{stem}.xml", f"{stem}_BDHb.xml", f"{stem}_FPHb.xml", "x_ICON.png"):
        (out / name).write_text("<a/>", encoding="utf-8")


class TestExtractionChurn:
    def _count(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
        counts = {"rename": 0, "replace": 0}
        real_rename, real_replace = os.rename, os.replace

        def rename(src, dst, *a, **k):  # type: ignore[no-untyped-def]
            counts["rename"] += 1
            return real_rename(src, dst, *a, **k)

        def replace(src, dst, *a, **k):  # type: ignore[no-untyped-def]
            counts["replace"] += 1
            return real_replace(src, dst, *a, **k)

        monkeypatch.setattr(os, "rename", rename)
        monkeypatch.setattr(os, "replace", replace)
        return counts

    def test_many_vis_one_staging_directory_one_rename_each(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extractor, "_extract_in_process", _fake_extract)
        (tmp_path / ".git").mkdir()
        counts = self._count(monkeypatch)
        for i in range(5):
            vi = tmp_path / f"v{i}.vi"
            vi.write_bytes(b"vi%d" % i)
            bd, fp, main = extractor.extract_vi_xml(vi)
            assert bd.exists() and fp is not None and main is not None
            assert (bd.parent / "vi.meta.json").exists()
        assert len(_run_dirs()) == 1
        assert counts["rename"] == 5  # one directory rename per VI
        assert counts["replace"] == 1  # the owner index, once for the project

    def test_cache_hit_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extractor, "_extract_in_process", _fake_extract)
        (tmp_path / ".git").mkdir()
        vi = tmp_path / "v.vi"
        vi.write_bytes(b"vi")
        extractor.extract_vi_xml(vi)
        staging.release_all()
        before = {
            p: p.stat().st_mtime_ns for p in _root().rglob("*") if p.is_file()
        }
        counts = self._count(monkeypatch)
        extractor.extract_vi_xml(vi)
        after = {p: p.stat().st_mtime_ns for p in _root().rglob("*") if p.is_file()}
        assert after == before
        assert counts == {"rename": 0, "replace": 0}
        assert not staging.staging_root(_root()).exists() or not _run_dirs()

    def test_no_staging_leftovers_after_a_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extractor, "_extract_in_process", _fake_extract)
        vi = tmp_path / "v.vi"
        vi.write_bytes(b"vi")
        extractor.extract_vi_xml(vi)
        staging.release_all()
        assert list(staging.staging_root(_root()).iterdir()) == []

    def test_failed_extraction_leaves_no_work_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(vi: Path, out: Path, stem: str) -> None:
            raise ValueError("bad vi")

        monkeypatch.setattr(extractor, "_extract_in_process", boom)
        vi = tmp_path / "v.vi"
        vi.write_bytes(b"vi")
        with pytest.raises(RuntimeError):
            extractor.extract_vi_xml(vi)
        (run,) = _run_dirs()
        assert list(run.iterdir()) == []

    def test_reextraction_of_unchanged_blocks_replaces_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extractor, "_extract_in_process", _fake_extract)
        vi = tmp_path / "v.vi"
        vi.write_bytes(b"vi")
        extractor.extract_vi_xml(vi)
        counts = self._count(monkeypatch)
        extractor.extract_vi_xml(vi, force=True)
        # Same bytes produced -> only the meta (which records a new time) moves.
        assert counts["replace"] == 1
