# Provenance — issue #30 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/30
- **Reporter:** an lvkit user (LV2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **File:** `visible-frame-case-sequence.vi` (renamed from the reporter's
  "[LV2025] Bug Visible Frame - Case Structure and Stacked Sequence.vi"; unmodified
  bytes). The reporter's `Test LVKit.lvproj` is omitted — the VI is self-contained
  and renders standalone.
- **sha256:** `d42322b0d5db6dec67af7b53198f219d23b1d6420841c913a7903e504f375f42`

## What it reproduces

A Case Structure and a Stacked Sequence were each saved with a non-first frame
showing (the "visible frame"), but lvkit always opened frame 0. The displayed
frame is the structure node's heap `dIdx` — but only when it is a valid local
index (`0 <= dIdx < n_frames`); an out-of-range `dIdx` is a legacy global-diagram
ordinal (what issue #81 saw) and must be rejected. Here both structures carry
`dIdx=1` (their 2nd frame), so both now open on frame 1. Fixed in the PR that adds
this fixture.
