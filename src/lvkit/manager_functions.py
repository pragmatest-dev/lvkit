"""LabVIEW's own library functions (the ``LabVIEW`` library a Call Library
Function Node names), and the Python that stands in for each.

Loaded from ``data/labview_manager.json``; see its ``_comment`` for the call
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
class ManagerFunction:
    """One LabVIEW function: the Python callable that stands in for it, the
    import that provides it, and NI's parameter names."""

    name: str
    python: str
    import_statement: str
    parameters: tuple[str, ...]
    doc_url: str


@functools.cache
def manager_functions() -> dict[str, ManagerFunction]:
    """Every LabVIEW function with a Python equivalent, by name."""
    with open(data_dir() / "labview_manager.json", encoding="utf-8") as handle:
        entries = json.load(handle)["functions"]
    return {
        name: ManagerFunction(
            name=name,
            python=entry["python"],
            import_statement=entry["import"],
            parameters=tuple(entry["parameters"]),
            doc_url=entry["doc_url"],
        )
        for name, entry in entries.items()
    }
