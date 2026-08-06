# A GraphQL query surface for lvkit

**Status: proposal.** Written 2026-08-06 against the bundled `lvkit 0.5.8`
(`repo/lvkit/lvkit.exe`, 19 MCP tools, `mcp 1.27.0`).

Companion to `lvkit-mcp-improvements.md`. That document lists individual defects
in the MCP surface; this one argues that several of them share a root cause —
**a fixed menu of tools standing in for a query surface** — and proposes
replacing the read-only half of the surface with a single GraphQL endpoint.

It came out of the same task that produced the improvements document, re-run a
second time on the current build: *count the names this project uses for error
indicators*, across the 487 VIs of VI Tester.

---

## 1. The evidence

`find_terminals` is the tool built for this exact question. Its docstring says
so:

> Combine `direction="output"` with `is_error_cluster=True` for the canonical
> "what names does this project use for error indicators?" question — then tally
> the returned `name` values.

Run as documented, it returned **379 records / 141,508 characters** — enough to
overflow the client's context, so the MCP client spilled it to a file. The
answer being sought was **13 rows**. Three distinct limitations combined to
produce a ~10,000× overshoot:

**No aggregation.** The question is `GROUP BY name, COUNT(*)`. The tool has no
way to express it, so the tally has to happen client-side over the full payload.

**No projection.** The caller needs one field, `name`. Every record arrives with
`vi_path`, `vi_name`, `control_type`, `py_type`, `is_public`, `field_names[]`
and `fp_dco_uid` regardless.

**Only the author's chosen filters.** The signature exposes `direction`,
`is_error_cluster`, `name` and `py_type`. `is_public` and `field_names` come
*back* in every record but cannot be filtered *on*. "VIs with more than one error
indicator" — a natural follow-up, and one the data fully supports — is not
expressible at all.

None of these is a bug in `find_terminals`. They are what a frozen tool
signature *is*. Every unanticipated question needs a new tool, and the ninth such
tool is no more able to anticipate the tenth question than the first was.

---

## 2. Why GraphQL specifically

### 2.1 Projection is mandatory, not optional

A GraphQL client names every field it wants or receives nothing. The 141KB
response above becomes:

```graphql
{ terminals(direction: OUTPUT, isErrorCluster: true) { name } }
```

For a surface whose primary consumer is a language model on a context budget,
making projection *structural* is the single highest-value property on this
list. SQL does not give you it — `SELECT *` is always available and always
tempting.

### 2.2 The data is a graph

VI → callees → their callees. VI → terminals → type. Class → members. GraphQL
spells traversal natively:

```graphql
{ vi(path: "…/run.vi") { callers { callers { name } } } }
```

Three of the current tools (`get_callers`, `get_callees`, `blast_radius`) exist
largely because traversal is awkward to express in a flat call/response shape.
As schema edges they cost nothing extra.

### 2.3 Introspection is discovery

Schema introspection is part of the spec, so the surface documents itself at the
moment of use. This matters more for MCP than for a typical web API: **one tool
schema occupies the model's context instead of nineteen**, and schema detail is
pulled on demand. `@deprecated(reason: "…")` rides along in introspection too,
so a caller sees "use X instead" while writing the query — something no SQL
surface can express.

### 2.4 Partial failure has somewhere to live

A GraphQL response can return `null` for a field and an entry in `errors[]`
explaining why, without failing the whole query. This is the structural fix for
the fabricated-label bug (`lvkit-mcp-improvements.md`, and §5 below): the current
flat row has no way to say *"I could not read this label,"* which is precisely
why it invents one.

### 2.5 It abstracts storage and the in-memory models

This is the strongest long-term argument, and it is not hypothetical here.
lvkit has **already performed** the storage migration this protects against:
from a name-keyed in-memory graph to a path-keyed persisted index. That change
altered observable behavior at the tool surface — project-wide counts went from
422 to 487 — with no way for a caller to know the semantics had changed. Under a
schema contract the same migration is invisible: `vis { path }` means the same
thing on both sides of it, and the collision fix arrives as *correct results*
rather than as a silent semantic shift.

