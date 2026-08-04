# Implementation plan — lvkit code-understanding index

Execution plan for the architecture in `lvkit-mcp-index.md`. Ordering principle:
**build the index engine as a standalone, CLI-testable library; the MCP server is
a thin wrapper added last on mcp 2.x.** This keeps the hard, valuable core (index
+ queries) independent of MCP API churn and lets the demo be proven via CLI
before any MCP is involved.

Branch: `mcp-improvements`. Each phase ends in a verified commit.

## Phase 0 — unbreak + diagnose (protects the shippable 0.5.8)

- [x] **P0.1** Cap `mcp>=0.9.0,<2` (`pyproject.toml`) + relock. Verified: server
  imports, 12 tools list under mcp 1.27.0. Commit `e0b7fb1`.
- [ ] **P0.2** `lvkit mcp --selftest` — initialize the server, list tools, print
  the count, exit non-zero on any failure. Catches the import-time class of bug
  in one command. `cli.py` (`mcp` subparser + `cmd_mcp`). Reusable by CI + the
  2.x rewrite. Test: `lvkit mcp --selftest` exits 0 and prints ≥12.
- [ ] **P0.3** CI job: real `initialize` → `tools/list` handshake against the
  built package (or just `lvkit mcp --selftest`). An import error must never
  reach a release again.

## Phase 1 — the index engine (standalone `lvkit/index/`)

New package `src/lvkit/index/`, no MCP dependency. Everything CLI/pytest-testable.

- [ ] **P1.1 `model.py`** — facts dataclasses (strings only, serializable):
  `VIFacts` (path, name, qualified_name, library, klass, is_stub, content_sha),
  `TerminalFact` (name, direction, is_indicator, is_public, control_type,
  py_type, field_names, is_error_cluster, fp_dco_uid), `CallEdge`
  (caller_path, callee_key), `TypeUse` (vi_path, type_key), `ConstantFact`
  (vi_path, value, label, type, wired_to ∈ indicator|control|other),
  `ClassFact` (parent, methods+scope). Keyed by **resolved path** (fixes the
  422/487 collision).
- [ ] **P1.2 `project.py`** — resolve a load target → project root (reuse
  `cache_paths._project_root_for` / `project_store.find_project_store`) + the VI
  file list (reuse `pipeline.py:406-425` dispatch). The project root is the index
  identity.
- [ ] **P1.3 `build.py`** — project an `InMemoryVIGraph` (loaded MINIMAL) → the
  facts above, reusing existing queries: `get_inputs`/`get_outputs`
  (signatures), `Terminal.is_error_cluster` (models.py:164),
  `get_all_constants` + `outgoing_edges`/`is_indicator` (constants→wire target),
  `metadata.subvi_qualified_names` (call edges), `type_map` (type-uses),
  class nodes/edges (class facts, call edges filtered `rel!="owns"`). Detect +
  report path collisions instead of silently dropping.
  - **Mode = MINIMAL (measured decision).** NONE vs MINIMAL whole-repo build is
    within noise (127.6 s vs 120.0 s) and yields an identical demo tally (325
    error-indicator terminals, 13 names) — because `load_directory` loads every
    VI regardless, so mode only changes dependency pull-in, already covered by
    the directory walk. Use MINIMAL (fidelity free). FULL stays out (pulls
    external vi.lib method trees).
  - **Fast build = parallelism + incremental, NOT mode.** The ~120 s is per-VI
    `parse_vi` + construction (deps are cheap here), which is VI-intrinsic and
    pylabview-free once XML is cached → fan out across cores
    (`ProcessPoolExecutor`), ~120 s → ~15-20 s on 8 cores. The facts pass SKIPS
    `resolve_dispatch_qnames`/`stamp_nmux_lane_names` (display-only). Incremental
    (`meta_fresh`) makes an unchanged repo ≈ ms. (Optional: profile parse-vs-
    construct to squeeze the per-VI cost further.)
  - **Incremental:** per VI, `cache_paths.meta_fresh(vi, meta, extra={schema,
    version})` → reuse the stored row, else rebuild that VI. Unchanged repo ≈ ms.
- [ ] **P1.4 `store.py`** — SQLite at
  `~/.lvkit/cache/index/projects/<slug>/index.db` (reuse `classify`/`_slug` for
  the slug). Tables: `vis`, `terminals`, `calls(caller,callee)`, `type_uses`,
  `constants`, `class_facts`, `meta(vi_path, sha, schema)`. WAL. Upsert by path.
  FTS5 on `vis.name` (later; `LIKE` is fine at 635 rows).
- [ ] **P1.5 `query.py`** — `find_terminals(direction?, is_error_cluster?,
  type?, name?)`, `get_callers(vi)`/`get_callees(vi)` (pure call edges),
  `find_constants(wired_to?)`, `find_type_usages(type)`, `find_symbols(...)`,
  `blast_radius(vi, depth?)` (= `nx.ancestors` over a DiGraph rebuilt from the
  `calls` rows; store `impact_score` per VI at build time). Graph walks =
  networkx over the `calls` rows; aggregates = SQL/`Counter`.
- [ ] **P1.6 CLI** — `lvkit index <path>` (build/refresh; prints
  {vis, collisions, stale_rebuilt, ms}). `cli.py` subparser + `cmd_index`,
  mirroring `cmd_mcp`.
- [ ] **P1.7 tests** — build the index over a small class fixture + assert the
  four demo answers (error-indicator name counts; `get_callers`; constants wired
  to indicators; `blast_radius`). Execute against JKI VI Tester as the accept.

**Acceptance:** the four demo answers on JKI VI Tester, via CLI, path-keyed
(487 symbols, not 422), first build < ~30 s, follow-on queries sub-ms.

## Phase 2 — MCP 2.x server (thin wrapper)

- [ ] **P2.1** Rewrite `mcp/server.py` on mcp 2.x (FastMCP `@mcp.tool()`).
- [ ] **P2.2** Project-scoped index (keyed by project root) — retire the global
  `_graph`; parallel-agent safe.
- [ ] **P2.3** Expose `query.py` as tools + on-demand deep single-VI tools
  (`describe`/`get_operations`/`get_dataflow`/`get_structure`) that load one VI
  live. `index` tool builds/refreshes.
- [ ] **P2.4** Lift the `<2` cap in the same PR; keep `--selftest` + CI green.

## Phase 3 — premium + polish

- [ ] **P3.1** `visualize_project(scope?, highlight?)` — Mermaid/SVG project map
  (call graph / hierarchy / DSM) + blast-radius overlay. Reuse the renderer /
  pyvis.
- [ ] **P3.2** git-diff incremental refresh (no OS watcher).
- [ ] **P3.3** Skills prefer MCP when connected, else CLI (update `skill_templates/`
  — **after** the P2 tool surface settles, so skills aren't rewritten twice).
