"""Node type subclasses and parsing handlers.

Each LabVIEW node type (prim, iUse, cpdArith, aBuild, etc.) has:
1. A Node subclass with type-specific fields
2. A handler that knows how to parse its XML
3. Registration in NODE_HANDLERS for factory lookup
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .models import Node
from .nodes.base import extract_label, extract_terminal_types

# =============================================================================
# Node Subclasses
# =============================================================================


@dataclass
class PrimitiveNode(Node):
    """A LabVIEW primitive node (class="prim")."""

    prim_index: int | None = None
    prim_res_id: int | None = None


@dataclass
class SubVINode(Node):
    """A SubVI call node (class="iUse" or "polyIUse")."""

    vi_path: str | None = None


@dataclass
class CpdArithNode(Node):
    """Compound Arithmetic node (class="cpdArith").

    Combines multiple inputs with a single operation (OR, AND, ADD, etc.).
    """

    operation: str = "or"  # "or", "and", "add"


@dataclass
class ArrayBuildNode(Node):
    """Build Array node (class="aBuild").

    Collects multiple inputs into an array.
    """

    pass  # No extra fields yet


@dataclass
class LoopNode(Node):
    """Loop structure node (class="whileLoop" or "forLoop")."""

    loop_type: str = ""  # "whileLoop" or "forLoop"


@dataclass
class SelectNode(Node):
    """Select node (class="select")."""

    pass


# =============================================================================
# Node Type Handlers
# =============================================================================


class NodeTypeHandler(ABC):
    """Base class for node type handlers.

    Each handler knows:
    - What XML class it handles
    - What display name to use
    - How to parse its specific attributes
    """

    xml_class: str  # e.g., "cpdArith", "prim", "aBuild"
    display_name: str  # e.g., "Compound Arithmetic"

    @abstractmethod
    def parse(self, elem: ET.Element) -> Node:
        """Parse XML element into typed Node."""
        pass

    def _extract_common(self, elem: ET.Element) -> dict:
        """Extract common fields from XML element."""
        name = extract_label(elem)
        input_types, output_types = extract_terminal_types(elem)
        return {
            "uid": elem.get("uid"),
            "node_type": self.xml_class,
            "name": name or self.display_name,
            "input_types": input_types,
            "output_types": output_types,
        }


class PrimitiveHandler(NodeTypeHandler):
    """Handler for primitive nodes (class="prim")."""

    xml_class = "prim"
    display_name = "Primitive"

    def parse(self, elem: ET.Element) -> PrimitiveNode:
        common = self._extract_common(elem)

        prim_idx_elem = elem.find("primIndex")
        prim_res_elem = elem.find("primResID")

        return PrimitiveNode(
            **common,
            prim_index=int(prim_idx_elem.text) if prim_idx_elem is not None else None,
            prim_res_id=int(prim_res_elem.text) if prim_res_elem is not None else None,
        )


class SubVIHandler(NodeTypeHandler):
    """Handler for SubVI nodes (class="iUse")."""

    xml_class = "iUse"
    display_name = "SubVI"

    def parse(self, elem: ET.Element) -> SubVINode:
        common = self._extract_common(elem)
        return SubVINode(**common)


class PolySubVIHandler(NodeTypeHandler):
    """Handler for polymorphic SubVI nodes (class="polyIUse")."""

    xml_class = "polyIUse"
    display_name = "Polymorphic SubVI"

    def parse(self, elem: ET.Element) -> SubVINode:
        common = self._extract_common(elem)
        return SubVINode(**common)


class DynamicDispatchHandler(NodeTypeHandler):
    """Handler for dynamic dispatch VI nodes (class="dynIUse").

    Dynamic dispatch VIs are class methods that use runtime dispatch
    based on the class of the input object. In Python, this is just
    regular method calls - Python's MRO handles dispatch automatically.
    """

    xml_class = "dynIUse"
    display_name = "Dynamic Dispatch VI"

    def parse(self, elem: ET.Element) -> SubVINode:
        common = self._extract_common(elem)
        return SubVINode(**common)


class CpdArithHandler(NodeTypeHandler):
    """Handler for Compound Arithmetic nodes (class="cpdArith")."""

    xml_class = "cpdArith"
    display_name = "Compound Arithmetic"

    # dcoFiller value -> operation name
    OPERATIONS = {
        1: "or",
        2: "and",
        256: "add",
    }

    def parse(self, elem: ET.Element) -> CpdArithNode:
        common = self._extract_common(elem)
        operation = self._extract_operation(elem)

        return CpdArithNode(
            **common,
            operation=operation,
        )

    def _extract_operation(self, elem: ET.Element) -> str:
        """Extract operation from dcoFiller in first terminal's DCO."""
        term_list = elem.find("termList")
        if term_list is not None:
            first_term = term_list.find("SL__arrayElement")
            if first_term is not None:
                dco = first_term.find("dco")
                if dco is not None:
                    filler = dco.findtext("dcoFiller")
                    if filler:
                        return self.OPERATIONS.get(int(filler), "or")
        return "or"


