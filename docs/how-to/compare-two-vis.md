# Compare two VIs

Diff two versions of a VI — added/removed terminals, changed operations, rewired connections — and read what actually changed in the binary `.vi` file, not just that it changed.

## Prerequisites

- lvkit installed (`pip install lvkit`).
- Two `.vi` files to compare — typically the same VI at two commits, or two related VIs.

## Run the diff

For code review or CI, the default text output is usually what you want:

```bash
lvkit diff "path/to/Some VI (old).vi" "path/to/Some VI (new).vi"
```

This prints a change summary to stdout: one line per added (`+`), removed (`-`), or modified (`~`) node, structure, or wire, in the VI's own dataflow order — not grouped by change kind. `diff` matches nodes and structures by their stable LabVIEW UID, not by name, so an added instance of a repeated node (e.g. a second `Match Pattern`) is never confused with a rewire of an unrelated one.

Add `-v`/`--verbose` for full depth — the same change set, plus a `Signature` section for the VI's own connector-pane interface, old→new detail on modified constant values, and a trailing unchanged-node tally:

```bash
lvkit diff "path/to/Some VI (old).vi" "path/to/Some VI (new).vi" --verbose
```

If a SubVI referenced by either VI isn't found, `diff` doesn't fail — it's recorded as unresolved and the comparison proceeds with what could be loaded. Add `--search-path` for SubVIs that live outside the VI's own directory or project; see [SubVI & vi.lib resolution](../reference/subvi-resolution.md).

## Open the interactive diff viewer

For a visual, side-by-side comparison:

```bash
lvkit diff "path/to/Some VI (old).vi" "path/to/Some VI (new).vi" --open
```

This renders a self-contained HTML file (default `outputs/vi-diff/<old-stem>__<new-stem>.html`) and opens it in your browser — no server needed, so the same file works opened locally, attached to a review, or hosted as a static CI artifact. Pass `-o FILE` to control the output path without opening it automatically, or `--format html` without `--open` to just write the file.

### Reading the viewer

- **Two view modes** in the toolbar: **Split** (the default — before above, after below, panning and zooming in sync) and **Overlay** (both versions stacked with an opacity slider; drag it, or click its **before** / **after** ends to snap to one side).
- **The change list** in the sidebar has one entry per added/removed/modified node or wire; each entry's number is drawn as a badge on the diagram next to what it correlates with. Click an entry (or its badge) to jump to it; `p`/`n` or the prev/next buttons step through the list in order.
- **Selecting a change** spotlights it and dims the rest of the diagram. If the change lives inside a case or sequence frame that isn't currently showing, the viewer switches that frame into view in both panes first, so a change never hides behind an unselected case.
- **Flat vs. Tree** toggle above the change list: Flat is the numbered list in engine order; Tree regroups the same list by containment (a structure's own line, its frames as sub-headers, changes nested beneath) — same clicks, same selection, just grouped differently.
- Zoom (toolbar, `+`/`-`, or Ctrl/Cmd+scroll) is shared across both view modes.

## Reading the text change tree

The text output is a netlist — a node's own line reads `name(port=net, ...) -> outNets`, with each wired input shown by its source net's name rather than by position; a structure's own line reads `case (selector):` / `while (...):` / `sequence:`, with its affected frames as quoted sub-headers and their changes indented beneath. An unchanged structure that merely *contains* a change still prints its own scope line, so a nested change always shows its enclosing case/loop context.

```text
+ Strip Path(path=file name) -> stripped path, name
- Match Pattern#1(string=file name, regular expression='\\.[~\\.]*$') -> before substring, match substring, after substring, offset past match
- case (new ending (none)):
    "Default":
-     Match Pattern#2(string=new ending (none), regular expression='^\\.') -> before substring, match substring, after substring, offset past match
```

Read the `+`/`-`/`~` gutter the same way at every level: `+`/`-` on a node or structure line means added/removed; `~` (verbose mode) or a value shown inline means modified in place. The full grammar — including how wire-only changes and case-frame changes are shown — is documented once in [Netlist](../reference/netlist.md), since `describe --verbose`'s `## Netlist` section uses the same syntax.

## Use in CI

`diff` always exits `0` when it successfully compares two VIs, **including when they're identical** — the exit code doesn't tell you whether the VIs differ. A tool *failure* (unreadable path, corrupt VI) exits non-zero instead, so `0` always means "compared successfully," identical or not — see [Exit codes](../reference/diff.md#exit-codes). To gate on whether they differ, check the printed text (`No changes detected.` vs. an actual diff) or parse `--format json`, which serializes the same UID-correlated change map for scripts and AI agents to consume directly:

```bash
lvkit diff "path/to/Some VI (old).vi" "path/to/Some VI (new).vi" --format json
```

Because `vi.lib` auto-detection depends on whatever LabVIEW is installed on the machine running lvkit, pass `--no-auto-vilib` for a diff that's identical everywhere (CI, code review):

```bash
lvkit diff "path/to/Some VI (old).vi" "path/to/Some VI (new).vi" --no-auto-vilib
```

## See also

- [reference/diff](../reference/diff.md) — the full flag reference: `--format`, `--verbose`/`--long`, `-o`, `--open`, `--load-mode`, exit codes.
- [reference/netlist](../reference/netlist.md) — the node-first, named-port grammar the text output and diff viewer's Tree change list both use.
- [View a block diagram](view-a-block-diagram.md) — render a single VI's diagram faithfully, without a comparison.
- [reference/subvi-resolution](../reference/subvi-resolution.md) — `--search-path`, `--vilib`, `--userlib`, `--no-auto-vilib`, shared by `diff` and every command that resolves SubVIs.
