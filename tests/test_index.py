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

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from lvkit.cli import cmd_structure
from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.index.build import (
    BuildResult,
    build_index,
    build_lvproj_membership,
    refresh_index,
)
from lvkit.index.model import NodeKind, WiredTo
from lvkit.index.project import resolve_project
from lvkit.index.query import blast_radius, get_callers
from lvkit.index.store import load as load_index
from lvkit.index.store import load_lvproj_members, save_lvproj_members
from lvkit.index.store import save as save_index
from lvkit.structure import parse_lvlib

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
JKI_ROOT = SAMPLES / "JKI-VI-Tester"
TESTCASE_DIR = JKI_ROOT / "source" / "Classes" / "TestCase"


# === Fast: a small class dir — build + store round-trip ====================


class TestParallelBuildEquivalence:
    """``load_directory``'s optional parallel pre-parse (graph/parallel_parse.py)
    must be byte-identical to the serial path — see graph/loading.py's
    ``PARALLEL_THRESHOLD`` gate. The real threshold (50) is above this small
    class dir's VI count, so both runs force the gate via monkeypatch: one
    high (never parallel), one at 0 (always parallel, even for this small
    dir) — same VI set either way, so any divergence is a real bug, not a
    sample-size artifact.
    """

    def test_parallel_matches_serial(self, monkeypatch):
        root, vi_paths = resolve_project(TESTCASE_DIR)
        assert len(vi_paths) < 50  # sanity: confirms the real gate wouldn't fire here

        monkeypatch.setattr("lvkit.graph.loading.PARALLEL_THRESHOLD", 10**9)
        serial = build_index(root, vi_paths)

        monkeypatch.setattr("lvkit.graph.loading.PARALLEL_THRESHOLD", 0)
        parallel = build_index(root, vi_paths)

        assert serial.collisions == parallel.collisions
        assert {f.path for f in serial.facts} == {f.path for f in parallel.facts}

        by_path_serial = {f.path: f for f in serial.facts}
        by_path_parallel = {f.path: f for f in parallel.facts}
        for path, sf in by_path_serial.items():
            pf = by_path_parallel[path]
            # Full dataclass equality: terminals (name/direction/type_descriptor/
            # type_kind/field_names, in order), constants, calls, type_uses,
            # class_fact, is_stub, impact_score -- everything VIFacts carries.
            assert pf == sf, f"parallel facts diverged from serial for {path}"


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
        # The block-diagram node spine is populated, and every row carries a
        # kind from the closed NodeKind set (a stringly-typed leak would show
        # up as an unexpected value here).
        assert any(f.nodes for f in result.facts)
        kinds = {n.kind for f in result.facts for n in f.nodes}
        assert kinds <= set(NodeKind)

    def test_node_callee_paths_resolve(self):
        # Merge-time backfill: a kind='vi' node whose callee is an in-repo VI
        # gets callee_path set to that VI's path (the direct call-site edge on
        # the node spine), and every resolved path points at a real indexed VI.
        root, vi_paths = resolve_project(TESTCASE_DIR)
        result = build_index(root, vi_paths)
        indexed = {f.path for f in result.facts}

        vi_nodes = [
            n for f in result.facts for n in f.nodes if n.kind is NodeKind.VI
        ]
        resolved = [n for n in vi_nodes if n.callee_path is not None]
        assert resolved, "no SubVI-call node resolved to an in-repo callee path"
        # Every resolved callee_path is a real indexed VI (never a guess).
        assert all(n.callee_path in indexed for n in resolved)
        # A qualified same-class call (run.vi -> CallTestMethod.vi) resolves.
        assert any(
            n.callee_path and n.callee_path.endswith("CallTestMethod.vi")
            for n in vi_nodes
        )

    def test_class_methods_have_owning_class_fact(self):
        root, vi_paths = resolve_project(TESTCASE_DIR)
        result = build_index(root, vi_paths)

        with_class_fact = [f for f in result.facts if f.class_fact is not None]
        assert with_class_fact
        for f in with_class_fact:
            class_fact = f.class_fact
            assert class_fact is not None
            assert class_fact.owning_class.endswith(".lvclass")
            # A directory build loads class methods as loose VIs (no
            # .lvlib/.lvclass "library" field on the VI itself) — build.
            # project_vi_facts falls back to the owning .lvclass as the
            # facts-index "library" column. This is the ALREADY-SHIPPED
            # .lvclass half of that fallback; the sibling .lvlib half is
            # covered by TestLibraryMembership below.
            assert f.library == class_fact.owning_class

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
        assert [t.type_descriptor for t in back.terminals] == [
            t.type_descriptor for t in orig.terminals
        ]
        assert [t.type_kind for t in back.terminals] == [
            t.type_kind for t in orig.terminals
        ]
        assert len(back.constants) == len(orig.constants)
        assert back.type_uses == orig.type_uses
        # The node spine round-trips in order, with kinds preserved.
        assert [n.uid for n in back.nodes] == [n.uid for n in orig.nodes]
        assert [n.kind for n in back.nodes] == [n.kind for n in orig.nodes]


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
            if t.type_descriptor == "Error" and t.direction == "output"
        ]
        assert len(names) == 406

        tally = Counter(names)
        top_name, top_count = tally.most_common(1)[0]
        assert top_name == "error out"
        assert top_count == 382

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

        # The call graph is VI->VI only: a node's callee_path is always a VI
        # path, never the owning class (a type reference is not a call).
        for f in class_methods:
            assert f.class_fact is not None
            callee_paths = {n.callee_path for n in f.nodes if n.callee_path}
            assert f.class_fact.owning_class not in callee_paths

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
            if c.wired_to == WiredTo.INDICATOR
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

    def test_dead_code_via_callers_count(self, jki_index: BuildResult):
        """#20: uncalled-VI / dead-code detection reads ``callers_count`` — the
        in-degree of the path-keyed inverse call graph, which is now derived
        from the ``kind='vi'`` node spine (each SubVI-call node's resolved
        ``callee_path``). Keyed on VI path, so NULL-``qualified_name`` VIs are
        still classified.
        """
        by_name: dict[str, list] = {}
        for f in jki_index.facts:
            by_name.setdefault(f.name, []).append(f)

        # A common init subVI — definitely called (unique name in the corpus).
        # 14 (was 12 off the calls table): the node spine counts actual
        # block-diagram call sites, catching real static calls the VI-dependency
        # list missed.
        called = by_name["TestCase_Init.vi"]
        assert len(called) == 1
        assert called[0].callers_count == 14

        # A genuine top-level example runner — nothing calls it (unique name).
        uncalled = by_name["VI Tester JUnitXML Example.vi"]
        assert len(uncalled) == 1
        assert uncalled[0].callers_count == 0

        total_uncalled = sum(1 for f in jki_index.facts if f.callers_count == 0)
        # Plausible dead-code count: some, but not everything and not nothing.
        assert 0 < total_uncalled < len(jki_index.facts)
        # 229 (no static caller): dead code + top-level entry points + VIs
        # reached only dynamically (Call-By-Reference / VI Server). Matches the
        # eval harness; the prior 284 was a stale pre-node-spine pin.
        assert total_uncalled == 229


