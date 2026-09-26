"""The value glyph for a SCALAR front-panel control type."""

from __future__ import annotations

from ...glyph import ConstantGlyph, Glyph, PathGlyph
from ...style import Theme
from .boolean import BooleanControlGlyph
from .enum_control import EnumControlGlyph
from .number_format import format_number
from .numeric import NumericControlGlyph
from .unknown import UnknownControlGlyph


def leaf_glyph(
    control_type: str,
    default_value: str | None,
    enum_values: list[str],
    theme: Theme,
    show_spinner: bool = True,
    number_format: str | None = None,
) -> Glyph:
    """Front-panel value cells use a neutral ``struct_border`` outline (a real
    LabVIEW control's border is never type-colored -- wire colors are a
    block-diagram-only convention), so ``ConstantGlyph``/``PathGlyph`` are
    reused for their wrapping/folder-icon LOGIC with that neutral color passed
    in, not their diagram color choice. ``show_spinner`` is False only for an
    array's own disabled default-element row (see
    ``NumericControlGlyph.show_spinner``)."""
    if control_type == "stdString":
        return ConstantGlyph(
            value=default_value or "", color=theme.struct_border,
            fill_attr="fp_value_fill", text_attr="fp_value_text", multiline=True,
        )
    if control_type == "stdPath":
        return PathGlyph(value=default_value or "", color=theme.struct_border)
    if control_type in ("stdNum", "stdNumeric"):
        return NumericControlGlyph(
            value=format_number(default_value or "0", number_format),
            show_spinner=show_spinner,
        )
    if control_type == "stdBool":
        return BooleanControlGlyph(on=default_value in ("True", "1"))
    if control_type in ("stdEnum", "stdRing"):
        return EnumControlGlyph(
            default_value=default_value, enum_values=tuple(enum_values)
        )
    return UnknownControlGlyph(control_type=control_type)
