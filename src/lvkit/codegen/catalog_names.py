"""The Python name of each terminal of a primitive's catalog entry.

A catalog terminal's ``name`` is NI's label. Its Python name is the terminal's
``python_name`` when the entry records one, else the label as
``catalog_python_label`` gives it, made a Python name by ``to_var_name``. The
names of one direction of one node are pairwise distinct. Templates, the keys of
a dict ``python_code`` and generated variables use only these names; a label
appears only in messages.
"""

from __future__ import annotations

from collections.abc import Sequence

from lvkit.models import TerminalDirection
from lvkit.primitive_resolver.models import PrimitiveTerminal

from .ast_utils import to_var_name, unique_names
from .label_names import catalog_python_label

CatalogNames = dict[tuple[TerminalDirection, int], str]


def catalog_python_names(terminals: Sequence[PrimitiveTerminal]) -> CatalogNames:
    """The Python name of each named terminal, keyed by ``(direction, index)``."""
    names: CatalogNames = {}
    for direction in TerminalDirection:
        ordered = sorted(
            (t for t in terminals if t.direction == direction and t.name),
            key=lambda t: t.index,
        )
        derived = unique_names(
            [
                t.python_name or to_var_name(catalog_python_label(t) or "")
                for t in ordered
            ]
        )
        for terminal, name in zip(ordered, derived, strict=True):
            names[(direction, terminal.index)] = name
    return names
