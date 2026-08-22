# Provenance — issue #35 / #39 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/35 (and #39)
- **Reporter:** an lvkit user (LV2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files:**
  - `objects-hidden-by-structures.vi` (renamed from the reporter's
    "[LV2025] Objects not correctly hidden by structures.vi"; unmodified bytes).
    sha256:
    `a7ad94d958cf2e299a27ed736c7f5fddb3db0255abd3708644aaafd7ad0c6430`

  The reporter `Test LVKit.lvproj` is omitted — the VI is self-contained and
  renders standalone.

## What it reproduces

Two overlapping For loops plus a For loop nested inside the larger one, and a
`0 → Numeric` wire that runs behind the larger loop. LabVIEW draws each diagram's
children in `zPlaneList` order (backmost first) with OPAQUE structure bodies, so
a front structure occludes what is behind it. lvkit used to draw every structure
outline-only (no fill) in three flat passes over a uid-sorted node list, so
overlapping structures, nested loops, and wires all showed *through* each other.

Reference (`ref-labview.png` in the issue): the small top-left loop is FRONT
(last in the root `zPlaneList`) and occludes the big loop's corner; the
`0 → Numeric` wire is occluded where it passes behind the front loop.

Fixed by the ONE hierarchical composite render tree: a single recursive
`root.draw()` walk nests every element by graph containment, orders siblings by
the layout's `zPlaneList` paint rank (`Scene.z_order`), and each structure paints
an opaque body before its own inner wires and children. See the draw-order
assertion in `tests/test_issue_corpus.py`.
