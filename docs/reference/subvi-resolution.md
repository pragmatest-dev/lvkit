# SubVI & vi.lib resolution

A VI rarely stands alone — it calls SubVIs, and those may live in your
project, in LabVIEW's `vi.lib`/`user.lib`, or in a third-party library. These
flags tell lvkit where to look for them.

Most lvkit commands accept the same set of flags for this, documented once
here rather than repeated on every command page. Not every command needs
resolution to succeed to do its job: [`describe`](describe.md), `diff`,
`visualize`, `docs`, and `render` work on any VI regardless of what resolves
— an unresolved dependency just shows up unresolved (see [Unresolved
dependencies](#unresolved-dependencies) below). [`generate`](generate.md) is
the command whose *output* depends on how much resolves.

## Options

| Option | Description |
|--------|-------------|
| `--search-path DIR` | An *extra* directory to search for SubVIs referenced by the VI. Repeat the flag to add several roots. lvkit always checks the calling VI's own directory first, then your project root (auto-detected — see below), then each `--search-path` root — a direct name match at the root, then recursively through subfolders. You only need this for SubVIs that live outside the project (e.g. an extracted library elsewhere on disk). |
| `--project-root DIR` | Project root containing a `.lvkit/` resolution store. Defaults to walking up from the current directory looking for `.lvkit/`. No store is required: without one, resolution still runs using only lvkit's bundled primitive and vi.lib data. A store (created by [`setup`](setup.md)) adds your project's own mappings and lets a `--vilib` run cache newly-observed terminal layouts to `.lvkit/vilib/` for future runs. The detected project root (the directory holding `.lvkit/`) is also searched for SubVIs automatically, so project dependencies resolve without an explicit `--search-path`. |
| `--vilib DIR` | Path to LabVIEW's `vi.lib` directory on disk. When set, `<vilib>` dependency refs resolve to real `.vi` files, their terminal layouts are captured automatically, and lvkit caches the result to `.lvkit/vilib/` for future runs (only if a `.lvkit/` store exists — see `--project-root` above). When omitted, lvkit auto-detects a local LabVIEW install unless `--no-auto-vilib` is set — see [Auto-detection](#auto-detection) below. |
| `--userlib DIR` | Path to LabVIEW's `user.lib` directory on disk. When set, `<userlib>` dependency refs resolve to real `.vi` files, the same as `--vilib` does for `<vilib>` refs. |
| `--no-auto-vilib` | Disable auto-detection of a locally installed LabVIEW's `vi.lib`/`user.lib` when `--vilib` is not given. Use this for reproducible, machine-independent runs (e.g. CI). |

## Auto-detection

When `--vilib` is omitted and `--no-auto-vilib` is not set, lvkit auto-detects
a local LabVIEW install and uses its `vi.lib`/`user.lib` (see
[`detect`](detect.md)). Detection is best-effort and non-fatal: if none is
found, resolution proceeds as if `--no-auto-vilib` had been passed. When an
install is found, lvkit prints the version, path, and detection source to
stderr:

```text
lvkit: auto-detected LabVIEW 2021 vi.lib at /usr/local/natinst/LabVIEW-2021-64/vi.lib (linux-glob)
```

This makes results depend on what's installed on the machine running lvkit.
For runs that must be identical everywhere — CI, code review, committed
artifacts — pass `--no-auto-vilib` so the output depends only on the input VI
and your explicit `--search-path`s.

## Unresolved dependencies

A SubVI or `<vilib>`/`<userlib>` reference that can't be resolved — not found
on any `--search-path`, and not one of the vi.lib/OpenG/driver VIs lvkit
already ships mapping data for — is not a resolution failure. lvkit records
it as unresolved and keeps going:

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

The following runs [`describe`](describe.md), which needs no resolution to
succeed, with explicit search paths and no auto-detection:

```bash
lvkit describe "src/MyVI.vi" \
  --search-path src/ \
  --search-path libraries/extracted/ \
  --no-auto-vilib
```

## See also

- [generate](generate.md) — the command that consumes the `.lvkit/` store and reports a gap for each unresolved primitive or vi.lib VI.
- [setup](setup.md) — creates the `.lvkit/` resolution store these flags read from and cache into.
- [detect](detect.md) — the local-LabVIEW detection this page's auto-detection uses when `--vilib` is omitted.
