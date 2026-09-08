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
