# lvkit Block-Diagram Renderer — Roadmap

Durable, cross-session follow-on list for the faithful LabVIEW block-diagram
renderer (`src/lvkit/render/`). Grouped into themed batches; task IDs refer to
the session TaskList when live. Validate every visual change against
`.tmp/array average 1.png` (and the VI-sampling gallery) — icon assets are
local-only/cleanroom (see below).

## Done (recent highlights)

- Graph-driven render: layout(geometry) → scene(join) → draw → SvgBackend.
- **Correct geometry**: primitives center their icon within the node box
  (layout rule, not a per-node shift); arith triangles + wires exact.
- Fidelity: black cascade For-Loop border, unified 1.2 line widths (arrays
  thicker), FP terminals icon-view + label-visible gating, SubVI names below
  the icon (gated), element-vs-array wire thickness, primitive-internal
  coercion dot on the border (general via `_wire_edge_point`).
- **Icons vector through-and-through**: pixel-faithful per-color-`<path>`
  encoder (`icons.png_to_svg`, ~2KB/icon, 0-mismatch, beats pixels2svg, no
  deps); SubVI `_ICON.png` vectorized at render time (cached).
- **Cleanroom packaging**: ship ZERO NI-derived icons (PNG + SVG both
  local-only/gitignored/wheel-excluded); user generates locally from their own
  LabVIEW (like vi.lib autodetect). PDF + extract/vectorize scripts are
  dev-only build inputs.
- **vi.lib re-categorization**: killed the 4.46MB `other.json` grab-bag —
  categories now derived from the reference-manual TOC (`recategorize_vilib.py`).
- Added primitive 1426 = Current VI's Path.

## Now / Next — themed batches

### A. Case structures (coherent batch)
- **[#9] Error-case structure** — an error cluster wired into a Case selector
  renders as an error-case: **"No Error" frame = GREEN border, "Error" frame =
  RED border**, selector reads Error / No Error. Semantics: run one case if
  error-occurred == true, the other if not.
- **[#16] Typed case selector values** — the `◄ value ▼ ►` bar shows
  type-faithful values, not just numbers: bool → True/False, string → "...",
  enum/ring → symbolic name, default → "Default", ranges "1..5", lists
  "1,3,5", error → Error/No Error. Pull real per-frame selector value + type
  from the graph.
- **[#17] Interactive frame selector** — clickable dropdown/tabs on
  Case/Stacked-Sequence that switches the shown frame in the rendered webpage,
  via inline `<script>` in the SVG root + backend group ops + scene
  frame-tagging. (Was DESIGN P4; never built.)

### B. Error-handling visuals
- **[#7] DONE** Error-cluster wires = mustard/dark-yellow (`theme.wire_error
  #a88d1e`, LV 8.2+). Variant added too: `wire_variant #840984` (NI
  rgb(132,9,132)) + a `variant` `type_family` bucket.
- **[#8] DONE** Schematic, shippable glyphs (generic, like brackets/triangles —
  cleanroom-safe): `ErrorClusterGlyph` (mustard shell + green status LED + two
  code/source bars) and `VariantGlyph` (solid opaque purple box), wired into
  both constants (`GeneratedGlyphResolver`) and FP terminals (`draw_fp_terminal`,
  ≥ `_FP_MIN_ICON_SIZE`). The exact NI terminal-icon *art* remains user-side
  (icon pipeline, #14) — only the generic schematic ships.
- (#9 error-case belongs here too — see batch A.)

### C. Structures
- **[#10] Flat Sequence = film strip** — frames sharing one reel, dividers
  between frames, sprocket-hole notches along top/bottom edges ("film holes
  gears pull through"); size from real per-frame heap geometry. (Stacked
  sequence ≈ single frame + selector, already ok.)

### D. Semantics
- **[#11] Broaden coercion dot to all math prims** — replace the `ArithGlyph`
  proxy with a data-driven "coerces numeric inputs" flag/category in
  primitives.json (covers comparisons etc. without false-firing on structural
  cases like Index Array's i32 index into a DBL array).

### E. Icon coverage & sourcing
- **[#13] Auto-discover `pdf_page`** for the 20 unmapped + 6 stale primitives
  (scan the PDF for the doubled-heading pattern → name→page map) so
  extract+vectorize fills them in automatically.
- **[#14] Web-scraper icon source** — fetch a function's connector-pane figure
  from ni.com/docs (public) instead of the PDF, reusing border-detect +
  vectorize; ship the scraper, user runs it locally (cleanroom). Enables
  user-side generation without the PDF.
- **[#12] SubVI hover `<title>`** — VI name in an SVG `<title>` so anonymous
  icon-less SubVI boxes keep identity on hover (needs backend title support).
- **[#15] (optional) inline-icon overlay populator** — script emitting a local,
  gitignored `_inline.json` (stem→svg) overlay if the single-file inline form
  is preferred over the current local `.svg` files. Shipped JSONs stay
  icon-free.

## Later / parked
- Parallel-wire lane separation (collinear wires overlap → track channels).
- Docs pipeline: switch `visualize`/flowchart.py to the SVG renderer; golden
  tests; Mermaid stays as the fail-closed fallback.
- PNG/Canvas backend; LabVIEW pixel-font (OFL) for fallback boxes.
