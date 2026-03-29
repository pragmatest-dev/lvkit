---
name: full-convert
description: Complete autonomous VI → idiomatic Python conversion. Generates code, resolves all errors, and produces clean output. One command, full pipeline.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Full Convert

End-to-end LabVIEW → idiomatic Python. Handles everything automatically.

## Input

User provides a path to a `.vi`, `.lvclass`, `.lvlib`, or directory.

## Step 1: Initial generation

```bash
vipy generate "INPUT_PATH" -o outputs --search-path samples/OpenG/extracted
```

Add additional `--search-path` arguments as needed for the VI's dependencies.

Record the error count from the output summary.

## Step 2: Resolution loop

If errors > 0, resolve them ONE AT A TIME:

For each error in the output:

- **`PrimitiveResolutionNeeded`** → invoke `/resolve-primitive` with the primResID
- **`TerminalResolutionNeeded`** → invoke `/resolve-primitive` (same skill, terminal mismatch)
- **`VILibResolutionNeeded`** → invoke `/resolve-vilib` with the VI name
- **`TypeResolutionNeeded`** → investigate the nMux field index (check typedef fields in dep_graph vs terminal lv_type)

After resolving each error, re-run `vipy generate` with the same arguments. Repeat until `error: 0`.

## Step 3: Quality check

After 0 errors, invoke `/judge-output` on the generated files to verify:
- Function signatures match VI inputs/outputs
- Control flow structures are correct
- All SubVI calls present
- No semantic drift

## Step 4: Idiomatic rewrite

For each generated `.py` file, invoke `/idiomatic` to rewrite mechanical translations into clean Python.

## Step 5: Documentation (optional)

```bash
vipy docs "INPUT_PATH" outputs/docs --search-path samples/OpenG/extracted
```

Generates HTML documentation alongside the Python code.

## Step 6: Report

Summarize:
- Total VIs processed
- Successfully generated (AST)
- Idiomatically rewritten
- Remaining issues (if any)
- Documentation location

## Notes

- Always use `outputs/` directory, never `/tmp/`
- The resolution skills modify `data/primitives-codegen.json` and `data/vilib/*.json`
- Each resolution makes ALL VIs using that primitive/VI work, not just the current one
- Re-running `vipy generate` after resolution may uncover NEW errors from VIs that previously couldn't proceed — this is expected, keep looping
