"""Project-local resolution store discovery and initialization.

vipy ships as cleanroom — its bundled data directory contains only
mappings derived from public documentation. A project-local `.vipy/`
directory lets users with LabVIEW licenses contribute their own mappings
(potentially derived from licensed sources) without contaminating vipy's
shipment.

The store is a parallel layout to vipy's bundled data:

    .vipy/
      README.md                 # license-boundary explainer
      primitives.json           # primitive overrides
      vilib/
        _index.json
        <category>.json
      openg/
      drivers/

Resolvers load `.vipy/` first, then fall back to vipy's bundled data.

This module also installs Claude Code skills and Copilot instructions
from the packaged templates so downstream users can run vipy's resolve
workflows in their own LLM-enabled editor.
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

PROJECT_STORE_DIR = ".vipy"

_README_TEMPLATE = """# .vipy/ — Project-local resolution store

This directory is your project's private mapping store for vipy. vipy ships
as a **cleanroom** tool — its bundled `data/` directory contains only
mappings derived from public documentation, never from LabVIEW source or
vi.lib internals.

If you have a LabVIEW license, you (or your LLM assistant) can populate this
directory with mappings derived from the real vi.lib, your own LabVIEW
sources, or third-party libraries you have rights to use. vipy reads
`.vipy/` **first** and only falls back to its shipped `data/` if no project
mapping exists.

## License boundary

**vipy itself never reads `.vipy/` into its own data files.** Anything you
put here stays in your project. Do not submit content from `.vipy/` upstream
to vipy unless it is independently cleanroom-derived.

If any file under `.vipy/` is derived from licensed material (vi.lib block
diagrams, NI driver source, etc.), consider gitignoring it so it does not
get committed to a public repository.

## Layout

    .vipy/
      primitives.json   # primitive ID → name, terminals, python_code
      vilib/
        _index.json             # category → filename
        <category>.json         # vi.lib VI mappings
      openg/                    # OpenG library mappings (same format)
      drivers/                  # NI driver mappings (same format)

The format mirrors vipy's shipped `data/` exactly. Copy an example from
`<vipy install>/data/` if you need a starting point.

## Populating it

When `vipy generate` hits an unknown primitive or vi.lib VI, it raises a
`PrimitiveResolutionNeeded` or `VILibResolutionNeeded` exception with full
diagnostic context — including the qualified path of the unknown VI. An
LLM with access to your LabVIEW sources can use this to author the
mapping and write it here.

If you use Claude Code, run `vipy init --skills claude` to install
resolution skills into your project's `.claude/skills/`. For Copilot or
Cursor, use `vipy init --skills copilot`.
"""


def find_project_store(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default CWD) looking for a .vipy/ directory.

    Stops at the first match, at a `.git` directory marker, or at the
    filesystem root. Returns the path to the .vipy/ directory, or None if
    no project store is found.

    Args:
        start: Directory to start the walk from. Defaults to CWD.

    Returns:
        Path to the .vipy/ directory, or None.
    """
    current = (start or Path.cwd()).resolve()

    for candidate in [current, *current.parents]:
        store = candidate / PROJECT_STORE_DIR
        if store.is_dir():
            return store
        # Stop at repo root — don't escape into a parent project's .vipy/
        if (candidate / ".git").exists():
            return None

    return None


def init_project_store(root: Path) -> Path:
    """Create .vipy/ under `root` with a template README.md.

    Idempotent: if .vipy/ already exists, leaves it alone but ensures the
    README is present (does not overwrite an existing README).

    Args:
        root: Directory under which to create .vipy/.

    Returns:
        Path to the created (or existing) .vipy/ directory.
    """
    store = root / PROJECT_STORE_DIR
    store.mkdir(exist_ok=True)

    readme = store / "README.md"
    if not readme.exists():
        readme.write_text(_README_TEMPLATE)

    # Create empty index files for each category dir so loaders find them.
    for subdir in ("vilib", "openg", "drivers"):
        sub = store / subdir
        sub.mkdir(exist_ok=True)
        index = sub / "_index.json"
        if not index.exists():
            index.write_text(json.dumps({"categories": {}}, indent=2) + "\n")

    return store


# ============================================================
# Skill installation
# ============================================================
#
# vipy ships Claude Code skill templates as package data under
# `src/vipy/skill_templates/claude/<name>/SKILL.md`. The two functions
# below copy them into a downstream user's project so the user's LLM
# editor (Claude Code, Copilot, Cursor) can run vipy's workflows.

# Marker comments wrap the vipy section in copilot-instructions.md so
# we can replace just our part on subsequent installs without touching
# the user's other instructions. The "managed by" hint warns users not
# to edit inside the block — anything between the markers is replaced
# on `vipy init --skills`.
_COPILOT_MARKER_START = (
    "<!-- vipy:resolve start "
    "— managed by `vipy init --skills`, edits inside this block "
    "will be replaced on re-install -->"
)
_COPILOT_MARKER_END = "<!-- vipy:resolve end -->"

# Logical workflow order for the Copilot file: understand → convert →
# resolve unknowns → refactor. Alphabetical order would bury describe-vi
# at position 2 and put convert first.
_SKILL_ORDER = [
    "describe-vi",
    "convert",
    "resolve-primitive",
    "resolve-vilib",
    "idiomatic",
]


