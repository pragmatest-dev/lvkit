"""Parse LabVIEW library and class files for structural mapping."""

from __future__ import annotations

import re
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lvpy.graph_types import _LV_TO_PYTHON_TYPE


@dataclass
class LVMethod:
    """A method in a LabVIEW class."""
    name: str
    vi_path: str
    scope: str  # "public", "private", "protected"
    is_static: bool = False
    is_accessor: bool = False
    accessor_type: str | None = None  # "getter" or "setter"
    accessor_field: str | None = None  # field name being accessed


@dataclass
class LVPrivateDataField:
    """A private data field in a LabVIEW class."""
    name: str
    python_type: str = "Any"  # Inferred Python type
    default_value: str | None = None  # Default value expression
    lv_type_name: str = ""  # Raw LV type from VCTP (e.g. "String", "Boolean")
    sub_fields: list[LVPrivateDataField] = field(default_factory=list)


@dataclass
class LVClass:
    """A LabVIEW class."""
    name: str
    path: Path
    parent_class: str | None = None
    private_data_ctl: str | None = None
    methods: list[LVMethod] = field(default_factory=list)
    private_data_fields: list[LVPrivateDataField] = field(default_factory=list)


@dataclass
class LVLibrary:
    """A LabVIEW library."""
    name: str
    path: Path
    version: str | None = None
    members: list[LVLibraryMember] = field(default_factory=list)


@dataclass
class LVProjectItem:
    """An item in a LabVIEW project."""
    name: str
    item_type: str  # "VI", "LVClass", "Library", "Folder", "Document", etc.
    url: str | None  # Relative path to file (None for folders)
    children: list[LVProjectItem] = field(default_factory=list)


@dataclass
class LVProject:
    """A LabVIEW project."""
    name: str
    path: Path
    lv_version: str | None = None
    items: list[LVProjectItem] = field(default_factory=list)


@dataclass
class LVLibraryMember:
    """A member (VI, class, or nested library) in a library."""
    name: str
    member_type: str  # "VI", "Class", "Library"
    url: str


# Method scope mapping
SCOPE_MAP = {
    1: "public",
    2: "private",
    3: "protected",
}

# Accessor pattern detection
# Note: Patterns require either:
#   - Space after keyword (Read/Get/Write/Set X.vi) - case-insensitive
#   - Uppercase letter after keyword (getX.vi) - camelCase
# This avoids false positives like setUp -> "setter for Up"
GETTER_PATTERNS = [
    re.compile(r"^Read\s+(.+)\.vi$", re.IGNORECASE),  # "Read FieldName.vi"
    re.compile(r"^Get\s+(.+)\.vi$", re.IGNORECASE),   # "Get FieldName.vi"
    re.compile(r"^get([A-Z].+)\.vi$"),                # "getFieldName.vi" (camelCase)
]

SETTER_PATTERNS = [
    re.compile(r"^Write\s+(.+)\.vi$", re.IGNORECASE),  # "Write FieldName.vi"
    re.compile(r"^Set\s+(.+)\.vi$", re.IGNORECASE),    # "Set FieldName.vi"
    re.compile(r"^set([A-Z].+)\.vi$"),                 # "setFieldName.vi" (camelCase)
]

# Method names that look like accessors but aren't (e.g., test framework methods)
NON_ACCESSOR_METHODS = {
    "setUp", "tearDown", "setUpClass", "tearDownClass",
    "globalSetUp", "globalTearDown",
}


def _detect_accessor(method_name: str) -> tuple[str | None, str | None]:
    """Detect if a method is a getter or setter and extract the field name.

    Returns:
        Tuple of (accessor_type, field_name) or (None, None) if not an accessor.
    """
    # Check if this is a known non-accessor method (e.g., setUp, tearDown)
    base_name = method_name.replace(".vi", "").replace(".VI", "")
    if base_name in NON_ACCESSOR_METHODS:
        return (None, None)

    for pattern in GETTER_PATTERNS:
        match = pattern.match(method_name)
        if match:
            return ("getter", match.group(1))

    for pattern in SETTER_PATTERNS:
        match = pattern.match(method_name)
        if match:
            return ("setter", match.group(1))

    return (None, None)


