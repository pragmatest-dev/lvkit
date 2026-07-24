# LVKit VS Code extension — development

Contributor and maintainer notes. User-facing docs are in [README.md](README.md).
## How the extension finds lvkit

The extension resolves which `lvkit` to run, in order: an explicit **`lvkit.path`**
setting → the repo's own `.venv/bin/lvkit` (`.venv\Scripts\lvkit.exe` on Windows) →
`uv run lvkit` when the repo has a `pyproject.toml`/`uv.lock` and `uv` is on `PATH` →
**the bundled binary** (`bin/lvkit/lvkit`) → a global `lvkit`. The first three let
developers working inside the LVKit repo use their own latest build; everyone else
falls through to the bundled binary automatically. Set **`lvkit.path`** only to override.

## Try it locally (dev host)

```bash
# Prepare a JKI VI Tester repo with a one-file change to diff:
./setup-jki-demo.sh            # clones + sets up + writes .vscode/settings.json
```

1. Open **this folder** (`editors/vscode`) in VS Code and press **F5** — an
   *Extension Development Host* window launches with the extension loaded.
2. In that window, **File → Open Folder →** the demo repo `setup-jki-demo.sh` printed.
3. **Explorer:** click any `.vi` → it renders.
4. **Source Control:** right-click the changed `run.vi` → **LVKit: Open Visual Diff**.

## Versioning

The extension versions on its **own** track (independent of the LVKit library) —
`0.1.0` is the first real release. It requires **LVKit ≥ 0.5.0** (constant `MIN_LVKIT`
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

The bundled binary means end users need nothing installed; developers inside the LVKit
repo still get their own build via the `.venv`/`uv run` resolution.

