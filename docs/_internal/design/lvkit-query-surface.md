# lvkit query surface — SQL over facts, typed ops for traversal

**Status: accepted (2026-08-08); first slice IMPLEMENTED (2026-08-08).** The §7
first slice shipped: `src/lvkit/index/sql.py` (view layer + structural read-only
sandbox + `run_query`/`describe_schema`/`error_indicator_histogram`), the MCP
`query`/`query_schema` tools, and the `lvkit query` CLI. The subsumed read tools
(`find_*`/`get_signatures`) were retired (MCP 21→16 tools); reachability stays as
the typed `get_callers`/`get_callees`/`blast_radius` ops. Validated on the JKI
corpus. Open decisions in §9 (return shape settled on columnar; views are
connection-time TEMP views) are resolved as built.

Supersedes the query-*language* decision in
`lvkit-graphql-api.md` (which proposed GraphQL — see §4 for why that flipped).
Builds on `lvkit-mcp-index.md` (the two-tier index, unchanged) and
`lvkit-mcp-index-plan.md` (the index engine). The parallel index *build* (perf)
is a separate, independent work item — TASKS.md "indexer / MCP query
architecture" **[C]**; this doc is about how the index is *queried*.

Grounded in two verified prior-art sweeps (2026-08-08): one on incremental
relational-index building, one on query surfaces for code indexes. Findings are
folded in inline and captured in TASKS.md **[A]–[E]**.

---

## 1. The decision, up front

Replace the read-half of the ~19 MCP tools with:

