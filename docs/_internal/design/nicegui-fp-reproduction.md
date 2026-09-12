# Reproducing a LabVIEW front panel in NiceGUI — architecture

Captures the design as directed (2026-09-11). Goal: a VI becomes a lean, idiomatic
Python **script** plus an *optional* faithful NiceGUI **front panel** bound to it.

## Clusters — plan (standalone + arrays of clusters)

Status (2026-09-11): `state.py` already nests a cluster as a bindable dataclass
(`state_gen`); the rest is partial/unbuilt.

1. **Standalone cluster** (a cluster control/indicator) — ✅ **DONE.** A bordered
   "cluster shell" containing each field rendered BY ITS TYPE (the per-type widget
   logic recurses: numeric/string/path/bool/enum), each bound to
   `state.<cluster>.<field>` (a nested bindable dataclass). The caption-above-box
   refactor also cleared the old peephole clipping, so no special handling is
   needed. Verified on OpenG/JKI `Is an Error (error cluster)` (source/code/status).

2. **Array of clusters** — ✅ **DONE, end to end** (AG Grid's sweet spot: a real
   multi-column table). For an `indArr` whose element ddo is `stdClust`:
   - `array_control`: pass `fields=[ArrayField(...)]` (one per cluster field) and
     it builds a COLUMN PER FIELD, each declaring its `cellDataType`
     (number/boolean/text) from the field's real type, with `state.<field>` a
     `list[dict]`. Rows are stored verbatim (AG Grid delivers each cell already
     typed) — no per-value coercion; a scalar array is the one-field case.
   - Parser (`_parse_cluster_fields`): an `indArr` whose element ddo is a
     `stdClust` exposes the element cluster's fields as the array control's
     `children` (same extraction as a standalone cluster).
   - `panel_gen`: an array control with `children` emits `fields=[ArrayField(key,
     header, element_type)]`, a fixed row height + column header, and a widened
     box. The codegen already types a cluster array as `list[dict]`, so state and
     grid agree with no glue.
   - Verified on JKI `Is an Error (error array)` (source/code/status columns).
   Deferred: numeric int-vs-float representation + range per cluster field (the
   scalar cluster child carries no StdNumMin/Max in the heap; defaults to float).

3. **Depth**: a cluster field that is itself an array → nested array control in
   the standalone case; for array-of-clusters that's a 2D cell (defer — start
   with scalar cluster fields).

## 1. The split: pure logic vs UI wrapper (two VI-named files)

Each converted VI produces **two VI-named files** (every VI has both a diagram
and a panel, so both are always emitted), plus **one shared `controls.py`**
runtime per directory. Because the files are VI-named, many converted VIs
co-locate in a single directory — the way a LabVIEW `.llb` maps to a package.

- **`<vi>.py` — the pure logic.** The VI's block-diagram logic as idiomatic
  Python, written *as if the UI does not exist*: no NiceGUI import, plain
  functions/values. This is the primary artifact and the **TesterKit / pytest**
  target (the functional suite already executes exactly these), it runs
  **headless**, and it stays **location-independent** so the logic can later run
  on a *different machine* than the UI — the UI talks to it across a seam that can
  become RPC. Its SubVI dependencies are VI-named peer modules in the same dir.
- **`<vi>_panel.py` — the UI wrapper.** One file holding: the `State` view-model,
  `build_panel()`, and a guarded `__main__` runner (`python <vi>_panel.py` serves
  it). Reproduces the LV front panel (real geometry, §3) and binds it to the pure
  logic: inputs `bind_value(state,'x')`; indicators `bind_value_from(state,'y')`;
  Run calls the pure logic and writes the outputs.
  - **`State` — a `@binding.bindable_dataclass`**, inlined here (it lives with the
    UI). One `BindableProperty` field per FP control (input) and indicator
    (output) — the clean decorator-based MVC binding.
- **`controls.py` — the shared runtime**, written once per directory (not copied
  per VI); every `<vi>_panel.py` imports it.

Presentation boundary: the logic returns real Python types (e.g. `pathlib.Path`);
the control layer coerces anything non-JSON-serializable to text at the widget
seam (`_jsonable`), so NiceGUI can serialize it and the logic stays pure.

## 2. Execution model — Run / Run Continuous (LabVIEW semantics, NOT per-keystroke)

Binding is the plumbing; **Run** is the trigger. Three cases, all off one model:

1. **Run, simple dataflow VI** — latch the controls, read their values, one
   function call into `logic`, write the outputs. Done.
2. **Run, interactive/state-machine VI** — Run *starts the VI's event loop / state
   machine* (the event-structure layer). It responds to control changes and
   updates indicators over time until it stops (indefinite single run).
3. **Run Continuous** — case 1 repeated over and over until stopped.

So controls are **latched** on Run (not recomputed on every input change). The
harness must cover the simple call, the running state machine, and the continuous
loop.

### The VI toolbar/header (consistent chrome, like a LabVIEW VI window)

Every reproduced panel gets a consistent **header bar** across the top — the VI
window toolbar — not a lone "Run" button. Buttons + states, from NI's VI Toolbar
Buttons reference (docs, not artwork):

