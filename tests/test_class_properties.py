"""Tests for the ``.lvclass``/``.lvlib`` "class-item property" enrichment:

- ``structure.SCOPE_MAP``'s missing value 4 ("community") fix.
- ``LVClass.version`` / ``LVClass.ancestors`` (the full ancestor chain, built
  by recursively resolving each ancestor's own ``.lvclass`` on disk).
- ``LVMethod.must_override`` / ``must_call_parent`` (per-method
  ``NI.ClassItem.*`` properties).
- ``ClassFact.is_static`` actually reaching the facts index (previously
  parsed by ``LVMethod`` but dropped on the way into ``ClassFact`` -- a real
  gap, not a new feature).

Uses the real sample corpora (JKI-VI-Tester, measurement-plugin-labview,
DCAF-DAQModule) -- each test skips individually when its specific corpus
isn't present, matching ``tests/test_class_field_walkup.py``'s convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.index.build import build_index
from lvkit.index.project import resolve_project
from lvkit.structure import parse_lvclass

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"

JKI_ROOT = SAMPLES / "JKI-VI-Tester" / "source"
MEASUREMENT_ROOT = SAMPLES / "measurement-plugin-labview"
DCAF_ROOT = SAMPLES / "DCAF-DAQModule"

SESSION_RESERVATION = (
    MEASUREMENT_ROOT / "Source" / "Runtime" / "Clients" / "Session Management V1"
    / "Session Reservation" / "Session Reservation.lvclass"
)


def _skip_unless(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"sample not present: {path}")


# === SCOPE_MAP: MethodScope=4 -> "community" ================================


def test_scope_map_resolves_community() -> None:
    """Session Reservation.lvclass carries real MethodScope=4 methods, which
    used to silently mislabel as "public" (SCOPE_MAP.get(4, "public"))."""
    lvclass_path = SESSION_RESERVATION
    _skip_unless(lvclass_path)

    cls = parse_lvclass(lvclass_path)
    scopes = {m.scope for m in cls.methods}
    assert "community" in scopes
    assert "public" not in {  # a value-4 method must NOT default to "public"
        m.scope for m in cls.methods
        if m.name in {"Register Sessions Wrapper", "Session Client FGV"}
    }


# === LVClass.version =========================================================


def test_class_version_parsed() -> None:
    """NI.Lib.Version on a real class -- verbatim dotted-quad string."""
    lvclass_path = SESSION_RESERVATION
    _skip_unless(lvclass_path)

    cls = parse_lvclass(lvclass_path)
    assert cls.version == "1.0.0.3"


# === LVClass.ancestors ========================================================


def test_ancestor_chain_resolves_across_directories() -> None:
    """_TextTestResult.JUnitXML.lvclass's parent (_TextTestResult) is decoded
    as a vi.lib-style link (is_vilib_parent=True) but its file is actually
    IN-REPO under a different branch of the tree (Classes/_TextTestResult/,
    not a sibling of the child) -- the walk-up+rglob resolution must still
    find it and continue to the root (TestResult)."""
    lvclass_path = (
        JKI_ROOT / "Ant Plugin" / "Source" / "_TextTestResult.Ant"
        / "_TextTestResult.JUnitXML.lvclass"
    )
    _skip_unless(lvclass_path)

    cls = parse_lvclass(lvclass_path)
    assert cls.ancestors == ["_TextTestResult", "TestResult"]


def test_root_class_has_empty_ancestor_chain() -> None:
    lvclass_path = JKI_ROOT / "Classes" / "TestResult" / "TestResult.lvclass"
    _skip_unless(lvclass_path)

    cls = parse_lvclass(lvclass_path)
    assert cls.ancestors == []


# === LVMethod.must_override / must_call_parent ===============================


def test_must_override_true_and_false() -> None:
    must_override_true = (
        MEASUREMENT_ROOT / "Source" / "Runtime" / "Measurements"
        / "Measurement Plugin Service" / "Measurement Plugin Service.lvclass"
    )
    must_override_false = (
        MEASUREMENT_ROOT / "Source" / "Runtime" / "Sessions" / "Instrument"
        / "ISession Factory" / "ISession Factory.lvclass"
    )
    _skip_unless(must_override_true)
    _skip_unless(must_override_false)

    service = parse_lvclass(must_override_true)
    get_plugin_paths = next(
        m for m in service.methods if m.name == "Get Plugin Paths"
    )
    assert get_plugin_paths.must_override is True

    factory = parse_lvclass(must_override_false)
    clean_up = next(
        m for m in factory.methods if m.name == "Clean Up After Init Error"
    )
    assert clean_up.must_override is False


def test_must_call_parent() -> None:
    lvclass_path = (
        DCAF_ROOT / "source" / "module" / "execution" / "Daqmx Module runtime.lvclass"
    )
    _skip_unless(lvclass_path)

    cls = parse_lvclass(lvclass_path)
    init = next(m for m in cls.methods if m.name == "init")
    assert init.must_call_parent is True


# === ClassFact.is_static reaching the index ===================================


def test_is_static_reaches_class_fact() -> None:
    """LVMethod.is_static was already parsed but dropped on the "owns" edge
    (never threaded into ClassFact) -- this is the gap fix, not new parsing.
    createTestCaseXML.vi is a static method; addError.vi is not."""
    class_dir = JKI_ROOT / "Ant Plugin" / "Source" / "_TextTestResult.Ant"
    _skip_unless(class_dir)

    root, vi_paths = resolve_project(class_dir)
    result = build_index(root, vi_paths)
    by_name = {f.name: f for f in result.facts}

    static_fact = by_name["createTestCaseXML.vi"].class_fact
    assert static_fact is not None
    assert static_fact.is_static is True

    instance_fact = by_name["addError.vi"].class_fact
    assert instance_fact is not None
    assert instance_fact.is_static is False

    # class_version/ancestors are duplicated per method, same convention as
    # parent/private_data.
    assert static_fact.class_version == "1.0.0.0"
    assert static_fact.ancestors == ["_TextTestResult", "TestResult"]
