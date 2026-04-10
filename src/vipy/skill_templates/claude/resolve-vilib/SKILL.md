---
name: resolve-vilib
description: Resolve a single unknown vilib VI by looking up its terminal layout in the LabVIEW documentation. Called when VILibResolutionNeeded fires during vipy generate.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Resolve Unknown vilib VI

When `VILibResolutionNeeded` fires during `vipy generate`, this skill resolves it. vilib VIs have KNOWN names (they're filenames), so we can look them up directly. Follow ALL steps IN ORDER.

## Step 0: Detect mode

This skill runs in two contexts. The destination directory and the lookup workflow differ. Decide which one applies BEFORE you do anything else.

Read `pyproject.toml` from the current directory (walk up if needed). If it contains `name = "vipy"`, you are working **inside vipy itself**:

- Destination: `data/vilib/<category>.json` (vipy's shipped, cleanroom data)
- Lookup source: vipy's shipped `docs/labview_ref_manual.txt`
- The mapping must be cleanroom — derived from public documentation, NOT from the actual vi.lib block diagram

Otherwise, you are working **inside a downstream user's project**:

- Destination: `.vipy/vilib/<category>.json` (project-local store; run `vipy init` first if `.vipy/` doesn't exist)
- Lookup source: open the actual file at the **qualified path** the diagnostic provides (e.g. `<vilib>/Utility/error.llb/Error Cluster From Error Code.vi`). The user's own LabVIEW install resolves `<vilib>` to the on-disk `vi.lib` directory. Read the real connector pane and terminals.
- Do NOT use vipy's `docs/labview_ref_manual.txt` — rely on the user's own LabVIEW install
- The mapping you write may be derived from the actual vi.lib source; that's the user's call. vipy itself never reads `.vipy/`.

## Step 1: Record the diagnostic

Write down the EXACT diagnostic output:
- VI name (filename)
- Qualified path (the `<vilib>/...` line — if absent, the parser didn't capture it; ask the user to point you at the source file)
- Every terminal listed: name, index, direction, type
- The caller VI name

## Step 2: Look up the function

This step is mode-dependent.

### vipy mode: search the shipped LabVIEW reference

The VI name IS the function name. Search vipy's reference text:

```bash
grep -n "EXACT VI NAME" docs/labview_ref_manual.txt | head -10
```

Read the full Inputs/Outputs section at the relevant page. The documentation gives:
- Every terminal name and its purpose
- Direction (input/output)
- Data type
- Default values for optional inputs

### User-project mode: read the real source file

Use the qualified path from the diagnostic to find the file on disk. Resolve `<vilib>` to the user's LabVIEW vi.lib directory (commonly `C:\Program Files\National Instruments\LabVIEW <version>\vi.lib\` on Windows or `/Applications/LabVIEW <version>/vi.lib/` on macOS).

If `vipy describe` works on the file (it doesn't require resolution), use it for a quick terminal layout:

```bash
vipy describe "<full-vilib-path>"
```

Otherwise, ask the user to open the VI in LabVIEW and read off the connector pane terminal positions. The connector pane index is what vipy needs.

## Step 3: Match documentation/source terminals to wire types

The diagnostic shows "Wire types from dataflow" with actual indices from the caller. Match each wire index to the terminal list you found in Step 2:

- idx_0 (input) → which terminal?
- idx_1 (output) → which terminal?

The connector pane layout determines the index mapping. Use the actual wire types (from the diagnostic) to disambiguate when multiple terminals have similar types.

## Step 4: Check existing entries

This step is mode-dependent.

### vipy mode

```bash
grep -r "VI NAME" data/vilib/
```

### User-project mode

```bash
grep -r "VI NAME" .vipy/vilib/ 2>/dev/null
grep -r "VI NAME" data/vilib/ 2>/dev/null  # also check shipped data — may already be there
```

If a partial entry exists with missing indices, update it. If no entry exists, create one in the appropriate category file.

## Step 5: Add the JSON entry

The destination depends on the mode you detected in Step 0:

- **vipy mode** → `data/vilib/<category>.json`
- **user-project mode** → `.vipy/vilib/<category>.json` (and add the category to `.vipy/vilib/_index.json` if it's not there yet)

Add the entry:

```json
{
  "VI Name.vi": {
    "name": "VI Name",
    "terminals": [
      {
        "name": "terminal_name",
        "index": N,
        "direction": "input",
        "type": "actual_type",
        "python_param": "python_parameter_name"
      }
    ],
    "python_code": "python_equivalent_expression",
    "inline": true
  }
}
```

Rules:
- Terminal names come from the DOCUMENTATION (vipy mode) or SOURCE FILE (user mode), not guesses
- `python_param` must be a valid Python identifier
- Include ALL terminals including error clusters
- `index` values come from the wire types in the diagnostic (actual connector pane positions)
- If you can't determine an index from the diagnostic, leave it out — the codegen will raise again with more info on the next call site

## Step 6: Re-run generation

```bash
vipy generate "<vi-path>" -o "<output-dir>" --search-path "<library-path>"
```

If the same VI fails again, the terminal matching is wrong — go back to Step 1.
If a NEW VI fails, start this process again for that one.

**Alternative: soft codegen mode.** Instead of authoring a mapping up front, you can re-run `vipy generate --placeholder-on-unresolved`. The generated Python contains an inline `raise VILibResolutionNeeded(...)` statement with the same diagnostic context. You can fix the gap contextually in the Python (or come back and write a real mapping later).

## NEVER do these things

- NEVER guess terminal indices — they come from the caller's dataflow diagnostic
- NEVER invent terminal names — they come from the documentation or real source file
- NEVER skip the documentation/source lookup — the VI name IS searchable
- NEVER assume terminal order from the documentation matches connector pane order — use the wire types from the diagnostic to determine actual indices
- NEVER write user-mode mappings into vipy's `data/` (cleanroom contamination)
- NEVER write vipy-mode mappings into a user project's `.vipy/` (wrong destination)
