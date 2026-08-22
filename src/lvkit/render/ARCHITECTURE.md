# Render architecture — the enforceable contract

The renderer turns an `InMemoryVIGraph` + `Layout` into one SVG string. This
file is the contract the code is grep-gated against; change the code and this
doc together.

## Two roles, two contracts (do not merge them by accident)

There are exactly two kinds of drawable, kept deliberately separate:

- **`Glyph` — a draw-only leaf FACE.** `draw(backend, bounds, theme)`. It is
  handed a rect and paints one node's appearance into it (an Add triangle, a
  constant box, a bundle rail). It has no place in the containment tree, no
  z-order, no children of its own in the diagram sense; a leaf may compose its
  face from private sub-pieces (cluster fields, the array little-box motif), but
  those are internal to the face, never tree citizens. Glyphs live under
  `glyphs/` (`glyphs/nodes/*`, `glyphs/structures/*` body glyphs,
  `glyphs/terminals/*`), one kind per file, dispatched only by the
  kind→class factories (`resolve_glyph`, `structure_body_glyph`,
  `border_terminal_glyph`). Glyphs import neither the graph nor the layout.

- **`RenderObject` — a placed CITIZEN of the composite tree.**
  `draw(backend, theme)`. It knows where it sits (its bounds/containment come
  from the view model) and draws itself; a container additionally recurses its
  children in paint order. `composite.py` holds the hierarchy:
  - `NodeObject` — a leaf diagram object; draws its resolved `Glyph` face.
  - `StructureObject` — a structure; draws its opaque body (a body `Glyph`),
    clips, then recurses its body diagram or per-frame `lv-frame` groups.
  - `DiagramObject` — the block-diagram root; the SINGLE draw entry
    (`draw_scene` → `build_render_tree` → `root.draw()`), which also emits the
    two overlay passes (dropdown menus, then connector-help panels) last.
  - `DiagramContent` — the wires + z-ordered children + FP terminals + coercion
    dots of ONE diagram (root, a loop body, or one frame). Not a border itself.

`Glyph` says "paint into this rect"; `RenderObject` says "I am placed, I draw
myself (and my children)." They may unify later (a leaf's face could become a
field on the object — see the plan's R-4), but until then the split is real and
named honestly. The word "Element" does not appear.

## Draw order (byte-load-bearing — see `tests/test_render_emission_order.py`)

- **Paint order = zPlaneList back-to-front.** `Scene.z_order` is the layout's
  document index; index 0 is the FRONTMOST object, drawn LAST. Siblings sort
  DESCENDING by rank (reverse-stable); unknown-rank items tie at -1 and keep
  their append order (nodes appended before structures).
- **A structure draws its opaque body FIRST** (so a later sibling occludes an
  earlier sibling's whole subtree — #35/#39), then its inner wires, then its
  children, then its border terminals; contents are clipped to the structure
  bounds, border terminals are not.
- **clipPath ids number by draw-call ARRIVAL order** and the `<defs>` emit in
  that order (`backend.py`: `setdefault(clip, len(self._clip_ids))`).
- **Overlays (menus, help panels) emit LAST**, in BUILD order, above everything.

## Pipeline

`parser/layout.py` (geometry, paint rank) + graph semantics → `scene.py`
(`Scene` view model; resolves each node's `Glyph`) → `composite.py`
(`build_render_tree` → one recursive `root.draw()`) → `backend.py`
(`SvgBackend`) → SVG. `draw.py` holds the leaf pixel helpers a `NodeObject`
reuses; there is NO flat scene-list draw loop.
