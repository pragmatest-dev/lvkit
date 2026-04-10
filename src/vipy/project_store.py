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
from typing import Any

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
# vipy ships user-facing skill templates as package data under
# `src/vipy/skill_templates/vipy-<name>/SKILL.md`. The functions below
# install them into a downstream user's project so the user's LLM
# editor (Claude Code, Copilot, Cursor) can run vipy's workflows.
#
# Two install targets:
#
# - install_claude_skills(): copies SKILL.md files to .claude/skills/
#   one-for-one. Claude Code reads them as auto-launchable skills.
#
# - install_copilot_skills(): writes per-workflow .github/prompts/<name>.
#   prompt.md files for explicit /<name> invocation, plus a single
#   .github/instructions/vipy.instructions.md router that auto-loads
#   into every chat with a short list of available prompts. This dual
#   pattern matches Claude Code's auto + explicit skill semantics.

# Marker comment vipy embeds in every generated Copilot file so users
# (and re-installs) can recognize a vipy-managed file.
_COPILOT_MANAGED_MARKER = (
    "<!-- vipy: managed by `vipy init --skills`, "
    "re-running will overwrite this file -->"
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
    "vipy-describe",
    "vipy-convert",
    "vipy-resolve-primitive",
    "vipy-resolve-vilib",
    "vipy-idiomatic",
]


def _iter_skill_templates() -> list[Any]:
    """Yield the skill template directories as Traversable objects.

    Hides the package-data plumbing (importlib.resources, filtering of
    package-internal directories like __pycache__) from callers.
    """
    template_root = files("vipy.skill_templates")
    return sorted(
        (
            d for d in template_root.iterdir()
            if d.is_dir() and not d.name.startswith(("_", "."))
        ),
        key=lambda d: (
            _SKILL_ORDER.index(d.name) if d.name in _SKILL_ORDER
            else len(_SKILL_ORDER),
            d.name,
        ),
    )


def install_claude_skills(target_dir: Path, force: bool = False) -> list[Path]:
    """Install vipy's Claude Code skills into a project.

    Copies every packaged template under
    `src/vipy/skill_templates/<name>/SKILL.md` into
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
        _stage_write(dest, template_file.read_text(), force, plan, conflicts)

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
        dest.write_text(new_content)
        written.append(dest)

    return written


def install_copilot_skills(
    target_dir: Path, force: bool = False
) -> list[Path]:
    """Install vipy's workflows for GitHub Copilot.

    Writes two file types under `<target_dir>/.github/`:

    - `prompts/<name>.prompt.md` — one per workflow. User-invocable via
      `/<name>` slash command in Copilot Chat. Body is the SKILL.md body
      (frontmatter stripped); frontmatter is rewritten to Copilot's
      prompt schema (`mode`, `description`).
    - `instructions/vipy.instructions.md` — single router file. Auto-
      loaded into every Copilot chat (`applyTo: "**"`). Lists the five
      prompts and when to suggest each. Stays small so it doesn't
      bloat every chat.

    Why both: Claude Code skills are dual-mode (auto-loaded by
    description match AND explicit `/<name>` invocation). The Copilot
    equivalent is a router instruction (auto half) plus per-workflow
    prompts (explicit half). Together they give Copilot users the same
    dual-mode UX as Claude Code skills.

    Atomic with respect to local-edit detection: pre-validates every
    destination, collects conflicts, raises once before writing.

    Args:
        target_dir: Project root. `.github/prompts/` and
            `.github/instructions/` are created under this directory.
        force: Overwrite existing files even if they have local edits.

    Returns:
        List of paths that were written or overwritten. Empty when all
        files are already byte-identical to the templates.
    """
    plan: list[tuple[Path, str]] = []
    conflicts: list[Path] = []

    # Phase 1: build per-prompt content + router content, validate
    # all destinations, collect conflicts.
    skill_dirs = _iter_skill_templates()

    for skill_dir in skill_dirs:
        template_file = skill_dir.joinpath("SKILL.md")
        if not template_file.is_file():
            raise ValueError(
                f"Skill template directory {skill_dir.name!r} is missing"
                " its SKILL.md — packaging or sync error."
            )
        prompt_content = _build_copilot_prompt(template_file.read_text())
        dest = target_dir / ".github" / "prompts" / f"{skill_dir.name}.prompt.md"
        _stage_write(dest, prompt_content, force, plan, conflicts)

    router_content = _build_copilot_router(skill_dirs)
    router_dest = target_dir / ".github" / "instructions" / "vipy.instructions.md"
    _stage_write(router_dest, router_content, force, plan, conflicts)

    if conflicts:
        names = "\n  ".join(str(p) for p in conflicts)
        raise FileExistsError(
            f"{len(conflicts)} Copilot file(s) have local edits and would"
            f" be overwritten:\n  {names}\n"
            "Re-run with force=True to overwrite."
        )

    # Phase 2: commit all writes.
    written: list[Path] = []
    for dest, new_content in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content)
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

    Shared by ``install_claude_skills`` and ``install_copilot_skills``
    for their atomic phase splits: phase 1 calls this for every
    destination so phase 2 can either commit everything in one pass or
    raise a single error listing every conflict. Files that already
    match the new content are silently skipped (not added to plan,
    not flagged as conflicts).
    """
    if dest.exists():
        existing = dest.read_text()
        if existing == new_content:
            return  # already up to date
        if not force:
            conflicts.append(dest)
            return
    plan.append((dest, new_content))


