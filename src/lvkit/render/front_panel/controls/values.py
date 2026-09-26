"""Give a control its own saved value: the same control shape, a different
decoded default -- how one array row (or one cluster field of a row) is drawn
with its real value rather than the type default."""

from __future__ import annotations

from dataclasses import replace

from ....parser.models import ParsedFPControl


def with_value(ctrl: ParsedFPControl, value: object) -> ParsedFPControl:
    """``ctrl`` carrying ``value`` (``_decode_element``'s structured value for
    it): a display string for a scalar/enum, a dict by field name for a
    cluster (recursively), a list for an array."""
    if isinstance(value, str):
        return replace(ctrl, default_value=value)
    if isinstance(value, dict) and ctrl.children:
        return replace(
            ctrl,
            children=[
                with_value(child, value[child.name]) if child.name in value else child
                for child in ctrl.children
            ],
        )
    if isinstance(value, list):
        return replace(ctrl, element_values=list(value))
    return ctrl
