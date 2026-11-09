# Plan: `generate` produces a runnable function module and an optional live NiceGUI front panel

Status: APPROVED by the maintainer 2026-11-07 ("Yes, your recommendations"), with D5 and D9 decided as recorded in section 5. Built on one branch as three PRs: (1) the hooks in generated code, (2) the NiceGUI panel, (3) Event Structures. Extends `nicegui-fp-reproduction.md` (the two-file split, one-way call direction and Run model stay as recorded there); this plan adds how a running VI exchanges live values and events with a UI.

Receipts: every claim cites `file:line` (paths under `/home/ryanf/repos/lvkit-fp/` unless a repo is named) or a command I ran.
"Not verified" marks what I could not check.

## 0. Which tree the citations point at, and two corrections to the brief

- **I cite the `feat/front-panels` checkout (`/home/ryanf/repos/lvkit-fp`, head `61bfc31b`) for all codegen seams.** The shared checkout
  `/home/ryanf/repos/lvkit` is on `main` (`27ce1868`), and `lvkit-fp` is 391 commits ahead of it with `main` as merge base
  (`git log --oneline main..HEAD | wc -l` = 391). That includes errors as values, the frozen-dataclass results and the new codegen modules
  (`local_indicators.py`, `front_panel.py`, `error_flow.py`, ...). Seams cited from `main` would be wrong.
- **Correction 1: `src/lvkit/codegen/function.py:161,195` is not the seam.** On `main` those lines are `_build_params` / `_build_outputs` of
  `FunctionBuilder`, which nothing calls (`grep -rn FunctionBuilder src` on `main` finds only its definition and the `codegen/__init__.py`
  export). `lvkit-fp` has no `function.py`. The live seams are `codegen/builder.py` `build_function_def` (:506), `build_args` (:542),
  `build_return_stmt` (:377), and `codegen/class_builder/methods.py:101` (the one other caller of `build_args`).
- **Correction 2: local variables ARE handled in `codegen/` on `lvkit-fp`** (on `main` there is none; `grep -rln "local variable" src/lvkit/codegen` on
  `main` finds nothing). Details in 2.2. There is a hole: a local variable write of a CONTROL generates nothing (2.2, test run).
- Also: `ARCHITECTURE.md` I read is the one on `main` (the brief's path). I did not check whether the `lvkit-fp` copy differs.
  `docs/_internal/design/errors-as-values.md` I read from `/home/ryanf/repos/lvkit-errvalue/` (not in the `main` checkout); that work is already on `lvkit-fp`.
- "Front panel injection" (`front_panel=None`, `fp.x`, `/home/ryanf/.claude/projects/-home-ryanf-repos-lvkit/TASKS.md` lines 56-57) is
  REPLACED by the callback design (decision 1 of 2026-11-07): `on_update(name, value)` and `on_read(name, value) -> value`, on the variables
  the function already has. There is no `front_panel` parameter, no `<VI>Panel` class and no `fp.x` access in the plan. TASKS.md already records
  the change (line 57, "2026-11-07 LATER").

## 1. Corpus count: VIs that read or write a front-panel value while running

### Method

Scripts: `/home/ryanf/repos/lvkit-fp/.tmp/count_fp_runtime.py` (main count), `count_value_nodes.py`, `count_dup_labels.py`. Output saved in `.tmp/count_fp_runtime.out`.
No `parse_vi` or graph build was run; only `xml.etree` over the pre-extracted cache, one VI at a time, tree dropped before the next.

- Files: every `*_BDHb.xml` under `~/.lvkit/cache` = 17,303 files; **de-duplicated by the sha256 of the file bytes = 4,598 unique block diagrams**
  (246 `adhoc`, 4,345 `projects`, 7 `shared`; `shared` is vilib/userlib, mostly not extracted). This is not the whole 5,261-VI corpus quoted in
  `errors-as-values.md`; it is what the cache holds. Control/indicator flag read from the sibling `*_FPHb.xml` (`fPDCO` `objFlags` bit 0,
  the same bit the parser uses: `parser/front_panel.py:63,105`).
- **FP terminal (`class="fPTerm"`) inside a loop or event structure:** I walked the XML with the stack of ancestor `class` values; a terminal is
  "inside a loop" when any ancestor is `whileLoop` or `forLoop`, "inside an event structure" when any is `eventStruct`. The corpus has no timed
  loops (`grep` for `timedLoop`/`tLoop`/`timedSeq` over every BDHb: 0 files). Class names `forLoop`, `whileLoop`, `eventStruct` are the parser's own
  (`parser/constants.py:15-16,21`). Terminals are stored under the diagram's `sRN` element, and a loop's diagram has its own `sRN`
  (verified on `TEST Performance Benchmark.vi`: ancestor chain `... diag, whileLoop, diag, sRN`), so ancestry is the right test. I also confirmed on a
  handmade VI that the graph keeps the same fact: `FPTerminal.parent` is the loop's id for a Stop control and an indicator placed inside a While Loop
  (scratch test `.tmp/test_scratch_loop.py::test_fp_terminal_placement`; output `parent=.../Scratch.vi::106`).
- **Local variable:** `class="gRef"` with no `connectorTM` (a global variable has one: `parser/node_types.py:157-159`) whose `paramIdx` is below the length
  of the front-panel heap's `ddoList` (the rule `parser/models.py:963-970` uses). A `gRef` whose index is past the list is a "This VI"-style reference,
  counted separately (`graph/vi_reference.py:247-259`). Read vs write: a `gRef` terminal that is a destination in a `signal` is a write, a source is a read.
- **Value property node on the VI's own controls:** `class="propNode"` with a direct `<ddo>` child (the "bound to a control" marker,
  `parser/node_types.py:1012-1013`) and a `<PropItemName>` equal to `Value`.

### Counts (unique block diagrams; 4,598 total)

| Kind | VIs | Detail |
|---|---|---|
| FP terminal inside a While/For Loop | **222** | 103 hold a control there (Stop button style, a read each iteration); 165 hold an indicator (a write each iteration); some hold both |
| FP terminal inside an Event Structure | 49 | 18 controls, 12 indicators, 19 whose flag the heap does not store |
| terminal in a loop or event structure (union) | 225 | |
| Local variable (to a control or indicator of the VI) | **161** | 123 read, 96 write; **103 of them inside a loop** |
| Value property node bound to the VI's own control | **34** | all spelled `Value`; 0 spelled `Value (Signaling)` (no `PropItemName` containing "Signal" anywhere in the cache: `grep -rh "<PropItemName>" ... | grep -i signal` empty). 10 VIs have a node whose names are all `Value`; 28 have a node with at least one other name (another row, or a deeper path segment of a Value row: I did not separate the two) |
| **Any of the above (union)** | **277 = 6.0 %** | only these functions would get `on_update=None, on_read=None` |
| Global variable nodes (not counted above) | 101 | a Global VI's controls, not this VI's panel; `codegen/global_module.py` already handles them |