### 2.6 Growth is non-breaking by construction

Adding types and fields cannot break existing clients, because clients only
receive what they asked for. The surface can ship with five types and grow to
thirty without a versioned endpoint. Combined with `@deprecated`, wrong guesses
are cheap to retract — which is what makes "start with a small curated slice and
grow it" safe rather than merely appealing.

Note this is *not* true of the alternatives. A raw SQL surface breaks anyone who
wrote `SELECT *` the moment a column moves. The current tool surface breaks
every caller of a tool whose return shape changes.

---

## 3. Curation is the point (and why that isn't the old problem)

An objection worth answering head-on: isn't a hand-maintained schema just the
fixed tool menu again, in nicer clothes?

No — the granularities differ in kind. The 19 tools are curated at the level of
**whole frozen questions**: `find_terminals` bakes in its filters *and* its
output shape, so an unanticipated question needs new code. A schema is curated at
the level of **types, fields and edges, which compose**. Fifteen well-chosen
fields answer far more than fifteen questions.

The caveat applies to aggregates specifically, since each aggregate shape *is* a
frozen question — see §4.

Curation is also where the data gets cleaned up. Today's payloads leak internals
that a schema absorbs:

| Today | Under a schema |
|---|---|
| `{vi_path, vi_name, terminal: {…}}` — a join artifact, and inconsistent with the docstring's implied flat shape | `Terminal` with a `vi: VI` edge, uniform everywhere |
| `fp_dco_uid: "93"` — raw LabVIEW front-panel object UID, string-typed | typed `ID`, or not surfaced at all |
| `py_type: "dict[str, Any]"` — a codegen concern denormalized onto every terminal | a field on a type object, resolved when asked for |
| VI identity ambiguous between path and name | `VI.path` is the `ID`; `name` is an attribute — the collision bug becomes *unrepresentable*, not merely fixed |

---

## 4. Aggregation

Aggregation is **not** in the GraphQL spec. It is, however, a thoroughly solved
convention: Hasura's `_aggregate` fields (`count`, `sum`, `min`/`max`,
`distinct_on`, with grouped aggregates in its later versions), PostGraphile's
aggregates plugin, Relay's conventional `totalCount`.

**Recommendation: adopt Hasura's spelling rather than inventing one.** A model
can then write the query without reading lvkit's docs, because that shape is
well represented in training data:

```graphql
{
  terminals_aggregate(where: {isErrorCluster: {_eq: true},
                              direction: {_eq: OUTPUT}}) {
    aggregate { count }
    group_by(field: NAME) { key count }
  }
}
```

The distinction to keep in view: Hasura offers this on *every* table because it
introspects Postgres and generates resolvers mechanically. A hand-written
Strawberry or Graphene schema gets exactly the aggregates someone writes
resolvers for. So decide early whether the aggregate layer is generated or
maintained by hand — generated preserves the maintainability win; hand-maintained
drifts back toward a fixed menu, just a better-looking one.

Conveniently, **the generated/hand-written line falls along the storage seam**
(§5): generate aggregates over the relational side, hand-curate traversal edges
over the graph side.

Design at least `count`, `groupBy`, `distinct` and `histogram` up front rather
than discovering each one via a bug report.

---

## 5. What this looks like against lvkit's actual internals

Observed from the bundle:

- **networkx** — pinned `3.6.1` in the previous `.venv` install; ~30 references
  in the current exe. In-memory graph.
