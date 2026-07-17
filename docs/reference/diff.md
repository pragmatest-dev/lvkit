# diff

Compare two versions of a VI: added/removed terminals, changed operations, and
rewired connections. `diff` never requires primitive or vi.lib mappings, so it
works on any two VIs out of the box. Useful in code review and CI to see what
actually changed in a binary `.vi` file, not just that it changed.

Output is picked along two independent axes: `--format` selects the
serialization (`text`, `json`, or `html`), and `-v`/`--verbose` selects the
detail level within `text`. They compose independently — `--format` never
implies a detail level, and `--verbose` has no effect on `json` or `html`.

## Synopsis

```bash
lvkit diff <vi_a> <vi_b> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `vi_a` | Path to the first `.vi` file. |
| `vi_b` | Path to the second `.vi` file. |

## Options

| Option | Description |
|--------|-------------|
| `--format {text,json,html}` | Output serialization. `text` (default) is a unified diff of the two VIs' [`describe`](describe.md) output, printed to stdout. `json` serializes the diff engine's UID-correlated change map (`ChangeMap.to_dict()`) — for scripts, CI, or an AI agent reading the diff. `html` writes a self-contained [interactive diff viewer](#interactive-diff-viewer) to a file. |
| `-v`, `--verbose` | Show the structured change report instead of the compact unified diff. Only affects `--format text` — a detail level, not a format. |
| `--long` | Back-compat alias for `--verbose`. |
| `-o FILE`, `--output FILE` | Output file path. Used by `--format html` (default `outputs/vi-diff/<stemA>__<stemB>.html` when omitted) and `--format json` (prints to stdout instead when omitted). Has no effect on `--format text`, which always goes to stdout. |
| `--open` | Render `--format html` and open it in a browser. With no explicit `--format`, `--open` resolves the format to `html`; combined with `--format text` or `--format json` it's an error (`Error: --open requires --format html`, exit `1`). |
| `--load-mode {none,minimal,full}` | How deep to load dependencies when resolving SubVI signatures. Default `minimal` — enough to compare the diagram accurately without walking the full SubVI tree. |
| SubVI resolution flags | `--search-path`, `--project-root`, `--vilib`, `--userlib`, `--no-auto-vilib` — see [SubVI & vi.lib resolution](subvi-resolution.md). |

`--format` is becoming lvkit's house convention for output selection across
subcommands (a single flag for mutually exclusive output projections, rather
than one boolean per format); other commands' existing `--json` booleans will
migrate to it over time, with `--json` kept as an alias.

## Example

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted
```

```text
--- Convert File Extension (String)__ogtk.vi
+++ Convert File Extension (Path)__ogtk.vi
@@ -1,31 +1,19 @@
-# Convert File Extension (String)__ogtk.vi
+# Convert File Extension (Path)__ogtk.vi

-  convert_file_extension_(string)__ogtk(file name: str, new ending (none): str) -> new filename: str, prev ending: str
+  convert_file_extension_(path)__ogtk(file name: Path, new ending (none): str) -> new filename: Path, prev ending: str

 ## Inputs
-  file name: str (unknown)
+  file name: Path (unknown)
   new ending (none): str (unknown)

 ## Outputs
-  new filename: str
+  new filename: Path
   prev ending: str

-## Constants
-  (unnamed): str = '\\.[~\\.]*$'
-
-## Control Flow
-  Case structure (2 frames, gated on new ending (none))
+## Dependencies
+  Convert File Extension (String)__ogtk.vi: (file name: str, new ending (none): str) → (new filename: str, prev ending: str)

 ## Operations
-Match Pattern [prim 1535]
-Case Structure (2 frames)
-  Frame "Default" (default):
-    Match Pattern [prim 1535]
-    Less Than 0? [prim 1118]
-    Select [prim 1516]
-    Format String
-    (unnamed): str = '^\\.'
-    (unnamed): str = '.%s'
-    (unnamed): str = '%s'
-  Frame "":
-    (pass-through)+Strip Path [prim 1420]
+Convert File Extension (String)__ogtk.vi(new ending (none), file name) → prev ending, new filename
+Build Path [prim 1419]
```

Pass `-v`/`--verbose` (or its back-compat alias `--long`) for the structured
report instead:

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted --verbose
```

```text
Signature:
  ~ input: file name: str -> Path
  ~ output: new filename: str -> Path

Operations:
  + Build Path [prim]
  + Convert File Extension (String)__ogtk.vi [iUse]
  - Match Pattern [prim]
  + Strip Path [prim]
  - regular expression [select]

Constants:
  - (unnamed str) = "'\\\\.[~\\\\.]*$'"

