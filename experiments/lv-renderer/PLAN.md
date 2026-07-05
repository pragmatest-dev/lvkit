# Plan — Faithful LabVIEW Block Diagram (& Front Panel) Rendering

## Context

lvkit's current diagrams are a **Mermaid abstraction** (colored boxes, auto-layout) —
useful but not faithful. The POC in this folder proves we can render a **pixel-accurate**
LabVIEW block diagram to SVG directly from the pylabview heap XML lvkit already extracts,
with **no LabVIEW install**. This plan turns that POC into a real, scalable renderer:
a reusable module powering (1) the HTML docs, (2) the `visualize` command, and
(3) — longer term — a **Front-Panel → NiceGUI** path that could stand up a live UI for a
converted VI. See [`README.md`](./README.md) for the heap-XML learnings this builds on.

Design pillar (from the original ask): a **scalable node-rendering system** — use the real
extracted VI icon when we have it; otherwise a prebuilt SVG glyph declared in our
prim/vilib JSON; otherwise (known name, no glyph) an optional web-searched icon; otherwise
a **fallback labeled box in LabVIEW's recognizable small font**. Structures (For/While/Case/
Sequence) and their terminals (`N`, `i`, conditional, shift registers, tunnels) get
purpose-drawn borders.

## Architecture — new package `src/lvkit/render/`

Kept deliberately separate from the codegen parser, because it needs the **geometry**
(`<bounds>`/`<termBounds>`) that the normal parser discards, and it must never affect
code generation.

- **`heap_scene.py`** — geometry-aware reader: heap XML → typed `DiagramScene`
  (dataclasses): `SceneNode{bounds, kind, name, prim_id/vi_ref, terminals}`,
  `SceneStructure{bounds, kind, frames, border_terms}`, `SceneTerminal{center, uid, dco_class, lv_type}`,
  `SceneWire{uid_net, lv_type}`, `SceneConstant`, `SceneFPTerm`, `icon_path`. This is the
  productized form of `poc_render_svg.py`'s `walk()`/`process_terms()`. Reuse
  `extractor.extract_vi_xml()` (cache) and `primitives.json`/vilib for names & symbols.
- **`glyph.py`** — `NodeGlyph{svg, width, height, source, terminals}` + the **resolver chain**
  `resolve_glyph(node) ->` real icon → JSON glyph → web icon (opt-in) → fallback box.
- **`glyphs/` asset library** + a new optional **`icon` field** on prim/vilib JSON entries:
  ```json
  "icon": { "svg": "<path .../>", "file": "glyphs/add.svg", "size": [24,24] }
  ```
  Seed with the distinctive small nodes: Add/Sub/Mul/Div triangles, Compound Arithmetic,
  Build/Index Array (expandable brackets), Bundle/Unbundle, comparisons. Pydantic: add
  `NodeIcon` to `PrimitiveEntry`/`ResolvedPrimitive` (primitive_resolver.py) and `VIEntry`
  (vilib_resolver.py) — backward-compatible/optional.
- **`structures.py`** — purpose-drawn, size-parameterized borders + terminal glyphs:
  For-Loop (stacked-cascade border, `N` count + `i` index boxes), While-Loop (wrapped-arrow
  border, conditional terminal: red stop-circle for Stop-if-True / green loop-arrow for
  Continue-if-True, + `i`), Case (selector label bar `◄ name ▼ ►` + `?` selector terminal),
  Flat Sequence (filmstrip frame), shift registers (`▼`/`▲` paired boxes), auto-index tunnels.
- **`wire_router.py`** — v1 orthogonal auto-router (clean LabVIEW-style right-angle routing,
  better than the POC midpoint). v2 research spike: decode `<compressedWireTable>` for exact
  bends. Type-driven **color + thickness** via `wire_style.py` (LVType → color: DBL orange,
  int blue, bool green, string pink, path teal, error cluster dark yellow/black; array = thicker,
  cluster = bundled/striped). Reuse `models.LVType`.
- **`fonts.py`** — the LabVIEW label look: embed a permissively-licensed pixel webfont as a
  data-URI + CSS stack `"Small Fonts","<embedded>","MS Sans Serif",Tahoma,sans-serif` at 8–9px
  (real font on Windows, approximation elsewhere). *(Licensing decision — see Open decisions.)*
