# VS Code extension

```bash
code --install-extension pragmatest.lvkit
```

**LVKit** is a VS Code extension that views and diffs `.vi` block diagrams
directly in the editor, on a machine with no LabVIEW installed — the VS Code
surface for the same reading lvkit's CLI and MCP server do. It bundles a
signed standalone `lvkit` binary, so it works with no Python, uv, or pip
installed.

Install it from the
[VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=pragmatest.lvkit)
(search "LVKit"), or with the command above. To point the extension at a
different `lvkit` — your own build, or a project's `.venv` — set `lvkit.path`
in Settings; unset, it prefers a lvkit checkout's own `.venv`/`uv run lvkit`
when you're developing inside one, then falls back to the bundled binary.

## What it surfaces

The extension is three things layered on the same bundled binary, each a
thin VS Code front end for a feature that also exists as a plain `lvkit`
command:

| Surface | Trigger | Reference |
|---|---|---|
| **View** | Open any `.vi` in the Explorer | [render](render.md) — the block-diagram SVG the custom editor (`lvkit.viPreview`) renders inline, with zoom/pan and a light/dark diagram-theme toggle, instead of the binary-file notice. |
| **Open Visual Diff** | Right-click a changed `.vi` in Source Control or the Explorer, or a `.vi`'s entry in the Timeline | [diff](diff.md) — the interactive before/after diff viewer (`lvkit.diffVI`). Git-history-aware: a working-tree file diffs against `HEAD`; a commit from Source Control or the Timeline diffs against its parent, so it shows that commit's own changes rather than repeating `HEAD` against itself. |
| **Zero-config MCP** | Automatic once the extension is installed | [mcp](mcp.md) — the extension auto-registers its bundled `lvkit mcp` server as an MCP server definition provider, so VS Code agent mode gets the full lvkit tool surface with nothing to install or configure. Requires VS Code ≥ 1.101 (the version where the MCP provider API is stable); on an older VS Code the extension still loads and View/Diff still work, the registration just no-ops. |

Both View and Open Visual Diff open read-only in an editor tab. A node the
renderer doesn't have a glyph for yet draws as a labeled box instead of
failing.

## Settings

| Setting | Default | Description |
|---|---|---|
| `lvkit.path` | `lvkit` | Path to the `lvkit` executable. Leave as `lvkit` to auto-resolve: a repo-local `.venv` → `uv run lvkit` (when developing inside a lvkit checkout) → the bundled binary → `lvkit` on `PATH`. An absolute-path override is ignored in an untrusted workspace. |
| `lvkit.searchPaths` | `[]` | Extra directories to search for SubVIs, in addition to the auto-detected repository root — see [SubVI & vi.lib resolution](subvi-resolution.md). |
| `lvkit.diagramTheme` | `auto` | Color theme for the rendered diagram itself (`auto`/`light`/`dark`). The surrounding viewer chrome always follows your VS Code theme; this setting controls only the diagram, and updates automatically when you cycle the in-viewer theme control. |

## Requirements

- **VS Code ≥ 1.101.** Below that, View and Open Visual Diff still work; the
  zero-config MCP registration no-ops.
- **A trusted workspace.** The extension runs a local `lvkit` executable
  against your `.vi` files, so VS Code disables it in Restricted Mode — a
  `.vi` opens as a binary file and the LVKit commands are missing until you
  choose **Trust** from the Restricted Mode banner. Virtual workspaces (e.g.
  `vscode.dev`) aren't supported, for the same reason. In an untrusted-but-not-restricted
  workspace, View and Open Visual Diff still run using the extension's own
  bundled binary; only the `lvkit.path` override — which would run an
  executable the workspace names — is ignored.

## Trademarks

LVKit is an independent, clean-room project, not affiliated with, authorized
by, endorsed by, or sponsored by NI. LabVIEW, NI, and National Instruments
are trademarks of National Instruments Corporation, used only to identify the
file format LVKit reads.

## See also

- [install](install.md) — the other install paths (Claude Desktop, the Claude Code plugin, and hand-written MCP config files).
- [render](render.md) — the block-diagram renderer behind View.
- [diff](diff.md) — the VI diff engine behind Open Visual Diff.
- [mcp](mcp.md) — the MCP server the extension auto-registers, and its full tool list.