# === parse_lvlib folder recursion (real corpus, no graph build needed) =====

# A real .lvlib nests members inside Type="Folder" containers (Public/
# Private/Protected/custom scope groups) — parse_lvlib must flatten those,
# not just read top-level <Item>s. These three cover the shapes seen in the
# corpus: fully nested (WaveGen, MeasurementServerTests) and flat/unaffected
# (VITesterUtilities, used again below by TestLibraryMembership).
_WAVEGEN_LVLIB = SAMPLES / "lv-flex-channel-examples" / "WaveGen" / "WaveGen.lvlib"
_MEASUREMENT_SERVER_TESTS_LVLIB = (
    SAMPLES
    / "measurement-plugin-labview"
    / "Source"
    / "Tests"
    / "Tests.Runtime"
    / "Measurement Server"
    / "MeasurementServerTests.lvlib"
)
_VITESTER_UTILITIES_LVLIB = (
    JKI_ROOT / "source" / "Libraries" / "VITesterUtilities.lvlib"
)
_VITESTER_UTILITIES_MEMBER_COUNT = 46  # <Item Type="VI" .../> count in the file

# The one LVClass-typed member in the JKI-VI-Tester corpus's lvlib set —
# MyLibrary.lvlib is flat (no Folder nesting), so this exercises the
# Type="LVClass" branch independent of the folder-recursion fix above.
# It is itself a "Library"-typed member of MyParentLibrary.lvlib (one level
# up, same dir) — a REAL two-deep library nesting in the corpus. Its class
# member's own method VIs embed a FULLY qualified name that carries that
# whole chain (verified against the real corpus:
# "MyParentLibrary.lvlib:MyLibrary.lvlib:ABC - Parentheses (Valid).lvclass:
# setUp.vi"), so ``load_lvlib``'s LVClass branch MUST pass owner_chain for
# the "owns" edge to land on the real VI node — entering through the
# correct nesting (MyParentLibrary.lvlib, not MyLibrary.lvlib standalone)
# is what makes that chain line up.
_MYPARENTLIBRARY_LVLIB = (
    JKI_ROOT / "source" / "Tests" / "Library Test" / "MyParentLibrary.lvlib"
)
_MYLIBRARY_LVLIB = JKI_ROOT / "source" / "Tests" / "Library Test" / "MyLibrary.lvlib"
_MYLIBRARY_CLASS_NAME = (
    "MyParentLibrary.lvlib:MyLibrary.lvlib:ABC - Parentheses (Valid).lvclass"
)
_MYLIBRARY_CLASS_METHODS = {
    "setUp.vi",
    "testExample.vi",
    "tearDown.vi",
    "test (Example).vi",
}


