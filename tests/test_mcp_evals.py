"""Correctness regression harness for the lvkit MCP evals question bank.

Pins the *assertable* questions from ``docs/_internal/mcp-evals.md`` against
the real JKI VI Tester corpus, so a regression in index building or the SQL
view layer turns a green question RED instead of silently drifting the eval
bank out of sync with reality. Open-ended questions (magic numbers, hardcoded
creds, naming consistency, "what's the public API") aren't assertable this
way — they're graded by the ``eval-judge`` skill instead (see the
``lvkit-eval`` skill for the full loop).

Pattern follows ``tests/test_index.py``'s ``TestFullCorpusDemo``: ``JKI_ROOT``
/ ``_HAVE_JKI`` guard, module-scoped ``jki_index`` fixture (build once,
reusing the developer's warm extraction cache), ``@pytest.mark.slow``.

RUN SERIALLY: ``uv run pytest tests/test_mcp_evals.py -m slow -n0``.
The whole module shares ONE on-disk index (the fixture ``save``s to the real
cache so ``_query`` has a real DB to hit), so the default ``-n auto`` xdist
workers race the same SQLite file -> ``OperationalError`` + partial-read
assertion flakes. ``-n0`` overrides ``-n auto`` to run serially (``-p no:xdist``
does NOT work -- it drops the plugin, leaving ``-n auto`` unrecognized).

Every assertion below pins the value actually OBSERVED against the corpus
(computed and printed while developing this file, then hardcoded) — not a
hoped-for value. A baseline going RED is a real regression. The two
``xfail``-marked tests are known gaps (#18, #19 in the eval bank): they FAIL
today by design, and flipping to XPASS is the signal that the fix landed and
the eval bank + this test need updating.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lvkit.index import sql as isql
from lvkit.index.build import BuildResult, build_index
from lvkit.index.project import resolve_project
from lvkit.index.sql import QueryResult
from lvkit.index.store import save as save_index
from lvkit.index.store import save_lvproj_members
from lvkit.structure import parse_lvclass

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
JKI_ROOT = SAMPLES / "JKI-VI-Tester"

_HAVE_JKI = JKI_ROOT.is_dir() and any(JKI_ROOT.rglob("*.vi"))


@pytest.fixture(scope="module")
def jki_index() -> BuildResult:
    """Build + persist the full JKI-VI-Tester index once, reusing the
    developer's warm extraction cache (see ``test_index.jki_index`` — same
    rationale: a cold 487-VI MINIMAL build is O(minutes), and this fixture
    also ``save``s the built facts so ``_query`` below has a real on-disk
    index to run SQL against, exactly like a ``lvkit index`` + ``query`` run
    would)."""
    if not _HAVE_JKI:
        pytest.skip("JKI-VI-Tester sample not present")

    saved = os.environ.pop("LVKIT_CACHE_DIR", None)
    try:
        root, vi_paths = resolve_project(JKI_ROOT)
        result = build_index(root, vi_paths)
        save_index(root, result.facts)
        # Persist .lvproj membership too, so the `lvproj` view has rows to
        # answer #19's membership questions (mirrors what `lvkit index` /
        # the MCP index tool do after a build).
        save_lvproj_members(root, result.lvproj_members)
        return result
    finally:
        if saved is not None:
            os.environ["LVKIT_CACHE_DIR"] = saved


def _query(sql: str) -> QueryResult:
    """Run ``sql`` against the persisted JKI index (see ``jki_index``).

    The per-test ``_hermetic_cache`` autouse fixture (``tests/conftest.py``)
    points ``LVKIT_CACHE_DIR`` at a fresh per-test tmp dir so no test writes
    to the real cache — which would also hide the on-disk index ``jki_index``
    just saved there. So, like that fixture does for the build, unset it for
    the duration of the query.
    """
    saved = os.environ.pop("LVKIT_CACHE_DIR", None)
    try:
        root, _ = resolve_project(JKI_ROOT)
        return isql.run_query(root, sql)
    finally:
        if saved is not None:
            os.environ["LVKIT_CACHE_DIR"] = saved


# === A. Class & library structure ===========================================


@pytest.mark.slow
class TestQ1ClassHierarchy:
    """Q1: 'What classes are in this project and how do they inherit?'"""

    def test_distinct_owning_class_count(self, jki_index: BuildResult):
        owning = {
            f.class_fact.owning_class
            for f in jki_index.facts
            if f.class_fact is not None
        }
        # 27 + the 4 classes #18 recovered (Class1, MySecondTestCase,
        # "Merge Errors TestCase", "Queue TestCase"). UserInterfaceTestCase
        # (zero methods) stays unresolved — #19, out of scope here.
        assert len(owning) == 31

    def test_testcase_direct_subclass_count(self, jki_index: BuildResult):
        """Q1/Q5: 'How many classes inherit from TestCase?' A definitive
        structural answer — pin it EXACT, no range. Direct subclasses are the
        distinct owning_classes whose parent is ``TestCase.lvclass``; none of
        the 14 have children of their own, so the transitive answer is also 14.
        """
        subclasses = {
            f.class_fact.owning_class
            for f in jki_index.facts
            if f.class_fact is not None
            and f.class_fact.parent == "TestCase.lvclass"
        }
        assert len(subclasses) == 14
        # No grandchildren: nothing lists one of the 14 as its parent.
        grandchildren = {
            f.class_fact.owning_class
            for f in jki_index.facts
            if f.class_fact is not None and f.class_fact.parent in subclasses
        }
        assert grandchildren == set()

    def test_no_owning_class_has_two_distinct_parents(self, jki_index: BuildResult):
        """The de-duplication invariant: every class_fact row resolved for
        the same owning_class must agree on its parent — a class can't
        appear to have two different parents depending on which method VI
        you ask."""
        parents_by_class: dict[str, set[str | None]] = {}
        for f in jki_index.facts:
            if f.class_fact is None:
                continue
            parents_by_class.setdefault(f.class_fact.owning_class, set()).add(
                f.class_fact.parent
            )
        multi = {k: v for k, v in parents_by_class.items() if len(v) > 1}
        assert multi == {}

    def test_wait_on_test_complete_vi_not_collapsed(self, jki_index: BuildResult):
        """Path-keyed collision guard (distinct from the class-parent bug
        below): the corpus has TWO same-named ``WaitOnTestComplete.vi`` files —
        TestCase's own protected method and TestSuite's own protected method.
        Path-keyed indexing must resolve each to ITS OWN owning class, never
        collapse them, never leave either unresolved."""
        wotc = [f for f in jki_index.facts if f.name == "WaitOnTestComplete.vi"]
        assert len(wotc) == 2
        assert all(f.class_fact is not None for f in wotc)  # zero unresolved
        owning = {f.class_fact.owning_class for f in wotc if f.class_fact}
        assert owning == {"TestCase.lvclass", "TestSuite.lvclass"}
        assert all(f.class_fact.scope == "protected" for f in wotc if f.class_fact)

    def test_wait_on_test_complete_class_single_parent(self, jki_index: BuildResult):
        """The ACTUAL #15 regression the user reported: the
        ``WaitOnTestComplete.lvclass`` CLASS (a TestCase subclass under
        source/Tests/) showed BOTH a NULL and a TestCase parent, because its
        collision-routed methods (CleanUp/setUp/tearDown) loaded the class
        without its parent and get_class_hierarchy gated on that. Every method
        of the class must now resolve a single ``TestCase.lvclass`` parent,
        no NULL."""
        methods = [
            f
            for f in jki_index.facts
            if f.class_fact
            and f.class_fact.owning_class == "WaitOnTestComplete.lvclass"
        ]
        assert methods  # the class resolves at all
        parents = {f.class_fact.parent for f in methods if f.class_fact}
        assert parents == {"TestCase.lvclass"}  # single value, no None


@pytest.mark.slow
def test_q3_testcase_private_methods(jki_index: BuildResult):
    """Q3: 'What are the private methods of TestCase.lvclass?' A definitive
    enumerable set — pin it EXACT. scope='private' catches all four regardless
    of folder (two sit directly under the class dir, one under private/, and
    testMethod.vi at the class root)."""
    res = _query(
        "SELECT vi_path FROM class_fact "
        "WHERE owning_class='TestCase.lvclass' AND scope='private'"
    )
    methods = {str(row[0]).rsplit("/", 1)[-1] for row in res.rows}
    assert methods == {
        "closeMethodViReference.vi",
        "openMethodViReference.vi",
        "CallTestMethod.vi",
        "testMethod.vi",
    }


@pytest.mark.slow
def test_q4_accessor_field_map(jki_index: BuildResult):
    """Q4: 'Which class fields have accessors, and which field does each
    read/write?' A definitive enumerable map — pin the EXACT set of
    (owning_class, field) pairs. 18 accessors across 9 classes; every field has
    exactly one accessor VI (no read/write pair splits the field into two rows).
    """
    res = _query(
        "SELECT owning_class, accessor_field FROM class_fact WHERE is_accessor=1"
    )
    pairs = {(row[0], row[1]) for row in res.rows}
    assert pairs == {
        ("Class1.lvclass", "Queue"),
        ("FrameworkSubTestSuite.lvclass", "Special"),
        ("TestCase.lvclass", "CustomReportText"),
        ("TestCase.lvclass", "SkipMessage"),
        ("TestLoader.lvclass", "TestsFromTestCase"),
        ("TestLoader.lvclass", "TestsFromTestCaseByClassPath"),
        ("TestLoader.lvclass", "TestsFromTestCaseObject"),
        ("TestResult.lvclass", "ShouldStop"),
        ("TestResult.lvclass", "Test Skipped Message"),
        ("TestRunner.lvclass", "PublicEvents"),
        ("TestRunner.lvclass", "StartTime"),
        ("TestRunner.lvclass", "StopTime"),
        ("TestRunner.lvclass", "TestTimingInfo"),
        ("TestSuite.lvclass", "SkipMessage"),
        ("TextTestRunner.lvclass", "descriptions"),
        ("TextTestRunner.lvclass", "stream"),
        ("TextTestRunner.lvclass", "verbosity"),
        ("_TextTestResult.lvclass", "Description"),
    }
    # Each (class, field) pair is unique — no field double-counted.
    assert len(res.rows) == 18


@pytest.mark.skipif(not _HAVE_JKI, reason="JKI-VI-Tester sample not present")
class TestQ2VilibVsInRepoParent:
    """Q2: 'Which classes inherit from a vi.lib class vs an in-repo class?'

    Reads ``parse_lvclass`` directly (no index build needed) — same clean-
    room source ``structure.parse_lvclass`` uses for ``is_vilib_parent``.
    """

    def test_junitxml_runner_points_at_vilib(self):
        path = (
            JKI_ROOT
            / "source"
            / "Ant Plugin"
            / "Source"
            / "TextTestRunner.Ant"
            / "TextTestRunner.JUnitXML.lvclass"
        )
        lv = parse_lvclass(path)
        assert lv.is_vilib_parent is True
        assert lv.parent_class == "TextTestRunner"

    def test_texttestrunner_points_in_repo(self):
        path = (
            JKI_ROOT
            / "source"
            / "Classes"
            / "TextTestRunner"
            / "TextTestRunner.lvclass"
        )
        lv = parse_lvclass(path)
        assert lv.is_vilib_parent is False
        assert lv.parent_class == "TestRunner"

    def test_testcase_is_root_no_parent(self):
        path = JKI_ROOT / "source" / "Classes" / "TestCase" / "TestCase.lvclass"
        lv = parse_lvclass(path)
        assert lv.is_vilib_parent is False
        assert lv.parent_class is None


# === C. Error handling =======================================================


@pytest.mark.slow
def test_q10_error_indicator_histogram_top_row(jki_index: BuildResult):
    """Q10: 'What names does this project use for error indicators, and how
    often?' — 'error out' dominates; pin its current count as baseline."""
    res = _query(
        "SELECT name, COUNT(*) n FROM terminal "
        "WHERE type_descriptor='Error' AND direction='output' "
        "GROUP BY name ORDER BY n DESC, name"
    )
    # Baseline count, re-pinned when it drifts (the answer -- "error out"
    # dominates -- is the eval; the number is a regression tripwire). Was 352;
    # 382 as of this branch (cumulative parser terminal-extraction improvements,
    # not a projection change). Distinct from a doubling bug -- other count
    # pins (q22/q26 + the path-keyed collision counts) held, so the index
    # isn't double-counting.
    assert res.rows[0] == ["error out", 382]


