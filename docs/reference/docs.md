# docs

Generate cross-referenced static HTML documentation for a VI, library, or
class — one page per VI, linked to its callers and callees, with inputs,
outputs, operations, and wiring diagrams. Like `describe`, `docs` never
requires primitive or vi.lib mappings, so it works on any VI out of the box.

## Synopsis

```bash
lvkit docs <input_path> <output_dir> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `input_path` | Path to a `.vi`, `.lvlib`, `.lvclass`, or a directory. |
| `output_dir` | Output directory for the generated HTML files. |

## Options

| Option | Description |
|--------|-------------|
| `--load-mode MODE` | How deep to load dependencies: `full` (default), `minimal`, or `none`. `full` gives complete cross-references. `--load-mode none` generates pages only for the VIs in `input_path` — not for the SubVIs they call: their calls still show on the wiring diagram and in the Dependencies list, but without a link, since no page exists for them. |
| SubVI resolution flags | `--search-path`, `--project-root`, `--vilib`, `--userlib`, `--no-auto-vilib` — see [SubVI & vi.lib resolution](subvi-resolution.md). |

## Example

```bash
lvkit docs "Result Logger.lvclass" outputs/docs \
  --search-path samples/OpenG/extracted
```

This writes a set of cross-linked HTML pages to `outputs/docs/` — one per VI in
the class, each showing its signature, operations, and wiring diagram, with
links to any SubVI it calls and any caller that references it. Open
`outputs/docs/index.html` to start browsing.

## Notes

- Because pages are cross-linked, point `docs` at a whole `.lvlib`/`.lvclass`
  or a directory rather than a single VI — that's when the caller/callee
  links pay off.
- The same treatment applies to any SubVI lvkit can't resolve at all (not
  found on `--search-path`): it renders as a plain box on the diagram and a
  plain, unlinked entry in the Dependencies list — no page is generated for
  it either.

## See also

- [render](render.md) — a single VI's block diagram as one SVG.
- [structure](structure.md) — a quick text listing of a library or class's members.
- [generate](generate.md) — convert the same VI, library, or class to Python source instead of HTML docs.
