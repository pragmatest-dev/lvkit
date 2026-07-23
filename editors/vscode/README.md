# lvkit — LabVIEW VI Viewer & Diff (VS Code)

See LabVIEW `.vi` block diagrams and **visual before/after diffs** directly in VS Code —
**no LabVIEW license required**. Powered by [lvkit](https://pragmatest.com).

An open-source tool from **[Pragmatest](https://pragmatest.com)**.

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

Both features use only **stable** VS Code APIs, so this can ship to the Marketplace as-is.

## Requirements

**None — no Python, no LabVIEW.** The extension ships a **bundled, standalone `lvkit`
binary** (a PyInstaller build with no Python dependency), so preview and diff work out
of the box.

The extension resolves which `lvkit` to run, in order: an explicit **`lvkit.path`**
setting → the repo's own `.venv/bin/lvkit` (`.venv\Scripts\lvkit.exe` on Windows) →
`uv run lvkit` when the repo has a `pyproject.toml`/`uv.lock` and `uv` is on `PATH` →
**the bundled binary** (`bin/lvkit/lvkit`) → a global `lvkit`. The first three let
developers working inside a lvkit checkout use their own latest build; everyone else
falls through to the bundled binary automatically. Set **`lvkit.path`** only to override.

## Workspace Trust

lvkit runs a local lvkit executable against the `.vi` files in your workspace, so it
requires a **trusted** workspace. In Restricted Mode VS Code disables the extension
entirely — no preview, no diff, and no `.vi` context-menu entries. If a `.vi` opens as
a binary file and the commands are missing, check the status bar for **Restricted
Mode** and choose *Trust* (Command Palette → **Workspaces: Manage Workspace Trust**).

For the same reason the extension does not run in virtual workspaces (e.g. `vscode.dev`),
which have no real filesystem to read `.vi` files from.

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

## Building the bundled binary

The `bin/` binary is **not committed** (git-ignored, ~70 MB, per-platform). Build it
locally (needed once for F5 testing) or in CI at release time:

```bash
uv pip install pyinstaller                     # once
editors/vscode/build/build-binary.sh           # -> editors/vscode/bin/lvkit/lvkit
```

PyInstaller is platform-specific, so run it on **each** of macOS / Windows / Linux to
produce that OS's binary; ship one platform-specific `.vsix` per target.

## Publishing to the Marketplace

You need a **publisher** on the VS Code Marketplace and a token:

1. Create an **Azure DevOps** organization (free) and a **Personal Access Token** with
   **Marketplace → Manage** scope.
2. Create/claim the `pragmatest` publisher: `npx @vscode/vsce create-publisher pragmatest`
   (or via the Marketplace management page), then `npx @vscode/vsce login pragmatest`.
3. Package + publish **per platform** (each carries its own `bin/`):
   `npx @vscode/vsce publish --target linux-x64` (and `win32-x64`, `darwin-x64`,
   `darwin-arm64`). CI does this on an extension tag — see
   `.github/workflows/publish-extension.yml`.

The bundled binary means end users need nothing installed; developers inside a lvkit
checkout still get their own build via the `.venv`/`uv run` resolution.

## Not yet (needs proposed API)

Opening the diff on a plain **left-click** of a changed file (like a text diff) needs
VS Code's proposed `resolveCustomDiffEditor` API, which can't ship to the Marketplace
yet. The right-click command is the shippable path today.

## Trademarks

lvkit is an independent, clean-room project, not affiliated with, authorized by,
endorsed by, or sponsored by NI. LabVIEW, NI, and National Instruments are
trademarks of National Instruments Corporation, used only to identify the file
format lvkit reads.
