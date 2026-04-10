"""Type system for representing LabVIEW types in Python.

Provides a structured representation of types that can be:
- Extracted from VI front panels and typedefs
- Stored in the graph
- Rendered to Python type annotations
- Serialized to JSON for caching
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TypeInfo:
    """Base class for type information."""

    kind: str  # "primitive", "array", "cluster", "enum", "typedef", "variant"

    def to_python(self) -> str:
        """Render as Python type annotation string."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        raise NotImplementedError

    @staticmethod
    def from_dict(data: dict[str, Any]) -> TypeInfo:
        """Deserialize from dict."""
        kind = data.get("kind", "primitive")
        if kind == "primitive":
            return PrimitiveType.from_dict(data)
        elif kind == "array":
            return ArrayType.from_dict(data)
        elif kind == "cluster":
            return ClusterType.from_dict(data)
        elif kind == "enum":
            return EnumType.from_dict(data)
        elif kind == "typedef":
            return TypedefRef.from_dict(data)
        elif kind == "variant":
            return VariantType.from_dict(data)
        else:
            return PrimitiveType(python_type="Any")


@dataclass
class PrimitiveType(TypeInfo):
    """Primitive/scalar type."""

    kind: str = "primitive"
    python_type: str = "Any"  # "int", "float", "str", "bool", "Path", "Any"

    def to_python(self) -> str:
        return self.python_type

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "python_type": self.python_type}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> PrimitiveType:
        return PrimitiveType(python_type=data.get("python_type", "Any"))


@dataclass
class ArrayType(TypeInfo):
    """Array/list type with element type."""

    kind: str = "array"
    element_type: TypeInfo = field(default_factory=lambda: PrimitiveType())
    dimensions: int = 1  # 1D, 2D, etc.

    def to_python(self) -> str:
        inner = self.element_type.to_python()
        result = f"list[{inner}]"
        # Nested lists for multi-dimensional
        for _ in range(self.dimensions - 1):
            result = f"list[{result}]"
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "element_type": self.element_type.to_dict(),
            "dimensions": self.dimensions,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ArrayType:
        element_data = data.get("element_type", {})
        return ArrayType(
            element_type=TypeInfo.from_dict(element_data),
            dimensions=data.get("dimensions", 1),
        )


@dataclass
class ClusterField:
    """A field within a cluster type."""

    name: str
    type_info: TypeInfo
    default_value: str | None = None


@dataclass
class ClusterType(TypeInfo):
    """Cluster/struct type with named fields."""

    kind: str = "cluster"
    name: str = "Cluster"  # Python class name
    fields: list[ClusterField] = field(default_factory=list)

    def to_python(self) -> str:
        # Reference the generated NamedTuple/dataclass
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "fields": [
                {
                    "name": f.name,
                    "type_info": f.type_info.to_dict(),
                    "default_value": f.default_value,
                }
                for f in self.fields
            ],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ClusterType:
        fields = []
        for f in data.get("fields", []):
            fields.append(
                ClusterField(
                    name=f["name"],
                    type_info=TypeInfo.from_dict(f.get("type_info", {})),
                    default_value=f.get("default_value"),
                )
            )
        return ClusterType(name=data.get("name", "Cluster"), fields=fields)

    def generate_class(self) -> str:
        """Generate Python NamedTuple class definition."""
        lines = [f"class {self.name}(NamedTuple):"]
        if not self.fields:
            lines.append("    pass")
        else:
            for f in self.fields:
                type_str = f.type_info.to_python()
                lines.append(f"    {f.name}: {type_str}")
        return "\n".join(lines)


@dataclass
class EnumType(TypeInfo):
    """Enumeration type with named values."""

    kind: str = "enum"
    name: str = "MyEnum"  # Python class name
    values: dict[str, int] = field(default_factory=dict)  # name -> int value

    def to_python(self) -> str:
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "values": self.values,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EnumType:
        return EnumType(
            name=data.get("name", "MyEnum"),
            values=data.get("values", {}),
        )

    def generate_class(self) -> str:
        """Generate Python IntEnum class definition."""
        lines = [f"class {self.name}(IntEnum):"]
        if not self.values:
            lines.append("    pass")
        else:
            for name, value in sorted(self.values.items(), key=lambda x: x[1]):
                lines.append(f"    {name} = {value}")
        return "\n".join(lines)


@dataclass
class TypedefRef(TypeInfo):
    """Reference to a named typedef (resolved elsewhere)."""

    kind: str = "typedef"
    name: str = ""  # Typedef name to look up
    source: str | None = None  # Path to .ctl file if known

    def to_python(self) -> str:
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "source": self.source,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> TypedefRef:
        return TypedefRef(
            name=data.get("name", ""),
            source=data.get("source"),
        )


@dataclass
class VariantType(TypeInfo):
    """Variant type (can hold any type at runtime)."""

    kind: str = "variant"

    def to_python(self) -> str:
        return "Any"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> VariantType:
        return VariantType()


# Common type instances for convenience
INT = PrimitiveType(python_type="int")
FLOAT = PrimitiveType(python_type="float")
STR = PrimitiveType(python_type="str")
BOOL = PrimitiveType(python_type="bool")
PATH = PrimitiveType(python_type="Path")
ANY = PrimitiveType(python_type="Any")


def from_labview_type(lv_type: str, control_type: str | None = None) -> TypeInfo:
    """Convert LabVIEW type string to TypeInfo.

    Args:
        lv_type: Type string from graph (e.g., "Path", "NumInt32", "Array")
        control_type: Control type from front panel (e.g., "stdPath", "stdClust")

    Returns:
        Appropriate TypeInfo instance
    """
    # Map from LabVIEW type names
    type_map = {
        "Path": PATH,
        "path": PATH,
        "String": STR,
        "string": STR,
        "str": STR,
        "Boolean": BOOL,
        "bool": BOOL,
        "boolean": BOOL,
        "NumInt32": INT,
        "NumInt16": INT,
        "NumInt8": INT,
        "NumUInt32": INT,
        "NumUInt16": INT,
        "NumUInt8": INT,
        "int": INT,
        "NumFloat64": FLOAT,
        "NumFloat32": FLOAT,
        "float": FLOAT,
        "Void": PrimitiveType(python_type="None"),
        "Variant": VariantType(),
    }

    if lv_type in type_map:
        return type_map[lv_type]

    # Check control type
    control_map = {
        "stdPath": PATH,
        "stdString": STR,
        "stdBool": BOOL,
        "stdNum": FLOAT,
        "stdDBL": FLOAT,
        "stdI32": INT,
        "stdI16": INT,
        "stdU32": INT,
    }

    if control_type in control_map:
        return control_map[control_type]

    # Array types (stdArray = control, indArr = indicator array)
    if lv_type == "Array" or control_type in ("stdArray", "indArr"):
        return ArrayType(element_type=ANY)

    # Cluster types
    if lv_type == "Cluster" or control_type in ("stdClust", "stdCluster"):
        return ClusterType(name="Cluster")

    return ANY
