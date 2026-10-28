# generate

**Experimental.** Deterministically convert a VI, library, or class to Python
source. The same VI always produces the same Python — there's no LLM in this
path.

Like every lvkit command, `generate` is **read-only** on the VI: it parses the
source and writes the Python to a *separate* output path. The `.vi` is never
modified, re-saved, or edited.

## Synopsis

```bash
lvkit generate <input_path> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `input_path` | Path to a `.vi`, `.lvlib`, `.lvclass`, or a directory. A `.lvlib`/`.lvclass`/directory generates every VI it contains, in dependency order. |

## Options

| Option | Description |
|--------|-------------|
| `-o OUTPUT`, `--output OUTPUT` | Output directory. Default: `outputs`. Generated files land in a VI-named subfolder beneath it (see [Output layout](#output-layout)). |
| `--load-mode MODE` | How deep to load dependencies: `full` (default), `minimal`, or `none`. Codegen defaults to `full` — it compiles the whole SubVI/class-method tree. `--load-mode none` generates only the top-level VI, not the [SubVIs](subvi-resolution.md) it calls: the SubVI calls still appear in the output, but no module is generated for them. |
| SubVI resolution flags | `--search-path`, `--project-root`, `--vilib`, `--userlib`, `--no-auto-vilib` — see [SubVI & vi.lib resolution](subvi-resolution.md). |

## Example

```bash
lvkit generate "Convert File Extension (String).vi" \
  --search-path samples/OpenG/extracted \
  -o outputs
```

This writes the generated Python to `outputs/convert_file_extension_string/`.

## Output layout

`generate` creates one subfolder per input, named after the input's stem
(lowercased, non-word characters replaced with `_`), directly under `-o`. So
`-o outputs` on `MyVI.vi` writes to `outputs/myvi/`. **That subfolder is wiped
and recreated on each run**, so point `-o` at a throwaway directory, not one
holding hand-written code. A `.lvlib`, `.lvclass`, or directory input generates
every VI it contains into that one subfolder, in dependency order.

If `input_path` doesn't exist, `generate` prints `Error: Path not found` and
exits non-zero without writing anything.

## Gaps

Coverage is incremental. A **gap** is anything `generate` cannot convert: an
unknown primitive or vi.lib VI, a primitive entered as a placeholder, a property
with no Python meaning, a Call Library Function Node whose library file is not
there, a VI reference with nothing to resolve to, an Event Structure, a VI that
was not found. `generate` handles every gap the same way:

- It prints `warning: <VI path>: <node>: <reason>` and counts the gaps in its
  summary.
- The generated code raises `NotImplementedError("<reason>")` where the node
  would run, and the node's outputs are set to `None`, so the code after it is
  still there for you to finish. A gap that belongs to no node (a class whose
  parent is not loaded, a constant with no value to write) makes the module
  raise where that definition runs.

A run that has only gaps exits `0`. It exits non-zero when a VI could not be
written at all (`error` or `failed` in the summary), and when two VIs would be
written to the same module, in which case it prints the message and writes
nothing.

`lvkit unresolved <input-path> [--json]` lists the same gaps, grouped by kind
with the VIs each occurs in.

Resolving a primitive or vi.lib VI is a one-time step: lvkit saves the mapping
into your project's `.lvkit/` resolution store, and every future `generate` run
reuses it. `generate` runs fine without a store — it resolves fresh each time;
the store only caches results. Run [`setup`](setup.md) to create the store and
install the AI-agent skills that fill in unknowns for you.

## Notes

- Determinism holds for a fixed set of resolutions. Because `vi.lib`
  auto-detection uses whatever LabVIEW is installed on the machine (see
  [SubVI & vi.lib resolution](subvi-resolution.md)), pass `--no-auto-vilib` for
  output that's identical across machines and in CI.

## See also

- [describe](describe.md) — preview a VI's would-be Python signature without generating code.
- [setup](setup.md) — install the `.lvkit/` resolution store and AI-agent skills that assist with gaps from unknown primitives and vi.lib VIs.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the resolution flags `generate` shares with the other commands.
