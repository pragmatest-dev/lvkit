# callers / callees / blast-radius

Answer call-graph and change-impact questions over a repo's code-understanding
index: *who calls this VI, what does it call, and what breaks if I change it?*
These are graph walks, not SQL — [`query`](query.md) handles the relational
facts; these three commands handle reachability.

## Synopsis

```bash
lvkit callers      <vi> <project>
lvkit callees      <vi> <project>
lvkit blast-radius <vi> <project> [--depth N]
```

- `callers` — VIs that call `<vi>` directly (who depends on it).
- `callees` — VIs `<vi>` calls directly (its dependencies).
- `blast-radius` — the **transitive** set of dependents ("what breaks if I
  change this?"), with a count. `--depth N` bounds the search to N hops.

`<vi>` may be a path, a qualified name (`Foo.lvclass:run.vi`), or an unambiguous
bare name. `<project>` is any path inside the repo; its enclosing project's
index is used. Call edges are pure VI→VI — a class method's owning class is
never counted as a caller.

## Options

| Option | Applies to | Description |
|--------|-----------|-------------|
| `--depth N` | `blast-radius` | Bound the transitive search to N hops (default: unbounded). |
| `--format {table,json}` | all | `table` (default) or JSON. `callers`/`callees` JSON is a list of paths; `blast-radius` JSON is `{vi_key, dependents, impact_score}`. |
| `--no-refresh` | all | Use the stored index as-is without refreshing first (faster, may be stale). |

Like `query`, these build/refresh the index before reading (see
[Building & refreshing the index](query.md#building--refreshing-the-index)).

## Example

```bash
lvkit blast-radius "source/JKI Reuse/Clear All Errors … .vi" MyRepo
```

```text
73 transitive dependent(s) of …/Clear All Errors … .vi:
  …/Ant Plugin/Source/VI Tester JUnitXML Example.vi
  …/Classes/TestCase/closeMethodViReference.vi
  …
```

For a count without the full list, `impact_score` on the `vi` view
(`lvkit query <project> "SELECT name, impact_score FROM vi ORDER BY impact_score
DESC LIMIT 10"`) is a precomputed transitive-dependent count.

## Notes

- An unindexed project prints an error to stderr and exits `2`.
- These CLI commands have **no MCP tool twin** — the MCP server answers the
  same questions as SQL over the `node` view's `callee_path` column: direct
  callers are `SELECT DISTINCT vi_path FROM node WHERE callee_path='<path>'`,
  direct callees are `SELECT callee_path FROM node WHERE vi_path='<X>' AND
  kind='vi'`, and transitive blast radius is a `WITH RECURSIVE` over
  `callee_path` (`vi.callers_count`/`vi.impact_score` give the counts without
  the walk). See [mcp](mcp.md).

## See also

- [query](query.md) — read-only SQL for the relational facts (terminals,
  constants, symbols, types).
- [visualize](visualize.md) — the call graph as a diagram.
- [mcp](mcp.md) — the same operations for an AI agent.
