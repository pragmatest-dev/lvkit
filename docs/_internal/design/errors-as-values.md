# Errors as values in generated Python — plan

Status: **PLAN APPROVED 2026-11-01** (with the recommendations under
"Decisions" and the replacements in the table below). Branch
`feat/errors-as-values` (from `main` 0ec53ddc).

**Implementation status, 2026-11-04** (head 8299a6b5):

- #237's 13 later commits (polymorphic variant choice, one import per name,
  node-owned names) are on the branch; the indicator a VI keeps between calls
  takes a node-owned name too.
- Error Ring (decision 6): generated when its configuration decodes, a gap
  otherwise; identified by its library's recorded path (`XNXI`).
- Catalog failures: Search and Replace String (a pattern `re` cannot compile:
  42), Delete (implemented: 1430, 1, 7, 8, 6), To More Specific Class on a class
  wire (1448).
- Step-7 before/after on the 39 VIs, since the last run (2026-11-02 12:41):
  lvnet terse and `-v` unchanged except Delete Recursive__ogtk, whose two
  Delete Boolean inputs swap places (input 9 is now `confirm`, 10
  `entire_hierarchy`); describe gains `clear_indicators_when_called` in one VI
  and JSON in all 39 (decision 15). Generated code: 9,911 changed lines across
  40 commits, each pattern group traced to its commit; Error Rings remove 12
  `xNode` gaps; class Property Nodes on classes that are not loaded add gaps
  (decision 9). 34 VIs partial (exit 3), 5 complete.
