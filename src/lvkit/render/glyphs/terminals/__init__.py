"""Border-terminal glyphs — one class per structure border-terminal KIND, one
file each.

A border terminal is a decoration on a structure's edge: a loop count (N) or
iteration (i), a while-loop conditional (cond), a case selector, a shift
register, or a data TUNNEL (plain / auto-indexing / auto-concatenating). Each
kind draws itself from an already-inset bounds rect + theme + injected config;
the composite tree unpacks the scene ``RenderBorderTerminal`` and picks the right
class via the factory. Glyphs import neither ``scene`` nor ``draw``.
"""
