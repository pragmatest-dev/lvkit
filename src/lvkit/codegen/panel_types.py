"""The types a generated ``Panel`` records for its controls and indicators.

A panel holds each control's value in the control's own type, while the Ctrl Val
methods and the Value property of a control reference carry a ``Variant``
(``variants_module``), which records its type. So the ``Panel`` is given:

- ``types``: the name of each control and indicator whose type has a
  type-descriptor code to its ``LVType``, the type of the ``Variant`` a read of it
  gives;
- ``variants``: the name of each control and indicator that is itself a Variant,
  which keeps the ``Variant`` it is given and gives it back.

A control whose type has no code (and is not a Variant) is in neither.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import NamedTuple

from lvkit.conversion_gap import ConversionGap
from lvkit.models import LVType

from .ast_utils import parse_expr
from .variant_values import is_variant_type
from .variants_module import lv_type_expression


class PanelTypes(NamedTuple):
    """The keywords of a ``Panel`` that record its controls' types and the names
    of ``variants`` they read."""

    keywords: list[tuple[str, ast.expr]]
    names: set[str]


def panel_type_keywords(entries: Sequence[tuple[str, LVType | None]]) -> PanelTypes:
    """The ``types=`` and ``variants=`` keywords of a ``Panel`` over ``entries``
    (the name of each control or indicator and its type); a keyword with no entry
    is left out."""
    types: dict[str, ast.expr] = {}
    variants: list[str] = []
    imported: set[str] = set()
    for name, lv_type in entries:
        if lv_type is None:
            continue
        if is_variant_type(lv_type):
            variants.append(name)
            continue
        try:
            expression = lv_type_expression(lv_type)
        except ConversionGap:
            continue
        types[name] = parse_expr(expression.text)
        imported.update(expression.names)
    keywords: list[tuple[str, ast.expr]] = []
    if types:
        keywords.append(
            (
                "types",
                ast.Dict(
                    keys=[ast.Constant(value=name) for name in types],
                    values=list(types.values()),
                ),
            )
        )
    if variants:
        keywords.append(
            (
                "variants",
                ast.List(
                    elts=[ast.Constant(value=name) for name in variants],
                    ctx=ast.Load(),
                ),
            )
        )
    return PanelTypes(keywords, imported)
