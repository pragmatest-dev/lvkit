# lvkit ARCHITECTURE

**This file is the canonical architecture reference. Read it before making any
architectural claim or proposing any solution that touches the graph, the
pipeline, or a VI view. Every claim here cites `file:line` — verify against the
code, never against memory.**

> Memory (`MEMORY.md` and `memory/*`) is for **BEHAVIORS ONLY** (how we work,
> feedback, hard constraints). Architecture — how the code is shaped — lives
> HERE. When you get architecture wrong or learn a new architecture fact, add it
> to THIS file, not to memory. This file exists because architecture facts kept
> being forgotten and mis-stated; keep it filled.

---

## THE INVARIANT (do not forget this again)

**lvkit progressively builds ONE graph, and every command is a projection of
that graph.** There is no separate subsystem for render vs. describe vs. lvnet vs.
diff vs. codegen — they all read the **same** `InMemoryVIGraph`. To add a new view
of a VI you BUILD/EXTEND THE GRAPH exactly like every other command does, then
project it with that view's renderer. You never route one view through another
view's renderer, and you never reason about the tool as if graphs aren't the
substrate.

---

## The graph (`src/lvkit/graph/`)

- `InMemoryVIGraph` is a persistent, **incrementally built** structure. `load_vi`
  loads a VI hierarchy INTO the same graph and dedups by resolved-file-path key:
  a VI already loaded at ≥ the requested depth is a no-op early return; a VI first
  seen as a leaf UPGRADES when later loaded in its own right (`loading.py:278`,
  dedup/upgrade at `:327-339`). **Identity is the file path**, always.
- `LoadMode` sets dependency depth (`loading.py:290-294`):
  `NONE` = this VI only; `MINIMAL` = this VI + direct SubVIs' connector panes +
  referenced-type fields (render/diff/describe — byte-identical to FULL, far
  cheaper); `FULL` = the whole SubVI/class-method tree (codegen).
- `load_vi_by_path(path, mode, …)` builds ONE VI into a **fresh** graph and
  returns `(graph, vi_name)` — this is what CLI commands call (`graph/loading.py`,
  `graph/__init__.py:7`).
- Class fields: `get_class_fields` returns **parent + own** fields, parent first,
  walking the inheritance chain by parent PATH key — but only when the parent is
  loaded and **not a stub**: `pk not in self._stubs` (`core.py:380-432`, gate at
  `:408`). A stubbed/absent parent drops inherited fields (this is the root of the
  Bundle-By-Name `[N]` index-fallback bug — nMux indices are into the full
  parent+own list, `core.py:385-388`).

## Views are projections of the graph (all peers, all read the graph)

| View | Entry point | Where |
|------|-------------|-------|
| Diagram (SVG/HTML) | `build_scene(graph, vi_name)` → SVG | `render/scene.py:1767`; body `render/__init__.py` |
| lvnet (text) | `build_netlist_from_graph(graph, vi_name)` → `render_lvnet(module)` | `cli.py:1196-1200`; `graph/render_lvnet.py:1773` |
| describe (text) | `describe_vi(graph, vi_name)` | `cli.py:1202` |
| codegen (Python) | `build_module(vi_context, vi_name)` | `codegen/builder.py` |

**Trap I fell into:** `render_vi_body` only handles `fmt` `svg`/`html`
(`render/__init__.py:32`). lvnet is NOT a render format — it's a separate
projection (`build_netlist_from_graph` + `render_lvnet`). Do not try
`cached_render(fmt="lvnet")`.

## Output cache (`src/lvkit/output_cache.py`)

Caches **rendered output STRINGS** (not graphs), keyed by
`(input_path, fmt, options, version)`: `lookup_render`/`store_render`/
`cached_render` (`:201`, `:307`). There is **no graph-object cache** — each
command/job builds its own graph (progressively) and discards it. A repeat render
of the same VI is an instant cache hit on the stored string.

## Frontends

- **CLI** (`src/lvkit/cli.py`): each command does `load_vi_by_path(mode)` into a
  fresh graph, then projects (e.g. `cmd_describe`: `:1153` load → `:1189-1202`
  project by `--format`).
- **Desktop VS Code** (`editors/vscode/extension.js`): each custom editor
  **shells a fresh `lvkit` subprocess** per view — `viPreview` shells
  `lvkit render`, `viText` shells `lvkit describe --format lvnet` (`:470`). Views
  do NOT share a graph (separate processes). Registers `viPreview` + `viText`
  (`:592-597`).
- **Web VS Code** (`editors/vscode/web/extension.js`): a persistent Pyodide
  "engine" WebviewView boots lvkit-in-wasm once and answers jobs (`:86-90`). The
  host stages a VI's dependency closure into the engine's `/proj` MEMFS
  (`stageDependencyClosure`), then runs a job. Engine jobs are dispatched by type:
  `render`/`diff`/`probe`/`deps` (`:625-744`), each building a graph and
  projecting. Wheels/Pyodide load from CDN (`CDN_PYODIDE`/`CDN_WHEELS`, `:70-72`);
  the webview CSP allow-lists those origins (`webviewCsp`, `:565`).
  - **Parity gap (known):** the web entry registers ONLY `viPreview` (`:536`);
    it does NOT implement `viText`/lvnet, though `package.json`'s
    `contributes.customEditors` advertises `lvkit.viText` for every `*.vi`
    (priority `option`) on both targets — so the web offers an lvnet editor with
    no provider, and it spins forever. Fix = add a web `viText` provider + an
    `lvnet` engine job that builds the graph (like `render` does) and projects
    `render_lvnet`.

## How to add a new VI view (the rule)

1. Build/extend the graph the same way every command does (`load_vi` at the right
   `LoadMode`; on the web, stage the closure into `/proj` first, same as render).
2. Project it with that view's renderer (a peer of `build_scene`/
   `build_netlist_from_graph`).
3. Wire the frontend: CLI subcommand, desktop subprocess, AND a web engine job +
   custom-editor provider. Check `package.json` `customEditors` isn't advertising
   a viewType one target doesn't register.

---

## Design principles & decisions (consolidated from memory)

Architecture/design decisions moved out of auto-memory (which is behaviors-only).
Some carry code receipts; others are principles/laws to **verify and cite against
the code as they're touched** — treat an uncited claim here as a lead, not a fact.
Integrate these into the sections above (with `file:line`) over time.

### CORE DIRECTIVE — Use the graph for relationships and lookups, NEVER add parallel dict/table structures
<!-- was memory: feedback_graph_not_dicts -->

The InMemoryVIGraph is the single source of truth. NEVER add parallel dict structures or lookup tables to the graph class. Everything is a node with properties, connected by edges.

**Why:** The graph IS the perfect tool for this application. Adding dicts alongside the graph creates redundant state, breaks the single-source-of-truth principle, and ignores the graph's natural ability to represent relationships through traversal.

**How to apply:**
- Need to store class field info? Add a class NODE to the graph with fields as properties.
- Need to look up type info? Traverse edges from the terminal to the type definition node.
- Need to associate data with an entity? Add it as a property on the entity's graph node.
- NEVER add `_foo_dict: dict[str, ...]` to InMemoryVIGraph as a parallel lookup structure.
- The only acceptable internal state on InMemoryVIGraph is the graph itself and indexes that accelerate graph traversal (like `_term_to_node`).

### "THE KEY for every dep-graph node is the FILE PATH — never qname/name; resolve refs by caller-scoped graph query, name is only confirmation"
<!-- was memory: feedback_path_is_the_node_key -->

**THE KEY FOR DEP-GRAPH NODES IS THE FILE PATH. FOREVER.** (#26,
path-is-VI-identity.) Nodes still CARRY a name and qname — as attributes, for
display and *confirmation* — but the node KEY, every edge target, and every
dedup is the resolved absolute file path (`str(path.resolve())`).

