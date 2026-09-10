# NiceGUI control library + faithful front-panel layout — execution plan

**Status:** in progress. **Phase 2 (1D ArrayControl) DONE** — see "Phase 2
landed" below; remaining phases (typed scalar library, parser defaults/element
type, size-from-bounds height, cluster control, 2D arrays) still to do.

## Phase 2 landed (2026-09-10) — 1D array control, composed from native NiceGUI
- `scripts/panelgen/controls_runtime.py` — `array_control(state, field, *,
  readonly, label)`: one `ui.input` per element + add/delete buttons, composed
  from `ui.column`/`ui.row`/`ui.button` (no custom Vue), `@ui.refreshable` rows.
  `generate.py` copies it into each panel dir as `controls.py` (self-contained
  for the gallery loader).
- `control_types.py` — `indArr`/`array` → `ControlTypeInfo("list", "[]",
  "array")`; `panel_gen._render_widget` emits `array_control(...)` for them
  instead of the string-input fallback. Array *indicators* are refreshed after
  Run via a captured `_w_<field>()` (the composed control isn't
  `bind_value`-driven).
- `state_gen._field_default_rhs` — a `list` field can't take a bare `[]` default
  in a dataclass; mutable literals now route through `field(default_factory=…)`.
  Keyed on the literal, so a future decoded array/cluster default is handled the
  same way. Regression-guarded by `tests/test_panel_gen.py`.
- Verified on `Reorder 1D Array2 (I32)`: array control renders, `[10,20,30]` +
  `[2,0,1]` → `[30,10,20]` round-trips, panel serves HTTP 200, `pytest -m
  functional` green (9).

**Original plan (below) still stands for the remaining phases.**
Owner context: the FP panel generator (`scripts/gen_panel.py` + `scripts/panelgen/`)
currently renders EVERY unmapped control as a 15-char `ui.input`, so array-in/
array-out VIs (the bulk of OpenG) get string boxes instead of usable controls.
This plan replaces the string-input fallback with a typed control library, sizes
widgets from the VI's real bounds, and wires front-panel default values.

## Current state (grounded, so the plan targets the real gaps)

Verified on `Reorder 1D Array2 (I32)` (`array: [I32] in`, `indices: [I32] in`,
`reordered array: [I32] out`):

- **Bounds ARE read and positioned.** `panel_gen` emits absolute
  `left/top` from `control.bounds`, normalized to a 16px margin. For this VI the
  two left-column controls both land at `left:16px` (their real bounds share
  `left=26`) — alignment from LV is preserved. So "bounds ignored" is not the
  bug; the bugs below are.
- **Every control is `indArr` and hits the string-input fallback**
  (`control_types._UNKNOWN` → `ui.input` + `# TODO: unsupported control_type`).
  That's why "array in" is a 15-char string box. Only
  `stdString/stdPath/stdNumeric/stdBool/stdEnum` are mapped today
  (`scripts/panelgen/control_types.py:_KNOWN`).
- **Widget height is not sized from bounds.** Bounds carry height
  (`array` = (top52,left26,bot103,right142) → 51px tall = a multi-row array box),
  but only width is used; height is left to the widget default, so tall controls
  (arrays, multiline) collapse and rows look misaligned vertically.
- **Defaults:** array/cluster `default_value` is `None` from the parser (gap,
  below); scalar defaults already flow via `control_types.default_source`.

## Goal

For a function-style VI, generate a panel where each control/indicator is a
control matching its LabVIEW type — arrays get an add/remove/edit array control
whose elements match the element type; numerics get numeric inputs; enums get
dropdowns; clusters get grouped sub-controls — laid out and sized from the VI's
real bounds, seeded with the front-panel default values. Verify by execution
(the value round-trips through the pure logic) and by a visual check against LV.

## 1. Control library (`scripts/panelgen/control_types.py` → a `controls/` package)

Map by **control_type + element/cluster structure**, single choke point. Table:

| LV control_type | NiceGUI control | State field type |
|---|---|---|
| stdString / stdPath | `ui.input` (path: with a folder affordance later) | `str` |
| stdNumeric (int subtype) | `ui.number` (integer step) | `int` |
| stdNumeric (float subtype) | `ui.number` | `float` |
| stdBool | `ui.switch` (indicator: `ui.icon`/LED, read-only) | `bool` |
| stdEnum / stdRing | `ui.select(options=enum_values)` | `str`/`int` |
| **indArr / array** | **ArrayControl (below)** | `list[T]` |
| stdClust | bordered group of child controls (recurse) | dataclass |
| refnum / unmapped | disabled `ui.input` + visible TODO chip | `Any` |

Element/subtype resolution: the generated **logic signature already carries it**
(`array: list[int]`, `list[str]`, `list[Path]`) — read the annotation the panel
already matches args against, so the ArrayControl knows its element widget with
NO parser change. (Cluster field types likewise come from the logic signature /
the connector-pane `LVType`.)

