---
name: lvkit-resolve
description: Identify an unknown LabVIEW primitive, terminal mismatch, or vi.lib VI and persist the mapping to the project's .lvkit/ store. Optional/advanced — query-driven conversion (/lvkit-convert) handles unknowns inline; use this to make a mapping stick for every future VI. Gap-triage works via CLI or the MCP `unresolved` tool; the persist step (writing .lvkit/ mappings, `lvkit setup`) is CLI/filesystem.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch
---

# Resolve an unknown dependency

This skill is **optional**. `/lvkit-convert` never blocks on an unresolved
primitive or vi.lib VI — it identifies the gap inline from the facts and
writes code (or a marked TODO) right there. Reach for `/lvkit-resolve`
when you want that identification to *persist*: `lvkit generate` (the
deterministic oracle) throws on an unresolved dependency, and a mapping
written here fixes it for every VI that hits the same primitive or vi.lib
call, not just the one in front of you.

```bash
lvkit unresolved "<path>"
```

collects every gap in a VI/library in one report (each unknown primitive's
full terminal signature; each unmapped vi.lib VI's name and caller
dataflow) — hand this skill the batch instead of hitting `lvkit generate`'s
exceptions one at a time.

## Setup

Destination is the project-local store: `.lvkit/primitives.json` and
`.lvkit/vilib/<category>.json`. If `.lvkit/` doesn't exist yet:

```bash
lvkit setup --no-skills
```

lvkit reads `.lvkit/` **first**, falling back to its own shipped `data/`
mappings — so a project mapping here overrides (or fills a gap in) lvkit's
built-in coverage without touching lvkit's own source. lvkit itself never
reads a project's `.lvkit/` back into its shipment.

Resolve ONE unknown at a time, fully verified. Do not batch-guess entries.

## Branch A: `PrimitiveResolutionNeeded` / `TerminalResolutionNeeded`

