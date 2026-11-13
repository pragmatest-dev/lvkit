"""Corpus VIs that scan text with Scan From String, generated through the real
pipeline: the generated function is imported and run, and what it returns is
asserted."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from tests.corpus_graphs import OPENG_USERLIB
from tests.test_catalog_corpus_callers import SAMPLES, _generate

pytestmark = [pytest.mark.functional, pytest.mark.needs_samples]


def _module(vi: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str):
    package = _generate(vi, tmp_path)
    monkeypatch.syspath_prepend(str(package.parent))
    return package, importlib.import_module(f"{package.name}.{name}")


def _forget(package: Path) -> None:
    for name in [m for m in sys.modules if m.split(".")[0] == package.name]:
        del sys.modules[name]


def test_slice_string_reads_the_two_indices_of_its_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenG's Slice String 1 scans the text before and after the ``:`` of
    ``i:j`` with ``%d``; a negative index counts from the end."""
    vi = OPENG_USERLIB / "_OpenG.lib/string/string.llb/Slice String 1__ogtk.vi"
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    package, module = _module(
        vi,
        tmp_path,
        monkeypatch,
        "userlib._openglib.string.stringllb.slice_string_1__ogtk",
    )
    try:
        slice_string = module.slice_string_1__ogtk
        assert slice_string("abcdef", "1:3").substring_stringij == "bc"
        assert slice_string("abcdef", " 1 : 3 ").substring_stringij == "bc"
        assert (
            slice_string("abcdef", "1:3", include_stringj=True).substring_stringij
            == "bcd"
        )
        assert slice_string("abcdef", "-3:-1").substring_stringij == "de"
        assert "re.match(" in Path(module.__file__).read_text(encoding="utf-8")
    finally:
        _forget(package)


def test_increment_version_number_reads_each_part_of_the_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JKI's Increment Version Number scans one part of the version string with
    ``%d`` and adds one."""
    vi = (
        SAMPLES
        / "JKI-VI-Tester/source/Build Support/Package Builder Utilities"
        / "Increment Version Number__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
    )
    package, module = _module(
        vi,
        tmp_path,
        monkeypatch,
        "build_support.package_builder_utilities."
        "increment_version_number__jki_right_click_build_support",
    )
    try:
        increment = module.increment_version_number__jki_right_click_build_support
        assert (
            increment(version_string="1.2.3", action=0).incremented_version_string
            == "2.0.0"
        )
        assert (
            increment(version_string="1.2.3", action=1).incremented_version_string
            == "1.3.0"
        )
        assert (
            increment(version_string="1.2.3", action=2).incremented_version_string
            == "1.2.4"
        )
    finally:
        _forget(package)