@pytest.mark.slow
def test_q8_error_cluster_input_vis(jki_index: BuildResult):
    """Q8: 'Which VIs take an error cluster as an input?' A definitive
    structural count — pin it EXACT. Error clusters are identified by their
    type (``type_descriptor='Error'``), NOT by terminal name, so this catches
    the ``error in``/fallback-labelled inputs a name-grep would miss."""
    res = _query(
        "SELECT COUNT(DISTINCT vi_path) FROM terminal "
        "WHERE type_descriptor='Error' AND direction='input'"
    )
    assert res.rows == [[395]]


@pytest.mark.slow
def test_q9_no_input_vis(jki_index: BuildResult):
    """Q9: 'Which VIs have no inputs (entry points / top-level runners)?'
    Definitive — a VI whose path never appears as an input-terminal owner."""
    res = _query(
        "SELECT COUNT(*) FROM vi WHERE path NOT IN "
        "(SELECT DISTINCT vi_path FROM terminal WHERE direction='input')"
    )
    assert res.rows == [[30]]


@pytest.mark.slow
def test_q11_no_error_out_vis(jki_index: BuildResult):
    """Q11: 'Which VIs have NO error out terminal?' Definitive — the
    complement of the 382 VIs that carry exactly one ``error out`` output
    (see :func:`test_q10_error_indicator_histogram_top_row`): 487 - 382 = 105.
    """
    res = _query(
        "SELECT COUNT(*) FROM vi WHERE path NOT IN "
        "(SELECT DISTINCT vi_path FROM terminal "
        "WHERE type_descriptor='Error' AND direction='output' "
        "AND name='error out')"
    )
    assert res.rows == [[105]]


