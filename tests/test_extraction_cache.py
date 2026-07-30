"""Unit tests for the cache-path logic (task #78; moved to ``cache_paths``).

Drives ``cache_paths.classify(vi, "extract")`` / ``extractor._cache_target``
directly for every row of the design doc's classification matrix, plus the
cache-freshness (mtime fast-path vs content-hash) invalidation logic. The autouse
``_hermetic_cache`` fixture (conftest) points ``LVKIT_CACHE_DIR`` at a per-test
tmp dir, so the global cache root here is always that tmp dir and nothing touches
the real ``~/.lvkit/cache``.

The location primitives now live in the stdlib-only ``lvkit.cache_paths`` module
(so the CLI's cache-HIT path can import them without pulling pylabview); each
artifact KIND gets its own top-level tree, so an extraction lands under
``<cache>/extract/<ns>/…`` rather than the old ``<cache>/<ns>/…``.

The crux regression: a vendored OpenG VI that lives *inside* a project tree but
is NOT under the run's ``userlib_root`` must land under ``extract/projects/…`` —
the prefix-match answer — not the ``shared/…`` tier the old substring scan gave.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit import cache_paths, extractor

_SAMPLE_VI = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
)


def _touch_vi(path: Path) -> Path:
    """Create an empty ``.vi`` file (+ parents). The classifier only does path
    arithmetic + ancestor marker checks, so contents are irrelevant here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _mark_project(root: Path) -> None:
    """Make ``root`` a project root (git marker) so ``_project_root_for`` finds
    it."""
    (root / ".git").mkdir(parents=True, exist_ok=True)


def _extract_root() -> Path:
    """The extraction kind tree: ``<cache>/extract``."""
    return cache_paths.global_cache_root() / "extract"


# ── classification matrix ──────────────────────────────────────────────────


