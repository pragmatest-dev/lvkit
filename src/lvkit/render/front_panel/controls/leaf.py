"""The value glyph for a SCALAR front-panel control type."""

from __future__ import annotations

from ....models import LVType
from ....parser.utils import heap_color
from ...glyph import (
    ConstantGlyph,
    Glyph,
    PathGlyph,
    RefnumGlyph,
    TypeTerminalGlyph,
    VariantGlyph,
)
from ...style import Theme, type_repr, wire_style
from .boolean import BooleanControlGlyph
from .color import ColorControlGlyph
from .enum_control import EnumControlGlyph
from .number_format import format_number
from .numeric import NumericControlGlyph
from .picture import PictureControlGlyph
from .unknown import UnknownControlGlyph


def _color_from_default(default_value: str | None) -> str | None:
    """A Color Box's saved ``NumUInt32`` value (a decimal string, from the
    normal numeric decode -- ``stdColorNum`` needs no type-specific parsing,
    with or without a resolvable VCTP) as ``#RRGGBB``, via the SAME
    ``00RRGGBB`` byte packing ``parser.utils.heap_color`` already reads.
    ``None`` when the control has no saved default, or the value doesn't fit
    that packing (a value above 0xFFFFFF)."""
    if default_value is None:
        return None
    try:
        value = int(default_value)
    except ValueError:
        return None
    # The lower bound is load-bearing, not symmetry: the unresolved-VCTP
    # fallback decode (_decode_numeric_default) reads this SIGNED, so a value
    # whose top byte has the high bit set would come back negative -- this
    # correctly rejects it as "not a color" rather than mis-packing it.
    if not 0 <= value <= 0xFFFFFF:
        return None
    return heap_color(f"{value:08X}")


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
    if control_type == "stdTag":
        # LabVIEW's I/O-name type (a DAQmx physical channel/task name, a VISA
        # resource name, ...) -- real corpus heap: a selLabel part + dropdown-
        # arrow decorations, the SAME value-cell-with-chevron shape a ring/enum
        # draws, not a bare reference icon (RefnumGlyph's own contract is that
        # a refnum's held data is NEVER shown, but a Tag's saved channel name
        # IS the point of the control). Reused for that SHAPE, not because a
        # Tag is an enum: there is no fixed item list, so it always falls back
        # to its own literal text. Most real corpus "stdTag" heap text is
        # actually an inert partID=8017 decoration nested inside a stdRefNum's
        # OWN parts, unrelated to this control_type -- never reaches here.
        return EnumControlGlyph(default_value, ())
    if control_type == "stdPict":
        return PictureControlGlyph(theme.struct_border)
    if control_type == "stdLvVariant":
        # Opaque, type-erased data -- there IS no visible internal structure
        # to a Variant (verified against the real corpus: the heap's own
        # inner part draws a built-in watermark icon we cannot reproduce, per
        # the clean-room rule, so the SAME solid box a block-diagram Variant
        # draws is the honest front-panel form too), neutral-bordered like
        # every other front-panel value cell rather than the wire-purple a
        # diagram uses.
        return VariantGlyph(fill_attr="fp_value_fill", stroke_attr="struct_border")
    if control_type == "stdColorNum":
        return ColorControlGlyph(
            color=_color_from_default(default_value), border_color=theme.struct_border
        )
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
    if control_type in ("stdEnum", "stdRing", "stdComboBox"):
        # A Combo Box draws the SAME value-cell-with-chevron shape (real
        # corpus heap: a selLabel part + dropdown-arrow decorations) and
        # shares the ring/enum multiLabel item-list convention when a
        # developer set one; unlike a ring it can also hold free-typed text
        # with no fixed list at all, which EnumControlGlyph already falls
        # back to showing literally when enum_values is empty. Known,
        # accepted gap: a combo box that has BOTH a fixed list AND a
        # non-indexable saved value (a user typed custom text into one that
        # also has a list -- normal LabVIEW usage, unlike a ring) would show
        # enum_values[0] instead of that text, since EnumControlGlyph's
        # index lookup can't distinguish the two cases. No real corpus
        # instance exercises this (every one found has an empty list).
        return EnumControlGlyph(
            default_value=default_value, enum_values=tuple(enum_values)
        )
    return UnknownControlGlyph(control_type=control_type)
