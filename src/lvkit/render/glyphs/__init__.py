"""Decomposed render glyphs — one KIND per class, one class per file.

Each glyph draws ONE kind of diagram element (a structure body, a leaf node, a
wire, a border terminal) given only a backend, a bounds rect, and a theme. A
glyph imports NEITHER ``scene`` NOR ``draw`` — it is pure backend drawing, so
adding a new specialty is one new file with one class plus one line in the
kind→class factory, and nothing else is touched.
"""