def _parse_private_data_fields(lvclass_path: Path) -> list[LVPrivateDataField]:
    """Parse private data fields from any extracted VI XML in the class directory.

    Any method VI that uses the class object will have the
    "Cluster of class private data" type definition in its VCTP section.
    Field names and LV type names are extracted from the same XML's VCTP.

    For user-defined field types (classes, typedefs), the LV type name here
    is bare (e.g. "Refnum"). Full qualification happens at the dep_graph
    level where ownership context is known.
    """
    class_dir = lvclass_path.parent

    for xml_path in class_dir.glob("*.xml"):
        if "_BDHb" in xml_path.name or "_FPHb" in xml_path.name:
            continue

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # Find "Cluster of class private data" TypeDesc
            for typedesc in root.iter("TypeDesc"):
                label = typedesc.get("Label", "")
                if "class private data" in label.lower():
                    type_ids = [td.get("TypeID") for td in typedesc.findall("TypeDesc")]
                    fields = _resolve_type_ids(root, type_ids)
                    if fields:
                        return fields

        except ET.ParseError:
            continue

    return []


def _resolve_type_ids(
    root: ET.Element,
    type_ids: list[str | None],
    type_descs: list[ET.Element] | None = None,
    _visited: frozenset[int] | None = None,
) -> list[LVPrivateDataField]:
    """Resolve TypeID references to field definitions from VCTP.

    Gets field name from Label, LV type name from Type attribute,
    and extracts qualified classname from <Item> elements for class fields.
    Recurses into Cluster fields to capture nested sub-fields (needed for
    nMux flat-index resolution across the full cluster hierarchy).
    """
    fields: list[LVPrivateDataField] = []

    if type_descs is None:
        vctp = root.find(".//VCTP/Section")
        if vctp is None:
            return fields
        type_descs = [elem for elem in vctp if elem.tag == "TypeDesc"]

    if _visited is None:
        _visited = frozenset()

    for tid in type_ids:
        if tid is None:
            continue
        try:
            idx = int(tid)
        except ValueError:
            continue
        if idx >= len(type_descs):
            warnings.warn(
                f"TypeID {idx} is out of bounds (VCTP has {len(type_descs)} entries); "
                "skipping field",
                stacklevel=2,
            )
            continue
        if idx in _visited:
            # Circular TypeID reference in malformed VI — skip to avoid infinite loop
            continue

        type_elem = type_descs[idx]

        # Resolve the actual type element (unwrap TypeDef)
        resolved_elem = type_elem
        lv_type = type_elem.get("Type", "")
        if lv_type == "TypeDef":
            nested = type_elem.find("TypeDesc")
            if nested is not None:
                resolved_elem = nested
                lv_type = nested.get("Type", "")

        # Get label: try the resolved element first, then the outer TypeDef wrapper.
        # Every named cluster field must have a label (the field name). If neither
        # element carries one, this is an anonymous structural TypeDesc (e.g. an
        # inline type used for wiring only) and cannot be mapped to a Python field.
        label = resolved_elem.get("Label", "") or type_elem.get("Label", "")
        if not label:
            continue

        # For class refnums, extract qualified classname from <Item> chain
        ref_type = resolved_elem.get("RefType", "")
        if ref_type == "UDClassInst":
            items = type_elem.findall("Item")
            if not items:
                items = resolved_elem.findall("Item")
            if items:
                lv_type = ":".join(it.get("Text", "") for it in items)

        # Recurse into Cluster sub-fields so _flatten_fields works correctly.
        # Guard against malformed circular references by tracking visited indices.
        sub_fields: list[LVPrivateDataField] = []
        if lv_type == "Cluster":
            child_ids = [c.get("TypeID") for c in resolved_elem if c.tag == "TypeDesc"]
            if child_ids:
                sub_fields = _resolve_type_ids(
                    root, child_ids, type_descs, _visited | {idx}
                )

        python_type = _lv_type_to_python(lv_type)
        fields.append(LVPrivateDataField(
            name=label,
            python_type=python_type,
            lv_type_name=lv_type,
            sub_fields=sub_fields,
        ))

    return fields