class TestClassify:
    def test_no_project_is_adhoc(self, tmp_path: Path) -> None:
        # A bare dir with no .git/.lvkit ancestor -> adhoc/<slug(parent dir)>.
        vi = _touch_vi(tmp_path / "loose" / "foo.vi")
        target, source, ns = cache_paths.classify(vi, "extract")
        slug = cache_paths._slug(vi.parent.resolve())
        assert target == _extract_root() / "adhoc" / slug
        assert source == "foo.vi"
        assert ns == "adhoc"

    def test_under_project_is_projects(self, tmp_path: Path) -> None:
        proj = tmp_path / "myproj"
        _mark_project(proj)
        vi = _touch_vi(proj / "src" / "bar.vi")
        target, source, ns = cache_paths.classify(vi, "extract")
        slug = cache_paths._slug(proj.resolve())
        assert target == _extract_root() / "projects" / slug / "src"
        assert source == str(Path("src") / "bar.vi")
        assert ns == "projects"

    def test_under_vilib_root_is_shared_vilib(self, tmp_path: Path) -> None:
        vilib = tmp_path / "LV2025-64" / "vi.lib"
        vi = _touch_vi(vilib / "Utility" / "u.vi")
        cache_paths.set_extraction_roots(vilib_root=vilib, userlib_root=None)
        target, source, ns = cache_paths.classify(vi, "extract")
        slug = cache_paths._slug(vilib.resolve())
        assert target == _extract_root() / "shared" / "vilib" / slug / "Utility"
        assert source == str(Path("Utility") / "u.vi")
        assert ns == "shared"

    def test_under_userlib_root_is_shared_userlib(self, tmp_path: Path) -> None:
        userlib = tmp_path / "install" / "user.lib"
        vi = _touch_vi(userlib / "MyAddon" / "a.vi")
        cache_paths.set_extraction_roots(vilib_root=None, userlib_root=userlib)
        target, _, _ = cache_paths.classify(vi, "extract")
        slug = cache_paths._slug(userlib.resolve())
        assert target == _extract_root() / "shared" / "userlib" / slug / "MyAddon"

    def test_vendored_openg_under_project_is_projects_not_shared(
        self, tmp_path: Path
    ) -> None:
        """THE crux: OpenG vendored inside the project tree, with the run's
        userlib_root pointing at a *different* install dir. Prefix-match must put
        it under projects/ (a project copy), never shared/ — which the old
        substring `_OpenG.lib` marker got wrong."""
        proj = tmp_path / "work"
        _mark_project(proj)
        vi = _touch_vi(
            proj / "user.lib" / "_OpenG.lib" / "array" / "Sort.vi"
        )
        # A real, unrelated install userlib elsewhere — the VI is NOT under it.
        other_userlib = tmp_path / "LVinstall" / "user.lib"
        other_userlib.mkdir(parents=True, exist_ok=True)
        cache_paths.set_extraction_roots(
            vilib_root=None, userlib_root=other_userlib
        )

        target, _, _ = cache_paths.classify(vi, "extract")
        root = _extract_root()
        assert (root / "projects") in target.parents
        assert (root / "shared") not in target.parents

    def test_two_vilib_roots_distinct_namespaces(self, tmp_path: Path) -> None:
        """LV2025-64 vs -32: two distinct vilib roots -> distinct shared/vilib
        namespaces, no collision, even for the same relative VI path."""
        root64 = tmp_path / "LV2025-64" / "vi.lib"
        root32 = tmp_path / "LV2025-32" / "vi.lib"
        rel = Path("Utility") / "same.vi"
        vi64 = _touch_vi(root64 / rel)
        vi32 = _touch_vi(root32 / rel)

        cache_paths.set_extraction_roots(vilib_root=root64, userlib_root=None)
        t64, _, _ = cache_paths.classify(vi64, "extract")
        cache_paths.set_extraction_roots(vilib_root=root32, userlib_root=None)
        t32, _, _ = cache_paths.classify(vi32, "extract")

        assert t64 != t32
        # target == shared/vilib/<ns>/Utility -> parent.parent is shared/vilib
        # (shared), parent.name is the <root-slug> namespace.
        assert t64.parent.parent == t32.parent.parent
        assert t64.parent.parent.name == "vilib"
        assert t64.parent.name != t32.parent.name

    def test_shared_vilib_reused_across_projects(self, tmp_path: Path) -> None:
        """A vilib VI resolves to the SAME shared entry regardless of which
        project is being processed — written once, reused by both."""
        vilib = tmp_path / "LV" / "vi.lib"
        vi = _touch_vi(vilib / "Array" / "Init.vi")
        cache_paths.set_extraction_roots(vilib_root=vilib, userlib_root=None)

        first, _, _ = cache_paths.classify(vi, "extract")
        # (roots persist for the run; a second project's pass sees the same VI)
        second, _, _ = cache_paths.classify(vi, "extract")
        assert first == second
        assert (_extract_root() / "shared" / "vilib") in first.parents

    def test_cache_target_creates_dir(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        _mark_project(proj)
        vi = _touch_vi(proj / "x.vi")
        target = extractor._cache_target(vi)
        assert target.is_dir()


# ── cache freshness: mtime fast-path vs content-hash invalidation ───────────


class TestCacheFreshness:
    def test_touch_hits_fastpath_content_edit_invalidates(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "x.vi"
        f.write_bytes(b"hello world")
        meta = tmp_path / "x.meta.json"
        extractor._write_cache_meta(f, meta)

        # Unchanged -> fresh.
        assert cache_paths.meta_fresh(f, meta)

        # Touch only (mtime bumped, same bytes) -> fresh via sha fast-path.
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + 100))
        assert cache_paths.meta_fresh(f, meta)

        # Content edit -> not fresh, re-extract.
        f.write_bytes(b"hello world!!")
        assert not cache_paths.meta_fresh(f, meta)

    def test_extra_fields_must_match(self, tmp_path: Path) -> None:
        """When ``extra`` is given (e.g. lvkit version + render options), a
        mismatch is a miss even though the VI bytes are unchanged — the hook the
        render/diff output cache uses to invalidate on a version/options change.
        """
        f = tmp_path / "x.vi"
        f.write_bytes(b"same bytes")
        meta = tmp_path / "x.meta.json"
        cache_paths.write_meta(f, meta, lvkit_version="0.5.7", options="html|auto")

        assert cache_paths.meta_fresh(
            f, meta, extra={"lvkit_version": "0.5.7", "options": "html|auto"}
        )
        # Version bump -> miss.
        assert not cache_paths.meta_fresh(
            f, meta, extra={"lvkit_version": "0.5.8", "options": "html|auto"}
        )
        # Options change (e.g. theme) -> miss.
        assert not cache_paths.meta_fresh(
            f, meta, extra={"lvkit_version": "0.5.7", "options": "svg|dark"}
        )


# ── real extraction: cold miss extracts, warm hit does not ──────────────────


@pytest.mark.needs_samples
class TestRealExtraction:
    SAMPLE = Path(
        ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/"
        "TrackDroppedFrames_FP.vi"
    )

    def test_cold_extracts_warm_hits_edit_reextracts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if not self.SAMPLE.exists():
            pytest.skip(f"sample VI not available: {self.SAMPLE}")
        # Copy into a temp dir so we can mutate it without touching the corpus.
        vi = tmp_path / "sample.vi"
        vi.write_bytes(self.SAMPLE.read_bytes())

        calls: list[int] = []
        orig = extractor._extract_in_process

        def spy(vi: Path, out: Path, stem: str) -> None:
            calls.append(1)
            return orig(vi, out, stem)

        monkeypatch.setattr(extractor, "_extract_in_process", spy)

        # Cold -> one extraction, cache lands under LVKIT_CACHE_DIR.
        bd, _fp, _main = extractor.extract_vi_xml(vi)
        assert len(calls) == 1
        assert cache_paths.global_cache_root() in bd.resolve().parents

        # Warm -> hit, no new extraction.
        extractor.extract_vi_xml(vi)
        assert len(calls) == 1

        # Touch only -> still a hit (sha fast-path).
        st = vi.stat()
        os.utime(vi, (st.st_atime, st.st_mtime + 100))
        extractor.extract_vi_xml(vi)
        assert len(calls) == 1

        # Content edit -> re-extract (restore-then-append keeps it a valid VI).
        data = self.SAMPLE.read_bytes()
        vi.write_bytes(data + b"\x00")
        extractor.extract_vi_xml(vi)
        assert len(calls) == 2


