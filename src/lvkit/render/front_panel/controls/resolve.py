"""Resolve a ``ParsedFPControl`` to the ``Glyph`` that draws its value.

An ordered resolver list: the first resolver that claims the control wins, and
the scalar leaf resolver is the fallback (an unknown type is a labeled box,
never silently dropped). Resolves recursively -- a cluster's fields and an
array's element are each resolved the same way, mirroring how
``render/nodes.py`` composes a block-diagram cluster/array constant from its
own field/element glyphs.
"""

from __future__ import annotations

from ....models import leading_element
from ....parser.models import ParsedFPControl, ParsedFPPart
from ...glyph import ClusterConstantGlyph, Glyph
from ...style import Theme
from ...text_style import TextStyle, part_style
from .array import array_control
from .base import CAPTION_PART_ID, LABEL_PART_ID, find_part, frame_colors
from .boolean import boolean_control
from .enum_control import selected_text
from .graph import GraphControlGlyph
from .label import LABEL_SIZE
from .leaf import leaf_glyph
from .listbox import listbox_control
from .numeric import numeric_control
from .string_control import string_control
from .style import ControlStyle, has_dropdown, numeric_style
from .tab import tab_control
from .timestamp import timestamp_control
from .values import with_value


def _field_label_styles(ctrl: ParsedFPControl) -> dict[str, TextStyle]:
    """Each field's name in the font of its label (or caption) text."""
    return {
        f.name: part_style(
            find_part(f, LABEL_PART_ID if f.label_visible else CAPTION_PART_ID)
        )
        for f in ctrl.children
    }


def _cluster_glyph(
    ctrl: ParsedFPControl, fields: tuple[tuple[str, Glyph], ...]
) -> ClusterConstantGlyph:
    """A cluster drawn in its own frame colors when the heap records them."""
    fill, outline = frame_colors(ctrl)
    return ClusterConstantGlyph(
        field_label_styles=_field_label_styles(ctrl),
        fields=fields,
        cluster_geom=ctrl.cluster_geom,
        fill_attr="fp_panel",
        fill_color=fill,
        border_color=outline,
        field_label_size=LABEL_SIZE,
    )


def _shown_fields(
    ctrl: ParsedFPControl, theme: Theme
) -> tuple[tuple[str, Glyph], ...]:
    """The glyphs of ``ctrl``'s fields the panel shows: a field the author hid
    is not drawn."""
    return tuple(
        (f.name, resolve_glyph(f, theme)) for f in ctrl.children if not f.hidden
    )


def _cluster(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "stdClust":
        return None
    fields = _shown_fields(ctrl, theme)
    return _cluster_glyph(ctrl, fields)


def _element(ctrl: ParsedFPControl, theme: Theme, value: object = None) -> Glyph | None:
    """The array's ELEMENT type's own glyph -- at ``value`` (that element's
    saved value) when given, else at the real representative-row value the
    heap carries (``ctrl.element_default_value`` / each ``ctrl.children``
    field's own ``default_value``), else the type's bare default. A cluster
    element is built from ``ctrl.children``/``cluster_geom``; a scalar element
    from the element ddo's own control-type part (``part_id=None``). ``None``
    when neither is available."""
    if ctrl.children:
        row = with_value(ctrl, value) if isinstance(value, dict) else ctrl
        return _cluster_glyph(ctrl, _shown_fields(row, theme))
    element_part: ParsedFPPart | None = next(
        (p for p in ctrl.parts if p.part_id is None), None
    )
    if element_part is None:
        return None
    # A scalar element goes straight to leaf_glyph, bypassing the _RESOLVERS
    # list above -- so an element control_type only _graph (or a future
    # resolver) handles, never leaf_glyph, falls back to UnknownControlGlyph
    # here even though a bare control of that type renders correctly. No
    # real corpus array-of-stdGraph exists to force this open yet (Phase D
    # corpus counts: stdGraph 9, all scalar controls).
    return leaf_glyph(
        element_part.part_class,
        value if isinstance(value, str) else ctrl.element_default_value,
        ctrl.enum_values,
        theme,
        show_spinner=False,
        number_format=ctrl.number_format,
        lv_type=ctrl.element_lv_type,
    )


def _array(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    """Saved elements draw as real (enabled) rows; every row past the array's
    end -- or every row of an empty array -- is the disabled default element."""
    if ctrl.control_type != "indArr":
        return None
    rows = tuple(
        glyph
        for value in ctrl.element_values
        if (glyph := _element(ctrl, theme, leading_element(value))) is not None
    )
    return array_control(ctrl, _element(ctrl, theme), rows)


def _numeric(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type not in ("stdNum", "stdNumeric"):
        return None
    return numeric_control(ctrl)


def _ring(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    """A Modern ring or enum with spinner arrows is drawn as the Modern numeric
    is (its selected item in the readout); any other is the generic ring."""
    if ctrl.control_type not in ("stdRing", "stdEnum"):
        return None
    if has_dropdown(ctrl) or numeric_style(ctrl) != ControlStyle.MODERN:
        return None
    text = selected_text(ctrl.default_value, tuple(ctrl.enum_values))
    glyph = numeric_control(ctrl, text)
    return glyph if glyph.readout_local is not None else None


def _string(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type not in ("stdString", "stdPath"):
        return None
    return string_control(ctrl)


def _listbox(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "listbox":
        return None
    return listbox_control(ctrl)


def _boolean(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "stdBool":
        return None
    return boolean_control(ctrl)


def _graph(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "stdGraph":
        return None
    return GraphControlGlyph.from_control(ctrl, theme.struct_border)


def _timestamp(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "absTime":
        return None
    return timestamp_control(ctrl)


def _tab(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "tabControl":
        return None
    return tab_control(ctrl)


_RESOLVERS = (
    _cluster,
    _array,
    _numeric,
    _ring,
    _string,
    _listbox,
    _boolean,
    _graph,
    _timestamp,
    _tab,
)


def resolve_glyph(ctrl: ParsedFPControl, theme: Theme) -> Glyph:
    for resolver in _RESOLVERS:
        glyph = resolver(ctrl, theme)
        if glyph is not None:
            return glyph
    return leaf_glyph(
        ctrl.control_type,
        ctrl.default_value,
        ctrl.enum_values,
        theme,
        number_format=ctrl.number_format,
        lv_type=ctrl.lv_type,
        slide_min=ctrl.slide_min,
        slide_max=ctrl.slide_max,
    )
