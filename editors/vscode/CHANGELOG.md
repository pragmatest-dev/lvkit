# Changelog

The extension versions on its **own** track, independent of the LVKit library.

## [0.1.4]

- **macOS ships.** macOS PyInstaller emits a `Python.framework` bundle, and
  Apple's framework layout is built on symlinks (`Versions/Current -> 3.14`,
  `Resources -> Versions/Current/Resources`). `vsce`'s secret scanner follows
  every entry and reads it; on a symlink pointing at a *directory* that raises
  an unhandled `EISDIR`, reported only as an empty
  `Error occurred while scanning secrets (files):`. It was a crash, not a
  detection — which is why permitting secrets never helped. Those symlinks are
  now materialized into real directories at build time (they can't simply be
  dropped: `Versions/Current` is what the macOS loader resolves through).
- Each platform now builds **and packages** its own `.vsix`; publishing is a
  separate job that only uploads. All four packages must exist before anything
  is published, so a release can no longer go out half-finished.

## [0.1.3]

- **macOS builds publish.** The release now fans out to per-OS build jobs that
  upload their binary as an artifact, then fans back in to a single Linux job
  that packages and publishes every platform target. Nothing requires a VSIX to
  be built on the OS it targets, and this routes around a `vsce` failure that
  only occurs on macOS runners.
- Releases are now **atomic** — one failed build publishes nothing, rather than
  leaving some platforms live and others missing.
- Listing screenshots are no longer shipped inside the package (~380 KB saved);
  they're referenced from the repository.

## [0.1.2]

- **macOS builds publish again.** `vsce` runs a local secret scan before upload,
  and on both macOS targets it aborted with an empty
  `Error occurred while scanning secrets (files):` — no secret type, no file —
  before contacting the Marketplace at all. Linux and Windows scanned the same
  source without complaint; only the Mach-O binary triggered it. Publishing now
  passes `--allow-package-all-secrets`.

## [0.1.1]

- **macOS builds restored.** The release workflow targeted `macos-13`, which
  GitHub retired on 2025-12-04 — those jobs queued forever and never ran — and
  `macos-14`, which is itself deprecated (unsupported after 2026-11-02). Now on
  `macos-15` (arm64) and `macos-15-intel` (x86_64).
- **Branding** — the extension is **LVKit** (the wordmark form) throughout the
  UI: Settings section, Command Palette category, and editor picker. Lowercase
  `lvkit` now appears only where it genuinely is an identifier — the CLI
  executable, setting/command ids, and the PyPI/git names. Marketplace banner
  corrected to the brand slate (`#2b3038`).

## [0.1.0] — first release

Read LabVIEW `.vi` files directly in VS Code, with **neither LabVIEW nor Python
installed**.

- **VI preview** — a `.vi` opens as a rendered block diagram instead of the
  "binary file" notice. Interactive viewer with zoom/pan and a light/dark
  diagram-theme toggle; your choice persists to the `lvkit.diagramTheme`
  setting.
- **Visual diff** — right-click a changed `.vi` (Source Control or Explorer) →
  **LVKit: Open Visual Diff** for the interactive before/after viewer:
  cross-fade, change list, and highlights tied to the diagram.
- **Works in VS Code's native diff too** — clicking a changed `.vi` in Source
  Control shows the two versions side by side, each rendered as a diagram.
- **Self-contained** — ships a standalone `lvkit` binary for your platform, so it
  works out of the box. If you're developing inside the LVKit repo it prefers
  your repo's own `lvkit` (`.venv` → `uv run lvkit`) so you always see current
  code; `lvkit.path` overrides everything.
- **Extra SubVI search paths** via `lvkit.searchPaths` (the repository root is
  detected automatically).

### Requirements

- **A trusted workspace.** The extension runs a local `lvkit` executable against your
  `.vi` files, so VS Code disables it in Restricted Mode. Choose *Trust* if a `.vi`
  opens as a binary file and the LVKit commands are missing. Virtual workspaces
  (e.g. `vscode.dev`) are not supported for the same reason.

### Known limitations

- Rendering coverage grows with the LVKit library; a node LVKit doesn't
  recognize yet is drawn as a labeled box rather than its LabVIEW glyph.