# === F. Project scoping =======================================================


@pytest.mark.skipif(not _HAVE_JKI, reason="JKI-VI-Tester sample not present")
def test_q20_lvproj_count():
    """Q20: 'How many LabVIEW projects (.lvproj) are in this repo?'

    Filesystem fact — the repository-vs-project baseline the eval's 'Watch
    for' warns not to conflate with 'one project'."""
    assert len(list(JKI_ROOT.rglob("*.lvproj"))) == 6


# === E. Impact / dead code ===================================================


def test_q18_dead_code_column_is_modeled():
    """Q18/#20 (fast half): the ``vi`` view exposes ``callers_count`` so
    "VIs nothing calls" is a ``callers_count = 0`` filter, not a fragile
    ``qualified_name``/``callee_key`` name anti-join."""
    assert "callers_count" in isql.VIEWS["vi"].columns


@pytest.mark.slow
def test_q18_dead_code_uncalled(jki_index: BuildResult):
    """Q18: 'Is anything dead code — VIs that nothing calls?'

    The fixed answer is ``vi.callers_count = 0`` (#20) — the in-degree of the
    call graph, whose edges are now the ``kind='vi'`` node spine: each SubVI-call
    node's ``callee_path``, resolved once at merge time through the same three
    tiers (``by_path`` → ``by_qualified`` → leaf-name; see
    ``query._resolve_callee``). Keyed on VI path, so it classifies even the many
    VIs whose ``qualified_name`` is NULL. 234 uncalled of 487, incl. the
    JUnitXML example runner; a common init subVI (``TestCase_Init.vi``) is NOT
    uncalled. Keyed on VI path, so dynamic-dispatch overrides and
    Call-By-Reference targets read as statically uncalled — order-invariant.
    (Ground truth: ``docs/_internal/mcp-evals.md`` Q18, confirmed 2026-08-17.)"""
    total = _query("SELECT COUNT(*) FROM vi WHERE callers_count = 0")
    assert total.rows == [[234]]

    called = _query("SELECT callers_count FROM vi WHERE name = 'TestCase_Init.vi'")
    assert called.rows == [[14]]
    uncalled = _query(
        "SELECT callers_count FROM vi WHERE name = 'VI Tester JUnitXML Example.vi'"
    )
    assert uncalled.rows == [[0]]


