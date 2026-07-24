# LVKit — View and diff VIs without LabVIEW

See `.vi` block diagrams and **visual before/after diffs** directly in VS Code, on a
machine with **no LabVIEW installed**. Powered by [LVKit](https://pragmatest.com).

An open-source tool from **[PragmaTest](https://pragmatest.com)**.

Instead of *"this is a binary file,"* a `.vi` opens as a rendered block diagram, and a
changed `.vi` in Source Control opens as an interactive before/after diff (cross-fade,
change list, highlights).

![A .vi open in VS Code as a rendered block diagram](https://github.com/pragmatest-dev/lvkit/raw/main/editors/vscode/View.png)

*Click a `.vi` in the Explorer — it opens as a diagram instead of a binary-file notice.*

![Right-click a changed .vi and open the visual before/after diff](https://github.com/pragmatest-dev/lvkit/raw/main/editors/vscode/Diff.png)

*Right-click a changed `.vi` → **LVKit: Open Visual Diff** — before/after panes with a
numbered change list.*

## Features

- **VI preview** — open any `.vi` in the Explorer and it renders (read-only) instead of
  the binary-file notice, in an interactive viewer with zoom/pan and a light/dark
  diagram-theme toggle. Your theme choice is remembered (persisted to the
  `lvkit.diagramTheme` setting).
- **Visual diff** — right-click a changed `.vi` (Source Control or Explorer) →
  **LVKit: Open Visual Diff** → the interactive before/after viewer in an editor tab
  (same diagram-theme control).

## Requirements

**None — no Python, no LabVIEW.** The extension ships a **bundled, standalone `lvkit`
binary** (a PyInstaller build with no Python dependency), so preview and diff work out
of the box.

If you already have LVKit installed and want the extension to use it instead, point
**`lvkit.path`** at it. See [DEVELOPMENT.md](https://github.com/pragmatest-dev/lvkit/blob/main/editors/vscode/DEVELOPMENT.md) for the full resolution
order.

## Workspace Trust

LVKit runs a local `lvkit` executable against the `.vi` files in your workspace, so it
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

## Trademarks

LVKit is an independent, clean-room project, not affiliated with, authorized by,
endorsed by, or sponsored by NI. LabVIEW, NI, and National Instruments are
trademarks of National Instruments Corporation, used only to identify the file
format LVKit reads.
