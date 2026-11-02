# Error-wire inventory: LabVIEW error handling → generated Python (read-only)

Code refs are to the PR #234 worktree `<PR 234 checkout>` (head 4b6d64f6) unless marked **main**. Main means origin/main 0ec53ddc, checked out read-only at `<main checkout>`, a detached worktree I created for this; it can be removed. PR #234 changes only `ast_utils.py`, `builder.py`, `error_handler.py`, `error_path.py`, `error_wires.py` and `nodes/case.py` (`git diff --stat origin/main...HEAD`). Everything else maps the same way on both.

Scripts and data: `.tmp/err_corpus.py` (XML-only counter), `err_summary.py`, `err_cross.py`, `err_rows.json`, `confirm_parse.py`, `catalog_err.py`, `runs_despite.py`.

## Bottom line

- **One mismatch causes most of the defects.** LabVIEW skips only the nodes that are *wired downstream* of a failed node (they receive the error). A Python exception skips *every later statement*, wired or not. All five reported defects come from lvkit trying to close that gap after the fact:
  - try spans (defect 3);
  - held errors plus re-raise (defect 4);
  - default values for skipped outputs (defect 1);
  - a caught-error variable that every Unbundle reads (defects 2 and 5).
- **The current model has more wrong cases than the five reported.** Four have concrete examples:
  - a While Loop whose stop terminal is wired to an error cluster runs exactly once (29 corpus VIs);
  - Clear Errors and error case structures catch only `LabVIEWError`, so native Python failures such as `OSError` from `open()` get past them;
  - nodes NI documents as running despite an incoming error (Close Reference, Release Queue, TCP Close and others; 38 catalog entries) never run after a failure;
  - warnings (status False, code ≠ 0) are dropped everywhere.
- **Corpus:** 5,261 unique sample VIs; 3,374 have an error cluster. 1,790 use at least one error-handling construct (error case, Clear Errors, Merge Errors, an error handler, or an Unbundle of an error). 457 have an error shift register. 486 have an error wire that forks, and 367 of those have no Merge Errors, so the held error model is OFF for them.
- **Error as a value (model ii) is a direct translation for most constructs.** The judgement stays in two places:
  - mapping native Python exceptions to error codes at each node;
  - deciding whether a callee skips on an incoming error. The parsed graph shows this for 1,273 VIs, whose error case is fed straight from a front-panel terminal. For a vi.lib VI it is only in the catalog's description text; there is no structured field.

---

## A + B. Constructs, how lvkit recognises them, and today's mapping

Verdicts: **faithful**, **lossy** (what is lost), **wrong** (with a failing example).

**1. Error case structure (No Error / Error frames)**
- *Recognised by:* a case selector whose type is a Cluster gives `SelectorKind.ERROR` (`parser/nodes/case.py:631`). The No Error frame is the single-value open-ended point in `SelectRangeArray32` (`parser/nodes/case.py:164-188`, `:402-407`). Codegen: `is_error_selector` (`codegen/error_path.py:67-76`), `classify_error_node` → `ErrorRole.CASE` (`:88-89`).
- *Main:* `try: <every statement from the first to the last error-path node>` / `except LabVIEWError as e: <one error frame>; raise` / `else: <No Error frame>`. The `raise` is unconditional (main `error_wires.py` `_error_case`: `handler = _handler(error_var, [*parts.on_error, ast.Raise()])`).
- *PR #234:*
  - every error frame is generated (`nodes/case.py:584-615`);
  - a frame raises again only when the incoming error reaches a wired error out tunnel (`_frame_keeps_error`, `case.py:519-556`);
  - outputs that a producer assigns and an error frame reads are declared before the `try` (`error_wires.py:198-235`, `:265-272`).
- *Verdict:* **wrong on main.** A handled error is always raised again.
- *Verdict:* **wrong on the PR.**
  - **Defect 3:** the `try` covers every statement in `tagged[first:last+1]` (`error_wires.py:251-263`), including nodes that are not on the error path. When an early node fails, the later independent nodes are skipped. LabVIEW runs them.
  - Only path nodes' outputs that error frames read are declared (`:207-230`). Any other variable bound inside the `try` is unbound in the `else` and after it.

