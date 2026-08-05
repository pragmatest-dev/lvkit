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
- [x] **P0.2** `lvkit mcp --selftest` — initialize the server, list tools, print
  the count, exit non-zero on any failure. Catches the import-time class of bug
  in one command. `cli.py` (`mcp` subparser + `cmd_mcp`). Reusable by CI + the
  2.x rewrite. Verified: `lvkit mcp --selftest` → `server initialized, 12 tools`.
- [x] **P0.3** CI job: real `initialize` → `tools/list` handshake against the
  built package (or just `lvkit mcp --selftest`). An import error must never
  reach a release again. (Commit `7bd58c9`.)

Un-breaking setup is only half the "trash garbage." Setup still needed
uv/Python/pip to *run* the server at all — **Phase D** removes that: the signed
standalone binary is already an MCP server today (D.1, verified).

## Phase 1 — the index engine (standalone `lvkit/index/`)

New package `src/lvkit/index/`, no MCP dependency. Everything CLI/pytest-testable.

> **Status: complete on this branch** (tasks P1 index engine + `lvkit index`
> CLI + demo validation; 8/8 tests). The per-item boxes below predate that and
> are left as the design record.

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

## Phase 2 — FastMCP server (thin wrapper) — DONE

- [x] **P2.1** Rewrote `mcp/server.py` on **FastMCP** (`@mcp.tool()`) — 19 tools,
  selftest green. (See P2.4: FastMCP is the mcp **1.x** high-level API.)
- [x] **P2.2** Project-scoped index cache keyed by project root (`_indexes`);
  retired the module-global `_graph` + the `load`/`clear`/`list_loaded` session
  dance. Different repos → different entries, so parallel agents don't collide.
- [x] **P2.3** Exposed `query.py` as project tools (`index`, `find_symbols`,
  `find_terminals`, `find_constants`, `find_type_usages`, `get_callers`,
  `get_callees`, `blast_radius`, `get_signatures`, `visualize_project`) + deep
  single-VI tools (`describe`/`get_operations`/`get_dataflow`/`get_structure`/
  `get_constants`/`get_context`/`generate_ast_code`) that take a `vi_path` and
  load one VI live. Verified end-to-end over real MCP stdio on JKI VI Tester:
  error-indicator names in ONE call (`{'error out': 352, …}`), `get_callers`,
  `blast_radius`, deep `describe`, Mermaid map — all green.
- [~] **P2.4 — DEFERRED, cap NOT lifted (factual discovery).** `mcp` 2.0.0
  removed `mcp.server.fastmcp` **entirely** (verified in a 2.0.0 venv — the
  import that FastMCP, and this server, are built on is gone). 2.x's replacement
  is a brand-new `mcp.server.mcpserver.MCPServer`; adopting it would abandon
  mcp 1.x (what the shipped binaries + extension run) for an unstable fresh API
  with no user benefit. Kept **`mcp>=1.2,<2`** (floor bumped to reflect the
  FastMCP requirement); `--selftest` + the CI handshake remain the guard.
  Revisit when 2.x stabilizes, or via the standalone `fastmcp` package.

## Phase 3 — premium + polish

- [ ] **P3.1** `visualize_project(scope?, highlight?)` — Mermaid/SVG project map
  (call graph / hierarchy / DSM) + blast-radius overlay. Reuse the renderer /
  pyvis.
- [x] **P3.2** Incremental refresh (no OS watcher). **Content-hash, not git**
  (deviation from the original wording): a VI whose `sha256_file` still equals
  its stored `content_sha` is reused; changed/new VIs rebuild via
  `build_one_vi`; deleted ones drop; `impact_score` recomputed globally. Chosen
  over git-diff because it is universal (no git repo needed), simpler, and
  matches the model's existing `content_sha` incrementality key. Wired as
  `lvkit index --refresh` and the MCP `index(project, refresh=True)` tool.
  Measured on JKI: unchanged repo refresh **86 ms vs 152 s** full build (~1800×).
  Tests: `TestIncrementalRefresh` (no-change / stale-sha / deletion).
