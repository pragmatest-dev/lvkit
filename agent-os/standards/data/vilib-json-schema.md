# vilib JSON Schema

Files in `src/lvpy/data/vilib/*.json` define LabVIEW standard library VIs.

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
      "direction": "input",
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

`index` and `direction` are auto-discovered by `auto_update_terminals()` when code generation encounters wired terminals. Don't add manually.

## Source

Extracted from `NI's labview-api-ref.pdf (gitignored — not redistributable)` via script.