**2. Error case with error-code frames (`-1012`, `43`, a range, Default)**
- *Recognised by:* a frame's `SelectorRange`s other than the No Error point (`case.py:_error_code_ranges` `:497-502`, `_error_frame_condition` `:505-516`).
- *Main:* only the LAST non-No-Error frame is kept (main `case.py`: `for frame in node.frames: ... else: error_frame = frame`). The other code frames are dropped silently.
- *PR:* an `if e.code == …` chain, then the Default frame, else `raise` (`error_wires.py:173-195`).
- *Verdict:* **wrong on main**; **faithful in shape on the PR**. It still suffers from items 1 and 9.

**3. Error case fed by the VI's own `error in` (the standard "skip on error" wrapper)**
- *Recognised by:* same as item 1. The error path has no producer in the diagram (`error_wires.py:16-17`).
- *Main and PR:* only the No Error frame is generated (`case.py:623-636`, `_generate_error_case`; ARCHITECTURE.md:409 "Errors are never Python parameters").
- *Verdict:* **faithful** when the Error frame only passes the error through. **Wrong** when the Error frame does work: that work is deleted.
  - Corpus: 16 VIs, e.g. `OpenG/.../dictionary.llb/Dictionary Data Core__ogtk.vi` and `JKI-VI-Tester/source/JKI Reuse/Filter Error Codes (Array)__jki_lib_error_handling -- VI Tester.vi`.

**4. Unbundle (By Name) of an error cluster**
- *Recognised by:* an `nMux`/`demux` whose aggregate type is an error cluster (`_is_error_cluster`, `models.py`: exactly Boolean, I32, String). Codegen: `nmux.py:135-144`.
- *Main and PR:* outside a handler each field is a constant (status `False`, code `0`, source `""`). Inside an error frame each field is `<caught>.code` etc., with status `True` (`nmux.py:298-302`).
- *Verdict:* **wrong (defect 2b).**
  - Inside an error frame, *any* Unbundle of *any* error wire reads the caught error. For example, an Unbundle of a subVI's error out inside the Error frame reports the caught error's code, not the subVI's. `nmux.py:141` never checks which wire it unbundles.
  - Outside a handler, status is the constant `False`. Every downstream `if status:` is dead code.

**5. Unbundle `status` → Case selector (a Boolean case on status)**
- *Recognised by:* item 4, then a Boolean case.
- *Main and PR:* the selector is `False`, so only the False frame does anything. The error escapes as an exception instead of running the True frame.
- *Verdict:* **wrong** when the True frame handles the error (logs it, clears it, retries).
  - Corpus: 33 VIs, e.g. `ni-labview-icon-editor/.../Graphics/Pull data from disc.vi` and `OpenG/.../dictionary.llb/Dictionary Create__ogtk.vi`.

**6. Bundle (By Name) into an error cluster**
- *Recognised by:* `nmux.py:158-165` → `_generate_error_bundle` (`:416-458`).
- *Main and PR:*
  - status wired → `raise LabVIEWError(code=…, source=…)`, under an `if` unless status is the constant `True`;
  - status not wired, outside a handler → **nothing** (`:437-439`);
  - inside a handler → the caught error's fields are set (`:440-445`).
- *Verdict:* **lossy.** A warning (status False, code set) disappears. The bundled cluster's own value (for an indicator or a log) never exists.

**7. Merge Errors** (primitive 2147 class `mergeErrors`, or vi.lib `Merge Errors.vi`)
- *Recognised by:* `error_path.py:81-86`, by prim id or by VI **name** `op.name == "Merge Errors.vi"`. The parser registers the class at `parser/node_types.py:1969`.
- *Main and PR:*
  - the node generates nothing (`nodes/primitive.py:115-116`, `nodes/error_vi.py:39-46`);
  - the held error model turns on for the whole VI (`error_handler.py:61-77`) and runs every multi-node level of the diagram in a `ThreadPoolExecutor` (`builder.py:245-253`);
  - each branch's error is held, and `if _held_error: raise _held_error` runs **only at the end of the VI** (`builder.py:176-178`).
