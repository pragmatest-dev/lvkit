# lvkit — LabVIEW VI Viewer & Diff (VS Code)

See LabVIEW `.vi` block diagrams and **visual before/after diffs** directly in VS Code —
**no LabVIEW license required**. Powered by [lvkit](https://pragmatest.com).

Instead of *"this is a binary file,"* a `.vi` opens as a rendered block diagram, and a
changed `.vi` in Source Control opens as an interactive before/after diff (cross-fade,
change list, highlights).

## Features

- **VI preview** — open any `.vi` in the Explorer and it renders (read-only) instead of
  the binary-file notice.
- **Visual diff** — right-click a changed `.vi` (Source Control or Explorer) →
  **lvkit: Open Visual Diff** → the interactive before/after viewer in an editor tab.

Both use only **stable** VS Code APIs, so this can ship to the Marketplace as-is.

## Requirements

- **lvkit** installed and reachable. Either put it on your `PATH` or set the
  **`lvkit.path`** setting to an absolute path (e.g. a project venv's
  `.venv/bin/lvkit`). lvkit needs Python 3.10+.

## Settings

| Setting | Default | Description |
|---|---|---|
| `lvkit.path` | `lvkit` | Path to the `lvkit` executable. |
| `lvkit.searchPaths` | `[]` | Extra dirs to search for SubVIs (repo root is auto-detected). |

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

## Before publishing to the Marketplace

- Set a real `publisher` (create one: `vsce create-publisher`), add `repository`/`icon`.
- `npx @vscode/vsce package` → `.vsix`; `npx @vscode/vsce publish` (needs a PAT).
- Consider bundling/pinning a lvkit resolution strategy so users don't have to set
  `lvkit.path` by hand.

## Not yet (needs proposed API)

Opening the diff on a plain **left-click** of a changed file (like a text diff) needs
VS Code's proposed `resolveCustomDiffEditor` API, which can't ship to the Marketplace
yet. The right-click command is the shippable path today.
