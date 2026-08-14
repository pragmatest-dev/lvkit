---
name: lvkit-review
description: Turn a `lvkit diff` between two VI versions into a PR-ready "what changed, why it matters, who's affected" narrative. Works via CLI or MCP (blast-radius only — diff itself is CLI-only).
allowed-tools: Bash, Read, Grep
---

# Review a VI change

```bash
lvkit diff "<vi-before>" "<vi-after>"
```

```
  Properties:
~   ▤ source-only: true -> false
  Block Diagram:
+   sequence:
+   Tick Count (ms)#1() -> millisecond_timer_value
+   Parse XML.vi(error in (no error)=Flat Sequence.0, xml string=text) -> error out, xml out
...
-   while:
-   event:
      "0":
-       Event Data Node#1() -> Event Data Node#1.1
...
```

`+`/`-` are added/removed nodes; `~` is a modified one (old → new value
inline, e.g. `▤ source-only: true -> false` for a VI-property change).
Indentation is the containment tree — a case/loop/sequence's added or
removed body sits nested under it. This is the raw material; your job is to
turn it into a change narrative, not to paste it verbatim.

## Getting the facts

**`lvkit diff` has no MCP twin — always run it as a CLI command, even with
the MCP server connected.** `blast_radius`/`lvkit blast-radius` (a true
MCP/CLI twin) is what supplies the "who's affected" half.

`lvkit diff <before> <after>` diffs two `.vi` files directly — typically two
git revisions of the same VI, checked out to two paths (or use
`--before-ref`/`--after-ref` to label the two sides with git revs in the
output without lvkit touching git itself). `-v/--verbose` adds full depth:
the VI-interface signature diff, the containment tree expanded rather than
collapsed, and an unchanged-node tally — use it when the concise default
elides something you need. `--format json` gives the same change map as
structured data (`{vi, changes: [...]}`, each an `ElementChange` tagged
`kind` — `node`/`wire`/`property`/`health`/`signature`); `--format html
--open` renders a self-contained interactive side-by-side viewer.

## Write the narrative

For each PR/commit range touching VIs:

1. **`lvkit diff`** the before/after pair for every changed VI.
2. Read the change tree. Group it into what a reviewer actually cares
   about: new/removed SubVI calls and structures, rewired dataflow inside
   an unchanged structure, VI-property changes (execution/window/protection
   — the `▤` lines), and connector-pane/signature changes (only visible
   with `-v`, or always in `--format json`'s `signature`-kind entries).
3. **`lvkit blast-radius <vi> <repo>`** (or `get_callers` for just the
   direct callers) for each changed VI — who calls it, so a connector-pane
   or behavior change is flagged with its blast radius, not just described
   in isolation.
4. Write the summary: **what changed** (plain language, not a wire dump),
   **why it matters** (behavior change vs. cosmetic — a rewired constant
   value is not the same risk as a removed error check), **who's affected**
   (the blast-radius list, or "no callers in this repo" if `callers_count`
   is 0).

A signature change (an input/output added, removed, retyped, or reordered)
is always worth calling out explicitly — every caller in the blast radius
needs to be checked against it, and `lvkit diff`'s concise default doesn't
show it without `-v`.

## Related

- `/lvkit-query` — `callers_count`/`class_fact` facts to enrich the "who's
  affected" section beyond the direct blast-radius list
- `/lvkit-convert` — if the diff needs to land in a generated port too
