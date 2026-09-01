# lvkit coding-style rules

The house rules for HOW lvkit code is written — idiom, structure, correctness
conventions. These are enforced conventions, not architecture (how the system is
shaped: `ARCHITECTURE.md`) and not workflow behaviors (auto-memory). CLAUDE.md's
"Code Style" section points here; follow these whenever writing lvkit code. Add a
new coding-style rule HERE, not to memory.

---

## Keep It Simple Stupid — no special structures, no duplication, consistent patterns across the whole system
<!-- was memory: feedback_kiss -->

Keep It Simple Stupid. On projects like this complexity compounds fast. Every layer should be consistent and direct. The implementation should define and use conceptually simple rules that apply consistently and generically across the space.

**Why:** Complexity snowballs. Special-case code creates maintenance burden and inconsistencies. When one subsystem uses a different pattern than another for the same concept, bugs hide in the gaps.

**How to apply:**
- Match prior art in the same codebase — don't invent new structures when existing ones work
- If primitives use `terminals` arrays, node_types use `terminals` arrays
- No code-side lookup maps when the data belongs in JSON
- One lookup path, not parallel systems
- If a field is optional metadata on an existing structure (like `dco_ref` on a terminal), add it there — don't create a parallel structure
- When tempted to create a "map" or "bridge" between two things, ask: why aren't they the same thing?
- DRY — Don't Repeat Yourself. If data exists in one place, don't duplicate it in code. If a structure is defined once, reuse it everywhere.

## "The user HATES god-modules — build code as small single-responsibility modules from the start, never let a file grow into a thousand-line multi-job module"
<!-- was memory: feedback_no_god_modules -->

The user hates god-modules. When **building** code (not just refactoring later),
keep every module to **one responsibility** and split proactively. Do not let a
file accrete multiple jobs and grow into a thousand-line catch-all — that is the
single biggest source of friction: huge edit blast-radius, no parallelism (every
change serializes on the one file), agents thrash across cross-references, and
review is painful.

**Why:** e.g. `src/lvkit/graph/netlist.py` reached **6,325 lines** doing five jobs
at once (the model, the graph→model builder, the old renderer, `render_lvnet`, the
JSON serializer). It became *the* bottleneck for a multi-day feature — every phase
had to fight the same giant file. The pain was structural, not effort.

**How to apply:**
- Decide module boundaries **up front, by responsibility** (model / builder /
  each renderer / serializer / grammar-constants), and create the seams as you
  write — don't defer the split to a someday-refactor.
- If a file is heading past a few hundred lines with more than one clear job,
  that's the signal to split *now*, not later.
- A new "engine" (render/diff/serialize/parse) is its own module from birth.
- This is the general rule; the variant-hierarchy case is
  [[feedback_decompose_variants]], the parked netlist split is
  [[project_module_decomposition]], and it's the module-level face of
  [[feedback_single_job_fields]] / [[feedback_kiss]].

## "Variants of a concept → a decomposed class hierarchy (abstract base + one subclass per variant, one file each), NOT a god module with flat dispatch"
<!-- was memory: feedback_decompose_variants -->

When there are VARIANTS of a concept, model them as a **decomposed class hierarchy**: an abstract base class (the shared interface/contract + genuinely shared behavior) that **everything inherits**, and **one subclass per variant, each in its own file** — never a single 1000s-of-lines script that dispatches on a kind string. A variant "draws/does itself" via the base's contract.

Applies to every variant family: render glyphs (arith, array, bundle, constant, structure, wire, terminal, property/invoke, event, …), and in time primitives, vi.lib VIs, structure kinds, node types, LVType kinds, artifact producers — graph, parser, codegen, netlist, all of it.

**Why:** lvkit's value is the LONG TAIL of specialties (the missing primitives / vilib VIs / glyphs / behaviors). A class hierarchy scales that tail — adding a new specialty is adding a subclass in a new file, not editing a shared god module — while a flat dispatch script gets more tangled and cycle-prone with every addition (the render `draw.py`/`glyph.py`/`scene.py` god modules were the trigger for this rule).

**How to apply:** the moment you see or introduce variants of a concept, reach for base-class + per-variant-subclass + one-file-each, and DESIGN the file layout (propose the package tree, get sign-off) before moving code — don't blindly split. Start with the render layer (glyphs); the pattern set there is the house style the rest of the codebase conforms to over time, decomposed module-by-module as we touch each. See [[project_module_decomposition]], [[feedback_kiss]], [[feedback_types_over_dicts]], [[feedback_graph_not_dicts]].