def _build_copilot_prompt(skill_md: str) -> str:
    """Build a Copilot prompt file from a Claude SKILL.md.

    Rewrites the frontmatter from Claude's schema (``name``,
    ``description``, ``allowed-tools``) to Copilot's prompt schema
    (``mode``, ``description``). Body is preserved unchanged.

    The description is double-quoted in the output frontmatter so
    YAML special characters (``:``, ``#``, ``[``, ``{``, etc.) in the
    SKILL.md description don't break the prompt's frontmatter parser.
    """
    fm, body = _split_frontmatter(skill_md)
    description = _yaml_get(fm, "description") or ""
    return (
        "---\n"
        "mode: agent\n"
        f"description: {_yaml_quote(description)}\n"
        "---\n"
        f"{_COPILOT_MANAGED_MARKER}\n"
        "\n"
        f"{body.lstrip()}"
    )


def _yaml_quote(value: str) -> str:
    """Wrap a string in YAML double quotes, escaping inner quotes/backslashes.

    Minimal — handles the only YAML escapes vipy needs (`"` and `\\`).
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _build_copilot_router(skill_dirs: list[Any]) -> str:
    """Build the .github/instructions/vipy.instructions.md router.

    Lists every packaged skill in workflow order with its
    description, so Copilot can auto-suggest the right slash command
    when context matches. Stays small (under 30 lines + the warning
    marker) so it doesn't bloat every Copilot chat.
    """
    lines = [
        "---",
        'applyTo: "**"',
        "---",
        _COPILOT_MANAGED_MARKER,
        "",
        "# vipy: LabVIEW VI to Python workflows",
        "",
        "This project uses vipy for LabVIEW VI to Python conversion.",
        "Five workflows are available as prompts. Suggest the appropriate",
        "slash command when context matches; the user can also invoke",
        "them directly:",
        "",
    ]
    for skill_dir in skill_dirs:
        skill_md = skill_dir.joinpath("SKILL.md").read_text()
        fm, _ = _split_frontmatter(skill_md)
        description = _yaml_get(fm, "description") or skill_dir.name
        lines.append(f"- `/{skill_dir.name}` — {description}")
    lines.extend(
        [
            "",
            "Run `vipy init --skills copilot` to refresh these prompts and",
            "this router file.",
            "",
        ]
    )
    return "\n".join(lines)


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
    return text[4:end], text[end + len("\n---\n"):]


def _yaml_get(frontmatter: str, key: str) -> str | None:
    """Read a single top-level scalar value from a YAML frontmatter block.

    Intentionally minimal — vipy's SKILL.md frontmatter only uses flat
    string fields, so this avoids dragging in PyYAML for one lookup.
    Strips surrounding double quotes if present so the helper round-trips
    with ``_yaml_quote``.
    """
    prefix = f"{key}:"
    for line in frontmatter.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            if (
                len(value) >= 2
                and value.startswith('"')
                and value.endswith('"')
            ):
                # Strip the wrapping quotes and unescape the same minimal
                # set _yaml_quote escapes.
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return value
    return None
