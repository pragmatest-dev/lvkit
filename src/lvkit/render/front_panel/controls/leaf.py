"""The value glyph for a SCALAR front-panel control type."""

from __future__ import annotations

from ....models import LVType
from ...glyph import ConstantGlyph, Glyph, PathGlyph, RefnumGlyph, TypeTerminalGlyph
from ...style import Theme, type_repr, wire_style
from .boolean import BooleanControlGlyph
from .enum_control import EnumControlGlyph
from .number_format import format_number
from .numeric import NumericControlGlyph
from .unknown import UnknownControlGlyph


def _refnum_glyph(lv_type: LVType | None, theme: Theme) -> RefnumGlyph:
    """The SAME ``RefnumGlyph`` a block-diagram refnum terminal draws --
    COMPACT only (a front-panel control's heap carries no per-field
    payload-EXPANSION geometry; that's a cluster-FIELD-only concept,
    ``ClusterFieldGeom.refnum_expanded``). ``lv_type`` is None for an
    unresolved control (an old VI with no VCTP) -- still draws the frame +
    generic kind symbol, since ``control_type == "stdRefNum"`` alone already
    identifies the SHAPE without needing the type to resolve."""
    terminal = None
    if lv_type is not None and lv_type.element_type is not None:
        payload = lv_type.element_type
        terminal = TypeTerminalGlyph(type_repr(payload), wire_style(payload).color)
    kind = lv_type.ref_type if lv_type is not None else None
    return RefnumGlyph(kind, theme.struct_border, terminal=terminal)


def leaf_glyph(
    control_type: str,
    default_value: str | None,
    enum_values: list[str],
    theme: Theme,
    show_spinner: bool = True,
    number_format: str | None = None,
    lv_type: LVType | None = None,
) -> Glyph:
    """Front-panel value cells use a neutral ``struct_border`` outline (a real
    LabVIEW control's border is never type-colored -- wire colors are a
    block-diagram-only convention), so ``ConstantGlyph``/``PathGlyph`` are
    reused for their wrapping/folder-icon LOGIC with that neutral color passed
    in, not their diagram color choice. ``show_spinner`` is False only for an
    array's own disabled default-element row (see
    ``NumericControlGlyph.show_spinner``). ``lv_type`` is this control's own
    resolved type (``ParsedFPControl.lv_type``/``element_lv_type``) -- used
    only by ``stdRefNum`` today, for its real kind and registered payload."""
    if control_type == "stdRefNum":
        return _refnum_glyph(lv_type, theme)
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
