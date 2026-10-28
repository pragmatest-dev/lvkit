"""Type mapping and VCTP parsing for LabVIEW types.

Handles parsing TypeID mappings and VCTP section to build LVType structures.
"""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..models import ClusterField, EnumValue, LVType, LVTypeKind, PaneSlot
from .constants import CORRUPT_STUB_FORMAT
from .flags import CONPANE_FLAG_OUTPUT
from .heap_types import HeapTypes, decode_heap_types, heap_sidecar_path
from .naming import build_qualified_name, build_relative_path
from .utils import clean_labview_string
from .xml_trees import parsed_xml

# The VI Server class a generic reference's ``CtlFlags`` names
# (docs/_internal/design/labview-binary-format.md).
_SERVER_CLASSES = {"0x0001": "Application", "0x0002": "VI"}


class TypeMap(dict[int, LVType]):
    """A VI's TypeID -> type table. ``pool_failure`` says why a pre-LV9 VI's heap
    type pool could not be read to its end (None when it could, and for every
    VI that has a VCTP). ``labels`` maps a TypeID to the label its type
    descriptor carries (``path in``, ``error out``), for the TypeIDs that have
    one. ``flat_ids`` maps a TypeID to its FlatTypeID (the position of its type
    descriptor in the VCTP)."""

    pool_failure: str | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.labels: dict[int, str] = {}
        self.flat_ids: dict[int, int] = {}


