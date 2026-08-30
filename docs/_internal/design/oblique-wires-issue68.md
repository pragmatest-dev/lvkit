# Oblique wires (issue #68) — investigation

Status: **investigated, not fixed** — the fix needs an LV visual confirmation of
the primitive icon-placement rule (a maintainer call; do not ship a guessed
offset). Repro attached to the issue (LV2025 Q3): a VI whose horizontal wires
render as slight diagonals.

## Root cause

Every wire in the repro **faithfully decodes** (`layout.wire_by_uid` = 37/37), so
this is **not** a decode-coverage failure and **not** the autorouter. The decoded
`compressedWireTable` geometry is inherently orthogonal, and its leaves are
**snapped onto our computed terminal centers** (`parser/wire_table.py`). When a
terminal center is wrong, that snap tilts the wire's final segment into a
diagonal.

The wrong centers are on **source primitive terminals**. The prim icon-centering
offset (`parser/layout.py:357-380`) maps a primitive's icon-relative `termBounds`
into diagram space by **centering the term-bbox extent inside the node's clickable
bounds**:

```
off_y = (nodeH − extentH) / 2 − emin_y
```

That assumption is false for these nodes. Measured on the repro:

- 4 wires oblique, worst **8.5px** offset; sinks are all `fPTerm` boxes (explicit,
  clean box-centers — reliable). Source/sink y-mismatch is ±8.5, sign varying.
- Source node uid 109 (a `prim`): node-bounds height **17**, but term-extent
  height **21** — the terminals extend *beyond* the node box, so centering the
  extent in the box mislocates them.
- Expandable prims (Index Array, etc.) are the worst case: as the node grows, the
  term-extent is not centered in the grown bounds.

So: source prim terminal centers drift ±8.5px → the faithful wire snapped to them
tilts → oblique. It also explains "terminals look slightly larger / mispositioned."

## Recommended fix (task C)

Replace the term-extent-centering heuristic with the **real icon geometry**: LV
primitives sit on a fixed icon grid (≈32px — the size the doc-image prototype
validated), placed by LV's actual rule; terminals are at fixed positions relative
to that icon. Needs confirmation of LV's icon-placement rule (top-left vs centered
vs anchored). Clean-room reference for the icon box + offset if the parse can't
give it: `docs/_internal/design/glyph_terminal_role_detection_prototype.py`
(validated 31×31 / 31×51 icon boxes, maps doc image ↔ `termBounds` 1:1).

Acceptance metric: on the repro, oblique wire-segment count → 0.

## Related, separate

- **Autorouter diagonal** (`render/wire_router.py:95-96`): the "straight (0 bends)"
  candidate emits `[p1, p2]` — a diagonal — when endpoints are within `align_tol`
  but not exactly aligned. Latent (it does not fire on this VI: 0 routed wires),
  but it's the "autoroute orthogonal" half. Entangled with the scene center-splice
  (`render/scene.py`: `branch = [src_center, *mid, dst_center]`), so not a clean
  one-liner.
- **Half-stroke inset** (bounding-box outlines): structures inset by ½·stroke
  (`render/glyphs/structures/base.py`), but the generic node box draws at the raw
  bbox (`render/glyphs/nodes/base.py:220`) and terminals use a %-fudge
  (`render/glyphs/terminals/base.py`, `inset(frac=0.075)`) instead of the geometric
  rule — outlines bleed ½·stroke outside, reading slightly large. Fix: own the
  half-stroke inset in one place (backend/`stroke_rect`) and reconcile the two
  existing ad-hoc insets.