class TestParseLvlibFolderRecursion:
    """``parse_lvlib`` (structure.py) must recurse into Type="Folder" items —
    a member's URL is already the full relative path from the .lvlib, so
    flattening the folder nesting during parse is correct and loses no
    information.
    """

    def test_wavegen_recovers_nested_members(self):
        if not _WAVEGEN_LVLIB.is_file():
            pytest.skip(f"Sample not available: {_WAVEGEN_LVLIB}")

        lib = parse_lvlib(_WAVEGEN_LVLIB)
        assert len(lib.members) == 8
        names = {m.name for m in lib.members}
        assert "WaveGen.vi" in names
        assert "Generate.vi" in names
        # Folder containers themselves are never members.
        assert all(m.member_type != "Folder" for m in lib.members)

    def test_measurement_server_tests_recovers_nested_members(self):
        if not _MEASUREMENT_SERVER_TESTS_LVLIB.is_file():
            pytest.skip(f"Sample not available: {_MEASUREMENT_SERVER_TESTS_LVLIB}")

        lib = parse_lvlib(_MEASUREMENT_SERVER_TESTS_LVLIB)
        assert len(lib.members) == 192

    def test_flat_lvlib_is_unaffected(self):
        """A flat (non-nested) .lvlib's member count must not change."""
        if not _VITESTER_UTILITIES_LVLIB.is_file():
            pytest.skip(f"Sample not available: {_VITESTER_UTILITIES_LVLIB}")

        lib = parse_lvlib(_VITESTER_UTILITIES_LVLIB)
        assert len(lib.members) == _VITESTER_UTILITIES_MEMBER_COUNT
        assert all(m.member_type == "VI" for m in lib.members)


class TestStructureCommandLvlibMembers:
    """``lvkit structure Foo.lvlib`` (cmd_structure, cli.py) prints/emits
    ``Members (N)`` straight from ``lib.members`` — a user-facing guard for
    the folder-recursion fix above, not just the internal ``parse_lvlib``
    return value.
    """

    def test_json_reports_all_nested_members(
        self,
        capsys: pytest.CaptureFixture[str],
    ):
        if not _WAVEGEN_LVLIB.is_file():
            pytest.skip(f"Sample not available: {_WAVEGEN_LVLIB}")

        rc = cmd_structure(
            argparse.Namespace(input=str(_WAVEGEN_LVLIB), json=True, plan=False)
        )
        out = capsys.readouterr().out

        assert rc == 0
        data = json.loads(out)
        assert len(data["members"]) == 8
        names = {m["name"] for m in data["members"]}
        assert "WaveGen.vi" in names
        assert "Generate.vi" in names

    def test_text_output_reports_member_count(
        self,
        capsys: pytest.CaptureFixture[str],
    ):
        if not _WAVEGEN_LVLIB.is_file():
            pytest.skip(f"Sample not available: {_WAVEGEN_LVLIB}")

        rc = cmd_structure(
            argparse.Namespace(input=str(_WAVEGEN_LVLIB), json=False, plan=False)
        )
        out = capsys.readouterr().out

        assert rc == 0
        assert "Members (8):" in out


