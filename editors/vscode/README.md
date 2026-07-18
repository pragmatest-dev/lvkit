# lvkit — LabVIEW VI Viewer & Diff (VS Code)

See LabVIEW `.vi` block diagrams and **visual before/after diffs** directly in VS Code —
**no LabVIEW license required**. Powered by [lvkit](https://pragmatest.com).

Instead of *"this is a binary file,"* a `.vi` opens as a rendered block diagram, and a
changed `.vi` in Source Control opens as an interactive before/after diff (cross-fade,
change list, highlights).

## Features

- **VI preview** — open any `.vi` in the Explorer and it renders (read-only) instead of
  the binary-file notice, in an interactive viewer with zoom/pan and a light/dark
  diagram-theme toggle. Your theme choice is remembered (persisted to the
  `lvkit.diagramTheme` setting).
- **Visual diff** — right-click a changed `.vi` (Source Control or Explorer) →
  **lvkit: Open Visual Diff** → the interactive before/after viewer in an editor tab
  (same diagram-theme control).
- **Convert VI to Python (beta)** — right-click a `.vi` (Explorer, editor tab, or
  Source Control) → **lvkit: Convert VI to Python (beta)**, or run it from the Command
  Palette on the active `.vi`. Generates Python and opens it. Coverage of LabVIEW nodes
  is still growing: unrecognized nodes are emitted as inline-raise **stubs** for you to
  review (you'll be told how many), and a VI that can't be fully converted yet reports
  which node lvkit doesn't know about — so treat the output as a starting point, not a
  finished port.

All features use only **stable** VS Code APIs, so this can ship to the Marketplace as-is.

## Requirements

- **lvkit ≥ 0.5.0** installed and reachable (Python 3.10+). The extension resolves
  lvkit automatically, in order: an explicit **`lvkit.path`** setting → the repo's own
  `.venv/bin/lvkit` (`.venv\Scripts\lvkit.exe` on Windows) → `uv run lvkit` when the
  repo has a `pyproject.toml`/`uv.lock` and `uv` is on `PATH` → a global `lvkit`. Set
  **`lvkit.path`** to an absolute path only if none of those apply. If the resolved
  lvkit is older than 0.5.0 you'll get a one-time upgrade prompt
  (`pip install -U lvkit`).

## Settings

| Setting | Default | Description |
|---|---|---|
| `lvkit.path` | `lvkit` | Path to the `lvkit` executable. Leave as `lvkit` to auto-resolve (repo `.venv` → `uv run lvkit` → global). |
| `lvkit.searchPaths` | `[]` | Extra dirs to search for SubVIs (repo root is auto-detected). |
| `lvkit.diagramTheme` | `auto` | Diagram color theme (`auto`/`light`/`dark`); updated automatically when you cycle the in-viewer theme control. |

## Try it locally (dev host)

```bash
# Prepare a JKI VI Tester repo with a one-file change to diff:
./setup-jki-demo.sh            # clones + sets up + writes .vscode/settings.json
```

1. Open **this folder** (`editors/vscode`) in VS Code and press **F5** — an
   *Extension Development Host* window launches with the extension loaded.
2. In that window, **File → Open Folder →** the demo repo `setup-jki-demo.sh` printed.
3. **Explorer:** click any `.vi` → it renders.
4. **Source Control:** right-click the changed `run.vi` → **lvkit: Open Visual Diff**.

## Versioning

The extension versions on its **own** track (independent of the lvkit library) —
`0.1.0` is the first real release. It requires **lvkit ≥ 0.5.0** (constant `MIN_LVKIT`
in `extension.js`); bump that constant when a feature needs a newer library, and bump
the extension `version` in `package.json` per its own release cadence.

## Before publishing to the Marketplace

- Set a real `publisher` (create one: `vsce create-publisher`), add `repository`/`icon`.
- `npx @vscode/vsce package` → `.vsix`; `npx @vscode/vsce publish` (needs a PAT).
- The repo-local / `uv run` resolution means users working inside a lvkit checkout
  usually need no `lvkit.path`; end users still need lvkit ≥ 0.5.0 on `PATH` (or the
  setting).

## Not yet (needs proposed API)

Opening the diff on a plain **left-click** of a changed file (like a text diff) needs
VS Code's proposed `resolveCustomDiffEditor` API, which can't ship to the Marketplace
yet. The right-click command is the shippable path today.
