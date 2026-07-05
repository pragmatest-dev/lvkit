# LV Renderer — experiment archive & learnings

Goal: render a LabVIEW **block diagram** (and, later, **front panel**) faithfully
to SVG, straight from the pylabview heap XML that lvkit already extracts — **no
LabVIEW install required**. See [`PLAN.md`](./PLAN.md) for the productization plan.

## Files here

- `poc_render_svg.py` — the working proof-of-concept (originally `.tmp/poc_render_svg.py`).
  Reads absolute geometry, terminal offsets, wire connectivity and the extracted
  VI icon; self-routes wires orthogonally; emits an SVG.
- `poc_glyphs.py` — probe for For-Loop terminal/tunnel/shift-register geometry.
- `example_array_average_output.svg` — a real rendered output (For-Loop with an
  Add + Divide, orange DBL wires). The `<image href>` for the icon is an absolute
  `/tmp` path from when it ran — archival snapshot only.

These were untracked scratch in gitignored `.tmp/` / `outputs/` — committed here so
they survive `git clean` and seed the real renderer.

## What the POC established (heap-XML model)

pylabview writes `_BDHb.xml` (block-diagram heap) and `_FPHb.xml` (front-panel heap).
Walking `<root>` → recurse:

- **Nodes** live in `zPlaneList/SL__arrayElement`, each with absolute
  `<bounds>` = `"(top,left,bottom,right)"`. Classes seen: `prim` (primitive),
  `forLoop` / `whileLoop` / `flatSequence` (structures), `fPTerm` / `parm` /
  `overridableParm` (front-panel terminals on the diagram), `term` (constants, via
  a nested `<ddo>` with its own absolute bounds).
- **Structures** contain inner diagrams via `diagramList/SL__arrayElement[class=diag]`;
  recurse with the structure's origin as offset. Child coords are relative to it.
- **Terminals**: `termList/SL__arrayElement[class=term]`, each with a relative
  `<termBounds>`. Center = node origin + termBounds center. Every uid a wire might
  reference (term uid, `<dco>` uid, nested inner/outer paired uids) maps to that center.
- **Wires**: `signalList/SL__arrayElement[class=signal]` → `termList` lists the term
  uids the signal connects. Connectivity is a *net of uids*, resolved to centers.
- **Icon**: `<stem>_ICON.png` is already extracted next to the XML; embed via `<image>`.

## What the follow-up probe added (`.tmp/probe_render_facts.py`)

- **Wire routing is the hard part.** A signal stores only a
  `<compressedWireTable>` **binary blob** (e.g. `040800000E10`) — NOT explicit
  polyline/bend points. So exact wire paths require either decoding that table or
  a LabVIEW-style orthogonal **auto-router** (the POC uses a naive midpoint router).
- **While-loop** children: `loopTestDCO` (class `lTst`, the conditional terminal —
  Stop/Continue-if-True), `loopIndexDCO` (`lCnt` = `i`), `srDCOList` (shift
  registers), `tunnelList`, `contRect`, `structColor`. All positioned via termBounds.
- **For-loop** count/index: `loopIndexDCO` (`i`) plus the count (`N`) DCO; shift
  registers `lSR`/`rSR`; auto-index tunnels `lpTun`.
- **flatSequence**: `sequenceList` of frames + `structColor`.
- **Case structure**: heap class still to confirm — the `mux` class is the *Select*
  primitive, not a Case. (Parser calls it `SelectHandler`; identify the real
  structure class + `selector` DCO before implementing the Case border.)
- **Front panel** controls (`_FPHb.xml`) carry class (`stdNum`, `stdBool`,
  string/ring, …) + absolute `<bounds>` + label + default — enough to map to
  NiceGUI widgets later.

## How to run the POC

```bash
# needs a local sample VI (samples are local-only; not shipped)
uv run python experiments/lv-renderer/poc_render_svg.py   # edit VI/OUT paths inside
```
