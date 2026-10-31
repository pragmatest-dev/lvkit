"""Project-local resolution store discovery and initialization.

lvkit ships as cleanroom — its bundled data directory contains only
mappings derived from public documentation. A project-local `.lvkit/`
directory lets users with LabVIEW licenses contribute their own mappings
(potentially derived from licensed sources) without contaminating lvkit's
shipment.

The store is a parallel layout to lvkit's bundled data:

    .lvkit/
      README.md                 # license-boundary explainer
      primitives.json           # primitive overrides
      vilib/
        _index.json
        <category>.json
      openg/
      drivers/

Resolvers load `.lvkit/` first, then fall back to lvkit's bundled data.

This module also installs Claude Code, GitHub Copilot, and OpenAI Codex
workflows from the packaged templates so downstream users can run lvkit's
resolve workflows in their own LLM-enabled editor.
"""

from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path
from typing import Any

PROJECT_STORE_DIR = ".lvkit"


def global_home() -> Path:
    """The per-user lvkit home — ``~/.lvkit`` (branded, like ``~/.claude``).

    Single source of truth for the global-home location: the extraction cache
    lives under it (``extractor.global_cache_root``), and ``find_project_store``
    guards against it so a bare ``~/.lvkit`` is never mistaken for a *project*
    store (which any ``.lvkit/`` dir otherwise would be). Same brand name as the
    project store, two scopes.
    """
    return Path.home() / PROJECT_STORE_DIR


_README_TEMPLATE = """# .lvkit/ — Project-local resolution store

This directory is your project's private mapping store for lvkit. lvkit ships
as a **cleanroom** tool — its bundled `data/` directory contains only
mappings derived from public documentation, never from LabVIEW source or
vi.lib internals.

If you have a LabVIEW license, you (or your LLM assistant) can populate this
directory with mappings derived from the real vi.lib, your own LabVIEW
sources, or third-party libraries you have rights to use. lvkit reads
`.lvkit/` **first** and only falls back to its shipped `data/` if no project
mapping exists.

## License boundary

**lvkit itself never reads `.lvkit/` into its own data files.** Anything you
put here stays in your project. Do not submit content from `.lvkit/` upstream
to lvkit unless it is independently cleanroom-derived.

If any file under `.lvkit/` is derived from licensed material (vi.lib block
diagrams, NI driver source, etc.), consider gitignoring it so it does not
get committed to a public repository.

## Layout

    .lvkit/
      primitives.json   # primitive ID → name, terminals, python_code
      vilib/
        _index.json             # category → filename
        <category>.json         # vi.lib VI mappings
      openg/                    # OpenG library mappings (same format)
      drivers/                  # NI driver mappings (same format)

The format mirrors lvkit's shipped `data/` exactly. Copy an example from
`<lvkit install>/data/` if you need a starting point.

## Populating it

When `lvkit generate` hits an unknown primitive or vi.lib VI, it warns and
the generated code raises `NotImplementedError` at that node; `lvkit
unresolved` lists the full diagnostic context of each — including the
qualified path of the unknown VI. An
LLM with access to your LabVIEW sources can use this to author the
mapping and write it here.

If you use Claude Code, run `lvkit setup claude` to install
resolution skills into your project's `.claude/skills/`. For Copilot or
Cursor, use `lvkit setup copilot`. For Codex, run
`lvkit setup codex`; skills are installed into `.agents/skills/`.
"""


