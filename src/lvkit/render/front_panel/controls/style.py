"""Which look a front-panel control is drawn in, decided from the picture ids of
its own parts (``ParsedFPPart.image_ids``).

The look is not one field: it is carried by the ids of the control's frame and
field parts. A negative id is a built-in shape. The tables below are the ids
measured on the corpus (``docs/_internal/design/labview-binary-format.md``
"Front panel structure"): numeric frames (part 9)
``-412`` 27,041 controls, ``-301`` 309, ``-766`` 106; string field (part 11)
``-302`` 724 and string frames ``-412`` 9,742. An id that is not in a table is
``OTHER``: not measured, so it keeps the generic drawing.
"""

from __future__ import annotations

from enum import Enum

from ....parser.models import ParsedFPControl, ParsedFPPart
from .base import FRAME_PART_ID, find_part

STRING_FIELD_PART_ID = 11
BOOLEAN_BODY_PART_ID = 21
DROPDOWN_PART_ID = 28


class ControlStyle(Enum):
    MODERN = "modern"
    SYSTEM = "system"
    SILVER = "silver"
    OTHER = "other"


class BooleanKind(Enum):
    SLIDE_SWITCH = "slide switch"
    ROUND_LED = "round LED"
    MODERN_BUTTON = "modern button"
    SYSTEM_BUTTON = "system button"
    CHECK_BOX = "check box"
    RADIO = "radio button"
    SQUARE_LED = "square LED"
    OTHER = "other"


# The look of a frame (part 9) by its picture id.
_FRAME_STYLE = {
    -412: ControlStyle.MODERN,
    -301: ControlStyle.SYSTEM,
    -302: ControlStyle.SYSTEM,
    -766: ControlStyle.SILVER,
}

# The look of a string's field (part 11) by its picture id; the frame decides
# every other string.
_STRING_FIELD_STYLE = {-302: ControlStyle.SYSTEM}

# A Boolean's kind by the picture ids of its body (part 21): slide switch 2,958
# controls, round LED 1,199, modern button 203, square LED 41, system button
# 203, check box 520, radio button 426. The radio button ids ``(-292, -293)``
# are the body ids of 304 Booleans that sit inside a ``radioClust`` (a Radio
# Buttons control; ``.tmp/radio_ids.py`` over 3,000 panels). The same ids
# appear on 124 Booleans outside one. A 15 by 15 body with ids
# ``(-270, -271)`` and no label part is on 740 Booleans; the corpus shows no
# screenshot or part layout that names its kind, so it stays ``OTHER``.
_BOOLEAN_KIND: dict[int, BooleanKind] = {
    -407: BooleanKind.SLIDE_SWITCH,
    -404: BooleanKind.ROUND_LED,
    -535: BooleanKind.MODERN_BUTTON,
    -536: BooleanKind.MODERN_BUTTON,
    **dict.fromkeys((-272, -273, -274, -275, -684), BooleanKind.SYSTEM_BUTTON),
    **dict.fromkeys((-296, -297, -298, -299, -685, -686), BooleanKind.CHECK_BOX),
    **dict.fromkeys((-292, -293, -294, -295, -687, -688), BooleanKind.RADIO),
    -441: BooleanKind.SQUARE_LED,
}


def _first_style(
    part: ParsedFPPart | None, table: dict[int, ControlStyle]
) -> ControlStyle | None:
    if part is None:
        return None
    return next((table[i] for i in part.image_ids if i in table), None)


def numeric_style(ctrl: ParsedFPControl) -> ControlStyle:
    """A numeric, ring or enum's look, from its frame."""
    style = _first_style(find_part(ctrl, FRAME_PART_ID), _FRAME_STYLE)
    return style or ControlStyle.OTHER


def string_style(ctrl: ParsedFPControl) -> ControlStyle:
    """A string or path's look: its field's id when the field has one in the
    table (System), else its frame's."""
    style = _first_style(
        find_part(ctrl, STRING_FIELD_PART_ID), _STRING_FIELD_STYLE
    ) or _first_style(find_part(ctrl, FRAME_PART_ID), _FRAME_STYLE)
    return style or ControlStyle.OTHER


# A ring or enum is a drop-down (a cell with a chevron) when its part 28 has
# one of these ids (1,966 + 197 controls); one with spinner arrows instead is
# the look the numeric styles draw.
_DROPDOWN_IDS = frozenset({-466, -277})


def has_dropdown(ctrl: ParsedFPControl) -> bool:
    part = find_part(ctrl, DROPDOWN_PART_ID)
    return part is not None and any(i in _DROPDOWN_IDS for i in part.image_ids)


# A listbox's look by its frame's picture id: the System listbox, 69 + 3 + 1
# controls.
_LISTBOX_STYLE = {-300: ControlStyle.SYSTEM, -786: ControlStyle.SYSTEM}


def listbox_style(ctrl: ParsedFPControl) -> ControlStyle:
    style = _first_style(find_part(ctrl, FRAME_PART_ID), _LISTBOX_STYLE)
    return style or ControlStyle.OTHER


def boolean_kind(ctrl: ParsedFPControl) -> BooleanKind:
    """A Boolean's kind, from the picture ids of its body part."""
    body = find_part(ctrl, BOOLEAN_BODY_PART_ID)
    if body is None:
        return BooleanKind.OTHER
    return next(
        (_BOOLEAN_KIND[i] for i in body.image_ids if i in _BOOLEAN_KIND),
        BooleanKind.OTHER,
    )
