"""What a function's input terminal holds when nothing is wired to it.

Both the primitive and the VI catalogs record it the same way, on the terminal:
``default`` is the LabVIEW value NI documents (the value a decoded constant of
that type has: a number, a Boolean, a string, or a path text), and
``default_behavior`` is a documented behaviour that is no value. A terminal with
neither has no documented default.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from .models import ScalarValue


class DefaultBehavior(str, Enum):
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


class TerminalDefault(BaseModel):
    """The default fields of a catalog terminal."""

    default: ScalarValue = None
    default_behavior: DefaultBehavior | None = None
    # What an unwired copy of this terminal holds when the node was resized to its
    # array types and the terminal repeats: in a later set of terminals, or as a
    # later dimension of its set (see ``graph/terminal_layout.py``).
    later_set_default_behavior: DefaultBehavior | None = None
    later_dimension_default_behavior: DefaultBehavior | None = None
