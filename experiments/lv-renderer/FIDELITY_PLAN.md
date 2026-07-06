# Renderer Fidelity Plan — "look like LabVIEW as closely as possible"

Reconciled after a Fable review that measured against the actual repo/samples/PDF.
Follow-on to the shipped renderer (P0 graph-driven, P1 fidelity, P3 docs, P2 glyph chain).
Reference example to match: `.tmp/array average 1.vi` + `.tmp/array average 1.png`.

## Requested outcomes (hard — no shortcuts, no placeholders, validate every visual)
1. Nodes show **real LabVIEW icons**, not fallback boxes / text abbreviations.
2. **Terminal iconography correct** — array-of-DBL = `[DBL]` in an orange box (brackets=array),
   scalar = `DBL`; NOT the invented `[ ]`.
3. **No huge stretched blocks** — nodes at their true size.
4. **Boundary tunnels behave like real LV** — wire meets the tunnel's outer edge outside / inner
   edge inside, never drawn over the terminal; auto-index shows a bracket; array wires thicker.
5. **Interactive frame selection** for Case / stacked Sequence in the HTML docs.
6. Every visual validated against a rasterized image + the reference, **continuously**.

## Key measured facts (Fable, verified)
- Node sizes: primitives **32×32 in all 2,121 instances**; subVI `iUse` **32×32 in 1,309/1,315**
  (6 genuinely *expanded* subVIs at ~96×228). The 137×188 "huge" rects are `select`/case
  structures, **large cluster/typedef/refnum constants**, and `label`/`comment` decorations —
  NOT stretched icons. → "cap at 32×32" is wrong; it fixes nothing and breaks expanded nodes.
- vi.lib icons already work end-to-end when a VI is resolvable: `vilib_resolver` finds the `.vi`
  (incl. `.llb`), pylabview extracts `{stem}_ICON.png`, `ExtractedIconResolver` uses it. The gap
  is ONLY that `render_vi_file` hardcodes `expand_subvis=False` and `cmd_render` passes no
  `--vilib`/`--search-path`. → real subVI icons need plumbing, not PDF extraction.
- `wire_style` already adds stroke width per array `dimensions` (thicker array wires exist).
- Primitives: 77/97 entries have `pdf_page`; `node_types` have none. Arithmetic/comparison panes
  are **borderless triangles** (already drawn by `GeneratedGlyphResolver`); only *boxed* prim
  icons (Init/Index/Build Array) are worth PDF-cropping.
- PDF: embedded images pull cleanly at native res, dedup by reused object IDs; but figures can
  bleed across pages (p.400's first figure is Init Array's) and stored pages can be off (Greater?
  745 vs 748). → associate figure→nearest-preceding-heading via pymupdf layout + verify page text.
- `type_family` is coarse and **NumComplex falls to "unknown" (existing bug)**. Need a
  `type_repr()` for the terminal text ("DBL"/"I32"/"CDB"/"TF"/"abc"/brackets).
- Docs already ship an inline `<script type="module">` (Mermaid), no CSP, SVG inlined → JS runs.
- `Backend` has no group op; `SvgBackend.image()` lacks `image-rendering: pixelated`;
  `JsonGlyphResolver` reads `icon.file` as SVG **text** (PNG needs a separate `PdfIconResolver`).

## Plan (Fable-corrected order — cheap high-impact first, PDF long-pole late, validate per step)

**1. Root-cause the "huge box" against heap data (investigation first, per repo rule).**
   Trace which node kind/glyph produces the oversized boxes (suspect: large cluster/typedef
   constants drawn as one `ConstantGlyph`, or a UID-join mismatch). Fix the actual cause. Rule:
   32×32 → glyph fills bounds (already correct); `VINode` larger → expanded composition (32×32
   icon top + terminal rows); large constants → bounded box (accepted-loss list). NO 32×32 cap.

