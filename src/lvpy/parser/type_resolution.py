"""Type resolution and typedef lookup functions.

Handles resolving TypeID references and looking up typedef definitions.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..graph_types import LVType
from .models import ParsedResolvedTypeDefValue, ParsedTypeDefRef
from .type_mapping import parse_type_map_rich


def parse_type_map(xml_path: Path | str) -> dict[int, str]:
    """Parse TypeID mappings from main XML comments.

    Looks for both basic and consolidated type mappings:
    - <!-- TypeID N: TypeName -->
    - <!-- Heap TypeID N = Consolidated TypeID M: TypeName -->

    Args:
        xml_path: Path to main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping TypeID -> type name (e.g., 37 -> "NumInt64")
    """
    rich_map = parse_type_map_rich(xml_path)
    # Return simple type names for backward compatibility
    return {k: v.underlying_type or v.kind for k, v in rich_map.items()}


def resolve_type(type_ref: str, type_map: dict[int, str]) -> str:
    """Resolve TypeID(N) reference to type name.

    Args:
        type_ref: String like "TypeID(37)"
        type_map: Mapping from TypeID -> type name

    Returns:
        Resolved type name or original string if not resolvable
    """
    match = re.match(r'TypeID\((\d+)\)', type_ref)
    if match:
        type_id = int(match.group(1))
        return type_map.get(type_id, type_ref)
    return type_ref


def resolve_type_rich(type_ref: str, type_map: dict[int, LVType]) -> LVType:
    """Resolve TypeID(N) reference to LVType.

    Args:
        type_ref: String like "TypeID(37)"
        type_map: Mapping from TypeID -> LVType

    Returns:
        LVType for the resolved type, or primitive LVType with original string
    """
    match = re.match(r'TypeID\((\d+)\)', type_ref)
    if match:
        type_id = int(match.group(1))
        return type_map.get(type_id, LVType(kind="primitive", underlying_type=type_ref))
    return LVType(kind="primitive", underlying_type=type_ref)


def parse_type_chain(xml_path: Path | str) -> dict:
    """Parse the complete type resolution chain from main XML.

    Builds mappings to resolve TypeID(N) to actual type info including:
    - Heap TypeID -> Consolidated TypeID
    - Consolidated TypeID -> FlatTypeID
    - FlatTypeID -> Type descriptor (name, underlying type, typedef name)

    Returns:
        Dict with 'heap_to_consolidated', 'consolidated_to_flat', 'flat_types'
    """
    heap_to_consolidated: dict[int, tuple[int, str]] = {}
    consolidated_to_flat: dict[int, int] = {}
    flat_types: dict[int, dict] = {}

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Parse Heap TypeID comments
    xml_text = Path(xml_path).read_text(encoding='utf-8', errors='replace')
    for match in re.finditer(
        r'Heap TypeID\s+(\d+)\s*=\s*Consolidated TypeID\s+(\d+):\s*(\w+)',
        xml_text
    ):
        heap_id = int(match.group(1))
        consolidated_id = int(match.group(2))
        type_name = match.group(3)
        heap_to_consolidated[heap_id] = (consolidated_id, type_name)

    # Parse VCTP section for Consolidated -> FlatTypeID mapping
    for type_desc in root.findall(".//VCTP//TopLevel/TypeDesc"):
        index = type_desc.get("Index")
        flat_id = type_desc.get("FlatTypeID")
        if index and flat_id:
            consolidated_to_flat[int(index)] = int(flat_id)

    # Parse FlatTypeID comments
    for match in re.finditer(r'FlatTypeID (\d+):\s*([^\n<]+)', xml_text):
        flat_id = int(match.group(1))
        description = match.group(2).strip()
        flat_types[flat_id] = {"description": description}

    # Find actual TypeDef definitions with labels
    for type_desc in root.findall(".//VCTP//TypeDesc[@Type='TypeDef']"):
        label = type_desc.find("Label")
        if label is not None:
            typedef_name = label.get("Text", "")
            flat_types[0] = flat_types.get(0, {})
            flat_types[0]["typedef_name"] = typedef_name
            flat_types[0]["type"] = "TypeDef"
            nested = type_desc.find("TypeDesc[@Nested='True']")
            if nested is not None:
                flat_types[0]["underlying_type"] = nested.get("Type", "")
                flat_types[0]["label"] = nested.get("Label", "")

    return {
        "heap_to_consolidated": heap_to_consolidated,
        "consolidated_to_flat": consolidated_to_flat,
        "flat_types": flat_types,
    }


def resolve_type_to_typedef(type_ref: str, type_chain: dict) -> str | None:
    """Resolve a TypeID reference to its typedef name if applicable.

    Args:
        type_ref: String like "TypeID(36)"
        type_chain: Result from parse_type_chain()

    Returns:
        TypeDef name like "System Directory Type.ctl" or None
    """
    match = re.match(r'TypeID\((\d+)\)', type_ref)
    if not match:
        return None

    heap_id = int(match.group(1))

    # Step 1: Heap -> Consolidated
    if heap_id not in type_chain["heap_to_consolidated"]:
        return None
    consolidated_id, type_name = type_chain["heap_to_consolidated"][heap_id]

    if type_name != "TypeDef":
        return None

    # Step 2: Consolidated -> Flat
    flat_id = type_chain["consolidated_to_flat"].get(consolidated_id)
    if flat_id is None:
        return None

    # Step 3: Flat -> TypeDef name
    flat_info = type_chain["flat_types"].get(flat_id, {})
    return flat_info.get("typedef_name")


def load_enum_reference() -> dict:
    """Load the labview-enums.json reference file.

    Returns:
        Dict with typedef definitions, or empty dict if not found
    """
    from .._data import data_dir as _bundled_data_dir
    enums_path = _bundled_data_dir() / "labview-enums.json"

    if not enums_path.exists():
        return {}

    with open(enums_path) as f:
        return json.load(f)


def resolve_typedef_value(
    typedef_ref: ParsedTypeDefRef,
    value: int
) -> ParsedResolvedTypeDefValue | None:
    """Resolve a typedef enum value to its description and OS paths.

    Args:
        typedef_ref: The typedef reference (from parse_typedef_refs)
        value: The integer enum value

    Returns:
        ParsedResolvedTypeDefValue with name, description, and OS paths, or None
    """
    enums = load_enum_reference()
    typedefs = enums.get("typedefs", {})

    key = f"{typedef_ref.vilib_path}:{typedef_ref.name}"

    typedef_info = typedefs.get(key)
    if not typedef_info:
        return None

    values = typedef_info.get("values", {})
    value_info = values.get(str(value)) or values.get(value)
    if value_info:
        return ParsedResolvedTypeDefValue(
            name=value_info.get("name", ""),
            description=value_info.get("description", ""),
            windows_path=value_info.get("windows"),
            unix_path=value_info.get("unix"),
        )

    return None


def parse_typedef_refs(root: ET.Element) -> list[ParsedTypeDefRef]:
    """Parse VICC elements to find typedef references.

    Args:
        root: Root element of the main VI XML

    Returns:
        List of ParsedTypeDefRef with vilib path and control name
    """
    refs = []

    for vicc in root.findall(".//LIvi//VICC"):
        type_desc = vicc.find("TypeDesc")
        if type_desc is None:
            continue
        type_id_str = type_desc.get("TypeID")
        if not type_id_str:
            continue
        type_id = int(type_id_str)

        qual_name = vicc.find("LinkSaveQualName/String")
        if qual_name is None or not qual_name.text:
            continue
        name = qual_name.text

        path_ref = vicc.find("LinkSavePathRef")
        if path_ref is None:
            continue

        path_parts = [s.text for s in path_ref.findall("String") if s.text]
        if not path_parts or path_parts[0] != "<vilib>":
            continue

        vilib_path = "/".join(path_parts[1:-1])

        refs.append(ParsedTypeDefRef(type_id=type_id, name=name, vilib_path=vilib_path))

    return refs
