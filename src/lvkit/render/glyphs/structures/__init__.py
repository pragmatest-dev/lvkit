"""Structure body glyphs — one class per structure KIND, one file each.

Every structure paints an OPAQUE body (the occlusion fix for #35/#39: a later
sibling's body covers an earlier sibling's whole subtree) plus its signature
outline. The outline chrome that needs frame/selector data (case selector,
sequence dividers, border terminals) is drawn by the composite tree using the
existing helpers; the glyph owns only what needs bounds+theme.
"""
