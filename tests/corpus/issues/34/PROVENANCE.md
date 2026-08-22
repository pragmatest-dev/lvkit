# Provenance — issue #34 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/34
- **Reporter:** an lvkit user (LV2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files:**
  - `hidden-iteration-terminal.vi` (renamed from the reporter's
    "[LV2025] Loop iteration terminal visibility not respected.vi"; unmodified
    bytes). sha256:
    `8422551e9b308fb9830ab5501f058f9c0d03a72b6e54c62e5a60a989478330d3`

  The reporter's `Test LVKit.lvproj` is omitted — the VI is self-contained and
  renders standalone.

## What it reproduces

A For Loop and a While Loop, each with its **iteration terminal (`i`) hidden**
via LabVIEW's "Visible Items" menu. lvkit drew `i` on both anyway.

Root cause: the renderer emits the loop iteration terminal unconditionally.
Per-terminal visibility is encoded as **objFlags bit `0x800000` on the
terminal's inner `sRN` `<term>`** (not on the `loopIndexDCO` — every
`loopIndexDCO` in the corpus carries the identical no-objFlags/`termBMPs=1`
signature). Verified data-driven: across the whole corpus every visible
`i`/`N`/stop terminal has the bit CLEAR (537 `i`, 438 `N`, 101 stop — zero set),
while this repro's two hidden `i` terminals have it SET.

The fix reads that bit in the semantic loop parser and carries the hidden
KINDS (`"i"`/`"N"`/`"cond"`) on the graph `LoopNode.hidden_border_terminals`;
the renderer omits a hidden terminal's glyph (still emitting it into the scene,
tagged `hidden`, so a future "show hidden" viewer toggle can reveal it). The bit
is generic, so a hidden `N` or stop terminal is respected too. Fixed in the PR
that adds this fixture.
