"""Errors the graph raises when a reference names more than one VI."""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap


class AmbiguousVIReferenceError(ConversionGap):
    """A bare name/qname lookup (``resolve_vi_name``) matched two or more
    on-disk VIs that are genuinely DIFFERENT (distinct qualified identity),
    not duplicate copies of the same VI -- or a caller's recorded VI, absent at
    its recorded path, matched several files of the same qualified name. Raised
    instead of silently guessing one -- resolve by full source path instead."""