def _lv_type_to_python(lv_type: str) -> str:
    """Convert LabVIEW type to Python type hint.

    Uses the canonical type mapping from graph_types.py.
    """
    # Additional types not in the core mapping
    extra_types = {
        "Refnum": "Any",  # VI references, notifiers, etc.
        "Array": "list",
        "Cluster": "dict",
    }

    return _LV_TO_PYTHON_TYPE.get(lv_type, extra_types.get(lv_type, "Any"))


def parse_lvclass(lvclass_path: Path | str) -> LVClass:
    """Parse a .lvclass file to extract class structure.

    Args:
        lvclass_path: Path to the .lvclass file

    Returns:
        LVClass with methods, inheritance, and private data info
    """
    lvclass_path = Path(lvclass_path)
    tree = ET.parse(lvclass_path)
    root = tree.getroot()

    class_name = lvclass_path.stem
    parent_class = None
    private_data_ctl = None
    methods: list[LVMethod] = []

    # Try to get parent from Geneology XML property
    for prop in root.findall("Property"):
        if prop.get("Name") == "NI.LVClass.Geneology":
            geneology_str = prop.find("String/Val")
            if geneology_str is not None and geneology_str.text:
                # The geneology contains parent class references
                parent_class = _extract_parent_from_geneology(geneology_str.text)

    # Fallback: try to find parent class in nearby directories
    if parent_class is None:
        parent_class = _find_parent_class_by_path(lvclass_path)

    # Parse items recursively (methods and private data can be in folders)
    _parse_items(root, methods, private_data_ctl)

    # Find private data control
    for item in root.findall(".//Item"):
        if item.get("Type") == "Class Private Data":
            private_data_ctl = item.get("Name")
            break

    # Parse private data fields from _Init.xml
    private_data_fields = _parse_private_data_fields(lvclass_path)

    return LVClass(
        name=class_name,
        path=lvclass_path,
        parent_class=parent_class,
        private_data_ctl=private_data_ctl,
        methods=methods,
        private_data_fields=private_data_fields,
    )


def _parse_items(
    parent_elem: ET.Element,
    methods: list[LVMethod],
    private_data_ctl: str | None,
) -> None:
    """Recursively parse Item elements to find methods.

    Args:
        parent_elem: Parent XML element to search
        methods: List to append methods to
        private_data_ctl: Name of private data control (if found)
    """
    for item in parent_elem.findall("Item"):
        item_name = item.get("Name", "")
        item_type = item.get("Type", "")
        item_url = item.get("URL", "")

        if item_type == "Folder":
            # Recurse into folders (private, protected, etc.)
            _parse_items(item, methods, private_data_ctl)
        elif item_type == "VI" and item_name.endswith(".vi"):
            # Get method properties
            scope_prop = item.find("Property[@Name='NI.ClassItem.MethodScope']")
            static_prop = item.find("Property[@Name='NI.ClassItem.IsStaticMethod']")

            scope_val = 1  # default public
            if scope_prop is not None and scope_prop.text:
                try:
                    scope_val = int(scope_prop.text)
                except ValueError:
                    pass

            is_static = False
            if static_prop is not None and static_prop.text:
                is_static = static_prop.text.lower() == "true"

            # Detect accessor methods
            accessor_type, accessor_field = _detect_accessor(item_name)
            is_accessor = accessor_type is not None

            methods.append(LVMethod(
                name=item_name.replace(".vi", ""),
                vi_path=item_url,
                scope=SCOPE_MAP.get(scope_val, "public"),
                is_static=is_static,
                is_accessor=is_accessor,
                accessor_type=accessor_type,
                accessor_field=accessor_field,
            ))