# === G. Consistency / integrity ==============================================


@pytest.mark.slow
def test_q22_stub_count(jki_index: BuildResult):
    """Q22: 'Which VIs couldn't be loaded (protected, missing deps, stubs)?'

    Pin the current stub count as baseline — 0 in this corpus today."""
    res = _query("SELECT COUNT(*) FROM vi WHERE is_stub=1")
    assert res.rows == [[0]]


@pytest.mark.slow
def test_pathkeyed_name_collisions_not_double_counted(jki_index: BuildResult):
    """Path-keyed indexing must count same-named VIs correctly — neither
    collapsing distinct files that share a filename nor double-counting them.
    CleanUp/setUp/tearDown recur across the TestCase subclasses; these exact
    counts are the anti-double-count tripwire (cited by
    :func:`test_q10_error_indicator_histogram_top_row`). (The old 'same-named
    VIs that could be confused?' eval question was cut — LabVIEW namespaces by
    library, so the copies are distinct files, not a confusion risk — but this
    structural invariant is worth keeping.)"""
    res = _query(
        "SELECT name, COUNT(*) n FROM vi GROUP BY name HAVING COUNT(*)>1 "
        "ORDER BY n DESC, name"
    )
    names = {row[0] for row in res.rows}
    assert {"CleanUp.vi", "setUp.vi", "tearDown.vi"} <= names
    top3 = {row[0]: row[1] for row in res.rows[:3]}
    assert top3 == {"CleanUp.vi": 17, "setUp.vi": 17, "tearDown.vi": 16}


