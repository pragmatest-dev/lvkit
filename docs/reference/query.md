# query

Run read-only SQL over a repo's code-understanding index and get back just the
answer — a project-wide question (e.g. "what names does this project use for
error indicators?") answered as a small table instead of a dump of rows.

## Synopsis

```bash
lvkit query <path> "<SELECT …>"
lvkit query <path> --schema
lvkit query <path> "<SELECT …>" --format json
```

`<path>` is any file or directory inside the repo — a directory, `.lvproj`,
`.lvlib`, `.lvclass`, or `.vi`. Its **enclosing project** is queried, so the
index always covers the whole repo. Build the index first with `lvkit index`
(or let ordinary `describe`/`render`/`generate` runs warm it as you work);
querying an unindexed project fails with a clear message.

## Options

| Option | Description |
|--------|-------------|
| `--schema` | List the queryable views and their columns, then exit (ignores the SQL argument). |
| `--format {table,json}` | Output format. `table` (default) prints an aligned text table; `json` prints `{columns, rows, row_count, truncated}`. |

The SQL argument is optional only when `--schema` is given; otherwise it is
required.

## Building & refreshing the index

`query` reads a persisted index. You rarely build it by hand — `query` (and the
`callers`/`callees`/`blast-radius` commands) build it on first use and
incrementally refresh it before each read, and ordinary `describe`/`render`/
`generate`/`docs` runs warm it as you work. To build or refresh it explicitly:

```bash
lvkit index <path>              # build (or, with --refresh, incrementally update)
lvkit index <path> --refresh    # rebuild only content-changed/added VIs; drop deleted
```

`<path>` resolves to its enclosing project, and the whole repo is indexed
(path-keyed, so same-named VIs like `setUp.vi` ×17 never collide). A refresh is
keyed by each VI's content hash, so it only re-parses what actually changed.
Pass `--no-refresh` to `query`/`callers`/… to skip the pre-read refresh (faster,
but results may be stale if a VI changed since the last build).

## The views

Query these curated views (run `lvkit query <path> --schema` for the exact
columns of each):

| View | One row per | Key columns |
|------|-------------|-------------|
| `vi` | indexed VI | `path`, `name`, `qualified_name`, `library`, `is_stub`, `impact_score` |
| `terminal` | connector-pane terminal | `vi_path`, `name`, `direction`, `is_indicator`, `is_error_cluster`, `py_type`, `field_names` |
| `constant` | block-diagram constant | `vi_path`, `value`, `label`, `py_type`, `wired_to` |
| `call` | call edge | `caller_path`, `callee_key` |
| `type_use` | type reference | `vi_path`, `type_key` |
| `class_fact` | class-member VI | `vi_path`, `owning_class`, `parent`, `scope`, `is_accessor`, `accessor_field` |

Reachability questions ("what calls this?", "what breaks if I change it?") are
**not** SQL — they're a graph walk, so they're separate commands, not views:
[`lvkit callers`](callers.md) / `lvkit callees` / `lvkit blast-radius` (or the
matching MCP `get_callers` / `get_callees` / `blast_radius` tools). For a quick
count without the full list, `impact_score` on the `vi` view is a precomputed
transitive-dependent count.

## Example

The driving question — the names a project uses for error indicators, as a
histogram:

```bash
lvkit query MyRepo \
  "SELECT name, COUNT(*) AS n FROM terminal
   WHERE is_error_cluster=1 AND direction='output'
   GROUP BY name ORDER BY n DESC"
```

```text
name                    n
----------------------  ---
error out               352
Error out               2
vi error out            1
…
```

The `GROUP BY` returns the *answer* — a handful of rows — rather than every
matching terminal. `--format json` gives the same data for piping into another
tool.

## Read-only by construction

`query` opens the index database read-only and rejects anything that isn't a
single `SELECT`/`WITH`:

- writes (`INSERT`/`UPDATE`/`DELETE`/`DROP`/`CREATE`), `PRAGMA`, and `ATTACH`
  are refused;
- a second, stacked statement (`SELECT …; DROP …`) is refused;
- a long-running query is cut off by a time limit, and results are row-capped
  (`truncated` reports when the cap was hit).

A rejected or failing query prints `query error: …` to stderr and exits `2`.

## Notes

- The index is stored per project root under
  `~/.lvkit/cache/index/projects/<slug>/index.db` (SQLite/WAL), rebuilt cheaply
  from the content-hash-keyed extraction cache.
- **The views are the interface; you never query the tables.** They are a
  curated layer that decouples callers from the physical schema, so the tables
  can change underneath without breaking your SQL. Pre-1.0, the views
  themselves may still evolve — but they are the intentional, documented seam,
  and `--schema` always reports the current shape. The SQL dialect is SQLite's.
- The MCP server exposes the same surface as its `query` / `query_schema` tools
  — see [mcp](mcp.md).

## See also

- [CLI reference](index.md) — the map of every `lvkit` command (`lvkit index`
  builds the index `query` reads).
- [mcp](mcp.md) — the same query surface for an AI agent.
- [describe](describe.md) — deep, single-VI inspection when SQL isn't the right
  grain.
