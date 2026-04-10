"""Project-local resolution store discovery and initialization.

vipy ships as cleanroom — its `data/` directory contains only mappings derived
from public documentation. A project-local `.vipy/` directory lets users with
LabVIEW licenses contribute their own mappings (potentially derived from
licensed sources) without contaminating vipy's shipment.

The store is a parallel layout to `data/`:

    .vipy/
      README.md                 # license-boundary explainer
      primitives-codegen.json   # primitive overrides
      vilib/
        _index.json
        <category>.json
      openg/
      drivers/

Resolvers load `.vipy/` first, then fall back to shipped `data/`.
"""

from __future__ import annotations

import json
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
      primitives-codegen.json   # primitive ID → name, terminals, python_code
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
