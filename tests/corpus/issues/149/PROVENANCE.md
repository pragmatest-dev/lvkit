# Provenance — issue #149 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/149
- **Reporter:** metman-oss (LabVIEW 2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files** (unmodified bytes from the attachment):
  - `SV Sample.lvproj` — sha256
    `8e996a450f43bef5d0faff4a938782a38f5ec3dade421163790f8dafdfc89990`
  - `SV Sample.lvlib` — sha256
    `34c6e37817c030bb9d0a3e1c121351fe754b949471b7cbb24960aabbc44a4044`
  - `SV Sample.vi` — sha256
    `9923c007ff5d49cd50f0ef565fbc8ef9403ad08a655bb63fb3fb6bef617de170`

## What it reproduces

`SV Sample.lvlib` holds one Single Process Shared Variable `Value` (DBL, with a
timestamp). `SV Sample.vi` has three Shared Variable nodes in a flat sequence:
write 1.5, read, read with Show Timestamp. The variable's library is recorded
only in the VI's block-diagram link table, in an `HpVL` record.
