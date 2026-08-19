# Glyph Terminal-Role Detection (design note / validated prototype)

**Status:** validated proof-of-concept in `glyph_terminal_role_detection_prototype.py` (this dir); not yet a
shipped module. This note captures the method so it survives the prototype.

## What it is: an independent validator for EVERY primitive's index+name

This is not just a fallback for ambiguous panes — it is the way we should
**validate the parmIndex→name mapping of every primitive**, because it gives an
*independent ground truth* for both:

- **Index.** The BD `<dco>` gives `parmIndex` (+ direction from `objFlags` bit 0);
  the doc image's icon box maps 1:1 to `termBounds`, so a wire's drop-in point
  gives the *same* parmIndex a second way. Agreement corroborates; **disagreement
  is a bug flag** on the `primitives.json` entry (or on our XML reading).
- **Name.** The wire's far-end label is NI's *actual* terminal name — more
  authoritative than a name *inferred* from caller wiring (caller wiring shows how
  a terminal is used; you still guess the name from that).

So the strongest form is a **three-way cross-check** — `<dco>` parmIndex vs image
geometry vs image label — run over every entry; any disagreement means something
is wrong. It is clean-room source #2 (public web docs) — never LabVIEW.

Same-typed terminals are just where it matters *most*: Array To Spreadsheet String
(primResID 1540) has three `String`-ish inputs — `delimiter`, `format string`,
`array` — that types alone cannot order, and no caller may wire them to named
controls. There the image is the only clean-room resolver; everywhere else it is
the independent check.

**Applicability.** Any primitive whose NI page shows a wired connector-pane image
(nearly all function pages). A few internal/undocumented resIDs have none. For
expanding nodes it validates the *named* terminals, not the positional `arg N`
leftovers.

## The insight (maintainer)

The NI doc's connector-pane figure draws the **real primitive icon** at its real
bounds, with each wire drawn to the terminal it lands on and a text label at the
far end of each wire. Therefore:

- The icon's pixel bounding box in the image **is** the primitive's node-space
  box. Once you know that box, the VI's `termBounds` space maps onto the image
  **1:1** (offset + the node's 32-px cell size).
- A wire's **drop-in point** — where it touches the icon perimeter — is that
  terminal's `termBounds` position. Wire *routing* (a delimiter wire that loops
  over the top and drops into the top-center) is cosmetic; only the drop-in
  matters.
- The wire's **far end leads to the role label**. Colour is incidental — you
  follow the wire, not its colour.

So: find the icon box → find each perimeter drop-in → the drop-in position, run
through the primitive's `termBounds`, yields the parmIndex; the wire's far end
yields the role name; join them.

## Pipeline (`glyph_terminal_role_detection_prototype.py` (this dir))

1. **`icon_bbox(size, px)`** — the icon is the largest non-wire-ink **connected
   component** that does not touch the image frame (border/lines/fill form one
   blob; label letters are separate; the outer frame hugs the edges). Wire ink is
   masked first. Critically, an **error-cluster wire** (olive `~127,127,0` with a
   **black core**) must be masked *including* its black core — otherwise the black
   core reads as icon "ink" and bridges the icon out to the error terminals. The
   fix: mark black pixels that are vertically sandwiched between wire pixels as
   wire too. Validated: 1540 → 31×31; Format Into String (expanding + error) →
   31×51.

2. **`attach_points(size, px, bbox)`** — wire pixels within a few px just outside
   each icon edge are drop-ins; cluster contiguous ones per edge; return
   `(edge, along-fraction, colour)`.

3. **`match_parmindex(pos, termbounds)`** — clamp the drop-in to `[0,1]` (it's on
   an edge) and return the parmIndex whose `termBounds` rect **contains** it
   (smallest containing rect wins); else nearest rect centre.

`termbounds` is `(parmIndex, top,left,bottom,right)` per terminal, read verbatim
from the BD `<dco>` (`termBounds` + `parmIndex`) — the same source as the
maintainers doc.

## Wire-ink classifier (`_wire`)

- **magenta** (string): green suppressed — `g < r-25 and g < b-25`.
- **olive/yellow** (error cluster, incl. the dark `127,127,0` shade): `r>90 and
  g>90 and b < min(r,g)*0.55 and abs(r-g)<45`. Distinct from the pale-yellow icon
  *fill* (`b~192`).
- **orange** (numeric/array): `g < r-14 and b < g-28 and r>150`.

## Validation

`1540` (fixed pane), ground truth `delimiter=1 array=2 format=3 spreadsheet=0`:

```
icon bbox x[117,148] y[23,54]  (31x31)
  right @ (1.0, 0.5)  [magenta] -> parmIndex 0   # spreadsheet (output)
  top   @ (0.5, 0.0)  [magenta] -> parmIndex 1   # delimiter (routes over the top)
  left  @ (0.0, 0.25) [magenta] -> parmIndex 3   # format (upper-left)
  left  @ (0.0, 0.77) [orange ] -> parmIndex 2   # array  (lower-left)
```

All four correct. Format Into String validated for icon detection + drop-ins on
the expanding/error case (31×51). This is the corrected 1540 mapping now shipped
in `primitives.json` (commit be5fd6b) — the method reproduces it from the image
alone.

## Expanding nodes

Format Into String and friends grow extra argument terminals. The **named**
terminals in the doc (format string, initial string, result) are always present
and map by this method; the extra positional `arg N` terminals are the leftover
drop-ins after the named ones are claimed. So an expanding node may have MORE
terminals than the documentation shows, but every important named one is still
mappable.

## What's left before this is a module

- **Directional wire-tracing to the label.** The current label-reach is a
  symmetric BFS window; where two wires run parallel (~16 px apart, e.g. 1540's
  delimiter over-the-top wire beside the format wire) it can jump to the wrong
  wire. Lowering the reach stops the jump but then stops short of the label text
  (reaches the correct *region* — enough to disambiguate top-vs-mid labels by Y,
  not enough to read the label). A proper fix follows each wire's **local
  direction** through dash gaps instead of a symmetric window.
- **Bind the role name** — OCR the label, or order the drop-ins against the doc
  prose's terminal list.
- **Encode the static-terminal sets** per primitive so expanding nodes are
  handled deterministically.
- **Promote to a real module** with tests over a few known panes.
- **Batch validation pass** — the endgame implied by "validate every primitive":
  a harness that, for each `primitives.json` entry with a public doc image, fetches
  the connector-pane figure, runs this extractor, and asserts image-geometry
  parmIndex == `<dco>`/JSON parmIndex and image label == the JSON terminal name.
  Disagreements are the work-list. This is what turns the method from a per-entry
  aid into a standing correctness check (candidate for a slow/opt-in test).

Dead code to drop on promotion: `glyph_detect()` (the ray-march-from-density-peak
detector) was superseded by `icon_bbox()` (connected-component) — its centre
biased toward the input side.

## See also

- `docs/_internal/maintainers/primitive-terminals.md` — parmIndex/direction from
  the `<dco>` (this method adds the role→parmIndex join).
- `src/lvkit/data/connector_pane_patterns.json` — the *VI* connector-pane grid
  catalog (patterns keyed by conId). Primitives have **no** conId; they serialize
  `termBounds` directly, which is why this image-based method exists for them.
- The `lvkit-resolve-primitive` skill — the no-guess process this feeds.
