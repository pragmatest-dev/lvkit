"""Type resolution and mapping for parser module."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..graph_types import LVType
from ..naming import build_qualified_name, build_relative_path
from .models import DefaultValue, ResolvedTypeDefValue, TypeDefRef


def parse_type_map_rich(xml_path: Path | str) -> dict[int, LVType]:
    """Parse TypeID mappings with rich type info from main XML.

    For TypeDefs, extracts the underlying type and .ctl filename.
    For Clusters, extracts field names and types.
    For Arrays, extracts element types.
    For regular types, just the type name.

    Args:
        xml_path: Path to main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping TypeID -> LVType with full type structure
    """
    type_map: dict[int, LVType] = {}
    heap_to_consolidated: dict[int, int] = {}
    # Track type names for primitives before we have full type info
    type_names: dict[int, str] = {}

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
                type_names[heap_id] = type_name
                type_map[heap_id] = _make_primitive_lvtype(type_name)
                continue

            # Match <!-- TypeID N: TypeName -->
            match = re.search(r'<!--\s*TypeID\s+(\d+):\s*(\w+)', line)
            if match:
                type_id = int(match.group(1))
                type_name = match.group(2)
                if type_id not in type_map:
                    type_names[type_id] = type_name
                    type_map[type_id] = _make_primitive_lvtype(type_name)

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

        # Parse full type info from VCTP (includes cluster fields, array elements)
        vctp_types = parse_vctp_types(xml_path)

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

        # Now update type_map: heap -> consolidated -> flat -> full type info
        for heap_id, cons_id in heap_to_consolidated.items():
            flat_id = consolidated_to_flat.get(cons_id)
            if flat_id is None:
                continue

            existing_type_name = type_names.get(heap_id, "unknown")

            # Check if this is a typedef
            if flat_id in flat_to_typedef:
                underlying_type, ctl_filename = flat_to_typedef[flat_id]

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

                # Create LVType for this typedef
                if typedef_path:
                    type_map[heap_id] = LVType(
                        kind="typedef_ref",
                        underlying_type=underlying_type or existing_type_name,
                        typedef_path=typedef_path,
                        typedef_name=typedef_qname,
                    )
                else:
                    type_map[heap_id] = _make_primitive_lvtype(
                        underlying_type or existing_type_name
                    )

            # For non-typedefs (clusters, arrays), use VCTP full type info
            elif flat_id in vctp_types:
                vctp_type = vctp_types[flat_id]
                # Copy fields/element_type from VCTP parsed type
                if vctp_type.kind == "cluster" and vctp_type.fields:
                    type_map[heap_id] = LVType(
                        kind="cluster",
                        underlying_type=existing_type_name,
                        fields=vctp_type.fields,
                    )
                elif vctp_type.kind == "array" and vctp_type.element_type:
                    type_map[heap_id] = LVType(
                        kind="array",
                        underlying_type=existing_type_name,
                        element_type=vctp_type.element_type,
                        dimensions=vctp_type.dimensions,
                    )
                elif vctp_type.kind == "enum" and vctp_type.values:
                    type_map[heap_id] = LVType(
                        kind="enum",
                        underlying_type=existing_type_name,
                        values=vctp_type.values,
                    )

    except ET.ParseError:
        pass  # Fall back to comment-based parsing only

    return type_map


def _make_primitive_lvtype(type_name: str) -> LVType:
    """Create an LVType for a primitive type name."""
    # Map LabVIEW type names to kind
    if type_name in ("Cluster",):
        return LVType(kind="cluster", underlying_type=type_name)
    elif type_name in ("Array",):
        return LVType(kind="array", underlying_type=type_name)
    elif type_name.startswith("Enum") or type_name == "Ring":
        return LVType(kind="enum", underlying_type=type_name)
    else:
        return LVType(kind="primitive", underlying_type=type_name)


def parse_vctp_types(xml_path: Path | str) -> dict[int, LVType]:
    """Parse VCTP section to get full type info including cluster fields.

    This does a two-pass parse:
    1. First pass: collect all FlatTypeID → basic TypeDesc info
    2. Second pass: resolve nested references to build full LVType

    Args:
        xml_path: Path to main .xml file

    Returns:
        Dict mapping FlatTypeID → LVType with fields/element_type populated
    """
    from ..graph_types import ClusterField, EnumValue

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError:
        return {}

    # First pass: collect all TypeDescs by FlatTypeID
    flat_types: dict[int, ET.Element] = {}
    flat_id = 0

    for section in root.findall(".//VCTP/Section"):
        for type_desc in section.findall("TypeDesc"):
            flat_types[flat_id] = type_desc
            flat_id += 1

    # Second pass: build LVTypes with resolved references
    resolved: dict[int, LVType] = {}

    def resolve_type(fid: int, visited: set[int] | None = None) -> LVType | None:
        """Recursively resolve a FlatTypeID to LVType."""
        if visited is None:
            visited = set()

        if fid in visited:
            # Cycle detection - return placeholder
            return LVType(kind="primitive", underlying_type="Recursive")

        if fid in resolved:
            return resolved[fid]

        if fid not in flat_types:
            return None

        visited = visited | {fid}
        td = flat_types[fid]
        type_name = td.get("Type", "")

        if type_name == "Cluster":
            # Parse cluster fields
            fields: list[ClusterField] = []
            for nested in td.findall("TypeDesc"):
                nested_type_id = nested.get("TypeID")
                if nested_type_id:
                    field_type = resolve_type(int(nested_type_id), visited)
                    if field_type:
                        # Get field name from the referenced type's label
                        ref_td = flat_types.get(int(nested_type_id))
                        default_name = f"field_{len(fields)}"
                        # Use 'is not None' - Element bool is based on children count
                        field_name = (
                            ref_td.get("Label", default_name)
                            if ref_td is not None else default_name
                        )
                        fields.append(ClusterField(name=field_name, type=field_type))

            lv_type = LVType(
                kind="cluster",
                underlying_type="Cluster",
                fields=fields if fields else None,
            )

        elif type_name == "Array":
            # Parse array element type
            element_type = None
            dims = len(td.findall("Dimension"))
            for nested in td.findall("TypeDesc"):
                nested_type_id = nested.get("TypeID")
                if nested_type_id:
                    element_type = resolve_type(int(nested_type_id), visited)
                    break

            lv_type = LVType(
                kind="array",
                underlying_type="Array",
                element_type=element_type,
                dimensions=dims if dims > 0 else 1,
            )

        elif type_name == "TypeDef":
            # Parse typedef - get underlying type and name
            nested = td.find("TypeDesc[@Nested='True']")
            underlying = nested.get("Type") if nested is not None else None
            typedef_name = None
            for lbl in td.findall("Label"):
                text = lbl.get("Text", "")
                if text.endswith(".ctl"):
                    typedef_name = text
                    break

            # Parse enum labels if present
            enum_values = None
            if nested is not None:
                enum_labels = nested.findall("EnumLabel")
                if enum_labels:
                    enum_values = {}
                    for i, el in enumerate(enum_labels):
                        if el.text:
                            enum_values[el.text] = EnumValue(value=i)

            lv_type = LVType(
                kind="typedef_ref" if typedef_name else _get_kind(underlying or ""),
                underlying_type=underlying,
                typedef_name=typedef_name,
                values=enum_values,
            )

        elif type_name in ("UnitUInt16", "UnitUInt32", "UnitUInt8") or (
            type_name.startswith("Enum")
        ):
            # Enum type - parse labels
            enum_values = None
            enum_labels = td.findall("EnumLabel")
            if enum_labels:
                enum_values = {}
                for i, el in enumerate(enum_labels):
                    if el.text:
                        enum_values[el.text] = EnumValue(value=i)

            lv_type = LVType(
                kind="enum",
                underlying_type=type_name,
                values=enum_values,
            )

        else:
            # Primitive type
            lv_type = LVType(
                kind=_get_kind(type_name),
                underlying_type=type_name,
            )

        resolved[fid] = lv_type
        return lv_type

    # Resolve all types
    for fid in flat_types:
        resolve_type(fid)

    return resolved


def _get_kind(type_name: str) -> str:
    """Get LVType kind from type name."""
    if type_name == "Cluster":
        return "cluster"
    elif type_name == "Array":
        return "array"
    elif type_name.startswith("Enum") or type_name == "Ring":
        return "enum"
    elif type_name.startswith("Unit"):
        return "enum"  # UnitUInt16 etc are often enums
    else:
        return "primitive"


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
