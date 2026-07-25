# Changelog

lvkit follows semantic versioning.

## [0.5.3]
- **Extraction cache no longer written into your repo.** lvkit used to cache a
  VI's extracted XML under `<project>/.lvkit/cache/`, so opening a `.vi` just to
  view it created a `.lvkit/` in your working tree. The cache now lives in a
  per-user home, `~/.lvkit/cache/` (override with `$LVKIT_CACHE_DIR`) — nothing
  is written into the project. The `.lvkit/` **resolution store** (your authored
  mappings) is unchanged and still lives in the repo.
- Cache entries are now keyed by the VI's resolved source: VIs from a real
  `vi.lib`/`user.lib`/`instr.lib` install are shared across projects (one copy,
  keyed by the resolved root — so LabVIEW 32- and 64-bit installs don't collide),
  while a project's own and vendored VIs stay per-project. Content-hash
  invalidation is retained.

## [0.5.2]
- **Fix: lvkit reported the wrong version.** The 0.5.1 release bumped
  `pyproject.toml` but not the hardcoded `__version__` string, so the published
  package's metadata said `0.5.1` while `lvkit --version` and
  `lvkit.__version__` both reported `0.5.0`. Anyone checking which lvkit they
  were running — or gating on it, as the VS Code extension does — got a stale
  answer. All three version sites (`pyproject.toml`, `src/lvkit/__init__.py`,
  `uv.lock`) are now synchronized, and `test_version` asserts the code and the
  installed metadata agree.

  No functional changes to parsing, rendering, diffing, or codegen.

## [0.5.1]
- **Event structures:** lvkit now parses, renders, describes, diffs, and
  netlists LabVIEW Event Structures — the border band + timeout hourglass, the
  event data node, and per-frame event labels reconstructed from the heap
  (`[3] "copyrights": Value Change`). Event-type codes not yet clean-room
  confirmed surface as an explicit `<unknown event 0x…>` sentinel rather than a
  blank frame. **Codegen** refuses an event structure *loudly*
  (`raise NotImplementedError`, naming the events) instead of silently dropping
  the VI's behaviour — an asynchronous UI event loop has no headless equivalent.
- **Queue operations — complete.** Codegen now covers all six Queue ops: Obtain
  Queue, Enqueue Element, Enqueue Element At Opposite End, Dequeue Element,
  **Release Queue**, and **Get Queue Status** — backed by a stdlib-only runtime
  (`lvkit.labview_queue`) that models LabVIEW's real semantics: reference-counted
  lifecycle with force-destroy (pending ops raise error 1122), create-if-not-found
  (error 1100), bounded-blocking enqueue, and the full Get-Queue-Status pane.
- **Control-reference constants** render faithfully — drawn where they live with
  their wire kept local and correctly typed as a refnum, using a dedicated glyph
  (the referenced type in the box, control name above, reference arrow).
- **Fix:** removed a redundant sRN index-pairing pass that fabricated
  type-impossible internal edges (e.g. a boolean indicator "wired" across
  structure borders with no tunnel); genuine tunnels are untouched.
- Cloud render service updated for the 0.5.0 API and `format=html`.

## [0.5.0]
- **Diff:** `lvkit diff <a.vi> <b.vi>` — compare two VIs (terminals, operations,
  wiring) as a text diff or `--long` change report, plus an interactive HTML diff
  viewer with per-change spotlight and synced before/after panes.
- **Netlist:** a node-first text projection of a VI, shared by `describe
  --verbose`, `diff`'s text output, and the viewer's change tree.
- **MCP server:** `lvkit mcp` exposes the VI graph to AI agents (describe, render,
  diff, generate, and more) over the Model Context Protocol.
- **Project-local cache:** extraction XML is cached under `.lvkit/cache/`
  (content-hash invalidated, path-classified) instead of beside the `.vi`.
- **Auto SubVI resolution:** every command that resolves SubVIs
  (`describe`/`diff`/`generate`/`docs`/`render`/`visualize`) auto-detects the
  project root (nearest enclosing `.lvkit/`) and searches it, so project
  dependencies resolve with no `--search-path`.
- **`structure` accepts a `.lvproj`** — discovers the project from its declared
  member list (summary, `--json`, `--plan`).
- Numeric constants honor their LabVIEW display format in `describe`/`diff`/
  `netlist` (hex constants render as `x…`).
- lvkit is **read-only** on VIs — stated explicitly across every capability,
  including convert (it parses the VI and emits a separate file, never editing it).
- More resolved primitives; Merge Errors fix; extraction memory now bounded.

## [0.4.0]
- ***Block-diagram renderer:** `lvkit render <vi> -o out.svg` — produces a headless
  block-diagram SVG with interactive frames and procedural primitive shapes.
- Known limitation: a standalone `.vi` may under-resolve types (e.g. cluster
  field names) — render with `--search-path` / a project for full fidelity.

## [0.3.0]
- Formula Node support with LabVIEW-validated numeric semantics.

## [0.2.0]
- Published to PyPI; `lvkit setup`, visualization extra.

## [0.1.0]
- Initial release.
