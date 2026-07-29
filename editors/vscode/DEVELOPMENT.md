# LVKit VS Code extension — development

Contributor and maintainer notes. User-facing docs are in [README.md](README.md).
## How the extension finds lvkit

The extension resolves which `lvkit` to run (the `importStrategy` idiom, like Ruff),
in order:

1. an explicit **`lvkit.path`** setting — a developer pointing at their own build.
   **Ignored in an untrusted workspace** (it names an executable to run).
2. **`lvkit.importStrategy`** (default `useBundled`; forced to `useBundled` when the
   workspace is untrusted):
   - `useBundled` — the extension's **own self-contained runtime**: the bundled
     python-build-standalone interpreter run as `<bin/python> -m lvkit` with
     `PYTHONPATH=<bin/libs>` (where lvkit + deps are pre-installed). No uv, no venv.
   - `fromEnvironment` — a `lvkit` already on `PATH` if present, else the bundle.

Why a bundled Python + a module instead of a bundled `lvkit.exe`: on Windows, **Device
Guard / Smart App Control** blocks an unsigned, zero-reputation PyInstaller exe — but
they evaluate the binary being **loaded** (`python.exe` + the `.pyd` files it imports),
not the launcher. Those are signed / high-reputation python-build-standalone + PyPI
binaries (the same ones uv merely spawned in 0.1.8), and running lvkit as a **module**
(`python -m lvkit`) means no `lvkit.exe` is ever created or executed. So 0.1.9 drops uv
from the runtime entirely: it **ships** the interpreter with lvkit pre-installed and
launches it directly — nothing is downloaded or assembled (0.1.8 fetched the runtime
from the network on first use). `LVKIT_PIN` in `extension.js` fixes the exact version;
bump it **and refetch the bundle** (`bin/libs` must match).

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

## Bundling the runtime

`bin/` is **not committed** (git-ignored, per-platform, ~250 MB). It holds the two
components of the ready-to-run runtime (approach B — no uv, no venv):

```
bin/python/python/   a python-build-standalone install_only CPython 3.12
bin/libs/            lvkit==<LVKIT_PIN> + its full dependency set, INSTALLED
```

Fetch both for a target before packaging (or for F5 testing):

```bash
editors/vscode/build/fetch-bundle.sh win32-x64   # Python + libs into bin/
# also: linux-x64 | darwin-x64 | darwin-arm64 | win32-arm64 | linux-arm64
```

`fetch-bundle.sh` downloads the pinned python-build-standalone tarball (`PBS_RELEASE` /
`PY_VERSION` in the script) and `pip install --target bin/libs`s lvkit + deps for the
target's platform tag(s). `pip install --target` unpacks wheels **without running** the
target interpreter (`--platform`/`--python-version`/`--implementation` select the
wheels), so a **win32** bundle builds fine from Linux. Everything is per-platform, so
fetch the matching bundle for **each** target and ship one `.vsix` per platform (e.g.
`npx @vscode/vsce package --target win32-x64`). vsce just bundles whatever is in `bin/`,
so the fetched **Windows** components are what matter, not the build host.

Because you can't run a win32 Python on Linux, prove the **runtime mechanism** on the
build host with a linux bundle: `fetch-bundle.sh linux-x64`, then
`PYTHONPATH=bin/libs bin/python/python/bin/python3 -m lvkit --version` → `lvkit <pin>`
(and a real `render … --format html` → exit 0). No uv, no venv anywhere.

(The old PyInstaller path — `build/build-binary.sh` → `bin/lvkit/` — is superseded and
no longer used; see "How the extension finds lvkit".)

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

