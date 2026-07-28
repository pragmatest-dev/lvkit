# Changelog

lvkit follows semantic versioning.

## [0.5.6]
- **`diff`: a case/event frame whose selector VALUE changed (e.g. `"1"` → `"0"`)
  now reads as one *modification* instead of remove + add.** Frames are paired
  across versions by their contained nodes' dataflow identity, so a renamed
  frame that kept its contents is a single change. Uniform across
  case/sequence/event/disable structures.
- **Diff viewer: renamed frames reveal in BOTH panes at once.** Selecting the
  change, or toggling the case selector directly on the diagram, now lands each
  pane on its own side of the pairing (before `"1"`, after `"0"`) instead of
  driving the other pane to a value it doesn't have (which showed an empty frame).
- **Change list rows are uniform:** every row reads `<type of thing>
  <change-word>` (e.g. `case frame modified`, `node added`) with the specific
  value/name as truncated subtext — frames no longer show a bare quoted value.
- Renderer fixes: bundle/unbundle terminals no longer cross, cluster constants
  render at their natural size with default values, and missing-geometry gaps
  (floating wires) are closed. Viewer `Fit` fits both dimensions.

## [0.5.5]
- **Fix: `diff` overlay before/after scroll is now locked.** In overlay mode a
  native scroll (plain mouse wheel, scrollbar, trackpad, keyboard) on the active
  pane didn't move the other, so the two stacked diagrams slid out of alignment.
  Both panes now scroll together in overlay, matching split mode.

## [0.5.4]
- **`diff`: constants are now first-class changes.** An added, removed, or
  changed constant — at any nesting depth, including one inside a newly-added
  case frame — now shows a highlight box + numbered badge on the diagram, a row
  in the change list and tree, a JSON entry, and a count. Previously only a
  *modified* top-level constant was reported; added/removed constants (and any
  nested constant) were invisible on the diagram and missing from the counts.
- Added/removed constants highlight their feed wire on selection, the same way
  added/removed nodes do.
- A constant row shows its type (e.g. `float constant`) with a small-circle
  marker; the value stays out of the change list (a string can be arbitrarily
  long) but is kept in `--format json` and shown by `diff --verbose` and on the
  before/after diagrams.

## [0.5.3]
- **Fix: `render` / `diff` / `docs` crashed on Windows.** Output text wasn't
  UTF-8-pinned, so on Windows (cp1252) any non-Latin1 character in a diagram
  (e.g. the ◄ glyph) raised `UnicodeEncodeError` and left an empty file. All file
  I/O now pins `encoding="utf-8"`.
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
- **Cache dirs are readable, not hashed.** Each namespace is the project (or
  library) path with separators turned into `-`, the way cross-project tools
  name their per-repo dirs (`~/.claude/projects`: `-home-user-repo`) — so you
  can browse `~/.lvkit/cache` and see which repo, and which VI, every file came
  from. This also fixes a case where a VI inside a repo under your home
  directory collapsed into one `hash($HOME)` bucket (climbing past its own
  `.git`), because the global `~/.lvkit` was mistaken for a project marker.

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
