# vilib Status Workflow

The `status` field tracks resolution progress for vilib VI entries.

## Status Values

| Status | Count | Meaning |
|--------|-------|------|
| `needs_terminals` | ~2000 | From PDF. Has name/description but no terminal `index`/`direction` |
| `needs_review` | ~900 | Has terminal indices. `python_code` may need verification |
| `complete` | ~5 | Verified and production-ready |

## Auto-Discovery

`vilib_resolver.auto_update_terminals()` automatically populates terminal `index` and `direction` when code generation encounters wired terminals. This moves entries from `needs_terminals` → `needs_review`.

## Workflow

1. PDF extraction creates entries with `needs_terminals`
2. Code generation encounters VI, `auto_update_terminals()` adds indices → `needs_review`
3. Developer verifies `python_code` works → `complete`
