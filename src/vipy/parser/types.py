"""Type resolution and mapping for parser module."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..naming import build_qualified_name, build_relative_path
from .models import DefaultValue, ResolvedTypeDefValue, TypeDefRef


@dataclass
class TypeInfo:
    """Rich type information including typedef details."""
    type: str  # Underlying type (NumInt32, Cluster, Path, etc.)
    typedef_path: str | None = None  # Filesystem path: "Utility/sysdir.llb/Type.ctl"
    typedef_name: str | None = None  # Qualified name: "sysdir.llb:Type.ctl"


def parse_type_map_rich(xml_path: Path | str) -> dict[int, TypeInfo]:
    """Parse TypeID mappings with rich type info from main XML.

    For TypeDefs, extracts the underlying type and .ctl filename.
    For regular types, just the type name.

    Args:
        xml_path: Path to main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping TypeID -> TypeInfo
    """
    type_map: dict[int, TypeInfo] = {}
    heap_to_consolidated: dict[int, int] = {}

    # First pass: parse comments to get heap->consolidated mapping and basic types
    with open(xml_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            # Match <!-- Heap TypeID N = Consolidated TypeID M: TypeName -->
            match = re.search(
                r'Heap TypeID\s+(\d+)\s*=\s*Consolidated TypeID\s+(\d+):\s*(\w+)',
                line
            )
            if match:
                heap_id = int(match.group(1))
                consolidated_id = int(match.group(2))
                type_name = match.group(3)
                heap_to_consolidated[heap_id] = consolidated_id
                type_map[heap_id] = TypeInfo(type=type_name)
                continue

            # Match <!-- TypeID N: TypeName -->
            match = re.search(r'<!--\s*TypeID\s+(\d+):\s*(\w+)', line)
            if match:
                type_id = int(match.group(1))
                type_name = match.group(2)
                if type_id not in type_map:
                    type_map[type_id] = TypeInfo(type=type_name)

    # Second pass: parse VCTP to get TypeDef details
    # Chain: Heap TypeID -> Consolidated ID -> FlatTypeID -> VCTP TypeDesc
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Build consolidated -> flat mapping from TopLevel
        consolidated_to_flat: dict[int, int] = {}
        for td in root.findall(".//VCTP//TopLevel/TypeDesc"):
            index = td.get("Index")
            flat_id = td.get("FlatTypeID")
            if index and flat_id:
                consolidated_to_flat[int(index)] = int(flat_id)

        # Build flat -> typedef info from VCTP Section TypeDescs
        flat_to_typedef: dict[int, tuple[str | None, str | None]] = {}
        for section in root.findall(".//VCTP/Section"):
            # Find TypeDefs and their FlatTypeID from preceding comment
            for i, type_desc in enumerate(section.findall("TypeDesc")):
                if type_desc.get("Type") != "TypeDef":
                    continue

                # Get the .ctl filename from Label element
                typedef_name = None
                for label in type_desc.findall("Label"):
                    text = label.get("Text", "")
                    if text.endswith(".ctl"):
                        typedef_name = text
                        break

                # Get nested underlying type
                nested = type_desc.find("TypeDesc[@Nested='True']")
                underlying_type = nested.get("Type") if nested is not None else None

                # The FlatTypeID is the position in the section
                flat_to_typedef[i] = (underlying_type, typedef_name)

        # Parse VICC elements to get path info for typedefs
        # Maps ctl_filename -> path_tokens (match by filename, not type_id)
        vicc_paths: dict[str, list[str]] = {}
        for vicc in root.findall(".//LIvi//VICC"):
            path_ref = vicc.find("LinkSavePathRef")
            if path_ref is None:
                continue

            path_parts = [s.text for s in path_ref.findall("String") if s.text]
            if not path_parts:
                continue

            # Check if this is a .ctl file
            filename = path_parts[-1]
            if not filename.endswith('.ctl'):
                continue

            # Remove special markers like <vilib>, <userlib>
            clean_parts = [p for p in path_parts if not p.startswith('<')]
            if clean_parts:
                vicc_paths[filename] = clean_parts

        # Now update type_map: heap -> consolidated -> flat -> typedef
        for heap_id, cons_id in heap_to_consolidated.items():
            flat_id = consolidated_to_flat.get(cons_id)
            if flat_id is not None and flat_id in flat_to_typedef:
                underlying_type, ctl_filename = flat_to_typedef[flat_id]
                if heap_id in type_map:
                    existing = type_map[heap_id]

                    # Build path and qualified name from VICC if available
                    typedef_path = None
                    typedef_qname = None
                    if ctl_filename and ctl_filename in vicc_paths:
                        path_tokens = vicc_paths[ctl_filename]
                        typedef_path = build_relative_path(path_tokens)

                        # Extract owner chain (containers in path)
                        owner_chain = [
                            t for t in path_tokens[:-1]
                            if t.endswith(('.llb', '.lvlib', '.lvclass'))
                        ]
                        typedef_qname = build_qualified_name(
                            owner_chain, ctl_filename
                        )
                    elif ctl_filename:
                        # No path info, just use filename
                        typedef_qname = ctl_filename

                    type_map[heap_id] = TypeInfo(
                        type=underlying_type or existing.type,
                        typedef_path=typedef_path,
                        typedef_name=typedef_qname
                    )
    except ET.ParseError:
        pass  # Fall back to comment-based parsing only

    return type_map


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
    return {k: v.type for k, v in rich_map.items()}


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


def resolve_type_rich(type_ref: str, type_map: dict[int, TypeInfo]) -> TypeInfo:
    """Resolve TypeID(N) reference to rich TypeInfo.

    Args:
        type_ref: String like "TypeID(37)"
        type_map: Mapping from TypeID -> TypeInfo

    Returns:
        TypeInfo with type and optional typedef, or TypeInfo with original string
    """
    match = re.match(r'TypeID\((\d+)\)', type_ref)
    if match:
        type_id = int(match.group(1))
        return type_map.get(type_id, TypeInfo(type=type_ref))
    return TypeInfo(type=type_ref)


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
    data_dir = Path(__file__).parent.parent.parent.parent / "data"
    enums_path = data_dir / "labview-enums.json"

    if not enums_path.exists():
        return {}

    with open(enums_path) as f:
        return json.load(f)


def resolve_typedef_value(
    typedef_ref: TypeDefRef,
    value: int
) -> ResolvedTypeDefValue | None:
    """Resolve a typedef enum value to its description and OS paths.

    Args:
        typedef_ref: The typedef reference (from parse_typedef_refs)
        value: The integer enum value

    Returns:
        ResolvedTypeDefValue with name, description, and OS paths, or None
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
        return ResolvedTypeDefValue(
            name=value_info.get("name", ""),
            description=value_info.get("description", ""),
            windows_path=value_info.get("windows"),
            unix_path=value_info.get("unix"),
        )

    return None


