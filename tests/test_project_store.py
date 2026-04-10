"""Tests for project-local resolution store discovery and overlay semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vipy import primitive_resolver, vilib_resolver
from vipy.primitive_resolver import PrimitiveResolver
from vipy.project_store import find_project_store, init_project_store
from vipy.vilib_resolver import VILibResolver

# ============================================================
# Discovery
# ============================================================


def test_find_project_store_in_cwd(tmp_path: Path) -> None:
    """find_project_store returns .vipy/ if directly under start dir."""
    (tmp_path / ".vipy").mkdir()
    assert find_project_store(start=tmp_path) == tmp_path / ".vipy"


def test_find_project_store_walks_up(tmp_path: Path) -> None:
    """Walks up the directory tree looking for .vipy/."""
    (tmp_path / ".vipy").mkdir()
    deep = tmp_path / "src" / "subpkg"
    deep.mkdir(parents=True)
    assert find_project_store(start=deep) == tmp_path / ".vipy"


def test_find_project_store_stops_at_git(tmp_path: Path) -> None:
    """Stops walking at a .git directory marker."""
    # Outer dir has .vipy/, but inner dir has .git/ — should NOT find outer .vipy/
    (tmp_path / ".vipy").mkdir()
    inner = tmp_path / "subrepo"
    inner.mkdir()
    (inner / ".git").mkdir()
    deep = inner / "src"
    deep.mkdir()
    assert find_project_store(start=deep) is None


def test_find_project_store_returns_none_when_absent(tmp_path: Path) -> None:
    """Returns None when no .vipy/ exists anywhere on the path."""
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    # Drop a .git so we don't escape into a real parent .vipy/
    (tmp_path / ".git").mkdir()
    assert find_project_store(start=deep) is None


def test_init_project_store_creates_layout(tmp_path: Path) -> None:
    """init_project_store creates .vipy/ with README and category dirs."""
    store = init_project_store(tmp_path)
    assert store == tmp_path / ".vipy"
    assert store.is_dir()
    assert (store / "README.md").exists()
    assert "cleanroom" in (store / "README.md").read_text().lower()
    for sub in ("vilib", "openg", "drivers"):
        assert (store / sub / "_index.json").exists()


def test_init_project_store_idempotent(tmp_path: Path) -> None:
    """Re-running init does not clobber existing files."""
    store = init_project_store(tmp_path)
    custom = "MY CUSTOM README"
    (store / "README.md").write_text(custom)
    init_project_store(tmp_path)
    assert (store / "README.md").read_text() == custom


# ============================================================
# PrimitiveResolver overlay
# ============================================================


@pytest.fixture
def project_with_prim_override(tmp_path: Path) -> Path:
    """A .vipy/ with a primitive entry overriding ID 1419 (Build Path)."""
    store = init_project_store(tmp_path)
    (store / "primitives-codegen.json").write_text(json.dumps({
        "primitives": {
            "1419": {
                "name": "PROJECT OVERRIDE Build Path",
                "terminals": [
                    {"index": 0, "direction": "in", "name": "base", "type": "Path"},
                    {"index": 1, "direction": "in", "name": "name", "type": "String"},
                    {"index": 2, "direction": "out", "name": "result", "type": "Path"},
                ],
                "python_code": "PROJECT_PATH",
            }
        }
    }))
    return store


def test_primitive_overlay_wins(project_with_prim_override: Path) -> None:
    """Project primitive entry takes priority over shipped entry."""
    resolver = PrimitiveResolver(project_data_dir=project_with_prim_override)
    entry = resolver.get_by_id(1419)
    assert entry is not None
    assert entry["name"] == "PROJECT OVERRIDE Build Path"


def test_primitive_shipped_fallback() -> None:
    """Without a project store, shipped data is loaded normally."""
    resolver = PrimitiveResolver(project_data_dir=None)
    entry = resolver.get_by_id(1419)
    assert entry is not None
    # Shipped entry — not the project override
    assert "PROJECT OVERRIDE" not in entry.get("name", "")


def test_primitive_reset_resolver_with_project(
    project_with_prim_override: Path,
) -> None:
    """reset_resolver swaps the cached singleton in place."""
    primitive_resolver.reset_resolver(project_data_dir=project_with_prim_override)
    try:
        entry = primitive_resolver.get_resolver().get_by_id(1419)
        assert entry is not None
        assert entry["name"] == "PROJECT OVERRIDE Build Path"
    finally:
        primitive_resolver.reset_resolver(project_data_dir=None)
        # Force re-init from shipped data
        primitive_resolver._resolver = None


# ============================================================
# VILibResolver overlay
# ============================================================


@pytest.fixture
def project_with_vilib_override(tmp_path: Path) -> Path:
    """A .vipy/ with a vilib entry overriding 'Trim Whitespace.vi'."""
    store = init_project_store(tmp_path)
    vilib_dir = store / "vilib"
    (vilib_dir / "_index.json").write_text(json.dumps({
        "categories": {"string": "string.json"}
    }))
    (vilib_dir / "string.json").write_text(json.dumps({
        "entries": [
            {
                "name": "Trim Whitespace.vi",
                "vi_path": "<vilib>/Utility/string.llb/Trim Whitespace.vi",
                "category": "string",
                "description": "PROJECT OVERRIDE description",
                "terminals": [
                    {
                        "name": "string", "index": 0, "direction": "in",
                        "type": "string",
                    },
                    {
                        "name": "trimmed string", "index": 1, "direction": "out",
                        "type": "string",
                    },
                ],
                "python_code": "PROJECT_TRIM",
                "inline": True,
            }
        ]
    }))
    return store


def test_vilib_overlay_wins(project_with_vilib_override: Path) -> None:
    """Project vilib entry takes priority over shipped entry."""
    resolver = VILibResolver(project_data_dir=project_with_vilib_override)
    entry = resolver.resolve_by_name("Trim Whitespace.vi")
    assert entry is not None
    assert entry.description == "PROJECT OVERRIDE description"


def test_vilib_shipped_fallback() -> None:
    """Without a project store, shipped vilib data is loaded normally."""
    resolver = VILibResolver(project_data_dir=None)
    # Shipped data should still resolve at least one VI we know exists.
    # If Trim Whitespace isn't shipped, this is fine — we just want no crash.
    entry = resolver.resolve_by_name("Trim Whitespace.vi")
    if entry is not None:
        assert "PROJECT OVERRIDE" not in (entry.description or "")


def test_vilib_reset_resolver_with_project(
    project_with_vilib_override: Path,
) -> None:
    """reset_resolver swaps the cached vilib singleton in place."""
    vilib_resolver.reset_resolver(project_data_dir=project_with_vilib_override)
    try:
        entry = vilib_resolver.get_resolver().resolve_by_name("Trim Whitespace.vi")
        assert entry is not None
        assert entry.description == "PROJECT OVERRIDE description"
    finally:
        vilib_resolver.reset_resolver(project_data_dir=None)


# ============================================================
# Regression: absent .vipy/ behaves identically
# ============================================================


def test_no_project_store_default_behavior() -> None:
    """With no project store, both resolvers behave as before."""
    p_resolver = PrimitiveResolver()
    VILibResolver()  # Just verify it constructs without error.
    # Sanity-check the primitive resolver loaded shipped data.
    assert p_resolver.get_by_id(1419) is not None


# ============================================================
# CLI: vipy init
# ============================================================


def test_cli_init_creates_project_store(tmp_path: Path) -> None:
    """`vipy init <dir>` creates .vipy/ with template content."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "vipy.cli", "init", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Initialized project store" in result.stdout
    assert (tmp_path / ".vipy" / "README.md").exists()
    assert (tmp_path / ".vipy" / "vilib" / "_index.json").exists()
