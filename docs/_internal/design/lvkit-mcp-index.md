# lvkit MCP: a persistent project index ("tree-sitter for VIs")

Supersedes and extends `lvkit-mcp-improvements.md` (which stays valid as the
list of concrete bugs). That doc came from one real task — *count the names this
project uses for error indicators, across VI Tester's 487 VIs* — that was
impossible through the MCP as shipped. This doc turns those findings into an
architecture: the MCP should **index a VI repo into a persistent, incrementally
maintained graph and let an agent interrogate it like code** — the tree-sitter /
LSP model, applied to VIs.

Every claim below is grounded in a read of the current code; file:line cited.

---

## 1. Prior art (and the one lesson each contributes)

- **codebase-memory-mcp** (DeusData) — MCP server, indexes a repo into a
  persistent **SQLite** knowledge graph, "avg repo in ms," sub-ms queries, ~99%
  fewer tokens. *Lesson:* SQLite+WAL on disk, FTS for name search, git-based
  change detection (re-index only changed files), tools split index / query.
- **Serena** — LSP-over-MCP, symbol-level navigation (`find_symbol`,
  `find_referencing_symbols`, `get_symbols_overview`). *Lesson:* return
  **symbols, not files** — token efficiency is a granularity choice.
- **GitHub stack-graphs** — tree-sitter-based, **file-incremental** name
  resolution at scale: each file → a disjoint subgraph built in isolation;
  cross-file edges resolved by merging at query time; change one file →
  recompute only its subgraph. *Lesson:* the incremental model that makes
  "index a huge repo instantly + incrementally" real.
- **SCIP** (Sourcegraph) — replaced LSIF using **human-readable, stable string
  symbol IDs** instead of opaque per-run ints. *Lesson:* stable symbol identity
  is what makes incremental + cross-file possible — and it's the fix for lvkit's
  silent name-collision bug (§4 of the old doc).
- **salsa** (rust-analyzer) — memoized query DAG; change an input, only
  dependent queries recompute. *Lesson:* the compute model for incrementality.

**lvkit already fits the stack-graphs model.** A `.vi` *is* the "file." lvkit
already: extracts each VI's XML into a **content-hash-keyed cache**
(`extractor.extract_vi_xml`, gated by `cache_paths.meta_fresh`); builds each
VI's subgraph then resolves cross-VI SubVI/type refs (`_dep_graph`); and has a
per-user cache home ready to hold an index. What's missing is **serialization +
a query surface** — not new analysis.

---

## 2. What already exists (reuse) vs. what's new (build)

Grounded in `graph/queries.py`, `graph/core.py`, `graph/loading.py`,
`models.py`.

**Reuse (already computed, just trapped in a live in-memory object):**

- Forward + reverse call links: `get_vi_dependencies` / `get_vi_dependents`
  (`queries.py:327,333`). *Caveat:* they return raw `_dep_graph`
  successors/predecessors, which **mix call edges with `rel="owns"`
  containment** (`loading.py:283,414`).
- Class hierarchy & membership — the most complete cross-VI subsystem:
  `list_classes`, `get_owning_class`, `get_class_hierarchy`,
  `get_method_access`, `get_method_overrides`, `get_class_fields`
  (`queries.py:955-1100`, `core.py:295`).
- Dependency/load ordering: `get_conversion_order` / `get_generation_order`
  (`queries.py:353,383`). Polymorphic groups: `get_polymorphic_groups`.
- Cross-VI discovery scans: `get_all_constants` / `get_all_primitives` /
  `get_all_clusters` (`queries.py:106,133,161`).
- Lightweight per-VI signatures: `get_inputs` / `get_outputs`
  (`queries.py:525,549`) — terminal lists **without** the heavy `get_vi_context`
  (`queries.py:850`, which also builds operations+wires).
- **Error-cluster classification, already battle-tested:**
  `Terminal.is_error_cluster` + `_is_error_cluster` (`models.py:164,96`) —
  structural (`{status,code,source}` fields, or typedef name contains "error").
