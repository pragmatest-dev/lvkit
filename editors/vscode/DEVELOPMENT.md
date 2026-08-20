# LVKit VS Code extension — development

Contributor and maintainer notes. User-facing docs are in [README.md](README.md).
## How the extension finds lvkit

The extension resolves which `lvkit` to run, in order: an explicit **`lvkit.path`**
setting (**ignored in an untrusted workspace** — it names an executable to run) → the
repo's own `.venv/bin/lvkit` (`.venv\Scripts\lvkit.exe` on Windows) → `uv run lvkit`
when the repo has a `pyproject.toml`/`uv.lock` and `uv` is on `PATH` → **the bundled
binary** (`bin/lvkit/lvkit[.exe]`) → a global `lvkit`. The first three let developers
working inside the LVKit repo use their own latest build; everyone else falls through
to the bundled binary automatically.

The bundled binary is a **PyInstaller onedir** standalone build (`build/build-binary.sh`
+ `build/lvkit_entry.py`) — no Python install required on the user's machine. Earlier
0.1.8/0.1.9 releases replaced this with a bundled `uv`/managed-Python runtime because the
unsigned PyInstaller `.exe` was blocked by Windows Device Guard / Smart App Control; that
is now solved by **signing** the Windows binary (every `.exe`/`.dll`/`.pyd` under
`bin/lvkit/`) with Azure Artifact Signing in CI (`win32-x64` builds on a
`windows-latest` runner specifically for this — Authenticode signing cannot run on
Linux). macOS/Linux binaries ship unsigned (mac notarization is a separate, later item).

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

Through **0.1.11** the extension versioned on its own track (independent of the
LVKit library). Since **0.6.0** its `version` is kept **in lockstep with the
LVKit library** — both are bumped together to the same number each release (a
`0.6.x` extension bundles `0.6.x` lvkit; see the release runbook / repo `CLAUDE.md`
"align every version site"). The `MIN_LVKIT` constant in `extension.js` still
gates the minimum bundled library a build accepts.

## Building the bundled binary

The `bin/` binary is **not committed** (git-ignored, ~70 MB, per-platform). Build it
locally (needed once for F5 testing) or in CI at release time:

```bash
uv pip install pyinstaller                     # once
editors/vscode/build/build-binary.sh           # -> editors/vscode/bin/lvkit/lvkit
```

PyInstaller is platform-specific, so run it on **each** of macOS / Windows / Linux to
produce that OS's binary; ship one platform-specific `.vsix` per target. The script
builds against whatever `lvkit` is installed in the active environment (`uv pip install
-e .` picks up the repo's own `pyproject.toml` version).

**Windows signing**: `windows-latest` in CI signs every `.exe`/`.dll`/`.pyd` under
`bin/lvkit/` with the `azure/artifact-signing-action` (Azure Artifact Signing,
`pragmatest` account / `pragmatest-public-trust` certificate profile) before packaging.
This can only run on a Windows runner (Authenticode/`signtool` is Windows-only) — it
cannot be reproduced locally on Linux/macOS dev machines.

## Publishing to the Marketplace

You need a **publisher** on the VS Code Marketplace and a token:

1. Create an **Azure DevOps** organization (free) and a **Personal Access Token** with
   **Marketplace → Manage** scope.
2. Create/claim the `pragmatest` publisher: `npx @vscode/vsce create-publisher pragmatest`
   (or via the Marketplace management page), then `npx @vscode/vsce login pragmatest`.
3. Package + publish **per platform** (each carries its own `bin/`):
   `npx @vscode/vsce publish --target linux-x64` (and `win32-x64`, `darwin-x64`,
   `darwin-arm64`). CI does this on an extension tag, one job per platform runner — see
   `.github/workflows/publish-bundles.yml`, which builds the binary once per platform and
   publishes every form (VSIX, standalone zip, Claude Code plugin archive, Claude Desktop
   `.mcpb`).

The bundled binary means end users need nothing installed; developers inside the LVKit
repo still get their own build via the `.venv`/`uv run` resolution.
