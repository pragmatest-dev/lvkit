# Subpanels: reading, drawing and embedding the inserted VI's panel

Extends `live-panel-plan.md` §10 (`on_property`) and D6 (hooks do not reach subVIs). Maintainer, 2026-11-09: a Subpanel is read and drawn (step 1, the parser's `ParsedFPSubPanel` and `render/front_panel/controls/subpanel.py`), and the generated panel and the render embed the inserted VI's panel (step 2, below).

## 1 What LabVIEW does (NI, `labview/page/subpanel-controls.html`)

"Use the subpanel control to display the front panel of another VI on the front panel of the current VI … LabVIEW does not create a terminal for the control on the block diagram. Instead, LabVIEW creates an Invoke Node. The Invoke Node uses the Insert VI method. To load a VI in the subpanel control, wire a reference to that VI to the Invoke Node." Remove VI unloads it, and stopping the VI clears it.

## 2 Corpus (2 VIs)

| VI | Inserted reference | Then |
|---|---|---|
| JKI-VI-Tester `EXE Test.vi` | a static VI reference to `Graphical Test Runner - Main UI - .vi`, opened by path; known at generation time | `Ctrl Val.Set` on it, `Insert VI`, `Run VI` (Wait Until Done TRUE), Close Reference |
| actor-framework `Add Attributes to Message.vi` | a VI made while running (`New VI`, VI scripting) | Remove VI in Event Structure frames |

- The actor-framework VI stays a gap whatever is built: VI scripting has no Python meaning.
- `EXE Test.vi`'s `Run VI` is already a gap on main (`run_vi.py:16-19`: a value set on the same reference before the run).
- So no corpus VI runs end to end with an embedded panel until Run VI after `Ctrl Val.Set` is converted.

## 3 Generated shape

The Subpanel gets a member in the VI's control enum, though it has no terminal; it is named by its label, made distinct the same way (D2). Insert VI and Remove VI change what the Subpanel's NI property **Inserted VI** returns. NI (`subpanel/insertedvi.html`): "Returns a reference to the VI currently loaded in the referenced subpanel control", Read Only. So each method reports the new value of that property through `on_property` (§10):

```python
if on_property is not None:
    on_property(ExeTestValue.SUB_PANEL, ExeTestProperty.INSERTED_VI, graphical_test_runner_main_ui)
...
if on_property is not None:          # Remove VI
    on_property(ExeTestValue.SUB_PANEL, ExeTestProperty.INSERTED_VI, None)
```

The value is the inserted VI's function: a VI reference known at generation time is an import (CLAUDE.md). A reference not known until the code runs is a gap, as for every VI reference.

## 4 Panel side

- The panel generator lists, for each Subpanel, the VIs any Insert VI on it can insert (known at generation time). It imports each one's generated panel builder (`<vi>_panel.build_panel`), generated with `--panel` like the rest of the package.
- `holder.property(SUB_PANEL, INSERTED_VI, fn)` clears the Subpanel's container and builds `fn`'s panel inside it, sized to the Subpanel's content bounds. `None` clears it.

## 4b The render nests the inserted VI's panel (maintainer, 2026-11-09: "we can render svg inside an svg also")

- `lvkit render --view front-panel` draws the inserted VI's own front-panel picture inside the Subpanel's content area (part 28): a nested `<svg>` at the content bounds, clipped to them, at 1:1. NI: "LabVIEW displays only the visible area of the front panel of the VI you load in the subpanel control."
- This happens when exactly one VI can be inserted into that Subpanel and its reference is known when the VI is loaded (the same resolution `generate` uses: `InMemoryVIGraph.referenced_vi`).
- With none, or more than one, the Subpanel draws empty, as LabVIEW shows it before Insert VI runs.
- The inserted VI's panel is drawn by the same front-panel renderer, so it is loaded at the depth that render already uses for dependencies. A cycle (a VI inserted into its own Subpanel) draws empty.

## 5 Live updates: the inserted VI's run updates the embedded panel

LabVIEW's point is that the inserted VI runs and its panel shows live. Today Run VI is `fn()` with no hooks (D6).

- Option A, display only (not chosen). The embedded panel shows the inserted VI's controls at their defaults; the run's values don't reach it. Small, no change to Run VI.
- **Option B, live (chosen, D21).** `on_property` for Inserted VI returns the embedded panel's hooks (`{"on_update": …, "on_read": …, "on_property": …}`) or None. A Run VI on the same reference passes them: `fn(**(inserted_hooks or {}))`. This is a narrow exception to D6, only for a VI the diagram itself put in a Subpanel.

## 6 Decisions

- **D20.** Insert VI / Remove VI report the new Inserted VI through `on_property`, with the Subpanel as an enum member. Recommend yes.
- **D21. DECIDED (maintainer, 2026-11-09): Option B, live, now** ("Why not live updates? That hard?"). Tested on a handmade VI: Insert VI, then Run VI of the same reference. The run's indicator writes reach the embedded panel's holder.
- **D22. DECIDED (maintainer, 2026-11-09):** embed at generation: "NiceGUI can import and embed exactly like this in generation and we can render svg inside an svg also" (11.4, 11.4b).