class TestLoadLvlibClassMember:
    """``load_lvlib`` (graph/loading.py) must register a Type="LVClass"
    member as a class node — previously dropped entirely because the branch
    checked for ``member_type == "Class"``, a string real ``.lvlib`` XML
    never uses (it's always "LVClass").
    """

    def test_class_member_becomes_class_node_with_methods(self):
        if not _MYPARENTLIBRARY_LVLIB.is_file():
            pytest.skip(f"Sample not available: {_MYPARENTLIBRARY_LVLIB}")
        assert _MYLIBRARY_LVLIB.is_file()  # sanity: the nested lvlib exists too

        # Enter through MyParentLibrary.lvlib (its real container) — not
        # MyLibrary.lvlib directly — so owner_chain accumulates the full,
        # correct two-library prefix. search_paths=[JKI_ROOT]: both
        # lvlib's own nested-Library URL and the class's URL are one
        # directory off from their real on-disk locations, so resolution
        # falls through to _find_file's rglob(project_root) fallback — the
        # same search_paths build.py's _load_library_ownership passes for
        # the real index build.
        graph = InMemoryVIGraph()
        graph.load_lvlib(
            _MYPARENTLIBRARY_LVLIB,
            LoadMode.FULL,
            search_paths=[JKI_ROOT],
        )

        assert _MYLIBRARY_CLASS_NAME in graph.list_classes()

        # The class's methods loaded as real VI nodes, owned by the class
        # (not left as a dead/unreachable member).
        owned = {
            succ
            for succ in graph._dep_graph.successors(_MYLIBRARY_CLASS_NAME)
            if (graph._dep_graph.get_edge_data(_MYLIBRARY_CLASS_NAME, succ) or {}).get(
                "rel"
            )
            == "owns"
        }
        # Method VIs are keyed by their file path (identity) now, so the owns
        # edges point at those paths — take the filename for the method name.
        assert {Path(n).name for n in owned} == _MYLIBRARY_CLASS_METHODS


# === .lvlib membership -> "library" column (real corpus) ===================


