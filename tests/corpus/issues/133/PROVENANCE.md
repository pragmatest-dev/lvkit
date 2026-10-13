# Provenance — issue #133 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/133
- **Reporter:** an lvkit user (LV2024), attached as a bug reproduction
  (`diff_bug.zip`).
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files** (unmodified bytes from the attachment):
  - `Run_before.vi` — sha256
    `12e7976110b337acf104039d350b3b7e075054d322ce6f9b8cd2449ae661d734`
  - `Run_after.vi` — sha256
    `26a005f694794863fca61423e90981f91ca2261b1a2a64ad030f2acc4742a102`

## What it reproduces

A For Loop passes `error in` → `error out` through two plain tunnels; in the
after version those tunnels are replaced by a shift register (and a string
constant is added inside the loop). `lvkit diff` reported only the added
constant: the wire diff follows wires through tunnels, so the data flow looked
unchanged, and nothing compared a structure's own tunnels. Fixed by comparing
each matched structure's border terminals across versions — the loop is now
reported as modified with "tunnel → shift register".
