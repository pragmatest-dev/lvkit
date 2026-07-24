# Changelog

The extension versions on its **own** track, independent of the LVKit library.

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
