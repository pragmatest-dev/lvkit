# vilib JSON Schema

Files in `src/lvkit/data/vilib/*.json` define LabVIEW standard library VIs.

## File Structure

```json
{
  "category": "File I/O",
  "count": 150,
  "entries": [ ... ]
}
```

## Entry Structure

```json
{
  "name": "Get System Directory.vi",
  "page": 1234,
  "description": "Returns path to...",
  "status": "needs_terminals",
  "terminals": [
    {
      "name": "system directory type",
      "index": 0,
      "direction": "in",
      "python_param": "system_directory_type",
      "type": "sysdir.llb:System Directory Type.ctl"
    }
  ],
  "python_code": "...",
  "inline": false
}
```

## Terminal Index/Direction

PDF extraction provides `name`, `description`, `python_param`.

`direction` is `"in"` or `"out"`, the spelling primitive entries use too.

`index` and `direction` are learned in memory by `auto_update_terminals()` when code generation encounters wired terminals; the bundled files are read-only at runtime, so add confirmed values to the JSON by hand.

## Source

Extracted from `NI's labview-api-ref.pdf (gitignored — not redistributable)` via script.
