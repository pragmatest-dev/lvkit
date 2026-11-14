"""Tests for ``structure.generate_python_structure_plan``'s method-scope ->
Python-visibility-prefix mapping (design-review finding R5).

Pure unit test (no VI/sample corpus needed — the function takes a plain
dict), so this deliberately does NOT carry ``needs_samples``.
"""

from __future__ import annotations

from lvkit.structure import generate_python_structure_plan


def _plan_for_scope(scope: str) -> str:
    structure = {
        "libraries": [],
        "classes": [
            {
                "name": "MyClass.lvclass",
                "path": "MyClass.lvclass",
                "private_data": None,
                "methods": [
                    {"name": "DoThing.vi", "scope": scope, "is_static": False}
                ],
            }
        ],
    }
    return generate_python_structure_plan(structure)


def test_private_scope_gets_double_underscore_python_name_mangling() -> None:
    """Python's OWN convention: a leading DOUBLE underscore triggers real
    name-mangling inside the class -- that's "private", not "protected"."""
    plan = _plan_for_scope("private")
    assert "\n  - __dothing_vi()" in plan


def test_protected_scope_gets_single_underscore() -> None:
    """A single leading underscore is Python's weak "internal use" marker --
    matches LabVIEW's "protected" (visible to subclasses), never the
    double-underscore name-mangled "private" form."""
    plan = _plan_for_scope("protected")
    assert "\n  - _dothing_vi()" in plan


def test_community_scope_gets_its_own_explicit_marker_not_public() -> None:
    """LabVIEW's "community"/"package" scope (SCOPE_MAP value 4) has no
    Python equivalent -- it must NOT silently fall into the public
    (no-prefix) case, and must be distinct from private/protected too."""
    plan = _plan_for_scope("community")
    assert "\n  - pkg_dothing_vi()" in plan


def test_public_scope_gets_no_prefix() -> None:
    plan = _plan_for_scope("public")
    assert "\n  - dothing_vi()" in plan