- [x] **P3.3** Skills prefer MCP when connected, else CLI. Updated the two
  MCP-referencing templates (`lvkit-describe`, `lvkit-convert`) to the P2 tool
  surface — deep tools take a `vi_path` (no `load`/`clear`), plus the project
  index tools (`index`/`find_*`/`get_callers`/`blast_radius`/`visualize_project`)
  — with an explicit "prefer MCP when connected, else CLI" framing (= D.4).
  Dropped stale `load`/`list_loaded`/`analyze` references. NOTE: the project
  index queries are **MCP-only** today; a `lvkit query`/`explore` CLI would
  complete the MCP-banned fallback (follow-up).

## Phase D — distribution: the signed binary as zero-setup transport

An **independent axis** from the engine phases. P0 stopped the server dying
silently; this removes the *other* half of the "trash garbage" setup — needing
uv/Python/pip to run it at all. lvkit already builds a **code-signed standalone
PyInstaller binary** (Azure Public Trust) for the VS Code extension, and it
carries the whole CLI, `lvkit mcp` included. D.1 already holds today; D.3 waits
on P2 (so what auto-registers is the good, project-scoped surface).

- [x] **D.1 — verified: the signed bundle already *is* an MCP server.** Built the
  onedir bundle (`editors/vscode/build/build-binary.sh`) and drove it over real
  stdio: `initialize` → `serverInfo{lvkit-mcp}` → `tools/list` = all 12 tools,
  with **zero** Python/uv/pip. `mcp` + compiled `pydantic_core` are pulled in by
  PyInstaller's import graph with no extra `--collect-*` flags (`mcp` is a hard
  dep — pyproject.toml). Same binary: `lvkit --version` → 0.5.8.
- [x] **D.2 — ship the binary as a release asset (Path A).** DONE (`42afb7e`):
  the `publish-extension.yml` build matrix zips the signed onedir bundle
  (`lvkit-mcp-<lvkit-ver>-<target>.zip`) and a tag-gated `release-binaries` job
  attaches all four to the GitHub Release. Setup collapses to
  `{"command": "/abs/path/to/lvkit", "args": ["mcp"]}` — no `uvx --from lvkit`.
  Dry-run 30981139533 verified: 4/4 builds green, publish + release skipped; the
  downloaded linux artifact unzips (exec bit + mac framework symlinks preserved)
  and runs `lvkit mcp` → 12 tools. **Goes live on the next `ext-v*` tag.**
- [x] **D.3 — VS Code auto-registration (Path B).** DONE (extension 0.1.12):
  `registerMcpProvider` in `extension.js` registers the bundled `lvkit mcp` via
  `vscode.lm.registerMcpServerDefinitionProvider` + a
  `contributes.mcpServerDefinitionProviders` point → agent mode gets the P2
  tools with **zero config**. VERIFIED (not assumed): the API is stable in VS
  Code **≥ 1.101** — `vsce package` accepts the contribution point at
  `engines.vscode: ^1.101.0` (it validates contributions against the engine).
  Feature-detected (`typeof vscode.lm?.registerMcpServerDefinitionProvider`), so
  the extension still loads on older editors (render/diff unaffected). Landed
  after P2, so the registered surface is the project-scoped one. **NOT
  published** — packaging-verified only; the `ext-v0.1.12` tag is the user's
  call (the engine bump drops pre-1.101 editors from future updates).
- [ ] **D.4 — enterprise-restriction fallback.** MCP is a convenience layer over
  the CLI: every MCP tool is also a `lvkit` subcommand
  (`describe`/`generate`/`diff`/`render`/`structure`/`index`). An MCP ban is a
  client-side gate (Copilot/Cursor/Claude policy, upstream of us) — under it,
  skills fall back to CLI shell-out (ties into P3.3) and the extension's own
  render/diff (which never used MCP) are unaffected. Allow-list pitch: **local
  stdio, zero network egress, code-signed** — the easiest profile to get an
  exception for.

**Environment reach** (governing rule: a `command` MCP server needs a real
backend process *and* a matching-platform binary): desktop, Remote-SSH, Dev
Containers, Codespaces, WSL → works; **browser-only virtual workspaces**
(vscode.dev / github.dev) → not possible (no native process — the extension
already declares `virtualWorkspaces: supported: false`); Cursor → Path A /
sideload (Open VSX + its own MCP config, not our MS-Marketplace channel).
