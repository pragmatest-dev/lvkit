# lvkit Block-Diagram Renderer — Roadmap

Durable, cross-session follow-on list for the faithful LabVIEW block-diagram
renderer (`src/lvkit/render/`). Task IDs refer to the session TaskList when
live; this file is the persistent record. Validate every visual change against
the VI-sampling gallery: `uv run python scripts/render_gallery.py` →
`outputs/gallery/index.html`. Icon assets are local-only/cleanroom (see below).

## Foundations (done)

- Graph-driven render: layout(geometry) → scene(join) → draw → SvgBackend.
- Correct geometry (icon centering, arith triangles, exact wires); FP terminals
  icon-view; element-vs-array wire thickness; border coercion dots.
- Icons vector through-and-through (`icons.png_to_svg`); SubVI `_ICON.png`
  vectorized at render time (cached).
- **Cleanroom packaging**: ship ZERO NI-derived icons (PNG + SVG local-only /
  gitignored / wheel-excluded); user generates locally from their own LabVIEW.
- vi.lib re-categorization from the reference-manual TOC.

## Shipped this session (renderer branch `lv-renderer`)

- **[#17] Interactive frame selector — DONE.** Case + stacked-sequence frames
  render into per-frame `lv-frame` groups (compositional `data-path`, nesting
  composes); id-scoped inline JS controller; **real dropdown menu** (▼ opens a
  list of frame values, ◄/► step) via a value box + separate click targets;
  script CDATA-guarded so a standalone `.svg` is valid XML.
- **[#10] Flat Sequence film strip — DONE.** Per-frame heap offset tiles frames
  side-by-side; vertical dividers between frames.
- **Sequence tunnel geometry — DONE.** Frame termLists now walked; stacked
  tunnels resolve via aliased uids (was: no tunnel drawn, wires dropped).
- **[#12] SubVI + primitive hover `<title>` — DONE.** Full-identity tooltip on
  every named node (backend `begin_group(title=…)`).
- **In-box name wrapping — DONE.** Icon-less subVIs AND primitives wrap their
  name inside the box (`WrappedBoxGlyph`, ≤4 lines, adaptive font-shrink before
  truncating), replacing empty box + below-label.
- **Boolean constants — DONE.** LabVIEW T/F button (`BooleanConstantGlyph`):
  True = green fill + white bezel + white T; False = white + green F; centered
  square, sharp corners.
- **Data fix: primResID 1062 = And (was mislabeled "Decimate 1D Array").**
  Confirmed a 2-input boolean gate via corpus sweep; codegen was emitting the
  wrong `in_1[::2]`. (And-vs-Or/Xor pending user confirm.)

## Open

### Bugs
- **Stacked sequence render is broken** (flat is fine) — under investigation.
- **[#20] Wire routing: never route a wire UNDER a node it doesn't connect
  to** — reads as a false connection; treat non-endpoint nodes as obstacles and
  route around. Folds into the parallel/collinear-overlap routing work.

### Case structures
- **[#16] Typed case selector values** — the value bar shows type-faithful
  values, not just numbers: bool → True/False, string → "...", enum/ring →
  symbolic name, "Default", ranges "1..5", lists "1,3,5", error → Error/No
  Error. Pull real per-frame selector value + type from the graph.
- **[#9] Error-case structure** — an error cluster wired into a Case selector:
  "No Error" frame = GREEN border, "Error" frame = RED border; selector reads
  Error / No Error.

### Semantics
- **[#11] Broaden coercion dot to all math prims** — data-driven "coerces
  numeric inputs" flag in primitives.json (covers comparisons; no false-fire on
  structural cases like Index Array's i32 index into a DBL array).
- **[#19] Codegen: While loop is do-while**, not pre-test — emit
  `while True: <body>; if <cond>: break`, so the body runs ≥1× (LabVIEW
  semantics), instead of `while <cond>:`.

### Terminals
- **[#18] Terminal display mode: icon (square) vs compact form** — the terminal
  carries this as a heap flag; `draw_fp_terminal` always uses the compact
  icon-view today.

### Icon coverage & sourcing
- **[#13] Auto-discover `pdf_page`** for unmapped/stale primitives (scan the PDF
  doubled-heading pattern → name→page map) so extract+vectorize fills them in.
- **[#14] Web-scraper icon source** — fetch a function's connector-pane figure
  from ni.com/docs (public) instead of the PDF; ship the scraper, user runs it
  locally (cleanroom).
- **[#15] (optional) inline-icon overlay populator** — script emitting a local
  gitignored `_inline.json` (stem→svg) overlay.

## Later / parked
- Parallel-wire lane separation (collinear wires overlap → track channels)
  — see #20.
- Docs pipeline: switch `visualize`/flowchart.py to the SVG renderer; golden
  tests; Mermaid stays as the fail-closed fallback.
- PNG/Canvas backend; LabVIEW pixel-font (OFL) for fallback boxes.
