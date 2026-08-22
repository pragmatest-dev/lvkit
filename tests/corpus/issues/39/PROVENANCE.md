# Provenance — issue #39 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/39
- **Reporter:** an lvkit user (LV2025), attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files:**
  - `zorder-not-respected.vi` (renamed from the reporter's
    "[LV2025] Objects Z-order not respected.vi"; unmodified bytes).
    sha256:
    `c9e52da36f866f1a8fc6cbdaff4fb7aed6fc6a82f5875ffa5999983f0ccfabe9`

  The reporter's `Test LVKit.lvproj` is omitted — the VI is self-contained and
  renders standalone.

## What it reproduces

Two overlapping **subVI nodes** ("Open/Create/Replace File"), plus three
control→indicator pairs (Numeric `I32` + `y` → Add → `x+y`; String in → String
out). This is the **node-vs-node** z-order case (distinct from #35, whose repro
is overlapping *structures*): LabVIEW draws each diagram's children in
`zPlaneList` order (front object last), so the FRONT node occludes the corner of
the one behind it. lvkit used to draw objects in three flat passes over a
uid-sorted node list, so overlapping nodes showed *through* each other.

Reference (the image attached to issue #39): of the two overlapping file nodes,
the front one covers the back one's overlapping corner.

Fixed by the ONE hierarchical composite render tree: a single recursive
`root.draw()` walk orders siblings by the layout's `zPlaneList` paint rank
(`Scene.z_order`) and paints each node opaquely, so the front node occludes the
back. See the #39 z-order assertion in `tests/test_issue_corpus.py`.
