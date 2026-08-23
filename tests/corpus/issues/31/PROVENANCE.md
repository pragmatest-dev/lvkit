# Provenance — issue #31

- **Source issue:** https://github.com/pragmatest-dev/lvkit/issues/31
  ("Disable Structures show incorrect frame names")
- **File:** `disable-structure-frame-names.vi`
  (renamed from `[LV2025] Bug Disable Structure Frame Names.vi`)
- **License:** Apache-2.0 — contributed to this repository as a bug
  reproduction per the bug-report issue template (see
  `tests/corpus/issues/README.md`).
- **sha256:** `4cd08cf81eafef352bd48cedc79fad42c03c2e06183b6e8c8eeef3a30dc93848`
- **Saved from:** LabVIEW 2025 (25.3.2).

## What it reproduces

A single VI holding one of each disable-family structure:

- a **Diagram Disable** structure (`Enabled` / `Disabled`),
- a **Conditional Disable** structure (`Default` / `RUN_TIME_ENGINE==False`),
- a **Type Specialization** structure (`[0] Declined` / `[1] Accepted` /
  `[2] Ignored`).

lvkit previously mislabeled these — `Frame N` placeholders, an Enabled/Disabled
inversion, and a duplicate `Disabled`. Frame labels are now reconstructed
data-driven from `activeDiag` + the detected subtype (see
`parser/nodes/disable.py`); this fixture guards that.