## ALWAYS use typed dataclasses/NamedTuples instead of dicts - user has strong preference for types everywhere
<!-- was memory: feedback_types_over_dicts -->

ALWAYS use typed dataclasses instead of dicts. No exceptions.

**Why:** User has explicitly and repeatedly stated this preference. Using dicts loses type safety, makes code harder to reason about. Typed dataclasses provide clear type contracts. This is a hard rule, not a suggestion.

**How to apply:** When creating new data structures, defining return types, edge attributes, node attributes — always use typed dataclasses with typed fields. Never use `dict[str, Any]` as a lazy substitute. For new code, use dataclasses. Existing dicts can be migrated opportunistically.

## Never put imports inside methods or functions — always at module level
<!-- was memory: feedback_no_inline_imports -->

NEVER put imports inside methods or functions. ALL imports go at module level (top of file).

**Why:** User explicitly and repeatedly corrected this. Inline imports are lazy, make dependencies invisible, and violate Python conventions.

**How to apply:** Before writing any `from X import Y` inside a function body, STOP and add it to the module-level imports instead. No exceptions.

## NEVER use multi-line inline python3 -c calls — write temp scripts and run them
<!-- was memory: feedback_no_inline_python -->

NEVER use multi-line inline `python3 -c "..."` calls in Bash. NEVER use /tmp/ for temp scripts.

**Why:** Multi-line inline scripts trigger permission prompts. /tmp/ is not project-local and causes permission issues.

**How to apply:** Write temp scripts to `.tmp/` (project-local, gitignored). Example: `.tmp/lvkit_check.py`. Run with `python3 .tmp/lvkit_check.py`. Single-line `python3 -c "short one liner"` is OK only if it truly fits on one line.

## All user-defined types (classes, typedefs, controls) must use fully qualified names — bare filenames are ambiguous
<!-- was memory: feedback_qualified_names -->

All user-defined LabVIEW types — classes, typedefs, custom controls — are identified by their file path in the project hierarchy. The fully qualified name (with library ownership chain) IS the type identity. Two types can share the same short name but differ by namespace.

**Why:** `LibA.lvlib:Status.ctl` and `LibB.lvlib:Status.ctl` are different types. Bare filenames like `Status.ctl` are ambiguous.

**How to apply:** Every `lv_type.classname`, typedef reference, and custom control reference must carry the fully qualified name, not the bare filename. The VCTP in the XML only stores bare filenames — resolve bare → qualified at load time using the project/library structure, and store the qualified form everywhere. Generalize rules that work consistently across all type references, not special-case fixes per use site.

## "Codegen must be byte-reproducible: per-VI node UIDs live in hash-randomized sets — any order-materializing query MUST sort by _node_order_key"
<!-- was memory: feedback_deterministic_node_order -->

Non-determinism in generated code is unacceptable ("non-determinism is death" — rfried, 2026-06-23). lvkit stores per-VI node UIDs as **sets** (`_vi_nodes: dict[str, set[str]]`), so iterating `node_uids` / any UID set has **hash-randomized order between processes** (`PYTHONHASHSEED`).

**Why:** the same VI generated twice produced different Python — independent parallel-tier ops swapped order, dragging their collision-suffix variable names (`output_array_696`↔`_999`) with them.

**How to apply:** any time node order becomes observable (operation order, parallel tiers, inner-structure ordering, disconnected-op append, type/enum/cluster discovery), impose a deterministic order with `_node_order_key` (in `graph/core.py`: VI base, then numeric LabVIEW object id). When feeding a dependency graph to networkx, both `add_nodes_from(sorted(..., key=_node_order_key))` AND use `nx.lexicographical_topological_sort(g, key=_node_order_key)` — plain `nx.topological_sort` breaks ties by insertion order, which is hash-dependent when nodes came from a set. Guarded by `tests/test_determinism.py` (generates a parallel-branch VI under multiple hash seeds, asserts identical output). Add new ordering paths to that test. See [[reference_formula_node_oracle_issue8]], [[project_parallel_execution_gap]], [[feedback_no_heuristics]].

## User prefers over-parallelization over incorrect sequential ordering — downstream AI can simplify
<!-- was memory: feedback_over_parallelization -->

