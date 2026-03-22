"""Node type subclasses and parsing handlers.

Each LabVIEW node type (prim, iUse, cpdArith, aBuild, etc.) has:
1. A Node subclass with type-specific fields
2. A handler that knows how to parse its XML
3. Registration in NODE_HANDLERS for factory lookup
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .utils import clean_labview_string

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
    poly_variant_name: str | None = None  # Resolved variant for polyIUse


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

    def _extract_common(self, elem: ET.Element) -> dict[str, Any]:
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

        prim_index = None
        if prim_idx_elem is not None and prim_idx_elem.text:
            prim_index = int(prim_idx_elem.text)
        prim_res_id = None
        if prim_res_elem is not None and prim_res_elem.text:
            prim_res_id = int(prim_res_elem.text)

        return PrimitiveNode(
            **common,
            prim_index=prim_index,
            prim_res_id=prim_res_id,
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
        variant_name = self._extract_poly_variant(elem)
        return SubVINode(**common, poly_variant_name=variant_name)

    def _extract_poly_variant(self, elem: ET.Element) -> str | None:
        """Extract the selected polymorphic variant name.

        The preferredInstIndex is an edit-time selection stored in the VI.
        The variant list is in the polySelector's buf field.
        Index offset: first 2 entries are "Automatic" and "-" separator.
        """
        import re

        idx_elem = elem.find("preferredInstIndex")
        if idx_elem is None or not idx_elem.text:
            return None
        try:
            inst_index = int(idx_elem.text.strip(), 16)
        except ValueError:
            return None

        # Find polySelector's buf with variant names
        for selector in elem.iter():
            if selector.get("class") == "polySelector":
                for child in selector.iter():
                    if child.tag == "buf" and child.text:
                        items = re.findall(r'"([^"]+)"', child.text)
                        # Offset by 2: skip "Automatic" and "-"
                        actual_index = inst_index + 2
                        if 0 <= actual_index < len(items):
                            return items[actual_index]
        return None


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
            uid=elem.get("uid", ""),
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
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
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


@dataclass
class PropertyNode(Node):
    """A property node (class="propNode")."""

    object_name: str = ""
    object_method_id: str = ""
    properties: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InvokeNode(Node):
    """An invoke node (class="invokeNode")."""

    object_name: str = ""
    object_method_id: str = ""
    method_name: str = ""
    method_code: int = 0


class PropertyNodeHandler(NodeTypeHandler):
    """Handler for Property Node (class="propNode")."""

    xml_class = "propNode"
    display_name = "Property Node"

    def parse(self, elem: ET.Element) -> PropertyNode:
        common = self._extract_common(elem)
        object_name = clean_labview_string(elem.findtext("nodeName"))
        omid = elem.findtext("oMId") or ""

        properties: list[dict[str, Any]] = []
        for prop_info in elem.iter("propItemInfo"):
            name = clean_labview_string(prop_info.findtext("PropItemName"))
            code_text = prop_info.findtext("PropItemCode") or "0"
            try:
                code = int(code_text)
            except ValueError:
                code = 0
            properties.append({"name": name, "code": code})

        return PropertyNode(
            **common,
            object_name=object_name,
            object_method_id=omid,
            properties=properties,
        )


class InvokeNodeHandler(NodeTypeHandler):
    """Handler for Invoke Node (class="invokeNode")."""

    xml_class = "invokeNode"
    display_name = "Invoke Node"

    def parse(self, elem: ET.Element) -> InvokeNode:
        common = self._extract_common(elem)
        meth_code_text = elem.findtext("methCode") or "0"
        try:
            meth_code = int(meth_code_text)
        except ValueError:
            meth_code = 0
        return InvokeNode(
            **common,
            object_name=clean_labview_string(elem.findtext("nodeName")),
            object_method_id=elem.findtext("oMId") or "",
            method_name=clean_labview_string(elem.findtext("methName")),
            method_code=meth_code,
        )


class FlatSequenceHandler(NodeTypeHandler):
    """Handler for Flat Sequence structures (class="flatSequence")."""

    xml_class = "flatSequence"
    display_name = "Flat Sequence"

    def parse(self, elem: ET.Element) -> Node:
        input_types, output_types = extract_terminal_types(elem)
        return Node(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
            input_types=input_types,
            output_types=output_types,
        )


class StackedSequenceHandler(NodeTypeHandler):
    """Handler for Stacked Sequence structures (class="seq")."""

    xml_class = "seq"
    display_name = "Stacked Sequence"

    def parse(self, elem: ET.Element) -> Node:
        input_types, output_types = extract_terminal_types(elem)
        return Node(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
            input_types=input_types,
            output_types=output_types,
        )


class PrintfHandler(NodeTypeHandler):
    """Handler for Format String nodes (class="printf").

    LabVIEW's printf node takes a format string and arguments,
    producing a formatted string output. Treated as a primitive.
    """

    xml_class = "printf"
    display_name = "Format String"

    def parse(self, elem: ET.Element) -> PrimitiveNode:
        common = self._extract_common(elem)
        return PrimitiveNode(**common)


class NMuxHandler(NodeTypeHandler):
    """Handler for Node Multiplexer (class="nMux").

    Selects between inputs based on a selector value.
    Like a ternary operator or indexed selection.
    """

    xml_class = "nMux"
    display_name = "Node Multiplexer"

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

# Built-in array/string operations with specialized XML classes.
# These are block diagram primitives but use different XML class names
# than "prim" because they have expandable/polymorphic terminals.
# Parsed identically to PrimitiveHandler (they ARE primitives).
class _BuiltinPrimitiveHandler(NodeTypeHandler):
    """Handler for built-in primitives with non-standard XML classes.

    These are block diagram primitives that LabVIEW stores with their own
    XML class (aDelete, aIndx, etc.) instead of "prim". They don't have
    primResID in the XML, so we assign it here based on the known mapping.
    """

    def __init__(self, xml_class: str, display_name: str, prim_res_id: int):
        self.xml_class = xml_class
        self.display_name = display_name
        self._prim_res_id = prim_res_id

    def parse(self, elem: ET.Element) -> PrimitiveNode:
        common = self._extract_common(elem)
        return PrimitiveNode(
            **common,
            prim_index=None,
            prim_res_id=self._prim_res_id,
        )


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
    PropertyNodeHandler(),
    InvokeNodeHandler(),
    FlatSequenceHandler(),
    StackedSequenceHandler(),
    PrintfHandler(),
    NMuxHandler(),
    # Built-in primitives with specialized XML classes
    _BuiltinPrimitiveHandler("aDelete", "Delete From Array", 1901),
    _BuiltinPrimitiveHandler("aIndx", "Index Array", 1809),
    _BuiltinPrimitiveHandler("concat", "Concatenate Strings", 1051),
    _BuiltinPrimitiveHandler("subset", "Array Subset", 1516),
    _BuiltinPrimitiveHandler("mergeErrors", "Merge Errors", 2401),
    _BuiltinPrimitiveHandler("oHExt", "Obtain/Release Semaphore", 8069),
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
    xml_class = elem.get("class", "")
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