def _find_parent_class_by_path(lvclass_path: Path) -> str | None:
    """Try to find parent class by looking at the class's _Init.vi file.

    In LabVIEW inheritance, a child class's _Init.vi calls the parent
    class's _Init.vi. We look for this pattern to detect inheritance.

    Args:
        lvclass_path: Path to the .lvclass file

    Returns:
        Parent class name or None
    """
    class_name = lvclass_path.stem
    class_dir = lvclass_path.parent

    # ONLY look at the class's _Init.vi file - this is where parent
    # class _Init calls appear. Looking at other methods would give
    # false positives (e.g., factory methods creating other class objects).
    init_xml_path = class_dir / f"{class_name}_Init.xml"
    if init_xml_path.exists():
        return _extract_parent_from_vi_xml(init_xml_path, class_name)

    return None


def _extract_parent_from_vi_xml(xml_path: Path, current_class: str) -> str | None:
    """Extract parent class name from a VI's XML file.

    Method VIs contain LinkSaveQualName elements that reference their class
    hierarchy. The parent class is identified by finding a call to
    ParentClass_Init.vi in the class's own _Init method.

    Args:
        xml_path: Path to the VI's XML file
        current_class: Name of the current class (to exclude)

    Returns:
        Parent class name or None
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Look for LinkSaveQualName elements that contain both a .lvclass
        # reference AND an _Init.vi call - this indicates parent class
        for link_elem in root.iter("LinkSaveQualName"):
            strings = link_elem.findall("String")
            if len(strings) >= 2:
                class_ref = None
                has_init_call = False

                for string_elem in strings:
                    text = string_elem.text
                    if not text:
                        continue

                    if text.endswith(".lvclass"):
                        class_ref = text[:-8]  # Remove ".lvclass" suffix
                    elif "_Init.vi" in text or "_init.vi" in text.lower():
                        has_init_call = True

                # If we found a class ref with an _Init call, and it's not
                # the current class, this is likely the parent
                if class_ref and has_init_call:
                    # Only accept clean class names
                    if not (
                        class_ref.isidentifier()
                        or all(c.isalnum() or c in "_- " for c in class_ref)
                    ):
                        continue

                    # Skip if it's the current class
                    if class_ref.lower() == current_class.lower():
                        continue

                    return class_ref

    except ET.ParseError:
        pass  # Malformed XML in method VI — not a reliable source of parent info
    except Exception:  # noqa: BLE001 — filesystem / encoding errors; fall back to None
        pass

    return None


def _extract_parent_from_geneology(geneology_data: str) -> str | None:
    """Try to extract parent class name from geneology data.

    The geneology data contains encoded XML with class hierarchy info.
    This is a fallback - the method VI parsing is more reliable.

    Args:
        geneology_data: The encoded geneology string from NI.LVClass.Geneology

    Returns:
        Parent class name or None if not found/parseable
    """
    # The Geneology data is heavily encoded. Skip it and rely on
    # _find_parent_class_by_path which parses method VI XML files.
    # Those contain reliable plain-text class references.
    return None


def parse_lvlib(lvlib_path: Path | str) -> LVLibrary:
    """Parse a .lvlib file to extract library structure.

    Args:
        lvlib_path: Path to the .lvlib file

    Returns:
        LVLibrary with all member VIs and nested items
    """
    lvlib_path = Path(lvlib_path)
    tree = ET.parse(lvlib_path)
    root = tree.getroot()

    lib_name = lvlib_path.stem
    version = None
    members = []

    # Get version from properties
    for prop in root.findall("Property"):
        if prop.get("Name") == "NI.Lib.Version":
            version = prop.text

    # Parse items
    for item in root.findall("Item"):
        item_name = item.get("Name", "")
        item_type = item.get("Type", "")
        item_url = item.get("URL", "")

        members.append(LVLibraryMember(
            name=item_name,
            member_type=item_type,
            url=item_url,
        ))

    return LVLibrary(
        name=lib_name,
        path=lvlib_path,
        version=version,
        members=members,
    )


def parse_lvproj(lvproj_path: Path | str) -> LVProject:
    """Parse a .lvproj file to extract project structure.

    Args:
        lvproj_path: Path to the .lvproj file

    Returns:
        LVProject with all items (VIs, classes, libraries) included in the project
    """
    lvproj_path = Path(lvproj_path)
    tree = ET.parse(lvproj_path)
    root = tree.getroot()

    proj_name = lvproj_path.stem
    lv_version = root.get("LVVersion")

    def parse_item(item_elem: ET.Element) -> LVProjectItem:
        """Recursively parse an Item element."""
        name = item_elem.get("Name", "")
        item_type = item_elem.get("Type", "")
        url = item_elem.get("URL")

        children = []
        for child in item_elem.findall("Item"):
            children.append(parse_item(child))

        return LVProjectItem(
            name=name,
            item_type=item_type,
            url=url,
            children=children,
        )

    items = []
    for item in root.findall("Item"):
        items.append(parse_item(item))

    return LVProject(
        name=proj_name,
        path=lvproj_path,
        lv_version=lv_version,
        items=items,
    )


def get_project_vis(project: LVProject) -> list[tuple[str, Path]]:
    """Extract all VI paths from a parsed project.

    Args:
        project: Parsed LVProject

    Returns:
        List of (vi_name, absolute_path) tuples for all VIs in the project
    """
    proj_dir = project.path.parent
    vis: list[tuple[str, Path]] = []

    def collect_vis(items: list[LVProjectItem]) -> None:
        for item in items:
            if item.item_type == "VI" and item.url:
                vi_path = proj_dir / item.url
                vis.append((item.name, vi_path))
            # Recurse into children (folders, classes with nested VIs, etc.)
            collect_vis(item.children)

    collect_vis(project.items)
    return vis


def get_project_classes(project: LVProject) -> list[tuple[str, Path]]:
    """Extract all lvclass paths from a parsed project.

    Args:
        project: Parsed LVProject

    Returns:
        List of (class_name, absolute_path) tuples for all classes in the project
    """
    proj_dir = project.path.parent
    classes: list[tuple[str, Path]] = []

    def collect_classes(items: list[LVProjectItem]) -> None:
        for item in items:
            if item.item_type == "LVClass" and item.url:
                class_path = proj_dir / item.url
                classes.append((item.name, class_path))
            collect_classes(item.children)

    collect_classes(project.items)
    return classes


def get_project_libraries(project: LVProject) -> list[tuple[str, Path]]:
    """Extract all lvlib paths from a parsed project.

    Args:
        project: Parsed LVProject

    Returns:
        List of (lib_name, absolute_path) tuples for all libraries in the project
    """
    proj_dir = project.path.parent
    libs: list[tuple[str, Path]] = []

    def collect_libs(items: list[LVProjectItem]) -> None:
        for item in items:
            if item.item_type == "Library" and item.url:
                lib_path = proj_dir / item.url
                libs.append((item.name, lib_path))
            collect_libs(item.children)

    collect_libs(project.items)
    return libs


def discover_project_structure(root_path: Path | str) -> dict[str, Any]:
    """Discover LabVIEW project structure from a directory.

    Scans for .lvlib, .lvclass, and .vi files to build a complete
    picture of the project structure.

    Args:
        root_path: Root directory to scan

    Returns:
        Dict with libraries, classes, and standalone VIs
    """
    root_path = Path(root_path)

    structure: dict[str, list[Any]] = {
        "libraries": [],
        "classes": [],
        "standalone_vis": [],
    }

    # Find all libraries
    for lvlib_path in root_path.rglob("*.lvlib"):
        lib = parse_lvlib(lvlib_path)
        structure["libraries"].append({
            "name": lib.name,
            "path": str(lvlib_path.relative_to(root_path)),
            "version": lib.version,
            "members": [
                {"name": m.name, "type": m.member_type, "url": m.url}
                for m in lib.members
            ],
        })

    # Find all classes
    for lvclass_path in root_path.rglob("*.lvclass"):
        cls = parse_lvclass(lvclass_path)
        structure["classes"].append({
            "name": cls.name,
            "path": str(lvclass_path.relative_to(root_path)),
            "parent_class": cls.parent_class,
            "private_data": cls.private_data_ctl,
            "methods": [
                {
                    "name": m.name,
                    "scope": m.scope,
                    "is_static": m.is_static,
                    "vi_path": m.vi_path,
                }
                for m in cls.methods
            ],
        })

    # Find standalone VIs (not in class directories)
    class_dirs = {Path(cls["path"]).parent for cls in structure["classes"]}

    for vi_path in root_path.rglob("*.vi"):
        rel_path = vi_path.relative_to(root_path)
        # Check if VI is standalone (not in a class or referenced by library)
        if rel_path.parent not in class_dirs:
            structure["standalone_vis"].append(str(rel_path))

    return structure


def generate_python_structure_plan(structure: dict[str, Any]) -> str:
    """Generate a plan for Python module/package structure.

    Args:
        structure: Project structure from discover_project_structure()

    Returns:
        Human-readable plan for Python structure mapping
    """
    lines = ["# Python Structure Plan", ""]

    # Plan for libraries -> Python modules
    if structure["libraries"]:
        lines.append("## Libraries -> Python Modules")
        for lib in structure["libraries"]:
            module_name = _to_python_identifier(lib["name"])
            lines.append(f"\n### {lib['name']} -> {module_name}.py")
            lines.append(f"Path: {lib['path']}")
            if lib["members"]:
                lines.append("Functions:")
                for member in lib["members"]:
                    if member["type"] == "VI":
                        name = member["name"].replace(".vi", "")
                        func_name = _to_python_identifier(name)
                        lines.append(f"  - {func_name}()")
        lines.append("")

    # Plan for classes -> Python classes
    if structure["classes"]:
        lines.append("## Classes -> Python Classes")
        for cls in structure["classes"]:
            class_name = _to_python_class_name(cls["name"])
            parent = cls.get("parent_class")
            parent_str = f"({_to_python_class_name(parent)})" if parent else ""

            lines.append(f"\n### {cls['name']} -> class {class_name}{parent_str}:")
            lines.append(f"Path: {cls['path']}")

            if cls["private_data"]:
                lines.append(f"Instance data: {cls['private_data']}")

            if cls["methods"]:
                lines.append("Methods:")
                for method in cls["methods"]:
                    decorator = "@staticmethod " if method["is_static"] else ""
                    scope = method["scope"]
                    if scope == "private":
                        visibility = "_"
                    elif scope == "protected":
                        visibility = "__"
                    else:
                        visibility = ""
                    method_name = visibility + _to_python_identifier(method["name"])
                    lines.append(f"  - {decorator}{method_name}()")
        lines.append("")

    return "\n".join(lines)


def _to_python_identifier(name: str) -> str:
    """Convert a LabVIEW name to a valid Python identifier."""
    # Replace spaces and special chars with underscores
    result = name.lower()
    result = result.replace(" ", "_")
    result = result.replace("-", "_")
    result = result.replace(".", "_")
    result = result.replace("(", "")
    result = result.replace(")", "")
    # Remove leading numbers
    while result and result[0].isdigit():
        result = result[1:]
    # Ensure not empty
    if not result:
        result = "item"
    return result


def _to_python_class_name(name: str) -> str:
    """Convert a LabVIEW class name to Python PascalCase."""
    # Remove spaces and special chars, capitalize words
    words = name.replace("-", " ").replace("_", " ").replace(".", " ").split()
    return "".join(word.capitalize() for word in words)
