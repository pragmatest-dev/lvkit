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

## Workspace Trust

LVKit runs a local executable against your `.vi` files, so it needs a **trusted**
workspace — in Restricted Mode VS Code disables it entirely. If a `.vi` opens as a
binary file and the LVKit commands are missing, choose **Trust** from the Restricted
Mode banner.

## Settings

| Setting | Default | Description |
|---|---|---|
| `lvkit.path` | `lvkit` | Path to the `lvkit` executable. Leave as `lvkit` to auto-resolve: project `.venv` → `uv run lvkit` → the bundled binary. |
| `lvkit.searchPaths` | `[]` | Extra dirs to search for SubVIs (repo root is auto-detected). |
| `lvkit.diagramTheme` | `auto` | Diagram color theme (`auto`/`light`/`dark`); updated automatically when you cycle the in-viewer theme control. |

## Trademarks

LVKit is an independent, clean-room project, not affiliated with, authorized by,
endorsed by, or sponsored by NI. LabVIEW, NI, and National Instruments are
trademarks of National Instruments Corporation, used only to identify the file
format LVKit reads.