- Per-terminal wire tracing: `outgoing_edges` / `incoming_edges`
  (`core.py:410,390`), `get_terminal` (`queries.py:830`), and
  `FPTerminal.is_indicator` (`models.py:254`).
- Whole-project load in one call: `pipeline.py:406-425` dispatch
  (`load_lvclass`/`load_lvlib`/`load_llb`/`load_directory`/`load_vi`).
- Incremental primitive: `meta_fresh` / `write_meta` / `classify(vi, kind)`
  (`cache_paths.py:177,214,128`) — sha256 + version-aware invalidation, per-VI
  cache namespace keyed by project root (`_project_root_for`, `cache_paths.py:104`).

**Build (does not exist today):**

1. **Persistence + a query surface.** Nothing serializes the graph;
   `_dep_graph`/`_graph` are rebuilt in-memory every run and discarded (verified:
   no pickle / json-of-graph anywhere). This is the core new work.
2. **Pure call edges.** No `get_callers`/`get_callees` that filters `rel="owns"`.
   ~5 lines over `_dep_graph.in_edges(x, data=True)`.
3. **Reverse type-usage index.** "Which VIs use class/typedef T" has no backing
   edge — only on-demand name resolution (`core.py:330`). Must be recorded at
   index time from each VI's `type_map`.
4. **Stable symbol identity.** `_vi_nodes`, `_source_paths`, `list_vis()` are
   keyed by **bare VI name** (`queries.py:204`, `core.py:166`), so same-named
   loose VIs silently overwrite (`setUp.vi`×17 etc. → 487 requested, 422 kept,
   zero errors). Loose VIs get bare names; class/lib members get qualified names
   (`loading.py:594-595`).

---

## 2a. Measured baseline (JKI VI Tester, 487 VIs, extraction cache warm)

Timed `InMemoryVIGraph().load_directory(repo, MINIMAL)` — i.e. exactly what an
index build does — then a warm query:

| Metric | Value |
|---|---|
| **Graph build (MINIMAL)** | **173.78 s** (~2.9 min) |
| **Warm `get_callers` query** (×1000 avg) | **~0.000 ms** (sub-µs) |
| dep-graph nodes / edges | 635 / 1,116 |
| dataflow nodes / edges | 6,724 / 26,565 |
| VIs in `list_vis()` | **422** (not 487) |

Takeaways that anchor the whole design:

- **~174 s → ~0 ms** is the value prop, measured. The 174 s is the *residual*
  the extraction cache does nothing for (parse + construct + resolve). Ten
  follow-on questions through the stateless CLI ≈ **29 min** of reload; the index
  pays it once (incremental after), each query a graph lookup.
- **422, not 487** — the collision bug (§2 #4) reproduced live on the demo repo:
  65 same-named loose VIs silently overwrote, zero errors. On this exact demo,
  "count error indicators" is **wrong by 65 VIs** without path-keying.
- **635 symbol nodes** → one SQLite `index.db` (635 rows); per-VI JSON is
  unwarranted. **6,724 dataflow nodes / 26,565 edges** → never persist per graph
  node; the deep dataflow tier stays on-demand.

---

## 3. Architecture: a two-tier index

### Tier 1 — Facts projection (persisted, incremental) — the tree-sitter layer

A per-project index of **resolved facts as strings** (not the raw graph — which
mixes Pydantic nodes, frozen `WireEnd` edges, and dataclass `LVType`/
`ClusterField`, and is a serialization slog). Per VI, keyed by **resolved path**
(unique — fixes §4), carrying bare name as a secondary, ambiguity-reporting
index:

- **symbol:** path, bare name, qualified_name, owning library/class, is_stub.
- **terminals:** name, direction, `is_indicator`, `is_public`, `control_type`,
  python type, cluster **field names**, **`is_error_cluster` (precomputed)**,
  `fp_dco_uid`.
- **constants:** value, label, type, and **wired-target kind**
  (indicator/control/other) — precomputed via `outgoing_edges`.
- **call edges:** callees (from the caller's own
  `metadata.subvi_qualified_names` — caller-intrinsic), reverse derived.
- **type uses:** classnames / typedef_names referenced (from `type_map`) — the
  reverse type-usage index.
- **class facts:** parent_class, owned methods + scope (from `_dep_graph` class
  nodes/edges).

**Why this is soundly per-VI incremental** despite cross-VI type propagation:
`_load_vi_recursive` builds a VI's graph *after* its callees so types /
dispatch-names / nmux-lane display-names propagate (`loading.py:709-715`,
`resolve_dispatch_qnames`, `stamp_nmux_lane_names`). But every *fact* above is
intrinsic to a VI's **own** bytes (own FP terminals, own constants, own
`subvi_qualified_names`, own `type_map`). Cross-VI propagation only refines
*display* names, which the facts layer doesn't depend on. So a VI's facts are a
pure function of its content hash → `meta_fresh` is a correct staleness gate.

