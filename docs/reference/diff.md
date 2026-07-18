# diff

Compare two versions of a VI: added/removed terminals, changed operations, and
rewired connections. `diff` never requires primitive or vi.lib mappings, so it
works on any two VIs out of the box. Useful in code review and CI to see what
actually changed in a binary `.vi` file, not just that it changed.

Output is picked along two independent axes: `--format` selects the
serialization (`text`, `json`, or `html`), and `-v`/`--verbose` selects the
detail level within `text`. They compose independently — `--format` never
implies a detail level, and `--verbose` has no effect on `json` or `html`.

Both `text` tiers, and `json`/`html`, project the SAME UID-keyed change map —
`diff` matches nodes and structures by their stable LabVIEW UID (not by name),
so an added or removed instance of a repeated node name is never confused with
a rewire of an unrelated one, and a node merely wrapped in a new structure or
handed a fresh UID never shows up as a phantom change. `-v`/`--verbose` only
adds DEPTH to the same set of changes — it never shows a different diff.

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
| `--format {text,json,html}` | Output serialization. `text` (default) is a concise, logical change summary — node/structure add/remove, wire connectivity, constant value changes — printed to stdout. `json` serializes the diff engine's UID-correlated change map (`ChangeMap.to_dict()`) — for scripts, CI, or an AI agent reading the diff. `html` writes a self-contained [interactive diff viewer](#interactive-diff-viewer) to a file. |
| `-v`, `--verbose` | Show the change summary in full depth: a `Signature` section for the VI's own connector-pane interface, old→new detail on modified values, and a trailing unchanged-node tally. Both tiers already show the full recursive containment tree — `--verbose` adds depth, never a different shape. Only affects `--format text` — a detail level, not a format. |
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

`text` output is a single recursive containment tree, rendered as a
**netlist** — the same node-first, named-port syntax `describe --verbose`'s
`## Netlist` section uses (see [Netlist](netlist.md)). Every change reads
inline as a change tag (`+`/`-`/`~`) in column 0, followed by netlist syntax
at whatever depth its own containment puts it: a structure's own line reads
`case (selector):` (or `while (...):`/`for (...):`/`sequence:`), and a
node's own line reads `name(port=net, ...) -> outNets` — the node leads,
each wired input bound to its source net by port name, never by position. A
structure is a recursion point: its own scope line, then a quoted
sub-header per affected frame (`"Default":`), with that frame's changes
indented beneath it on their own lines, recursing into nested structures. An
unchanged structure that merely CONTAINS a change still prints its own scope
line (with a space gutter, not a change tag), so nested changes always show
their enclosing `case (...):` context. There are no
`Operations:`/`Wiring:`/`Structures:` sections. Siblings inside a container
print in the VI's own dataflow order (the order the current/head version
places them), not grouped by change kind — a genuinely new node can print
before a genuinely removed one, or the reverse, depending on where each one
falls.

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted
```

```text
+ Strip Path(path=file name) -> stripped path, name
+ Convert File Extension (String)__ogtk.vi(new ending (none)=new ending (none), file name=name) -> prev ending, new filename
+ Build Path(name or relative path=new filename, base path=stripped path) -> appended path
- Match Pattern#1(string=file name, regular expression='\\.[~\\.]*$') -> before substring, match substring, after substring, offset past match
- case (new ending (none)):
    "Default":