## 2. ArrayControl component (the priority)

A reusable NiceGUI component bound to a `list[T]` on `State`:
- Renders each element with the element-type widget (number/input/switch/select).
- **Add** (append a default element), **Delete** (per-row, or delete-last),
  **Modify** (edit in place; edits write back to the bound list and refresh).
- An index column, and an empty-state row.
- Read-only variant for indicators (no add/delete, disabled element widgets).
- 2D arrays (`list[list[T]]`): a table/grid; start with 1D, stub 2D as a JSON/
  textarea with a TODO, then iterate.
- Bind semantics: mutations update the `State.<field>` list in place and trigger
  a refresh so the Run handler reads the current value; `run.io_bound` unchanged.

Keep it a standalone module (`panelgen/controls/array_control.py`) with a factory
`array_control(state, field, element_type, *, readonly) -> None` that renders into
the current container. `panel_gen` calls the library factory instead of the
inline `ui.input` fallback.

## 3. Front-panel default values

- Scalars already handled (`control_types.default_source`).
- **Parser gaps to close** (so arrays/clusters get real defaults):
  - Array constant/control DefaultData is decoded for scalars but array/cluster
    control `default_value` comes back `None`. Extend `_decode_default_data` /
    `_parse_ddo` (`src/lvkit/parser/vi.py`) to decode array + cluster
    DefaultData, or expose the element/field defaults.
  - Cluster CHILD defaults: `_parse_ddo` (`src/lvkit/parser/vi.py:1394-1400`)
    always recurses children with `default_data=None`. Thread the child's slice
    of DefaultData through.
- Until the parser exposes them, seed arrays to `[]` and cluster fields to their
  type zero — but wire the plumbing so the values appear once the parser lands.

## 4. Layout / alignment fidelity (`panel_gen._render_container`)

- Position from `bounds.left/top` (done) AND **size from bounds**: set width =
  `right-left` and min-height = `bottom-top` on the wrapper; let the control fill
  it (`.classes('w-full h-full')`). Tall array boxes then render tall.
- Normalize by the min bounds (done) so the panel isn't offset; keep the 16px
  margin.
- Remove NiceGUI default margins/gaps on the absolute wrappers so positions are
  exact (`.style('margin:0')`, container `position:relative` with explicit
  width/height = panel extent — already emitted).
- LV alignment is already encoded in the bounds (controls the author aligned
  share an edge coordinate); faithfully reproducing bounds reproduces the
  alignment. Add a light grid/snap only if a visual pass shows sub-pixel drift.

## 5. Parser support tasks (surfaced, do alongside)

- Expose array **element type** on `ParsedFPControl` (currently `indArr` is
  opaque; the logic signature is the interim source, but the panel should not
  depend on codegen for a UI fact). Add `element_type: LVType | None`.
- Array + cluster **default values** (see §3).
- Cluster-child **absolute bounds**: `_parse_ddo`
  (`src/lvkit/parser/vi.py:1386-1412`) discards the nested cluster pane's
  `origin`/`docBounds`, so cluster children can't be placed exactly. Add the
  nested pane origin so cluster layout is faithful (until then, flow cluster
  children in a column).

## 6. Phased execution + acceptance

1. Control library table + factories (numeric/bool/enum/string) — panels stop
   using the string fallback for scalars.
2. **ArrayControl (1D)** + element-type from logic signature — Reorder/Empty/
   array VIs get a real array control. **Acceptance:** generate the Reorder
   panel, enter `[10,20,30]` + `[2,0,1]`, Run → indicator array control shows
   `[30,10,20]`; verified headless (build_panel runs) + served HTTP 200.
3. Parser: array/cluster defaults + array element_type → controls seed correctly.
4. Layout: size-from-bounds (height) + margin reset → visual alignment matches LV.
5. Cluster control (recursive) + cluster-child bounds parser fix.
6. 2D array control; path folder affordance; enum-as-int.
- **Guardrails:** every step keeps `pytest -m functional` green and does not
  regress `scripts/` ruff; add a headless panel-builds test per control type;
  do a one-look visual check of a Reorder panel and a multi-control VI against
  the LV reference.

## Files
- `scripts/panelgen/control_types.py` → `scripts/panelgen/controls/` package
  (`__init__` mapping, `array_control.py`, `cluster_control.py`).
- `scripts/panelgen/panel_gen.py` (call the library; size from bounds).
- `scripts/panelgen/state_gen.py` (typed fields incl. `list[T]`, dataclass
  clusters; defaults).
- `src/lvkit/parser/vi.py` + `src/lvkit/parser/models.py` (array element_type,
  array/cluster defaults, cluster-child bounds).
- `tests/` (headless per-control-type build test; keep functional green).