# === H. Type faithfulness (validates the #7 faithful-LVType sweep) =========


@pytest.mark.slow
def test_q25_enum_interface_members_are_queryable(jki_index: BuildResult):
    """Q25: 'What are the possible values of the `method` enum input to
    CallTestMethod.vi?'

    Answerable straight from the index — the exact ``type_descriptor`` carries
    the members and ``enum_values`` is the ordinal-ordered JSON array. Before
    the #7 faithful-type sweep every surface projected the enum through
    ``python_type()`` to ``int``, so the members were unreadable and an agent
    could only INFER them (the demonstrated MCP miss)."""
    res = _query(
        "SELECT type_descriptor, enum_values FROM terminal "
        "WHERE vi_path LIKE '%CallTestMethod.vi' AND name='method'"
    )
    assert len(res.rows) == 1
    type_descriptor, enum_values = res.rows[0]
    assert type_descriptor == "method--Enum{setUp, testMethod, tearDown}"
    for member in ("setUp", "testMethod", "tearDown"):
        assert f'"{member}"' in str(enum_values)


@pytest.mark.slow
def test_q26_interface_types_are_faithful_not_python(jki_index: BuildResult):
    """Q26 (meta guard for the faithful-types LAW): the answer column
    ``type_descriptor`` never carries a Python annotation for a known type. The
    ``method`` enum is not ``int``; and across the whole corpus no terminal's
    ``type_descriptor`` is a Python projection token (``int``/``float``/
    ``dict[str, Any]``/…). An unresolved type is the empty string ``''`` (with
    ``type_kind`` still naming the family), never ``Any`` or a codegen token."""
    enum_type = _query(
        "SELECT type_descriptor FROM terminal "
        "WHERE vi_path LIKE '%CallTestMethod.vi' AND name='method'"
    ).rows[0][0]
    assert enum_type != "int"
    assert "{" in str(enum_type)  # carries its members

    # No terminal leaks a Python annotation into the faithful column — not the
    # codegen tokens, and not 'Any'. Unresolved types are '' (see type_kind).
    leaked = _query(
        "SELECT COUNT(*) FROM terminal WHERE type_descriptor IN "
        "('int','float','bool','str','dict[str, Any]','list[float]',"
        "'list[int]','Any')"
    )
    assert leaked.rows == [[0]]


# === Known gaps — xfail today; XPASS is the "update the eval" signal =======