**Why:** a NAME is not unique and never can be. A bare name (`Do.vi`) matches
two different libraries' files. Even a FULL qname is not unique — a **built copy
beside its source twin share the same qname** at different paths (the exact bug
#26 fixed). Only the PATH is unique. So:

- NEVER dedup, key, or resolve identity by qname/name. `has_node(qname)`,
  `_name_to_keys[bare]`, "pick one of N by min()/first" — all WRONG (a guess
  between two real files). Guessing = forbidden ([[feedback_no_heuristics]]).
- The disambiguating information is ALWAYS the **caller**: the caller referenced
  ONE specific file by RELATIVE PATH (its LinkSavePathRef), which the loader
  resolved to a unique path and recorded as a caller→callee EDGE.

**How to apply — resolve a reference by CALLER-SCOPED GRAPH QUERY:** to resolve
what class/typedef/VI a terminal's type or a call refers to, query the graph
among the **caller VI's own dep successors** (`_dep_graph.successors(caller_key)`)
and use the name/qname only to CONFIRM which edge — a caller links exactly one
path per referenced dependency, so within the caller the name is unambiguous
even for built/source twins. The query RETURNS the path key. You start from a
name ONLY at true boundaries (a VI's parsed `type_map` records a class TYPE by
name; user/CLI input) — and even there, scope by caller and never guess; if it
can't be pinned to one node, return None, never a guess.

Graph-internal navigation NEVER touches names: dependency loading starts from
LinkSavePathRef path tokens; inheritance follows a recorded `parent_key` (the
parent's PATH on the node), not `parent_class` name.

**Confluence (the correctness property):** loading the same corpus in ANY order
must fill in the exact same, maximal information — holds by construction only if
identity is the path (each path loads once, fully, from its own bytes). A
name/qname key made loading first-visit-wins: order-dependent AND
information-losing (a later, richer copy never contributed; dead-code counts even
differed by OS enumeration order). Path is also the index's load + invalidation
unit (`(path, content_sha)`), so identity, load, and invalidation are all the
one code file.

**⛔ OPERATIONAL anti-pattern (recurred 3×): never re-resolve a loaded VI by BARE
FILENAME.** `graph.resolve_vi_name(node.name)` throws away the path and silently
picks ONE of several same-named VIs — catastrophic for dynamic-dispatch class
hierarchies where every override is literally `run.vi` (so `describe .../run.vi`
renders the wrong class's VI and tests "pass" on the wrong one). The fix: use the
`loaded_key` that `load_vi(path)` returns; the user-facing sites share ONE helper
`graph.load_vi_by_path(path, mode) → (graph, key)` (a DRY law — re-deriving the
dance per call site is why it recurred); a subVI callee resolves by its own
`qualified_name`/edge key, never `node.name`; and `_pick_vi_key` RAISES
`AmbiguousVIReferenceError` when candidates are >1 distinct identity (only
collapses genuine on-disk duplicates of the same VI).

**Built copies are stripped** (clean-room binary fact): a built copy is smaller,
`password_protected`, reports an older `lv_version`, and its controls lose their
labels — so content richness (BD/labels present) / `.lvproj` membership is the
signal for "which twin is authoritative," NEVER a `"Built"` folder-name
heuristic. See [[feedback_graph_not_dicts]], [[feedback_no_string_matching]],
[[feedback_no_heuristics]], [[feedback_qualified_names]].

### Containment is semantic → graph (forward children); z-order is a draw concern → render layer (layout paint rank). Never put paint order in the graph.
<!-- was memory: feedback_containment_graph_zorder_render -->

When splitting "who is inside whom" from "in what order do they paint," keep the
two in different layers:

- **Containment = semantic understanding → the GRAPH.** It matters to codegen
  scoping, netlist, describe, blast-radius — not just render. The graph must
  expose it as a first-class FORWARD relationship (a stored children list), not
  only a scalar `parent` back-link that forces an O(N) whole-VI reverse scan to
  find a container's children.
- **z-order / paint sequence = a DRAW concern → the RENDER layer.** It comes
  from the heap's zPlaneList and belongs with the render geometry (the layout),
  never in the graph. Bundling "children **in zPlaneList order**" onto a graph
  node smuggles a draw concern into the graph — split them: graph children in a
  deterministic/canonical order (pure structure), render applies paint order on
  top.

The render composite then asks the graph *"what's inside X?"* and the layout
*"in what order do I paint them?"*

**Why:** The user re-derived this hard under pressure while reviewing the #35/#39
render-tree plan — first "z-order is a draw concern, keep it out of the graph,"
then "but containment IS critical logical understanding, why wouldn't the graph
have it," then "deriving children from `parent` needs a full-graph reverse
lookup." The resolution reconciles all three.

**How to apply:** Before adding any ordering/index field to a graph node, ask
"is this ordering a render/paint concern?" If yes, it lives in the render/layout
layer, not the graph. See [[feedback_graph_not_dicts]],
[[feedback_dont_defend_fossilized_decisions]] (the plan's parser+graph z-order
step was a prior, not a verdict — re-derived to render-only). Also
[[feedback_never_describe_vi_as_python]] (live layer vs codegen separation).

### try/except wrapping goes at Clear/Merge/error-case nodes, not at every node with error terminals
<!-- was memory: feedback_error_handling_model -->

Error output terminals do NOT mean "this function raises." The error cluster is just data flowing through. The `raise` only happens when something actually sets `status = True` (creates an error). Most SubVIs just pass the error cluster through unchanged — that's natural exception propagation in Python (no code needed).

try/except wrapping goes at the point where the graph HANDLES the error, wrapping whatever upstream code could have created the error:

1. **Clear Errors** → `try: <upstream code> except LabVIEWError: pass` (swallow)
2. **Merge Errors** → `try: <upstream code> except LabVIEWError as e: _held_error = _held_error or e` (hold first error, reraise merged)
3. **Error case structure with non-empty handling** → `try: <upstream code> except LabVIEWError as e: <do the work>; raise` (lowest priority pattern)

**Why:** The previous approach wrapped every branch in try/except when `use_held_error_model` was True, producing dead code. Error handling is graph-driven — look for Clear/Merge/error-case nodes and wrap the code that feeds into them.

**How to apply:** Don't use `use_held_error_model` as a blanket flag. Instead, find Clear/Merge/error-case nodes in the graph and wrap the upstream operations that feed into their error input terminals.

### "Error clusters are transformed/filtered in EXACTLY ONE place — Python generation. Every other surface (describe/document/diff/netlist/render/visualize) must show them faithfully, never hide them."
<!-- was memory: feedback_errors_faithful_except_codegen -->

**CRITICAL RULE.** A LabVIEW error cluster is turned into something else — a Python exception — in EXACTLY ONE place: **Python generation (`src/lvkit/codegen/`)**. Everywhere else is a FAITHFUL representation of the VI: `describe`, `document`, `diff`, `netlist`, `render`, `visualize`. Those surfaces MUST show error clusters as the real terminals and wires they are. NEVER filter/hide `is_error_cluster` terminals — not from signatures, not from wiring, not from component ports, not from SubVI I/O.

**Why:** error clusters are real VI structure — connector-pane terminals + wires the block diagram literally draws. Hiding them is a lie about the VI. "errors → exceptions" is a CODEGEN translation choice; it has no business in a faithful view. This exact bug happened: the codegen filter (`if not t.is_error_cluster: skip`) leaked into `describe.py`, `diff.py`, `netlist.py`, `cli.py`, `op_walk.py` — hiding error in/out from every signature/netlist/diff, and making error-handling nodes like `Merge Errors.vi` render as a meaningless `()`. The user was (rightly) furious; it had to be ripped out of all faithful surfaces.

**How to apply:** In any non-codegen surface, treat an error-cluster terminal like any other terminal — list it, wire it, declare it. `is_error_cluster` / `_is_error_cluster` is legitimately used ONLY to: (a) STYLE error wires in render (color/glyph — that SHOWS them), (b) LABEL case frames ("No Error"/"Error"), (c) FORMAT an error-cluster VALUE for display, (d) map to exceptions INSIDE `codegen/`. If you are about to write `if not x.is_error_cluster` to SKIP/omit a terminal anywhere outside `codegen/`, STOP — that is the banned filtering. Same principle generalizes: transformation/translation of LabVIEW semantics belongs in codegen; the view surfaces stay faithful. [[project_netlist_feature]] [[feedback_no_heuristics]]

### LAW — describe VIs as LabVIEW types everywhere except the Python generator; python_type()/to_python() are codegen-only
<!-- was memory: feedback_never_describe_vi_as_python -->

**Never describe a VI's types as Python anywhere but the Python generator.**
`Terminal.python_type()` / `LVType.to_python()` are the CODEGEN target-type
projection — lossy by design (`enum`/`ring`→`int`, `cluster`→`dict[str,Any]`,
`typedef_ref`→`Any`), so they destroy enum member strings, cluster field names,
typedef names, and refnum types. Every FAITHFUL surface — `describe`,
`get_context`, `get_constants`, `netlist`, `diff`, `render`, the project index,
docs, CLI — must render the real `LVType` instead (`kind` + `enum_values` +
cluster fields + `typedef_name` + refnum type). A faithful renderer already
exists: `render/style.py::lv_type_label` / `type_repr`.

**Why:** generalizes [[feedback_errors_faithful_except_codegen]] from error
clusters to ALL types. Demonstrated failure (2026-08): an MCP agent asked for a
VI's enum interface (`method: methodEnum` → setUp/testMethod/tearDown), and the
parser HAD the members (`Terminal.enum_values`), but `describe` rendered the
signature via `python_type()` → `int`, and no MCP tool surfaced `enum_values`,
so the agent could only INFER the mapping. The "index is lean because you can
load+interrogate a VI live" justification is void when the live layer also
projects to Python.

**How to apply:** grep `python_type()`/`to_python()`; anything outside
`codegen/` is a violation. Replace with the shared faithful `lv_type_label`.
Confine `python_type`/`to_python` to `codegen/`. Describe/netlist/diff golden
outputs change to LabVIEW-faithful — regenerate + re-review them. Index
`TerminalFact` should carry faithful type facts (enum members, fields), not a
lossy `py_type` string. See [[project_netlist_feature]] (netlist is the shared
IR for describe --verbose / diff / viewer).

### Primitive polymorphism cannot be assumed — must be observed in data or confirmed by user
<!-- was memory: feedback_primitive_polymorphism -->

Never assume a primResID is polymorphic (used for different operations based on type). Primitive polymorphism must be determined by:

1. **Observed multi-type use** — multiple instances in the codebase with genuinely different terminal type signatures (not just element types leaking through the parser)
2. **Asking the user** — if uncertain, ask rather than guess

Do NOT independently decide that a primResID maps to a different function based on terminal types. The parser reports element types for array terminals (e.g., NumFloat64 for an Array of Float64), which makes array operations look like numeric operations. This caused the wrong Split Number override for prim 1056.

**Why:** Incorrectly classifying a primitive as polymorphic led to generating `2 >> 16` (Split Number) when the correct output was `2[:offset]` (Split 1D Array on a type descriptor byte array). The "numeric" terminals were actually array element types.

**How to apply:** When resolving primitives via the `/resolve-primitive` skill, do not add type-conditional overrides or switch to a different primitive's template. If terminal types seem wrong for the documented function, investigate the parser's type representation first. Polymorphic VIs (marked in metadata) are different — those ARE clearly marked and can be processed independently.

### lvkit ships ZERO NI-derived primitive art; NI-icon render path removed; only public docs available (no licensed LabVIEW)
<!-- was memory: project_ni_art_licensing -->

lvkit must be **cleanroom** for primitive glyphs. The user has **no licensed
LabVIEW** — only NI's **public documentation**. Public NI docs are still NI's
copyrighted material, so extracting/redistributing NI's icon images (even from
public docs, even vectorized) is a licensing problem.

**Removed 2026-07-11 (commit 7b23d85, lv-renderer):** the entire NI-icon path.
Deleted `PdfIconResolver` + its asset-stem/size helpers (render/nodes.py), the
`src/lvkit/data/glyphs/` asset dir (PNGs pulled from the reference PDF + their
SVG vectorizations + `_manifest.json`/`_svg_sizes.json`), and the extraction
scripts (`scripts/extract_lv_icons.py`, `scripts/vectorize_icons.py`). The
image assets were already gitignored + excluded from wheel/sdist (nothing
licensed ever *shipped*), but the render path still embedded that art into
locally-generated SVGs. Un-migrated primitives now render as a labeled box
(`WrappedBoxGlyph`). Verified: 0 NI-derived primitive glyphs render (was 11).

**What survives (all clean):** `OriginalGlyphResolver` (clean-room ORIGINAL
shapes, drawn by us, leads the chain), `GeneratedGlyphResolver` (procedural
triangles/brackets), `JsonGlyphResolver` (declarative inline SVG — dormant,
primitives.json has no icon fields), `ExtractedIconResolver` (renders a
subVI's OWN `_ICON.png` — the USER's file at runtime, not shipped by us),
`FallbackBoxResolver`. pyproject exclude globs kept as a guard.

**Implication for [[project_task_list]] #14 / #30 / #36:** #36 ("matched SVG
renderings") produced NI-DERIVED vectors and is now purged — treat it as
reverted. The real path is #14: draw ORIGINAL clean-room glyphs (own interior
symbol, LV footprint from public reference *for measurements only*, never
copying NI pixels). See [[feedback_verify_render_against_reference]],
[[feedback_never_fake_lookups]].

### "The VI netlist IR must be a TRUE netlist (schematic of concurrent nets), not a sequential SSA IR — the core design intent behind --format netlist"
<!-- was memory: project_netlist_is_a_schematic -->

The `describe --format netlist` IR exists because **LabVIEW dataflow IS a schematic**: nodes are parts, wires are nets, and all data flows **simultaneously, like electrical signals** — there is no statement order. That is the whole reason a netlist was chosen as the representation. The maintainer wants a REAL netlist, not "netlist-inspired SSA."

The current emitter drifted into a **gated-SSA IR wearing a netlist costume** (γ/σ merges rendered as `select`/`shift`/`collect`, `dest := source` assignments, scope-local abbreviated net names). The maintainer's verdict: "THE IR IS THE WORST … I SAID NETLIST INSPIRED AND YOU MADE SHIT SANDWICH INSPIRED." This is a hard design constraint, not a preference.

**LOSSLESS IR — THE HARDEST LAW.** The netlist IR must express the ENTIRETY of the program; a human or agent must be able to understand the whole VI from this text ALONE, with zero data loss. NEVER arbitrarily drop "uninteresting" information — every instance (full qualified component path), every terminal (inputs AND outputs, wired or not), every type, every constant/default value, every wire, every frame + selector value, shift-register seed/recur, must be present. No summarizing, no abbreviating, no collapsing that loses a fact. (This is DISTINCT from the Properties-section terse/verbose curation, which applies only to VI *settings* like Window flags — the DATAFLOW/structure/wiring is always complete.) The repeated failure here was rendering *summaries* and calling them the IR.

**DON'T INVENT SYNTAX — APE ESTABLISHED NETLIST/HARDWARE-IR LANGUAGES.** Web-searched prior art (Aug 2026). Chosen base: **FIRRTL** (chipsalliance firrtl-spec) — the only netlist-family IR that is human-readable AND has all of: typed ports (`input x : T`/`output`), `inst U of Module`, `<=` connect, `when/else` conditionals (= LabVIEW case frames), `reg` (= shift registers/feedback), typed signals, constants. Extend ONLY for LabVIEW's DYNAMIC loops (`for`/`while` — no hardware netlist has them; they unroll). Rejected: SPICE/BLIF (no types/no control flow), EDIF (LISP-y, machine-only, unreadable), gate-level Verilog (no control flow), Yosys RTLIL (has control via process/switch but verbose + tool-facing). Note FIRRTL's `<=` is `sink <= source` (reads right-to-left) — standard convention, keep unless the maintainer says flip. Criteria the maintainer set: "read most simply for humans but still pack the semantics and structure for agents."

A true-netlist rendering must satisfy (each fixes a defect the maintainer surfaced on `loadTestsFromTestCase.vi`):
1. **One name per net, spelled identically at driver and every reader** — follow a wire by grepping its name, land once. No `_short_net` abbreviation, no `out0` meaning a loop net in one place and a case net in another, no quoted-in-header/unquoted-in-arm.
2. **Every node — structures included — presents its outputs the same way.** A subVI reads `ins ▷ Node ▷ outs`; a case/loop is also a node and lists its output nets on its own header, NOT hoisted into separate `select`/merge lines below the frames.
3. **Each frame declares what it drives onto the structure's output nets, inside the frame** — don't force the reader to reconstruct a frame's contribution from a bottom-of-block `select`.
4. **Read in flow order, source → dest** (a schematic runs left-to-right), never `dest = source`.
5. **No fake machinery**: a tunnel/merge whose source is identical in every frame is a plain wire, not a degenerate `select`.
6. **Concurrent, not sequential**: present it as connectivity (a set of parts + nets), not `:=` assignment statements.

**CONVERGED DESIGN (2026-08-25, after a long iteration).** The netlist is a GENERATED (never hand-written) lossless IR, FIRRTL's *structure* but 100% LabVIEW *vocabulary*, with types INLINE at each node (no separate "component"/`extmodule` interface block — that was a rejected FIRRTL import; every subVI node shows its own terminals+types+wires+defaults, exactly as it appears on the diagram). Agreed keyword table: `vi` (the described VI) · `subVI <qualified>` (a call; kinds also `function <LV name>` / `property-node` / `invoke-node` / `feedback-node` / `constant` / `local-variable` / `formula-node` / `comment`) · `case` + `frame "<value>"` · `for-loop` / `while-loop` · `flat-sequence` / `stacked-sequence` · `event-structure` · `diagram-disable` / `conditional-disable` / `type-specialization` · `in-place-element` · `shift-register` (init/each) · `tunnel : <auto-indexing|last-value|concatenating|pass-through[ + conditional]>` · `control`/`indicator` (= the VI's `in`/`out` boundary) · `wire` (a net, followed by name). Data types render as faithful LVType `: T` (`DBL`,`String`,`Error`,`[String]`,`Cluster{…}`,`Enum{…}`,`MyClass.lvclass`,`refnum{…}`). Working prototype: `.tmp/ir_to_lvnet.py` transforms `describe --format json` → this text (proves lossless-by-generation on `loadTestsFromTestCase.vi`), with sample renders `.tmp/before.lvnet`/`.tmp/after.lvnet`. **BINDING OPERATOR — DECIDED (final, 2026-08-26, supersedes `:=` and `<=`): `=` means "connected to a DRIVER" — a net OR a constant (a *literal* is a constant, an *identifier* like `loop0.shift0` is a net, per the Verilog convention); `default <value>` with NO `=` means UNWIRED (the terminal falls back to its default; for a type with no literal default → `default (default T)`). Direction is `sink = source` (terminal/boundary-output on the LEFT, R→L dataflow).** The Greek `γ/μ/η` AND the `select`/`shift`/`collect` rename are BOTH DEAD in the human surface — case output = per-frame `caseN.outK = source` line INSIDE each frame; shift register = a `shift-register <net> :` block (`init =` / `each =`); loop output tunnel = `tunnel <net> : <mode> = source`. (select/shift/collect survive only as INTERNAL JSON `kind` tags in `ir_to_lvnet.py`, never in the text.) `.tmp/*.lvnet` + `ir_to_lvnet.py` are stale on the operator ONLY (they still show `<=`; final is `=`) — everything else in them is the landed form. STILL OPEN: whether to collapse a degenerate all-frames-identical tunnel to a plain wire; the exact per-construct TERSE reduction (categories known, not field-by-field); verify not-yet-modeled LV constructs (Timed Loop, Global/Shared Variable, Call By Reference, MathScript — proposed keywords `timed-loop`/`global-variable`/… unverified in the model). It is an IR in the CLASS of a hardware IR (FIRRTL is to VHDL as LLVM IR is to C) — the synthesizable subset could in principle flow to hardware (cf. LabVIEW FPGA), which is CONFIRMATION the model faithfully captures the dataflow (shift-register→register, case→mux, primitive→logic, cluster→bus), not a project goal.

