# Provenance — issue #30 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/30
- **Reporter:** an lvkit user (LV2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files:**
  - `visible-frame-case-sequence.vi` (renamed from the reporter's
    "[LV2025] Bug Visible Frame - Case Structure and Stacked Sequence.vi"; unmodified
    bytes). sha256:
    `d42322b0d5db6dec67af7b53198f219d23b1d6420841c913a7903e504f375f42`
  - `visible-frame-conditional-disable.vi` (renamed from the reporter's
    "[LV2025] Bug Visible Frame - Conditional Disable Structure.vi", from the issue's
    second comment; unmodified bytes). sha256:
    `148a5d61b4d41f8cfe18ecda1d4279a956d1759c2b139143d21b4c893e1d9d65`

  Each reporter `Test LVKit.lvproj` is omitted — the VIs are self-contained and
  render standalone.

## What it reproduces

A Case Structure, a Stacked Sequence, and a Conditional Disable Structure were each
saved with a non-first frame showing (the "visible frame"), but lvkit always opened
frame 0 (or, for the disable structure, the *enabled* frame). The displayed frame is
the structure node's heap `dIdx` — but only when it is a valid local index
(`0 <= dIdx < n_frames`); an out-of-range `dIdx` is a legacy global-diagram ordinal
(what issue #81 saw) and must be rejected. In both repros the structures carry
`dIdx=1` (their 2nd frame), so all now open on frame 1. For a Conditional Disable the
saved visible frame can be a *disabled* frame (viewed but not compiled), so the
visible frame (`dIdx`) is preferred over the enabled one (`activeDiag`). Fixed in the
PR that adds these fixtures.
