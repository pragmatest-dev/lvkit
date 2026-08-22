"""Leaf node glyphs — one class per file.

The shared ``Glyph`` protocol and the text-fitting / split-box / node-tile
helpers live in :mod:`base`; every other module here holds exactly one glyph
class (arith, array_*, bundle, constant, …). ``render/glyph.py`` re-exports them
so existing ``from .glyph import X`` call sites keep working.
"""
