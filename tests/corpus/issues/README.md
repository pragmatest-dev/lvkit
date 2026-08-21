# Issue reproduction corpus

Minimal LabVIEW files attached to lvkit GitHub issues as **bug reproductions**,
kept here as permanent **regression fixtures** — each `<N>/` holds the repro from
issue #N. A test renders / describes / converts the real file and asserts the
corrected behaviour, so a fixed bug stays fixed.

This is **separate** from the bulk sample corpus (`samples/`, gitignored and
pulled via `scripts/pull_samples.sh`). These are tiny, single-bug repros that are
small enough to commit and belong *with* the fix they guard.

## Licensing

**Reproduction code attached to an lvkit issue is contributed to this repository
under the Apache License 2.0** (the repo's license), so it can live here as a
regression fixture. Each `<N>/PROVENANCE.md` records the source issue, the
reporter, and this license. If a repro derives from other OSS (e.g. JKI‑VI‑Tester,
BSD‑3‑Clause), the derivation and upstream license are noted there too.

Contributors are told this expectation up front (see the bug-report issue
template). If you attach a repro you can't license this way, say so in the issue
and we'll keep it out of the tree and pull it locally instead.

## Adding a fixture

```
tests/corpus/issues/<N>/
  <the minimal .vi / project files>
  PROVENANCE.md      # source issue URL, reporter, license, sha256
```

Keep it minimal — the smallest file set that still reproduces. A single VI is
ideal (its type info is embedded); include typedef `.ctl`s or a `.lvproj` only
when the render genuinely needs them.