-     Match Pattern#2(string=new ending (none), regular expression='^\\.') -> before substring, match substring, after substring, offset past match
-     Less Than 0?(x=Match Pattern#2.offset past match) -> result
-     Select#2(f_value='%s', selector=Less Than 0?.result, t_value='.%s') -> result
-     Format String(0=Select#2.result, 1=Match Pattern#1.before substring, 5=new ending (none)) -> Format String.2
- = (unnamed str) = "'\\\\.[~\\\\.]*$'"
```

The renamed VI now calls the old one as a SubVI (`+ Convert File Extension
(String)__ogtk.vi(new ending (none)=new ending (none), file name=name) ->
prev ending, new filename`) instead of inlining its old `case (new ending
(none)):` logic (`- case (new ending (none)):`) — everything the removed
case used to contain (`Match Pattern#2`, `Less Than 0?`, `Select#2`, `Format
String`) nests under its `"Default":` frame, indented beneath the
structure's own line. The OTHER `Match Pattern` instance (`Match Pattern#1`)
— the one that lived outside the case and is unrelated to it — sits at the
top level instead, right alongside the genuinely new/removed nodes. Note the
ordering: the three ADDED nodes print first — that's where the renamed VI's
own dataflow places them — then the removed case and the removed `Match
Pattern#1`, last because they exist only in the OLD version and have no
position in the new one's order.

Pass `-v`/`--verbose` (or its back-compat alias `--long`) for the SAME tree,
plus more depth:

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted --verbose
```

```text
Signature:
  ~ input: file name: str -> Path
  ~ output: new filename: str -> Path

+ Strip Path(path=file name) -> stripped path, name
+ Convert File Extension (String)__ogtk.vi(new ending (none)=new ending (none), file name=name) -> prev ending, new filename
+ Build Path(name or relative path=new filename, base path=stripped path) -> appended path
- Match Pattern#1(string=file name, regular expression='\\.[~\\.]*$') -> before substring, match substring, after substring, offset past match
- case (new ending (none)):
    "Default":
-     Match Pattern#2(string=new ending (none), regular expression='^\\.') -> before substring, match substring, after substring, offset past match
-     Less Than 0?(x=Match Pattern#2.offset past match) -> result
-     Select#2(f_value='%s', selector=Less Than 0?.result, t_value='.%s') -> result
-     Format String(0=Select#2.result, 1=Match Pattern#1.before substring, 5=new ending (none)) -> Format String.2
- = (unnamed str) = "'\\\\.[~\\\\.]*$'"
```

`--verbose` doesn't change WHICH changes are reported, or how they're
nested — it's the exact same containment tree either way. It only adds: a
`Signature` section BEFORE the tree (the VI's own connector-pane interface, a
distinct concern the change map doesn't cover); old→new detail on a modified
constant's value instead of just its name; and, when at least one other
change is present, a trailing `(N unchanged nodes)` tally AFTER the tree. This
pair happens to share no unchanged nodes at all, so no tally appears in
either tier.

The `+`/`-`/`~` gutter (column 0) is orthogonal to WHAT changed — the same
tag prefixes a structure's `case (...):` line, a node's `name(port=net, ...)
-> outNets` line, and a constant's `= name = value` line alike. A
`frame`/`value`-kind change (a whole case/sequence frame added, removed, or
reselected) has no line of its own: its tag folds straight onto the quoted
frame header it decorates (e.g. `+ "4":` for a brand-new case value with
content added inside it) instead of appearing as a separate line — that line
already says everything a repeated one would. A wire whose sink is otherwise
unchanged but sits alongside an added/removed node at the same containment
depth is suppressed outright — that node's own `name(port=net, ...) ->
outNets` line already shows the net inline, so a standalone wire line would
just repeat it. A wire change with no such sibling — an unchanged node
losing or gaining a connection, with no node add/remove alongside it — still
renders its own `sink = source` line, and a REMOVED wire always renders even
when nothing else at that containment depth changed; see [Netlist: Diff
annotations](netlist.md#diff-annotations) for a worked example. (A constant
edited IN PLACE — same UID, new value — reads as its own instance line like
any other modified node, since it's tracked in the UID-keyed change map by
its stable UID; only an ADDED or REMOVED constant, which has no UID of its
own to key on, goes through the separate name/value-matched list and keeps
the dedicated `=` marker, e.g. `+ = name = value`.)

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
`bounds_before`/`path_before`) — plus `common_nodes`, the count of nodes matched
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
  overlays both VIs with an opacity slider), **before**, **after**, and
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
- The URL fragment encodes the selected change, view mode, change-list mode,
  and per-structure frame selection
  (`#c=N&view=overlay&list=tree&frame=UID=VALUE`), so a link into the viewer
  can deep-link straight to a specific change.

### Flat vs. tree change list

A **Flat / Tree** toggle sits above the change list. **Flat** (the default)
is the numbered list in engine order, one row per change. **Tree** regroups
the SAME list by containment — client-side, from the `container_uid`/
`frame_path` every entry already carries, no extra data and no second engine
call — into exactly the structure the `text` containment tree above prints:
a structure is a recursion point, its frames are quoted sub-headers, and
contained changes nest beneath them. Every row keeps its number badge and
click-to-select behavior in either mode — switching modes only changes how
the list is grouped, never which change a click selects or what it does.

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

- [Netlist](netlist.md) — the node-first, named-port grammar this page's `text` output renders, documented in full (including the diff-specific gutter/suppression rules).
- [describe](describe.md) — a snapshot of a single VI's signature and operations.
- [render](render.md) — a faithful diagram of a single VI, for visual comparison.
- [structure](structure.md) — the same terminal/operation/wiring breakdown for `.lvlib`/`.lvclass` members.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the resolution flags `diff` shares with the other commands.