- **Run** — solid arrow ▶. Enabled when idle/runnable; a **broken** state when the
  VI can't run; shows *running* while executing.
- **Run Continuously** — two looping arrows. Toggles the continuous loop (case 3).
- **Abort Execution** — red stop. **Live only while running** (dimmed otherwise);
  aborts the run — this is the abort the model must support.
- **Pause** — two bars; red while paused. Pauses/resumes a running VI.
- **Enter** — the little confirm glyph that appears when a control holds a new,
  not-yet-latched value (LabVIEW's "new value available"); it fits the latch model.

**Clean-room:** draw OUR OWN glyphs in these shapes — NEVER ship NI's GIFs
(`noloc_env_*.gif`). CLAUDE.md forbids NI-derived artwork.

**Styleable, faithful baseline:** the layout + toolbar are reproduced
**deterministically** from the VI (people invested work in their LV UI — start
them exactly there), but the *look* is a swappable theme: a clean **modern**
default and an optional **classic/ancient** LabVIEW look. Restyling changes
appearance only, never the deterministic layout/geometry.

## 3. Faithful layout from the REAL front-panel XML (no invented constants)

The FPHb heap records each control's **internal parts**, and the parser currently
keeps only the control's outer box and discards the rest — so the panel invented
`_INDEX_W`, `_CELL_H`, caption size, visible-cell count. Fix: expose the child-part
geometry and drive the panel from it.

Receipt — the `indArr` "array" ddo on Reorder 1D Array2 (I32), from the FPHb XML:
- outer box `partID 66` = `(52,26,103,142)` → 116×51 (already used, verbatim)
- **index display** = element `stdNum` `partID 8002` at local `(16,2,46,43)` (left
  edge, ~44 wide) — with its own inc/dec `bigMultiCosm` parts
- **element cell** = the array's element `<ddo>` `stdNum` at `(23,47,49,112)`
  (~26 tall, in the element-display region right of the index display)

So index-display width, element-cell height, and the visible-element count all
come from the VI. Parser work (**first step**): expose FP control child-part
geometry generically (partID + class + local bounds) on `ParsedFPControl`, so
`panel_gen` reads real sizes instead of guessing. Distinguish index display vs
element by structure (the element is the array's element `<ddo>`; the index display
is the `partID 8002` part), not by position heuristics.

## 4. Controls, faithfully (grounded in render/glyphs/nodes/array_constant.py)

The block-diagram array glyph already encodes the truth: **index display on the
left** (stacked ▲/▼ + index digits), **element display on the right** showing a
WHOLE number of cells `floor(height/cell_h)` — never half rows, index-driven. The
front-panel control mirrors that, now sized from §3's real bounds.

## 5. Waveform (later)

A waveform/graph indicator → native `ui.echart` / `ui.plotly`. Added as its own
example once the binding + faithful-layout foundation lands.

## Models carry UI PRESENTATION, not just logic

Honoring the layout deterministically means the parser/models must record the
front panel's *presentation* — control geometry incl. child-part bounds (§3),
indicator vs control, the toolbar/window chrome — alongside the logic graph, so a
downstream spec that spans **logic AND UI presentation** can consume both from
one model. This is a reason the parser-first work matters beyond the panel.

## Build order
1. ✅ **Parser**: expose FP control child-part geometry (§3) — real bounds
   (`ParsedFPControl.parts`, commit dd47588).
2. ✅ Array control sized from real index-display/element/visible geometry
   (dd47588).
3. ✅ Consistent VI toolbar (Run / Run Continuous / Abort / Pause, clean-room
   glyphs) + Run/latch execution harness `RunController` (6ef73f1).
4. ✅ `@binding.bindable_dataclass` state + reactive bindings (inputs two-way,
   indicators one-way `bind_value_from`); logic stays pure + UI optional (e2885d4).
5. ⬜ State-machine / event-structure VIs under Run (a longer-lived `compute`
   that runs the VI's event loop; the same Abort stops it). The corpus has 84
   event-structure VIs (incl. a WaveGen SFP) but they need event-structure +
   hardware codegen — a separate lift beyond FP reproduction.
6. ✅ Waveform example — `waveform_indicator` (ui.echart) in the control library
   + `examples/waveform-nicegui/` (pure sine logic + toolbar; Run draws, Run
   Continuously animates). No graph *controls* exist in the OpenG corpus, so
   this is a focused example proving the pattern (adopt a maintained chart lib).

**Also done:** a central `Theme` seam (MODERN/CLASSIC presets, one switch
re-skins the whole panel); the array control adopts AG Grid (typed cells from
the VI's real properties, add/remove/drag-reorder, hover-reveal chrome); a
click-to-run **gallery** (`scripts/gen_gallery.py`); a real **path control**
(`path_control`) with a server-filesystem Browse dialog (clean-room folder/file
glyphs); the **two-VI-named-files + shared `controls.py`** output contract (many
VIs co-locate in one dir; `python <vi>_panel.py` runs one); and the `_jsonable`
presentation-boundary coercion (fixes path *outputs* silently no-op-ing on Run —
a `Path` reaching NiceGUI's JSON serializer failed outside the Run error
boundary).
