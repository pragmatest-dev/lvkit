"""Tests for the authoritative class-parent decode from
``NI.LVClass.ParentClassLinkInfo`` (structure.py::_parent_from_link_info),
which replaced the old ``_Init.vi``-sniffing heuristic
(``_find_parent_class_by_path``).

Uses the real JKI-VI-Tester corpus sample; skipped when it isn't present on
disk (consistent with other sample-backed tests in this repo, e.g.
test_docs_class_hierarchy.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VINode
from lvkit.load_mode import LoadMode
from lvkit.structure import parse_lvclass

SAMPLE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")


def _has_sample() -> bool:
    return SAMPLE_ROOT.exists()


pytestmark = pytest.mark.skipif(
    not _has_sample(), reason="JKI-VI-Tester sample not present"
)


def test_in_repo_parent_resolves_by_name_no_vilib() -> None:
    """TextTestRunner's parent (TestRunner) lives in-repo under
    Classes/TestRunner/ -- the decoded ParentClassLinkInfo path is a plain
    relative path with no ``<vilib>`` marker."""
    lvclass_path = SAMPLE_ROOT / "Classes" / "TextTestRunner" / "TextTestRunner.lvclass"
    cls = parse_lvclass(lvclass_path)

    assert cls.parent_class == "TestRunner"
    assert cls.is_vilib_parent is False


def test_vilib_parent_flagged() -> None:
    """TextTestRunner.JUnitXML's parent (TextTestRunner) is resolved via a
    vi.lib-installed copy -- the decoded path contains the literal
    ``<vilib>`` marker, so is_vilib_parent must be True."""
    lvclass_path = (
        SAMPLE_ROOT
        / "Ant Plugin"
        / "Source"
        / "TextTestRunner.Ant"
        / "TextTestRunner.JUnitXML.lvclass"
    )
    cls = parse_lvclass(lvclass_path)

    assert cls.parent_class == "TextTestRunner"
    assert cls.is_vilib_parent is True


def test_vilib_parent_link_path_carries_the_recorded_path_tokens() -> None:
    """Issue #84: the binary ``ParentClassLinkInfo``'s own ``PTH0`` record
    carries the parent's RECORDED PATH, not just its bare name -- dropped
    entirely before this fix. TextTestRunner.JUnitXML's parent link decodes
    to the vi.lib-rooted token list ending in the parent's own filename, in
    the SAME shape ``parser.models.ParsedDependencyRef.path_tokens`` already
    uses everywhere else (so a loader resolves it with the exact same
    ``resolve_against`` machinery a SubVI/type ``<vilib>`` dependency does)."""
    lvclass_path = (
        SAMPLE_ROOT
        / "Ant Plugin"
        / "Source"
        / "TextTestRunner.Ant"
        / "TextTestRunner.JUnitXML.lvclass"
    )
    cls = parse_lvclass(lvclass_path)

    assert cls.parent_link_path is not None
    assert cls.parent_link_path[0] == "<vilib>"
    assert cls.parent_link_path[-1] == "TextTestRunner.lvclass"


def test_root_class_has_no_parent() -> None:
    """TestCase is a root class: it carries no ParentClassLinkInfo property
    at all, so parent_class must be None (not a guessed/heuristic value)."""
    lvclass_path = SAMPLE_ROOT / "Classes" / "TestCase" / "TestCase.lvclass"
    cls = parse_lvclass(lvclass_path)

    assert cls.parent_class is None
    assert cls.is_vilib_parent is False


def test_wait_on_test_complete_resolves_single_parent() -> None:
    """Regression guard for the downstream index bug: WaitOnTestComplete
    must resolve to exactly TestCase, with no ambiguity."""
    lvclass_path = (
        SAMPLE_ROOT / "Tests" / "WaitOnTestComplete" / "WaitOnTestComplete.lvclass"
    )
    cls = parse_lvclass(lvclass_path)

    assert cls.parent_class == "TestCase"
    assert cls.is_vilib_parent is False


def _call_keys(vi_rel: str) -> dict[str, str | None]:
    """``{callee file name: callee_key}`` of every subVI call of ``vi_rel``."""
    vi_path = (SAMPLE_ROOT / vi_rel).resolve()
    graph = InMemoryVIGraph()
    key = graph.load_vi(vi_path, LoadMode.MINIMAL, search_paths=[SAMPLE_ROOT.resolve()])
    assert key is not None
    return {
        Path(n.qualified_name.rsplit(":", 1)[-1]).name: n.callee_key
        for n in graph.iter_nodes(key)
        if isinstance(n, VINode) and n.id != n.vi_path and n.qualified_name
    }


def test_in_repo_parent_is_found_by_its_recorded_path() -> None:
    """MyFirstTest.lvclass records its parent as three ``..`` hops then
    ``Classes/TestCase/TestCase.lvclass``: a sibling subtree no up-tree walk
    reaches."""
    graph = InMemoryVIGraph()
    graph.load_vi(
        (SAMPLE_ROOT / "Tests/MyFirstTest/testDemo.vi").resolve(),
        LoadMode.MINIMAL,
        search_paths=[SAMPLE_ROOT.resolve()],
    )
    cls_key = str((SAMPLE_ROOT / "Tests/MyFirstTest/MyFirstTest.lvclass").resolve())
    parent_key = str((SAMPLE_ROOT / "Classes/TestCase/TestCase.lvclass").resolve())
    assert graph.class_parent_key(cls_key) == parent_key


def test_a_method_called_through_a_subclass_names_the_ancestors_file() -> None:
    """``MyFirstTest.lvclass:failUnlessEqual.vi`` is recorded under the wire's
    class; the file is TestCase's."""
    keys = _call_keys("Tests/MyFirstTest/testDemo.vi")
    expected = (SAMPLE_ROOT / "Classes/TestCase/failUnlessEqual.vi").resolve()
    assert keys["failUnlessEqual.vi"] == str(expected)


def test_a_method_a_class_declares_itself_keeps_its_own_file() -> None:
    """Neighbouring case: TestCase's own methods are not redirected."""
    keys = _call_keys("Classes/TestCase/run.vi")
    own = [k for n, k in keys.items() if k and "/Classes/TestCase/" in k]
    assert own
