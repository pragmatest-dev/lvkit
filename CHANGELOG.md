# Changelog

lvkit follows semantic versioning.

## [0.5.0]
- **Diff:** `lvkit diff <a.vi> <b.vi>` — compare two VIs (terminals, operations,
  wiring) as a text diff or `--long` change report, plus an interactive HTML diff
  viewer with per-change spotlight and synced before/after panes.
- **Netlist:** a node-first text projection of a VI, shared by `describe
  --verbose`, `diff`'s text output, and the viewer's change tree.
- **MCP server:** `lvkit mcp` exposes the VI graph to AI agents (describe, render,
  diff, generate, and more) over the Model Context Protocol.
- **Project-local cache:** extraction XML is cached under `.lvkit/cache/`
  (content-hash invalidated, path-classified) instead of beside the `.vi`.
- **Auto SubVI resolution:** every command that resolves SubVIs
  (`describe`/`diff`/`generate`/`docs`/`render`/`visualize`) auto-detects the
  project root (nearest enclosing `.lvkit/`) and searches it, so project
  dependencies resolve with no `--search-path`.
- **`structure` accepts a `.lvproj`** — discovers the project from its declared
  member list (summary, `--json`, `--plan`).
- Numeric constants honor their LabVIEW display format in `describe`/`diff`/
  `netlist` (hex constants render as `x…`).
- lvkit is **read-only** on VIs — stated explicitly across every capability,
  including convert (it parses the VI and emits a separate file, never editing it).
- More resolved primitives; Merge Errors fix; extraction memory now bounded.

## [0.4.0]
- ***Block-diagram renderer:** `lvkit render <vi> -o out.svg` — produces a headless
  block-diagram SVG with interactive frames and procedural primitive shapes.
- Known limitation: a standalone `.vi` may under-resolve types (e.g. cluster
  field names) — render with `--search-path` / a project for full fidelity.

## [0.3.0]
- Formula Node support with LabVIEW-validated numeric semantics.

## [0.2.0]
- Published to PyPI; `lvkit setup`, visualization extra.

## [0.1.0]
- Initial release.
