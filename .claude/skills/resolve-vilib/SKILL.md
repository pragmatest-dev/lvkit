---
name: resolve-vilib
description: Resolve a single unknown vilib VI by looking up its terminal layout in the LabVIEW documentation. Called when VILibResolutionNeeded fires during vipy generate.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Resolve Unknown vilib VI

When `VILibResolutionNeeded` fires during `vipy generate`, this skill resolves it. vilib VIs have KNOWN names (they're filenames), so we can look them up directly in the documentation. Follow ALL steps IN ORDER.

## Input

The `VILibResolutionNeeded` exception provides:
- VI name (e.g., "Error Cluster From Error Code.vi")
- Terminal names from the VI's XML
- Wire types from the caller's dataflow (shows actual indices being used)

## Step 1: Record the diagnostic

Write down the EXACT diagnostic output:
- VI name
- Every terminal listed: name, index, direction, type
- The caller VI name

## Step 2: Search the LabVIEW documentation by name

The VI name IS the function name. Search directly:

```bash
grep -n "EXACT VI NAME" docs/labview_ref_manual.txt | head -10
```

Then read the full Inputs/Outputs section at the relevant page. The documentation gives:
- Every terminal name and its purpose
- Direction (input/output)
- Data type
- Default values for optional inputs

## Step 3: Match documentation terminals to wire types

The diagnostic shows "Wire types from dataflow" with actual indices from the caller. Match each wire index to the documentation's terminal list:

- idx_0 (input) → which documented input terminal?
- idx_1 (output) → which documented output terminal?

The connector pane layout determines the index mapping. Use the actual wire types (from the diagnostic) to disambiguate when multiple terminals have similar types.

## Step 4: Check existing vilib JSON files

Look at `data/vilib/*.json` to see if a partial entry already exists:

```bash
grep -r "VI NAME" data/vilib/
```

If an entry exists with missing indices, update it. If no entry exists, create one in the appropriate category file.

## Step 5: Add the JSON entry

Add to the appropriate `data/vilib/*.json` file (e.g., `error-handling.json`, `string.json`, `numeric.json`):

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
- Terminal names come from the DOCUMENTATION, not guesses
- `python_param` must be a valid Python identifier
- Include ALL terminals including error clusters
- `index` values come from the wire types in the diagnostic (actual connector pane positions)
- If you can't determine an index from the diagnostic, leave it out — the codegen will raise again with more info on the next call site

## Step 6: Re-run generation

```bash
vipy generate "path/to/vi" -o outputs --search-path samples/OpenG/extracted
```

If the same VI fails again, the terminal matching is wrong — go back to Step 1.
If a NEW VI fails, start this process again for that one.

## NEVER do these things

- NEVER guess terminal indices — they come from the caller's dataflow diagnostic
- NEVER invent terminal names — they come from the documentation
- NEVER skip the documentation lookup — the VI name IS searchable
- NEVER add a vilib entry without verifying against the PDF
- NEVER assume terminal order from the documentation matches connector pane order — use the wire types from the diagnostic to determine actual indices
