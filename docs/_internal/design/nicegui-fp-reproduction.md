# Reproducing a LabVIEW front panel in NiceGUI — architecture

Captures the design as directed (2026-09-11). Goal: a VI becomes a lean, idiomatic
Python **script** plus an *optional* faithful NiceGUI **front panel** bound to it.

## 1. The split: pure script vs UI wrapper (separation of concerns)

- **`logic.py` — the pure script.** The VI's block-diagram logic as idiomatic
  Python, written *as if the UI does not exist*: no NiceGUI import, plain
  functions/values. This is the primary artifact and the **TesterKit / pytest**
  target (the functional suite already executes exactly these). It must stay
  **location-independent** so the logic can later run on a *different machine*
  than the UI — the UI talks to it across a seam that can become RPC.
- **UI is OPTIONAL and progressive.** Do **not** force UI files on someone who
  just wants the logic. Keep generated overhead minimal; let people add the UI
  layer only when they want it, implementing pieces as they need them.
- **`state` — a `@binding.bindable_dataclass`.** One `BindableProperty` field per
  FP control (input) and indicator (output). This is the decorator-based binding
  (NiceGUI has several binding APIs; this is the clean MVC one).
- **`panel.py` — the UI wrapper.** Reproduces the LV front panel (real geometry,
  §3) and binds it to the pure script: inputs `bind_value(state,'x')`; indicators
  `bind_value_from(state,'y')`; a change runs the pure `logic` and writes outputs.

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
the VI's real properties, add/remove/drag-reorder, hover-reveal chrome); and a
click-to-run **gallery** (`scripts/gen_gallery.py`).