def find_project_store(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default CWD) looking for a .lvkit/ directory.

    Stops at the first match, at a `.git` directory marker, or at the
    filesystem root. Returns the path to the .lvkit/ directory, or None if
    no project store is found.

    Args:
        start: Directory to start the walk from. Defaults to CWD.

    Returns:
        Path to the .lvkit/ directory, or None.
    """
    current = (start or Path.cwd()).resolve()
    # The global home (``~/.lvkit``) is a ``.lvkit/`` dir too — never treat it as
    # a *project* store. Compare against the accessor, not a hard-coded path, so
    # the two can't drift.
    home = global_home().resolve()

    for candidate in [current, *current.parents]:
        store = candidate / PROJECT_STORE_DIR
        if store.is_dir() and store.resolve() != home:
            return store
        # Stop at repo root — don't escape into a parent project's .lvkit/
        if (candidate / ".git").exists():
            return None

    return None


def project_root(input_path: Path) -> Path:
    """The project folder of a VI (or folder): the nearest folder
    above it holding a ``.lvproj`` file, a ``.git`` folder or a ``.lvkit`` project
    store; the VI's own folder when there is none."""
    start = input_path.resolve()
    own = start if start.is_dir() else start.parent
    home = global_home().resolve()
    for folder in [own, *own.parents]:
        store = folder / PROJECT_STORE_DIR
        if (
            any(folder.glob("*.lvproj"))
            or (folder / ".git").exists()
            or (store.is_dir() and store.resolve() != home)
        ):
            return folder
    return own


def init_project_store(root: Path) -> Path:
    """Create .lvkit/ under `root` with a template README.md.

    Idempotent: if .lvkit/ already exists, leaves it alone but ensures the
    README is present (does not overwrite an existing README).

    Args:
        root: Directory under which to create .lvkit/.

    Returns:
        Path to the created (or existing) .lvkit/ directory.
    """
    store = root / PROJECT_STORE_DIR
    store.mkdir(exist_ok=True)

    readme = store / "README.md"
    if not readme.exists():
        readme.write_text(_README_TEMPLATE, encoding="utf-8")

    # Create empty index files for each category dir so loaders find them.
    for subdir in ("vilib", "openg", "drivers"):
        sub = store / subdir
        sub.mkdir(exist_ok=True)
        index = sub / "_index.json"
        if not index.exists():
            index.write_text(
                json.dumps({"categories": {}}, indent=2) + "\n", encoding="utf-8"
            )

    return store


