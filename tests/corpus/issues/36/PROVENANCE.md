# Provenance — issue #36 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/36
- **Reporter:** an lvkit user (LV2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **File:** `bundle-unbundle-names.vi` (renamed from the reporter's
  "[LV2025] Bug Unbundle Bundle Nodes Incorrect Element Names.vi"; unmodified bytes).
- **sha256:** `2e3a7c7f1914d02993b6ad1b02a4329216e466e2bc7d3cdc96686430ce655f33`

## What it reproduces

Named Bundle/Unbundle nodes, IPE cluster border nodes, and waveform/digital-data
component nodes rendered cluster field names WRONG — e.g. "Elements in array" (an
array type descriptor's internal dimension label) leaking in as a cluster field
name, because a cluster child's consolidated (TopLevel) TypeID was resolved as a
flat VCTP index. Fixed in the PR that adds this fixture.
