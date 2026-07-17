# generate

**Experimental.** Deterministically convert a VI, library, or class to Python
source. The same VI always produces the same Python — there's no LLM in this
path.

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
| `--placeholder-on-unresolved` | Keep the build succeeding when a primitive or vi.lib VI is unresolved — see [Unresolved calls](#unresolved-calls). |
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

## Unresolved calls

Coverage is incremental. By default, an unknown primitive or vi.lib VI **fails
the build** with a `PrimitiveResolutionNeeded` or `VILibResolutionNeeded` error
that names exactly what's missing.

Pass `--placeholder-on-unresolved` to keep the build succeeding instead: each
unresolved call becomes an inline `raise PrimitiveResolutionNeeded(...)` /
`raise VILibResolutionNeeded(...)` in the generated Python, so you see every
unresolved call in one pass rather than fixing them one failure at a time.

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
- [setup](setup.md) — install the `.lvkit/` resolution store and AI-agent skills that assist with unresolved primitives/vi.lib VIs.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the resolution flags `generate` shares with the other commands.
