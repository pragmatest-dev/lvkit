---
name: lvkit-document
description: Use when the user wants a documentation site, README, or browsable reference generated for a LabVIEW VI/library/class/directory — "document this library", "generate docs for X.lvclass". Builds the static HTML site (`lvkit docs`, CLI-only) then augments each page with a "what it does" interpretation via `describe`.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Document a LabVIEW library

```bash
lvkit docs "<library-path>" "<output-dir>" --search-path "<search-path>"
```

`<library-path>` is a `.vi`, `.lvlib`, `.lvclass`, or a directory.
This writes a full static HTML site: one page per VI (signature, controls/
indicators tables, the rendered block-diagram SVG, dependencies, callers,
and — for a class — a hierarchy/override page), plus `<output-dir>/
index.html` as the table of contents. Open it and confirm it built before
augmenting anything.

## Getting the facts

`lvkit docs` is CLI-only — the MCP server has no doc-generation tool (it's
understanding-only: `index`, `query`, `query_schema`, `describe`, `read_vi`,
`unresolved`). Run `lvkit docs` as a shell command even with the MCP server
connected, then use the MCP `describe` tool (or `/lvkit-describe`) for the
per-VI interpretation in the Augment step below.

## Augment: add the semantic layer

The structural site `lvkit docs` builds carries no interpretation — every
VI page's `<section id="summary">` holds exactly one mechanical sentence:

```html
<section id="summary">
    <h2>Summary</h2>
    <p>Takes 3 input(s), returns 1 output(s)</p>
</section>
```

For each VI page (start with the entry points — VIs `/lvkit-query` shows
with `callers_count = 0` — and work outward through dependencies), get the
same interpretation `/lvkit-describe` produces (`describe(vi_path)` over
MCP, or `lvkit describe <vi-path>` on the CLI) and replace that `<p>` with
1–2 sentences of real purpose — what the VI does, not just its arity. Keep
the original arity sentence or fold it in; don't just delete information.

This is `/lvkit-describe`'s "what it does" interpretation applied at
library scale — the value is doing it consistently across every page in the
site, not the mechanics of running `lvkit docs` (which needs no skill).

## Class/library landing pages

For a `.lvclass` input, `lvkit docs` also writes a class landing page
(parent/children, method list, private-data fields) with no equivalent
per-class summary section. If the class's purpose isn't obvious from its
name and field list, add a short paragraph near the top describing what the
class represents, grounded in its private-data fields and method names —
not invented.

## Related

- `/lvkit-describe` — the per-VI interpretation this skill applies at scale
- `/lvkit-query` — find entry points (`callers_count = 0`) to prioritize
