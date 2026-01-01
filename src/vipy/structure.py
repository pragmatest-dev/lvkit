"""Parse LabVIEW library and class files for structural mapping."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LVMethod:
    """A method in a LabVIEW class."""
    name: str
    vi_path: str
    scope: str  # "public", "private", "protected"
    is_static: bool = False


@dataclass
class LVClass:
    """A LabVIEW class."""
    name: str
    path: Path
    parent_class: str | None = None
    private_data_ctl: str | None = None
    methods: list[LVMethod] = field(default_factory=list)


@dataclass
class LVLibrary:
    """A LabVIEW library."""
    name: str
    path: Path
    version: str | None = None
    members: list[LVLibraryMember] = field(default_factory=list)


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
    methods = []

    # Parse properties for parent class info
    for prop in root.findall("Property"):
        prop_name = prop.get("Name", "")
        if prop_name == "NI.LVClass.ParentClassLinkInfo":
            # Parent info is in binary, but we can try to extract from Geneology
            pass

    # Try to get parent from Geneology XML property
    for prop in root.findall("Property"):
        if prop.get("Name") == "NI.LVClass.Geneology":
            geneology_str = prop.find("String/Val")
            if geneology_str is not None and geneology_str.text:
                # The geneology contains parent class references
                # Look for class names in the encoded data
                parent_class = _extract_parent_from_geneology(geneology_str.text)

    # Parse items (methods and private data)
    for item in root.findall("Item"):
        item_name = item.get("Name", "")
        item_type = item.get("Type", "")
        item_url = item.get("URL", "")

        if item_type == "Class Private Data":
            private_data_ctl = item_name
        elif item_type == "VI":
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

            methods.append(LVMethod(
                name=item_name.replace(".vi", ""),
                vi_path=item_url,
                scope=SCOPE_MAP.get(scope_val, "public"),
                is_static=is_static,
            ))

    return LVClass(
        name=class_name,
        path=lvclass_path,
        parent_class=parent_class,
        private_data_ctl=private_data_ctl,
        methods=methods,
    )


def _extract_parent_from_geneology(geneology_data: str) -> str | None:
    """Try to extract parent class name from geneology data.

    The geneology data is a complex encoded format. We look for
    common patterns that indicate class hierarchy.
    """
    # For now, return None - full parsing would require
    # understanding LabVIEW's binary encoding
    # The parent class reference is typically in ParentClassLinkInfo
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

    structure = {
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
                        func_name = _to_python_identifier(member["name"].replace(".vi", ""))
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
                    decorator = "@staticmethod" if method["is_static"] else ""
                    visibility = "_" if method["scope"] == "private" else "__" if method["scope"] == "protected" else ""
                    method_name = visibility + _to_python_identifier(method["name"])
                    lines.append(f"  - {decorator + ' ' if decorator else ''}{method_name}()")
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