class ArrayBuildHandler(NodeTypeHandler):
    """Handler for Build Array nodes (class="aBuild")."""

    xml_class = "aBuild"
    display_name = "Build Array"

    def parse(self, elem: ET.Element) -> ArrayBuildNode:
        common = self._extract_common(elem)
        return ArrayBuildNode(**common)


class WhileLoopHandler(NodeTypeHandler):
    """Handler for While Loop nodes (class="whileLoop")."""

    xml_class = "whileLoop"
    display_name = "While Loop"

    def parse(self, elem: ET.Element) -> LoopNode:
        # Don't use extract_label for loops - it would find labels from inner nodes
        input_types, output_types = extract_terminal_types(elem)
        return LoopNode(
            uid=elem.get("uid"),
            node_type=self.xml_class,
            name=self.display_name,  # Always use "While Loop"
            input_types=input_types,
            output_types=output_types,
            loop_type="whileLoop",
        )


class ForLoopHandler(NodeTypeHandler):
    """Handler for For Loop nodes (class="forLoop")."""

    xml_class = "forLoop"
    display_name = "For Loop"

    def parse(self, elem: ET.Element) -> LoopNode:
        # Don't use extract_label for loops - it would find labels from inner nodes
        input_types, output_types = extract_terminal_types(elem)
        return LoopNode(
            uid=elem.get("uid"),
            node_type=self.xml_class,
            name=self.display_name,  # Always use "For Loop"
            input_types=input_types,
            output_types=output_types,
            loop_type="forLoop",
        )


class SelectHandler(NodeTypeHandler):
    """Handler for Select nodes (class="select")."""

    xml_class = "select"
    display_name = "Select"

    def parse(self, elem: ET.Element) -> SelectNode:
        common = self._extract_common(elem)
        return SelectNode(**common)


class GenericHandler(NodeTypeHandler):
    """Fallback handler for unknown node types."""

    def __init__(self, xml_class: str, display_name: str | None = None):
        self.xml_class = xml_class
        self.display_name = display_name or xml_class

    def parse(self, elem: ET.Element) -> Node:
        common = self._extract_common(elem)
        return Node(**common)


# =============================================================================
# Registry and Factory
# =============================================================================

# All known handlers
_HANDLERS: list[NodeTypeHandler] = [
    PrimitiveHandler(),
    SubVIHandler(),
    PolySubVIHandler(),
    DynamicDispatchHandler(),
    CpdArithHandler(),
    ArrayBuildHandler(),
    WhileLoopHandler(),
    ForLoopHandler(),
    SelectHandler(),
]

# Build registry from handlers
NODE_HANDLERS: dict[str, NodeTypeHandler] = {h.xml_class: h for h in _HANDLERS}


def parse_node(elem: ET.Element) -> Node:
    """Factory function - parse XML element into appropriate Node subclass.

    Args:
        elem: XML element with class attribute

    Returns:
        Appropriate Node subclass instance
    """
    xml_class = elem.get("class")
    handler = NODE_HANDLERS.get(xml_class)

    if handler:
        return handler.parse(elem)

    # Fallback for unknown types
    return GenericHandler(xml_class).parse(elem)


def get_display_name(node_type: str) -> str:
    """Get display name for a node type.

    Args:
        node_type: The XML class name (e.g., "cpdArith")

    Returns:
        Human-readable display name (e.g., "Compound Arithmetic")
    """
    handler = NODE_HANDLERS.get(node_type)
    return handler.display_name if handler else node_type
