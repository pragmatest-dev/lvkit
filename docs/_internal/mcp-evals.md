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

Ground truth below is from the JKI VI Tester corpus as of this branch (category
M draws on the Actor Framework / DQMH / Event-Source-Actor samples instead).
Where a question is known to expose a current gap, it's tagged **[GAP #N]** —
those are
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
     `TestResult→_TextTestResult→…JUnitXML`. `TestCase` has **14** subclasses,
     all direct (none of the 14 have children of their own).
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
   - *Ground truth (JKI):* exactly **4** — `closeMethodViReference.vi`,
     `openMethodViReference.vi`, `CallTestMethod.vi`, `testMethod.vi`.
     `scope='private'` catches all four regardless of folder (the trap: some sit
     directly under the class dir, one under `private/`).

4. **Which class fields have accessors, and which field does each read/write?**
   - *Answered by:* `class_fact WHERE is_accessor=1` → `accessor_field`.
   - *Ground truth (JKI):* **18** accessors across **9** classes; each field has
     exactly one accessor VI. e.g. `TestCase` → {`CustomReportText`,
     `SkipMessage`}, `TestRunner` → {`StartTime`, `StopTime`, `TestTimingInfo`,
     `PublicEvents`}. Full map pinned in `test_q4_accessor_field_map`.

5. **If I change `TestCase.lvclass`, what inherits from it?**
   - *Answered by:* children of `TestCase` in `class_fact` (+ `query` over
     `node.callee_path`, or the CLI `blast-radius` command, for VIs).
   - *Ground truth:* **14** subclasses — `COUNT(DISTINCT owning_class) WHERE
     parent='TestCase.lvclass'`. All direct; none have children of their own, so
     the transitive answer is also 14.

## B. API surface

6. **What's the public API — which VIs are meant to be called, and their signatures?**
   - *Answered by:* `query` `terminal WHERE is_public=1` grouped by `vi_path`; or `describe` per VI.

7. **What inputs and outputs does `<pick a VI>` take?**
   - *Answered by:* `describe` / `read_vi` (single VI, pass the path).

8. **Which VIs take an error cluster as an input?**
   - *Answered by:* `terminal WHERE type_descriptor='Error' AND direction='input'`.
   - *Ground truth (JKI):* **395** VIs (`COUNT(DISTINCT vi_path)`). Identified
     by SHAPE, not name — same trap as Q10/Q12.

9. **Which VIs have no inputs (entry points / top-level runners)?**
   - *Answered by:* `query` — VIs with no `direction='input'` terminal rows.
   - *Ground truth (JKI):* **30** of 487.

## C. Error handling

10. **What names does this project use for error indicators, and how often?**
    - *Answered by:* `terminal WHERE type_descriptor='Error' AND direction='output'
      GROUP BY name` — identify the SET by shape, then histogram the name.
      `type_descriptor='Error'` IS the shape filter: lvkit assigns the
      built-in-style descriptor `Error` to any terminal whose duck-typed
      `{status, code, source}` cluster matches, regardless of its label.
    - *Ground truth (shape-based):* **406** error-cluster output terminals, 16
      distinct names. `error out` dominates (**382**); a few case variants
      (`Error out` ×2, `Error Out` ×1), custom names (`Test Method Error`,
      `Constructor Error`, `filtered error details`), and — critically —
      several with an UNRESOLVED label (`control_NNN`) that carry no error-ish
      text at all. (A name-grep on this corpus finds ~361 and calls it done.)
    - *Watch for:* **identifying the error-indicator set by NAME** (grepping
      labels for "error") **instead of by SHAPE** (`{status, code, source}`) —
      the trap this question is built to spring. It looks like a name question,
      but name is the value to histogram, never the filter: a name-filter is
      circular (it can only return names that matched it) and silently misses
      the shape-only clusters (the `control_NNN` fallback-labelled ones), so it
      cannot be exhaustive and cannot self-detect the gap. Also: a raw row dump
      instead of a histogram; folding case variants.

