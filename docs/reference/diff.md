# diff

Compare two versions of a VI: added/removed terminals, changed operations, and
rewired connections. By default `diff` prints a unified diff of the two VIs'
[`describe`](describe.md) output; pass `--long` for a structured change report
instead. `diff` never requires primitive or vi.lib mappings, so it works on any
two VIs out of the box. Useful in code review and CI to see what actually
changed in a binary `.vi` file, not just that it changed.

## Synopsis

```bash
lvkit diff <vi_a> <vi_b> [options]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `vi_a` | Path to the first `.vi` file. |
| `vi_b` | Path to the second `.vi` file. |

## Options

| Option | Description |
|--------|-------------|
| `--long` | Show a structured change report instead of a unified diff. |
| SubVI resolution flags | `--search-path`, `--project-root`, `--vilib`, `--userlib`, `--no-auto-vilib` — see [SubVI & vi.lib resolution](subvi-resolution.md). |

## Example

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted
```

```text
--- Convert File Extension (String)__ogtk.vi
+++ Convert File Extension (Path)__ogtk.vi
@@ -1,31 +1,19 @@
-# Convert File Extension (String)__ogtk.vi
+# Convert File Extension (Path)__ogtk.vi

-  convert_file_extension_(string)__ogtk(file name: str, new ending (none): str) -> new filename: str, prev ending: str
+  convert_file_extension_(path)__ogtk(file name: Path, new ending (none): str) -> new filename: Path, prev ending: str

 ## Inputs
-  file name: str (unknown)
+  file name: Path (unknown)
   new ending (none): str (unknown)

 ## Outputs
-  new filename: str
+  new filename: Path
   prev ending: str

-## Constants
-  (unnamed): str = '\\.[~\\.]*$'
-
-## Control Flow
-  Case structure (2 frames, gated on new ending (none))
+## Dependencies
+  Convert File Extension (String)__ogtk.vi: (file name: str, new ending (none): str) → (new filename: str, prev ending: str)

 ## Operations
-Match Pattern [prim 1535]
-Case Structure (2 frames)
-  Frame "Default" (default):
-    Match Pattern [prim 1535]
-    Less Than 0? [prim 1118]
-    Select [prim 1516]
-    Format String
-    (unnamed): str = '^\\.'
-    (unnamed): str = '.%s'
-    (unnamed): str = '%s'
-  Frame "":
-    (pass-through)+Strip Path [prim 1420]
+Convert File Extension (String)__ogtk.vi(new ending (none), file name) → prev ending, new filename
+Build Path [prim 1419]
```

Pass `--long` for the structured report instead:

```bash
lvkit diff "Convert File Extension (String)__ogtk.vi" "Convert File Extension (Path)__ogtk.vi" \
  --search-path samples/OpenG/extracted --long
```

```text
Signature:
  ~ input: file name: str -> Path
  ~ output: new filename: str -> Path

Operations:
  + Build Path [prim]
  + Convert File Extension (String)__ogtk.vi [iUse]
  - Match Pattern [prim]
  + Strip Path [prim]
  - regular expression [select]

Constants:
  - (unnamed str) = "'\\\\.[~\\\\.]*$'"

Wiring:
  + Convert File Extension (Path)__ogtk.vi -> Convert File Extension (String)__ogtk.vi
  + Convert File Extension (String)__ogtk.vi -> Build Path
  + Convert File Extension (String)__ogtk.vi -> Convert File Extension (Path)__ogtk.vi
  - Convert File Extension (String)__ogtk.vi -> Match Pattern
  - Convert File Extension (String)__ogtk.vi -> regular expression
  - Match Pattern -> Convert File Extension (String)__ogtk.vi
  + Strip Path -> Convert File Extension (String)__ogtk.vi
  - regular expression -> Convert File Extension (String)__ogtk.vi

Structures:
  - regular expression (selector <- Convert File Extension (String)__ogtk.vi; frame Default: Match Pattern, Less Than 0?, Select, Format String, str constant, str constant, str constant)
```

The structured report has five sections — `Signature`, `Operations`,
`Constants`, `Wiring`, `Structures` — each listing only the entries that
changed; a section with no changes is omitted entirely. If the two VIs are
identical, both the default and `--long` output print a single line, `No
changes detected.`, instead.

## Exit codes

`diff` exits `0` whenever it successfully compares the two VIs — **including
when they're identical**. The exit code does not tell you whether the VIs
differ; a CI step that needs to gate on "did this VI change" has to check the
printed output (`No changes detected.` vs. a diff/report) rather than the
exit code.

`diff` exits `1` when `vi_a` or `vi_b` doesn't exist, or when loading either
VI raises a `ValueError`, `FileNotFoundError`, or `KeyError` (for example, a
path that isn't a `.vi` file) — both print a one-line `Error: ...` message to
stderr. A `.vi` that pylabview itself can't parse (corrupt or truncated data)
raises `RuntimeError`, which isn't one of the caught types: `diff` still
exits `1`, but stderr gets a full Python traceback instead of a clean `Error:`
line.

## Notes

- `diff` compares two individual `.vi` files. To cover a change set in CI,
  invoke it once per changed VI from your pipeline.
- `diff` runs without a `.lvkit/` resolution store — pass `--project-root` if
  one exists, but `diff` doesn't require [`setup`](setup.md) to have been run
  first.
- If a SubVI referenced by either VI isn't found on `--search-path`, `diff`
  doesn't fail: that SubVI is recorded as an unresolved dependency and the
  comparison proceeds with what could be loaded.
- Because `vi.lib` auto-detection uses whatever LabVIEW is installed on the
  machine (see [SubVI & vi.lib resolution](subvi-resolution.md)), pass
  `--no-auto-vilib` for a diff that's identical across machines and in CI.

## See also

- [describe](describe.md) — a snapshot of a single VI's signature and operations.
- [render](render.md) — a faithful diagram of a single VI, for visual comparison.
- [structure](structure.md) — the same terminal/operation/wiring breakdown for `.lvlib`/`.lvclass` members.
- [SubVI & vi.lib resolution](subvi-resolution.md) — the resolution flags `diff` shares with the other commands.
