"""Tests for lvkit.index — the code-understanding facts index engine.

Two tiers:

- ``TestSmallClassBuild`` — fast, exercises build + SQLite round-trip on one
  small class dir (~31 VIs, no same-name collisions). Runs on every test
  invocation that has the sample corpus.
- ``TestFullCorpusDemo`` — the actual 4-question acceptance demo from
  ``docs/_internal/design/lvkit-mcp-index.md`` §5, built ONCE (module-scoped
  fixture) over the full JKI VI Tester corpus (487 ``.vi`` files). A
  whole-repo MINIMAL load is O(minutes) even with a warm extraction cache
  (measured baseline in the design doc) — the fixture deliberately escapes
  the per-test hermetic cache dir (see ``jki_index`` below) to reuse the
  developer's real, already-warm extraction cache instead of re-extracting
  487 VIs from scratch on every run.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from lvkit.index.build import BuildResult, build_index, refresh_index
from lvkit.index.model import WIRED_INDICATOR
from lvkit.index.project import resolve_project
from lvkit.index.query import blast_radius, get_callers
from lvkit.index.store import load as load_index
from lvkit.index.store import save as save_index

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
JKI_ROOT = SAMPLES / "JKI-VI-Tester"
TESTCASE_DIR = JKI_ROOT / "source" / "Classes" / "TestCase"


# === Fast: a small class dir — build + store round-trip ====================


class TestSmallClassBuild:
    def test_build_over_testcase_dir(self):
        root, vi_paths = resolve_project(TESTCASE_DIR)
        assert root == TESTCASE_DIR.resolve()
        assert vi_paths  # sanity: the fixture dir has VIs

        result = build_index(root, vi_paths)

        assert len(result.facts) == len(vi_paths)
        # A single class dir has no same-named siblings to shadow each other.
        assert result.collisions == 0
        assert {f.path for f in result.facts} == {str(p) for p in vi_paths}
        # Every method VI has a real content hash and terminals.
        assert all(f.content_sha for f in result.facts)
        assert any(f.terminals for f in result.facts)

    def test_class_methods_have_owning_class_fact(self):
        root, vi_paths = resolve_project(TESTCASE_DIR)
        result = build_index(root, vi_paths)

        with_class_fact = [f for f in result.facts if f.class_fact is not None]
        assert with_class_fact
        for f in with_class_fact:
            class_fact = f.class_fact
            assert class_fact is not None
            assert class_fact.owning_class.endswith(".lvclass")

    def test_store_round_trip(self):
        root, vi_paths = resolve_project(TESTCASE_DIR)
        result = build_index(root, vi_paths)

        save_index(root, result.facts)
        reloaded = load_index(root)

        assert len(reloaded) == len(result.facts)
        by_path_orig = {f.path: f for f in result.facts}
        by_path_reloaded = {f.path: f for f in reloaded}
        assert set(by_path_orig) == set(by_path_reloaded)

        sample_path = next(iter(by_path_orig))
        orig = by_path_orig[sample_path]
        back = by_path_reloaded[sample_path]
        assert [t.name for t in back.terminals] == [t.name for t in orig.terminals]
        assert [t.is_error_cluster for t in back.terminals] == [
            t.is_error_cluster for t in orig.terminals
        ]
        assert len(back.constants) == len(orig.constants)
        assert back.calls == orig.calls
        assert back.type_uses == orig.type_uses


# === Incremental refresh (content-hash) — fast, on the small class dir =====


class TestIncrementalRefresh:
    def _built(self) -> tuple[Path, list[Path], list]:
        root, vi_paths = resolve_project(TESTCASE_DIR)
        return root, vi_paths, build_index(root, vi_paths).facts

    def test_no_change_rebuilds_nothing(self):
        root, vi_paths, stored = self._built()
        rr, merged = refresh_index(root, vi_paths, stored)
        assert rr.rebuilt == []
        assert rr.deleted == []
        assert rr.total == len(stored)
        assert {f.path for f in merged} == {f.path for f in stored}

    def test_stale_sha_forces_single_rebuild(self):
        root, vi_paths, stored = self._built()
        # Corrupt one fact's content hash -> refresh rebuilds ONLY that VI.
        target = stored[0].path
        stored[0].content_sha = "stale"
        rr, merged = refresh_index(root, vi_paths, stored)
        assert rr.rebuilt == [target]
        back = {f.path: f for f in merged}[target]
        assert back.content_sha and back.content_sha != "stale"

    def test_deleted_file_is_dropped(self):
        root, vi_paths, stored = self._built()
        dropped = str(vi_paths[0].resolve())
        rr, merged = refresh_index(root, vi_paths[1:], stored)
        assert rr.rebuilt == []
        assert dropped in rr.deleted
        assert dropped not in {f.path for f in merged}


# === Slow: the full JKI VI Tester corpus — the 4-question acceptance demo ==

_HAVE_JKI = JKI_ROOT.is_dir() and any(JKI_ROOT.rglob("*.vi"))


@pytest.fixture(scope="module")
def jki_index() -> BuildResult:
    if not _HAVE_JKI:
        pytest.skip("JKI-VI-Tester sample not present")

    # The per-test ``_hermetic_cache`` autouse fixture (tests/conftest.py)
    # points LVKIT_CACHE_DIR at a fresh tmp dir per test function so no test
    # writes to the real cache. That's right for cheap tests, but a
    # whole-repo 487-VI MINIMAL build pays full pylabview extraction from
    # scratch every time it runs cold. This fixture is module-scoped (built
    # once) AND deliberately unsets the override for the build itself, so it
    # reuses the developer's real, already-warm extraction cache
    # (~/.lvkit/cache) — same real-world path a `lvkit index` run takes.
    saved = os.environ.pop("LVKIT_CACHE_DIR", None)
    try:
        root, vi_paths = resolve_project(JKI_ROOT)
        return build_index(root, vi_paths)
    finally:
        if saved is not None:
            os.environ["LVKIT_CACHE_DIR"] = saved


@pytest.mark.slow
class TestFullCorpusDemo:
    def test_path_keyed_no_collision_loss(self, jki_index: BuildResult):
        """Demo #1: indexed VI count == every .vi file under the repo, not
        the name-collapsed 422 the bare in-memory graph gives."""
        vi_paths = sorted(JKI_ROOT.rglob("*.vi"))
        assert len(jki_index.facts) == len(vi_paths)
        assert {f.path for f in jki_index.facts} == {str(p) for p in vi_paths}
        assert jki_index.collisions > 0

    def test_error_indicator_tally(self, jki_index: BuildResult):
        """Demo #2: error-indicator terminals, tallied by name — 'error out'
        dominates."""
        names = [
            t.name
            for f in jki_index.facts
            for t in f.terminals
            if t.is_error_cluster and t.direction == "output"
        ]
        assert len(names) >= 325

        tally = Counter(names)
        top_name, top_count = tally.most_common(1)[0]
        assert top_name == "error out"
        assert top_count >= 298

    def test_callers_exclude_ownership_edges(self, jki_index: BuildResult):
        """Demo #3: a class method's calls and callers are pure VI->VI — its
        owning class never appears as a call or a caller.

        ``calls`` is ``_dep_graph`` successors minus ``rel=="owns"`` AND minus
        class/typedef/library nodes (build.project_vi_facts): a method
        referencing its own class TYPE for a "self" param is a type use —
        captured in ``type_uses`` — not a call. So the owning class is neither
        in ``calls`` nor returned by ``get_callers``.
        """
        class_methods = [f for f in jki_index.facts if f.class_fact is not None]
        assert class_methods  # JKI-VI-Tester is a class-heavy corpus

        # The owning class is a type reference, never a call — regression guard
        # for the class/typedef/library filter in build.project_vi_facts.
        for f in class_methods:
            assert f.class_fact is not None
            assert f.class_fact.owning_class not in f.calls

        all_paths = {f.path for f in jki_index.facts}
        with_callers = [
            f for f in class_methods if get_callers(jki_index.facts, f.path)
        ]
        assert with_callers
        sample = with_callers[0]
        sample_class_fact = sample.class_fact
        assert sample_class_fact is not None
        callers = get_callers(jki_index.facts, sample.path)
        assert sample_class_fact.owning_class not in callers
        assert set(callers) <= all_paths

    def test_constants_wired_to_indicators(self, jki_index: BuildResult):
        """Demo #4: constants wired directly to an indicator, non-empty."""
        wired = [
            c
            for f in jki_index.facts
            for c in f.constants
            if c.wired_to == WIRED_INDICATOR
        ]
        assert wired

    def test_blast_radius(self, jki_index: BuildResult):
        """blast_radius returns transitive dependents; impact_score == len,
        and matches the impact_score computed at build time."""
        candidate = max(jki_index.facts, key=lambda f: f.impact_score)
        assert candidate.impact_score > 0

        result = blast_radius(jki_index.facts, candidate.path)
        assert result.impact_score == len(result.dependents)
        assert result.impact_score == candidate.impact_score
        assert candidate.path not in result.dependents

        # A depth-1 radius is never larger than the unbounded one.
        shallow = blast_radius(jki_index.facts, candidate.path, depth=1)
        assert shallow.impact_score <= result.impact_score
