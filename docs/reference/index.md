# CLI Reference

Every `lvkit` command, with its arguments, options, and a worked example. Each
page documents one command exhaustively; this page is the map.

Run `lvkit <command> --help` at any time for the same argument listing offline.

## Understand a VI

Read-only commands that turn a `.vi` binary into something you can read, review,
or search. None of these require a primitive or [vi.lib mapping](subvi-resolution.md)
to be resolved first.

- [describe](describe.md) — human-readable signature, inputs/outputs, operations, and control flow.
- [structure](structure.md) — inspect the members of a `.lvlib` or `.lvclass`.
- [render](render.md) — a faithful, interactive block-diagram SVG.
- [visualize](visualize.md) — an interactive dataflow or dependency graph across a whole library.
- [docs](docs.md) — cross-referenced HTML documentation for a VI, library, or class.

## Track changes

- [diff](diff.md) — compare two versions of a VI: terminals, operations, and wiring.

## Convert

- [generate](generate.md) — deterministically generate Python from a VI, library, or
  class. Unlike the commands above, `generate` requires every primitive and vi.lib
  call it hits to resolve — see [Unresolved calls](generate.md#unresolved-calls).

## Set up & integrate

- [setup](setup.md) — install AI agent skills and create the project-local `.lvkit/` store.
- [detect](detect.md) — find a locally installed LabVIEW and its `vi.lib` / `user.lib`.
- [mcp](mcp.md) — run the MCP server so an AI agent can query VIs interactively.

## Shared options

- [SubVI & vi.lib resolution](subvi-resolution.md) — the `--search-path`, `--vilib`, `--userlib`, `--project-root`, and `--no-auto-vilib` flags shared by `describe`, `generate`, `docs`, `visualize`, `diff`, and `render`, explained once.
