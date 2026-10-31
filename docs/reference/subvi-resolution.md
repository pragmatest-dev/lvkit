# SubVI & vi.lib resolution

A VI rarely stands alone — it calls SubVIs and uses classes and type
definitions, and those may live in your project, in LabVIEW's
`vi.lib`/`user.lib`/`instr.lib`, or in a third-party library. These flags tell
lvkit where LabVIEW's library folders are on this machine.

Most lvkit commands accept the same set of flags for this, documented once
here rather than repeated on every command page. Not every command needs
resolution to succeed to do its job: [`describe`](describe.md), `diff`,
`visualize`, `docs`, and `render` work on any VI regardless of what resolves
— an unresolved dependency just shows up unresolved (see [Unresolved
dependencies](#unresolved-dependencies) below). [`generate`](generate.md) is
the command whose *output* depends on how much resolves.

## How a dependency is found

LabVIEW saves, inside every VI, the path of each file the VI uses. lvkit finds a
dependency **only at that recorded path** — it never looks a file up by its
name. A recorded path is one of:

- **Relative** to the calling VI (`../shared/PicoStatus.vi`): lvkit joins it
  onto the calling VI's own folder.
- **In a LabVIEW library folder** (`<vilib>/Utility/error.llb/Clear Errors.vi`,
  `<userlib>/_OpenG.lib/...`, `<instrlib>/...`): lvkit joins it onto the folder
  you name with `--vilib`, `--userlib` or `--instrlib` (or the auto-detected
  install — see [Auto-detection](#auto-detection)).
- **Absolute** (`C:\Users\me\lib\x.vi`): lvkit uses it as it is, when that file
  exists on this machine.

lvkit also reads a member stored inside a `.llb` on that path, a class or
library member stored beside its `.lvclass`/`.lvlib`, and the one file whose
name differs from the recorded one only in letter case (LabVIEW file names
ignore case). If nothing is at the recorded path, the dependency is **missing**:
it keeps the path its caller recorded, and lvkit carries on. A file with the
same name somewhere else is never used in its place — it may be a different
VI.

## Options

| Option | Description |
|--------|-------------|
| `--vilib DIR` | Path to LabVIEW's `vi.lib` directory on disk. When set, `<vilib>/...` recorded paths resolve to real files under it, their terminal layouts are captured automatically, and lvkit caches the result to `.lvkit/vilib/` for future runs (only if a `.lvkit/` store exists — see `--project-root` below). When omitted, lvkit auto-detects a local LabVIEW install unless `--no-auto-vilib` is set — see [Auto-detection](#auto-detection) below. |
| `--userlib DIR` | Path to LabVIEW's `user.lib` directory on disk (where toolkits such as OpenG install). When set, `<userlib>/...` recorded paths resolve to real files under it. |
| `--instrlib DIR` | Path to LabVIEW's `instr.lib` directory on disk (instrument drivers). When set, `<instrlib>/...` recorded paths resolve to real files under it. |
| `--project-root DIR` | Project root containing a `.lvkit/` resolution store. Defaults to walking up from the current directory looking for `.lvkit/`. No store is required: without one, resolution still runs using only lvkit's bundled primitive and vi.lib data. A store (created by [`setup`](setup.md)) adds your project's own mappings and lets a `--vilib` run cache newly-observed terminal layouts to `.lvkit/vilib/` for future runs. |
| `--no-auto-vilib` | Disable auto-detection of a locally installed LabVIEW's `vi.lib`/`user.lib`/`instr.lib` when `--vilib` is not given. Use this for reproducible, machine-independent runs (e.g. CI). |

`--search-path` no longer exists. Passing it is a usage error (exit code 2)
whose message names the flags above: name the folder a recorded
`<userlib>`/`<vilib>`/`<instrlib>` path starts from instead. A folder of
extracted toolkit VIs (for example an OpenG package) is used by naming the
folder that plays the role of `user.lib` — the one holding `_OpenG.lib` — with
`--userlib`.

## Auto-detection

When `--vilib` is omitted and `--no-auto-vilib` is not set, lvkit auto-detects
a local LabVIEW install and uses its `vi.lib`/`user.lib`/`instr.lib` (see
[`detect`](detect.md)); an explicit `--userlib` or `--instrlib` still wins.
Detection is best-effort and non-fatal: if none is found, resolution proceeds
as if `--no-auto-vilib` had been passed. When an install is found, lvkit
prints the version, path, and detection source to stderr:

```text
lvkit: auto-detected LabVIEW 2021 vi.lib at /usr/local/natinst/LabVIEW-2021-64/vi.lib (linux-glob)
```

This makes results depend on what's installed on the machine running lvkit.
For runs that must be identical everywhere — CI, code review, committed
artifacts — pass `--no-auto-vilib` so the output depends only on the input VI
and the library folders you name.

## Unresolved dependencies

A SubVI, class or type definition that isn't at its recorded path — and isn't
one of the vi.lib/OpenG/driver VIs lvkit already ships mapping data for — is
not a resolution failure. lvkit records it as missing, with the path its
caller recorded, and keeps going:

- [`describe`](describe.md), `diff`, `visualize`, `docs`, and `render` just
  show it by name, without a signature or description (`describe` documents
  this for its own output in its [Notes](describe.md#notes)).
- `generate` reports each call of it as one `missing_vi` [gap](generate.md#gaps):
  a warning naming the VI and the path its caller recorded, and a `raise
  NotImplementedError(...)` where the call would run, which lists the types and
  directions of the terminals the call wires. No module is written for the
  missing VI.

This is why the CI setup above (`--no-auto-vilib`, no `--vilib`) doesn't
block on every `<vilib>`/`<userlib>` call: lvkit ships its own mapping data
for a large set of common vi.lib, OpenG, and driver VIs, matched by name and
independent of `--vilib`/`--userlib`/auto-detection. Those still generate
working Python with no LabVIEW install anywhere. A `<vilib>`/`<userlib>` VI
lvkit doesn't already know about is that same `missing_vi` gap at
`generate` time. A call lvkit *does* know about, but can't fully map (an
unmapped terminal, an unresolved primitive), is a [gap](generate.md#gaps) at
that call: a warning, and a `raise NotImplementedError(...)` where it would
run.

## Example

The following runs [`describe`](describe.md) on a VI that calls OpenG VIs
(recorded as `<userlib>/_OpenG.lib/...`), with the OpenG files extracted under
`libraries/extracted/user.lib`, and no auto-detection:

```bash
lvkit describe "src/MyVI.vi" \
  --userlib libraries/extracted/user.lib \
  --no-auto-vilib
```

## See also

- [generate](generate.md) — the command that consumes the `.lvkit/` store and reports a gap for each unresolved primitive or vi.lib VI.
- [setup](setup.md) — creates the `.lvkit/` resolution store these flags read from and cache into.
- [detect](detect.md) — the local-LabVIEW detection this page's auto-detection uses when `--vilib` is omitted.
