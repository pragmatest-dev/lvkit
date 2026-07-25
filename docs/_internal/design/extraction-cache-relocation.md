# Extraction cache: relocate out of the repo, key by resolved source

**Status:** proposed. Supersedes the in-repo-cache decision of #63 (keeps its
content-hash invalidation + classification; only changes *where* the cache lives
and *how* the source is identified).

## Problem

Today `_cache_target()` (`extractor.py:126`) writes extracted VI XML into
`<project-root>/.lvkit/cache/extracted/…`, and identifies a VI's "source" by
**substring-scanning its path** for `vi.lib` / `_OpenG.lib` / `instr.lib`
(`classify_vi`, `:106`). Two problems:

1. **It pollutes the user's repo.** Opening a `.vi` to *view* it sprouts a
   `.lvkit/cache/` in their working tree (gitignored, but still a surprise write,
   often into a repo that isn't even theirs). This is the extension's normal case.
2. **Substring classification is wrong for real layouts.** OpenG/drivers are
   frequently *vendored into a project tree* (`…/user.lib/_OpenG.lib/…` inside the
   repo) or dropped in an arbitrary folder (VIPM extraction). The marker says
   "shared library" while the *location* says "project artifact." And a
   user-supplied `--vilib D:\weird` with no literal `vi.lib` component isn't
   recognized as vi.lib at all. It also drops **bitness**: LV2025-64 and LV2025-32
   both key to `vilib/2025` and thrash.

## Key insight

A VI's cache identity is **which resolved root it came through**, not what its
path is named. lvkit already knows every root at run time — `vilib_root`
(`--vilib`/`detect_labview()`), `userlib_root` (`--userlib`/detected),
`instr.lib`, the **project root**, and `--search-path` dirs (`_parse_library_roots`,
`cli.py:120`) — but discards them before caching and re-guesses from the path.

Testing the vi_path by **prefix against the real roots** answers shared-vs-project
correctly where substring-matching cannot:

| Resolved through | Represents | Same bytes across projects? | Storage |
|---|---|---|---|
| `vilib_root` (outside project) | LabVIEW built-ins, pinned to an install (version+bitness+path) | yes, per install | **shared** |
| `userlib_root` (outside project) — incl. installed OpenG | add-ons in an install | yes, per install | **shared** |
| `instr.lib` (outside project) | drivers in an install | yes, per install | **shared** |
| project tree | the user's work | no | **per-project** |
| `--search-path` dir | project-configured dep location | usually no | **per-project** |
| vendored OpenG/driver *inside* the project | a project copy that happens to be OpenG | no | **per-project** |
| ad-hoc `/tmp/foo.vi`, no project | one-off | — | keyed by path hash |

Vendored OpenG resolves under the *project root*, not `userlib_root`, so
prefix-matching lands it per-project — correct, where the substring said "shared."

## Design

Global, per-user cache; nothing in the repo. Shared tier keyed by the **resolved
root's identity** (a hash of its absolute path — which already encodes
version + bitness + location, so LV2025-64 ≠ LV2025-32 ≠ `D:\weird` for free):

```
<global-cache>/                     # platformdirs.user_cache_dir("lvkit"), or $LVKIT_CACHE_DIR
  shared/
    vilib/<hash(resolved vilib_root)>/<rel>/…
    userlib/<hash(resolved userlib_root)>/<rel>/…
    instrlib/<hash(resolved instr root)>/<rel>/…
  projects/<hash-or-slug(project_root)>/<rel>/…   # project tree, search-path, vendored libs
  adhoc/<hash(abspath)>/…                          # no project
  llb/…                                            # LLB cache (was _LLB_CACHE_ROOT)
```

- `<rel>` = the VI path relative to its root (browsable, per-project-clearable).
- **Content-hash stays for *invalidation*** (with the mtime/size fast-path), not as
  the primary key — keeps hits cheap and the layout human-readable.
- `.lvkit/` **store** (authored resolution data) is untouched and stays in the repo.

### Mechanism (the only real change)

Make the run's resolved roots available where the cache key is computed — a small
context object set once at CLI/MCP entry, mirroring how `reset_resolver()`
(`primitive_resolver.py:669`) already wires the discovered project store. Then:

- New `global_cache_root()` → `$LVKIT_CACHE_DIR` if set, else
  `platformdirs.user_cache_dir("lvkit")`. (`LVKIT_CACHE_DIR` doubles as the test
  hook and a power-user/CI override.)
- Rewrite `_cache_target(vi_path)`: prefix-match `vi_path` against the context's
  roots → pick tier + namespace as above.
- Replace `classify_vi`'s substring markers with this prefix match. Delete
  `extraction_cache_root()` (in-repo creator + auto-`.gitignore`), `_CACHE_ROOT`,
  `_default_temp_cache_dir`; repoint `_LLB_CACHE_ROOT`.