1. **One `query` tool: read-only SQL over a curated VIEW layer** — for the
   relational/*definition* facts (units, terminals, constants, calls, types).
   Native `GROUP BY`/projection is the point: it returns the *answer*, not the
   source.
2. **A few TYPED graph operations** — `callers` / `callees` / `blast_radius` /
   `descendants` — for *transitive* traversal, backed by networkx. Reachability
   deliberately stays **out** of the SQL surface.
3. **VI-scoped dataflow resolvers** — `dataflow(vi, …)` etc. — for *instance*-
   level wire detail, resolved on demand for one named VI, projected small.

Everything read-shaped collapses into (1)+(2)+(3). The compute/side-effect tools
(`index`, `describe`, `generate_python`, `generate_documents`, …) stay as tools.
Net: ~19 read tools → **1 `query` + ~4 graph ops + ~2 dataflow resolvers**.

---

## 2. Why this shape: definition vs instance

lvkit already holds **two graphs** (`graph/core.py:147,154`), and they are the
classic **definition vs instance** (defines/refs, type/token) distinction:

| | `_dep_graph` (DiGraph) | `_graph` (MultiDiGraph) |
|---|---|---|
| Level | **definitions** | **instances / occurrences** |
| Node | one per VI definition | each call *site*, each wire |
| Multiplicity | collapsed (A→B is one edge) | preserved (B called 5× = 5 instances) |
| Cardinality | **few** | **many** |
| Answers | callers, blast-radius, signatures | trace-a-wire, what-feeds-X, codegen |

The index persists only the **definition** side (`VIFacts` → the SQLite tables in
`index/store.py`); the **instance** dataflow is never materialized corpus-wide —
it's resolved per-VI on demand (`_load_one`). This is the load-bearing tiering:
**index the few definitions, resolve the many instances on demand.** Materializing
every wire occurrence across ~500 VIs *is* the payload blow-up we're avoiding.

So the three query tiers map cleanly:

- **Definition facts, corpus-wide** → SQL over views (§3). Cheap, aggregate.
- **Instance dataflow, VI-scoped** → on-demand resolver, must name a VI. Bounded.
- **Instance→definition links** → jump-to-def / find-all-instances, and the
  codegen provenance map (§5).

---

## 3. The relational surface: read-only SQL over views

The facts are already relational in SQLite (`index/store.py`: `vis`, `terminals`,
`constants`, `calls`, `type_uses`, `class_facts`). We expose **stable views** as
the public contract so the physical tables can churn underneath:

```sql
CREATE VIEW vi          AS SELECT path, name, qualified_name, library,
                                  is_stub, impact_score FROM vis;
CREATE VIEW terminal    AS SELECT vi_path, name, direction, is_indicator,
                                  is_public, py_type, is_error_cluster,
                                  field_names FROM terminals;
CREATE VIEW constant    AS SELECT vi_path, value, label, py_type, wired_to
                                  FROM constants;
CREATE VIEW call        AS SELECT caller_path, callee_key FROM calls;
```

The driving question — *"count the names this project uses for error
indicators"* — is then one statement returning the **13-row answer**, never the
379 terminal rows / 141 KB the current tool dumps:

```sql
SELECT name, COUNT(*) AS n
FROM terminal
WHERE is_error_cluster = 1 AND direction = 'output'
GROUP BY name ORDER BY n DESC;
```

**Native `GROUP BY` is the whole argument** (§4). Projection (`SELECT` only what
you need) is the second half of the token win.

### 3a. Sandbox — read-only enforced *structurally*, not by string match

Non-negotiable, because Anthropic's own Postgres MCP shipped a read-only-bypass
injection and was archived. The recipe (Datasette / MCP-DB consensus):

- open the DB file **`immutable` / `mode=ro`** (SELECT-only becomes structural —
  injection can't write);
- a **least-privilege** connection;
- a **parsed-AST allowlist** (single `SELECT`/CTE; reject writes, `PRAGMA`,
  `ATTACH`) — parse, don't grep;
- **statement timeout** (~1 s) via `set_progress_handler`/`interrupt`;
- a **row cap** (~1000) and an auto-`LIMIT`.

### 3b. Borrow the semantic layer's discipline (not its machinery)

Benchmarks say raw text-to-SQL is unreliable on *big* schemas (Spider 2.0 ~21 %,
BIRD ~73 %) and fails **silently wrong**; governed vocabularies hit 98–100 % and
fail **loud**. Our schema is *tiny and curated*, which is the regime where LLM
SQL is reliable — so we take the discipline, not a full semantic layer:

- keep the **view surface small and well-named**;
- ship **schema introspection** (`describe` the views) so the model grounds
  itself instead of hallucinating columns;
- provide **canned aggregate queries** for hot paths (the error-indicator
  histogram is canned);
- prefer a **loud "out of scope" error** over silent-wrong rows.

---

## 4. Why SQL, not GraphQL, not Datalog

- **vs GraphQL** (the superseded proposal): GraphQL is projection-native but
  **aggregation is not in the spec** — you hand-build Hasura `_aggregate` fields
  *per question*, which is the per-tool hand-shaping we're escaping. Our driving
  case is a server-computed histogram; **SQL `GROUP BY` is the direct fit**.
  Sourcegraph picked GraphQL for a *public multi-client* API — a different
  problem than a single local token-minimizing agent. The proposal's real
  concern (storage coupling) is answered by the **view layer**, not by GraphQL.
- **vs a Datalog/DSL** (CodeQL's QL, Glean's Angle): best-in-class for recursion
  and reusable predicates, but it's **a language the LLM must learn** (far less
  training data than SQL) and an engine to maintain — overkill at ~500 units.
- **but borrow their reachability lesson**: CodeQL and Glean — the systems built
  *for* code reachability — deliberately **do not express transitive closure in
  SQL** (QL has first-class `+`/`*` closure; Glean even caps recursion). That is
  exactly why blast-radius/callers stay **typed ops** (§1.2), returning the
  answer-set, not recursive CTEs an LLM would fumble.

---

## 5. The same surface serves codegen understanding

Codegen (`build_module`) consumes the **same `VIContext`** (`graph.get_vi_context`)
the query tools read — one graph, two consumers. Consequences:

- An agent can **understand and repair generated Python by querying structure —
  never reading `_BDHb.xml`.** "What feeds the node behind line 42? what's the
  callee's signature?" are an instance query + a definition query.
- Add the **provenance edge** codegen already builds (Python-line ↔ VI-operation)
  and "this line is wrong" resolves in one hop to "this instance → its dataflow →
  its definition's types." That is what makes the *AI cleanup agent replacing the
  LLM pipeline* roadmap actually work — structured facts, not XML parsing.

Codegen is an **instance walk that resolves definition signatures**, so it needs
both graphs + the link — precisely the query substrate above. Tension with the
token goal (codegen wants everything; queries want little) is reconciled by
**projection**: same model, callers ask for exactly what they need.

---

## 6. lvkit is the indexer; CLI and MCP both wrap it

`lvkit.index` (build / refresh / `build_one_vi` / store) is the first-class
surface. The CLI gains index/query subcommands; the MCP server calls the **same**
API (no divergent code paths). The lazy single-VI path and the batch build must
**share one fact store + hashing**, so a lazy query warms the same cache the
batch reads (Glean's unified DB; Salsa's unified query table) — not two paths.

---

## 7. First slice (the acceptance path)

Pick it from real `lvkit-wintest` transcripts, not from what's easy to resolve.
The error-indicator question alone exercises most of a first slice:

1. **View layer** (§3) + the **sandbox** (§3a) + a `query(sql)` tool.
2. The **canned error-indicator aggregate** returning the 13-row histogram.
3. **Schema introspection** tool (list views + columns).
4. **Typed graph ops**: `callers`, `callees`, `blast_radius` (networkx over
   `calls`).
5. Collapse/retire the ~8 read tools they replace; keep the compute tools.

Acceptance: the error-indicator question returns the 13-row histogram with **no
source-file read and no 100+-row payload**, and `blast_radius` returns an answer
set — both against JKI VI Tester.

---

## 8. Limits worth stating

- Views decouple from **table churn** but still publish **SQLite's SQL dialect**
  as the contract — real coupling if the store ever leaves SQLite.
- A view **hides cost**: `SELECT *` / unshaped joins look cheap but aren't
  (Steampipe's lesson) — pre-shape expensive joins *inside* the views.
- Typed ops are a **promise about traversal**: once `blast_radius(depth)` is
  public because networkx makes it cheap, it's owed even if storage later moves
  somewhere recursion is hard. Keep the first slice narrow.

---

## 9. Open decisions

1. **`query` return shape** — rows as list-of-objects (readable) vs columnar
   (compact). Lean columnar + a `columns` header for token economy.
2. **Where the views live** — materialized in the SQLite file at build time, or
   defined at connection time on a read-only handle. Lean connection-time (keeps
   the stored file a pure fact store; views are pure contract).
3. **Instance-dataflow surface** — a second SQL-ish view over an on-demand
   per-VI load, or a typed `dataflow(vi)` op. Decide when the first
   instance-level transcript demands it; not in the first slice.