Takeaways for sizing:
- 94 % of VIs stay plain functions. The hooks are a small, targeted change.
- All 34 Value property node VIs have a gap TODAY (I did not run them; this is from reading the code): `codegen/nodes/property_node.py:110-113` raises `ConversionGap("Implicit property reference requires a binding model")` for ANY bound property node.
- Not counted (not measured, method given): off-pane controls the diagram reads (they never reach a function today, see risk R6); event structure VIs that
  have no terminal inside (an Event Structure's registered event-source controls are not read as values).
- The same walk counted `gRef`/`fPTerm` per unique file, so a VI that calls many subVIs counts once; subVI propagation is not counted (hooks do not propagate, D6).

Also measured for decision D2 (naming): **357 of 5,151 unique front panels (6.9 %) have two top-level controls/indicators with the same label**,
143 have a control with no label at all (`.tmp/count_dup_labels.py`; label read from the `fPDCO`'s `ddo/partsList/label` part).

## 2. How codegen gets there

### 2.1 What generated functions look like today (run on handmade VIs; scratch tests in `.tmp/test_scratch_*.py`, run with `PYTHONPATH=. uv run pytest -n 0 -s`)

While Loop with a Stop control and a count indicator placed inside the loop, a shift register counter:

```
def scratch(stop: bool) -> ScratchResult:
    count = 0
    state = 0
    while True:
        x1 = state + 1
        count = x1
        state_updated = x1
        state = state_updated
        if stop:
            break
    return ScratchResult(count=count)
```

So today: the control is a parameter read as a plain variable (`context.py:1097-1101` binds each input terminal to its parameter name), an in-loop indicator
is a variable assigned in the loop (`local_indicators.py`), and the result is a frozen dataclass.
The loop never sees a new Stop value (`if stop:` re-reads the argument).

### 2.2 Local variables and Value property nodes today

- Local variable READ of a control: `codegen/references.py:1-8,28-32`: it "resolves through the wire to the control's parameter, so it generates
  nothing and is left out of the walk" (`generated_nodes` drops every `LocalVariableNode` that names a control). Correct only if the value never changes while running.
- Local variable READ of an indicator, and writes of connector-pane indicators inside a frame: `codegen/local_indicators.py` holds the indicator in a function variable
  (module docstring :1-25; `declare` :61; `_write` :130; `assignments_after` :141; `writes_in_frame` :158; kept between calls via `add_state_global` :117).
  Placed by `builder.function_body` (:168-174) and `generate_body` (:219,226).
- Local variable WRITE of a CONTROL: **generates nothing** (verified): scratch test `.tmp/test_scratch_local_write.py` built "constant 5 -> Local Variable write of control `n`; Local Variable read
  of `n` -> indicator `out`" and `generate` produced only `def scratch(n: int): return ScratchResult(out=n)`. No assignment to `n` exists
  (`indicator_local_reads` :145 and `indicators_written_by` :237 track indicators only; `generated_nodes` drops the write node). This is an existing bug the plan fixes as a side effect of rule R3 (it
  is in the path of the approved task: panels need it).
- "This VI" reference: `codegen/nodes/local_variable.py:20-27` (the only local-variable node that is generated).
- Value property node on an own control: gap (above). Explicit property nodes (wired reference) go through `nodes/property_node.py:147-175` as `ref.attr` reads/writes.
- Event Structure: a gap, `codegen/nodes/event.py:21-35`.

### 2.3 The one uniform rule: which access becomes which hook line

Rationale (maintainer, 2026-11-07): "one reason the in-a-loop control/indicator pattern exists was it was an easy way to get/set updates every loop. Otherwise you'd get the original tunnel or shift register value." The rule keys on where the terminal sits, not on the wiring.

A VI is "hooked" when a new graph query says its diagram does one of: a control or indicator terminal placed inside a loop or event structure (use `FPTerminal.parent`, `models.py:984`, walking enclosing structures); a local variable of
its own control/indicator; a Value property node bound to its own control; (open: an off-pane control the diagram reads, D8). Hooked VIs get two keyword-only parameters
`on_update=None, on_read=None` (annotation `Callable[[str, Any], None] | None` and `Callable[[str, Any], Any] | None`), after the connector-pane keywords. Everything else is unchanged.

| Access while running | Generated lines | Seam |
|---|---|---|
| Indicator written (terminal in a frame, via a wire, or a local variable write) | after the existing assignment `v = value`: `if on_update is not None: on_update("<name>", v)` | `LocalIndicators._write` (`local_indicators.py:130`), the one place every indicator assignment is made |
| Control terminal inside a loop / event structure | first statement of that diagram: `if on_read is not None: v = on_read("<name>", v)` (once per iteration) | `generate_body(frame=...)` start (`builder.py:218-219`), next to `writes_in_frame` |
| Local variable READ of a control | at the node: `if on_read is not None: v = on_read("<name>", v)`, then the node's output is `v` | `nodes/local_variable.py` + `references.generated_nodes` (keep the node in the walk in a hooked VI) |
| Local variable WRITE of a control | `v = value`, then `on_update("<name>", v)` | same node generator (this also fixes the dropped write, 2.2) |
| Value property READ / WRITE of an own control | same as the two rows above | `nodes/property_node.py:110` (replace the blanket gap for rows whose property is `Value`) |
| Off-pane control (hooked VI) | after `off_pane_locals` (`builder.py:159`): `v = on_read("<name>", v)` once | `function_body`, D8 |

Why a plain `if ... is not None:` and no helper: a helper that "does nothing" is a runtime emulation (CLAUDE.md "GENERATED PYTHON IS NATIVE PYTHON"). The guard costs nothing:
`.tmp/time_hook_call.py` on 1,000,000 loop iterations: bare loop 0.017 s, loop with the guard and `None` 0.018 s, loop calling a panel-style holder write (dict store + flag under a lock) 0.127 s = about 110 ns per call.

The read's initial value is the variable the function already has (the parameter, the saved default for an off-pane control), so the default behaviour is "unchanged".

Held-error behaviour: error clusters are plain values (`errors-as-values.md`); an `error out` indicator in a loop follows the same indicator rule; the panel binds `error_in`/`error_out` by name with no special code (that doc, "Why").

New module `src/lvkit/codegen/panel_hooks.py` (placement, see section 6 PR1): the hooked-VI decision, the hook name table, `update_stmt`, `read_stmt`, the two parameters. The graph query is `graph/wire_queries.py`
(351 lines) next to `indicator_local_reads` (:145). No change to `describe`, `lvnet`, JSON or the render: the hooks are code generation only (tests assert those views are unchanged).

### 2.4 The name passed (decision D2)

Recommended: the name the function already uses, from `interface_names` (`codegen/ast_utils.py:441-455`): the parameter name for a control (incl. off-pane controls, which `interface_names` already names), the
result field name for an indicator; extended once to name off-pane indicators (today `front_panel_docstring` makes them distinct on its own, `front_panel.py:60-66`). The label as LabVIEW stores it is not
unique (6.9 % of VIs, section 1), and a control and an indicator can share a label, so a second, panel-wide table is computed ONCE in `panel_hooks.py` (distinct across controls and indicators) and used by both
the logic (hook calls) and the panel generator (State fields). That removes the name-and-position guessing the prototype does (`scripts/panelgen/panel_gen.py:236-292`, `_match_args` and `_match_outputs`).
A cluster is passed whole (`on_update("cluster", value)`); the panel reads fields out of it. No per-field path is passed.

## 3. The panel generator, reconciled with today's rules

### 3.1 Where it lives and how it is run

- New package `src/lvkit/panel/` (new engine, own package from birth: `code-style.md` god-module rule). Entry point `write_panel(...)`, called from one place in `pipeline.generate_python` (`pipeline.py` is 1,137 lines; no panel logic goes into it).
- `lvkit generate <path> -o OUT --panel` writes, for each VI, `<vi>_panel.py` NEXT TO `<vi>.py`: the VI's module path is `vi_paths[vi_name]` (`pipeline.py:890`), so the panel is `output_path.with_name(f"{stem}_panel.py")`. The package mirrors the project folders; so do the panels. The default (no `--panel`) writes no panel and no nicegui requirement. The hook parameters are emitted regardless of `--panel` (they are part of the logic).
- Reads the graph, not generated source: `graph.get_vi_front_panel(vi_name)` (`graph/queries.py:1242`) → `ParsedFrontPanel`: `layout` (`parser/models.py:952`: pane/splitter tree with tab pages, groupers, free labels, decorations, `:849-935`), per-control `parts`/`pictures`/`hidden`/`cluster_geom`/`tab_pages`/`listbox` (`:685-812`), `fonts` (`:961`), plus `VIContext.inputs/outputs/off_pane_inputs/off_pane_outputs` (`graph/models.py:1106-1107`) for which controls are parameters. Terminal ids tie each widget to a parameter or result field: no name matching.
- Gaps follow the one rule: a control kind the panel cannot build warns at generation time (VI, control, kind, reason, through `run_report`/`GapLog` as `pipeline.py` already does for code gaps) and the generated `build_panel()` raises `NotImplementedError("<reason>")` at the place it would build that widget. A run with panel gaps is Partial: exit 3, in the summary, in `--format json`. A panel that cannot be written at all (two modules mapping to one file) stops the package (exit 1), as for code.
- The prototype's `UnknownStrategy` (a disabled `ui.input` plus TODO chip, `scripts/panelgen/strategies.py:171`) is the "silent placeholder" the gap rule forbids; it is dropped, not ported. To keep the number of gaps small, I recommend real conversions for the common kinds before raising: see 3.4.

### 3.2 `controls` helper package: needs the maintainer's approval (CLAUDE.md "A brief or plan that adds a runtime helper for generated code must name why Python has no construct for it")

Reason: NiceGUI offers primitive widgets (`ui.input`, `ui.number`, `ui.switch`, `ui.table`, `ui.echart`, `ui.aggrid`) but not LabVIEW's composites. Python and NiceGUI have no construct for: a LabVIEW array control (index display plus a window of element cells plus add/remove/reorder),
a Path control with a server-side Browse dialog, a Run/Run Continuously/Abort/Pause toolbar with the LabVIEW state model, a waveform indicator that takes a bounded deque, or a theme (Modern/Classic) applied over all of them. Writing each of them out inline in every `<vi>_panel.py` (the alternative) would repeat about 960 lines in every panel.
So one generated helper package, written once into the package root like `errors.py` (`pipeline.py:385-393`, `codegen/errors_module.py`), imported by name from each panel. It imports nicegui and the standard library only, never lvkit (so "Generated code imports nothing from lvkit" holds). It is written only when at least one panel is. A separate `pip install`able `lvkit-controls` is rejected: that is an lvkit import in disguise.

What goes in it (from the prototype, `scripts/panelgen/controls_runtime/`, all 962 lines): `theme.py` (72), `filepicker.py` (135, path control and Browse dialog), `arraygrid.py` (414, AG Grid array/cluster-array), `runtoolbar.py` (199, run controller and toolbar), `waveform.py` (58), `_util.py` (27), `__init__.py` (57); plus the new `LiveValues` holder (D4/section 4). Exact list is approved per file when each is ported (one PR each, section 6). The source lives in `src/lvkit/panel/runtime/` as tested files that lvkit never imports; the writer copies them (`shutil.copytree`, as `scripts/panelgen/generate.py:32-43` does).

### 3.3 Prototype module map (all read; sizes from `wc -l`)

| Prototype | Lines | What it does | New home | Verdict |
|---|---|---|---|---|
| `scripts/gen_panel.py` | 69 | CLI wrapper over `generate_panel` | `lvkit generate --panel` (`cli.py:2020 cmd_generate`) | dropped |
| `scripts/gen_gallery.py` | 248 | builds a NiceGUI gallery page of generated panels | `scripts/gen_gallery.py` stays a dev script, rewritten on the new entry point | kept as script, rewritten |
| `panelgen/generate.py` | 98 | orchestrates logic + panel + copy `controls/` | `src/lvkit/panel/write.py` | rewritten (reads graph, no logic generation of its own) |
| `panelgen/logic_gen.py` | 147 | loads the closure and writes flat `<vi>.py` modules with its own import resolver and polymorphic fallback | `generate_python` | **dropped**: it duplicates `pipeline.generate_python` and writes a flat directory, which breaks "package mirrors the project folders" and the gap rule. Its call `build_module(..., import_resolver=<function>)` (:130) no longer matches `build_module(import_resolver: ImportResolver | None)` (`builder.py:53`); I did not run it on this branch |
| `panelgen/loader.py` | 37 | imports a panel module by flat name with its directory on `sys.path` | `scripts/gen_gallery.py` helper (uses package imports) | dropped from the library |
| `panelgen/naming.py` | 27 | de-duplicated field names per control (declaration order) | `panel_hooks.hook_names` (section 2.4) | dropped (replaced by the one shared name table) |
| `panelgen/state_gen.py` | 70 | `@binding.bindable_dataclass State` per control; nested cluster dataclasses | `src/lvkit/panel/state.py` | rewritten (keyed by terminal id, from the shared name table) |
| `panelgen/panel_gen.py` | 414 | absolute layout from bounds; introspects the generated function to match controls to parameters by NAME then POSITION (:117-173); Run handler | `src/lvkit/panel/module.py` + `layout.py` | rewritten: layout from `ParsedFrontPanel.layout` (panes, splitters, tabs, groupers), mapping by terminal id; the name-then-position guess is the banned heuristic (`code-style.md` "NEVER use heuristic guesswork") |
| `panelgen/strategies.py` | 482 | one class per `control_type` string (string, path, numeric, bool, enum/ring, cluster, array) plus `UnknownStrategy`, all in one file | `src/lvkit/panel/widgets/` one file per kind under a base class | rewritten per `code-style.md` "variants → decomposed class hierarchy"; dispatch on the typed `LVType`/control kind, not on a class-name string |
| `controls_runtime/*` | 962 | the control library | `src/lvkit/panel/runtime/` | kept, ported one file per PR |
| `examples/waveform-nicegui`, `examples/build-path-nicegui` | | hand-built examples | become `tests`/docs examples | kept as references |

Which kinds exist in the corpus (`grep -rhoE '<ddo class="..."' --include=*_FPHb.xml`, occurrence counts over all cache files including duplicates, nested controls included): `stdClust` 27,056; `typeDef` 16,744; `stdNum` 16,403; `stdString` 12,762; `udClassDDO` 10,598;
`indArr` 9,267; `stdBool` 8,052; `stdRefNum` 2,971; `stdLvVariant` 2,643; `stdPath` 2,239; `stdRing` 1,855; `stdPict` 1,421; `treeControl` 696; `stdColorNum` 689; `stdGraph` 417; `stdComboBox` 377; `tabControl` 316; `radioClust` 316; `absTime` 95; `listbox` 85; `stdSlide` 74; `stdKnob` 46.
The prototype registers `stdString stdPath stdNum/stdNumeric stdBool stdEnum/stdRing stdClust indArr/array` (`strategies.py` lines 192,211,237,266,283,325,378). Everything else (refnum, variant, class instance, picture, tree, color, graph, combo box, tab, radio cluster, timestamp, listbox, slider, knob) is a gap until built. How `typeDef` and `udClassDDO` map to `ParsedFPControl.control_type` I did not verify.

### 3.4 What the panel emits (for one VI), from the design `nicegui-fp-reproduction.md` (§1-§3), unchanged

`<vi>.py` stays the pure logic (no UI import). `<vi>_panel.py` holds `State` (bindable dataclass, one field per control and indicator, from the shared name table), `build_panel()` (layout from `ParsedFrontPanel.layout`) and a `__main__` runner. The UI calls the logic one way, in-process now (`nicegui-fp-reproduction.md:82-93`).
The brief title says "runnable script": I found no record of a `__main__` in `<vi>.py` (`grep __main__ src/lvkit/codegen/*.py src/lvkit/pipeline.py` finds none). I read "runnable" as "the panel module runs with `python <vi>_panel.py`" (`nicegui-fp-reproduction.md:62`) and ask in D9.

## 4. Run model

### 4.1 Live updates (decision 2 of the maintainer, TesterKit's rule)

`/home/ryanf/repos/testerkit/docs/_internal/explorations/live-ui-pattern.md:6-37`: only NiceGUI's event loop touches a widget; background work writes a plain holder, and one `ui.timer` paints. The panel does exactly that:

- Run: `await run.io_bound(<vi function>, ...controls..., on_update=holder.update, on_read=holder.read)` (replaces the prototype's `compute()`; `scripts/panelgen/panel_gen.py:374-387`).
- `holder.update(name, value)` (worker thread): snapshot the value (copy; R3), store it as the latest for `name`, set a dirty flag; a bounded `deque` for waveform/chart indicators. Under a lock; about 110 ns (section 2.3).
- One `ui.timer(0.1, paint)` on the loop writes dirty values into `State`. `@ui.refreshable` only for structure changes.
- `holder.read(name, value)` returns the control's current value: widgets write `holder.controls[name]` from their change handlers (on the loop); the worker only reads a dict entry. Unwritten → returns the `value` it was handed (the default behaviour).
- Run ends: the returned result object (a frozen dataclass of the connector-pane outputs, `builder.py:398-404`) is painted onto the indicators, as in LabVIEW. `error_out` is just a result field now (`errors-as-values.md`), so `RunController`'s "we strip error clusters in favour of exceptions" error boundary (`controls_runtime/runtoolbar.py:40-58` docstring) changes: the banner shows `result.error_out`; an exception means a gap or an lvkit failure (the message names it).

### 4.2 Run, Run Continuously, Abort, Pause (`nicegui-fp-reproduction.md` §2)

- Run and Run Continuously: unchanged logic of `RunController` (`runtoolbar.py:40-120`): latch the controls, call the function, paint; continuous repeats after each completion.
- **Recommended Abort/Pause: through the hooks.** `holder.update`/`holder.read` check an abort flag and raise `RunAborted` (defined in the helper package, subclass of `BaseException` so no generated `except Exception` can swallow it; the generated native-failure handlers name specific exception types: `codegen/native_failure.py:142-149`), and block while paused. Reason: the function runs on a worker thread, which nothing can cancel from outside. Today's `RunController.abort()` only clears the `running` flag and the continuous loop exits "after the current run" (`runtoolbar.py:132-139`), so it cannot stop a run in progress at all; Pause only toggles a flag the continuous loop reads between runs (`:125-130`).
  Hooks fire in every loop that touches the panel; that is the loop a user watches and wants to stop.
- Limit (R1): a long loop that touches no front-panel value has no hook call, so Abort/Pause waits until it ends; and a hooked subVI's own loops are not covered (hooks do not propagate to subVIs, D6). Alternative if that is not acceptable: run the function in a child process and terminate it (true Abort), at the cost of copying every `on_update` through a queue and losing the module-level state the generated code keeps between calls (`local_indicators.py` `add_state_global`). I recommend the hook route first and measuring whether anyone hits R1.

### 4.3 Event structures

- Today a gap (`codegen/nodes/event.py:21-35`), 265 `eventStruct` elements in the cache, 49 VIs with a terminal inside one.
- What the hooks give them: the control values for a polling Value Change (`on_read`), and indicator writes inside frames. They do NOT give: blocking on "the event happened", the event data node, timeouts, filter events, lock panel. That needs a third hook (a blocking `on_event(sources, timeout) -> (event, data)` the panel fulfils from widget handlers) and a codegen of the event loop. Out of scope; state-machine VIs under Run (`nicegui-fp-reproduction.md:217-220`, build order item 5) stay unbuilt, and the event structure stays a gap with its existing message.

### 4.4 Dialogs (decision 3)

Today: One Button Dialog is `print(message)` (`data/vilib/dialog-user-interface.json:45`); Two Button Dialog has a call expression (`"python": "two_button_dialog(message, t_btn, f_btn)"`) but no function definition: `grep -n '"python_code"'` on the file matches line 45 (One Button) only, so I expect a gap; Prompt User for Input has `vi_path: null` and I found no template. I did not run these to see the exact gap.

TesterKit's chain (`src/testerkit/prompts/core.py:57-78`): installed handler (a ContextVar, `:37-49`), else `TESTERKIT_AUTO_CONFIRM` environment variable answers, else the terminal if stdin is a tty, else `PromptUnavailableError`.
Against the rules:
- Handler as a ContextVar = the ambient pattern decision 4 forbids. Reshape as an explicit keyword-only callable on the function: `on_prompt=None` (only on VIs with a dialog). A caller passes `on_prompt=my_handler`; the NiceGUI panel passes a function that opens a `ui.dialog` from the loop and returns the answer to the worker thread (`asyncio.run_coroutine_threadsafe`). Fits "a VI is a function, the connector pane is the signature"; it is a third parameter, i.e. an extension of the two in decision 1 → D5.
- Environment-variable auto-answer: process state that changes the output with no trace in the code. Rejected for generated code (a TesterKit test passes its handler instead).
- Terminal prompt = Python's own `input()` (native). With no handler: `input()`; with no tty Python raises `EOFError` at run time, which is a clear error but not the one gap mechanism. Alternatively the node is a normal gap when no handler: warn at generation, raise `NotImplementedError`. Which one is a design fork (D5).

## 5. Decisions for the maintainer (each with my recommendation and evidence)

- **D1. `on_update` on every write or throttled.** Recommend: **every write, in the logic; the panel coalesces.** Evidence: 110 ns per call (section 2.3), about 1 ns per iteration when the hook is `None`; a hook that throttles needs a clock and state inside generated code (an lvkit-invented behaviour); TesterKit's `channels.write(name, sample, *, namespace=None)` (`testerkit/channels.py:81`) wants every sample as a channel stream; the panel's holder keeps only the latest per indicator (and a bounded deque for charts).
- **D2. Label vs path naming.** Recommend: **the Python interface name, unique across all controls and indicators of the VI, from one table shared by logic and panel; clusters passed whole.** Evidence: duplicate labels in 357 of 5,151 front panels (6.9 %), 143 with an unlabeled control; a control and an indicator can share a label; the function already derives distinct names with `interface_names` (`ast_utils.py:441-455`); the prototype's name-then-position matching (`panel_gen.py:236-292`) is the banned guess.
  **DECIDED (maintainer, 2026-11-07): names are a generated `(str, Enum)` per hooked VI, imported by the panel with the function** (`class AcquireValue(str, Enum): COUNT = "count"`; hook calls pass the member, `on_update(AcquireValue.COUNT, count)`; the value stays the name string, so `member == "count"`).
- **D3. The `controls` helper package approval.** Recommend: **approve one generated helper package at the package root, written only with `--panel`,** reason in 3.2. Alternative: inline per panel (about 960 repeated lines each).
- **D4. Whether the hook parameters appear without `--panel`.** Recommend yes: the logic must not depend on the flag (one output; TesterKit can use the hooks with no panel). Cost: 277 of 4,598 VIs (6 %) have two extra keyword-only parameters.
- **D5. Dialogs.** Recommend: an explicit `on_prompt=None` keyword on VIs with dialogs; headless fallback `input()` for Two Button / Prompt User (a real Python meaning), One Button stays `print`. Ask: (a) accept a third hook parameter; (b) `input()` fallback or a gap when no handler. Rejected from TesterKit: the ContextVar and the environment auto-answer. **DECIDED (maintainer, 2026-11-07): "Input, same as the TesterKit fallback actually"** — `on_prompt` handler when passed; otherwise a terminal prompt via `input()` (TesterKit `prompts/core.py:72-73`, `:113-133`); with no terminal (stdin not a TTY) it raises a clear error, as TesterKit does (`:75-78`).
- **D6. Do hooks propagate to subVIs?** Recommend **no**: a LabVIEW subVI's panel is not shown while it runs; its `on_update`/`on_read` stay `None`. Cost: Abort cannot reach inside a hooked subVI (R1).
- **D7. Where the panel gets controls that are not on the connector pane.** Recommend: from the graph (`VIContext.off_pane_inputs/outputs`, `ParsedFrontPanel.controls`), shown on the panel like any other; values reach the function only through the hooks (`on_read` at entry for an off-pane control; D8), and off-pane indicators are shown because a hooked VI holds every indicator in a variable. Evidence: `front_panel.py:1-7,29-38` already makes an off-pane control a local variable set to its saved default, and an off-pane indicator is "written by the diagram, not returned".
- **D8. An off-pane control the diagram reads once at the top** makes the VI hooked (an `on_read` at entry), otherwise the panel can show it but the function never sees the user's value. Recommend yes; this goes beyond decision 1's list (terminal in a loop, local variable, Value property node). I did not count how many VIs this adds (R6).
- **D9. "Runnable script".** Recommend: the function module stays a function; the runnable thing is `python <vi>_panel.py`. Ask if a `__main__` in `<vi>.py` was meant. **DECIDED (maintainer, 2026-11-07): no `__main__` in logic modules** — "Not every file has just one method" (a LabVIEW class is one Python class with many methods); headless = import and call; `python <vi>_panel.py` is the runnable form.
- **D10. Hold every indicator in a variable in a hooked VI** (so each gets `on_update`), versus only the ones `local_indicators.py` holds today (read by a local variable or written in a frame). Recommend all: uniform rule, off-pane indicators become visible; costs one `x = default` line per indicator.
- **D11. A bound Value property node with other property rows** (28 of 34 VIs): per node, Value only → hooks, anything else in the node → the node stays a gap (as today). Recommend per node (keeps the one gap mechanism). Alternative: per row.

## 6. Phased steps (one PR each; 24-hour hold applies to each)

Shared pieces settled BEFORE any parallel work (brief agents with them as input): (a) the hook name table (2.4, D2); (b) the hook form: `if hook is not None:` guard, `on_update(name, value)`, `on_read(name, value)`, keyword-only `=None` after the pane keywords; (c) the graph query "VI touches the panel while running" and "enclosing loop of a front-panel terminal" in `graph/wire_queries.py`; (d) the `LiveValues` holder's interface (`update`, `read`, `controls`, `abort`, `paused`). PR1-PR4 all touch `local_indicators.py`/`builder.py`/`context.py`, so they are sequential; PR5 (dialogs) and PR6 (panel package skeleton) can start after (a)-(d) are fixed; PR6 reads names from (a) only.

Test pattern for every PR: a tiny handmade VI per case with `tests/error_diagrams.py` (`Vi`, `Project`, `project.generate`, `project.views`, as `tests/test_kept_indicators.py` and `tests/test_conditional_indicators.py` do), run through describe, lvnet (terse and `-v`), JSON, the render and generate; assert the exact generated text AND execute it with fake hooks (`feedback_execute_generated_code`). Neighbouring cases: a VI with a loop and no front-panel terminal inside gets NO hook parameters; a plain function VI is unchanged.

1. **PR1: hooked-VI query, name table, signature, `on_update` for indicators.**
   Placement: `graph/wire_queries.py` (query), new `codegen/panel_hooks.py` (names, decision, parameters, `update_stmt`), `codegen/builder.py` `build_args`/`build_function_def` + `class_builder/methods.py:101` (signature), `codegen/local_indicators.py` `_write` (statement), `declare` (hold all indicators of a hooked VI, D10). Tests: indicator in a For Loop (`out = x` inside the loop, existing test `test_conditional_indicators.py:150-172`) gets `on_update("out", out)` after the write; call with a recording `on_update` and assert (name, value) sequence; with `None` the result is identical to today; a VI with no loop terminal has no hook parameter in its signature; other views unchanged. Done: hooked VIs call `on_update` on every indicator write; plain VIs byte-identical to before (diff of the generated corpus sample for the tiny cases).
2. **PR2: `on_read` for control terminals inside loops, and off-pane controls at entry.**
   Placement: `panel_hooks.read_stmt`; `builder.generate_body` start-of-diagram next to `writes_in_frame` (:219); `function_body` after `off_pane_locals` (:159). Tests: the While Loop / Stop case of 2.1: run with an `on_read` that returns True on the 3rd call and assert the loop ends after 3 iterations (today it never ends); a control inside a case inside a loop. Done: the loop test passes and the `None` run equals today's output.
3. **PR3: local variables of controls (read and write) and the dropped write.**
   Placement: `codegen/nodes/local_variable.py` generates control reads/writes in a hooked VI; `codegen/references.py` `generated_nodes` keeps them in the walk; a control is a variable the way an indicator is (`local_indicators.py` pattern; to be decided at brief time whether it extends `LocalIndicators` or a sibling module; it must not grow `local_indicators.py` past one job). Tests: the dropped-write VI of 2.2 (expected: `n = 5` then the read yields 5, and `on_update("n", 5)`); local write then read inside a loop; read of an indicator local unchanged. Done: the write is no longer dropped in ANY VI, hooked or not (it is a plain assignment when not hooked; fix it generically).
4. **PR4: Value property node on own controls.**
   Placement: `codegen/nodes/property_node.py` (:110 gap → per-node split by D11) calling `panel_hooks`. Tests: Value read, Value write, Value plus Visible (stays a gap, warn + raise, exit 3). Done: the 10 VIs whose nodes are only Value generate; the others still show one gap each.
   Built (2026-11-07): `property_node._bound_value` calls `write_control` / `read_control` of `nodes/local_variable.py`; the control is found by `InMemoryVIGraph.bound_control` (`graph/wire_queries.py`), matching the node's bound uid with a control's own object uid. `Value (Signaling)` is a different stored name, so it stays a gap. A node bound to a part of a type-defined control works: `_resolve_bound_control_types` (`parser/vi.py`) records the enclosing control (`bound_control_dco_uid`, carried on `PropertyNode` and `PrimitiveNode`) and `bound_control` matches it. Corpus (9 VIs with Value-only bound nodes): 21 such nodes generate, 2 stay gaps (bound to indicators).
5. **PR5: dialogs** (D5): `on_prompt`, One/Two Button Dialog, Prompt User for Input templates in `data/vilib/dialog-user-interface.json`. Tests execute the function with a fake handler.
6. **PR6: panel package skeleton and `generate --panel`.**
   Placement: `src/lvkit/panel/{write.py,model.py,state.py,module.py,layout.py}`, `widgets/{base.py,...}`, `runtime/` (empty `LiveValues` + toolbar first); one call in `pipeline.generate_python`; `cli.py` `--panel`; exit-code/gap plumbing through `run_report`. Tests: a tiny VI with a numeric control, a string control and an indicator: panel source compiles, `build_panel()` builds headless, `LiveValues` round trip, a gap control (e.g. a tree control) gives a warning, exit 3 and a `NotImplementedError` in `build_panel`. NiceGUI's own test fixture: not verified to exist here; settle in R4. Done: `lvkit generate X.vi --panel` writes `x_panel.py` next to `x.py`, and `python x_panel.py` serves a panel that runs the function.
7. **PR7: live run model**: holder + `ui.timer`, Run / Run Continuously / Abort / Pause via the hooks, error banner from `result.error_out`. Tests: abort during the Stop-button loop of PR2; pause blocks and resumes.
8. **PR8..: one control kind per PR, from the prototype**: numeric, boolean, string, path, enum/ring, cluster, array (arraygrid), tab control, graph/waveform. Each ends with the render comparison to the stored panel (screenshot vs the stored bounds) per CLAUDE.md "Commit Rules".
9. **PR9: TesterKit example**: a test passing `on_update=` a fake with `write(name, sample, *, namespace=None)`'s signature (the real one is `testerkit/channels.py:81`); docs page. No import of testerkit in lvkit.

Each PR's own checks: hook before/after diff of every view on the VIs the changed code reaches (the in-loop, local-variable and Value-property VIs of section 1 plus the tester's examples), the pre-commit hook, the full suite once per PR.

## 7. Risks and unknowns (how to settle each)

- **R1. Abort/Pause only at hook calls.** Settle: build PR7, then list the loops of the 222 in-loop VIs that have no panel-touching terminal (would need a hooked VI to call into); measure with the subVI-heavy examples. Fallback: child process (4.2).
- **R2. Is it safe to write NiceGUI `BindableProperty` from the worker thread?** TASKS.md says "check first". Design avoids it (worker writes the holder; the loop paints) so it is moot if the rule is kept; settle in PR7 with a test that runs the function in `run.io_bound` and asserts no widget is touched off the loop. Not verified.
- **R3. A value passed to `on_update` can be changed in place later.** A held list or cluster may be modified in place by a later node; the codegen copies only when it sees another reader (`graph.value_is_shared`, `wire_queries.py:269`), and a hook is a reader it does not know. Settle: in PR1 add a test (array indicator updated in a loop whose body also changes the array in place); recommended fix: the panel snapshots on `update` (copy), not the generated code; measure the cost on a 100,000-element array.
- **R4. Test of panels.** I could not confirm NiceGUI's `nicegui.testing` user fixture is available (`grep nicegui pyproject.toml` finds no entry: it is not a dependency). Settle: add it as an optional extra and a dev dependency; keep `src/lvkit/panel/runtime/` out of pyright's strict set (the files import `nicegui`, and lvkit never imports them). Panel tests that need nicegui carry a mark so the hook-time suite stays small.
- **R5. A hook-time check that a control inside a case inside a loop is found.** `FPTerminal.parent` is set for a loop (verified on one handmade VI); for a terminal under a case structure inside a loop I expect `parent` is the case and a walk up is needed (`FPTerminal.parent` doc, `models.py:975-984`). Settle in PR2 tests with nesting.
- **R6. Off-pane controls the diagram reads.** Not counted. Method: `VIContext.off_pane_inputs` over a capped sample (for example 150 VIs, `load_vi_by_path(..., LoadMode.MINIMAL)`), count the ones with at least one wire from them. Needed before D8.
- **R7. TesterKit accepts what `on_update` passes.** `channels.write` documents "same shape rules" as `Context.observe` (`testerkit/channels.py:84-95`); I did not check whether a cluster (`SimpleNamespace`) or a list of clusters is accepted. Settle in PR9 by calling the real function with a cluster.
- **R8. `typeDef`/`udClassDDO` control kinds** (16,744 and 10,598 occurrences): how they appear in `control_type` and whether the panel needs the typedef's own kind. Settle in PR8 by dumping `control_type`/`lv_type.kind` for a handful of VIs.
- **R9. The prototype on this branch.** I did not run `scripts/gen_panel.py` here (no sample VI path was at hand in this checkout; samples exist under the shared checkout's `.lvkit/cache/samples/`). It is dropped from the library either way; settle only if the gallery script is kept.
- **R10. Panel layout fidelity.** The prototype lays out top-level controls absolutely from bounds and flows cluster children in a column (`panel_gen.py` module docstring), because cluster child geometry was missing. `parser/layout.py:669 _cluster_field_geoms` and `ParsedFrontPanel.layout` now exist; settle per control kind in PR8 by comparing a screenshot with the stored bounds.
- **R11. Event structure VIs (49 with a terminal inside, 265 `eventStruct` elements)** stay gaps; the panel for them shows the controls but the VI does not run. Say so in the gap message (existing text, `event.py:29-35`).
- **R12. ARCHITECTURE.md/docs.** The new panel engine and the hook rule must be added to `ARCHITECTURE.md` and `codegen/README.md` in the same PR that lands them; I did not edit either.
- **R13. Not verified: a Value (Signaling) property.** Zero occurrences by name; the signaling variant might share the name `Value` and differ in a field I did not read. Settle by reading a `propItem` row of a node known to be signaling; no such VI found in the cache.

## 8. What I changed

Only `/home/ryanf/repos/lvkit-fp/.tmp/` scratch files: `nicegui_plan.md` (this file), `count_fp_runtime.py` + `.out`, `count_value_nodes.py`, `count_dup_labels.py`, `check_fp_loop_hit.py`, `time_hook_call.py`,
`test_scratch_local_write.py`, `test_scratch_loop.py`. No branch, commit, source, test or doc file changed; `/home/ryanf/repos/lvkit` was only read.
(`uv run pytest` of the scratch tests wrote under `/tmp/pytest-of-ryanf/`.)

## 9. Event Structures: the UI pushes events in (added 2026-11-07)

This section SUPERSEDES section 4.3 ("stays a gap, needs a third hook").

### 9.1 Corpus: Event Structure VIs and event kinds

Script `.tmp/count_events.py` (unique BDHb by hash, 4,598; reads `eventStruct` elements and each frame's `EventSpec` `type` and `ddoUID`, the fields `parser/nodes/event.py:6-25` uses).
**94 VIs hold 99 Event Structures; 25 VIs have a Register For Events node.** Event kinds (VIs using it / frames), type codes named only where `_CONFIRMED_EVENT_TYPES` (`event.py:86-104`) names them:

| Kind | VIs | Frames |
|---|---|---|
| Panel Close? (filter, no control) | 63 | 63 |
| Timeout | 62 | 67 |
| Value Change on a control | 58 | 380 |
| code 0x3E8, no control, not in the confirmed table | 27 | 64 |
| Value Change with no control (not examined) | 5 | 17 |
| Mouse Down (control 23 frames / no control 4) | 4 | 27 |
| Mouse Up | 2 | 5 |
| Menu Selection (User) | 3 | 3 |
| Application Instance Close | 2 | 2 |
| about 20 other unconfirmed codes (0x40000034, 0x80000033, ...) | 1-3 each | |

Build first: Timeout, Value Change on a control, Panel Close? (together they cover most of the 94). Next Mouse Down/Up and Menu Selection. Unconfirmed codes stay gaps naming the code (no guessing, `event.py:90-95`). What 0x3E8 is: not verified; settle by reading its Event Data Node fields in one VI (not by NI's LabVIEW).

### 9.2 One message shape, both directions

`on_update` is the logic pushing `(label, value)` out. Events are the UI pushing in, with the SAME label (the hook name table, 2.4) and the SAME value form (the value a variable of that type holds). Proposed:

- out: `on_update(label, value)`.
- in: a message on a `queue.Queue` passed as `events=None`: `(label, value, kind, old_value)`. The first two items are exactly an `on_update` message, so a panel or TesterKit that receives `("x", 5)` can send `("x", 6, "Value Change", 5)` back with the same label. `kind` is the event name from the table above (`"Value Change"`, `"Mouse Down"`, `"Panel Close?"`, ...); `old_value` is None where the event has no old value. A plain tuple, no class, so generated code imports nothing.
- `on_read(label, value) -> value` stays the PULL form. Rule of thumb for what a VI needs: **a control read each iteration of a loop with no Event Structure (a Stop button, a polled numeric) uses `on_read`; a diagram that waits on an Event Structure uses `events`.** They combine: inside an event frame, a control that is not the event's source (read of another numeric when a button is clicked) is a terminal read in the loop, so it still uses `on_read`. A Value Change frame reads the new value from the Event Data Node (the message), not `on_read`.
- Hooked-VI query (2.3) therefore also includes "has an Event Structure" and adds `events=None` only for those VIs; the three parameters are independent keywords.

### 9.3 Generated shape

```
event = None                           # inside the loop that holds the structure
try:
    event = events.get(timeout=timeout_ms / 1000)   # timeout wired: Timeout terminal; -1 / unwired: no timeout argument
except queue.Empty:
    pass
if event is None:        -> the Timeout frame (outputs: Event Data Node timeout fields)
elif (event[0], event[2]) is frame k's (source label, kind):  -> frame k with its Event Data Node terminals bound (NewVal = event[1], OldVal = event[3])
```
`queue` is a stdlib import, a native construct (CLAUDE.md "a queue is `queue.Queue`"). Frame matching must use graph facts (the EventSpec's control uid and type code), not the label text: today `EventFrame` keeps only `event_label` (`parser/nodes/event.py:194`), so the control terminal id and type code must be added to `EventFrame` (`models.py`) first; matching by label text would be the banned string match. Seam: `codegen/nodes/event.py:21` (replace the gap; the structure is generated like a Case Structure, the way `nodes/case.py` does, with the frame selected by event); unconfirmed event codes keep today's gap with the code in the message.

### 9.4 Headless with no source

With `events=None`: only Timeout can fire. A timeout wired as a constant or value runs the Timeout frame after waiting that long, as LabVIEW with nobody touching the panel. With no timeout wired (or -1) nothing can ever wake the structure, so waiting is a hang. Recommended: not acceptable as a silent hang; generate `if events is None and <no timeout>: raise NotImplementedError("waits for a front-panel event; pass events=")` at that point, with the warning at generation time (the one gap rule, no new mechanism; this is the "nothing to resolve to" gap of CLAUDE.md "VI reference with nothing to resolve to" in spirit). Option B: block forever (faithful, but a stuck test). Decision D13.

### 9.5 Panel side

- The panel's change handlers (on the NiceGUI loop) do `events.put((label, value, "Value Change", old))`; `queue.Queue.put` is thread-safe, so the worker blocked in `events.get` wakes. Same handlers already keep `holder.controls` current for `on_read` (4.1). Mouse and menu events likewise from widget handlers where the kind is supported.
- Window close: NiceGUI's client disconnect/`app.on_shutdown` puts `("", None, "Panel Close?", None, reply)` and waits.
- **Filter events need a reply.** A filter event (Panel Close?, and any "?" event) lets the frame veto or modify it before LabVIEW acts. Proposed reply path: a filter event message carries a fifth item, a one-slot `queue.Queue` the sender made. The frame runs, then `reply.put((discard, modified_value))` (discard = the Event Filter Node's Discard? value, modified value = what the filter node returns, else the original). The panel waits on `reply` (from a thread, not the loop: `await run.io_bound(reply.get)`) before closing the window; discard True keeps the window open. Non-filter events have four items; the structure sees `len(event) == 5` as the filter case, known per frame from the graph, not guessed at run time.
- TesterKit can feed events the same way: `events.put(("start", True, "Value Change", False))`.

### 9.6 User events and dynamic registration: what maps and what does not

- Maps onto the same queue: Create User Event / Generate User Event / Register For Events when the user event is created and generated in the same VI or its subVIs. `Generate User Event` = `events.put((label, data, "User Event", None))` on the queue its Register For Events came from; the user event's name is the label. 25 VIs have a Register For Events node; the 0x3E8 frames (27 VIs) may be these (not verified).
- Out of scope (named gaps with the node and reason): registering events on a control reference or VI reference wired in at run time (Register For Events with a refnum not known at generation), Unregister, dynamic event reference property use, `Lock Panel Until Filter Events Complete`, timeout of -1 combined with an Event Structure inside a subVI that has no panel, Mouse Down details (position, modifiers) until the event data fields are decoded, and panel-wide events (resize, focus).

### 9.7 Decisions added

- **D12. The in-message shape**: `(label, value, kind, old_value)` tuple, filter events add a reply queue. Recommend yes; first two items equal an `on_update` message.
- **D13. No source and no timeout**: recommend the generated run-time raise plus generation-time warning (9.4), not a silent hang. **Replaced 2026-11-08 by Option B:** the maintainer chose "Faithful". LabVIEW waits for ever when Timeout is unwired or -1 ([LabVIEW Wiki](https://labviewwiki.org/wiki/Event_Structure), [NI event structure reference](https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/structures/event-structure.html)), so the function called with no `events` does too. No gap, no raise.
- **D14. Which kinds first**: Timeout, Value Change, Panel Close? first; Mouse Down/Up and Menu Selection second; unconfirmed codes stay gaps.
- **D15. User events**: map Create/Generate/Register for same-VI user events onto the same queue; everything dynamic stays out of scope.

### 9.8 Phased steps added

- **PR3b (before the panel): `EventFrame` carries the source control id and event type code** (parser/graph), with tests on handmade Event Structures through every view (describe, lvnet, JSON, render unchanged except where they now show the code for an unconfirmed event).
- **PR10: Event Structure codegen** (`nodes/event.py`, after PR2 and PR3b): Timeout + Value Change + Panel Close? with `events=None`; tests execute the function with a prefilled `queue.Queue` (a Value Change message, then a timeout) and assert frame order and the Event Data Node values; the no-source/no-timeout raise; a filter reply test.
- **PR7 grows**: the panel's change handlers put events, window close puts Panel Close? and waits for the reply.
- PR11: user events. PR12: Mouse and Menu kinds.

### 9.9 Risks added

- R14: matching by control id requires the EventSpec's `ddoUID` to map to the same control terminal as the graph's FP terminal (`fp_dco_uid`, `models.py:971`); verify on `Measurement UI.vi` (named in the request; I did not locate or open it).
- R15: a Value Change event on a control the user also reads with `on_read` in the same iteration: order is the panel's `put` vs `get`; LabVIEW guarantees the frame sees the event's new value. Test in PR10.
- R16: dynamic registration appears in all 99 structures only as the structure's own always-present tunnel pair; I did not count VIs that really register events other than through the 25 Register For Events nodes.

### 9.10 Built (feat/live-events)

D12 to D15 were approved ("Yes, your recommendations"). What was built, and where it differs from 9.2 to 9.8:

- **PR3b.** `EventFrame.events` is a list of `FrameEvent` (type code, `source_ddo_uid` from the file, `source_terminal_id` set at graph build), not one source and one code: 16 of the 95 Event Structures in the cached corpus list a frame twice (a frame that handles several events), and the label keeps showing the last one only. R14 verified on `VI Tester About.vi` and `Debug Interface.vi`: the `ddoUID` of a frame is the front-panel object whose control terminal is `"Cancel"`, `"stop"` and so on. Describe, lvnet, JSON and render are unchanged (the label text is the same; an unconfirmed code already showed as `<unknown event 0x...>`).
- **PR10.** As 9.3, with these differences. The kind enum is `<Vi>Event` (`VALUE_CHANGE`, `PANEL_CLOSE`), beside the name enum. A frame is matched by kind and, for a Value Change, the control's hook name. The Timeout frame is the `event is None` branch; a Panel Close? message no frame handles is answered `(False, None)` (not discarded). With `events=None` and a Timeout the function waits that long on an empty queue it makes and runs the Timeout frame. Event Data Node fields are found by the name the cluster gives them, not by index: the index of OldVal/NewVal is 5/6 with a hidden unnamed field before them and 4/5 without (`Reference Method Dialog.vi`). Only OldVal and NewVal are bound; Source, Type, Time, CtlRef and VIRef are not carried by a message, and a frame that reads one is a gap that names it. A Timeout frame's Type and Time are therefore a gap when wired. The frame sees the event's NewVal on the terminal of its own control (R15, tested).
- **D13.** Built as Option B (replaced, see 9.7): with no `events` and no Timeout the function waits for ever on an empty queue, as LabVIEW does, and no gap is recorded. Event Data Node fields are kept by name (maintainer, 2026-11-08: "By name. The event data node cluster is selective like a property node").
- **PR7 grows.** A control's change handler puts the Value Change while a run is active, the toolbar's Close button asks Panel Close? and waits for its reply, Abort and Pause reach a VI blocked in its Event Structure. A browser tab closed by the browser is not intercepted.
- Corpus VI served: `Debug Interface.vi` (JKI-VI-Tester), Value Change on its `stop` control only; its Event Structure has no wired Timeout, so it is the one gap above.

## 12. User events across VIs (added 2026-11-09)

Replaces D15's "same-VI user events" scope (maintainer, 2026-11-09: "List of queues, any VI"). Everything else in §9 is kept: the message shape (D12), `events` for the panel, and the Event Structure's dispatch (`codegen/nodes/event.py`).

### 12.1 Evidence

- **NI:**
  - Create User Event: "Wire the user event out output to a Generate User Event function to send the event and associated data to all Event structures registered for the event."
  - Generate User Event: "Broadcasts the user event … to each Event structure registered to handle the event."
  - Register For Events: "References must be to local objects."
  - A user event is a refnum like any other, so it passes between VIs.
- **Corpus:** in the extracted diagrams, 20 VIs Create, 37 Generate, 12 Destroy and 29 Unregister. 23 VIs generate an event they did not create. Example: JKI `Graphical Test Runner - Main UI` creates, and the `TestResult` methods (`addSuccess` …) and `TestSuite:run` generate.
- **Priority:** all 39 Generate User Event nodes in the 24 corpus VIs that have one leave priority unwired, so they use normal priority.
- **How a frame names its user event:** the frame's `EventSpec` has `type` 1000 (User Event), `source` 1 (dynamic) and `dynIndex` k ≥ 1. That k is the k-th user event row of the Register For Events wired to the structure's dynamic terminal.
  - Checked on JKI `Results Status Update Process__core.vi`: 4 rows; the frame with `dynIndex` 4 reads `testStarted`, the 4th row's data.
  - The parser does not keep `dynIndex` today (`parser/nodes/event.py:273` reads only `type`). So 3 of those 4 frames are labelled `<unknown event 0x000003E8>` in describe and lvnet.

### 12.2 Generated shape (plain Python, no helper)

```python
ev = []                                   # Create User Event: the list of registration queues
reg = (Queue(), (ev_a, ev_b))             # Register For Events: one queue, the user events by row
for registered in reg[1]:
    registered.append(reg[0])
for q in ev:                              # Generate User Event
    q.put((ev, data, Kind.USER_EVENT, None))
for registered in reg[1]:                 # Unregister For Events
    registered.remove(reg[0])
ev.clear()                                # Destroy User Event
```

- **The message keeps D12's shape.** The name position holds the user event itself, the list object, and its value is the event data.
- **The Event Structure** wired to a registration reads `reg[0]`, not `events`. A frame with `dynIndex` k is chosen by `event[0] is reg[1][k - 1]`, by identity, never by label.
- **A structure with both front-panel and user events:** the panel's messages and the registration's must reach one queue. Register For Events then uses the VI's `events` queue when the VI has one (`reg = (events, …)`), so one `get` serves both.
- **Event Data Node fields of a user event** are the event data's fields by name (maintainer, 2026-11-08: "By name"); `UsrEvtRef` is `event[0]`.
- **Register For Events with its input wired** (an existing registration): same queue, rows replaced (NI: "re-register").

### 12.3 Gaps

- Generate User Event with a priority other than an unwired or constant normal one (the switch to `PriorityQueue` is described in the 2026-11-09 decision: one place, but it changes the message shape for outside callers).
- Register For Events rows of a control, VI or application reference (dynamic front-panel events).
- A `dynIndex` that has no row.

### 12.4 Decisions

- **D24. DECIDED (maintainer, 2026-11-09):** a user event is a list of registration queues, in any VI.
- **D25. DECIDED (maintainer, 2026-11-09):** plain `Queue`; high priority is a gap until a VI needs it.

### 12.5 Steps

1. **Parser:** `FrameEvent.dyn_index` from `dynIndex`. A dynamic event's frame label names the registered row's user event (`<testStarted>: User Event`), in describe, lvnet, JSON and the render. Tests on a handmade structure with 2 rows.
2. **Codegen:** Create / Register / Generate / Unregister / Destroy, and the Event Structure's user-event frames. Tests execute a handmade VI that creates, registers and generates, with a frame reading the data. Also a two-VI case where one VI generates, the other creates and registers, and the user event passes as a parameter.

### 12.6 Revised after step 1's research (maintainer, 2026-11-09)

- **D26. DECIDED: frame names come from the registration's type.** Register For Events copies the label of each user event wired into a row into the type of the registration it outputs (the Event Registration refnum type lists one entry per row, each a User Event refnum type whose `Label` is the row name: JKI `Results Status Update Process__core.xml` type at line 5084). That type travels with the wire through subVI outputs, shift registers and clusters, so the names are the ones the Register For Events node stored wherever it is. WaveGen's three registrations come from WaveGen's own node and from two `Read Event.vi` subVIs.
  - The frame for `dynIndex` k is the k-th row, counted across the registrations of a cluster in field order.
  - A cluster field gives LabVIEW's own `Field.Label` form; LabVIEW's stored label for WaveGen's frame 2 is `[2] <Action.Event Data>: User Event`.
  - A dynamic terminal whose type carries no rows (a bare `EventReg refnum`, DCAF's subVI output) keeps today's label.
- **D27. DECIDED: one structure, several registrations.** When the Event Structure first runs, it makes its own queue. For each registration it listens to, it moves the waiting messages over and re-points each of the registration's user-event lists from the old queue to its own (`ev.remove(old); ev.append(mine)`). This replaces §12.2's "the Event Structure … reads `reg[0]`" for a cluster of registrations.

### 12.7 Decided for code generation (maintainer, 2026-11-09)

- **P1. A registration is a triple.** `reg = (Queue(), rows, registered)`. `rows` is in dynIndex order for frame tests: a user event, a tuple of the events of an array row, or `None` at a cluster row's own index. `registered` is the flat tuple of every user-event list that holds the queue (arrays expanded at registration). Register, Unregister and re-register loop over `reg[2]`, so they need no row layout. This replaces §12.2's pair.
- **P2. Kind.** Generate User Event puts the literal `'User Event'` as the kind, so a VI with no Event Structure needs no enum. The `<Vi>Event` enum of a VI with an Event Structure gains `USER_EVENT = 'User Event'`; the kind has an explicit name, and 1000 is not added to the confirmed event types (they name frame labels).
- **P3. The structure's queue is the VI's `events`.** A structure with a cluster of registrations (D27) or a registration from another VI reads `events` and re-points each registration's lists to it, guarded (`if reg[0] is not events`), moving waiting messages first. No new function-entry seam. A structure wired to one registration made in this VI reads `reg[0]`, which is `events` when the VI has static panel events.
- **P4.** (a) Unregister removes tolerantly (`if q in registered`), since Destroy clears the list. (b) Event data is copied per queue only for by-value objects (`value_copy`). (c) Priority: NI's Generate User Event page gives "0 high priority, 1 normal priority (default)"; unwired or a constant 1 generates, anything else is a gap.
