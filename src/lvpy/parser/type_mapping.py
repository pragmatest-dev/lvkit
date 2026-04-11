"""Type mapping and VCTP parsing for LabVIEW types.

Handles parsing TypeID mappings and VCTP section to build LVType structures.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import ClusterField, EnumValue, LVType
from ..naming import build_qualified_name, build_relative_path
from .utils import clean_labview_string


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

            # Use VCTP result directly — it has the full type info
            if flat_id in vctp_types:
                lv_type = vctp_types[flat_id]
                type_map[heap_id] = lv_type

            # Add typedef identity if this heap_id is a typedef
            if flat_id in flat_to_typedef:
                _, ctl_filename = flat_to_typedef[flat_id]
                if ctl_filename and heap_id in type_map:
                    # Build qualified name from VICC path info
                    if ctl_filename in vicc_paths:
                        path_tokens = vicc_paths[ctl_filename]
                        type_map[heap_id].typedef_path = (
                            build_relative_path(path_tokens)
                        )
                        owner_chain = [
                            t for t in path_tokens[:-1]
                            if t.endswith(
                                ('.llb', '.lvlib', '.lvclass'),
                            )
                        ]
                        type_map[heap_id].typedef_name = (
                            build_qualified_name(
                                owner_chain, ctl_filename,
                            )
                        )
                    else:
                        type_map[heap_id].typedef_name = ctl_filename

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
                        raw_label = (
                            ref_td.get("Label", default_name)
                            if ref_td is not None else default_name
                        )
                        field_name = clean_labview_string(raw_label) or default_name
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
            td_fields: list[ClusterField] | None = None
            if nested is not None:
                enum_labels = nested.findall("EnumLabel")
                if enum_labels:
                    enum_values = {}
                    for i, el in enumerate(enum_labels):
                        if el.text:
                            enum_values[el.text] = EnumValue(value=i)

                # Recurse into nested type's children (cluster fields,
                # array elements, etc.) — same as top-level parsing.
                if underlying == "Cluster":
                    td_fields = []
                    for child in nested.findall("TypeDesc"):
                        child_tid = child.get("TypeID")
                        if child_tid:
                            ft = resolve_type(int(child_tid), visited)
                            ref = flat_types.get(int(child_tid))
                            default = f"field_{len(td_fields)}"
                            raw = (
                                ref.get("Label", default)
                                if ref is not None else default
                            )
                            name = clean_labview_string(raw) or default
                            td_fields.append(
                                ClusterField(name=name, type=ft),
                            )

            lv_type = LVType(
                kind=_get_kind(underlying or ""),
                underlying_type=underlying,
                typedef_name=typedef_name,
                values=enum_values,
                fields=td_fields if td_fields else None,
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

        elif type_name == "Refnum":
            # Refnum type - extract RefType and class name if present
            ref_type = td.get("RefType")
            classname = None

            if ref_type == "UDClassInst":
                # Extract fully qualified class name from <Item> chain.
                # Single class: <Item Text="TestCase.lvclass" />
                # Nested:       <Item Text="Lib.lvlib" /><Item Text="Cls.lvclass" />
                # → "Lib.lvlib:Cls.lvclass"
                items = td.findall("Item")
                if items:
                    parts = [it.get("Text", "") for it in items]
                    classname = ":".join(parts)

            lv_type = LVType(
                kind="primitive",
                underlying_type="Refnum",
                ref_type=ref_type,
                classname=classname,
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
