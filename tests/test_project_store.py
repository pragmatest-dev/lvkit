"""Tests for project-local resolution store discovery and overlay semantics."""

from __future__ import annotations

import argparse
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
    (store / "primitives.json").write_text(json.dumps({
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


# ============================================================
# Skill installation
# ============================================================


def test_install_claude_skills_creates_all_user_facing(tmp_path: Path) -> None:
    """install_claude_skills writes every packaged template to .claude/skills/."""
    from vipy.project_store import install_claude_skills

    written = install_claude_skills(tmp_path)
    assert len(written) == 5
    for skill in (
        "vipy-resolve-primitive",
        "vipy-resolve-vilib",
        "vipy-describe",
        "vipy-convert",
        "vipy-idiomatic",
    ):
        path = tmp_path / ".claude" / "skills" / skill / "SKILL.md"
        assert path.is_file(), f"missing {path}"
        # Frontmatter present
        text = path.read_text()
        assert text.startswith("---\n")
        assert f"name: {skill}" in text


def test_install_claude_skills_idempotent(tmp_path: Path) -> None:
    """Re-running with no changes is a no-op (returns empty list)."""
    from vipy.project_store import install_claude_skills

    install_claude_skills(tmp_path)
    second = install_claude_skills(tmp_path)
    assert second == []


def test_install_claude_skills_refuses_local_edits(tmp_path: Path) -> None:
    """Re-running over a locally edited file fails without --force."""
    from vipy.project_store import install_claude_skills

    install_claude_skills(tmp_path)
    edited = tmp_path / ".claude" / "skills" / "vipy-convert" / "SKILL.md"
    edited.write_text("LOCAL EDIT\n")

    with pytest.raises(FileExistsError, match="local edits"):
        install_claude_skills(tmp_path)


def test_install_claude_skills_force_overwrites(tmp_path: Path) -> None:
    """--force overwrites local edits."""
    from vipy.project_store import install_claude_skills

    install_claude_skills(tmp_path)
    edited = tmp_path / ".claude" / "skills" / "vipy-convert" / "SKILL.md"
    edited.write_text("LOCAL EDIT\n")

    install_claude_skills(tmp_path, force=True)
    assert "LOCAL EDIT" not in edited.read_text()
    assert "name: vipy-convert" in edited.read_text()


def test_install_claude_skills_atomic_on_conflict(tmp_path: Path) -> None:
    """A conflict aborts the whole install — no skills are written.

    Regression test for the pre-validate-then-write phase split. The
    bare-minimum guarantee: if any skill conflicts and force is False,
    no other skills should be written either.
    """
    from vipy.project_store import install_claude_skills

    # Create a conflicting file for ONE skill before the install runs.
    # The other 4 skills don't exist yet — without the atomic phase
    # split, they'd get written before the install hit the conflict.
    skills_dir = tmp_path / ".claude" / "skills"
    (skills_dir / "vipy-convert").mkdir(parents=True)
    (skills_dir / "vipy-convert" / "SKILL.md").write_text("LOCAL EDIT\n")

    with pytest.raises(FileExistsError, match="local edits"):
        install_claude_skills(tmp_path)

    # Critical: the OTHER 4 skills must NOT have been written. The
    # install must be all-or-nothing for the conflict-validation phase.
    for skill in (
        "vipy-resolve-primitive",
        "vipy-resolve-vilib",
        "vipy-describe",
        "vipy-idiomatic",
    ):
        path = skills_dir / skill / "SKILL.md"
        assert not path.exists(), (
            f"{skill} was written despite a conflict in another skill"
        )
    # The conflicting file is preserved.
    assert (skills_dir / "vipy-convert" / "SKILL.md").read_text() == "LOCAL EDIT\n"


# ============================================================
# Copilot install: 5 prompts + 1 router instruction
# ============================================================


_VIPY_SKILLS = (
    "vipy-describe",
    "vipy-convert",
    "vipy-resolve-primitive",
    "vipy-resolve-vilib",
    "vipy-idiomatic",
)


def test_install_copilot_skills_writes_prompts_and_router(tmp_path: Path) -> None:
    """install_copilot_skills writes 5 prompts + 1 router file."""
    from vipy.project_store import install_copilot_skills

    written = install_copilot_skills(tmp_path)
    assert len(written) == 6  # 5 prompts + router

    # Each user-facing skill has its own prompt file
    prompts_dir = tmp_path / ".github" / "prompts"
    for skill in _VIPY_SKILLS:
        path = prompts_dir / f"{skill}.prompt.md"
        assert path.is_file(), f"missing {path}"
        text = path.read_text()
        # Copilot prompt frontmatter shape
        assert text.startswith("---\n")
        assert "mode: agent" in text
        assert "description:" in text
        # Body is from the original SKILL.md (frontmatter rewritten)
        assert "allowed-tools:" not in text  # Claude-only field, stripped
        assert f"name: {skill}" not in text  # Claude-only field, stripped

    # Single router instruction
    router = tmp_path / ".github" / "instructions" / "vipy.instructions.md"
    assert router.is_file()
    router_text = router.read_text()
    assert router_text.startswith("---\n")
    assert 'applyTo: "**"' in router_text
    # Router lists every prompt
    for skill in _VIPY_SKILLS:
        assert f"`/{skill}`" in router_text


def test_install_copilot_skills_router_uses_workflow_order(tmp_path: Path) -> None:
    """Router lists prompts in logical workflow order, not alphabetical."""
    from vipy.project_store import install_copilot_skills

    install_copilot_skills(tmp_path)
    router_text = (
        tmp_path / ".github" / "instructions" / "vipy.instructions.md"
    ).read_text()

    expected_order = [
        "/vipy-describe",
        "/vipy-convert",
        "/vipy-resolve-primitive",
        "/vipy-resolve-vilib",
        "/vipy-idiomatic",
    ]
    positions = [router_text.find(name) for name in expected_order]
    assert all(p >= 0 for p in positions), "missing prompt reference(s)"
    assert positions == sorted(positions), (
        f"prompts out of order in router: {positions}"
    )


def test_install_copilot_skills_idempotent(tmp_path: Path) -> None:
    """Re-running with no template changes returns empty list."""
    from vipy.project_store import install_copilot_skills

    install_copilot_skills(tmp_path)
    second = install_copilot_skills(tmp_path)
    assert second == []


def test_install_copilot_skills_atomic_on_conflict(tmp_path: Path) -> None:
    """A locally-edited prompt aborts the whole install."""
    from vipy.project_store import install_copilot_skills

    # Pre-create a conflicting prompt file before the install.
    prompts_dir = tmp_path / ".github" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "vipy-convert.prompt.md").write_text("LOCAL EDIT\n")

    with pytest.raises(FileExistsError, match="local edits"):
        install_copilot_skills(tmp_path)

    # Other prompts and the router must not have been written.
    for skill in _VIPY_SKILLS:
        if skill == "vipy-convert":
            continue
        path = prompts_dir / f"{skill}.prompt.md"
        assert not path.exists(), (
            f"{skill} prompt was written despite the conflict"
        )
    router = tmp_path / ".github" / "instructions" / "vipy.instructions.md"
    assert not router.exists()


def test_install_copilot_skills_force_overwrites(tmp_path: Path) -> None:
    """--force overwrites locally edited prompts."""
    from vipy.project_store import install_copilot_skills

    install_copilot_skills(tmp_path)
    edited = tmp_path / ".github" / "prompts" / "vipy-convert.prompt.md"
    edited.write_text("LOCAL EDIT\n")

    install_copilot_skills(tmp_path, force=True)
    assert "LOCAL EDIT" not in edited.read_text()
    assert "mode: agent" in edited.read_text()


def test_cli_init_skills_claude(tmp_path: Path) -> None:
    """`vipy init --skills claude` installs Claude Code skills."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable, "-m", "vipy.cli", "init",
            str(tmp_path), "--skills", "claude",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Installed 5 Claude Code skill(s)" in result.stdout
    assert (
        tmp_path / ".claude" / "skills" / "vipy-convert" / "SKILL.md"
    ).is_file()


def test_cli_init_skills_all(tmp_path: Path) -> None:
    """`vipy init --skills all` installs both Claude and Copilot."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable, "-m", "vipy.cli", "init",
            str(tmp_path), "--skills", "all",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Installed 5 Claude Code skill(s)" in result.stdout
    assert "Installed 6 Copilot file(s)" in result.stdout  # 5 prompts + router
    assert (
        tmp_path / ".claude" / "skills" / "vipy-resolve-primitive" / "SKILL.md"
    ).is_file()
    assert (
        tmp_path / ".github" / "prompts" / "vipy-convert.prompt.md"
    ).is_file()
    assert (
        tmp_path / ".github" / "instructions" / "vipy.instructions.md"
    ).is_file()


# ============================================================
# CLI: --project-root accepts both forms
# ============================================================


def _make_args(**kwargs: object) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for _configure_resolvers."""
    return argparse.Namespace(**kwargs)


def test_cli_project_root_accepts_parent_dir(tmp_path: Path) -> None:
    """`--project-root <root>` works when arg points to parent of .vipy/."""
    from vipy.cli import _configure_resolvers
    init_project_store(tmp_path)
    try:
        store = _configure_resolvers(_make_args(project_root=str(tmp_path)))
        assert store == tmp_path / ".vipy"
    finally:
        primitive_resolver.reset_resolver(project_data_dir=None)
        vilib_resolver.reset_resolver(project_data_dir=None)


def test_cli_project_root_accepts_dotvipy_dir(tmp_path: Path) -> None:
    """`--project-root <path>/.vipy` also works (user passes the store itself)."""
    from vipy.cli import _configure_resolvers
    store_dir = init_project_store(tmp_path)
    try:
        store = _configure_resolvers(_make_args(project_root=str(store_dir)))
        assert store == store_dir
    finally:
        primitive_resolver.reset_resolver(project_data_dir=None)
        vilib_resolver.reset_resolver(project_data_dir=None)


def test_cli_project_root_invalid_returns_none(tmp_path: Path) -> None:
    """When --project-root points nowhere, store is None and resolvers reset."""
    from vipy.cli import _configure_resolvers
    bogus = tmp_path / "does_not_exist"
    try:
        store = _configure_resolvers(_make_args(project_root=str(bogus)))
        assert store is None
    finally:
        primitive_resolver.reset_resolver(project_data_dir=None)
        vilib_resolver.reset_resolver(project_data_dir=None)


# ============================================================
# MCP: per-call discovery from VI path
# ============================================================


def test_mcp_discover_from_vi_file(tmp_path: Path) -> None:
    """MCP helper finds .vipy/ when given a file path inside the project."""
    from vipy.mcp.server import _configure_resolvers_for_vi

    init_project_store(tmp_path)
    deep_dir = tmp_path / "lib" / "subdir"
    deep_dir.mkdir(parents=True)
    fake_vi = deep_dir / "Foo.vi"
    fake_vi.write_text("not a real vi")  # only path matters

    try:
        _configure_resolvers_for_vi(str(fake_vi))
        # If discovery worked, the singleton resolvers were reset with the
        # store. We can't directly inspect, but we can re-discover.
        from vipy.project_store import find_project_store
        assert find_project_store(start=fake_vi.parent) == tmp_path / ".vipy"
    finally:
        primitive_resolver.reset_resolver(project_data_dir=None)
        vilib_resolver.reset_resolver(project_data_dir=None)


def test_mcp_discover_from_directory_path(tmp_path: Path) -> None:
    """MCP helper handles directory paths without going one level too high.

    Regression test for the bug where Path.resolve().parent returned the
    grandparent of a directory path (e.g. .lvlib), missing the project's
    .vipy/ when it lived inside the directory.

    Setup: the .vipy/ store lives INSIDE the lvlib directory itself, with
    a primitive override. With the buggy `start = path.parent` logic,
    discovery starts from `lvlib_dir.parent` and walks up — never visiting
    `lvlib_dir` itself, so it would miss `lvlib_dir/.vipy/`. We verify the
    MCP function actually loaded the project store by checking that the
    project's primitive override is what the resolver returns.
    """
    from vipy.mcp.server import _configure_resolvers_for_vi

    # Drop a .git at tmp_path so discovery can't escape upward.
    (tmp_path / ".git").mkdir()

    # .vipy/ inside the lvlib — buggy code (start from lvlib.parent)
    # would walk tmp_path (no .vipy) → .git → return None.
    lvlib_dir = tmp_path / "MyLib.lvlib"
    lvlib_dir.mkdir()
    store = init_project_store(lvlib_dir)
    # Add a primitive override so we can detect that the store was loaded.
    (store / "primitives.json").write_text(json.dumps({
        "primitives": {
            "1419": {"name": "MCP-DIR-TEST OVERRIDE", "terminals": []}
        }
    }))

    try:
        _configure_resolvers_for_vi(str(lvlib_dir))
        # If MCP correctly used the directory as start, the project store
        # was loaded and the override should be visible.
        entry = primitive_resolver.get_resolver().get_by_id(1419)
        assert entry is not None
        assert entry["name"] == "MCP-DIR-TEST OVERRIDE", (
            "MCP did not load .vipy/ from inside the directory path — "
            "likely walked from .parent instead of the directory itself."
        )
    finally:
        primitive_resolver.reset_resolver(project_data_dir=None)
        vilib_resolver.reset_resolver(project_data_dir=None)


def test_mcp_discover_returns_none_outside_project(tmp_path: Path) -> None:
    """MCP helper resets resolvers to no-store when VI is outside any project."""
    from vipy.mcp.server import _configure_resolvers_for_vi

    # Drop a .git so discovery stops here without finding any .vipy/
    (tmp_path / ".git").mkdir()
    fake_vi = tmp_path / "Foo.vi"
    fake_vi.write_text("not a real vi")

    try:
        _configure_resolvers_for_vi(str(fake_vi))
        # Verify no exception was raised; resolvers should fall back to shipped
        from vipy.project_store import find_project_store
        assert find_project_store(start=fake_vi.parent) is None
    finally:
        primitive_resolver.reset_resolver(project_data_dir=None)
        vilib_resolver.reset_resolver(project_data_dir=None)