Always emit ThreadPoolExecutor for parallel tiers, even if the parallelism is unnecessary (e.g., pure compute with no I/O). Do NOT add heuristics to skip parallelization.

**Why:** Incorrect sequential ordering is a silent bug — an AI downstream can't know when sequential code was supposed to be parallel. Over-parallelization is explicit and correct. A downstream AI can easily strip unnecessary ThreadPoolExecutor blocks.

**How to apply:** When generating code from tiered topological sort, always wrap multi-op tiers in ThreadPoolExecutor regardless of whether the ops are I/O-bound.

## String matching is the most brittle interface — use indices/connections from the graph
<!-- was memory: feedback_no_string_matching -->

String matching is one of the most brittle interfaces we can choose. When we have literal graph connections (terminal indices, wire edges), use those — not string-matched names.

**Why:** Template placeholders like `"x"`, `"array"`, `"number_path_refnum"` fail when `to_var_name()` transforms terminal names differently than the template author expected. Index-based references (`in_0`, `in_1`) map directly to graph connections and never break.

**How to apply:** Primitive python_code templates should use `in_N` (input index) references, not terminal names. Output expressions should be matched to outputs by position, not by string-matching dict keys against terminal names.

## "Give each field ONE known-information job; a field that degrades/conflates (a \"faithful\" fallback) is a hack — split it"
<!-- was memory: feedback_single_job_fields -->

Each data field/method should hold ONE well-defined piece of known information
and stick to that job. A field that conflates granularities or falls back
between different things is a hack.

**Example (this codebase):** `faithful_type_label()`/`faithful_type_descriptor()`
returned EITHER the exact type descriptor OR, when the type didn't resolve, a
coarse control-family word — two jobs crammed into one string, dressed up as
"faithful." The clean model is two single-job fields: `lv_type` = the exact type
descriptor (empty when unresolved, never degrades); `lv_type_family` = the coarse
kind (`primitive`/`cluster`/`enum`/`array`/`refnum`/`class`), ALWAYS present.

**Why:** conflated fields force string-matching on made-up values and hide a
guess/fallback behind a trustworthy-sounding name. Two honest fields each stay
truthful at their own granularity.

**How to apply:** when tempted to add a fallback/degrade path to a field, add a
SECOND field with its own job instead. Watch for "faithful"/"best-effort"/
"resolved-or-…" naming — that's the tell that a field is doing two jobs.
Relatedly: don't call a type-descriptor string a "label" ([[feedback_read_before_claiming]]
territory — labels/captions belong to controls).

## Unknown node types must cause errors, not silent warning strings in output
<!-- was memory: feedback_no_silent_warnings -->

Unknown node types in the codegen must FAIL LOUDLY (raise an error), not silently emit warning strings into the generated code. Silent warnings hide bugs and produce garbage output that looks like it succeeded.

**Why:** The user explicitly requires this. Warning strings like `"# WARNING: Unknown node type"` embedded in generated Python are invisible failures that make the output look cleaner than it is.

**How to apply:** The `UnknownNodeCodeGen` class in `base.py` should raise an error, not return a CodeFragment with a warning string. The caller must handle every node type or explicitly skip structural infrastructure nodes.

## NEVER use heuristic guesswork — either we know something from data or we don't and we fail clearly
<!-- was memory: feedback_no_heuristics -->

NEVER use heuristic guesswork. Either we KNOW something from actual data or we DO NOT and we fail with a clear diagnostic.

**Why:** Heuristics produce silently wrong results. Guessing VI names from display names, inferring types from terminal names, matching by "probably this" — all of these cause bugs that are invisible until they produce wrong code. Every heuristic is a lie waiting to be discovered.

**How to apply:**
- If the data tells us X, use X
- If the data doesn't tell us X, FAIL with a diagnostic saying "X is unknown, here's what we DO know"
- NEVER derive/construct/infer/guess values that aren't directly in the data
- Don't map display names to VI names. Don't infer types from names. Don't "probably" anything.
- When something is missing from JSON, say so and let the user fill it in
- The only "matching" allowed is exact data: type+direction matching against JSON terminal entries that have actual type values
- No magic strings: don't `split(" (")` to derive a base name. Store it explicitly.
- No string manipulation to "figure out" relationships. If A relates to B, store the relationship as data.
- Every string split, regex, name derivation, or pattern assumption is a heuristic. Find the data or store it.