def parse_type_map_rich(xml_path: Path | str) -> TypeMap:
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
    type_map = TypeMap()
    heap_to_consolidated: dict[int, int] = {}
    # Track type names for primitives before we have full type info
    type_names: dict[int, str] = {}

    # First pass: parse comments to get heap->consolidated mapping and basic types
    with open(xml_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            # Match <!-- Heap TypeID N = Consolidated TypeID M: TypeName -->
            match = re.search(
                r"Heap TypeID\s+(\d+)\s*=\s*Consolidated TypeID\s+(\d+):\s*(\w+)", line
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
            match = re.search(r"<!--\s*TypeID\s+(\d+):\s*(\w+)", line)
            if match:
                type_id = int(match.group(1))
                type_name = match.group(2)
                if type_id not in type_map:
                    type_names[type_id] = type_name
                    type_map[type_id] = _make_primitive_lvtype(type_name)

    # Second pass: parse VCTP to get TypeDef details
    # Chain: Heap TypeID -> Consolidated ID -> FlatTypeID -> VCTP TypeDesc
    try:
        root = parsed_xml(xml_path)

        # Build consolidated -> flat mapping from TopLevel
        consolidated_to_flat: dict[int, int] = {}
        for td in root.findall(".//VCTP//TopLevel/TypeDesc"):
            index = td.get("Index")
            flat_id = td.get("FlatTypeID")
            if index and flat_id:
                consolidated_to_flat[int(index)] = int(flat_id)

        # Parse full type info from VCTP (includes cluster fields, array elements)
        vctp_types = parse_vctp_types(xml_path)
        flat_descs = flat_type_descs(root)

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

        vicc_paths = _typedef_ctl_paths(root)

        # Now update type_map: heap -> consolidated -> flat -> full type info
        for heap_id, cons_id in heap_to_consolidated.items():
            flat_id = consolidated_to_flat.get(cons_id)
            if flat_id is None:
                continue

            type_map.flat_ids[heap_id] = flat_id
            label = _field_label(flat_descs.get(flat_id), "")
            if label:
                type_map.labels[heap_id] = label

            # Use VCTP result directly — it has the full type info
            if flat_id in vctp_types:
                lv_type = vctp_types[flat_id]
                type_map[heap_id] = lv_type

            # Add typedef identity if this heap_id is a typedef
            if flat_id in flat_to_typedef:
                _, ctl_filename = flat_to_typedef[flat_id]
                if ctl_filename and heap_id in type_map:
                    _apply_typedef_identity(type_map[heap_id], ctl_filename, vicc_paths)

    except ET.ParseError:
        pass  # Fall back to comment-based parsing only

    if not type_map:
        heap = _parse_pre_lv9_heap_types(xml_path)
        type_map.update(heap.types)
        type_map.pool_failure = heap.failure

    return type_map


def _typedef_ctl_paths(root: ET.Element) -> dict[str, list[str]]:
    """Map each ``.ctl`` filename to its path tokens, from the VI's VICC link
    records (matched by filename, not type id; ``<vilib>``-style markers dropped).
    """
    vicc_paths: dict[str, list[str]] = {}
    for vicc in root.findall(".//LIvi//VICC"):
        path_ref = vicc.find("LinkSavePathRef")
        if path_ref is None:
            continue

        path_parts = [s.text for s in path_ref.findall("String") if s.text]
        if not path_parts:
            continue

        filename = path_parts[-1]
        if not filename.endswith(".ctl"):
            continue

        clean_parts = [p for p in path_parts if not p.startswith("<")]
        if clean_parts:
            vicc_paths[filename] = clean_parts
    return vicc_paths


def _apply_typedef_identity(
    lv_type: LVType,
    ctl_filename: str,
    vicc_paths: dict[str, list[str]],
) -> None:
    """Stamp a typedef's qualified name (and relative path, when its link record
    gives one) onto ``lv_type``."""
    lv_type.typedef_file = ctl_filename
    if ctl_filename not in vicc_paths:
        lv_type.typedef_name = ctl_filename
        return
    path_tokens = vicc_paths[ctl_filename]
    lv_type.typedef_path = build_relative_path(path_tokens)
    owner_chain = [
        t for t in path_tokens[:-1] if t.endswith((".llb", ".lvlib", ".lvclass"))
    ]
    lv_type.typedef_name = build_qualified_name(owner_chain, ctl_filename)


def _parse_pre_lv9_heap_types(xml_path: Path | str) -> HeapTypes:
    """The heap ``TypeID`` map of a pre-LV9 VI, decoded from its ``DTHP`` sidecar
    (see ``heap_types``); empty when the VI has none. A typedef whose ``.ctl`` has
    a link record gains the same qualified name / path an LV9+ typedef gets."""
    sidecar = heap_sidecar_path(xml_path)
    if not sidecar.exists():
        return HeapTypes()
    heap = decode_heap_types(sidecar.read_bytes())
    try:
        vicc_paths = _typedef_ctl_paths(parsed_xml(xml_path))
    except ET.ParseError:
        return heap
    for lv_type in heap.types.values():
        if lv_type.typedef_file is not None and lv_type.typedef_file in vicc_paths:
            _apply_typedef_identity(lv_type, lv_type.typedef_file, vicc_paths)
    return heap


def flat_type_descs(root: ET.Element) -> dict[int, ET.Element]:
    """Every VCTP top-level TypeDesc by its FlatTypeID (its position across the
    VCTP sections)."""
    flat_types: dict[int, ET.Element] = {}
    for section in root.findall(".//VCTP/Section"):
        for type_desc in section.findall("TypeDesc"):
            flat_types[len(flat_types)] = type_desc
    return flat_types


def _field_label(ref_td: ET.Element | None, default_name: str) -> str:
    """Resolve a cluster field's display name from its own TypeDesc.

    Usually the field's ``Label`` XML attribute (e.g. ``Label="Time"``). But
    when the field's declared type is itself a bare TypeDef (no ``Label``
    attribute of its own — a TypeDef instead carries its ``.ctl`` filename via
    a sibling ``<Label Text="....ctl"/>`` ELEMENT, not an attribute), the real
    field name lives one level down, on the TypeDef's ``Nested='True'`` inner
    TypeDesc (e.g. ``eventsource.ctl``'s wrapped ``<TypeDesc ... Label="Source"
    Nested="True"/>``). Falls back to ``default_name`` when neither is present.
    """
    if ref_td is None:
        return default_name
    label = ref_td.get("Label")
    if label:
        return clean_labview_string(label) or default_name
    if ref_td.get("Type") == "TypeDef":
        nested = ref_td.find("TypeDesc[@Nested='True']")
        if nested is not None:
            nested_label = nested.get("Label")
            if nested_label:
                return clean_labview_string(nested_label) or default_name
    return default_name


def _make_primitive_lvtype(type_name: str) -> LVType:
    """Create an LVType for a primitive type name."""
    # Map LabVIEW type names to kind
    if type_name in ("Cluster",):
        return LVType(kind=LVTypeKind.CLUSTER, underlying_type=type_name)
    elif type_name in ("Array",):
        return LVType(kind=LVTypeKind.ARRAY, underlying_type=type_name)
    elif type_name.startswith("Enum") or type_name == "Ring":
        return LVType(kind=LVTypeKind.ENUM, underlying_type=type_name)
    else:
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=type_name)


# Type codes 0x73 and 0x74, which pylabview does not name (the patched reader
# writes them to the XML as their decimal code).
_COLLECTION_TYPE_NAMES = {"115": "Set", "116": "Map"}


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
        root = parsed_xml(xml_path)
    except ET.ParseError:
        return {}

    # First pass: collect all TypeDescs by FlatTypeID
    flat_types = flat_type_descs(root)

    # Second pass: build LVTypes with resolved references
    resolved: dict[int, LVType] = {}

    def build_lvtype(td: ET.Element, visited: set[int]) -> LVType:
        """The LVType one TypeDesc element describes, its child references
        followed by ``resolve_type``. A top-level descriptor and the descriptor
        nested inside a TypeDef are both built here."""
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
                        field_name = _field_label(ref_td, default_name)
                        fields.append(ClusterField(name=field_name, type=field_type))

            lv_type = LVType(
                kind=LVTypeKind.CLUSTER,
                underlying_type="Cluster",
                # A cluster that lists no members is empty (``[]``); one whose
                # members could not be resolved has no known fields (None).
                fields=fields if fields or not td.findall("TypeDesc") else None,
            )

        elif type_name in ("Array", "SubArray"):
            # A SubArray is an array VIEW — same element type + dimensions as a
            # plain Array. LabVIEW stores it as the output type of Reverse 1D
            # Array, Rotate, Split, etc. Its typedesc has the identical shape
            # (<Dimension> + nested element <TypeDesc>), so parse it identically;
            # otherwise the element type is dropped and it renders/typechecks as
            # unknown instead of an array.
            element_type = None
            dims = len(td.findall("Dimension"))
            for nested in td.findall("TypeDesc"):
                nested_type_id = nested.get("TypeID")
                if nested_type_id:
                    element_type = resolve_type(int(nested_type_id), visited)
                    break

            lv_type = LVType(
                kind=LVTypeKind.ARRAY,
                underlying_type=type_name,
                element_type=element_type,
                dimensions=dims if dims > 0 else 1,
            )

        elif type_name == "TypeDef":
            # A TypeDef wraps one Nested='True' descriptor: it is that
            # descriptor's type (cluster fields, enum labels, array element and
            # dimensions, refnum kind and payload, ...) plus the typedef's
            # identity, the .ctl name.
            nested = td.find("TypeDesc[@Nested='True']")
            typedef_name = None
            for lbl in td.findall("Label"):
                text = lbl.get("Text", "")
                if text.endswith(".ctl"):
                    typedef_name = text
                    break
            if nested is None:
                lv_type = LVType(
                    kind=LVTypeKind.PRIMITIVE,
                    underlying_type=None,
                    typedef_name=typedef_name,
                    typedef_file=typedef_name,
                )
            else:
                lv_type = dataclasses.replace(
                    build_lvtype(nested, visited),
                    typedef_name=typedef_name,
                    typedef_file=typedef_name,
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
                kind=LVTypeKind.ENUM,
                underlying_type=type_name,
                values=enum_values,
            )

        elif type_name == "Refnum":
            # Refnum type - extract RefType and class name if present
            ref_type = td.get("RefType")
            classname = None
            class_file = None
            element_type = None
            user_event_name = None

            if ref_type == "UDClassInst":
                # Extract fully qualified class name from <Item> chain.
                # Single class: <Item Text="TestCase.lvclass" />
                # Nested:       <Item Text="Lib.lvlib" /><Item Text="Cls.lvclass" />
                # → "Lib.lvlib:Cls.lvclass"
                items = td.findall("Item")
                if items:
                    parts = [it.get("Text", "") for it in items]
                    classname = ":".join(parts)
                    class_file = parts[-1]
            else:
                # A parametrized refnum (Queue/Notifier/UserEvent/DataValueRef)
                # carries its element/payload type as a single nested TypeDesc,
                # by TypeID. Resolve it so a Queue of error clusters is faithful,
                # not a bare "Queue refnum". Only when there's EXACTLY one nested
                # ref (EventReg lists several event types — not a single element).
                nested = td.findall("TypeDesc[@TypeID]")
                if len(nested) == 1:
                    tid = nested[0].get("TypeID")
                    if tid:
                        element_type = resolve_type(int(tid), visited)
                        if ref_type == "UserEvent":
                            user_event_name = (
                                _field_label(flat_types.get(int(tid)), "") or None
                            )

            lv_type = LVType(
                kind=LVTypeKind.PRIMITIVE,
                underlying_type="Refnum",
                ref_type=ref_type,
                server_class=(
                    _SERVER_CLASSES.get(td.get("CtlFlags", ""))
                    if ref_type == "LVObjCtl"
                    else None
                ),
                classname=classname,
                class_file=class_file,
                element_type=element_type,
                user_event_name=user_event_name,
            )

        elif type_name in _COLLECTION_TYPE_NAMES:
            # A Set (one member type: its element) or a Map (two: key, then
            # value). The members are listed by TypeID like a cluster's fields.
            members = [
                resolve_type(int(child.attrib["TypeID"]), visited)
                for child in td.findall("TypeDesc[@TypeID]")
            ]
            lv_type = LVType(
                kind=LVTypeKind.PRIMITIVE,
                underlying_type=_COLLECTION_TYPE_NAMES[type_name],
                element_type=members[0] if members else None,
                value_type=members[1] if len(members) > 1 else None,
            )

        elif type_name == "Function":
            # A connector pane: one child per slot, the unused ones Void. Bit 8 of
            # a slot's Flags marks an output.
            slots: list[PaneSlot] = []
            for index, child in enumerate(td.findall("TypeDesc[@TypeID]")):
                slot_type = resolve_type(int(child.attrib["TypeID"]), visited)
                if slot_type is None or slot_type.underlying_type == "Void":
                    continue
                flags = int(child.get("Flags", "0x0000"), 16)
                label = _field_label(flat_types.get(int(child.attrib["TypeID"])), "")
                slots.append(
                    PaneSlot(
                        index,
                        bool(flags & CONPANE_FLAG_OUTPUT),
                        slot_type,
                        label or None,
                    )
                )
            lv_type = LVType(
                kind=LVTypeKind.PRIMITIVE,
                underlying_type="Function",
                pane=tuple(slots),
            )

        elif type_name == "MeasureData":
            # Waveform / digital-data: an opaque built-in structure (no VCTP
            # children). Keep its FLAVOR so a built-in component table can name
            # its fields (see measure_data.py / the nMux field resolution).
            lv_type = LVType(
                kind=LVTypeKind.PRIMITIVE,
                underlying_type="MeasureData",
                measure_flavor=td.get("Flavor"),
            )

        else:
            # Primitive type
            lv_type = LVType(
                kind=_get_kind(type_name),
                underlying_type=type_name,
            )

        if td.get("Format") == CORRUPT_STUB_FORMAT:
            lv_type = dataclasses.replace(
                lv_type, unreadable="its type descriptor could not be read"
            )
        return lv_type

    def resolve_type(fid: int, visited: set[int] | None = None) -> LVType | None:
        """Recursively resolve a FlatTypeID to LVType."""
        if visited is None:
            visited = set()

        if fid in visited:
            # Cycle detection - return placeholder
            return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Recursive")

        if fid in resolved:
            return resolved[fid]

        if fid not in flat_types:
            return None

        lv_type = build_lvtype(flat_types[fid], visited | {fid})
        resolved[fid] = lv_type
        return lv_type

    # Resolve all types
    for fid in flat_types:
        resolve_type(fid)

    return resolved


def _get_kind(type_name: str) -> LVTypeKind:
    """Get LVType kind from type name. An unrecognized name is a PRIMITIVE (the
    closed-set catch-all -- there is no "unknown" kind)."""
    if type_name == "Cluster":
        return LVTypeKind.CLUSTER
    elif type_name == "Array":
        return LVTypeKind.ARRAY
    elif type_name.startswith("Enum") or type_name == "Ring":
        return LVTypeKind.ENUM
    elif type_name in ("UnitUInt8", "UnitUInt16", "UnitUInt32"):
        return LVTypeKind.ENUM  # a unit-tagged unsigned integer is an enum's storage
    else:
        return LVTypeKind.PRIMITIVE
