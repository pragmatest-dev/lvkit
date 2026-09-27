"""Resolve a ``ParsedFPControl`` to the ``Glyph`` that draws its value.

An ordered resolver list: the first resolver that claims the control wins, and
the scalar leaf resolver is the fallback (an unknown type is a labeled box,
never silently dropped). Resolves recursively -- a cluster's fields and an
array's element are each resolved the same way, mirroring how
``render/nodes.py`` composes a block-diagram cluster/array constant from its
own field/element glyphs.
"""

from __future__ import annotations

from ....parser.models import ParsedFPControl, ParsedFPPart
from ...glyph import ClusterConstantGlyph, Glyph
from ...style import Theme
from .array import array_control
from .base import frame_colors
from .boolean import boolean_control
from .label import LABEL_SIZE
from .leaf import leaf_glyph
from .numeric import numeric_control
from .values import with_value


def _cluster_glyph(
    ctrl: ParsedFPControl, fields: tuple[tuple[str, Glyph], ...]
) -> ClusterConstantGlyph:
    """A cluster drawn in its own frame colors when the heap records them."""
    fill, outline = frame_colors(ctrl)
    return ClusterConstantGlyph(
        fields=fields,
        cluster_geom=ctrl.cluster_geom,
        fill_attr="fp_panel",
        fill_color=fill,
        border_color=outline,
        field_label_size=LABEL_SIZE,
    )


def _cluster(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "stdClust":
        return None
    fields = tuple((f.name, resolve_glyph(f, theme)) for f in ctrl.children)
    return _cluster_glyph(ctrl, fields)


def _element(
    ctrl: ParsedFPControl, theme: Theme, value: object = None
) -> Glyph | None:
    """The array's ELEMENT type's own glyph -- at ``value`` (that element's
    saved value) when given, else at the real representative-row value the
    heap carries (``ctrl.element_default_value`` / each ``ctrl.children``
    field's own ``default_value``), else the type's bare default. A cluster
    element is built from ``ctrl.children``/``cluster_geom``; a scalar element
    from the element ddo's own control-type part (``part_id=None``). ``None``
    when neither is available."""
    if ctrl.children:
        row = with_value(ctrl, value) if isinstance(value, dict) else ctrl
        fields = tuple((f.name, resolve_glyph(f, theme)) for f in row.children)
        return _cluster_glyph(ctrl, fields)
    element_part: ParsedFPPart | None = next(
        (p for p in ctrl.parts if p.part_id is None), None
    )
    if element_part is None:
        return None
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
        if (glyph := _element(ctrl, theme, value)) is not None
    )
    return array_control(ctrl, _element(ctrl, theme), rows)


def _numeric(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type not in ("stdNum", "stdNumeric"):
        return None
    return numeric_control(ctrl)


def _boolean(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    if ctrl.control_type != "stdBool":
        return None
    return boolean_control(ctrl)


_RESOLVERS = (_cluster, _array, _numeric, _boolean)


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