**2. Tunnels / boundary wires (small, kills a headline complaint).**
   - Draw border terminals in a pass AFTER wires (z-order) so wires aren't painted over them.
   - Edge-anchor: in `_build_wire_nets`, snap a tunnel endpoint to its outer/inner edge midpoint
     using `TunnelTerminal.boundary` + the tunnel/structure rects (no router/model change).
   - Auto-index tunnel: draw a real bracket glyph (replace the `"[ ]"` text).
   - VERIFY array-wire thickness on a real VI (already implemented); fix source-terminal type
     resolution at tunnel endpoints only if it's actually not thick.

**3. Terminal iconography (small-med; kills the `[ ]` complaint; fixes the complex bug).**
   Add `type_repr(lv_type) -> str` beside `type_family` ("DBL"/"I32"/"U8"/"CDB"/"TF"/"abc";
   `kind=="array"` → `[elem]`; 2-D → `[[elem]]`). Fix NumComplex in the family map. Render the
   FP/tunnel terminal as the type box (color from `type_family`, text from `type_repr`); prefer
   the extracted 32×16 PDF glyphs for the common reps, drawn text-in-box fallback for the rest.
   FP label goes **above** the box (LV default), not below.

**4. Real subVI icons for `lvkit render` (hours; zero image processing; big visual win).**
   Add `--vilib`/`--userlib`/`--search-path` to `lvkit render` (reuse `_parse_library_roots`),
   have `render_vi_file` load with those roots + `expand_subvis=True` (or a lighter public
   graph "resolve dep-path token → real path" so `ExtractedIconResolver` can extract just the
   icon, extraction cached). Drop the stale docstring claim in `ExtractedIconResolver`.

**5. PDF icon extraction — boxed primitives only (long pole, scoped).**
   `scripts/extract_lv_icons.py` (build-time, pymupdf): per prim `pdf_page` (verify the function
   name in the page text, scan ±5), get each embedded image's page rect + each heading's text
   rect, associate figure→nearest-preceding-heading (avoids the p.400 bleed). Within the chosen
   connector-pane figure, **border-detect the icon box** (maximal near-black axis-aligned
   rectangle, side ∈ [30,36]px; wires are 1–3px colored horizontals, labels are detached text;
   growables have a dotted bottom + resize-arrow chrome → crop at nominal 32h, drop the chrome).
   On any failure → **skip, record reason in a manifest, fall through to `GeneratedGlyphResolver`**
   (never ship a dubious crop). Also emit the deduped 32×16 type-glyph assets for step 3. Cache
   PNGs in `src/lvkit/data/glyphs/` keyed by prim_id + a manifest; PDF never ships. New
   `PdfIconResolver` loads them, placed AFTER `JsonGlyph` (human override wins) and after
   `ExtractedIcon`. Add `image-rendering: pixelated` to `SvgBackend.image()`.

**6. Interactive frames (biggest change, last).**
   Backend gains `begin_group/end_group(id, attrs)`. scene.py's frame *exclusion* becomes frame
   *tagging* on `RenderNode`/`RenderWireNet` (keep single-frame as the default behind a flag);
   layout already walks all frames at the same origin (overlap = ready for toggling). Render all
   frames as `<g data-frame>` groups + an LV-style selector (`◄ label ▼ ►`); put the toggle
   `<script>` **inside the SVG root** so it works in the docs page AND a standalone SVG opened in
   a browser. Determinism: iterate frames in graph order.

**Also add (D8 — real-LV fidelity levers the plan was missing):** wire **dash/pattern per type
family** (bool dotted-green, 2-D array double-line, cluster patterned) — big lever, only solid
width today; junction dots at actual trunk split points (not just source center); a
**decoration/label pass** (node labels, free comments — DESIGN C5, still unimplemented); structure
chrome (while-loop border, stacked-seq `[0..N]` header, flat-seq per-frame separators, event
struct); reconsider white-knockout on subVI icons (real LV keeps the white field + border on the
diagram); accepted-loss entries for expanded-subVI panes and expanded-constant internals.

## Validation (per step, not a final phase)
Rasterize each render (cairosvg via `uv run --with cairosvg`) and diff against the reference +
`array average 1.png`; spot-check extracted icons against their PDF pages; keep the full suite
green + ruff/pyright clean; determinism (cross-PYTHONHASHSEED) holds.