- Suites at 8299a6b5: Linux `pytest -q` 5394 passed, 23 skipped, 1 xfailed;
  `-m slow -n 2` 30 passed, 1 xfailed. Windows (local `C:\` clone, `uv.exe`)
  5362 passed, 55 skipped, 1 xfailed; slow 30 passed, 1 xfailed.

## Why

LabVIEW skips only the nodes wired downstream of a failed node; they receive the
error on their error in. A Python exception skips every later statement, wired
or not. The exception mapping (`codegen/error_wires.py`, `codegen/error_handler.py`,
the held error model in `codegen/builder.py`) patches that gap per construct, and
the patches interact. PR #234's tester round found five defects; an inventory of
every error construct found more (file:line receipts are against PR #234's head
4b6d64f6; key findings below):

- A While Loop whose stop terminal is wired to an error runs once
  (`context.py:283-288` returns no value for an error wire; `nodes/loop.py:866-891`
  falls back to `break`). 29 corpus VIs.
- Nodes NI documents as running despite an incoming error (Close Reference,
  Release Queue, TCP Close, VISA Close, …; 38 catalog entries) never run after a
  failure.
- A forked error wire with no Merge Errors runs its branches in sequence, so the
  first failure stops the others. 367 VIs.
- Error cases and Clear Errors catch only `LabVIEWError`; a native failure
  (`OSError` from a file template) passes them.
- Merge Errors raises only at the VI's end; nodes after it run as if no error.
- Outside a handler an Unbundle of an error reads status as the constant
  `False`, so a status → Case True frame never runs. 33 VIs.
- A panel-fed error case drops its Error frame's work. 16 VIs.
- Warnings (status False, code set) are dropped.
- Merge Errors and Clear Errors are recognised by VI name (`error_path.py:84-87`).

Corpus (5,261 VIs): 3,374 carry an error cluster; 1,476 have an error case
(1,273 the standard "skip on error" wrapper); 457 an error shift register;
2,603 call a subVI with error terminals.

Error as a value also suits the other uses of generated code: a NiceGUI panel
(`scripts/panelgen/panel_gen.py`) binds controls and indicators by name, so
`error_in` / `error_out` bind with no special code, and a Run ends with every
indicator updated, as in LabVIEW. A pytest test can assert `error_out.code`
alongside the other outputs.

## Recorded design this replaces (needs the maintainer's yes, by name)

| Where | Decision | Status under this plan |
|---|---|---|
| `CLAUDE.md:66` | "errors are ordinary Python exceptions" | **replaced**: errors are values; gaps and lvkit failures stay exceptions |
| `CLAUDE.md` "Error Handling Strategy" / "Held Error Model" | held error, try/except per branch | **replaced** |
| `ARCHITECTURE.md:203` | "Errors are exceptions" (tunnels drop error clusters; Unbundle reads constants or the caught error) | **replaced** |
| `ARCHITECTURE.md:398-417` | try/except at Clear/Merge/error case; "Errors are never Python parameters" | **replaced** |
| `ARCHITECTURE.md` | only codegen transforms errors; every other view shows them faithfully | **kept** (codegen becomes closer to the other views) |
| `CLAUDE.md` gap rule | warn at generation, raise `NotImplementedError` at run time | **kept** |
| `CLAUDE.md` | generated code imports nothing from lvkit; `LabVIEWError` lives in the package's `errors.py` | **kept** |
| `CLAUDE.md` | a runtime helper needs the maintainer's approval | **kept** (see decision 3) |

## The model

An error wire is an ordinary wire carrying a value: `None` (no error) or a
`LabVIEWError`. `LabVIEWError` (`codegen/errors_module.py`) gains `status: bool`;
a warning is `LabVIEWError(status=False, code=…)`.

| Construct | Generated Python |
|---|---|
| VI with error in / error out on its connector pane | `def vi(..., error_in: LabVIEWError \| None = None)`; result gains `error_out` |
| SubVI / method call | `r = sub(..., error_in=err)`; `err = r.error_out`. Always called; the callee decides whether to skip, as in LabVIEW |
| Catalog node (primitive, vi.lib template, Property/Invoke Node) with error in | its statements inside `if err is None:` (consecutive nodes on one chain share one block); its outputs take their type's default when skipped. A node the catalog marks as running despite an error is not guarded |
| Native operation that can fail | `try: … except <Exception> as ex: err = LabVIEWError(code=…, source=…, message=str(ex))` (decision 2) |
| Error case | `if err is not None and err.status:` Error frames (`if err.code == …` chain, Default) / `else:` No Error frame; selector is the wire's own variable |
| Unbundle (By Name) of an error | `err is not None and err.status`, `err.code if err else 0`, … on the wire it unbundles |
| Bundle into an error | `LabVIEWError(status=…, code=…, source=…)`, or `None` when status False and code 0 |
| Merge Errors | first input with status True, else first warning, else `None`, in input order |
| Clear Errors | `err = None` (with a specific code wired: only that code) |
| Error Cluster From Error Code | `LabVIEWError(code, …, status=not is_warning)` or `None` for code 0 |
| Simple / General Error Handler | the error it reports: `err` when an error occurred on it; else, for a nonzero `error code`, `LabVIEWError(status=True, code=error_code, source=error_source)`; else `err` (`None`, or a warning). `error_out` is that error, outputs (`error?`, `code out`, `source out`, `message`) from it; an unwired `error code` is 0 and writes no code test; no dialog |
| Error shift register / loop tunnel | a loop variable like any other |
| Loop stop terminal wired to an error | `if err is not None and err.status: break` |
| Forked error wire / parallel branches | plain sequential code, each branch carrying its own variable; the thread pool tier (`builder.py:_generate_parallel_tier`) is removed |
| Unwired error in | the node runs regardless of earlier errors |
| Error constant | its value |
| Error local variable | a function variable (as `codegen/local_indicators.py` does for indicators) |

Outside the model: a gap still raises `NotImplementedError`; an lvkit failure is
still an exception; the VI function never raises its `error_out`.

## Placement

- `codegen/error_flow.py` (new): the guard rule (which nodes go in `if err is
  None:`, block sharing along a chain) and the Merge/Clear/case rules. Replaces
  `error_wires.py` and most of `error_handler.py`.
- `codegen/error_path.py`: keeps the chain trace; Merge Errors / Clear Errors
  recognised by recorded vi.lib path or primitive id, never by name.
- `codegen/context.py:wired_input`: the one choke point; an error wire resolves
  like any wire. The ~45 error-cluster skips in 19 codegen files then go.
- `codegen/ast_utils.py`: `type_default_expr` gives a type's real default (a
  typedef cluster as `SimpleNamespace(field=…)` from its fields, an array `[]`);
  `parameter_default_expr` keeps `None` for mutable parameter defaults. One rule
  replaces the copy in `nodes/compound.py:782-798`.
- `codegen/builder.py`: signature and result include error terminals; held
  error init/check and the parallel tier are deleted.
- Catalog (`data/primitives*.json`, `data/vilib/*.json`): a structured field
  for "runs even if an error comes in", seeded from the 38 entries whose NI
  description says so (each checked against NI's public page); the Simple Error
  Handler template; the 18 templates that pass `error_out` through.
- `codegen/errors_module.py`: `status` field.

## Steps and who does them

Sonnet wherever the step can be specified exactly; Opus only for the core rule.
Every commit's diff is reviewed by me (a stronger model than its author).

0. **Me:** this plan; the maintainer approves it and the replacements above.
1. **Sonnet — the spec as tests.** One tiny handmade diagram per construct in
   the table (as `tests/test_disable_graph.py` builds them), run through every
   view; for `generate`, the expected Python is executed and its outputs and
   `error_out` asserted. Includes the #234 tester VIs and the inventory's wrong
   cases as corpus checks. These fail until step 2-3 land.
2. **Opus — the core.** Error wire as a value through `wired_input`; signature
   and result; the guard rule and block sharing; real type defaults.
3. **Sonnet — one construct per commit** against step 1's tests: error case,
   Unbundle/Bundle, Merge, Clear, Error Cluster From Error Code, Simple/General
   Error Handler, loops, constants, local variables.
4. **Sonnet — catalog:** the "runs despite error" field and its 38 entries
   (Haiku fetches the NI pages), the templates.
5. **Sonnet — native failures:** the guard and the code mapping (decision 2).
6. **Sonnet — removal:** held model, parallel tier, error-cluster skips, stale
   tests; no mention of the removed design left in code or docstrings.
7. **Sonnet — corpus regression:** generate before/after for the tester VIs and
   the inventory examples, execute them, diff; re-pin slow-test counts with
   causes. Also report how many VIs only pass their error along (input for
   decision 7).
8. **Me — docs:** `CLAUDE.md`, `ARCHITECTURE.md`, `codegen/README.md`,
   CHANGELOG.
9. **Me — verification and PR:** Linux `pytest -q`, `pytest -q -m slow -n 2`,
   Windows; open the PR; then close #234 with a comment the maintainer approves,
   pointing at the new PR. 24-hour hold applies.

## Decisions for the maintainer

1. Real type defaults everywhere a type's default is used (shift-register
   seeds, queue timeout element, out-of-range Index Array, Disable Structure,
   globals), not only skipped outputs. **Recommend: everywhere.**
2. Native failure → error code: per catalog entry (the entry names the codes
   its operation can return, from NI's public error list), with one generic
   code when none matches. **Recommend: per entry plus a generic fallback.**
3. Native guard written inline (`try/except` at each node; no helper) or as a
   small context manager in the package's `errors.py`. **Recommend: inline for
   now; revisit if the output is too noisy.**
4. A typedef cluster's default: plain `SimpleNamespace` (as cluster constants
   are) or the typedef's own class. **Recommend: plain `SimpleNamespace`.**
5. Simple / General Error Handler dialog: nothing, or print to stderr.
   **Recommend: nothing** (the One Button Dialog precedent).
6. Error Ring (39 VIs, no codegen today): in this change or not. **Recommend:
   in, if its value decodes from the node's data; a gap otherwise.**
8. **Decided 2026-11-01:** a VI's results are a frozen `dataclass`, not a
   `NamedTuple`, so a result is read only by field name, never by position
   (positional unpacking depends on field order). Done in this change because it
   rewrites every result anyway (`error_out`). Today: ~25 `NamedTuple` mentions
   in 11 codegen files, 9 test files.
10. **Decided 2026-11-02:** `status` means "an error occurred" (NI). A guarded
    node skips only when one did: `if err is None or not err.status:`; a
    warning (status False, code set) does not skip and flows on. A node that
    runs despite an incoming error keeps the incoming error in its error out;
    its own failure surfaces only when nothing came in (first error wins).
    The Close File catalog entry is fixed to actually close the file.
11. **Decided 2026-11-02:** a Python name drops a label's parenthetical only when it
    states the terminal's default (`error in (no error)` → `error_in`, `enable (T)`
    → `enable` when its default is TRUE); any other parenthetical, such as a unit,
    stays (`timeout (ms)` → `timeout_ms`). Repeats are numbered
    (`codegen/label_names.py`).
9. **Decided 2026-11-02** (extends the 2026-11-01 note that accessors "need not be
   special method calls as long as they behave like LabVIEW"; maintainer: "use the
   accessor selectively… usually it's the exact shape you described"): an accessor
   stays a Python property (`obj.x`, `obj.x = v`) when every VI of its
   dynamic-dispatch family has one error behaviour that is either "runs whatever
   error comes in" or "skips all its work on an error"; a skipping property is
   guarded at the call (a read gives its default, a write does not happen, error
   out is the incoming error). Any other accessor (it can produce an error of its
   own, or the family mixes behaviours) is an ordinary method taking `error_in`
   and returning its result with `error_out`. The behaviour is read from each
   VI's diagram (`graph/error_behaviour.py`, `vi_error_behaviour`). Corpus: 25
   accessor VIs in 7 classes: 14 skip on error, 6 run regardless, 5 can fail.
   A class Property Node (NI, Property Node page: "The Property Node does not
   execute if an error occurs before it executes… If an error occurs in a
   property, LabVIEW ignores the remaining properties and returns an error")
   resolves each row to its accessor by the property's project-tree path
   (`folders:…:item`, recorded as `accessor_path`), runs rows top to bottom and
   skips the rest after a row's error. A Property Node on a class that is not
   loaded is a gap. **Not read:** "Ignore Errors Inside Node" — pylabview names no
   field for it, and the Property Node's `objFlags` vary in several bits across
   the corpus with no node known to set it; every node is generated with NI's
   default (stop at the first row error).
7. A later pass that turns a VI back into plain exceptions where provably the
   same (a VI that only passes its error along). **Recommend: decide after step
   7 reports how many VIs qualify; not part of this change.** Step 7 (39 VIs,
   top VI only): 3 of 38 have no error construct at all; 8 of 38 if the
   standard panel-fed "skip on error" case structure counts as passing along.
12. **Decided 2026-11-02:** Call By Reference and Start Asynchronous Call have
    their own error in and error out (frame terminals -3 / -4) beside the called
    VI's connector pane, whose error terminals (if its pane has any) are ordinary
    parameters and results of the call. An error on the node's own error in skips
    the call and passes through. The node's own error out reports a call that
    could not be made: a reference only known at run time that is Not a Refnum
    gives error 1026, "VI Reference is invalid." (NI, General LabVIEW Error
    Codes), when that error out is wired; an incoming error wins.
13. **Decided 2026-11-02:** an error cluster wired to an input NI says takes only
    its status (And, Or, Exclusive Or, Not, Not And, Not Or, Not Exclusive Or,
    Implies, Boolean To (0,1), Select's `s`, Compound Arithmetic; each NI page:
    "If x is an error cluster, only the status parameter of the error cluster
    passes to the input terminal") reads whether an error occurred
    (`error_flow.status_read`); a warning is False there. The catalog marks the
    input (`error_cluster_status`, or `inputs_error_cluster_status` on Compound
    Arithmetic, whose inputs grow with the node). And/Or Array Elements and
    Boolean Array To Number do not say it and are not marked.
14. **Decided 2026-11-02:** one rule for every node's output: it is written
    straight into its reader when exactly one terminal reads it on the node's own
    diagram (the same frame), its expression calls nothing, and nothing can
    change what it reads before the reader runs (a variable set again, a shift
    register's next value, an array or cluster changed in place); otherwise it is
    a variable named after its wire (`codegen/output_binding.py`). It changes how
    the code looks, never what it does.
15. **Decided 2026-11-02** (sources: NI's Greg McKaskle on NI's forum,
    td-p/81009: "these 'conditional indicators' should return a default value if
    they weren't written to… If still clear at subVI completion, the default is
    written to the indicator"; NI, Reentrancy page, "Choosing Which Type of
    Reentrant Execution to Use"; pylabview `ClearIndMask`): an indicator keeps its
    value from one call to the next unless the VI has "Clear indicators when
    called"; a Local Variable reads and writes that value; a connector-pane
    indicator a call does not write ends the call at its default, which is
    returned and kept. A connector-pane indicator no Local Variable reads simply
    starts each call at its default. Every value a VI keeps between calls (such an
    indicator, an uninitialized shift register, First Call?) is one module
    variable, whatever the VI's reentrancy (maintainer: "I want it to work… mark
    it in notes"): exact for a non-reentrant VI, one of LabVIEW's outcomes for a
    shared clone (NI: "Does not maintain state"), and different for a
    preallocated clone called from several places (NI: "Maintains state for each
    call site"), which the generated module notes above the variable.

## Open points for the maintainer

- **A gap that only a run-time value shows has no warning at generation.** For
  Search and Replace String with a pattern or replace string computed at run
  time, a PCRE escape Python `re` has no form of (`\h`, `\K`, `\Q`, …) and a
  `$n` beyond the pattern's submatches (NI does not say what LabVIEW gives) can
  only be known when the code runs: the generated code raises
  `NotImplementedError("<reason>")` there, with no warning at generation. The gap
  rule's "warn at generation" cannot apply when the value exists only at run
  time; a constant pattern or replace string is warned at generation as before.
- **To More Specific Class on a VI Server reference** passes the reference
  through; only a LabVIEW class wire is checked (`isinstance`, error 1448).
- **XNode views** (done after the talk): every XNode is named by its library (`XNXI`)
  with the terminal names its xTunnels record (`<id>`); the Error Ring's code has
  no slot in the views (lvnet shows no node detail), so it is not shown.