# ── repo-cleanliness invariant + cold/warm parity (subprocess CLI) ──────────


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run `lvkit` in a subprocess. LVKIT_CACHE_DIR is set in os.environ by the
    autouse hermetic-cache fixture, so the child inherits it."""
    return subprocess.run(
        [sys.executable, "-m", "lvkit.cli", *args],
        capture_output=True,
        text=True,
    )


@pytest.mark.needs_samples
class TestRepoCleanliness:
    def test_describe_never_writes_lvkit_cache_into_repo(
        self, tmp_path: Path
    ) -> None:
        if not _SAMPLE_VI.exists():
            pytest.skip(f"sample VI not available: {_SAMPLE_VI}")
        # A throwaway git repo with the VI copied inside it.
        repo = tmp_path / "userrepo"
        (repo / ".git").mkdir(parents=True)
        vi = repo / "MyVI.vi"
        vi.write_bytes(_SAMPLE_VI.read_bytes())

        res = _run_cli("describe", str(vi))
        assert res.returncode == 0, res.stderr

        # The whole point: no derived cache appears in the user's repo.
        assert not (repo / ".lvkit" / "cache").exists()
        assert not (repo / ".lvkit").exists()

        # And the extraction landed under LVKIT_CACHE_DIR (extract/projects tier).
        cache_root = Path(os.environ["LVKIT_CACHE_DIR"])
        bd_files = list(cache_root.rglob("MyVI_BDHb.xml"))
        assert bd_files, f"no extraction found under {cache_root}"
        assert (cache_root / "extract" / "projects") in bd_files[0].parents

    def test_cold_and_warm_runs_are_byte_identical(self, tmp_path: Path) -> None:
        if not _SAMPLE_VI.exists():
            pytest.skip(f"sample VI not available: {_SAMPLE_VI}")
        repo = tmp_path / "r"
        (repo / ".git").mkdir(parents=True)
        vi = repo / "V.vi"
        vi.write_bytes(_SAMPLE_VI.read_bytes())

        cold = _run_cli("describe", str(vi))
        warm = _run_cli("describe", str(vi))
        assert cold.returncode == 0 and warm.returncode == 0, cold.stderr
        assert cold.stdout == warm.stdout


class TestGlobalHomeGuard:
    """`find_project_store` must never mistake the branded global home
    (`~/.lvkit`) for a *project* store — any `.lvkit/` dir otherwise qualifies.
    The guard compares against the `global_home()` accessor, so we drive the
    accessor here rather than a hard-coded path."""

    def test_home_lvkit_is_not_a_project_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lvkit import project_store

        home_store = tmp_path / ".lvkit"          # stands in for ~/.lvkit
        home_store.mkdir()
        monkeypatch.setattr(project_store, "global_home", lambda: home_store)

        # A VI sitting loose under the home, no nearer .git/.lvkit.
        loose = tmp_path / "loose"
        loose.mkdir()
        assert project_store.find_project_store(loose) != home_store

    def test_real_project_store_still_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lvkit import project_store

        monkeypatch.setattr(
            project_store, "global_home", lambda: tmp_path / ".lvkit"
        )
        proj = tmp_path / "proj"
        (proj / ".lvkit").mkdir(parents=True)
        assert project_store.find_project_store(proj) == proj / ".lvkit"

    def test_project_root_skips_global_home_lvkit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The classifier's own root walk (`cache_paths._project_root_for`) must
        also skip `~/.lvkit`. Without the guard, a VI inside a git repo that
        lives under $HOME resolves to hash($HOME) — every project collapsing
        into one bucket and climbing past its own `.git`. Regression for the
        observed Windows cache tree (#78)."""
        from lvkit import project_store

        home = tmp_path / "home"
        (home / ".lvkit").mkdir(parents=True)  # stands in for the global ~/.lvkit
        monkeypatch.setattr(project_store, "global_home", lambda: home / ".lvkit")

        repo = home / "repo"  # a real git repo nested under the home
        (repo / ".git").mkdir(parents=True)
        vi = _touch_vi(repo / "source" / "x.vi")

        # Resolves to the repo, not the home; and lands under slug(repo).
        assert cache_paths._project_root_for(vi.resolve()) == repo.resolve()
        target, source, _ = cache_paths.classify(vi, "extract")
        slug = cache_paths._slug(repo.resolve())
        assert target == _extract_root() / "projects" / slug / "source"
        assert source == str(Path("source") / "x.vi")
