# Changelog

lvkit follows semantic versioning.

## [Unreleased]

## [0.7.0] - 2026-08-24
- **New: the VS Code extension runs in web / hosted editors** (vscode.dev, GitHub
  Codespaces web, GitLab Web IDE, Cursor) — rendering `.vi` diagrams, SubVI
  navigation, and visual diffs via lvkit in WebAssembly (Pyodide), no native
  binary or local disk. A 5th target; the desktop builds are unchanged.
- **The diagram now defaults to the light LabVIEW palette** regardless of OS or
  editor theme; the viewer chrome still follows its host (OS in a browser, VS
  Code theme in the editor). Toggle light/dark with the ☀/☾ button — the old
  "auto" diagram mode (and the `auto` value of `lvkit.diagramTheme`) is gone.
- **The web viewer now caches renders and diffs**, so reopening a VI in a session
  is instant — every render/diff path now shares one cache.
- **Rendering fidelity — many fixes so a diagram matches LabVIEW more closely:**
  nested structures occlude and clip their contents correctly (#35, #39); Case
  structures and stacked sequences show the saved-visible frame (#30); hidden
  loop terminals are respected and auto-indexing/concatenating tunnels draw per
  their mode (#34, #38); Disable-structure frame labels and per-subtype styling
  (#31); free-label comments and decorations render (#32); structure tunnel wire
  bends and byte-exact string constants (#37); enums take a coercion dot (#33);
  correct Bundle/Unbundle-By-Name field names (#36); and a SubVI call into its
  own library/class resolves to the caller's sibling (#29).
- **Fix: the connector-pane aside (▦) no longer balloons with zoom** — pinned
  outside the diagram and clamped to the view, like the help tip (#58).
- **The extension always uses its bundled lvkit** (never a repo `.venv`) so
  render, diff, and MCP share one cache; incompatible builds now coexist by
  fingerprint instead of re-extracting (#57).

## [0.6.3] - 2026-08-19
- **Fix: VI icons now render in color.** The renderer read the 1-bit
  black-and-white icon layer, so a class/library banner color was dropped. It now
  prefers the color layer (256-color `icl8`, then 16-color `icl4`, then the B&W
  fallback), in both the block-diagram render and the generated HTML docs, so an
  icon matches what LabVIEW shows.
- **Fix: SubVIs that share a filename across libraries each render their own
  icon.** Two VIs both named `Do.vi` in different libraries (`Lib1.lvlib` vs
  `Lib2.lvlib`) drew the same icon — the second showed the first's — because the
  icon source was resolved by bare filename. It is now resolved by the SubVI's
  project-relative path, so each gets its own.
- **Fix: rendered SVG/HTML and visual diffs no longer embed the source file's
  absolute path.** VI titles, node ids (`data-node`), and the diff before/after
  labels used the on-disk path; they now use the VI's qualified name, so a saved
  or hosted render/diff carries no local filesystem path (the render identity is
  unchanged — this is display-only).
- **Fix: `Reverse 1D Array` (1902) and `Format Value` (1540) were mislabeled.**
  They are actually **Transpose 2D Array** and **Array To Spreadsheet String** —
  corrected, with the right terminal roles and generated Python. Re-generate any
  Python produced from a VI that used either.
- **Fix: connector-pane pattern 4817** (`special-7`) was transcribed on the wrong
  row grid, so its middle terminal drew at the wrong height in the connector-pane
  view; corrected.

## [0.6.2] - 2026-08-18
- **Fix: corrected three mislabeled comparison primitives.** `Greater?` (1104),
  `Less?` (1110), and `Less Or Equal?` (1111) were each mapped to their logical
  complement, so generated code inverted `>`/`<`/`<=`/`>=` comparisons. The
  mapping is now the correct set of complementary pairs (`==`/`!=`, `<=`/`>`,
  `>=`/`<`).

## [0.6.1] - 2026-08-17
- **MCP: render and diff a VI as an interactive HTML viewer.** New `render` and
  `diff` MCP tools return a self-contained, theme-aware HTML viewer — the
  faithful block diagram / visual before-after — written to the per-user cache
  and returned as a path (not inlined, so it never floods the model's context).
  This is the visual you cannot reconstruct from the netlist. The `describe`
  tool is **removed from the MCP surface** — its prose is a lossy projection of
  `read_vi`, whose description now instructs the model to interpret the netlist
  and state what the VI *does*; `describe` remains a CLI command. The shared diff
  core is factored out of the renderer so the CLI and MCP produce the same body.
- **Friendlier Claude Code plugin names.** The published plugins are now
  `lvkit-mac-arm64`, `lvkit-mac-intel`, `lvkit-windows`, and `lvkit-linux`,
  installable from the `pragmatest` marketplace
  (`claude plugin marketplace add pragmatest-dev/claude-plugins`).

## [0.6.0] - 2026-08-17
- **New: ask a whole VI repo questions in SQL.** `lvkit query <path> "<SELECT>"`
  runs read-only SQL over the project's code-understanding index and prints just
  the answer — e.g. the names a project uses for error indicators, as a
  histogram instead of hundreds of rows:
  `lvkit query MyRepo "SELECT name, COUNT(*) AS n FROM terminal WHERE
  is_error_cluster=1 AND direction='output' GROUP BY name ORDER BY n DESC"`.
  Query the curated views `vi`, `terminal`, `constant`, `call`, `type_use`,
  `class_fact` (`--schema` lists their columns; `--format json` for machines).
  The connection is strictly read-only — writes, `PRAGMA`, `ATTACH`, and stacked
  statements are refused — and long queries are time- and row-capped. The MCP
  server exposes the same thing as the `query`/`query_schema` tools, which
  replace the older `find_terminals`/`find_constants`/`find_symbols`/
  `find_type_usages`/`get_signatures` tools (call-graph questions stay as
  `get_callers`/`get_callees`/`blast_radius`).
- **Fixed: `Divide`, `Subtract`, the ordered comparisons and the shift/scale
  primitives had their two inputs swapped.** `describe` reported the operands
  the wrong way round, and generated Python computed the inverse — `Divide`
  returned the reciprocal, `Subtract` flipped sign, `Less?`/`Greater?` inverted.
  19 entries are corrected. The connector-pane index space runs Bottom→Top, so
  the documented upper input `x` takes the *higher* index; these entries had
  been filled in from NI-doc listing order against ascending index. Commutative
  entries (`Add`, `Multiply`, `And`, `Or`, `Exclusive Or`, `Equal?`,
  `Not Equal?`) were affected too but showed no symptom, which is why this
  survived. **If you have generated Python from a VI using any of the
  non-commutative primitives, re-generate it.** A new gate
  (`tests/test_primitive_positional_order.py`) makes the invariant permanent.

## [0.5.8]
- **Fixes a broken fresh install** — capped `mcp<2`; mcp 2.0 removed the API the
  MCP server is built on, so a clean install had a dead server.
- **Diff now covers front-panel controls & indicators** — added, removed,
  retyped, or renamed connector-pane terminals show up.
- **Render/diff fidelity:** In-Place Element borders, class refnums labeled by
  class, front-panel terminal labels at their saved positions, corrected
  connector-pane indices, and class private-data field names.

## [0.5.7]
- **Re-opening or re-diffing a VI is now near-instant.** `render` and `diff`
  cache their output, so an unchanged VI skips the rebuild (render ~1.6 s →
  ~0.03 s, diff ~4 s → ~0.03 s), and every command starts quicker. Editing a VI
  (or a SubVI) rebuilds automatically; `--no-cache` forces it. The cache lives in
  `~/.lvkit/cache/`, stays bounded, and is safe to delete.
- **`render` accepts a directory** — a fast pass that caches every `.vi` under
  it. `-o` writes a file (or a mirrored tree); without it, the cache is warmed.

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