def write_json_atomic(path: Path, data: object) -> None:
    """Write ``data`` as JSON to ``path`` so a concurrent reader sees the old
    or the new file, never an empty or half-written one: the text goes to a
    sibling file named for this process, then ``os.replace`` swaps it in."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ============================================================
# Skill installation
# ============================================================
#
# lvkit ships user-facing skill templates as package data under
# `src/lvkit/skill_templates/lvkit-<name>/SKILL.md`. The functions below
# install them into a downstream user's project so the user's LLM
# editor (Claude Code, GitHub Copilot, OpenAI Codex) can run lvkit's workflows.
#
# Three install targets:
#
# - install_claude_skills(): copies SKILL.md files to .claude/skills/
#   one-for-one. Claude Code reads them as auto-launchable skills.
#
# - install_copilot_skills(): writes Agent Skills to .github/skills/. VS Code
#   loads them by description match and also exposes each as a /<name> slash
#   command, so no router file is needed.
#
# - install_codex_skills(): writes Agent Skills to .agents/skills/. Codex
#   natively supports both description-based implicit activation and explicit
#   $<name> mentions, so it does not need a separate router file.

# Marker comment lvkit embeds in every generated Copilot file so users
# (and re-installs) can recognize a lvkit-managed file. Prompt files and the
# router instruction that carry it are removed on install.
_COPILOT_MANAGED_MARKER = (
    "<!-- lvkit: managed by `lvkit setup`, re-running will overwrite this file -->"
)

# Logical workflow order. The Copilot router lists prompts in this
# order so they appear as understand → convert → resolve → refactor
# instead of alphabetical (which would put "convert" first).
#
# Order is also applied (incidentally) to the Claude install via
# _iter_skill_templates, but Claude doesn't care because each skill is
# its own file. New skills not in this list are appended in alphabetical
# order — they install fine, they just sort to the end of the router.
# To put a new skill in a specific position, add it to this list.
_SKILL_ORDER = [
    "lvkit",  # positioning / router — the entry point
    "lvkit-describe",
    "lvkit-query",
    "lvkit-convert",
    "lvkit-document",
    "lvkit-review",
    "lvkit-resolve",
]


def _iter_skill_templates() -> list[Any]:
    """Yield the skill template directories as Traversable objects.

    Hides the package-data plumbing (importlib.resources, filtering of
    package-internal directories like __pycache__) from callers.
    """
    template_root = files("lvkit.skill_templates")
    return sorted(
        (
            d
            for d in template_root.iterdir()
            if d.is_dir() and not d.name.startswith(("_", "."))
        ),
        key=lambda d: (
            _SKILL_ORDER.index(d.name) if d.name in _SKILL_ORDER else len(_SKILL_ORDER),
            d.name,
        ),
    )


def install_claude_skills(target_dir: Path, force: bool = False) -> list[Path]:
    """Install lvkit's Claude Code skills into a project.

    Copies every packaged template under
    `src/lvkit/skill_templates/<name>/SKILL.md` into
    `<target_dir>/.claude/skills/<name>/SKILL.md`.

    Atomic with respect to local-edit detection: pre-validates every
    destination before writing any of them, so a conflict on the third
    skill won't leave the first two already overwritten.

    Args:
        target_dir: Project root. The `.claude/skills/` tree is created
            under this directory.
        force: Overwrite existing files even if they have local edits.
            By default, conflicts raise FileExistsError listing every
            file that would be overwritten.

    Returns:
        List of paths that were written or overwritten. Empty when all
        templates are already byte-identical to the in-repo copies (a
        no-op re-install).
    """
    skills_dir = target_dir / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: discover and stage. Build the full work list and collect
    # any conflicts before touching disk. Shares the staging helper with
    # install_copilot_skills so the atomic semantics stay consistent.
    plan: list[tuple[Path, str]] = []
    conflicts: list[Path] = []

    for skill_dir in _iter_skill_templates():
        template_file = skill_dir.joinpath("SKILL.md")
        if not template_file.is_file():
            raise ValueError(
                f"Skill template directory {skill_dir.name!r} is missing"
                " its SKILL.md — packaging or sync error."
            )
        dest = skills_dir / skill_dir.name / "SKILL.md"
        _stage_write(
            dest, template_file.read_text(encoding="utf-8"), force, plan, conflicts
        )

    if conflicts:
        names = "\n  ".join(str(p) for p in conflicts)
        raise FileExistsError(
            f"{len(conflicts)} skill file(s) have local edits and would"
            f" be overwritten:\n  {names}\n"
            "Re-run with force=True to overwrite."
        )

    # Phase 2: commit all writes. No conflicts means we can commit.
    written: list[Path] = []
    for dest, new_content in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content, encoding="utf-8")
        written.append(dest)

    return written


def install_copilot_skills(target_dir: Path, force: bool = False) -> list[Path]:
    """Install lvkit's workflows as GitHub Copilot Agent Skills.

    Each packaged ``SKILL.md`` is converted to the Agent Skills shape VS Code
    reads and written to ``<target_dir>/.github/skills/<name>/SKILL.md``. The
    conversion keeps the portable ``name`` and ``description`` metadata,
    drops the Claude-specific ``allowed-tools`` field, keeps cross-skill
    references as ``/<name>`` slash commands, and adds the lvkit-managed
    marker after the frontmatter. ``disable-model-invocation`` is not set, so
    Copilot can load a skill by matching its description.

    Prompt files (``.github/prompts/lvkit*.prompt.md``) and the router
    ``.github/instructions/lvkit.instructions.md`` are deleted when they
    carry the lvkit-managed marker, and ``.github/prompts/`` is removed if
    that leaves it empty. Files without the marker are left alone.

    Atomic with respect to local-edit detection: every destination is
    validated before any file is written or deleted.

    Args:
        target_dir: Project root. The ``.github/skills/`` tree is created
            under this directory.
        force: Overwrite existing skill files even if they have local edits.

    Returns:
        List of paths that were written or overwritten. Empty when all
        installed skills are already up to date.
    """
    plan: list[tuple[Path, str]] = []
    conflicts: list[Path] = []

    for skill_dir in _iter_skill_templates():
        template_file = skill_dir.joinpath("SKILL.md")
        if not template_file.is_file():
            raise ValueError(
                f"Skill template directory {skill_dir.name!r} is missing"
                " its SKILL.md — packaging or sync error."
            )
        skill_content = _build_agent_skill(
            template_file.read_text(encoding="utf-8"),
            mention_prefix="/",
            marker=_COPILOT_MANAGED_MARKER,
        )
        dest = target_dir / ".github" / "skills" / skill_dir.name / "SKILL.md"
        _stage_write(dest, skill_content, force, plan, conflicts)

    if conflicts:
        names = "\n  ".join(str(p) for p in conflicts)
        raise FileExistsError(
            f"{len(conflicts)} Copilot skill file(s) have local edits and would"
            f" be overwritten:\n  {names}\n"
            "Re-run with force=True to overwrite."
        )

    written: list[Path] = []
    for dest, new_content in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content, encoding="utf-8")
        written.append(dest)
    _remove_managed_copilot_files(target_dir)
    return written


def _remove_managed_copilot_files(target_dir: Path) -> None:
    """Delete lvkit-managed Copilot prompt files and router instruction.

    Only files containing ``_COPILOT_MANAGED_MARKER`` are removed.
    ``.github/prompts/`` is removed afterwards if it is empty.
    """
    github_dir = target_dir / ".github"
    prompts_dir = github_dir / "prompts"
    candidates = sorted(prompts_dir.glob("lvkit*.prompt.md"))
    candidates.append(github_dir / "instructions" / "lvkit.instructions.md")
    for path in candidates:
        if path.is_file() and _COPILOT_MANAGED_MARKER in path.read_text(
            encoding="utf-8"
        ):
            path.unlink()
    if prompts_dir.is_dir() and not any(prompts_dir.iterdir()):
        prompts_dir.rmdir()


def install_codex_skills(target_dir: Path, force: bool = False) -> list[Path]:
    """Install lvkit's workflows as repository-scoped Codex skills.

    Each packaged ``SKILL.md`` is converted to the Agent Skills shape Codex
    consumes and written to
    ``<target_dir>/.agents/skills/<name>/SKILL.md``. The conversion preserves
    the portable ``name`` and ``description`` metadata, drops the
    Claude-specific ``allowed-tools`` field, and rewrites cross-skill slash
    commands to Codex's explicit ``$skill-name`` mention syntax.

    Atomic with respect to local-edit detection: every destination is
    validated before any skill file is written.

    Args:
        target_dir: Project root. The ``.agents/skills/`` tree is created
            under this directory.
        force: Overwrite existing files even if they have local edits.

    Returns:
        List of paths that were written or overwritten. Empty when all
        installed skills are already up to date.
    """
    plan: list[tuple[Path, str]] = []
    conflicts: list[Path] = []

    for skill_dir in _iter_skill_templates():
        template_file = skill_dir.joinpath("SKILL.md")
        if not template_file.is_file():
            raise ValueError(
                f"Skill template directory {skill_dir.name!r} is missing"
                " its SKILL.md — packaging or sync error."
            )
        skill_content = _build_agent_skill(
            template_file.read_text(encoding="utf-8"), mention_prefix="$"
        )
        dest = target_dir / ".agents" / "skills" / skill_dir.name / "SKILL.md"
        _stage_write(dest, skill_content, force, plan, conflicts)

    if conflicts:
        names = "\n  ".join(str(p) for p in conflicts)
        raise FileExistsError(
            f"{len(conflicts)} Codex skill file(s) have local edits and would"
            f" be overwritten:\n  {names}\n"
            "Re-run with force=True to overwrite."
        )

    written: list[Path] = []
    for dest, new_content in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content, encoding="utf-8")
        written.append(dest)
    return written


def _stage_write(
    dest: Path,
    new_content: str,
    force: bool,
    plan: list[tuple[Path, str]],
    conflicts: list[Path],
) -> None:
    """Add (dest, content) to plan, or to conflicts if local-edited.

    Shared by all skill installers for their atomic phase splits: phase 1
    calls this for every
    destination so phase 2 can either commit everything in one pass or
    raise a single error listing every conflict. Files that already
    match the new content are silently skipped (not added to plan,
    not flagged as conflicts).
    """
    if dest.exists():
        existing = dest.read_text(encoding="utf-8")
        if existing == new_content:
            return  # already up to date
        if not force:
            conflicts.append(dest)
            return
    plan.append((dest, new_content))


def _build_agent_skill(
    skill_md: str, mention_prefix: str, marker: str | None = None
) -> str:
    """Build an Agent Skill (Codex or Copilot) from a packaged SKILL.md.

    Keeps the portable ``name`` and ``description`` metadata. Cross-skill
    references are rewritten to ``<mention_prefix>name``: ``$`` for Codex's
    skill mentions, ``/`` for Copilot's slash commands. Lines referencing a
    nonexistent CLI command (``lvkit llm-generate``/``lvkit check``) are
    stripped as a safety net. When ``marker`` is given it is placed on its
    own line after the frontmatter.
    """
    fm, body = _split_frontmatter(skill_md)
    name = _yaml_get(fm, "name")
    description = _yaml_get(fm, "description")
    if not name or not description:
        raise ValueError("Skill templates require name and description")

    for skill_name in _SKILL_ORDER:
        body = body.replace(f"/{skill_name}", f"{mention_prefix}{skill_name}")
    body = "".join(
        line
        for line in body.splitlines(keepends=True)
        if not line.lstrip().startswith(("lvkit llm-generate ", "lvkit check "))
    )
    if "python3 -c" in body or "grep -" in body:
        body = (
            "> **Shell compatibility:** Examples below use POSIX syntax. On "
            "Windows, run them in WSL or Git Bash, or use equivalent "
            "PowerShell commands.\n\n"
            f"{body.lstrip()}"
        )

    separator = f"{marker}\n\n" if marker else "\n"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {_yaml_quote(description)}\n"
        "---\n"
        f"{separator}"
        f"{body.lstrip()}"
    )


def _yaml_quote(value: str) -> str:
    """Wrap a string in YAML double quotes, escaping inner quotes/backslashes.

    Minimal — handles the only YAML escapes lvkit needs (`"` and `\\`).
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a Markdown file into (frontmatter, body).

    Returns ``("", text)`` if there is no leading ``---\\n`` line at all.
    Raises ``ValueError`` if the file *opens* a frontmatter block but
    never closes it — that's a malformed SKILL.md a maintainer should
    fix, not silent fall-through that propagates the broken content
    into the generated prompt.

    The returned frontmatter is the inner content WITHOUT the wrapping
    ``---`` delimiters.
    """
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(
            "Malformed SKILL.md: opens with `---` frontmatter but has"
            " no closing `---` delimiter."
        )
    return text[4:end], text[end + len("\n---\n") :]


def _yaml_get(frontmatter: str, key: str) -> str | None:
    """Read a single top-level scalar value from a YAML frontmatter block.

    Intentionally minimal — lvkit's SKILL.md frontmatter only uses flat
    string fields, so this avoids dragging in PyYAML for one lookup.
    Strips surrounding double quotes if present so the helper round-trips
    with ``_yaml_quote``.
    """
    prefix = f"{key}:"
    for line in frontmatter.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                # Strip the wrapping quotes and unescape the same minimal
                # set _yaml_quote escapes.
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return value
    return None
