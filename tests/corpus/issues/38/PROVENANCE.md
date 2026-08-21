# Provenance — issue #38 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/38
- **Reporter:** an lvkit user (LV2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files:**
  - `auto-concatenating-tunnel.vi` (renamed from the reporter's
    "[LV2025] Auto-concatenating tunnel wrong display.vi"; unmodified bytes).
    sha256:
    `b65f11ab6ecb7bedea075314e51e72daad7b701b510c115b102f9698752ee95c`

  The reporter's `Test LVKit.lvproj` is omitted — the VI is self-contained and
  renders standalone.

## What it reproduces

A For Loop with three output tunnels — a last-value tunnel (`Numeric`), an
auto-indexing tunnel (`Array`), and an **auto-concatenating** tunnel (`Array 2`,
fed by Build Array). LabVIEW draws the concatenating tunnel with a distinct glyph
(two side-by-side blocks), but lvkit drew it identically to the auto-indexing
tunnel (`[ ]` brackets).

Root cause: the renderer chose the tunnel glyph from a separate binary
`indexing_tunnels` geometry flag that lumped `CONCATENATING` in with `INDEXING`
(and mislabelled passthrough *input* tunnels as auto-index). The parsed
`TunnelMode` on the terminal already distinguished all four modes; the fix drives
the glyph from that single source instead — `INDEXING` → `[ ]` brackets,
`CONCATENATING` (output-only) → two side-by-side blocks, `LAST_VALUE` /
`PASSTHROUGH` → a filled block. Fixed in the PR that adds this fixture.
