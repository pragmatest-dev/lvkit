# CLI Reference

Every `lvkit` command, with its arguments, options, and a worked example. Each
page documents one command exhaustively; this page is the map.

Run `lvkit <command> --help` at any time for the same argument listing offline.

## Reads, never writes

lvkit only ever reads a VI — it never modifies, re-saves, or edits one. Your files are never touched, and LabVIEW stays the only thing that authors them.

**Does lvkit modify my VIs?** No. It's strictly read-only. This holds for every command, including [`generate`](generate.md): converting a VI parses it and emits a *separate* Python file — it never edits the source `.vi`.

## Understand a VI

Read-only commands that turn a `.vi` binary into something you can read, review,
or search. None of these require a primitive or [vi.lib mapping](subvi-resolution.md)
to be resolved first.

- [describe](describe.md) — human-readable signature, inputs/outputs, operations, and control flow.
- [structure](structure.md) — inspect the members of a `.lvlib` or `.lvclass`.
- [render](render.md) — a faithful, interactive block-diagram SVG.
- [visualize](visualize.md) — an interactive dataflow or dependency graph across a whole library.
- [docs](docs.md) — cross-referenced HTML documentation for a VI, library, or class.

## Query across a repo

Index a repo once (`lvkit index`, though the commands below build/refresh it for
you), then ask project-wide questions in one call — no per-VI round trips, no
same-name collision loss.

- [query](query.md) — read-only SQL over the repo's index (views: `vi`,
  `terminal`, `constant`, `node`, `type_use`, the type catalog `type` / `type_field` /
  `type_item` / `vi_used_type`, the `.ctl` controls `typedef*`, `class_fact`, `lvproj`); get back
  the answer (a `GROUP BY` histogram), not a dump. Also documents `lvkit index`.
- [callers / callees / blast-radius](callers.md) — call graph & change impact:
  who calls a VI, what it calls, and what breaks if you change it.

## Track changes

- [diff](diff.md) — compare two versions of a VI: terminals, operations, and wiring.

## Convert

- [generate](generate.md) — deterministically generate Python from a VI, library, or
  class. What it cannot convert — an unknown primitive or vi.lib call, for
  example — is a warning and a `raise NotImplementedError(...)` at that node; see
  [Gaps](generate.md#gaps).

## Set up & integrate

- [setup](setup.md) — install AI agent skills and create the project-local `.lvkit/` store.
- [detect](detect.md) — find a locally installed LabVIEW and its `vi.lib` / `user.lib`.
- [mcp](mcp.md) — run the MCP server so an AI agent can query VIs interactively.
- [install](install.md) — get the MCP server in front of your agent: one-click bundles for Claude Desktop/VS Code/Claude Code, or a hand-written config file.
- [vscode-extension](vscode-extension.md) — the VS Code extension: view and diff a `.vi` in the editor, with zero-config MCP.
- [cache](cache.md) — where lvkit keeps unpacked VIs, how to move it with `LVKIT_CACHE_DIR`, and what to exclude under antivirus.

## Shared options

- [SubVI & vi.lib resolution](subvi-resolution.md) — the `--search-path`, `--vilib`, `--userlib`, `--project-root`, and `--no-auto-vilib` flags shared by `describe`, `generate`, `docs`, `visualize`, `diff`, and `render`, explained once.
- [Netlist](netlist.md) — the node-first text grammar shared by `describe --verbose`'s `## Netlist` section, `diff`'s text output, and the interactive diff viewer's Tree change list.

---

*lvkit is an independent, clean-room project, not affiliated with, authorized by, endorsed by, or sponsored by NI. LabVIEW, NI, and National Instruments are trademarks of National Instruments Corporation, used only to identify the file format lvkit reads.*
