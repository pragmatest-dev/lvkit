"""LabVIEW's own library functions (the ``LabVIEW`` library a Call Library
Function Node names), and the Python each is generated as.

Loaded from ``data/labview_manager.json``; see its ``_comment`` for the template
contract.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass

from ._data import data_dir

# The library name a Call Library Function Node uses for LabVIEW's own functions.
LABVIEW_LIBRARY = "LabVIEW"


@dataclass(frozen=True)
class ManagerDeclaration:
    """A module-level definition a function's statements use: the ``name`` it is
    defined under and its source lines (``{name}`` stands for the name)."""

    key: str
    name: str
    source: tuple[str, ...]


@dataclass(frozen=True)
class ManagerFunction:
    """One LabVIEW function: NI's parameter names, the Python that replaces the
    call, and the imports and declarations it needs."""

    name: str
    parameters: tuple[str, ...]
    imports: tuple[str, ...]
    declarations: tuple[ManagerDeclaration, ...]
    statements: tuple[str, ...]
    doc_url: str


@functools.cache
def manager_functions() -> dict[str, ManagerFunction]:
    """Every LabVIEW function with a Python equivalent, by name."""
    with open(data_dir() / "labview_manager.json", encoding="utf-8") as handle:
        data = json.load(handle)
    declarations = {
        key: ManagerDeclaration(key, entry["name"], tuple(entry["source"]))
        for key, entry in data["declarations"].items()
    }
    return {
        name: ManagerFunction(
            name=name,
            parameters=tuple(entry["parameters"]),
            imports=tuple(entry["imports"]),
            declarations=tuple(declarations[key] for key in entry["declarations"]),
            statements=tuple(entry["statements"]),
            doc_url=entry["doc_url"],
        )
        for name, entry in data["functions"].items()
    }