- **`scene_to_svg.py`** — compose a **self-contained** SVG (icon as data-URI, font CSS inline).

## Integration

- New CLI: `lvkit render <vi> -o diagram.svg` (`--png` optional via a headless rasterizer).
- **Docs**: replace/augment the Mermaid "Block Diagram" section
  (`docs/html_generator.py::MermaidRenderer`) with the faithful SVG; keep Mermaid as the
  fallback when a VI has no heap/bounds.
- **visualize**: add a faithful-SVG mode alongside the pyvis/mermaid modes.

## Phasing

1. **Scene + static SVG** — `heap_scene.py`, `scene_to_svg.py`, auto-router, type colors,
   For/While/Case/Sequence borders + terminals, primitive triangles → `lvkit render`.
   Deliverable: faithful static SVG for common VIs, validated against real LabVIEW screenshots.
2. **Scalable node system** — glyph library + JSON `icon` field + resolver chain; SubVI real-icon
   embedding; fallback labeled box + LabVIEW font; web-search icons (opt-in, cached to
   `.lvkit/icons/`, non-fatal — mirrors the vi.lib auto-detect philosophy).
3. **Integration + tests** — wire into docs & visualize; golden-SVG snapshot tests.
4. **Front Panel → SVG → NiceGUI** (future) — render the FP faithfully, then map control
   class → NiceGUI widget (`stdNum`→`ui.number`, `stdBool`→`ui.switch`/button, string→`ui.input`,
   ring/enum→`ui.select`, cluster→container, graph→plot), bounds → layout. Combined with lvkit's
   generated Python backend, this could stand up a **running NiceGUI app from a VI**.

## Resolved decisions

- **Font** → embed a **free/OFL pixel font** (data-URI) that reads as the LabVIEW small-font
  look (candidates: W95FA, Px437, an OFL bitmap face), CSS stack keeps real "Small Fonts" first
  for Windows. MS Small Fonts itself is not redistributable. Validate the match visually first.
- **Icon source** → **extract from the local LabVIEW reference PDF**, not web image search. We
  already have the page map: `pdf_page` on primitives, `page` on vilib entries. Pipeline:
  render page region → **strip attached wire stubs + terminal labels** (crop to the icon's
  bordered box / largest bordered component) → cache to `.lvkit/icons/`. NI online reference is
  a fallback only. Needs an image-processing script (`icon_from_pdf.py`).
- **Extracted-icon transparency** → make the **exterior** white transparent via corner
  flood-fill (preserve interior white), small tolerance for anti-aliased halo. Applies to both
  pylabview `_ICON.png` and PDF-extracted icons. Likely Pillow.
- **PNG → SVG?** → **No — do NOT vectorize extracted/PDF icons.** Embed the PNG as a `data:` URI
  with `image-rendering: pixelated`; it is then byte-identical to the source. LabVIEW icons are
  32×32 pixel art; tracing gains nothing (per-pixel rects = identical+bloated, or smoothed =
  wrong). **True SVG is authored only** for our own glyph library + structure borders.

## Wire fidelity — findings (from `.tmp/probe_wiretable.py`)

- pylabview does **not** decode wire geometry (`compressedWireTable`/`wireTable` are opaque field
  IDs only). Blobs are tiny (2–6 bytes): ~half are `0208` (LabVIEW default auto-route, ~no stored
  geometry), the rest ~6 bytes (one explicit bend). Trailing bytes don't map linearly to endpoint
  deltas → it's a real compressed encoding.
- **Plan**: v1 orthogonal **auto-router** using our exact terminal endpoints — matches the common
  `0208` wires essentially exactly; only hand-dragged bends diverge. v2: optional spike to reverse
  `compressedWireTable` if pixel-exact wires are ever required (uncertain payoff).

## Still to confirm

- **Case-structure heap class** + selector DCO (the `mux` class is the Select primitive, not a
  Case) before implementing the Case border.

## Verification

- Golden-SVG snapshot tests over a handful of sample VIs (assert node/structure/wire-net
  counts, structure borders present, **zero unresolved wire endpoints** — the POC already
  prints this metric).
- Visual review: render a set of sample VIs and compare side-by-side to LabVIEW screenshots
  (an Artifact gallery is a good format).
- `uv run pytest -q`, `ruff`, `pyright` clean.
