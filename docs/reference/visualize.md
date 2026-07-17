# visualize

Generate an interactive HTML graph of a VI's dataflow, or of the dependency
graph across a whole library or class. Like [`describe`](describe.md),
`visualize` never requires primitive or vi.lib mappings — it graphs
structure, not converts. It requires the optional `visualize` extra:

```bash
pip install lvkit[visualize]
```

If that extra isn't installed, `visualize` prints
`Error: pyvis not installed. Run: pip install pyvis` and exits non-zero
before writing anything.

## Synopsis

```bash
lvkit visualize <input_path> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `input_path` | Path to a `.vi`, `.lvlib`, `.lvclass`, or a directory. |

If `input_path` doesn't exist, `visualize` prints
`Error: Path not found: <input_path>` and exits non-zero.

## Options

| Option | Description |
|--------|-------------|
| `-o OUTPUT`, `--output OUTPUT` | Output HTML file. Defaults to `outputs/graph.html`. |
| `--mode {dataflow,deps}` | Graph type. `dataflow` graphs the operations inside a single VI — for a `.lvlib`/`.lvclass`/directory input it graphs only the first VI encountered while loading, not the whole set. `deps` graphs VI-to-VI dependencies and works with any `input_path`, including a lone `.vi`. |
| `--open` | Open the generated file directly in your system's default browser via Python's `webbrowser` module after writing it — no server is started. |
| `--load-mode MODE` | How deep to load dependencies: `full` (default), `minimal`, or `none`. `--load-mode none` skips [SubVI](subvi-resolution.md) expansion. |
| SubVI resolution flags | `--search-path`, `--project-root`, `--vilib`, `--userlib`, `--no-auto-vilib` — see [SubVI & vi.lib resolution](subvi-resolution.md). |

## Example

```bash
lvkit visualize "Result Logger.lvclass" \
  --search-path samples/OpenG/extracted \
  --mode deps \
  -o outputs/result-logger-deps.html
```

`--search-path samples/OpenG/extracted` tells `visualize` where to find
`Result Logger.lvclass`'s own SubVI dependencies (see
[SubVI & vi.lib resolution](subvi-resolution.md)). This writes an interactive
dependency graph to `outputs/result-logger-deps.html`. Switch
`--mode dataflow` to instead graph the operations within a single VI.

The output is a self-contained HTML file — open it in a browser directly,
no server needed. Nodes are color-coded by kind and a legend is drawn in the
corner: in `deps` mode, VI/library/class/typedef, plus a grey stub node for
any dependency `visualize` couldn't resolve; in `dataflow` mode, SubVI call/
primitive operation/structure/constant. Clicking a node populates a
properties panel with its details.

## Unresolved SubVIs

A SubVI or vi.lib call `visualize` can't resolve never fails the command —
it's recorded as a stub instead. In `deps` mode the stub is drawn as a grey
node with a "missing/stub" tooltip; in `dataflow` mode the call itself is
still drawn, just not expanded further. This differs from
[`generate`](generate.md#unresolved-calls), which fails the build on an
unresolved primitive or vi.lib VI by default.

## See also

- [render](render.md) — a faithful block-diagram SVG of a single VI's wiring.
- [docs](docs.md) — static, cross-linked HTML documentation instead of an interactive graph.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the resolution flags `visualize` shares with the other commands.
