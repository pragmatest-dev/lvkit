---
name: lvkit-query
description: Use when the user asks a project-wide (not single-VI) question about a LabVIEW repo — class hierarchy, who calls what, dead code, terminal/constant facts, `.lvproj` membership, error-handling conventions — "what classes inherit from X", "what VIs does nothing call", "what breaks if I change Y". Answers as SQL over lvkit's facts index, MCP `query` if connected else the `lvkit query` CLI.
allowed-tools: Bash, Read, Grep
---

# Query a LabVIEW repo

```bash
lvkit index <repo>
lvkit query <repo> "SELECT name, COUNT(*) AS n FROM terminal WHERE type_descriptor='Error' AND direction='output' GROUP BY name ORDER BY n DESC"
```

```
name        n
----------  --
error out   142
Error Out   37
error o     4
```

## Getting the facts

Prefer the MCP tools when the lvkit MCP server is connected: `index`,
`query`, and `query_schema` have identical CLI twins (`lvkit
index`/`lvkit query`). Reachability (callers/callees/blast radius) has **no
MCP tool of its own** — it's `query` over the `node` view's `callee_path`
column (see below), or the CLI's typed `callers`/`callees`/`blast-radius`
commands. `project`/`--project-root` default to the client's workspace root
over MCP; on the CLI, pass any path inside the repo.

## Build the index

`lvkit index <repo>` builds/refreshes the facts index once — a persisted,
path-keyed row per VI (so 17 different `setUp.vi` files never collide the
way a name-keyed lookup would). `lvkit query` and `lvkit callers/callees/
blast-radius` build or refresh it automatically on first use, so this step
is optional; run it explicitly to warm the cache before a batch of queries,
or with `--refresh` to incrementally update after files change. `lvkit query
--no-refresh` (or the `query` MCP tool with a stale index) reads the stored
index as-is, without re-scanning first — faster, may be stale.

## Query: the fact vocabulary

`lvkit query <repo> "<SQL>"` runs exactly one read-only `SELECT`/`WITH`
against seven curated views: `vi`, `terminal`, `constant`, `node`,
`type_use`, `class_fact`, `lvproj`. Writes, `PRAGMA`, `ATTACH`, and stacked
statements are refused structurally (a read-only DB handle plus a SQLite
authorizer), not by string-matching the query. Results are capped at 1000
rows and a 2s wall-clock budget; a truncated result says so.

**Call `lvkit query <repo> --schema` (or the `query_schema` MCP tool) before
writing SQL** — it lists every view's real column names and what each one
means, so you query against facts instead of guessing:

```bash
lvkit query <repo> --schema
```

The value of this skill is translating a plain-English question into the
right `SELECT` against those columns. Some worked examples:

**"What names does this project use for error indicators?"** (the driving
example — returns the small histogram, not the 406 raw terminal rows). Error
clusters are duck-typed in LabVIEW (no nominal type), so lvkit gives them the
built-in-style descriptor `Error` — match on shape via `type_descriptor`, not
on any assumed name:

```sql
SELECT name, COUNT(*) AS n FROM terminal
WHERE type_descriptor = 'Error' AND direction = 'output'
GROUP BY name ORDER BY n DESC
```

**"What VIs does nothing in this repo call?"** (dead code / entry points —
filter on `vi.callers_count`, not a name join against `node.qualified_name`,
which is often NULL or a bare filename and silently misfires):

```sql
SELECT path, name, library FROM vi WHERE callers_count = 0 ORDER BY name
```

**"What classes exist and how do they inherit?"**

```sql
SELECT owning_class, parent FROM class_fact
```

**"Which VIs are broken (bad node, bad subVI, failed compile)?"**

```sql
SELECT path, name FROM vi WHERE health_is_broken = 1
```

**"What's in VIUnit.lvproj?"** (`.lvproj` membership is many-to-many — a VI
can belong to several projects or none, so "the project" is ambiguous
without naming one):

```sql
SELECT member_name, member_type FROM lvproj
WHERE lvproj_name = 'VIUnit' AND member_type = 'LVClass'
```

**"Which enums carry a `setUp` member?"** (`enum_values` is a JSON array —
match it with `LIKE`):

```sql
SELECT vi_path, name FROM terminal WHERE enum_values LIKE '%"setUp"%'
```

## Reachability: `node` over MCP, typed commands on the CLI

Transitive call-graph questions ("who calls this, ever, transitively") have
no dedicated tool over MCP — they're `query` over the `node` view's
`callee_path` column. Direct callers of a VI:

```sql
SELECT DISTINCT vi_path FROM node WHERE callee_path='<abs path>'
```

Direct callees:

```sql
SELECT callee_path FROM node WHERE vi_path='<X>' AND kind='vi'
```

Transitive blast radius is a `WITH RECURSIVE` over `callee_path`;
`vi.callers_count` / `vi.impact_score` are precomputed columns for the
counts, so most impact questions don't need the walk at all.

On the CLI, the same questions have typed commands instead of SQL:

```bash
lvkit callers <vi> <repo>                    # direct callers
lvkit callees <vi> <repo>                    # direct callees
lvkit blast-radius <vi> <repo> [--depth N]   # transitive dependents — "what breaks if I change this?"
```

`<vi>` is a path, a qualified name, or an unambiguous bare name.
`blast-radius` returns `{vi_key, dependents, impact_score}` — `impact_score`
is `len(dependents)`. These CLI commands have no MCP tool twin — over MCP,
run the SQL above.

For a diagram instead of a list: `lvkit visualize <path> --mode deps`
(CLI-only, no MCP twin) draws a pyvis HTML dependency graph scoped to
whatever one input loads — not a whole-project view, and not available
through the MCP server today.

## Feeds

Cross-VI facts from `query` feed `/lvkit-convert` (what does this VI's
caller pass it?) and `/lvkit-document` (which VIs does nothing call, so the
docs site can flag them as entry points). The `node.callee_path` walk (or
`lvkit blast-radius`) feeds `/lvkit-review` (who's affected by this diff).
