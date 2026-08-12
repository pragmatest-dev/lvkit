# lvkit

lvkit reads, renders, documents, and diffs LabVIEW `.vi`/`.ctl`/`.lvclass`/`.lvlib` files — no LabVIEW license required. It can also generate a Python transliteration of a VI; that path is experimental and coverage is incremental.

```bash
pip install lvkit
lvkit describe "path/to/Some VI.vi"
```

That's the first win: a human-readable signature, inputs/outputs, constants, control flow, and the operations on the block diagram, printed straight from the `.vi` binary — see [reference/describe](reference/describe.md).

## Clean-room, by necessity

lvkit parses the VI binary itself — it does not open, script, or export from LabVIEW. Identifying a primitive, a `vi.lib` VI, or a type comes from the parsed graph, public NI documentation, and algorithm knowledge, never from a licensed LabVIEW install. This is why lvkit exists at all: reviewing, diffing, or documenting a VI no longer requires a LabVIEW seat.

lvkit is an independent, clean-room project, not affiliated with, authorized by, endorsed by, or sponsored by NI. LabVIEW, NI, and National Instruments are trademarks of National Instruments Corporation, used only to identify the file format lvkit reads.

## Install

```bash
pip install lvkit
```

For a global install: `pipx install lvkit` or `uv tool install lvkit`. `lvkit visualize` needs an extra: `pip install lvkit[visualize]`.

## Do something next

- [View a block diagram](how-to/view-a-block-diagram.md) — render a VI's block diagram to an interactive diagram and open it.
- [Compare two VIs](how-to/compare-two-vis.md) — diff two versions of a VI and read what changed.
- [Set up the MCP server](how-to/set-up-mcp.md) — give an AI agent live access to your VIs, instead of (or alongside) the CLI.
- [how-to/index](how-to/index.md) — all how-to guides.
- [reference/index](reference/index.md) — every `lvkit` command, exhaustively.

## See also

- [reference/mcp](reference/mcp.md) — the MCP server's tools and a worked project-understanding demo.
- [reference/render](reference/render.md) — the full `render` flag reference.
- [reference/diff](reference/diff.md) — the full `diff` flag reference.
