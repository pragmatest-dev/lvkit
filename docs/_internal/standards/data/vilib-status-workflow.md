# vilib Status Workflow

The `status` field tracks resolution progress for vilib VI entries.

## Status Values

| Status | Count | Meaning |
|--------|-------|------|
| `needs_terminals` | ~2000 | From PDF. Has name/description but no terminal `index`/`direction` |
| `needs_review` | ~900 | Has terminal indices. `python_code` may need verification |
| `complete` | ~5 | Verified and production-ready |

## Auto-Discovery

`vilib_resolver.auto_update_terminals()` learns terminal `index` and `direction` in memory, for the current resolver only, when code generation encounters wired terminals. It never writes the bundled JSON; promoting a learned value (`needs_terminals` → `needs_review`) is a hand edit.

## Workflow

1. PDF extraction creates entries with `needs_terminals`
2. Code generation encounters VI, `auto_update_terminals()` learns indices in memory; a developer copies confirmed ones into the JSON → `needs_review`
3. Developer verifies `python_code` works → `complete`
