# View a block diagram

Render a VI's block diagram to a faithful, interactive diagram and open it in a browser — without opening LabVIEW.

## Prerequisites

- lvkit installed (`pip install lvkit`).
- A `.vi` file. If it calls SubVIs that live outside its own directory, know where — you'll pass `--search-path`.

## Render the diagram

```bash
lvkit render "path/to/Some VI.vi" --format html -o outputs/some-vi.html
```

`--format html` wraps the block diagram in an interactive viewer page (zoom/pan and a theme toggle — see [Read the diagram](#read-the-diagram) below); the default `--format svg` writes just the diagram itself, no toolbar. `-o` writes the file at that path; without `-o`, `render` still builds the diagram into its per-user cache and prints the cache path instead of writing a file (`Rendered <name> → cached (<path>). Pass -o FILE to write a file.`) — pass `-o` when you want a file to open, keep, or attach to a review.

If the VI calls SubVIs that live outside its own directory, add `--search-path`:

```bash
lvkit render "path/to/Some VI.vi" --format html \
  --search-path path/to/libraries \
  -o outputs/some-vi.html
```

lvkit always searches the VI's own directory and its project root (the nearest enclosing `.lvkit/`) automatically — `--search-path` is only for dependencies elsewhere on disk. See [SubVI & vi.lib resolution](../reference/subvi-resolution.md) for the full set of resolution flags (`--vilib`, `--userlib`, `--no-auto-vilib`, ...), all accepted by `render` too.

## Open it

```bash
open outputs/some-vi.html    # macOS
xdg-open outputs/some-vi.html  # Linux
start outputs\some-vi.html   # Windows
```

The file is a single self-contained HTML page — no server, no external JS/CSS — so it opens the same way locally, attached to a code review, or hosted as a static CI artifact.

## Read the diagram

`render` draws the same wiring and structure layout LabVIEW's own editor would show, colors wires by data type, and gives every SubVI its real icon (pulled from the VI file, not redrawn) — a primitive lvkit doesn't recognize still draws, as a labeled box instead of a glyph, so nothing silently disappears from the diagram.

- **Zoom / pan** — `+`/`-` buttons or keys, `⛶` (or `f`) to fit the whole diagram back into view, Ctrl/Cmd+scroll to zoom under the cursor, click-drag to pan.
- **Theme toggle** — the small `◐` button in the toolbar cycles the diagram's own color theme `auto → light → dark → auto`, independent of your browser or editor's theme.
- **Case/sequence structures** — click a structure's selector (◄ / ► / dropdown) to switch which frame is visible.
- **Hover a node** for a help panel with its terminal names and types.
- **Recognized primitives and vi.lib VIs** deep-link to their NI documentation page; an unrecognized one draws as a plain labeled box with no doc link.

An unresolved SubVI doesn't fail the render — it draws as a box with its name wrapped inside instead of a real icon, and the rest of the diagram, including any SubVI that *did* resolve, still renders normally.

## Render a whole directory

Pointing `render` at a directory instead of a single `.vi` warms every VI under it into the cache in one pass (skipping any already-fresh slot); add `-o DIR` to also export a mirrored `.svg`/`.html` tree:

```bash
lvkit render path/to/library/ --format html -o outputs/library-diagrams/
```

## See also

- [reference/render](../reference/render.md) — the full `render` flag reference, including `--load-mode` and unresolved-SubVI behavior in depth.
- [reference/subvi-resolution](../reference/subvi-resolution.md) — `--search-path`, `--vilib`, `--userlib`, `--no-auto-vilib`, shared by every command that resolves SubVIs.
- [reference/visualize](../reference/visualize.md) — a graph view across a whole library or class instead of one VI's block diagram.
- [Compare two VIs](compare-two-vis.md) — the same faithful diagram, but overlaying two versions to show what changed.