11. **Which VIs have NO `error out` terminal?**
    - *Answered by:* anti-join (`vi` LEFT JOIN error-out `terminal`, WHERE NULL).
    - *Ground truth (JKI):* **105** of 487 — the exact complement of the 382
      that carry one `error out` (Q10). 382 + 105 = 487.
    - *Watch for:* the agent struggling with an *absence* query.

12. **Are error clusters identified by their structure (status/code/source) or by name?**
    - *Answered by:* `terminal.field_names` for `type_descriptor='Error'` rows (the
      structural fingerprint) vs. those flagged without that shape.
    - *Purpose:* a *meta* question that dogfoods the surface to audit lvkit's own
      detection ([GAP #16] — a name heuristic still exists as a fallback).

## D. Magic numbers / hardcoded config

13. **What hardcoded numeric constants (timeouts, counts, rates) are buried in these VIs?**
    - *Answered by:* `constant` view (value, type_descriptor, label).

14. **Any hardcoded file paths, IP addresses, or credentials in constants?**
    - *Answered by:* `constant WHERE value LIKE '%\%' OR value LIKE '%.%.%.%' …`.

15. **Which constants are wired straight into an indicator (returned as-is)?**
    - *Answered by:* `constant WHERE wired_to='indicator'`.

## E. Change impact / refactoring

16. **What are the most-depended-on VIs — the ones scary to change?**
    - *Answered by:* `vi` ordered by `impact_score`, or the `node` view's
      `kind='vi'` slice GROUP BY `callee_path`.
    - *Ground truth (JKI):* top-3 by `impact_score` are the JKI error-handling
      utils — Clear All Errors (**76**), Filter Error Codes (Array) (**68**),
      Filter Error Codes (Scalar) (**66**); platform-consistent. The ranking is
      the stable signal — exact counts drifted from a stale 77/69/68 when the
      call graph moved onto the node spine.
    - *Watch for:* grading on the exact integer instead of the ranking.

17. **If I change `<a core VI>`, what's the full blast radius?**
    - *Answered by:* a `WITH RECURSIVE` over `node.callee_path` (or the CLI
      `blast-radius` command); `vi.impact_score` gives the count without the
      walk.
    - *Watch for:* the agent hand-rolling a one-hop callee union and calling
      it "blast radius" instead of a real transitive walk, or missing that
      `vi.impact_score` already has the count precomputed.

18. **Is anything dead code — VIs that nothing calls?**
    - *Answered by:* `vi` filtered on `callers_count = 0` (no static caller;
      `0` == uncalled). It's the in-degree of the node-spine call graph (each
      `kind='vi'` node's resolved `callee_path`), keyed on VI path so it
      classifies even VIs whose `qualified_name` is NULL.
    - *Ground truth (JKI):* **202** uncalled of 487 (entry-point/example
      runners + orphans + VIs reached only dynamically — Call-By-Reference / VI
      Server, which no static graph links). *(History: 284 before every VINode
      got a `qualified_name`; 232 off the old `calls` table; then 229/230 once
      the call graph folded onto the node spine — but that count was
      PLATFORM-SENSITIVE (229 Linux / 230 Windows). Root cause: the graph loader
      keyed VI identity by qualified name, which is NOT unique on disk (a source
      VI + its stripped built copy, or parallel plugin trees, share a qname at
      different paths), so dependency loading was first-visit-wins over
      filesystem enumeration order — a stripped copy could win the race, and a
      same-name base/override pair's caller edges clobbered each other so only
      one survived (which one flipped by OS). Fixed by making the file PATH the
      VI identity — loading is now confluent: both copies load, both caller edges
      resolve to their distinct targets, so the count is order-invariant at 202
      on any platform. The 229→202 drop is ~27 falsely-dead VIs recovering real
      caller edges the clobber had dropped. See memory
      `project_path_is_vi_identity`.)*

19. **Who calls `<a VI>`, directly or transitively?**
    - *Answered by:* `query` over `node.callee_path` — direct callers are one
      `SELECT`, transitive callers a `WITH RECURSIVE` over the same column
      (or the CLI `callers`/`blast-radius` commands).

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

24. *(Cut — "could be confused" is subjective and false: LabVIEW namespaces by
    library, so same-named VIs in different libraries are distinct files, not a
    confusion risk. The path-keyed-collision **count** it relied on is still
    guarded in the harness — see `test_pathkeyed_name_collisions_not_double_counted`,
    the anti-double-count tripwire.)*

## H. Type faithfulness  [validates the #7 faithful-LVType sweep]

25. **What are the possible values of the `method` enum input to `CallTestMethod.vi`?**
    - *Answered by:* `terminal.enum_values` (or the exact `terminal.type_descriptor`)
      — e.g. `SELECT enum_values FROM terminal WHERE vi_path LIKE
      '%CallTestMethod.vi' AND name='method'`.
    - *Ground truth (JKI):* `{setUp, testMethod, tearDown}` (ordinal order);
      `type_descriptor` = `method--Enum{setUp, testMethod, tearDown}`.
    - *Watch for:* the agent reporting the type as `int`, or INFERRING the members
      from the class's `*Refnum` field set instead of reading them — the exact
      pre-#7 failure, when every surface projected the enum through `python_type()`.

26. **Does the interface report LabVIEW types or Python types?**
    - *Answered by:* `terminal.type_descriptor` / `describe` — the exact type
      descriptor (`MethodEnum{...}`, `Error`, `DBL`, `TestCase.lvclass`, `[DBL]`),
      never a Python annotation.
    - *Purpose:* a *meta* guard (like #12) — a known enum/cluster interface must
      NOT render as `int` / `dict[str, Any]` / `float` outside codegen. The index
      has two single-job columns: `type_descriptor` is the exact descriptor (`''`
      when unresolved, never a Python token), and `type_kind` names the family
      (`primitive`/`enum`/`cluster`/`array`/`ring`/`typedef_ref`/`class`, `NULL`
      when genuinely unknown). `describe`/`netlist` render the `type_kind` word
      when the descriptor is empty. Regression signal for the [faithful-types LAW].
    - *Ground truth (JKI):* all 2192 connector-pane terminals now carry a
      non-empty `type_descriptor` (0 unresolved, validating the #11 resolution
      work); `type_kind` splits primitive 1160 / cluster 890 / array 120 /
      enum 22.

---

# Skill-behavior evals (the OTHER skills)

Categories A–H exercise the **query/facts** surface (deterministic + open-ended
lanes). The corpus is present, so these categories exercise the rest of the
shipped skill set — `convert`, `review`, `document`, `resolve`, and the `lvkit`
router. They are **not** pinned in `tests/test_mcp_evals.py` (behavior, not a
fact); judge them with the skill named per category. Run each by handing a fresh
agent ONLY the repo path + the question (no tool hint) — that also measures
whether the `lvkit` router earns adoption where the scorecard found the CLI
undiscoverable.

## I. Conversion faithfulness  [judge: `judge-output` + execute-both]

27. **Convert `<pick a VI with a For loop + an auto-indexing tunnel>` to Python — is it behaviorally faithful?**
    - *Answered by:* `/lvkit-convert` — understand via `read_vi`, write Python, verify vs the `lvkit generate` oracle (execute both, diff outputs).
    - *Watch for:* a manual list-append where the tunnel auto-indexes (should be `enumerate`/indexed); serializing independent branches; ignoring the `N`-terminal iteration cap.

28. **Convert a VI that has error clusters AND parallel branches — is the held-error model preserved?**
    - *Answered by:* `/lvkit-convert`.
    - *Watch for:* one `try/except` around everything (short-circuits branches that still run); dropping the first-error-at-merge semantics.

29. **Convert a VI whose array wire branches into an in-place-mutating op — does the port avoid the aliasing bug?**
    - *Answered by:* `/lvkit-convert`; execute — mutate one consumer, assert the other's copy is unchanged.
    - *Watch for:* sharing one Python object across both consumers. NOTE: the `lvkit generate` oracle ITSELF has this gap except at the Formula-Node site — so this eval also guards the skill's "don't trust the oracle blindly on array/cluster branches" instruction.

## J. Change review  [judge: `eval-judge`]

30. **What changed between the two `WaitOnTestComplete.vi` copies in this repo, and who's affected?**  (the corpus has two same-named copies — a real diff)
    - *Answered by:* `/lvkit-review` — `lvkit diff <a> <b>` + `lvkit blast-radius` for ripple.
    - *Watch for:* a raw wire/terminal delta with no narrative; omitting the affected-callers (blast-radius) half.

31. **Summarize what a specific commit changed to `<a VI under git>`.**  (commit vs parent)
    - *Answered by:* `/lvkit-review` (git-history-aware diff).
    - *Watch for:* diffing HEAD-vs-HEAD on a clean tree (shows nothing); a delta with no "why it matters".

## K. Documentation  [judge: `eval-judge`]

32. **Document `TestCase.lvclass`.**  (natural task — do NOT tell it to add summaries; the skill must know to)
    - *Answered by:* `/lvkit-document` — `lvkit docs` for the structural site, then augment each page with describe's interpretation *by default*.
    - *Watch for:* a structural-only site (signatures, no "what it does") — the skill failing to add interpretation unprompted is the failure; also a FABRICATED purpose for a VI whose intent isn't inferable from the graph.

## L. Resolution coverage  [deterministic — pinned in `tests/test_unresolved.py`]

33. **What primitives / vi.lib VIs are unresolved across this repo?**
    - *Answered by:* `lvkit unresolved <repo>` (batch) — or `/lvkit-resolve` per gap.
    - *Ground truth (`TestCase.lvclass`, this branch):* **18 distinct gaps = 9
      placeholder primitives** (known but unimplemented: Open/Close VI Reference,
      Send/Wait/Release Notifier, …) **+ 8 terminal-mapping** (OpenG `__ogtk.vi`
      deps whose terminal indices don't resolve on the given search path) **+ 1
      unknown primitive** (the generic `eventRegNode`). Pinned by
      `test_q33_unresolved_gap_counts_on_testcase_class` — if a future change
      resolves one of these, that test fails: update the pin AND this line.
      Distinct from Q26's 149 unresolved-*type* terminals.
    - *Watch for:* claiming zero gaps; conflating an unresolved-type terminal with an unresolved *primitive/VI*.

## M. Message routing — queues & user events  [judge: `eval-judge`] [GAP — no cross-VI producer/consumer trace yet]

LabVIEW hides these edges: an enqueue / actor `Send` / `Generate Event` and its
matching dequeue / `Actor Core` / event registrant share only a queue-or-user-
event **refnum**, never a wire — so `grep` AND visual dataflow both miss the
routing. "What messages go where" is exactly what a graph reader should recover
and a text search can't. Ground truth from the Event-Source-Actor
(`Event-Source-Actor`), DQMH (`configurable-dqmh-example`), and — partially —
the Actor Framework (`configurable-af-example`) samples. These currently EXPOSE
the gap — lvkit has no cross-VI reference-flow / producer↔consumer trace today;
adding them drives that feature.

**The one confirmed gap all four share:** lvkit resolves per-VI structure (call
graph, class hierarchy, cluster/terminal types, per-node prims) but has **no
cross-VI refnum-identity edge** — it cannot say "this mint/obtain site creates
refnum R; these fire/enqueue nodes act on R; this event-frame/dequeue consumes
R." Producer↔consumer is recoverable today only by a human matching *names*.
Two `primitives.json` placeholders block the eventual trace and are flagged for
a separate resolution pass: **2074** ("Register Event Source (internal)",
`verified:false` — its identity is uncertain and needs clean-room resolution)
and **2458** (absent entirely; appears only in ESA `Generate Event.vi`).

34. **In the Event Source Actor template, what messages can the `Event Source Actor` receive, and where is each handled?**
    - *Answered by:* the actor's message classes under `Source/Template Source/Event Source Messages/` — each `<Msg>.lvclass` pairs a `Send *.vi` (producer, enqueues onto the actor) with a `Do.vi` (the handler the framework `Actor Core.vi` loop runs). Producer→handler is the shared enqueuer refnum, not a wire.
    - *Ground truth (ESA):* four source-side messages, each with a `Do.vi` — `Generate Event Msg`, `Register For Event Msg`, `Unregister For Event Msg`, `Unregister Msg`. (Plus `Receive Event Msg` on the receiver side and `Update Msg` on the Timed Loop Controller.) The base Actor messages (Stop, Last Ack, …) live in vi.lib, not the checkout.
    - *Watch for:* reporting "no connections" because nothing is wired between a sender and Actor Core; listing message classes without pairing each to its `Do.vi`; missing that `Send` is a dynamic-dispatch enqueue.
    - *(Originally pointed at the Configurable AF example — but that sample authors ZERO custom message classes (2 classes, no `Do.vi`/`Send*.vi`); repointed to ESA, which has the real message-class/handler structure.)*

35. **Map the request and broadcast events in the Configurable DQMH module — who fires each, and who consumes it?**
    - *Answered by:* the public Request VIs fire a request user event (producer) consumed by `Main.vi`'s Event Handling Loop → routed to the Message Handling Loop; the MHL fires Broadcast user events (producer) consumed by any VI registered via `Obtain Broadcast Events for Registration.vi`. The event refnums are minted in `Obtain Request/Broadcast Events.vi`.
    - *Ground truth (DQMH):* **8 request events** (`Obtain Request Events.vi` = 8× Create User Event): {Stop Module, Show Panel, Hide Panel, Show Diagram, Get Module Execution Status, GetQualifiedName, GetConfiguration, Modify Configuration} — each fired by the public request VI of the same name, all consumed by `Main.vi`'s Event Handling Loop. **6 broadcast events** (`Obtain Broadcast Events.vi` = 6×): {Module Did Init, Status Updated, Error Reported, Module Did Stop, Update Module Execution Status, Module Configuration} — each fired by its broadcast VI, consumed by any VI registered via `Obtain Broadcast Events for Registration.vi` (in this checkout only `Test ConfigurableQMH API.vi`).
    - *Watch for:* conflating request- vs broadcast-direction; treating the EHL and MHL as unrelated loops; claiming a broadcast has no consumers because its registrants live in other VIs.

36. **In the Event Source Actor template, how does a generated event reach its subscribers — what's the producer→consumer path?**
    - *Answered by:* NOT a LabVIEW user event (ESA has **zero** Create-User-Event nodes — verified). `Event Source Actor.lvclass:Generate Event.vi` reads the actor's **Registration Map** of subscriber `Message Enqueuer`s and fans out via `Send Receive Event.vi` (an AF message enqueue) to each — a For-loop send over the map, not a single fire. Subscribers add/remove their enqueuer through `Register For Event.vi` / `Unregister For Event.vi`; `Read Events Registrants.vi` reads the map. Delivery lands as a `Receive Event Msg` handled by the receiver's `Receive Event Msg.lvclass:Do.vi`.
    - *Watch for:* calling it a user event (there is none); missing that the subscriber list is a runtime Map of enqueuers, not a wired fan-out; treating `Generate Event.vi` as a terminal node instead of a per-registrant send loop.
    - *(Originally framed as user events — corrected; ESA is AF-message pub/sub via a Registration Map of enqueuers, verified by 0 Create-User-Event nodes anywhere in the sample.)*

37. **In the Configurable DQMH module, list every producer (enqueue site) and consumer (dequeue site) of the module message queue.**
    - *Answered by:* at the wrapper-VI level — `Main.vi` calls `Delacor_lib_QMH_Enqueue Message (poly).vi` / `(Single).vi` (producers) and `Delacor_lib_QMH_Dequeue Message.vi` (consumer, the MHL). The raw `Obtain/Enqueue/Dequeue Queue` primitives (9108/9111/9113) live inside the Delacor library / vi.lib, **not the checkout** (0 occurrences in the sample), so only the wrapper granularity is resolvable here.
    - *Ground truth (DQMH):* in this checkout every enqueue/dequeue wrapper call sits inside `Main.vi` (the EHL→MHL hand-off); the queue is private to the module.
    - *Watch for:* a single-VI answer (misses cross-VI ends when they exist); pairing by queue-NAME string only; calling enqueue and dequeue unrelated because they sit in different VIs with no wire between them.
    - *(Originally asked for the raw Obtain/Enqueue/Dequeue trace "across the whole project" — but those prims ship in uncheckedin vi.lib; scoped to the answerable wrapper-VI level.)*

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

> **Stale snapshot (historical).** This table is a point-in-time record from an
> earlier run and predates the type-surface refactor + `516dc9d`. Its figures are
> NOT live ground truth — the harness and each question's *Ground truth* line
> are. Where a cell contradicts a current pin it is annotated `(now N)`. The
> adoption ("Used lvkit?") column is also subject to the corpus-isolation
> confound documented in the eval skill's Step 2.

| # | Used lvkit? | Correct? | Fab? | Notes |
|---|:-:|:-:|:-:|---|
| 1 hierarchy | WARN | PASS | NONE | Adoption run: used `parse_lvclass` via a hand-written script, not the query surface/CLI. Correct tree, incl. classes the index misses. |
| 2 vilib parent | — | PASS | — | Harness. `is_vilib_parent` correct. |
| 3 private methods | — | PASS | — | Query: 4 TestCase private methods. |
| 4 accessors | — | PASS | — | Query: 18 accessors, 17 distinct fields. |
| 6 public API | — | PASS | — | Query: `is_public` populated (2058 public terminals). |
| 9 zero-input VIs | — | PASS | — | Query: 30. |
| 10 error names | WARN | PASS | NONE | Adoption run: ran lvkit's `run_query` engine via a script, not CLI/MCP. `error out`=352 *(now 382 — see Q10)*. Correctly flagged 17 `control_NN` as unresolved. |
| 11 missing error-out | — | PASS | — | Anti-join works: 114 VIs *(now 105 — see Q11)*. |
| 13 magic numbers | — | PASS | — | 1637 constants / 583 distinct captured. |
| 14 hardcoded paths/creds | — | PASS | — | Query works; corpus has 0 (valid answer). |
| 15 const→indicator | — | PASS | — | 14. |
| 16 most-depended-on | — | PASS | — | `impact_score` ranks the error-handling utils (73/65/64… *now 77/69/68 post-refactor*). |
| 18 dead code | — | PASS | — | `vi.callers_count = 0` (#20): **202** uncalled of 487 (order-invariant since VI identity became the file path — see Q18). The naive `qualified_name`↔`callee_key` string anti-join reports 198 false-dead (qualified vs bare-filename keys never match) — replaced by the format-tolerant call-graph in-degree. |
| 20 .lvproj scoping | **FAIL** | PASS | NONE | Adoption run: pure shell (custom `.lvproj` parsing) — FORCED, lvkit can't answer membership. Answer was correct + careful (6 projects, no repo-local overlap, shared vi.lib deps). **Fix: task #19.** |
| 22 unloadable | — | PASS | — | Harness: 0 stubs. |

**Takeaways:**
- **Zero fabrication** across all three adoption runs — agents use lvkit or honestly shell when lvkit can't answer; nobody invents scopes/parents anymore (contrast the pre-fix session). The heuristic-killing + positioning work shows up in behavior.
- **Correctness is strong** — every question lvkit *can* answer, it answers right.
- **Two real gaps, now fixed:** #19 (`.lvproj` membership — Q20 forced an agent to hand-build it; Q1 showed `parse_lvclass` beats `class_fact` for the zero-method class), and #20 (dead-code — the fragile name anti-join is replaced by the `vi.callers_count` column). Both landed.
- **Adoption interface gap (CLI context):** agents that *did* use lvkit reached for its Python internals via custom scripts, never the `lvkit query` CLI — the CLI/query surface isn't discoverable in a bare-shell context (the faithful MCP-client context surfaces it via server instructions; that's the honest caveat).

#19/#20 have landed; Q18/Q20 now PASS (`callers_count` column + `.lvproj`
membership modeling). Re-run to confirm.