`PrimitiveResolutionNeeded` fires for a completely unknown `primResID`.
`TerminalResolutionNeeded` fires for a KNOWN primitive whose actual wired
terminal index doesn't match the stored mapping — same workflow, smaller
fix (correct the terminal `index`, don't re-identify the function).

### 1. Record the diagnostic

The exception reports the primitive's FULL connector pane — every terminal,
wired or not, with its index, direction, and declared type. This is the
strongest discriminator; use the whole signature, not just the wired
subset. For a polymorphic/adaptive primitive an unwired terminal may show
an adapt/placeholder type — count, direction, and position stay reliable.

### 2. Find more instances — grep the XML, never parse the whole corpus

**Never `rglob('*.vi')` + parse everything** — a real tree can be
thousands of VIs / ~1GB of XML and will exhaust memory. Instead, grep the
already-dumped block-diagram XML for the primResID, then parse ONLY the
matches:

```bash
# extract (if not already extracted) — one subprocess per VI, memory-flat
python3 -c "
from pathlib import Path
from lvkit.extractor import extract_vi_xml
for vi in Path('<root>').rglob('*.vi'):
    try: extract_vi_xml(str(vi))
    except Exception as e: print('skip', vi.name, e)
"

# grep for the primResID (instant, memory-flat)
grep -rl '<primResID>PRIM_ID</primResID>' <root> --include=*_BDHb.xml
```

Feed ONLY the matched `*_BDHb.xml` paths to `parse_vi(bd_xml=...)` — it
parses that one VI's block diagram, no subVIs, no dependency loading. Cap
the count you parse and `del` each diagram before the next.

### 3. Examine graph context

Trace beyond immediate neighbors — through structure boundaries, nMux
nodes, into/out of SubVI calls. The name of the calling VI, and of the VIs
consuming this primitive's outputs, are often the strongest signal.
`lvkit describe <calling-vi>` is the fastest way to see that context.

### 4. Identify the function

Primary source: public NI documentation (`docs-be.ni.com`) via web
search/fetch — read the full Inputs/Outputs section and confirm the
terminal TYPES, NAMES, and DIRECTIONS match, including unwired terminals.

**Fallback (this project only, your license/your call):** if you have
LabVIEW installed and can open the calling VI, reading the primitive's
context menu / quick help is a valid way to confirm identity here — unlike
lvkit's own shipped `data/`, a project's `.lvkit/` mapping may be derived
from licensed sources.

### 5. Write the entry

`.lvkit/primitives.json`, under `"primitives"`:

```json
"PRIM_ID": {
    "name": "Confirmed Function Name",
    "terminals": [
        {"index": N, "direction": "in", "name": "descriptive_python_name", "type": "actual_type"},
        {"index": N, "direction": "out", "name": "descriptive_python_name", "type": "actual_type"}
    ],
    "python_code": {"output_name": "in_N op in_M"},
    "inline": true
}
```

Rules: terminal **indices** and **directions** come from Steps 1–3 (observed
wiring), never from documentation listing order (NI's docs often list
terminals in a different order than the connector pane). Terminal names
must be valid Python identifiers. Include every terminal that appears in
the actual VI data, including error clusters. `python_code` keys match
output terminal names.

**Last resort — placeholder.** Only after exhausting Steps 1–4: a
`"placeholder": true` entry lets generation proceed with a visible warning
instead of failing:

```json
"PRIM_ID": {
    "name": "Unknown Category Primitive PRIM_ID",
    "placeholder": true,
    "terminals": [...all terminals from the graph...],
    "python_code": "pass"
}
```

## Branch B: `VILibResolutionNeeded`

A vi.lib VI has a KNOWN name (it's a filename) — look it up directly rather
than reverse-engineering it.

### 1. Record the diagnostic

VI name, qualified `<vilib>/...` path (if present), every terminal (name,
index, direction, type), and the caller VI name.

### 2. Look up the terminal layout

Primary source: web search NI's documentation for the exact VI name — read
the Inputs/Outputs section (names, directions, types, defaults).

**Fallback (this project, your license):** the qualified path resolves
under the user's own LabVIEW `vi.lib` (e.g. `C:\Program Files\National
Instruments\LabVIEW <ver>\vi.lib\...` on Windows). `lvkit describe
"<full-vilib-path>"` works without resolution and gives a quick terminal
layout if the file is reachable.

### 3. Match terminals to the caller's wire indices

The diagnostic's "Wire types from dataflow" section shows the ACTUAL
indices the caller uses (`idx_0`, `idx_1`, ...) — match each to the
terminal you found in Step 2. This determines the connector-pane `index`
you write; never assume documentation order matches it.

### 4. Check for an existing partial entry

```bash
grep -r "VI NAME" .lvkit/vilib/ 2>/dev/null
```

Update it if partial; otherwise create a new entry.

### 5. Write the entry

`.lvkit/vilib/<category>.json` (register the category in
`.lvkit/vilib/_index.json` if it's new):

```json
{
  "VI Name.vi": {
    "name": "VI Name",
    "terminals": [
      {"name": "terminal_name", "index": N, "direction": "input", "type": "actual_type", "python_param": "python_parameter_name"}
    ],
    "python_code": "python_equivalent_expression",
    "inline": true
  }
}
```

If an index can't be determined from the diagnostic, leave it out — the
next call site's exception will supply more context.

## Re-verify

```bash
lvkit generate "<vi-path>" -o "<output-dir>" --search-path "<library-path>"
```

Same failure → the mapping is wrong, go back to Step 1 of that branch. New
failure → a different unknown was uncovered; resolve it the same way.

**Alternative to writing a mapping up front:** `lvkit generate
--placeholder-on-unresolved` emits an inline `raise
PrimitiveResolutionNeeded(...)`/`raise VILibResolutionNeeded(...)` with the
same diagnostic instead of failing the build — fix the gap contextually in
the generated Python, or come back and write the mapping later.

## NEVER do these things

- NEVER guess a terminal index from documentation order — confirm from
  observed wiring
- NEVER assume a primResID's meaning shifts because terminal types look
  different — primitive polymorphism must be observed in data or confirmed
- NEVER batch-fill entries without verifying each one
- NEVER add a placeholder before exhausting the identification steps
- NEVER write a mapping derived from this project's `.lvkit/` upstream into
  lvkit itself — the shipped `data/` stays cleanroom (public docs only);
  that boundary is enforced separately in lvkit's own repo, not by this
  skill

## Related

- `/lvkit-convert` — resolves unknowns inline without persisting; use this
  skill only when you want the fix to stick
- `/lvkit-describe` — quick terminal layout / calling-VI context, no
  resolution required
