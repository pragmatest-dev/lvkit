"""What a function's input terminal holds when nothing is wired to it.

Both the primitive and the VI catalogs record it the same way, on the terminal:
``default`` is the LabVIEW value NI documents (the value a decoded constant of
that type has: a number, a Boolean, a string, or a path text), and
``default_behavior`` is a documented behaviour that is no value. A terminal with
neither has no documented default.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from .models import ScalarValue


class DefaultBehavior(StrEnum):
    """A documented default that is no value."""

    REST = "rest"
    APPEND = "append"
    ONE_ELEMENT = "one element"
    LAST_ELEMENT = "last element"
    NO_PADDING = "no padding"
    EMPTY_VARIANT = "empty variant"
    CALLING_APPLICATION = "application of the calling VI"
    GENERIC_VI_REFERENCE = "generic VI reference"
    UNCHANGED = "unchanged"
    ALL_ATTRIBUTES = "all attributes"
    NEXT_INDEX = "previous index + 1"
    ENTIRE_DIMENSION = "entire row or column"
    LAST_DIRECTORY = "last directory viewed"
    CURRENT_TIME = "current date and time"
    FILE_DIALOG = "file dialog"
    NEW_TASK = "new task"


class TerminalDefault(BaseModel):
    """The default fields of a catalog terminal."""

    default: ScalarValue = None
    default_behavior: DefaultBehavior | None = None
    # A default NI states for when another input of the node is not wired (Set
    # File Position: ``from`` is 2 when ``offset`` is unwired, else ``default``).
    # Keyed by the other input's catalog index; the lowest unwired index decides.
    default_when_unwired: dict[int, ScalarValue] = {}
    # What an unwired copy of this terminal holds when the node was resized to its
    # array types and the terminal repeats: in a later set of terminals, or as a
    # later dimension of its set (see ``graph/terminal_layout.py``).
    later_set_default_behavior: DefaultBehavior | None = None
    later_dimension_default_behavior: DefaultBehavior | None = None


def default_for_wiring(entry: TerminalDefault, wired: set[int]) -> ScalarValue:
    """The value an unwired ``entry`` holds when the inputs at the indices in
    ``wired`` are wired: the ``default_when_unwired`` of the lowest-indexed input
    it names that is not wired, else ``default``."""
    for index in sorted(entry.default_when_unwired):
        if index not in wired:
            return entry.default_when_unwired[index]
    return entry.default