**LOSSLESS AUDIT (2026-08-25, 3 parallel agents vs the graph model).** Hard findings: (1) **No format has a lossless verbose today** — `describe --format json` IGNORES `-v` entirely (byte-identical, `cli.py:1202-1212`); `--format netlist -v` only prepends a `## Components` table, body unchanged; text `-v` shows all property flags but drops wiring & much else. (2) **TWO stacked lossy boundaries:** (A) **`GraphNode → Operation`** (`operations.py::_build_operation` / `core.py`) drops fields STRUCTURALLY so no downstream form can recover them — disable-structure KIND (Diagram/Conditional/TypeSpec all flatten to `disabled`), `displayed_frame`/`active_frame`/`case_insensitive`, `hidden_border_terminals`, Event `filter_node_uids` (Filter vs Data node), `poly_variant_name`, local-variable fields (`is_write`/`control_terminal_id`), `primResID`/`prim_index` as data, In-Place decompose/recompose ops; (B) each RENDERER then ignores surviving fields — **text conveys NO wiring** (`ctx.data_flow` never read; local variables excluded from `_OPERATION_KINDS` in `core.py:70-81` so they're invisible; Property/Invoke print as bare `"Property Node"`), **netlist ASCII drops** VI properties/health/class-context/free-labels/node-labels (all JSON-only or unread). (3) Convergent COLLAPSES in all three: `wiring_rule` tri-state→bool, structured `LVType`→one opaque string, internal (non-connector-pane) FP controls absent. **CONCLUSION: the emitter MUST read the `GraphNode` model + VIContext facets (labels/properties/class/FP-terminals) directly — NOT `Operation`, NOT `describe --format json`. Boundary (A): **DECIDED (maintainer) — emit straight from the full `GraphNode` model + VIContext facets; START FULL and throw away per-format during the conversion; NEVER start from the lossy `Operation` intermediate ("no reason to start lossy"). Do not retrofit `Operation`.** The gate is a round-trip `reparse(emit_verbose) == graph`. (This netlist/json/text-viewer work is DEFERRED to a future version; see below.)**

**SCOPE CORRECTION (maintainer, same day): the LOSSLESS forms are NETLIST + JSON only. `--format text` is the HUMAN description/overview — deliberately curated, NOT a lossless target; an agent must NOT use text for full understanding (it should use netlist/json). So text's audit "losses" (no wiring, no locals, bare property nodes) are BY DESIGN, not bugs. Focus losslessness on netlist + JSON.** Both netlist AND json have TWO modes: **verbose = LOSSLESS** (everything, round-trippable) and **non-verbose (terse) = STRUCTURALLY UNDERSTANDABLE** (the nodes/wiring/control-flow/types/values skeleton is fully clear; only verbose-only detail — full VI properties, node labels/descriptions, raw values, exhaustive type internals, wiring-rule nuance, hidden-terminal reveal — is dropped for volume). Terse is a documented reduction of the proven-lossless verbose, never its own ad-hoc thing. Also: **LOCAL VARIABLES ARE TERMINALS** (read/write endpoints on a front-panel control/indicator's net — a tap on that signal), NOT standalone nodes. Model a control/indicator as a named net; its FP terminal + every local/global read (source) and write (sink) are endpoints on it; multiple writes = multiple drivers of one signal (LabVIEW's sequential/race semantics — surface it). Full reconciled table: to be written to `docs/_internal/design/netlist-loss-audit.md`.

**DEFERRED-WORK DECISION (node-type baseline provenance):** incorporate the PUBLIC LIST OF NODES posted in the GitHub issue as a node-type baseline — into the text-format updates OR a separate format. Use **the issue as PROVENANCE** and mark every such entry **UNVERIFIED**, because the maintainer cannot personally verify this new baseline (clean-room: provenance must trace to the public source; unverifiable-by-maintainer → labeled unverified, never asserted as confirmed).

**This structure ALSO inspires the DIFF TREE** (`[[project_vi_diff_philosophy]]`): the netlist model is the backbone the VI diff is built on, so the redesign is not just a text renderer — the representation must carry **stable node/net identity** (nodes by uid, nets by a stable schematic name) and a **tree shape that diffs cleanly** (added / removed / rewired parts and nets each map to one tree node). A concurrent parts+nets model with stable names diffs far better than sequential SSA assignments whose text position shifts. Design the netlist and the diff tree together, not separately.

Related: [[feedback_never_describe_vi_as_python]] (the live layer is faithful LVType, codegen is the lossy one) and the netlist is a LIVE/faithful artifact, so it must describe the VI's real dataflow. Properties section also needs terse-vs-verbose curation (terse = genuinely unusual settings like `inline`-true / non-default reentrancy; verbose-only = `inlinable`, `allow_debugging`, `priority`, the whole Window group). VHDL comparison the maintainer drew: lexically scoped, one name → one declaration, you never "find a name at any level and assume."

### "lvnet (netlist text IR) design DIRECTION decided 2026-08-27 — transient regenerated view, losslessness=graph-rehydration, verbose folds in inline interfaces/types, format renamed to lvnet. Section LAYOUT still OPEN."
<!-- was memory: project_lvnet_design_direction -->

Design direction for the **lvnet** text IR, from the 2026-08-27 design session
(builds on [[project_netlist_is_a_schematic]]). These are firm CLARIFICATIONS;
the full **section layout** (how `uses`/`types` are arranged) is deliberately
**NOT decided yet** — do not bake it into `netlist-language.md` until confirmed.

**Firm decisions / reframings:**
- **lvnet is a TRANSIENT, regenerated view — NOT persisted source.** We emit it
  fresh from the current graph each time; we don't store it and diff stored
  copies. So "stale inlined-interface copy" drift is a non-issue.
- **"Lossless" means "can REHYDRATE THE GRAPH"** (enough to drive subsequent
  commands + confidence our representation is accurate) — NOT "reproduce the
  XML/binary." The round-trip gate `reparse(emit) == model` already tests
  exactly this. Stop overloading "lossless" to mean byte-replace-the-binary.
- **`LoadMode.MINIMAL` is a FLOOR, not a coupling.** The format must capture at
  least what MINIMAL resolves (target VI logic + direct deps' connector-pane
  interfaces + referenced typedef structure = the minimum to describe+render).
  But the format defines its OWN completeness contract; do NOT couple the output
  shape to LoadMode.
- **Readability is a CO-EQUAL objective** (humans read lvnet too, not just
  agents). The inline interface/type DETAIL is what **verbose** adds — it is NOT
  a separate "header-mode" knob. So: **terse** = main VI prominent + compact
  `uses` references (get to the VI fast); **verbose** = fold in interface
  definitions + type structures. ONE axis (verbose), not verbose×header-mode.
- **Bulky type defs (e.g. a ~300-member built-in enum like `lveventtype`) go in
  COMMENT FOOTNOTES**, surfacing the USED members inline (from the structure)
  and the full set only on request. People don't author lvnet, so they don't
  need every possible member — just the ones used.
- **`uses :` = the dependency manifest (edges/relationships)** — part of
  losslessness (rehydration needs the edges). File-backed types (typedef `.ctl`,
  class `.lvclass`) MIGHT fold their structure under their `uses` entry
  (C-header model); file-less named types (built-ins) + anonymous types can't —
  **this folding vs a separate `types :` section is OPEN.**
- **Format renamed `netlist` -> `lvnet`** (we have an extension now). Apply when
  `render_lvnet` is wired into `describe`/MCP/the extension (the Phase-C CLI
  wiring), keeping `netlist` as a DEPRECATED ALIAS so the git-textconv setup
  (commit 83324fc) and user scripts don't break. (Playful future: "frog".)

**DECIDED terse/verbose split (2026-08-27):**
- **Terse** = the VI's own logic + call sites (their wiring) + a plain `uses :`
  reference list (names/paths). Does NOT rehydrate dependencies. ≈ today's terse
  output + a `uses :` manifest. Keeps the main VI prominent.
- **Verbose** = terse + enough inlined about each dependency to **rehydrate the
  MINIMAL graph** — "the same helpful information we use for RENDERING": each
  dep's interface (connector pane) + type structures. So verbose is not abstractly
  "lossless" — it is **render-rehydratable**: parse(verbose) -> reconstruct a
  MINIMAL-equivalent graph -> it renders. That is the concrete round-trip bar
  (graduate the round-trip from signature-equality to actual model reconstruction
  that re-renders). Matches what `LoadMode.MINIMAL` already loads + what the
  web-staging closure staged.

**Still OPEN (not deciding yet):** section order/layout (`uses`/`types`
placement, folded-vs-separate), `=`-alignment, exact footnote syntax.

The critique that drove this is captured too: we had drifted toward a "portable
module format with header modes" (scope creep); the de-scoped shape is ONE
`verbose` axis over a transient, graph-rehydratable lvnet.

**lvnet is a GENERATOR, not a view (2026-08-29, maintainer's framing):** "this may
as well be in the generators section :-p — python being another option and maybe
frog. We're essentially encoding the logic." lvnet is a **generator target**, a
peer of Python codegen (and a future "frog"), NOT a describe/diff view layer. Its
losslessness gate — graph-identity round-trip (`reconstruct(parse(emit)) == graph`
BY IDENTITY, node/structure uids + pane pattern/@index + net producers, not textual
signature) — is the "does this generator faithfully encode the VI's logic?"
contract. Every generator answers the same question in its own language; lvnet's
answer is checkable because it round-trips back to the graph. This reframes where it
lives architecturally (a generator alongside codegen), not just what it prints.

**Anonymous-cluster field TYPES — CLOSED 2026-08-29 via option (a).** The
maintainer chose (a): harden the inline parser to be brace-aware and reuse the
capital lossless-def grammar inline. Implemented + proven by the graph-identity
gate: an anonymous cluster now renders `Cluster{ f : <type> }` inline (anon enum
`Enum{ m = 0 }`), one renderer `_lvnet_type_inline` whose leaf/structural split
mirrors `_lv_type_comparison_shape`; the inline line parser finds its own
`=`/`default`/`@index` only at brace DEPTH 0 (`_top_level_word_index` /
`_find_top_level_sep`); `_iter_named_subtypes` descends every non-error cluster's
fields (error clusters stay the opaque `Error` token); the reconstruct self-check
(`_maybe_attach_lvtype`) mirrors `_lvnet_type_inline`, not `type_descriptor`.
Two subtle mirror-bugs the gate caught + fixed: (1) `refnum{` detection must be
guarded so a `Cluster{ … refnum{…} … }` isn't misparsed as a refnum (a brace
before ` refnum{` ⇒ it's nested); (2) an AMBIGUOUS named type (§10 flat-footnote
caveat) must compare as `("leaf", name)`, never descended — the render shows its
name, the parsed side leafs it. Named/error/refnum/array top-level labels are
byte-unchanged (§16 golden still byte-identical). Original fork writeup, for
context:

Verified gap: an anonymous cluster's inline terminal label renders field NAMES only
(`type_descriptor(expand_named=False)` → lowercase `cluster{f1, f2}`), so field
types (and any named type reachable ONLY through an anonymous-cluster field) are not
text-recoverable. The over-collection fix (committed 86c9b8f) made this HONEST —
`_iter_named_subtypes` no longer collects those unrecoverable types into the `types:`
footnote — but it's still a losslessness gap. Closing it is NOT the one-liner it
looked like: the footnote lossless-def grammar (`Enum{ m0 = 0 }` / `Cluster{ a :
DBL }`) uses bare ` = ` / ` : ` tokens, and the INLINE terminal-line parser
(`_split_node_terminal_tail`) is whitespace-tokenized + treats any bare `=` as the
driver operator — NOT brace-aware. So expanding anonymous composites inline via the
existing capital grammar would misparse (`in x : Enum{ m0 = 0 }` → type=`Enum{ m0`,
driver=`0 }`). Two encoding options, both real grammar decisions for the maintainer:
(a) HARDEN the inline line parser to be brace-aware (ignore `=`/`default`/`@index`
inside `{}`/`[]`), then reuse the capital lossless-def grammar inline — consistent,
fully structural, but changes the inline-type contract the whole identity gate rests
on; (b) give each anonymous cluster a synthetic footnote handle — but §10 reserves
the footnote for NAMED types and anonymous types have no stable cross-occurrence
name, so this invents new grammar. Pure-scalar anonymous clusters happen to survive
as opaque strings today but gain nothing structural. DO NOT pick silently.

### "PARKED refactor — the \"correct\" module decomposition (organize by artifact-producer; lift engines out of graph/; split god-modules by responsibility). Design direction, not yet executed."
<!-- was memory: project_module_decomposition -->

**⛔ 2026-08-29 UPDATE — the refactor is now scoped + designed (refactor pass "C").
Actionable design lives at `docs/_internal/design/god-module-decomposition.md`
(committed 9812cac on branch feat/netlist-from-graph / PR #70).** The big NEW
insight beyond "split by responsibility": there is a **shared graph-generator
abstraction** to extract. Verified: `Operation` (models.py) is a LOSSY dataflow-
ordered VIEW projected from the source-of-truth `GraphNode` graph (drops local
vars/labels — that's why lvnet walks GraphNode directly, for losslessness).
codegen/render/lvnet each hand-roll the SAME per-type dispatch skeleton ~5×.
Target: ONE `GraphNode` model + shared graph-helper services (traversal/identity/
terminals/optional-ordering) on a base **context** + a shared dispatch/recursion/
UNSUPPORTED skeleton + per-generator context extensions (geometry for render/FROG,
symbols for codegen, nets for lvnet) + per-generator **handler tables**
`{NodeType→handler}` — FORWARD for export-only formats (codegen/render/describe/
diff/FROG), plus a REVERSE table for lossless round-trip formats (lvnet + json).
Handler return type is per-generator and may be None (accumulate via ctx, as
lvnet-render/visual-render already do). `Operation` demoted to opt-in ordering.
**Parallelism via a REGISTRY** (handlers self-register, no central switch):
Wave 1 (Category-B mechanical splits: cli/parser/construction/loading/…) runs
CONCURRENTLY with the serial C0 core; Wave 2 = one worktree PER NODE TYPE,
conflict-free. codegen/nodes/ is already the reference shape. Do C only AFTER
PR #70 merges (this branch touches cli/parser/mcp — refactoring off main before
merge = conflicts). The detail below is the ORIGINAL artifact-producer framing,
still valid, subsumed by the design doc.

Parked design direction for lvkit's module structure, derived with the maintainer
2026-08 while adding the MCP render/diff surface. **The full, grounded, actionable
plan lives in the repo at `MODULE_DECOMPOSITION.md`** (target tree + per-god-module
internal splits with symbol/line anchors + migration order); this note is the
durable summary. **Not executed** — the MCP
surface work (drop `describe`, `render`/`diff` return paths not blobs, `read_vi`
augment) shipped first at low cost; return to THIS afterward. Goal the maintainer
stated: **separation of concerns, kill god-modules, reduce circular deps, easier
testing, easier change over time.**

**Organizing principle: modules are ARTIFACT-PRODUCERS on the shared graph, named
by what they PRODUCE.** Everything is `graph → X`, so "analyzes over the graph"
categorizes nothing — the graph is the FOUNDATION, not a differentiator (same
empty-category trap as naming a layer "core": if a label includes everything it's
useless). Keep ONE boundary visible: **pure producers** (`graph → artifact`) vs
**impure surface-glue** (`path → load → dispatch → body`). A capability carries
only the formats its result can meaningfully BE (docs=site, netlist=data,
render=svg[+html wrap], diff=prose/data/visual) — reject a format-matrix.

**The big move — `graph/` has absorbed its own consumers.** `netlist`,
`describe`, `diff` are engines that USE the graph to produce distinct artifacts
(IR, prose, change-set) — the SAME category as `render` (→svg) and `codegen`
(→python), which correctly live OUTSIDE `graph/`. They're mis-filed inside it.
Lift them out to be siblings of render/codegen. `graph/` keeps ONLY the graph
proper: the structure + construction/loading + the query API (`QueryMixin` =
"ask the graph about itself") + node models/op-walk. The import shape reveals a
clean layered DAG currently flattened into one folder: `graph ← netlist ←
{describe, diff}` (describe & diff both build ON netlist). Make that DAG
cross-module + one-directional → cycles become impossible to write. (Leak to fix:
engines reach `graph.core._uid_of`, a private helper.)

**Viewers → ONE shared `viewer/` folder, NOT per-feature.** `render_viewer` and
`diff_viewer` BOTH import the same kit (`theme_control`, `help_tip`,
`properties_panel`, `connector_pane_panel`) + the renderer's SVGs — verified. So
by-feature (`diff/viewer`, `render/viewer`) would force a shared-kit folder
anyway (= `viewer/`) plus re-introduce the "diff reaches into render" coupling.
`diff` DISSOLVES by artifact: engine → `diff/` (or graph-engine sibling), viewer
→ `viewer/diff_viewer`, page-orchestrator → `viewer/diff_page`. So `diff_vi_files`
(currently the loose top-level `vi_diff.py`, its parked home) belongs as
`viewer/diff_page`; its text/json are direct `graph.diff` calls. Symmetric
`render_page` lives beside it. No `core/` package, no loose orchestrator file.

**`docs/` stays its own producer** — its output (a multi-page linked SITE with
breadcrumb/index) is unique; nothing else makes it. It shares NOTHING with the
viewers today except the raw SVG renderer (own `template.css`, own page shells,
own click-to-navigate). The only real DRY gaps: docs should source its palette
from `theme_web` (the one cross-cutting primitive — viewers, docs, sampler,
cloudrun), and could later reuse the connector-pane/properties PANELS. The shared
"html kit" = **tokens + panels, NOT a generator.** Don't merge the assemblers.

**Two kinds of split — don't confuse them.** LIFT OUT (→ new top-level package):
ONLY engines mis-filed in `graph/` that make a distinct artifact — `netlist/`,
`describe/`, `diff/`. DECOMPOSE IN PLACE (→ subpackage of the current parent): a
god-module that's correctly located but too big — `render/glyph/`, `render/draw/`,
`render/scene/`, `render/nodes/`, `cli/`. **glyph is SUBORDINATE to render**
(a graph→SVG pipeline stage), never a top-level sibling.

**God-modules split INTERNALLY by responsibility/family, not one dispatch file.**
The maintainer's litmus (their example): `glyph.py` should not hold every typed
glyph in one file — split by glyph FAMILY (numeric/boolean/array/cluster/
bundle/property-invoke/constant/…), each a module. Likewise `diff.py` → engine /
text-format / property-diff / frame-diff / netlist-diff; `netlist.py` → build /
serialize / components; `cli.py` → per-command; `draw.py`/`scene.py` by drawing
concern. (Re-read current sizes/seams from source when resuming — do NOT trust
any number written here; god-modules were the largest files in `src/lvkit`.)

**Rejected:** a `core/` package (empty name — graph/parser ARE the core); naming
glue like engines (it's the opposite of an engine); per-feature viewers; a
format-matrix; a combined html generator producing both viewers and docs.

See [[project_mcp_understanding_surface]] (the MCP surface this rode in on) and
[[feedback_kiss]] / [[feedback_graph_not_dicts]].

### "#26 connector-pane VIEW plan — conId is the pattern id (base ~4800); grid geometry NOT persisted, encode a compact conId→cell-arrangement table; render cells from the slots we already parse"
<!-- was memory: project_connector_pane_view -->

**#26 = the VISUAL connector-pane view** (the DIFF half is DONE: `kind="connector_pane"` changes, grouped under a `Connector pane:` folder, tested). Branch `connector-pane-view` (off main). User decided: **faithful LV pattern grid**, used in BOTH a standalone single-VI view AND the diff (before/after panes, changed cells ringed).

**What we HAVE** (per-cell content + placement rule): `ParsedConnectorPane` = `conId` + `slots` (`ParsedConnectorPaneSlot`: `index`, `fp_dco_uid`, `is_output`, `wiring_rule`, `type_id`). Input/output per terminal comes from wire-direction analysis (`parser/front_panel.py`). Faithful `LVType` per slot via the resolved terminal. So each cell's label (connected control name), color (type), side (in/out), and wiring rule are all in hand.

**What is NOT in the data — the grid CELL GEOMETRY.** Verified 3 coordinate spaces: `conPane` XML = `<conId>` + `<cons>` (connection uids) with NO rects; FP controls have `bounds` but that's PANEL layout (and the connection uids don't even key to them); BD terminal nodes are scattered CANVAS positions (user-placed), unrelated to the icon grid. LabVIEW stores the grid ONLY as `conId` and redraws it. (#38's "termBounds R→L,B→T" is about PRIMITIVE/subVI-ICON terminal ordering, NOT the VI's own pane — do not conflate.)

**The key finding:** `conId` == the LabVIEW connector-pane PATTERN NUMBER (the scripting U32). Corpus values cluster ~4800-4834, ordered by terminal count (4800→1, 4801→2, 4803→3, 4805→4, 4807→5, 4810→6, 4815→12, 4834→20). **conId=4815 (the 4-2-2-4, 12-terminal) is ~78% of VIs**; only ~15 distinct patterns in the whole corpus. So the "geometry table" is a compact `conId → cell-arrangement` map (each entry: which (row,col) each slot `index` occupies), NOT a hard reconstruction.

**PHASE 1 DONE (commits a282da5 + 00eb4d2, branch connector-pane-view):** the JSON reference + loader shipped, ALL 36 patterns.
- `src/lvkit/data/connector_pane_patterns.json` — per conId: name, terminal_count, corpus_files, and cell grid. Two layout forms: `columns` (list of columns, each a top→bottom index list; equal-width cols / equal-height cells-per-col — 18 regular patterns) OR CSS-Grid-style `grid`{cols,rows}+`cells`[{index,col,row,colspan,rowspan}] (18 irregular patterns with column-spanning cells, e.g. 4820 wide middle row). **Encodes the COMPLETE set conId 4800-4835 = 36 patterns** (not just corpus-common; a wild VI can use any). corpus_files = real usage across 4569 FPHb (0 = valid but unused locally; 4815=76%).
- `src/lvkit/render/connector_pane_geometry.py` — `get_pattern(conId)->PanePattern|None`, `PaneCell(index,x,y,w,h)` normalized rects (y down). PURE geometry; in/out comes from the VI's own `ParsedConnectorPaneSlot.is_output`, NOT the pattern. `tests/test_connector_pane_patterns.py` (58 cases): indices 0..N-1 contiguous, cells partition unit pane no-overlap, counts match.

**GEOMETRY SOURCE (corrected):** the grid is NOT in the VI (LabVIEW stores only conId, redraws) and NOT derivable from corpus geometry (iUse termBounds = icon rect, not per-cell). It came from the **public LabVIEW-Wiki pattern images** `labviewwiki.org/w/images/<md5>/…/Connector_Pane_Pattern_<conId>.png` (128×128; compute md5 path yourself, don't trust a fetch model's hash) — clean-room source #2. Each image LABELS every cell with its real terminal index. Irregular grids extracted by PIXEL DETECTION (connected-components → cell rects → CSS-grid col/row via `.tmp/detect_cells.py`+`infer_grid.py`); indices bound from reads then VERIFIED by overlaying encoded indices back onto each wiki image (`.tmp/overlay_verify.py` — every red number must sit on the matching printed digit; caught a mis-read on 4827). Rebuild via `.tmp/build_patterns.py`. **KEY: index origin/direction DIFFERS PER PATTERN** (4815/4810/4812 = idx0 bottom-right, R→L,B→T; 4820/4834 = idx0 top-left; 4833 = border interleave) — encoded as data, never a rule. maxidx+1 in FPHb conPane is the highest CONNECTED index (not capacity); true capacity = image cell count.

**PHASE 2 DONE (commits 46838da + 67afece):** the pane SVG renderer, standalone + diff.
- `src/lvkit/render/connector_pane.py` — `render_connector_pane(pattern_id, terminals, ring=)` → `<svg>`; `render_connector_pane_diff(pid_a, before, pid_b, after)` → side-by-side before/after with changed cells ringed (red `coercion_dot`); `pane_terminals(Terminal…)` adapter (no graph dep in render). Cells filled with a tint of the LVType wire color + full-height accent bar on wire-entry edge (left=input/right=output), name+type label, border weight by wiring_rule, empty slots faint-dashed. Unknown conId → input/output column fallback.
- Plumbed `pattern_id`: `VINode.connector_pattern_id` (from `conpane.pattern_id` in construction) → `VIContext.connector_pattern_id`. `PanePattern` now exposes `cols`/`rows`.
- `tests/test_connector_pane_render.py` (11). VISUALLY VERIFIED on TestCase passIfEqual (rich 4-2-2-4: error in/out bottom corners, class refs top corners, x/y/delta Variant, report String) + diff passIfEqual↔run (added/removed rings correct). Full suite 1777 green.

**PHASE 3 LARGELY DONE (commits 98c198d + 353c24d, branch connector-pane-view):** self-contained Context-Help aside.
- `render/connector_pane.py::render_connector_pane_help(pattern_id, terminals, *, title, description, icon_uri, ring=?, theme)` — LabVIEW Context-Help panel: icon+title+wrapped description + the compact colored pane grid, each terminal's type+name on a color-matched ORTHOGONAL leader. Routing: each side split OUTER col (straight leaders, labels aligned) vs INNER middle col (stack above/below the outer block, route up-over/down-under → NO crossings). Labels positioned with explicit x (NEVER text-anchor="end" — cairosvg mis-renders it). `_tw()` width estimate 0.6em/char.
- `render/__init__.py::_vi_aside_svg` embeds the help panel in `<defs>` (STRUCTURALLY non-rendered — survives JS-strip AND sanitization, unlike inline display:none; raw raster leaks nothing) pinned top-right via transform. Also `data-lv-connector-pane` JSON (resolved cells) on root for programmatic hosts. Scene.icon_png carries the raster. VIContext.connector_pattern_id wired in get_vi_context (was computed-but-not-passed bug).
- `render/connector_pane_panel.py` = ▦ button + JS that CLONES the defs aside into the visible tree (toggles ALL asides → one button covers diff's 2 panes). Wired into render_viewer.py/.html (button 6px gap matching fit/theme).
- Proof: `outputs/render_viewer_pane.html` (click ▦). 320 render tests pass.

**PHASE 3 REMAINING:** (1) DIFF viewer wiring (same ▦ button/script — diff_viewer.py/.html), (2) help panel RING param to highlight terminals whose CONNECTIONS changed (added/removed/retyped) — user: "this panel will highlight differences in connections", (3) tests for aside/help + panel chrome, commit. Then push.

**PHASE 3 DESIGN (user-driven):** the SELF-CONTAINED-SVG principle. Everything (diagram + icon + connector pane + properties) is DRAWN INTO the SVG and self-presents standalone; the viewer is a generic shell with NOTHING VI-specific unless it chooses to PROMOTE (clone an in-SVG group into HTML chrome). Chosen pattern = the TOOLTIP one, NOT the properties-JSON-data-attr one: `draw_help_overlay` (draw.py) draws hidden `<g class="lv-help">` panels revealed by the SVG's own `_HOVER_PANEL_JS` (class toggle). Three carriers clarified: (1) properties #19 = JSON on `data-lv-properties` → viewer BUILDS DOM (data-only, NOT self-presenting outside a viewer — the gap the user flagged); (2) tooltips = rendered panel drawn IN, self-revealing (standalone). Decision (option B): draw BOTH pane AND properties INTO the svg as hidden self-presenting groups; viewer later PROMOTES by cloning (not rebuilding) → one renderer, no drift. Layout: **top-LEFT = VI icon (raster _ICON.png) as the handle → reveals the connector pane**; **top-RIGHT = "▤" handle → reveals a drawn-SVG properties panel** (mirrors existing top-right props button). Keep JSON side-channels (`data-lv-properties/health` + NEW `data-lv-connector-pane`) for programmatic hosts (VSCode ext). "unless promoted by viewer" = standalone reveals in-place; viewer clones to overlay + suppresses in-SVG handles. FOLLOW-UP: retrofit #19 properties to self-present too (currently data-only outside viewer). Implementation delegated to Sonnet (self-contained increment, NO viewer changes yet); promotion is a later task. See [[reference_connector_pane_terminal_index]], [[feedback_verify_render_against_reference]], [[feedback_delegate_implementation]], [[feedback_icon_means_raster]].

### "VI-diff design law — wires are variables, containers are scaffolding; show only structural/logical change, collapse re-index/re-key/enclosure noise"
<!-- was memory: project_vi_diff_philosophy -->

The VI-diff (graph/diff.py `diff_uid` → `ChangeMap`, viewer in .tmp/build_vi_diff_viewer.py) exists to **help a reviewer understand a change as fast as possible**. Treat a VI like code; show only STRUCTURAL + LOGICAL change, hide everything that a good text diff would hide.

**Governing principles (from the user, hard-won over a long session):**
- **A wire is a variable.** It carries a value producer→consumer. Wrapping that in a case/loop does NOT delete it — it still flows, just guarded. "Wire moved into a conditional" is **not** a removal.
- **A container by itself is scaffolding, not logic.** Adding/removing a case/loop that only *wraps* existing code (no new branch behaviour) ≈ adding `{}` / `if(true)` in text = essentially no-change.
- **A REAL wire change = a consumer now reads from a DIFFERENT producer** (variable swap, `foo(a)`→`foo(b)`). e.g. run.vi 92be264→3fb850d: `Not`'s input moved from unbundle 1489 (field X) to a new unbundle 1065 (field Y) — that IS logical.
- **Never describe a change twice.** A tunnel exists *because* its container was added → it's a consequence, "already described" by the container change, not a separate wire change.
- **Never fake the abstraction:** a node with only a changed WIRE is not a node "modified" — that's a wiring change, and we model at the node level (wire-level is #10). Don't invent categories users won't want ("no user will EVER want to see 'rekeyed'/'enclosed' as a change type").

**What's DONE (committed 5f5fded):** node-level `diff_uid` — `added`/`removed` only. UID-set leftovers matched by kind-anchored dataflow (`_match_elements`: neighbour anchored by common/matched UID or kind + neighbour terminal, never raw self-UID). exact match (identical wiring)=re-keyed→collapse; fuzzy (Jaccard≥.5, wiring partly differs)=same node→collapse (the diff is a wire, not a node). Enclosed/moved/re-keyed → unchanged. run.vi: noisy 6add/2rem → clean **4 added** (new case + new skip nodes).

**VI-LEVEL changes in the diff (2026-08, locked with user):** the diff is for AUTHORED changes. Model VI-level changes as ordinary `ElementChange`s (same object, new `kind` values — NOT a bespoke `{field,old,new}` array; that was rejected). Trichotomy:
- **`property`** (reentrancy/priority/lock/…) — an authored SETTING → first-class change-kind, glyph `▤`, tree parent "Properties".
- **`connector_pane`** (was misnamed "signature" — JARGON; use the LabVIEW term) — the authored interface, terminals added/removed/retyped → first-class change-kind, glyph `▭` (terminal glyph), tree parent "Connector pane". Same concept as task #26.
- **`health`** (is_broken / bad_*) — an EMERGENT CHARACTERISTIC, "almost like file size" → **OMITTED from the diff entirely.** Not a change-kind. The diff shows the CAUSE (deleted subVI, retyped wire); brokenness is downstream, read off the VI via describe/index. (Health still lives in describe `## Health` + the index `health_*` columns — just not the diff.)
Arrow convention: `detail` holds unicode `→` internally (via `_transition`); TEXT maps to ASCII `->` via `_ascii_arrows` (netlist is ASCII-locked); JSON keeps the raw `→` like every other modified change. Before/after (NOT old/new) is the settled vocabulary (#38, commit b2bec00). Property/connector-pane rows must be selectable in BOTH the Flat list AND the Tree (key on synthetic uid). See [[feedback_never_describe_vi_as_python]], [[feedback_doc_review_accuracy]].

**Open — the hard part = #10 wire-level diff, containerization-invariant.** Validated approach: EFFECTIVE-DATAFLOW contraction — follow wires THROUGH structure terminals (`get_wires(include_internal=True)` exposes outer↔inner tunnel bridges) to the real node; anchor endpoints by the node matcher's canonical ids; diff those. Prototype cut tunnel noise 13→2. BUT the naive BFS has artifacts (fan-in tunnels `3925→3927`,`3926→3927` share an inner terminal; per-frame routing) — e.g. `819→493` "added" is really 819's value flowing THROUGH the new case (tied to the case add, not an independent rewire). Must be artifact-free (correct fan-in/out + per-frame tunnel routing) before shipping. See [[project_wire_routing_rearchitecture]] for the renderer's separate wire work.

### "GitHub-Action diff runs on the whole repo at two commits → path-identity (fixes qualified-name #9 leak), transitive/cross-file diff (subVI + typedef ripple), impact analysis. Per-VI diff stays the atom."
<!-- was memory: project_diff_project_context -->

Running the VI-diff as a **GitHub Action** (or any in-repo context) reframes it from "compare two `.vi` blobs" to **"compare two project states"** — the whole dependency closure present at base and head commits. This meaningfully improves diffs, reshaping the #109/#110 Action scope. Three levers:

1. **Path identity kills the #9 leak.** The one real bug in the wire-diff pressure test — a VI's connector-pane/self node identified by an *embedded qualified-name string* that flips `…lvlib:Foo.vi` → `Foo.vi` on library requalification, flooding phantom rewires — vanishes when a VI is identified by its **repo path** (+ git rename detection). The hand-coded "canonicalize the self-node name" fix *is* project context.
2. **Transitive / cross-file diff (the genuinely new capability).** A VI's own diagram can be byte-identical yet behavior-changed because a **subVI it calls** changed (today `expand_subvis=False` → opaque box) or a **typedef/`.ctl`** it consumes changed (a cluster field rename ripples into every downstream bundle/unbundle). With the closure, *attribute* those to root cause — same principle as the node/containment sieves ([[project_vi_diff_philosophy]]), lifted project-wide. Impossible on two isolated files.
3. **Impact analysis + PR hunk model.** Git says which `.vi/.ctl/.lvclass` changed; the dependency graph says which *unchanged* VIs are downstream. Render changed files + flag "N VIs consume this typedef; M have logic affected." Also solves "don't render 2000 VIs." Plus class/library structural diffs (method add/remove/override, dynamic dispatch) that need the whole class folder.

**Why:** the user's insight — "a project's worth of files to work with." Isolated-file diffing has a hard ceiling (ambiguous identity, no ripple, no impact); the repo context lifts it.

**How to apply:** per-VI wire/node diff ([[project_vi_diff_philosophy]], #10) stays the **atom**; project-awareness is the **orchestration layer** on top (path identity → transitive attribution → impact), NOT an engine rewrite. `pipeline.py` already does multi-VI load ordering + dependency resolution, so "diff two project states" is mostly orchestration over existing capabilities. Build order: per-VI wire diff first (the unit everything composes from), then layer project context as the Action takes shape.

### "CORRECTED — MCP should expose the mechanical/deterministic CAPABILITIES (render, diff, callers/callees/blast-radius, facet queries); the AI adds the semantic layer on top. The old \"minimal understanding-only, AI composes primitives\" thesis was wrong — see below."
<!-- was memory: project_mcp_understanding_surface -->

## ⚠️ This note was WRONG and is corrected. (see [[feedback_dont_defend_fossilized_decisions]])

The old thesis — "the lvkit MCP is a **minimal, understanding-only** surface;
expose NO artifact generators and NO shortcut tools because *the AI composes the
primitives*" — inverted the actual division of labor and caused real failures. In
a live Claude Desktop test, with no `render` tool, Claude **denied lvkit could
render** (false — `lvkit render` → block-diagram SVG, `render/__init__.py`) and
told the maintainer to **open it in LabVIEW** — a clean-room violation
([[feedback_no_labview_clean_room]]). "Show me a VI" also burned ~2 min
hand-building HTML out of `read_vi`'s netlist IR instead of just rendering. The
note even claimed "Claude Desktop drops `EmbeddedResource`" — the test showed
Desktop **does** consume file/artifact outputs.

## The corrected principle: tools do the mechanical work; the AI does judgment

An AI's value is **semantic — summary, interpretation, insight, judgment**. So:

- **Expose a missing CAPABILITY as a tool** — something the AI cannot produce at
  all and that no existing tool covers:
  - `render` (block-diagram SVG — the AI CANNOT reconstruct LV geometry; only
    lvkit can; it's the headline capability, and withholding it caused the
    clean-room violation),
  - visual/structured `diff` (two-version compare — lvkit-only).
  These are NOT reducible to `query` — pixels and a version compare aren't rows.
- **Do NOT add a tool for what is "just a query."** `callers`/`callees`/
  `blast-radius` are `query` invocations over the already-exposed graph: the
  `node` view carries `callee_path` (one-hop callers/callees), `vi` has
  precomputed `callers_count`/`impact_score`, and blast-radius is a
  `WITH RECURSIVE`. The capability (query the graph) is already there — the way
  to make these RELIABLE is GUIDANCE / discoverable query templates, not new
  tools. (Adding tools for canned queries is the over-correction; withholding
  render was the under-correction. The test is: is it a missing capability, or a
  query over an exposed one?)
- **Reserve the AI for the semantic layer**: relay `describe`'s prose, summarize
  what a VI does, judge/interpret a diff, author idiomatic Python from the IR,
  write the right `query`. Do NOT make it hand-render pixels it can't produce.
- **Clean-room guardrail must live where an MCP-only client sees it** (tool
  descriptions and/or an MCP prompt): *lvkit renders and reads `.vi` files WITHOUT
  LabVIEW; never suggest opening LabVIEW or screenshotting it.* Reason: the
  `.mcpb` path carries NO skills (they're inert PyInstaller data), so a
  `.mcpb`-only install has just the server. Put the guardrail in the tool
  descriptions so it's self-sufficient regardless of client.
- **BUT Desktop is NOT skills-less.** Claude Desktop supports plugins + plugin
  **marketplaces**, and the `.claude-plugin/marketplace.json` format is
  explicitly *shared with Claude Code* (Anthropic docs,
  claude.com/docs/third-party/claude-desktop/extensions). lvkit already ships that
  marketplace with per-platform plugins that bundle skills — so installing the
  lvkit PLUGIN in Desktop DOES deliver skills; only the `.mcpb` path doesn't.
  Two Desktop install paths: `.mcpb` (server, no skills) vs plugin/marketplace
  (skills, and — TO VERIFY — whether it also runs our LOCAL binary MCP server, or
  whether plugin-delivered servers are http/sse only so the local server still
  needs the `.mcpb`).

`generate` (deterministic Python AST) is the one genuine judgment-adjacent case
(the AI authors idiomatic code from the IR) — but it's ALSO a deterministic
oracle, so exposing it as a callable reference the AI improves on is consistent
with this principle, not against it. Re-evaluate, don't reflexively exclude.

## Still-true architecture invariants (unchanged)

- Both surfaces call ONE shared library core **in-process**. The MCP must NEVER
  subprocess a `scripts/` file — the wheel ships only `src/lvkit` (scripts/ is
  sdist-only), so a scripts-sheller breaks on `pip install`.
- The call graph is a SLICE of the `node` view (`kind='vi'` + resolved
  `callee_path`), not a separate `calls` table.
- `read_vi` is the netlist read; `lvkit describe --format json` is its CLI twin.
- Skills teach a Claude Code agent the CLI exists (no auto-discovery). See
  [[project_mcp_positioning]], [[feedback_never_describe_vi_as_python]].

## To verify when building the render/diff tools
Exact MCP return shape for an SVG (inline image vs `EmbeddedResource` vs
artifact) and size behavior in Claude Desktop — verify empirically, don't assume
(the last assumption here was wrong).

### MCP server defaults project/VI path to the client workspace root (roots); uv --project ≠ MCP project; server is multi-project path-keyed
<!-- was memory: project_mcp_roots_defaulting -->

MCP server (`src/lvkit/mcp/server.py`) now reads the client's advertised
**roots** so a user who opened their VI repo never retypes the path
(commit 7f506e0, branch mcp-improvements). Helpers `_resolve_project` /
`_resolve_target` / `_client_roots` / `_uri_to_path`:

- All 11 project-scoped tools: `project` is OPTIONAL → first client root → cwd.
- 7 single-VI tools + 2 generators: VI/library path may be **relative to** a
  client root (`describe("Classes/TestCase/run.vi")`).
- `ctx: Context | None` injected per tool; FastMCP hides it from the schema.
- Reorder gotcha: `find_type_usages`/`get_callers`/`get_callees`/`blast_radius`
  now take the required VI/type arg FIRST (optional `project` can't precede a
  required positional). MCP calls by name so clients are fine; call by keyword
  in tests. Tests: tests/test_mcp_roots.py.

**Two different "project" — the confusion to preempt:**
- `uv --project /home/ryanf/repos/lvkit run lvkit mcp` — **uv's** flag, picks
  which lvkit *code checkout* runs as the server. One-time launch config.
- MCP **`project` tool arg** — which VI repo a *question* is about. Now defaults
  to the workspace root; you don't pass it per question.

**Multi-project, already:** `_indexes: {resolved_root → facts}` is path-keyed,
so ONE running server serves any number of repos at once (each question's
`project`/root selects its cached index). Never limited to one; roots just
fills the path when omitted. See [[project_mcp_demo_questions]].