def install_claude_skills(target_dir: Path, force: bool = False) -> list[Path]:
    """Install vipy's Claude Code skills into a project.

    Copies every packaged template under
    `src/vipy/skill_templates/claude/<name>/SKILL.md` into
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
    template_root = files("vipy.skill_templates")
    skills_dir = target_dir / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: discover and validate. Build the full work list and
    # collect any conflicts before touching disk.
    plan: list[tuple[Path, str]] = []  # (dest, new_content)
    conflicts: list[Path] = []

    for skill_dir in template_root.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith(("_", ".")):
            # Skip __pycache__, __init__-style dirs, hidden dirs.
            continue
        template_file = skill_dir.joinpath("SKILL.md")
        if not template_file.is_file():
            raise ValueError(
                f"Skill template directory {skill_dir.name!r} is missing"
                " its SKILL.md — packaging or sync error."
            )

        skill_name = skill_dir.name
        dest = skills_dir / skill_name / "SKILL.md"
        new_content = template_file.read_text()

        if dest.exists():
            existing = dest.read_text()
            if existing == new_content:
                # Already up to date — skip silently.
                continue
            if not force:
                conflicts.append(dest)
                continue

        plan.append((dest, new_content))

    if conflicts:
        names = "\n  ".join(str(p) for p in conflicts)
        raise FileExistsError(
            f"{len(conflicts)} skill file(s) have local edits and would"
            f" be overwritten:\n  {names}\n"
            "Re-run with force=True to overwrite."
        )

    # Phase 2: write everything. No conflicts means we can commit.
    written: list[Path] = []
    for dest, new_content in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content)
        written.append(dest)

    return written


def install_copilot_instructions(
    target_dir: Path, force: bool = False
) -> Path:
    """Install vipy's resolve workflows into copilot-instructions.md.

    Builds a single Markdown section by concatenating every packaged
    Claude skill template (frontmatter stripped, body wrapped in
    section headers), then writes or updates
    `<target_dir>/.github/copilot-instructions.md`. The vipy section
    is wrapped in marker comments so re-installing replaces just our
    section and leaves any other Copilot instructions intact.

    Anything inside the marker block is fully replaced on each install
    — the marker comments themselves warn the user not to edit there.
    Content outside the marker block is always preserved verbatim.

    Args:
        target_dir: Project root. `.github/copilot-instructions.md` is
            created under this directory.
        force: Skip the no-op short circuit. Without `force`, a
            re-install with no template changes early-returns without
            touching the file's mtime.

    Returns:
        A single Path to copilot-instructions.md (singular: all skills
        are merged into one file, unlike install_claude_skills which
        writes one file per skill and returns a list).
    """
    section = _build_copilot_section()
    dest = target_dir / ".github" / "copilot-instructions.md"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        existing = dest.read_text()
        if _COPILOT_MARKER_START in existing and _COPILOT_MARKER_END in existing:
            before, _, rest = existing.partition(_COPILOT_MARKER_START)
            _, _, after = rest.partition(_COPILOT_MARKER_END)
            new_content = before + section + after
            if new_content == existing and not force:
                return dest
        else:
            # First-time install into a file with other Copilot content.
            sep = "" if existing.endswith("\n") else "\n"
            new_content = existing + sep + "\n" + section
    else:
        new_content = section

    dest.write_text(new_content)
    return dest


def _build_copilot_section() -> str:
    """Concatenate all Claude skill templates into a Copilot section.

    Strips YAML frontmatter, wraps the result in marker comments and a
    top-level header so Copilot/Cursor can pick it up alongside the
    user's other instructions.

    Skills are emitted in logical workflow order (`_SKILL_ORDER`):
    understand → convert → resolve unknowns → refactor. Skills not in
    that list are appended in alphabetical order so adding a new
    template doesn't silently drop it.
    """
    template_root = files("vipy.skill_templates")

    skill_dirs = [
        d for d in template_root.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    ]
    skill_dirs.sort(
        key=lambda d: (
            _SKILL_ORDER.index(d.name) if d.name in _SKILL_ORDER else len(_SKILL_ORDER),
            d.name,
        )
    )

    parts: list[str] = [
        _COPILOT_MARKER_START,
        "# vipy: LabVIEW VI to Python workflows",
        "",
        "The following workflows come from vipy's Claude Code skills.",
        "They describe how to understand, convert, and resolve unknown",
        "primitives in LabVIEW VIs. The same instructions work for any",
        "LLM-aware editor.",
        "",
    ]

    for skill_dir in skill_dirs:
        template_file = skill_dir.joinpath("SKILL.md")
        if not template_file.is_file():
            raise ValueError(
                f"Skill template directory {skill_dir.name!r} is missing"
                " its SKILL.md — packaging or sync error."
            )
        body = _strip_frontmatter(template_file.read_text())
        parts.append(f"## Workflow: {skill_dir.name}")
        parts.append("")
        parts.append(body.strip())
        parts.append("")

    parts.append(_COPILOT_MARKER_END)
    return "\n".join(parts) + "\n"


def _strip_frontmatter(text: str) -> str:
    """Remove a leading `---\\n...\\n---\\n` YAML frontmatter block."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + len("\n---\n"):]
