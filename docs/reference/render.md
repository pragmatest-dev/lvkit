# render

Render a VI's block diagram or front panel to an interactive SVG — the same
wiring and structure layout as the LabVIEW editor, without opening LabVIEW. lvkit draws
primitives as clean-room glyphs (it ships no LabVIEW-derived artwork) and
gives SubVIs their own icons from the VI file. Like [`describe`](describe.md),
`render` never requires primitive or vi.lib mappings — a primitive lvkit
doesn't recognize still draws, as a labeled box instead of a glyph.

## Synopsis

```bash
lvkit render <input_path> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `input_path` | Path to a `.vi` file, or its already-extracted `_BDHb.xml` block-diagram heap (skips re-running the extractor). The path may be written as lvkit prints it (`<userlib>/…`, `<vilib>/…`, `<instrlib>/…`, or a VI inside an `.llb` through its archive): it is found in the folders given by `--vilib`, `--userlib` and `--instrlib`. |

## Options

| Option | Description |
|--------|-------------|
| `-o FILE`, `--output FILE` | Output SVG path. Defaults to `<vi-stem>.svg` next to the input. |
| `--view VIEW` | Which picture of a VI to draw: `block-diagram` (the default), `front-panel`, or `both`. `both` writes two files: with `-o out/X.svg`, `out/X.block-diagram.svg` and `out/X.front-panel.svg` (the same for `.html`); without `-o`, both pictures go to the cache and both paths are printed. A `.ctl` control has only a front panel, so that is its default and `--view block-diagram` on a `.ctl` is a usage error (exit 2). |
| `--load-mode MODE` | How deep to load dependencies: `minimal` (default), `none`, or `full`. `minimal` loads this VI plus each **direct** SubVI's connector pane and the field definitions of referenced classes/typedefs — enough to render the diagram exactly, while skipping the transitive SubVI tree the render never needs (8–40× faster on a deep hierarchy). `none` loads this VI only, so SubVIs render as plain boxes. `full` walks the whole SubVI tree; its output is identical to `minimal`, just slower. |
| SubVI resolution flags | `--vilib`, `--userlib`, `--instrlib`, `--project-root`, `--no-auto-vilib` — see [SubVI & vi.lib resolution](subvi-resolution.md). |

## Example

```bash
lvkit render "Convert File Extension (String).vi" \
  --userlib "samples/OpenG/extracted/File Group 0/user.lib" \
  -o outputs/convert-file-extension.svg
```

This writes a single self-contained SVG to `outputs/convert-file-extension.svg`
and prints `Rendered outputs/convert-file-extension.svg`.

## Front panel

`--view front-panel` draws the whole front panel as it was saved: every control,
free label and decoration, with the saved window's edge as a thin frame.

- A tab control shows its tabs and the objects of the page that was shown when the
  VI was saved.
- A panel split into panes (a splitter) draws each pane at its place, clipped to the
  pane, with the splitter bar between them.
- A scrolled panel is placed by its scroll position, so objects sit where the saved
  window showed them; objects outside the window are drawn too.
- Objects stack as in LabVIEW: the first object in a list is the front one.
- A LabVIEW class control draws as the class cube with the class's name.

```bash
lvkit render "Main.vi" --view both -o outputs/main.svg
```

This writes `outputs/main.block-diagram.svg` and `outputs/main.front-panel.svg`.
With `--format html`, the front-panel page is the same zoom and pan viewer a `.ctl`
control gets.

## Interactive output

The SVG has no external JS or CSS, so it embeds directly in any HTML page.
Opened in a browser:

- Clicking a case or sequence structure's selector (◄ / ► / dropdown) switches
  the visible frame.
- Hovering a node shows a help panel with its terminal names and types.
- `render` colors wires by data type.
- A recognized primitive or vi.lib VI deep-links to its NI documentation page;
  an unrecognized one draws as a labeled box with no doc link.

## Unresolved SubVIs

Under the default `minimal` load mode, an unresolved SubVI call — one that
isn't at the path its caller records — does not fail the
render. It draws the same way `--load-mode none` draws every SubVI: a box with
the SubVI's name wrapped inside, no real icon. The rest of the diagram,
including other, resolvable SubVI calls, still renders with real icons.

Only a failure while loading the target VI itself (not one of its SubVIs)
falls back to `--load-mode none` for the whole diagram, so the VI's own block
diagram still renders.

## Notes

- Unlike [`visualize`](visualize.md), `render` needs no optional install
  extra — a plain `pip install lvkit` is enough.
- Also available one-click, with no CLI, as the [VS Code extension](vscode-extension.md)'s
  View command.

## See also

- [describe](describe.md) — a text summary instead of a diagram, when you just need the signature and operations.
- [visualize](visualize.md) — a graph view across a whole library instead of one VI's block diagram.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the resolution flags `render` shares with the other commands.