- **SQLite** — `_internal/_sqlite3.pyd`, `sqlite3.dll`. *Inferred* to be the
  persisted path-keyed index described in the `index` docstring ("projected into
  a persisted, path-keyed facts row"); not directly verified.

Two consequences:

**There is nothing to "integrate with," and that's fine.** NetworkX has no
GraphQL interface and no query language at all — it is an in-process library
whose nodes are arbitrary Python objects with dict attributes, so there is
nothing to introspect and no wire protocol to serve. (The projects that *do* ship
GraphQL layers — Neo4j, Dgraph, TigerGraph, ArangoDB — are graph *databases*,
which have both.) But GraphQL resolvers are just Python functions:
`descendants: [VI!]!` resolves to a call to `nx.descendants(g, vi)`. Reach for
Strawberry or Graphene and wire it to functions that already exist.

**The N+1 concern is mild here.** That problem is fundamentally about network
round trips per resolver; nx traversal is a dict lookup in the same process.
Full DataLoader machinery is likely over-engineering. A query-complexity ceiling
still earns its keep — `descendants` on a dense call graph is real CPU, and a
model will eventually write a six-deep recursive `callers` query.

---

## 6. Proposed tool surface

19 read/query tools → **1 query tool + ~10 operations**.

**Collapse into `graphql`** — thin filters over indexed facts:
`find_terminals`, `find_symbols`, `find_constants`, `find_type_usages`,
`get_callers`, `get_callees`, `get_signatures`, `get_constants`, `get_structure`

**Keep as tools** — real computation, side effects, or non-JSON output:
`index`, `describe`, `get_context`, `get_dataflow`, `get_operations`,
`blast_radius`, `generate_ast_code`, `generate_python`, `generate_documents`,
`visualize_project`

*Caveat: this split is drawn from tool names and docstrings. Only `index`,
`find_terminals`, `get_signatures`, `find_symbols` and `describe` were inspected
at the schema level. `get_structure` and `blast_radius` in particular may sit on
the other side of the line.*

### Rejected: a raw `sql` escape hatch

An earlier draft of this argument proposed `graphql` plus a raw `query(sql)`
tool for analytics. **That is the wrong call** — it would weld the public surface
to SQLite and to the current table layout permanently, which is the exact
coupling §2.5 exists to avoid. If aggregation needs an escape valve it should be
schema-level aggregate fields (§4).

---

## 7. Limits worth stating up front

**The contract covers shape and types, not cost.** If `VI.callers` moves from an
in-memory nx lookup to a SQL join, every query keeps returning identical JSON
while the performance profile changes completely, and GraphQL has no vocabulary
for expressing that in the schema. Storage stays swappable for *correctness* and
silently does not for *latency*. Client query patterns tuned against one backend
can fall off a cliff on the next.

**Schema promises bind the implementer, not just the caller.** Once
`descendants(depth: Int)` is in the schema because nx makes arbitrary-depth
traversal cheap, it is owed to clients even if storage later moves somewhere that
does not do recursion well. Every surfaced field is a promise about what future
storage must be able to do — which is a concrete argument for keeping the first
slice narrow.

**Choosing the first slice by what's easy to resolve is the failure mode.**
Pick it from real transcripts instead. lvkit is unusually well placed here: the
`lvkit-wintest` repo exists to exercise the MCP, so its session logs *are* the
requirements document. The single question behind this write-up already exercises
VI → terminals → error-cluster filtering → aggregation, which is most of a first
slice on its own.

---

## Appendix: the measured result

Index: 487 VIs, 65 name collisions retained, ~189s full build (sub-ms queries
after). `find_terminals(direction="output", is_error_cluster=True)` → 379
terminals across 373 distinct VIs, 13 distinct names.

| Count | Name |
|------:|------|
| 351 | `error out` |
| 2 | `Error out` |
| 1 | `Error Out` |
| 1 | `no error out` |
| 1 | `vi error out` |
| 2 | `filtered error details` |
| 2 | `Test Method Error` |
| 1 | `Constructor Error` |

Plus **18 fabricated names** — 14 × `source`, 4 × `control_<fp_dco_uid>` — where
the label could not be read and the server invented one rather than reporting it
as unknown. Verified against raw VI bytes: `setUp.vi`, reported as `control_262`,
literally contains `error out`; the 14 `Built Project Integration` VIs reported
as `source` have compressed string sections and contain no such literal at all,
so `source` is being synthesized from the error cluster's own third field name.
See `lvkit-mcp-improvements.md` for the defect write-up; §2.4 above for the
structural fix.
