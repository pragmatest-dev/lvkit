"""Errors the graph raises when the file a reference names cannot be determined."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from lvkit.conversion_gap import ConversionGap


class DependencyFileError(ConversionGap):
    """The file a dependency names cannot be determined. ``candidates`` are the
    files involved, as paths the loader shows project-relative; the message
    carries no path. Loading a dependency records this as the reason the
    dependency is missing and goes on."""

    def __init__(self, message: str, candidates: Sequence[Path] = ()):
        super().__init__(message)
        self.candidates = tuple(candidates)


class AmbiguousVIReferenceError(DependencyFileError):
    """A bare name/qname lookup (``resolve_vi_name``) matched two or more
    on-disk VIs that are genuinely DIFFERENT (distinct qualified identity),
    not duplicate copies of the same VI -- or a caller's recorded VI, absent at
    its recorded path, matched several files of the same qualified name, or
    several files differing only in case. Raised instead of silently guessing
    one -- resolve by full source path instead."""


class UnreadableDependencyError(DependencyFileError):
    """A file that could be the dependency's could not be read, so whether it is
    the dependency is unknown."""