@pytest.mark.slow
class TestLibraryMembership:
    """``_load_library_ownership`` (build.py) is the ``.lvlib`` mirror of the
    already-shipped ``_load_class_ownership``: a directory walk loads
    ``.lvlib`` member VIs as loose files with no ``VINode.library`` set, so
    without this pass ``VIFacts.library`` stays ``None`` for them.

    Reuses the module-scoped ``jki_index`` fixture (built once for
    ``TestFullCorpusDemo`` above) instead of paying for a second full-corpus
    build.
    """

    def test_lvlib_members_get_library_fact(self, jki_index: BuildResult):
        assert _VITESTER_UTILITIES_LVLIB.is_file()  # sanity: fixture VI exists

        members = [f for f in jki_index.facts if f.library == "VITesterUtilities.lvlib"]
        assert len(members) == _VITESTER_UTILITIES_MEMBER_COUNT

        # Spot-check one specific, known member VI.
        by_name = {f.name: f for f in members}
        assert "Get LVClass Name from TD.vi" in by_name
        sample = by_name["Get LVClass Name from TD.vi"]
        assert sample.library == "VITesterUtilities.lvlib"
        # A plain .lvlib utility VI is not a class method.
        assert sample.class_fact is None

    def test_library_membership_does_not_perturb_class_ownership(
        self,
        jki_index: BuildResult,
    ):
        """Regression guard: adding the ``.lvlib`` ownership pass alongside
        the existing ``.lvclass`` ownership pass must not change ANY class
        method's ``owning_class``/``library`` resolution — the two "owns"
        edge scans (``get_owning_class`` / ``get_owning_library``) are gated
        on disjoint ``node_type``s, so a class-owned VI must never also
        resolve as a library member.
        """
        class_methods = [f for f in jki_index.facts if f.class_fact is not None]
        assert class_methods  # JKI-VI-Tester is a class-heavy corpus

        for f in class_methods:
            class_fact = f.class_fact
            assert class_fact is not None
            assert f.library == class_fact.owning_class
            assert class_fact.owning_class.endswith(".lvclass")

        # No overlap: a VITesterUtilities.lvlib member is never ALSO a class
        # method (confirmed against the real corpus -- these are disjoint
        # VI sets).
        lib_members = {
            f.path for f in jki_index.facts if f.library == "VITesterUtilities.lvlib"
        }
        class_method_paths = {f.path for f in class_methods}
        assert not (lib_members & class_method_paths)

    def test_lvclass_library_member_gets_qualified_ownership(
        self,
        jki_index: BuildResult,
    ):
        """Empirical check for the Bug-B interaction: MyLibrary.lvlib's one
        member is ``ABC - Parentheses (Valid).lvclass`` (a ``Type="LVClass"``
        item, previously dropped entirely — see ``TestLoadLvlibClassMember``
        above). It ALSO sits on disk under ``project_root`` and so is ALSO
        picked up directly, bare, by ``_load_class_ownership``'s
        ``rglob("*.lvclass")`` — plus ``_load_library_ownership``'s OWN
        independent top-level visit to ``MyLibrary.lvlib`` (it never knows
        that lvlib is nested inside ``MyParentLibrary.lvlib``). Three
        ``_dep_graph`` "class" nodes for the one physical class result, but
        only the correctly-chained one (reached via ``MyParentLibrary.lvlib``)
        has "owns" edges that resolve to the REAL method VI nodes — the other
        two are inert orphans whose guessed target qnames match no loaded VI,
        so they never perturb ``get_owning_class`` (see
        ``TestLoadLvlibClassMember`` for the direct graph-level check).

        ALL FOUR methods are asserted here, including ``setUp.vi``/
        ``tearDown.vi``: those are common JKI TestCase method names (16-17
        same-named files elsewhere in this corpus) that hit ``build_index``'s
        path-keyed COLLISION path (``build_one_vi``) for THIS specific file.
        ``build_one_vi`` (``index/build.py``) now derives the owner chain
        from the loaded VI's OWN fully-qualified ``vi_name``
        (``_owner_chain_for_class``) and passes it to ``load_lvclass``, so
        the collision path registers the class under the SAME qualified
        qname its method VIs embed — matching what
        ``_load_class_ownership``/``_load_library_ownership`` already do for
        the whole-directory pass. Previously this was a narrow, documented
        limitation (bare ``load_lvclass`` call, no ``owner_chain``) that left
        ``class_fact`` ``None`` for these two collision-routed methods.
        """
        assert _MYLIBRARY_LVLIB.is_file()  # sanity: fixture lvlib exists

        method_facts = [
            f
            for f in jki_index.facts
            if f.class_fact is not None
            and f.class_fact.owning_class == _MYLIBRARY_CLASS_NAME
        ]
        found = {Path(f.path).name for f in method_facts}
        assert found == _MYLIBRARY_CLASS_METHODS

        for f in method_facts:
            class_fact = f.class_fact
            assert class_fact is not None
            assert class_fact.owning_class == _MYLIBRARY_CLASS_NAME
            assert f.library == _MYLIBRARY_CLASS_NAME


# === .lvproj membership (#19) ===============================================

# Own (is_dependency=False) VI-member count per .lvproj, keyed by path RELATIVE
# to JKI_ROOT (two projects share the stem "Test Project", so name is NOT a
# unique key — the same collision the path-keyed index exists to disentangle).
# Observed by parsing each .lvproj's declared member list, extension-classified
# (a .ctl tagged Type="VI" is a Control, not a VI). The four projects whose
# members use relative/in-repo URLs reproduce the eval bank's ground-truth VI
# counts exactly (VIUnit 83, Project Integration 49, Example 9, Test Project 4).
# The two whose members point above the checkout carry their real
# project-proper counts (JUnitXML 1 VI + 2 classes; VI Tester Plugin's Test
# Project 4 VI + 4 classes), not the eval doc's transcribed 2/12.
_EXPECTED_OWN_VI: dict[str, int] = {
    "source/VIUnit.lvproj": 83,
    "source/LabVIEW Project Plugin/VI Tester Project Integration.lvproj": 49,
    "source/Examples/VI Tester Example.lvproj": 9,
    "source/Prototype/Test Project/Test Project.lvproj": 4,
    "source/Prototype/VI Tester Plugin/Test Project.lvproj": 4,
    "source/Ant Plugin/Source/VI Tester JUnitXML.lvproj": 1,
}