- When no context/roots are available (a bare `resolve_extracted(path)` with no
  run context), fall back to: under a discovered project root → `projects/…`;
  else `adhoc/<hash>`. Never the repo.

### Untouched

`.lvkit/` store, `project_store.py` / `find_project_store`, `_data.py`,
`VilibResolver`, `PrimitiveResolver`, resolution/override layering, and the
`.lvkit/cache/samples` **source** corpus (dev-only; its *extracted* XML just lands
in the global cache under a `shared`/`projects` namespace like anything else).

### Migration

Clean break — first run re-extracts into the global cache (cheap). A leftover
`<repo>/.lvkit/cache/` from ≤0.5.2 is safe to `rm -rf` (offer in `lvkit setup`);
that's `cache/` only, **not** the `.lvkit/` store.

### Ship

lvkit 0.5.3 + extension 0.1.6 (rebundle ≥0.5.3, bump `MIN_LVKIT`). Extension needs
no cache plumbing — global is the default, so previews never touch the repo. Fix
docs calling `.lvkit/cache/` the extraction home (CLAUDE.md, README, store README
template).

## Test strategy — prove ALL commands + MCP still work

### 0. Hermetic cache fixture (conftest, autouse)
Point `LVKIT_CACHE_DIR` at a per-test `tmp_path` so tests never touch the real
`~/.cache/lvkit` and each starts cold. Provide a helper to run a command inside a
throwaway **git repo** dir (to assert the repo stays clean).

### 1. Cache-key unit tests (`tests/test_extraction_cache.py`, new)
Drive `_cache_target()` / the new classifier directly:
- no project → `adhoc/<hash>`.
- VI under project root → `projects/…`.
- VI under a `vilib_root` (in context) → `shared/vilib/<root-hash>/…`.
- VI under `userlib_root` → `shared/userlib/…`.
- **Vendored OpenG** (path under project, contains `_OpenG.lib`, NOT under
  `userlib_root`) → `projects/…`, **not** shared. *(regression for the substring→
  prefix fix — the crux.)*
- Two distinct vilib roots (simulate LV2025-64 vs -32 as two dirs) → **distinct**
  `shared/vilib/*` namespaces, no collision.
- content edit → re-extract; `touch` only → hit via mtime/size fast-path.

### 2. Repo-cleanliness invariant (the whole point)
Run the **full command matrix** on a VI inside a temp git repo; assert **no
`.lvkit/cache/` appears in the repo** and the cache landed under `LVKIT_CACHE_DIR`.
Repeat with `LVKIT_CACHE_DIR` unset → lands under `platformdirs` dir, still not the
repo.

### 3. Every CLI command still works (parametrized, `needs_samples`)
Subprocess-run each on a sample VI and assert exit 0 + expected artifact:
`describe`, `render` (svg **and** html), `diff`, `docs`, `visualize`, `generate`,
`structure`, `detect`, `setup`, `mcp` (starts + one tool call). Most exist
(`test_cli`, `test_diff`, `test_render`, `test_docs_render`, `test_e2e_codegen`) —
add smoke coverage for the gaps (`describe`, `structure`, `detect`, `visualize`).

### 4. Output-parity + cache-hit correctness
For each command: **cold run vs warm (second) run → byte-identical output**, and
the warm run is a **cache hit** (assert extraction wasn't redone — e.g. cache-dir
mtime unchanged, or an extraction counter). `test_determinism.py` already guards
byte-stability; extend it to run twice (cold/warm).

### 5. MCP — all 12 tools (`tests/test_mcp_cache.py`, new or extend)
Exercise every tool against the new cache (in-process handlers or a spawned
server): `load`, `list_loaded`, `clear`, `get_context`, `describe`,
`get_operations`, `get_dataflow`, `get_structure`, `get_constants`,
`generate_ast_code`, `generate_documents`, `generate_python`. Assert each succeeds
and (for the stateless generate/documents/analyze paths) that output matches the
CLI equivalent — same extraction path, so this is mainly a smoke + parity check.

### 6. Shared-reuse test
Two separate project dirs both referencing the **same** vilib VI (same
`vilib_root`) → the vilib extraction is written **once** under `shared/vilib/…`
and reused by both; each project's own VIs stay under its own `projects/…`. Assert
one shared entry, no duplication.

### Runbook
`uv run pytest -q` (full suite green, incl. new files) → `ruff check .` →
`uv run pyright src/`. Manual: open a `.vi` from a git repo in the extension →
confirm no `.lvkit/` appears and the diagram renders; `lvkit render` the same VI
twice → second is instant (warm shared cache).
