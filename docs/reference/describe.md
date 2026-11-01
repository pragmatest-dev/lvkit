# describe

Print a human-readable description of a VI: its signature, inputs and outputs,
constants, control flow, and the operations on its block diagram — without
opening LabVIEW. `describe` never requires primitive or [vi.lib](subvi-resolution.md)
mappings, so it works on any VI out of the box.

## Synopsis

```bash
lvkit describe <input_path> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `input_path` | Path to a `.vi` file. |

## Options

| Option | Description |
|--------|-------------|
| SubVI resolution flags | `--vilib`, `--userlib`, `--instrlib`, `--project-root`, `--no-auto-vilib` — see [SubVI & vi.lib resolution](subvi-resolution.md). |

## Example

```bash
lvkit describe "Convert File Extension (String).vi" --userlib libraries/extracted/user.lib
```

```text
# Convert File Extension (String)__ogtk.vi

  convert_file_extension_(string)__ogtk(file name: str, new ending (none): str) -> new filename: str, prev ending: str

## Inputs
  file name: str (unknown)
  new ending (none): str (unknown)

## Outputs
  new filename: str
  prev ending: str

## Constants
  (unnamed): str = '\\.[~\\.]*$'

## Control Flow
  Case structure (2 frames, gated on new ending (none))

## Operations
Match Pattern [prim 1535]
Case Structure (2 frames)
  Frame "Default" (default):
    Match Pattern [prim 1535]
    Less Than 0? [prim 1118]
    Select [prim 1516]
    Format String
    (unnamed): str = '^\\.'
    (unnamed): str = '.%s'
    (unnamed): str = '%s'
  Frame "":
    (pass-through)
```

## Notes

- The generated Python-style signature (`convert_file_extension_(string)__ogtk(...)`)
  is a preview of the name and parameters [`generate`](generate.md) would use — it
  does not require code generation to succeed.
- The `(unknown)` after an input (e.g. `file name: str (unknown)`) is the
  terminal's **connection requirement**, not its type — the type is the `str`
  before it. The connector-pane rule prints as `required`, `recommended`, or
  `optional`; `unknown` means the VI doesn't record one for that terminal.
- `[prim N]` after an operation (e.g. `Match Pattern [prim 1535]`) is the
  primitive's `primResID`, LabVIEW's internal ID for that built-in node.
- A For or While Loop ends with a `Tunnels:` list, inputs first: each
  tunnel, shift register, or count terminal in LabVIEW's words, with what it
  carries in parentheses. A Conditional tunnel also names what drives its
  condition, e.g. `out conditional indexing tunnel (BusType), condition ← True`.
- An unresolved [SubVI](subvi-resolution.md) is not an error. If a call's
  target isn't at the path its caller records, `describe` still lists it under
  `## Dependencies` — just without a signature or description, since those
  come from the target VI.

## Errors

If `input_path` doesn't exist, `describe` prints `Error: Path not found: <path>`
and exits `2` (a usage error). If it isn't a `.vi`, `.vim`, `.vit` or `.ctl` (a
`.lvlib`, `.lvclass`, or directory), it prints `Error: expected a VI file (...)
or a .ctl: <name>` and exits `2` — use [`structure`](structure.md) for those
instead. A VI that exists but cannot be read is a problem, not a usage error: see
[problems](problems.md) (`Stopped`, exit `1`).

## See also

- [generate](generate.md) — the Python code this VI's signature previews, once you're ready to convert it.
- [render](render.md) — a faithful, interactive block-diagram SVG instead of text.
- [structure](structure.md) — for `.lvlib`/`.lvclass` members rather than a single VI.