def _rel_lvproj(m_lvproj_path: str) -> str:
    return str(Path(m_lvproj_path).relative_to(JKI_ROOT.resolve()))


@pytest.mark.skipif(not _HAVE_JKI, reason="JKI-VI-Tester sample not present")
class TestLvprojMembership:
    """``build_lvproj_membership`` (#19) records every ``.lvproj`` member as a
    project-level, many-to-many fact — resolved-or-not, in-repo-or-not,
    own-content-or-dependency — over the real JKI VI Tester corpus."""

    def test_six_projects_with_expected_own_vi_counts(self):
        members = build_lvproj_membership(JKI_ROOT)

        # Six .lvproj files, but only FIVE distinct stems (two 'Test Project').
        proj_paths = {m.lvproj_path for m in members}
        assert len(proj_paths) == 6
        assert len(list(JKI_ROOT.rglob("*.lvproj"))) == 6
        assert {_rel_lvproj(p) for p in proj_paths} == set(_EXPECTED_OWN_VI)
        assert len({m.lvproj_name for m in members}) == 5  # stem collision

        # Own (non-dependency) VI members per project match the observed counts.
        own_vi: Counter[str] = Counter(
            _rel_lvproj(m.lvproj_path)
            for m in members
            if m.member_type == "VI" and not m.is_dependency
        )
        assert dict(own_vi) == _EXPECTED_OWN_VI

    def test_known_member_and_dependency_split(self):
        members = build_lvproj_membership(JKI_ROOT)
        viunit = [m for m in members if m.lvproj_name == "VIUnit"]

        # A known own member of VIUnit.lvproj.
        assert any(
            m.member_name == "TestCase.lvclass"
            and m.member_type == "LVClass"
            and not m.is_dependency
            for m in viunit
        )

        # VIUnit pulls in a large auto-collected Dependencies group, and every
        # dependency there is an external (vi.lib/userlib) ref — none resolve
        # in-repo — which is exactly the is_dependency vs is_in_repo split.
        deps = [m for m in viunit if m.is_dependency]
        assert len(deps) > 100
        assert all(not m.is_in_repo for m in deps)
        assert all(m.resolved_path is None for m in deps)

        # Targets come through: every member sits under a build target.
        assert all(m.target for m in viunit)
        assert "My Computer" in {m.target for m in viunit}

    def test_is_in_repo_distinguishes_resolved_members(self):
        members = build_lvproj_membership(JKI_ROOT)

        # Only a handful of projects have members whose URLs resolve in-repo
        # (Example + the Prototype Test Project); most .lvproj URLs encode a
        # layout above the checkout. Every in-repo member must have a real
        # resolved path on disk under the root.
        in_repo = [m for m in members if m.is_in_repo]
        assert in_repo  # some members DO resolve in-repo
        for m in in_repo:
            assert m.resolved_path is not None
            assert Path(m.resolved_path).exists()
            assert not m.is_dependency  # dependencies are all external here

        # VI Tester Example's 9 own VIs all live in-repo (sibling files).
        example_own = [
            m
            for m in members
            if m.lvproj_name == "VI Tester Example"
            and m.member_type == "VI"
            and not m.is_dependency
        ]
        assert len(example_own) == 9
        assert all(m.is_in_repo for m in example_own)

    def test_store_round_trip(self):
        # The autouse ``_hermetic_cache`` fixture (tests/conftest.py) points
        # LVKIT_CACHE_DIR at a per-test tmp dir, so the DB this writes is
        # isolated from the developer's real index.
        members = build_lvproj_membership(JKI_ROOT)

        save_lvproj_members(JKI_ROOT, members)
        reloaded = load_lvproj_members(JKI_ROOT)

        assert len(reloaded) == len(members)
        assert {_rel_lvproj(m.lvproj_path) for m in reloaded} == set(_EXPECTED_OWN_VI)
        sample = next(m for m in reloaded if not m.is_dependency)
        assert sample.target
        assert sample.member_type in {"VI", "Control", "LVClass", "Library"}

        # Wholesale replace: saving again doesn't duplicate rows.
        save_lvproj_members(JKI_ROOT, members)
        assert len(load_lvproj_members(JKI_ROOT)) == len(members)
