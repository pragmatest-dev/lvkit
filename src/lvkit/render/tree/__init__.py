"""The composite render tree — ONE hierarchical containment tree drawn by a
single recursive ``root.draw()`` walk.

Starting at the block-diagram root, every element (wire, glyph, node, structure)
draws itself; a structure draws its OPAQUE body first, then its own inner wires,
then its children in zPlaneList paint order, then its border terminals last —
and an interactive structure recurses its FRAMES. There is no flat scene-list
draw loop and no ``layers`` dict; the single draw entry is ``root.draw()``.
"""
