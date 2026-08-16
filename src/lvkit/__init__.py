"""lvkit - Convert LabVIEW VIs to Python code."""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.5.8"

# The public API is exposed LAZILY (PEP 562 module ``__getattr__``): the graph /
# parser / structure stacks (networkx, pydantic, pylabview, PIL — ~230 ms) load
# only when one of these names is actually accessed, NOT on a bare
# ``import lvkit``. This is what lets the CLI's render/diff cache-HIT path (and
# any caller that only needs a leaf module) start without the heavy machinery.
if TYPE_CHECKING:  # eager only for type-checkers / IDE completion
    from .graph.models import Constant as GraphConstant
    from .graph.models import Wire as GraphWire
    from .models import Operation, Terminal, Tunnel
    from .parser import (
        ParsedBlockDiagram,
        ParsedConnectorPane,
        ParsedConnectorPaneSlot,
        ParsedConstant,
        ParsedFPControl,
        ParsedFrontPanel,
        ParsedNode,
        ParsedVI,
        ParsedVIMetadata,
        ParsedWire,
        parse_vi,
    )
    from .structure import (
        LVClass,
        LVLibrary,
        LVMethod,
        discover_project_structure,
        generate_python_structure_plan,
        parse_lvclass,
        parse_lvlib,
    )

# Public name -> (submodule, attribute) for the lazy loader.
_LAZY: dict[str, tuple[str, str]] = {
    "GraphConstant": (".graph.models", "Constant"),
    "GraphWire": (".graph.models", "Wire"),
    "Operation": (".models", "Operation"),
    "Terminal": (".models", "Terminal"),
    "Tunnel": (".models", "Tunnel"),
    "parse_vi": (".parser", "parse_vi"),
    "ParsedVI": (".parser", "ParsedVI"),
    "ParsedVIMetadata": (".parser", "ParsedVIMetadata"),
    "ParsedBlockDiagram": (".parser", "ParsedBlockDiagram"),
    "ParsedConnectorPane": (".parser", "ParsedConnectorPane"),
    "ParsedConnectorPaneSlot": (".parser", "ParsedConnectorPaneSlot"),
    "ParsedConstant": (".parser", "ParsedConstant"),
    "ParsedFPControl": (".parser", "ParsedFPControl"),
    "ParsedFrontPanel": (".parser", "ParsedFrontPanel"),
    "ParsedNode": (".parser", "ParsedNode"),
    "ParsedWire": (".parser", "ParsedWire"),
    "LVClass": (".structure", "LVClass"),
    "LVLibrary": (".structure", "LVLibrary"),
    "LVMethod": (".structure", "LVMethod"),
    "discover_project_structure": (".structure", "discover_project_structure"),
    "generate_python_structure_plan": (".structure", "generate_python_structure_plan"),
    "parse_lvclass": (".structure", "parse_lvclass"),
    "parse_lvlib": (".structure", "parse_lvlib"),
}

__all__ = [
    "parse_vi",
    "ParsedVI",
    "ParsedVIMetadata",
    "ParsedBlockDiagram",
    "ParsedConnectorPane",
    "ParsedConnectorPaneSlot",
    "ParsedNode",
    "ParsedWire",
    "ParsedConstant",
    "parse_lvclass",
    "parse_lvlib",
    "discover_project_structure",
    "generate_python_structure_plan",
    "LVClass",
    "LVLibrary",
    "LVMethod",
    "ParsedFrontPanel",
    "ParsedFPControl",
    "Terminal",
    "Operation",
    "Tunnel",
    "GraphConstant",
    "GraphWire",
]


def __getattr__(name: str) -> object:
    try:
        module, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    value = getattr(importlib.import_module(module, __name__), attr)
    globals()[name] = value  # cache: PEP 562 skips __getattr__ once set
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