@pytest.mark.slow
def test_q21_gap18_four_classes_resolve_class_fact(jki_index: BuildResult):
    """Q21/#18 (also underlies Q1's owning-class count): Class1,
    MySecondTestCase, "Merge Errors TestCase", and "Queue TestCase" each had
    every method VI's ``class_fact`` unresolved (None) because the method
    VI's own embedded metadata never reports a ``qualified_name`` — so
    ``load_lvclass`` registered them under their bare filename, and the
    computed ``cls_qname:filename`` ownership key never matched a node. Fixed
    by falling back to source-path identity for the owns-edge (see
    ``graph/loading.py::load_lvclass``). These 4 now resolve; each also gets
    its parent from ``ParentClassLinkInfo``."""
    gap_classes: dict[str, str | None] = {
        "Class1.lvclass": None,
        "MySecondTestCase.lvclass": "TestCase.lvclass",
        "Merge Errors TestCase.lvclass": "TestCase.lvclass",
        "Queue TestCase.lvclass": "TestCase.lvclass",
    }
    by_class: dict[str, set[str | None]] = {}
    for f in jki_index.facts:
        if f.class_fact is not None and f.class_fact.owning_class in gap_classes:
            by_class.setdefault(f.class_fact.owning_class, set()).add(
                f.class_fact.parent
            )
    assert set(gap_classes) <= set(by_class)
    for owning_class, expected_parent in gap_classes.items():
        assert by_class[owning_class] == {expected_parent}


@pytest.mark.slow
@pytest.mark.xfail(
    reason=(
        "GAP #19: UserInterfaceTestCase has ZERO method VIs, so there is no "
        "method VI for load_lvclass's owns-edge loop to ever visit — the "
        "class-level index gap is distinct from #18's owns-edge bug (fixed "
        "above) and needs a class-with-no-methods fact of its own."
    ),
)
def test_q21_gap19_zero_method_class_resolves(jki_index: BuildResult):
    """Q21/#19: UserInterfaceTestCase (zero methods) should still surface as
    a known class once #19 lands."""
    resolved = {
        f.class_fact.owning_class for f in jki_index.facts if f.class_fact is not None
    }
    assert "UserInterfaceTestCase.lvclass" in resolved


def test_q20_19_lvproj_membership_is_modeled():
    """Q20/#19 (membership half, LANDED): the SQL view layer models .lvproj
    membership — the `lvproj` view exists and its columns name it."""
    assert "lvproj" in isql.VIEWS
    cols = isql.VIEWS["lvproj"].columns
    assert {"lvproj_name", "member_name", "member_type", "is_in_repo"} <= set(cols)


@pytest.mark.slow
def test_q20_19_lvproj_view_returns_six_projects(jki_index: BuildResult):
    """Q20/#19: the `lvproj` view answers "which LabVIEW projects are here?" —
    the 6 distinct `.lvproj` in the repo — and a join answers "classes in
    VIUnit.lvproj".

    The count is over `lvproj_path`, not `lvproj_name`: two of the six share
    the stem 'Test Project' (5 distinct names), the same name collision the
    path-keyed index exists to disentangle."""
    res = _query("SELECT COUNT(DISTINCT lvproj_path) FROM lvproj")
    assert res.rows == [[6]]
    names = {row[0] for row in _query("SELECT DISTINCT lvproj_name FROM lvproj").rows}
    assert len(names) == 5  # 'Test Project' stem occurs twice
    assert "VIUnit" in names

    # A join answering "classes in VIUnit.lvproj" returns its own classes.
    classes = _query(
        "SELECT member_name FROM lvproj "
        "WHERE lvproj_name='VIUnit' AND member_type='LVClass' "
        "AND is_dependency=0 ORDER BY member_name"
    )
    class_names = {row[0] for row in classes.rows}
    assert "TestCase.lvclass" in class_names
    assert len(class_names) >= 20  # VIUnit declares ~21 own classes

    # is_in_repo separates resolved-in-repo members from vi.lib dependency refs.
    dep = _query(
        "SELECT COUNT(*) FROM lvproj "
        "WHERE lvproj_name='VIUnit' AND is_dependency=1 AND is_in_repo=1"
    )
    assert dep.rows == [[0]]  # every VIUnit dependency is external (not in-repo)