**Storage.** Per-VI facts as JSON at `classify(vi, "index")` +
`write_meta(..., index_schema=N, lvkit_version=...)` — the incremental unit,
content-addressed exactly like extraction. A **project rollup** for cross-VI /
aggregate / FTS queries at `~/.lvkit/cache/index/projects/<slug>/index.db`
(SQLite+WAL, FTS5 on names) — validated by codebase-memory-mcp; start with a
JSON manifest and add SQLite when the corpus demands it. The rollup is rebuilt
from the per-VI JSON (cheap; no re-parse).

### Tier 2 — Deep graph (on-demand, single VI)

Dataflow-level questions (`describe`, `get_operations`, `get_dataflow`,
`get_structure`) load **that one VI** live at `LoadMode.MINIMAL` (XML already
cached). Not persisted. This is the token-lean Serena split: bulk/navigation off
the facts index, depth on demand.

### Incrementality without a watcher

`lvkit index <repo>` (or auto-index on the MCP server's first query) walks VIs;
per VI, `meta_fresh` → reuse facts JSON, else rebuild that one. Even before any
facts-cache, extraction is already cached, so re-index pays pylabview cost only
for changed VIs. **No OS file-watcher** (fragile on the maintainer's WSL↔Windows
mounts). Phase-2 "always-hot" refresh is **git-diff based** (`git status` →
re-index changed paths) — identical behavior on WSL and Windows, the same
approach codebase-memory-mcp defaults to.

---

## 4. MCP tool surface (project-scoped, replaces the global `_graph`)

Today the server holds a module-global `_graph` (`server.py:63`) that any
`clear` nukes and parallel agents pollute. Replace with a **project-scoped
index keyed by project root** (from `find_project_store` / `_project_root_for`)
— one index per repo, backed by the on-disk store, reloadable, safe for parallel
agents on different repos.

- `index(project_root)` — build/refresh; returns {vis, collisions_reported,
  stale_rebuilt, ms}.
- `find_symbols(name?/kind?/class?)` — workspace symbol search (FTS).
- `get_signatures(vi_names?)` — bulk connector panes, terminals summarized
  (name/direction/type/fields) — from `get_inputs`/`get_outputs`, **not**
  `get_vi_context`.
- `find_terminals(direction?/type?/is_error_cluster?/name?)` — the classifier
  query.
- `get_callers(vi)` / `get_callees(vi)` — **pure** call edges (`rel != "owns"`).
- `find_type_usages(type)` — reverse type-usage.
- `find_constants(wired_to?=indicator|control)` — constants→indicators.
- Deep single-VI (on-demand load): `describe`, `get_operations`,
  `get_dataflow`, `get_structure`.

---

## 5. The demo is the acceptance test (JKI VI Tester, 487 VIs)

| Ask | Tool | Backing (file:line) |
|---|---|---|
| counts of each name used for **error indicators** | `find_terminals(direction=indicator, is_error_cluster=true)` → `Counter(name)` | `get_outputs` `queries.py:549` + `Terminal.is_error_cluster` `models.py:164` |
| does **VI x** have callers? | `get_callers(x)` | `_dep_graph.in_edges` minus `rel="owns"` (`loading.py:414`) |
| all **constants wired to indicators** | `find_constants(wired_to=indicator)` | `get_all_constants` `queries.py:106` + `outgoing_edges` `core.py:410` + `FPTerminal.is_indicator` `models.py:254` |

Three-quarters of the demo is wiring existing per-VI primitives into cross-VI,
index-backed bulk tools. No LabVIEW needed — the corpus is already in the cache.

---

## 6. Phasing

- **P0 — unbreak setup (the "trash garbage").** Cap `mcp>=0.9.0,<2` (2.0.0
  removed the `@app.list_tools()` decorator API `server.py` is built on → dies
  at import, registers zero tools, silent) or port to 2.x; add `lvkit mcp
  --selftest` (initialize → tools/list, non-zero on failure) + a CI handshake;
  batch `load` (dir/.lvlib/.lvclass expansion — prototyped in the old doc §2);
  path-key at the index layer (report collisions instead of silently dropping).
  That un-breaks setup; **§8** covers the other half — the signed standalone
  binary that removes the uv/Python prerequisite entirely.
- **P1 — the demo.** Facts projection in cache + `find_terminals` /
  `get_callers` / `find_constants` + `lvkit index`. Acceptance = the three demo
  answers on JKI VI Tester.
- **P2 — premium.** SQLite+FTS rollup, `find_symbols`, `find_type_usages`,
  `get_signatures`, project-scoped server state, git-diff incremental refresh.
- **P3 — optional.** Always-hot background refresh (git-diff, not OS watch).

---

## 7. Blast radius & visualization — the premium layer

Two categories the index unlocks that lvkit is *already* half-built for.

### Blast-radius / change-impact analysis

The agent's real question when touching a VI is **"what breaks if I change
this?"** Blast radius = BFS outward along **reverse** call edges from a target;
depth-1 = direct callers, depth-N = transitive dependents. Prior art:

- **GitNexus** (MCP-native code KG) — **pre-computes** the dependency structure
  at index time, so an `impact` call returns a complete, confidence-scored blast
  radius in *one* tool call instead of the agent chaining 10+ graph queries.
- **Code Review Graph** / **CodeIndex** / **Arbor** / **Omen** — per-symbol
  blast-radius *scores* (how many files break if this changes), direct +
  transitive, some with a Claude Code "Impact Assessment" skill.
- Recurring premium move: **impact is a stored column, not a live traversal.**

**lvkit already does this in the diff.** The diff GitHub Action computes
"transitive subVI/typedef ripple + impact analysis" over a repo at two commits.
The index *generalizes* that from a diff-time computation to a **live,
precomputed query**:

- `blast_radius(vi)` = `nx.ancestors(call_graph, vi)` over **pure** call edges
  (`rel != "owns"`) — a networkx one-liner on the DiGraph rebuilt from the
  `calls` rows.
- Store the **count** per VI as an `impact_score` column at index time
  (GitNexus's lesson); materialize the **set** on demand.
- **The VI-Tester-specific killer:** the corpus *is* a test framework, so
  "which tests exercise VI X?" = blast radius filtered to test-method VIs
  (`TestCase.lvclass` subclasses / `runTest`/`test*`). "What breaks / what tests
  cover this change?" is the highest-value agent question and it's free here.

### Codebase visualization

Prior art: **NDepend** (Dependency Structure Matrix — matrix view beats a
hairball graph for spotting patterns/cycles), **CodeCharta** (3D "software city,"
local-only), **CodeSee** (live maps), **Madge/dependency-cruiser** (scriptable
graph emitters), **CodeScene** (health/hotspots via churn, not just structure),
Mermaid architecture-map auto-generation.

**lvkit already renders.** It has the faithful block-diagram SVG renderer, the
diff viewer, *and* a pyvis interactive graph (`cli.py` `visualize` →
`net.save_graph`). The index is the missing *project-scale* data source:

- `visualize_project(scope?)` — call graph / class hierarchy / DSM from the
  `calls` + class-facts rows. Self-contained **Mermaid or SVG** (fits the
  Artifact/clean-room model — no external hosts, ship-nothing-NI).
- **Blast-radius overlay:** highlight the affected subgraph for a proposed
  change — the visual twin of the `blast_radius` tool.
- Health overlay (later): hotspots by fan-in (`impact_score`) — the VIs most
  expensive to change.

### Added tools

- `blast_radius(vi, depth?)` — transitive dependents (+ test coverage filter).
- `visualize_project(scope?, highlight?)` — Mermaid/SVG project map, optional
  blast-radius highlight.

### Added demo ask (4th)

> "If I change `run.vi`, what breaks — and which tests cover it?"

→ `blast_radius(run.vi)` with the test filter. Precomputed `impact_score`
makes it O(1); `visualize_project(highlight=run.vi)` shows the ripple.

---

## 8. Distribution & setup — the signed binary closes the "trash garbage" loop

The "trash garbage setup" was **two** failures, and the sections above name only
one. (a) The server **died silently** on a fresh resolve — `mcp>=2` removed the
decorator API `server.py` was built on (§1 of the old doc); P0's `mcp<2` cap +
`--selftest` + CI handshake fix that. *(Later superseded: server.py's import
shim runs on FastMCP (1.x) OR MCPServer (2.0), so the cap is dropped and
`--selftest`/CI now guard both majors.)* (b) Even when it ran, *getting it there*
meant provisioning uv/Python/pip and a `uvx --from lvkit lvkit-mcp` incantation.
The **code-signed standalone binary** lvkit already builds for the VS Code
extension fixes (b): it carries the whole CLI, `lvkit mcp` included, and needs no
interpreter.

**Verified, not assumed.** Built the onedir bundle
(`editors/vscode/build/build-binary.sh`) and drove it over real MCP stdio:
`initialize` → `tools/list` returned all **12 tools** with zero Python/uv;
`mcp` + compiled `pydantic_core` are pulled in by PyInstaller's import graph with
no extra `--collect-*` flags. So the transport carrier already exists, and it's
code-signed (Azure Public Trust) — SAC/Device-Guard-clean, and the easiest
possible profile for an enterprise allow-list (**local stdio, no network
egress**).

**Two setup paths, both on the one binary:**

- **A — point any client at the binary:**
  `{"command": ".../lvkit", "args": ["mcp"]}`. Needs the binary shipped as a
  release asset (plan D.2). Works for Claude Desktop / Claude Code / Cursor.
- **B — VS Code auto-registers** the bundled `lvkit mcp` (plan D.3, after P2) →
  zero config for agent mode. Verify the `McpServerDefinitionProvider` API +
  minimum `engines.vscode` first (`^1.75.0` today is likely too old).

**Reach** follows one rule — a `command` server needs a real backend process and
a matching-platform binary: desktop / Remote-SSH / Dev Containers / Codespaces /
WSL work; **browser-only virtual workspaces** (vscode.dev / github.dev) cannot
(no native process; the extension already declares
`virtualWorkspaces: supported: false`). Cursor uses Path A (Open VSX + its own
MCP config, not our MS-Marketplace channel).

**Under an MCP ban the capability survives the transport.** Every MCP tool is
also a CLI subcommand, so skills shell out to `lvkit …` (plan P3.3 / D.4) and the
extension's render/diff — which never used MCP — keep working. MCP is a
convenience layer, not a dependency.

---

## 9. Open decisions (for the maintainer)

1. **Rollup substrate:** SQLite+FTS from the start (premium, more code) vs. JSON
   manifest first, SQLite when scale demands. (Per-VI JSON is the incremental
   unit either way.)
2. **Symbol identity depth:** fix only at the index layer (path-keyed index over
   the name-keyed graph — no core refactor) vs. re-key `_vi_nodes`/`_dep_graph`
   on path (correct everywhere, invasive). P0 proposes index-layer only.
3. **Watcher:** git-diff refresh sufficient (recommended) vs. a real OS watcher.