- *Verdict:* **wrong.**
  - Nodes downstream of the merge run as if there were no error. LabVIEW would skip them.
  - The error raised is the first in await order (`builder.py:443` sorts by `raises`), not Merge Errors' input order.
  - **Defect 4 (PR):** a branch whose error both reaches a handler and is also traced to the merge is held AND re-raised. The handler handles it, and the VI end raises it again (`builder.py:398-403`; `merged_producer_ids` `error_wires.py:88-101` stops only at handlers, not at the merge's own consumers).
  - Example: `outputs/inv_prep/prepare_lv_to_use_icon_editor_source/prepare_lv_to_use_icon_editor_source.py`. Each branch does `_held_error = _held_error or e; …; raise`. The error frame `if e.code == 43: pass` "handles" it, then the VI ends with `if _held_error: raise _held_error`.
  - Main (`<main checkout>/outputs/errinv_prep_main/...`) only holds, so the error case never sees the branches' errors (`except LabVIEWError as e: raise`).

**8. Parallel branches on a forked error wire with NO Merge Errors**
- *Recognised by:* not recognised. The held model needs a Merge Errors (`error_handler.py:71-77`).
- *Main and PR:* the branches are generated sequentially (`builder.py:254-262`).
- *Verdict:* **wrong.** The first branch that raises aborts the later ones. LabVIEW runs them all.
  - Corpus: 367 VIs, e.g. `JKI-VI-Tester/source/Utilities/GetControlRef.vi`.

**9. Clear Errors (vi.lib `Clear Errors.vi`)**
- *Recognised by:* by name, `error_path.py:86-87`.
- *Main and PR:* each run of consecutive producer statements is wrapped in `try: … except LabVIEWError: pass` (`error_wires.py:120-161`). With a specific code wired it is a gap (`:130-136`).
- *Verdict:* **wrong** in three ways:
  - It catches only `LabVIEWError` (`error_wires.py:104-109`). A failure of a native template, such as `open(in_11, 'r+b')` (`data/primitives/file-i-o.json:253`), raises `OSError` and passes the Clear Errors.
  - The outputs of a swallowed producer are unbound after the `try`.
  - Independent statements in between are not wrapped, so they still run, but only when the producers are not adjacent.

**10. Simple Error Handler / General Error Handler**
- *Recognised by:* the catalog: SEH at `data/vilib/dialog-user-interface.json:269-358`. GEH (`:360-512`) has no `python_code`.
- *Main and PR:* SEH is inlined from the template `if error_in and error_in.get("status"): raise RuntimeError(...)` (`:353`).
- *Verdict:* **wrong (defect 2a).**
  - `error_in` is never bound (the terminal's parameter name is `error_in_no_error`, `:318`). The error is treated as a dict. It raises `RuntimeError`, which no `except LabVIEWError` catches.
  - In the PR example, `elif error_in and error_in.get('status'):` sits inside an error frame, so it fails with `NameError`.
  - GEH: no template.
  - Corpus: SEH in 60 VIs, GEH in 9.

**11. Error Cluster From Error Code**
- *Recognised by:* by name (`error_path.py:39-45`, `nodes/error_vi.py`).
- *Main and PR:* `raise LabVIEWError(code, message)`, unless the code is the constant `0` or `is warning?` is `True` (`error_vi.py:66-101`).
- *Verdict:* **mostly faithful; lossy for warnings.** `is warning? = True` generates nothing (`:76-78`).
  - Corpus: 302 VIs.

**12. Error Ring (XNode)**
- *Recognised by:* catalog entry only (`dialog-user-interface.json:560`). No codegen found (`grep "Error Ring" src/lvkit` hits only the catalog and `parser/vi.py:808`).
- *Verdict:* **unsupported.** I did not run it; it is likely a gap.
  - Corpus: 39 VIs.

**13. SubVI / method call with error in/out**
- *Recognised by:* `is_error_cluster` terminals are skipped (`nodes/subvi.py:227`, `:276`, `:486`, `:639`). The callee signature skips them too (`builder.py:916-950`).
- *Main and PR:* a plain call. An upstream exception means the call is never reached.
- *Verdict:* **faithful for callees that skip on an incoming error. Wrong for callees that do not:**
  - cleanup VIs;
  - the 38 catalog entries whose NI-derived description says "runs normally even if an error occurred" (Close Reference, Release Queue, TCP Close Connection, VISA Close, Close File, Destroy User Event, … from `.tmp/runs_despite.py`).
  - These cleanup nodes never run after a failure, so resources leak.
  - Corpus: 9,755 calls with error terminals in 2,603 VIs.

**14. SubVI with error in unwired / all error terminals unwired**
- *Recognised by:* `error_path.py:144-149`. An unwired error in ends the trace (the retry case, ARCHITECTURE.md:409).
- *Main and PR:* same as item 13.
- *Verdict:* **lossy.** In LabVIEW an unwired error in means "run regardless of the upstream error". In Python it is still skipped if anything above it in the code raised.
  - Corpus: 1,054 calls with *all* error terminals unwired, in 346 VIs.

**15. Property / Invoke node error terminals**
- *Recognised by:* `property_node.py`, `invoke_node.py:49` skip them.
- *Main and PR:* same as item 13.
- *Verdict:* same as item 13. A Close Reference after a failed property read does not run.
  - Corpus: 1,621 wired uses in 508 VIs.

**16. Error through a loop tunnel or shift register**
- *Recognised by:* `CodeGenContext.tunnels` drops error tunnels and shift registers (`context.py:511-515`).
- *Main and PR:* the first exception exits the loop.
- *Verdict:* **lossy.** In LabVIEW, with an error shift register, later iterations' error-chain nodes skip but the loop keeps going until its stop condition. Non-error nodes still run, auto-indexed outputs are produced, and shift-register values are returned along with the error. In Python all of that is lost.
  - Corpus: error shift registers in 457 VIs, error loop tunnels in 188 VIs.

**17. Loop stopped on error**
- *Case: error wired straight to the stop terminal.*
  - *Recognised by:* `loopTestDCO class="lTst"` typed as an error cluster; 29 VIs.
  - *Mapping:* `ctx.wired_input` returns `None` for an error-cluster wire (`context.py:283-288`). `_build_while_loop` then falls back to `body + [break]` (`nodes/loop.py:866-891`).
  - *Verdict:* **wrong.** A While Loop that should run until an error **runs once**.
  - Example, generated with `--load-mode none`: `outputs/errinv_fwtest_pr/framework_test/framework_test.py:11-16`, from `JKI-VI-Tester/source/Tests/Framework Test.vi`. A Dequeue loop is generated as `while True: element = queue.get() … break`.
- *Case: a For Loop's conditional terminal wired to an error.*
  - *Mapping:* the conditional terminal is dropped, so the loop runs over every element. The exception still stops it on a real error.
  - *Verdict:* **lossy** (the outputs at the stop are lost).
- *Case: an unbundled status wired to the stop terminal.*
  - *Mapping:* status is the constant `False`.
  - *Verdict:* **wrong** in the same way.

**18. Error constant**
- *Recognised by:* an error-typed `bDConstDCO` (130 VIs).
- *Mapping:* `wired_input` returns `None` (`context.py:283-288`), so the constant is ignored.
- *Verdict:* **faithful for "no error". Wrong for a constant whose status is TRUE.** I did not check constant values.

**19. Nested handlers**
- *Recognised by:* `build_merge_try_except` hard-codes `name="e"` (`error_handler.py:159-162`), and `_held_error = _held_error or e` (`:136-145`).
- *Verdict:* **wrong (defect 5).**
  - It shadows the error frame's caught `e`. The PR example shows `except LabVIEWError as e:` nested inside `except LabVIEWError as e:`.
  - Python deletes an `except … as e` name when that handler finishes. When an inner held handler completes without raising, a later `e.code` in the outer frame fails with `UnboundLocalError`. This follows from the language rule; I did not run it.

**20. Defaults for skipped outputs**
- *Recognised by:* `default_value_expr` (`ast_utils.py:23-53`).
- *Mapping:* clusters, paths and typedefs become `None`; arrays become `None` unless `fresh_array` (PR). Case-structure pre-declarations use it without `fresh_array` (`case.py:682`). There is a duplicate rule in `compound.py:782-798` (`_reshape_default_element`).
- *Verdict:* **lossy/wrong (defect 1).** A downstream `.field` or `len()` on a skipped cluster or array fails with `AttributeError`/`TypeError`.

**21. Compound Arithmetic / Boolean node with an error-cluster input**
- *Recognised by:* `cpdArithDCO` typed as an error cluster (38 VIs).
- *Mapping:* `wired_input` returns `None`.
- *Verdict:* **ambiguous.** I did not check what the node generates with a `None` operand.

**22. Error kept in a local variable / indicator**
- *Recognised by:* `gRefDCO` typed as an error cluster (12 VIs).
- *Mapping:* there is no value to read or write.
- *Verdict:* **ambiguous / lossy.** Not checked further.

**23. Event Structure error tunnels**
- *Recognised by:* `xTunnel` (45 VIs).
- *Verdict:* the Event Structure itself is a gap in generation, so this is moot.

**24. Warnings (status False, code ≠ 0)**
- *Mapping:* there is no representation at all. See items 6 and 11.
- *Verdict:* **lossy everywhere.**

Side observations:
- ARCHITECTURE.md:409 cites `error_wires.py:130` and `:121`. In the PR those lines are now `:173-195` and `:164-170`. The citations are stale.
- Off-topic: `outputs/errinv_ppls_pr/test_missing_ppls/test_missing_ppls.py` never appends to `missing_items_from_labview_projects` (a For Loop's output tunnel). I did not look into it.

## C. Corpus frequency

**Method.** XML only: `vi.xml` type table (DTHP heap→consolidated, VCTP TopLevel→flat, error = a Cluster of Boolean, NumInt32, String) plus `vi_BDHb.xml`. I read 5,261 unique VIs (deduplicated by sha256) whose recorded source is under `.lvkit/cache/samples/`, from `~/.lvkit/cache/projects/lvkit-c3a6b136/extract`. I cross-checked three VIs against `parse_vi` (`confirm_parse.py`); the error-case and frame counts matched.

| Construct | VIs | Uses | Examples |
|---|---:|---:|---|
| Any error cluster | 3374 | | |
| Error case structure | 1476 | 1649 | `DCAF-DAQModule/source/module/configuration/from string.vi`; `OpenG/.../picture.llb/Write GIF File__ogtk.vi` |
| …selector fed straight from a front-panel terminal (the "skip on error" wrapper) | 1273 | | `actor-framework/Providers/MessageMakerProvider/.../Add to Project.vi` |
| …error case not fed straight from the panel | 270 | | |
| …an Error frame that contains nodes | 111 | 124 | `JKI-VI-Tester/source/Classes/TestCase/run.vi`; `picosdk-ni-labview-examples/plcm3/PicoLogCM3Example.vi` |
| …fed from the panel AND the Error frame has nodes (work deleted today, item 3) | 16 | | `OpenG/.../dictionary.llb/Dictionary Get Data to Modify__ogtk.vi` |
| …error-code frames | 3 | 3 | `actor-framework/Tooling/Prepare LV to Use Icon Editor Source.vi`; `measurement-plugin-labview/Source/Runtime/Clients/Discovery V1/Enumerate Compute Nodes.vi` |
| Merge Errors primitive | 336 | 412 | `actor-framework/Core/ActorFramework/Message Queue/Release Message Queue.vi`; `LabVIEW-OOP-Classes/DAQ/SSR/SSR_class/Stop SSR.vi` |
| Merge Errors.vi | 10 | | `OpenG/.../file.llb/Delete Recursive__ogtk.vi` |
| Error wire that forks | 486 | 754 | `JKI-VI-Tester/source/Classes/TestCase/run.vi` |
| …with no Merge Errors (held model off) | 367 | | `JKI-VI-Tester/source/Utilities/Get VI Error Out Value.vi` |
| Clear Errors.vi | 77 | | `ni-labview-icon-editor/Test/Unit Tests/INI Settings/Test INI Settings.vi` |
| Simple Error Handler | 60 | | `measurement-plugin-labview/Source/Generator/Create Measurement Client.vi` |
| General Error Handler | 9 | | `measurement-plugin-labview/.../Complete Call on Error.vi` |
| Error Cluster From Error Code | 302 | | `LabVIEW-OOP-Classes/.../SmartMotor_GetAttributes.vi` |
| Error Ring | 39 | | `actor-framework/Providers/AddActor/Classes/Actor Store/Retrieve Root Actor.vi` |
| Unbundle of an error | 204 | 260 | `measurement-plugin-labview/.../Assert Error Code.vi` |
| …unbundled field → case selector | 33 | 35 | `LabVIEW-OOP-Classes/.../SmartMotor_class/Read Troque.vi` |
| Bundle into an error | 30 | 34 | `OpenG/.../error.llb/Build Error Cluster__ogtk.vi` |
| Loop stop wired to an error or unbundled status | 32 | 34 | `JKI-VI-Tester/source/Tests/Framework Test.vi`; `picosdk-ni-labview-examples/ps6000/PicoScope6000ExampleBlockMultiUnit.vi` |
| …error wired straight to the stop terminal | 29 | 31 | |
| Error shift register | 457 | 1298 | `DCAF-DAQModule/source/module/configuration/from string.vi` |
| Error loop tunnel | 188 | 1080 | `JKI-VI-Tester/source/Classes/TestCase/WaitOnTestComplete.vi` |
| Error case tunnel | 1952 | 16992 | |
| Error sequence tunnel / flat sequence tunnel | 168 / 50 | | |
| In Place Element error tunnel | 107 | | |
| SubVI calls with error terminals | 2603 | 9755 | |
| …all error terminals unwired | 346 | 1054 | `LabVIEW-OOP-Classes/DAQ/Digital Input/Example - Simple.vi` |
| Property/Invoke node with an error terminal wired | 508 | 1621 | `actor-framework/.../Set Send Icon.vi` |
| Primitive with error terminals | 907 | 5575 terminals | |
| Format/Scan with error | 267 / 45 | | |
| Error constant | 130 | 164 | `JKI-VI-Tester/source/Classes/TestResult/CleanUp.vi` |
| Compound Arithmetic with an error input | 38 | | `measurement-plugin-labview/.../test_EnumerateComputeNodes_...vi` |
| Error local variable | 12 | | |
| Any handling construct (case/clear/merge/handler/unbundle) | 1790 | | |

**Limits of the counts.**
- Bundle vs Unbundle By Name is told apart by terminal count: fields + 1 means Unbundle.
- "Error frame has nodes" counts every node in a non-No-Error frame except the diagram's terminal holder (`sRN`).
- A LabVIEW 8/9 error case is still found, because detection is by the selector's type.
- "Unwired" means no wire touches any of the call's error terminals; I did not split error in from error out.

Catalog counts (`.tmp/catalog_err.py`):
- 359 of 680 primitive entries and 746 of 2,151 vi.lib entries have error terminals.
- Of those, 37 primitive and 2 vi.lib entries also have a `python_code` template.
- 18 templates bind `error_out` as a pass-through of `error_in` (`"error_out": "in_8"` ×11, `"in_4"` ×6, `passthrough_from` ×1).
- 38 entries are documented as running despite an incoming error.

## D. Exceptions (i) vs error as a value (ii), construct by construct

Model (ii) as the brief describes it:
- `def vi(..., error_in=None) -> Result(..., error_out)`;
- an error is a `LabVIEWError` instance or `None`;
- each node on an error chain runs under `if err is None:`, and a native failure becomes `err = LabVIEWError(...)`.

| Construct | (i) exceptions, with fixes | (ii) error as a value | What (ii) fixes | What (ii) costs, or the judgement left |
|---|---|---|---|---|
| Error case (1, 2) | try/except over a statement span. Needs the span, pre-declares and re-raise rules (defects 1, 3) | `if err is not None: <Error frames, if err.code == …>` / `else: <No Error>`. The selector is the wire's variable | Span, pre-declare and re-raise logic disappear. Code frames are a plain `if` | None; a direct translation |
| Panel-fed error case (3) | Only No Error generated; Error frame work deleted | `if error_in is not None: <Error frame>; return …` | The 16 VIs with work in the Error frame | Every VI gains an `error_in` parameter and an `error_out` result field. An AI clean-up can drop these where nothing handles errors |
| Unbundle / Bundle (4–6, 24) | Constants or the caught variable; wrong wire inside frames | `err.code`, `err is not None`, `LabVIEWError(code=…)` on the actual wire | Defect 2b, the status → case dead code, warnings | A warning needs a representation: `LabVIEWError` with `status=False`, so "no error" is `None` and a warning is `LabVIEWError(status=False)` |
| Merge Errors (7, 8) | Held model: threads, `_held_error`, raise at VI end, order by await | `err = next((e for e in (a, b, c) if e is not None and e.status), …)`, in input order | Defect 4; downstream nodes after the merge; 367 forked VIs without a merge. The `ThreadPoolExecutor` emulation goes away (`builder.py:290-489`, about 200 lines) | First-warning-if-no-error is a small rule |
| Clear Errors (9) | `try/except LabVIEWError: pass` | `err = None` (with a specific code: `if err and err.code == N: err = None`) | `OSError` leak, unbound outputs | None |
| Simple / General Error Handler (10) | Broken template | `error_out = err`, plus a print/log for the dialog | Defect 2a | The dialog has no headless meaning; same as today |
| Error Cluster From Error Code (11) | `raise` | `err = LabVIEWError(code, …) if code else None` | Warnings | None |
| SubVI call (13–15) | Plain call; skipped by any earlier raise | `out, err = sub(..., error_in=err)` | Independent nodes run; cleanup VIs run | **Judgement:** does the callee skip on an incoming error? See the note after this table |
| Native-op nodes (file, queue, TCP …) | Native exceptions escape the `LabVIEWError` handlers | `if err is None: try: … except OSError as ex: err = LabVIEWError(code=…)` | Clear Errors and error cases see native failures | **Judgement:** a mapping from exception to LabVIEW error code (`codegen/error_codes.py` exists, 37 lines). This is the bulk of the readability cost: one guard per error *chain*, not per node, if consecutive nodes on one wire share one `if err is None:` block |
| Loops (16, 17) | Tunnels dropped; the stop terminal is lost (runs once) | Error shift register = a loop variable; stop terminal = `if err is not None: break` | 29 + 457 VIs | None; a direct translation |
| Parallel branches (8) | Threads to emulate "every branch runs" | Plain sequential code; each branch carries its own `err` | All held-model complexity | None |
| Unwired error in (14) | Skipped anyway | Runs; its error is local | Retry / always-run nodes | None |
| Top-level VI | Raises | Returns `error_out` | | A top-level wrapper may `raise` for idiomatic use; that is a policy choice |
| Readability / "AI cleans up later" | Reads like Python, but behaves wrongly in the cases above | Explicit `err` plumbing, which is noisy | Correct before clean-up | The clean-up (turning plumbing into try/except) is easy for an AI because the meaning is explicit. The reverse direction (recovering the dataflow lost to exceptions) is not possible from the output |

**Does the parsed VI tell us whether a subVI skips on an incoming error?**
- **A project VI:** yes, from its graph.
  - If `error in` feeds an error case's selector and every other node sits in its No Error frame, it skips (1,273 VIs have the panel-fed wrapper).
  - Otherwise it skips if every node it calls on the error chain skips. Per the catalog, the standard nodes skip ("standard error in functionality"), except the 38 that "run normally even if an error occurred".
  - Nodes with no error terminals (Add, Build Array) still run, but they are pure.
  - This is a recursive, deterministic analysis. `error_reaches` / `trace_error_path` (`error_path.py:109-164`) is a starting point.
- **A vi.lib VI without its diagram:** only the description text in `data/vilib/*.json`. There is no structured field (a grep for `runs_on_error|skip_on_error|error_behavior` found nothing).
  - A data-driven solution needs a catalog field. The 38 entries found by text would seed it.
- Under (ii) the safe default needs no analysis: call the subVI with `error_in=err` and let the callee's own generated `if error_in is not None` decide. Analysis is only needed to *omit* the plumbing for readability.

## E. What (ii) would change (measured with `wc -l`, PR worktree)

**Rewritten or deleted (error-specific):**

| File | Lines | Change |
|---|---:|---|
| `codegen/error_wires.py` | 274 | Replaced by a small per-node error-variable rule |
| `codegen/error_handler.py` | 264 | Held model; mostly deleted |
| `codegen/error_path.py` | 164 | Trace kept for the "skip" analysis and readability |
| `codegen/nodes/error_vi.py` | 102 | |
| `codegen/errors_module.py` | 40 | Kept; class gains `status` |
| `codegen/builder.py` | 1004 | Held-error init/check `:142-178`; error-handler dispatch `:225-283`; `_generate_parallel_tier` + `_Branch` `:290-501` (exists only for the held model, `:239-245`); signature and result skips `:643-950` |
| `codegen/nodes/case.py` | 710 | Error-case parts `:450-636` |
| `codegen/nodes/nmux.py` | 515 | Error read/bundle `:135-165`, `:265-320`, `:416-458` |
| `codegen/nodes/primitive.py` | 1025 | Merge skip `:111-116`; template error terminals |
| `codegen/nodes/loop.py` | 1059 | Stop terminal; error tunnels and shift registers |
| `codegen/context.py` | 1219 | `error_var`, `use_held_error_model` `:95-102`; `wired_input` error-cluster `None` `:283-288` (one choke point); `tunnels` `:511-515` |

**Touched (the `is_error_cluster` skips):**
- 44 sites in 19 codegen files (`grep -rn is_error_cluster src/lvkit/codegen | wc -l`).
- Among them: `subvi.py` (729 lines; `:227`, `:276`, `:486`, `:639`), `method_call.py` (192), `property_node.py` (195), `invoke_node.py` (93), `printf.py` (85), `queue_ops.py` (523), `ops/variant_to_data.py` (233), `in_place.py` (470), `vi_reference.py` (609), `gaps.py` (349; outputs bound to `None`), `class_builder/methods.py` (232), `class_model.py` (574), `import_resolver.py` (190).
- Also `pipeline.py:799` (`is_error_wire_vi`).

**Catalog:**
- `data/vilib/dialog-user-interface.json` SEH template `:353`.
- 18 templates with `error_out` pass-through.
- The 37 + 2 templates on entries with error terminals need the guard.
- 38 run-despite-error entries would need a structured field if the analysis is wanted.

**Tests that pin error output** (lines; reference count):

| File | Lines | References |
|---|---:|---:|
| `tests/test_error_codegen.py` | 1169 | 43 |
| `test_error_case_frames.py` | 442 | 9 |
| `test_held_error_reaches_handler.py` | 310 | 3 |
| `test_held_branch_defaults.py` | 193 | 2 |
| `test_error_handling.py` | 121 | 26 |
| `test_parallel_codegen.py` | 388 | 6 |
| `test_clfn_corpus_run.py` | 373 | 5 |
| `test_variant_to_data.py` | 85 | 3 |

Single references in `test_new_codegen.py`, `test_corpus_functional.py`, `test_global_variable.py`, `test_lvnet_catalog_default.py`, `test_vilib_catalog_by_recorded_path.py`, `native_vi.py`, `conftest.py`.

**Recorded design that (ii) replaces.** It needs the maintainer's explicit yes by name:
- CLAUDE.md:66 ("errors are ordinary Python exceptions");
- CLAUDE.md:157-198 (Error Handling Strategy, Held Error Model);
- ARCHITECTURE.md:203 ("Errors are exceptions");
- ARCHITECTURE.md:398-417 (try/except at Clear/Merge/error-case; "Errors are never Python parameters").
- Not affected: ARCHITECTURE's rule that only codegen transforms errors and every other view shows them faithfully. Under (ii) codegen becomes *closer* to those faithful views.