Wiring:
  + Convert File Extension (Path)__ogtk.vi -> Convert File Extension (String)__ogtk.vi
  + Convert File Extension (String)__ogtk.vi -> Build Path
  + Convert File Extension (String)__ogtk.vi -> Convert File Extension (Path)__ogtk.vi
  - Convert File Extension (String)__ogtk.vi -> Match Pattern
  - Convert File Extension (String)__ogtk.vi -> regular expression
  - Match Pattern -> Convert File Extension (String)__ogtk.vi
  + Strip Path -> Convert File Extension (String)__ogtk.vi
  - regular expression -> Convert File Extension (String)__ogtk.vi

Structures:
  - regular expression (selector <- Convert File Extension (String)__ogtk.vi; frame Default: Match Pattern, Less Than 0?, Select, Format String, str constant, str constant, str constant)
```

The structured report has five sections — `Signature`, `Operations`,
`Constants`, `Wiring`, `Structures` — each listing only the entries that
changed; a section with no changes is omitted entirely.

If the two VIs are identical, `--format text` (both the default and
`--verbose`) prints a single line, `No changes detected.`, instead of an empty
diff/report. `--format json` and `--format html` still produce output when the
VIs are identical — an empty `changes` list, or a viewer with nothing in the
change list to select.

## JSON output

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted --format json
```

Prints the diff engine's `ChangeMap` as JSON: a `changes` list — one entry per
added/removed/modified node, wire, or structure, each with `uid`, `full_id`,
`kind`, `change` (`added`/`removed`/`modified`), `label`, `detail`, and diagram
geometry (`bounds`, `path`, and for a modified element the prior-version
`bounds_base`/`path_base`) — plus `common_nodes`, the count of nodes matched
unchanged across both VIs. This is the same change map `--format html` renders
into the viewer below, and what an editor integration or AI agent should parse
instead of scraping `text` output.

## Interactive diff viewer

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted --open
```

This writes `outputs/vi-diff/Convert File Extension (String)__ogtk__Convert File Extension (Path)__ogtk.html`
and opens it in the browser. The file is a single self-contained HTML page —
no server, no external JS/CSS — so it works equally well opened locally, sent
in a review, or hosted as a static CI artifact.

- Four view modes, switchable from the toolbar: **onion-skin** (the default —
  overlays both VIs with an opacity slider), **head**, **base**, and
  **side-by-side**.
- A numbered change list in the sidebar covers every added/removed/modified
  node or wire; each entry's number is drawn as a badge on the diagram next to
  the element it correlates with.
- Clicking a change in the list (or its badge on the diagram) jumps to it;
  **prev**/**next** buttons, or the `p`/`n` keys, step through the list in
  order.
- Selecting a change spotlights it — the rest of the diagram dims — and
  zooms/centers on it. If the change lives inside a case or sequence frame
  that isn't the one currently showing, the viewer switches that frame into
  view first, in both panes, so a change never hides behind an unselected
  case.
- Independent zoom (toolbar buttons, `+`/`-` keys, or Ctrl/Cmd+scroll) is
  shared across all four view modes.
- The URL fragment encodes the selected change, view mode, and per-structure
  frame selection (`#c=N&view=overlay&frame=UID=VALUE`), so a link into the
  viewer can deep-link straight to a specific change.

## Exit codes

`diff` exits `0` whenever it successfully compares the two VIs — **including
when they're identical**. The exit code does not tell you whether the VIs
differ; a CI step that needs to gate on "did this VI change" has to check the
printed output (`No changes detected.` vs. a diff/report), or parse the
`json`/`html` output, rather than the exit code.

`diff` exits `1` when `vi_a` or `vi_b` doesn't exist, when `--open` is
combined with `--format text`/`--format json`, or when loading either VI
raises a `ValueError`, `FileNotFoundError`, or `KeyError` (for example, a path
that isn't a `.vi` file) — each of these prints a one-line `Error: ...`
message to stderr. A `.vi` that pylabview itself can't parse (corrupt or
truncated data) raises `RuntimeError`, which isn't one of the caught types:
`diff` still exits `1`, but stderr gets a full Python traceback instead of a
clean `Error:` line.

## Notes

- `diff` compares two individual `.vi` files. To cover a change set in CI,
  invoke it once per changed VI from your pipeline.
- `diff` runs without a `.lvkit/` resolution store — pass `--project-root` if
  one exists, but `diff` doesn't require [`setup`](setup.md) to have been run
  first.
- If a SubVI referenced by either VI isn't found on `--search-path`, `diff`
  doesn't fail: that SubVI is recorded as an unresolved dependency and the
  comparison proceeds with what could be loaded.
- Because `vi.lib` auto-detection uses whatever LabVIEW is installed on the
  machine (see [SubVI & vi.lib resolution](subvi-resolution.md)), pass
  `--no-auto-vilib` for a diff that's identical across machines and in CI.

## See also

- [describe](describe.md) — a snapshot of a single VI's signature and operations.
- [render](render.md) — a faithful diagram of a single VI, for visual comparison.
- [structure](structure.md) — the same terminal/operation/wiring breakdown for `.lvlib`/`.lvclass` members.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the resolution flags `diff` shares with the other commands.
