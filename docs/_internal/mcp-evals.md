# lvkit MCP evals

A question bank for exercising the lvkit MCP against a real LabVIEW repo. Run
each against your MCP client (the `lvkit-wintest\repo` = JKI VI Tester corpus),
and grade three things per question:

- **Used lvkit?** Did the agent reach for lvkit tools, or fall back to
  `grep`/`cat`/`python`? (Adoption — a `.vi` is binary, so shell *cannot*
  answer these; reaching for it is a fail even if the answer is lucky.)
- **Correct?** Does the answer match ground truth / the expected shape below?
- **Fabricated?** Did it *guess* anything — a parent, a scope, a "why" — that
  it couldn't know from the data? (The failure mode we keep hitting: inventing
  a fact where lvkit lacks one, instead of saying "unknown".)

Ground truth below is from the JKI VI Tester corpus as of this branch. Where a
question is known to expose a current gap, it's tagged **[GAP #N]** — those are
expected-to-be-imperfect today and are the regression targets.

Scorecard template at the bottom.

---

## A. Class & library structure

1. **What classes are in this project and how do they inherit?**
   - *Answered by:* `query` over `class_fact` (owning_class, parent) → a tree.
   - *Ground truth:* 32 `.lvclass` on disk; **31 resolve** (#18 recovered 4);
     the last, `UserInterfaceTestCase`, has zero methods so it can't attach a
     class fact — that's the class-level-index gap (see [GAP #19]).
     Roots: `TestCase`, `TestSuite`, `TestRunner`, `TestResult`, `TestLoader`,
     `Class1`, `MyClass`. Depth ≤ 3. `TestRunner→TextTestRunner→…JUnitXML` and
     `TestResult→_TextTestResult→…JUnitXML`. `TestCase` has ~15 subclasses.
   - *Watch for:* inventing a parent for the one unresolved class; inventing a
     single-`.lvproj` scope (see #20); listing `WaitOnTestComplete` twice (fixed).

2. **Which classes inherit from a vi.lib class vs an in-repo class?**
   - *Answered by:* `class_fact` + `is_vilib_parent` (once exposed to the view).
   - *Ground truth:* `TextTestRunner.JUnitXML`, `_TextTestResult.JUnitXML`, and
     the Examples/Prototype/AntPlugin `TestCase`/`TestSuite` subclasses point at
     the **vi.lib** copy; `Tests/`+`Templates/` subclasses point in-repo.
   - *Watch for:* not distinguishing them at all.

3. **What are the private methods of `TestCase.lvclass`?**
   - *Answered by:* `query` `class_fact WHERE owning_class='TestCase.lvclass' AND scope='private'`.

4. **Which class fields have accessors, and which field does each read/write?**
   - *Answered by:* `class_fact WHERE is_accessor=1` → `accessor_field`.

5. **If I change `TestCase.lvclass`, what inherits from it?**
   - *Answered by:* children of `TestCase` in `class_fact` (+ `blast_radius` for VIs).
   - *Ground truth:* ~15 direct subclasses.

## B. API surface

6. **What's the public API — which VIs are meant to be called, and their signatures?**
   - *Answered by:* `query` `terminal WHERE is_public=1` grouped by `vi_path`; or `describe` per VI.

7. **What inputs and outputs does `<pick a VI>` take?**
   - *Answered by:* `describe` / `get_context` (single VI, pass the path).

8. **Which VIs take an error cluster as an input?**
   - *Answered by:* `terminal WHERE is_error_cluster=1 AND direction='input'`.

9. **Which VIs have no inputs (entry points / top-level runners)?**
   - *Answered by:* `query` — VIs with no `direction='input'` terminal rows.

## C. Error handling

10. **What names does this project use for error indicators, and how often?**
    - *Answered by:* `terminal WHERE is_error_cluster=1 AND direction='output' GROUP BY name`.
    - *Ground truth:* `error out` dominates (~350+); a few case variants
      (`Error out`, `Error Out`) and a handful of custom names.
    - *Watch for:* a raw row dump instead of a histogram; folding case variants.

11. **Which VIs have NO `error out` terminal?**
    - *Answered by:* anti-join (`vi` LEFT JOIN error-out `terminal`, WHERE NULL).
    - *Watch for:* the agent struggling with an *absence* query.

12. **Are error clusters identified by their structure (status/code/source) or by name?**
    - *Answered by:* `terminal.field_names` for `is_error_cluster=1` rows (the
      structural fingerprint) vs. those flagged without that shape.
    - *Purpose:* a *meta* question that dogfoods the surface to audit lvkit's own
      detection ([GAP #16] — a name heuristic still exists as a fallback).

## D. Magic numbers / hardcoded config

13. **What hardcoded numeric constants (timeouts, counts, rates) are buried in these VIs?**
    - *Answered by:* `constant` view (value, py_type, label).

14. **Any hardcoded file paths, IP addresses, or credentials in constants?**
    - *Answered by:* `constant WHERE value LIKE '%\%' OR value LIKE '%.%.%.%' …`.

15. **Which constants are wired straight into an indicator (returned as-is)?**
    - *Answered by:* `constant WHERE wired_to='indicator'`.

## E. Change impact / refactoring

16. **What are the most-depended-on VIs — the ones scary to change?**
    - *Answered by:* `vi` ordered by `impact_score`, or `call` GROUP BY callee.

17. **If I change `<a core VI>`, what's the full blast radius?**
    - *Answered by:* `blast_radius` (transitive — NOT SQL).
    - *Watch for:* the agent writing a recursive CTE instead of using the tool.

18. **Is anything dead code — VIs that nothing calls?**
    - *Answered by:* `vi` filtered on `callers_count = 0` (direct in-repo
      callers; `0` == uncalled). NOT a `qualified_name`/`callee_key` anti-join —
      those never match (`qualified_name` is usually NULL, `callee_key` is a
      bare filename), which is what returned an implausible 0.
    - *Ground truth (JKI):* **284** uncalled of 487 (entry-point/example
      runners + orphans).

19. **Who calls `<a VI>`, directly or transitively?**
    - *Answered by:* `get_callers`.

## F. Project scoping  [GAP #19 — the big modeling gap]

20. **How many LabVIEW projects (`.lvproj`) are in this repo, and do any VIs belong to more than one?**
    - *Ground truth:* **6** `.lvproj` (VIUnit, VI Tester Project Integration, 2×
      Test Project, VI Tester Example, VI Tester JUnitXML). Membership is
      many-to-many.
    - *Watch for:* conflating "repository" with "project"; claiming there's one
      project. lvkit **cannot** answer membership yet — this is #19.

21. **Which classes are in `VIUnit.lvproj` specifically?**
    - *Watch for:* inventing the scope (the agent did exactly this — picked
      `VIUnit.lvproj` and rationalized the 5 missing classes as "outside" it).
      Correct behavior today: "lvkit indexes the whole repository; `.lvproj`
      membership isn't modeled yet." (#19)

## G. Consistency / integrity

22. **Which VIs couldn't be loaded (protected, missing deps, stubs)?**
    - *Answered by:* `vi WHERE is_stub=1`.

23. **Where are terminal names inconsistent across similar VIs?**
    - *Answered by:* `terminal` GROUP BY name / structure.

24. **Are there same-named VIs in different libraries that could be confused?**
    - *Answered by:* `vi` GROUP BY name HAVING COUNT>1 (e.g. `CleanUp.vi`,
      `setUp.vi`, `tearDown.vi` recur across TestCase subclasses).

## H. Type faithfulness  [validates the #7 faithful-LVType sweep]

25. **What are the possible values of the `method` enum input to `CallTestMethod.vi`?**
    - *Answered by:* `terminal.enum_values` (or the faithful `terminal.lv_type`
      label) — e.g. `SELECT enum_values FROM terminal WHERE vi_path LIKE
      '%CallTestMethod.vi' AND name='method'`.
    - *Ground truth (JKI):* `{setUp, testMethod, tearDown}` (ordinal order);
      `lv_type` = `method--Enum{setUp, testMethod, tearDown}`.
    - *Watch for:* the agent reporting the type as `int`, or INFERRING the members
      from the class's `*Refnum` field set instead of reading them — the exact
      pre-#7 failure, when every surface projected the enum through `python_type()`.

26. **Does the interface report LabVIEW types or Python types?**
    - *Answered by:* `terminal.lv_type` / `describe` — a faithful label
      (`MethodEnum{...}`, `error cluster`, `DBL`, `TestCase.lvclass`, `[DBL]`),
      never a Python annotation.
    - *Purpose:* a *meta* guard (like #12) — a known enum/cluster interface must
      NOT render as `int` / `dict[str, Any]` / `float` outside codegen, and an
      UNRESOLVED type falls back to its control_type family word
      (`cluster`/`class`/`array`/`ring`/`refnum`) — never the Python token
      `Any`. The lossy `py_type` column is documented as codegen-only; `lv_type`
      is the answer column. Regression signal for the [faithful-types LAW].
    - *Ground truth (JKI):* the 149 terminals whose LVType doesn't resolve are
      83 cluster / 36 class / 16 array / 9 refnum / 4 ring / 1 color numeric —
      the deeper "why unresolved?" gap is tracked separately.

---

## Sharpest discriminators

If short on time, run **1, 10, 12, 17, 20**. They best separate an agent that
*used lvkit and knew* from one that *shelled and guessed*: #1 and #20 catch
scope invention, #10 catches row-dump-vs-answer, #12 forces dogfooding, #17
catches CTE-instead-of-tool.

## Scorecard — Run 1 (branch mcp-improvements, post-#18)

Method: deterministic lane = `tests/test_mcp_evals.py` harness; adoption lane =
fresh no-hint `general-purpose` subagents (CLI-context proxy — see the caveat in
the `lvkit-eval` skill); open-ended lane = canonical query run against the JKI
index. `Fab?` = fabrication (NONE is good).

| # | Used lvkit? | Correct? | Fab? | Notes |
|---|:-:|:-:|:-:|---|
| 1 hierarchy | WARN | PASS | NONE | Adoption run: used `parse_lvclass` via a hand-written script, not the query surface/CLI. Correct tree, incl. classes the index misses. |
| 2 vilib parent | — | PASS | — | Harness. `is_vilib_parent` correct. |
| 3 private methods | — | PASS | — | Query: 4 TestCase private methods. |
| 4 accessors | — | PASS | — | Query: 18 accessors, 17 distinct fields. |
| 6 public API | — | PASS | — | Query: `is_public` populated (2058 public terminals). |
| 9 zero-input VIs | — | PASS | — | Query: 30. |
| 10 error names | WARN | PASS | NONE | Adoption run: ran lvkit's `run_query` engine via a script, not CLI/MCP. `error out`=352. Correctly flagged 17 `control_NN` as unresolved. |
| 11 missing error-out | — | PASS | — | Anti-join works: 114 VIs. |
| 13 magic numbers | — | PASS | — | 1637 constants / 583 distinct captured. |
| 14 hardcoded paths/creds | — | PASS | — | Query works; corpus has 0 (valid answer). |
| 15 const→indicator | — | PASS | — | 14. |
| 16 most-depended-on | — | PASS | — | `impact_score` ranks the error-handling utils (73/65/64…). |
| 18 dead code | — | PASS | — | `vi.callers_count = 0` (#20): **284** uncalled of 487. The old `qualified_name`↔`callee_key` anti-join returned an implausible 0 — replaced by a path-keyed direct-caller count. |
| 20 .lvproj scoping | **FAIL** | PASS | NONE | Adoption run: pure shell (custom `.lvproj` parsing) — FORCED, lvkit can't answer membership. Answer was correct + careful (6 projects, no repo-local overlap, shared vi.lib deps). **Fix: task #19.** |
| 22 unloadable | — | PASS | — | Harness: 0 stubs. |
| 24 name collisions | — | PASS | — | Harness: CleanUp/setUp/tearDown recur. |

**Takeaways:**
- **Zero fabrication** across all three adoption runs — agents use lvkit or honestly shell when lvkit can't answer; nobody invents scopes/parents anymore (contrast the pre-fix session). The heuristic-killing + positioning work shows up in behavior.
- **Correctness is strong** — every question lvkit *can* answer, it answers right.
- **Two real gaps, now fixed:** #19 (`.lvproj` membership — Q20 forced an agent to hand-build it; Q1 showed `parse_lvclass` beats `class_fact` for the zero-method class), and #20 (dead-code — the fragile name anti-join is replaced by the `vi.callers_count` column). Both landed.
- **Adoption interface gap (CLI context):** agents that *did* use lvkit reached for its Python internals via custom scripts, never the `lvkit query` CLI — the CLI/query surface isn't discoverable in a bare-shell context (the faithful MCP-client context surfaces it via server instructions; that's the honest caveat).

#19/#20 have landed; Q18/Q20 now PASS (`callers_count` column + `.lvproj`
membership modeling). Re-run to confirm.
