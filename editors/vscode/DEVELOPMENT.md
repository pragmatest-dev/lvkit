# LVKit VS Code extension — development

Contributor and maintainer notes. User-facing docs are in [README.md](README.md).
## How the extension finds lvkit

The extension resolves which `lvkit` to run, in order:

1. an explicit **`lvkit.path`** setting — a developer pointing at their own build.
   This is the ONLY way an ambient/local lvkit is used; we never auto-discover one,
   because the extension can only guarantee its behavior at the **pinned** version.
2. **`uv run --no-project --with lvkit==<LVKIT_PIN> python -m lvkit`** using the
   bundled `uv` (`bin/uv/uv.exe`, else a `uv` already on `PATH`). This is the default.
3. a bare `lvkit` on `PATH` — last resort.

Why uv + a module instead of a bundled `lvkit.exe`: on Windows, **Device Guard /
Smart App Control** blocks an unsigned, zero-reputation PyInstaller exe. `uv` is a
signed, high-reputation binary those policies allow; running lvkit as a **module**
(`python -m lvkit`) means no `lvkit.exe` is ever created or executed. uv provisions a
managed Python + the pinned lvkit on first use (network once, cached after), so a
LabVIEW user needs no Python installed. `LVKIT_PIN` in `extension.js` fixes the exact
version so the extension's advertised behavior is reproducible — bump it (to a version
already on **PyPI**) when the extension depends on a newer lvkit.

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

## Bundling uv

`bin/uv/` is **not committed** (git-ignored, ~76 MB, per-platform). Fetch it before
packaging (or for F5 testing):

```bash
editors/vscode/build/fetch-uv.sh win32-x64     # -> editors/vscode/bin/uv/uv.exe
# also: linux-x64 | darwin-x64 | darwin-arm64 | win32-arm64 | linux-arm64
```

uv is platform-specific, so fetch the matching binary for **each** target and ship one
`.vsix` per platform (e.g. `npx @vscode/vsce package --target win32-x64`). The win32
`.vsix` can be built from WSL/Linux — vsce just bundles whatever `bin/uv/uv.exe` is
present, so the fetched **Windows** uv.exe is what matters, not the build host.

(The old PyInstaller path — `build/build-binary.sh` → `bin/lvkit/` — is superseded by
uv and no longer used; see "How the extension finds lvkit".)

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