def parse_typedef_refs(root: ET.Element) -> list[TypeDefRef]:
    """Parse VICC elements to find typedef references.

    Args:
        root: Root element of the main VI XML

    Returns:
        List of TypeDefRef with vilib path and control name
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

        refs.append(TypeDefRef(type_id=type_id, name=name, vilib_path=vilib_path))

    return refs


def parse_dfds(xml_path: Path | str) -> dict[int, DefaultValue]:
    """Parse the DFDS (Default Fill of Data Space) section for default values.

    Args:
        xml_path: Path to the main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping TypeID to DefaultValue with parsed values
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    defaults: dict[int, DefaultValue] = {}

    for data_fill in root.findall(".//DFDS//DataFill"):
        type_id_str = data_fill.get("TypeID")
        if not type_id_str:
            continue
        type_id = int(type_id_str)

        values, structure = _parse_data_fill(data_fill)
        if values is not None:
            defaults[type_id] = DefaultValue(
                type_id=type_id,
                values=values,
                structure=structure,
            )

    return defaults


def _parse_data_fill(elem: ET.Element) -> tuple[list[Any] | None, str]:
    """Parse a DataFill element and extract values."""
    cluster = elem.find("Cluster") or elem.find("SpecialDSTMCluster/Cluster")
    if cluster is not None:
        values = []
        for child in cluster:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values, "Cluster"

    array = elem.find("Array") or elem.find("SpecialDSTMCluster/Array")
    if array is not None:
        dim = array.find("dim")
        dim_val = int(dim.text) if dim is not None and dim.text else 0
        values = []
        for child in array:
            if child.tag != "dim":
                val = _parse_value_element(child)
                if val is not None:
                    values.append(val)
        return values, f"Array[{dim_val}]"

    for child in elem:
        val = _parse_value_element(child)
        if val is not None:
            return [val], "scalar"

    return None, "unknown"


def _parse_value_element(elem: ET.Element) -> Any:
    """Parse a single value element (Boolean, I32, DBL, String, etc.)."""
    tag = elem.tag

    if tag == "Boolean":
        return elem.text == "1" if elem.text else False
    elif tag in ("I32", "I16", "I8", "U32", "U16", "U8"):
        return int(elem.text) if elem.text else 0
    elif tag in ("DBL", "SGL", "EXT"):
        return float(elem.text) if elem.text else 0.0
    elif tag == "String":
        return elem.text or ""
    elif tag == "Path":
        path_str = elem.find("String")
        return path_str.text if path_str is not None and path_str.text else ""
    elif tag == "Cluster":
        values = []
        for child in elem:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values
    elif tag == "Array":
        values = []
        for child in elem:
            if child.tag != "dim":
                val = _parse_value_element(child)
                if val is not None:
                    values.append(val)
        return values
    elif tag == "RepeatedBlock":
        values = []
        for child in elem:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values
    elif tag == "Block":
        return elem.text or ""

    return None
