"""Generate Python default values for LabVIEW types.

Used in code generation when we need a default value, such as:
- Exception handlers (branch isolation)
- Unwired optional inputs
- Error cluster defaults
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .vilib_resolver import derive_python_name

if TYPE_CHECKING:
    from .graph_types import LVType

# Map LabVIEW numeric type names to Python defaults
_NUMERIC_DEFAULTS: dict[str, str] = {
    "NumInt8": "0",
    "NumInt16": "0",
    "NumInt32": "0",
    "NumInt64": "0",
    "NumUInt8": "0",
    "NumUInt16": "0",
    "NumUInt32": "0",
    "NumUInt64": "0",
    "NumFloat32": "0.0",
    "NumFloat64": "0.0",
    "NumFloatExt": "0.0",
    "NumComplex64": "complex(0, 0)",
    "NumComplex128": "complex(0, 0)",
    "NumComplexExt": "complex(0, 0)",
}

# Known error cluster field defaults
ERROR_CLUSTER_DEFAULT = "{'status': False, 'code': 0, 'source': ''}"


def get_default_for_type(lv_type: LVType | None) -> str:
    """Get Python default value expression for a LabVIEW type.

    Args:
        lv_type: The LVType to get a default for, or None

    Returns:
        Python expression string for the default value
    """
    if lv_type is None:
        return "None"

    kind = lv_type.kind
    underlying = lv_type.underlying_type or ""

    # Check for error cluster (by typedef name or field structure)
    if _is_error_cluster(lv_type):
        return ERROR_CLUSTER_DEFAULT

    if kind == "primitive":
        return _get_primitive_default(underlying)

    elif kind == "array":
        # Empty array, but we could generate typed empty list
        # e.g., list[int]() for Array[NumInt32]
        if lv_type.element_type:
            # For typed arrays, still return [] but we have the element info
            # Could be used for type hints: list[element_type]
            pass
        return "[]"

    elif kind == "cluster":
        # For non-error clusters, generate dict with field defaults
        if lv_type.fields:
            field_defaults = []
            for field in lv_type.fields:
                field_default = get_default_for_type(field.type)
                field_defaults.append(f"'{field.name}': {field_default}")
            return "{" + ", ".join(field_defaults) + "}"
        return "{}"

    elif kind in ("enum", "ring"):
        # Enums default to first value (usually index 0)
        if lv_type.values:
            # Find the enum member with value 0 (default)
            for member_name, enum_val in lv_type.values.items():
                if enum_val.value == 0:
                    if lv_type.typedef_name:
                        # Use qualified enum reference
                        class_name = derive_python_name(lv_type.typedef_name)
                        return f"{class_name}.{member_name}"
                    return f"0  # {member_name}"
            # If no value 0, use first member
            first_member = next(iter(lv_type.values.keys()), None)
            if first_member and lv_type.typedef_name:
                class_name = derive_python_name(lv_type.typedef_name)
                return f"{class_name}.{first_member}"
        return "0"

    elif kind == "typedef_ref":
        # TypeDef reference - try to resolve or return None
        return "None"

    else:
        return "None"


def _get_primitive_default(type_name: str) -> str:
    """Get default for a primitive type."""
    # Numeric types
    if type_name in _NUMERIC_DEFAULTS:
        return _NUMERIC_DEFAULTS[type_name]

    # String types
    if type_name in ("String", "SubString"):
        return "''"

    # Boolean
    if type_name == "Boolean":
        return "False"

    # Path
    if type_name == "Path":
        return "Path()"

    # Refnum types
    if type_name in ("Refnum", "TypedRefNum", "VIRefNum", "LVObjectRefNum"):
        return "None"

    # Void/special
    if type_name in ("Void", "VoidBlock"):
        return "None"

    # LVVariant
    if type_name == "LVVariant":
        return "None"

    # Picture
    if type_name == "Picture":
        return "b''"

    # Timestamp/time types
    if type_name in ("AbsTime", "Time128"):
        return "0.0"

    # Unknown - default to None
    return "None"


def _is_error_cluster(lv_type: LVType) -> bool:
    """Check if a type is an error cluster.

    Detects error clusters by:
    1. TypeDef name contains "error" (case-insensitive)
    2. Cluster with status/code/source fields
    """
    if lv_type.kind not in ("cluster", "typedef_ref"):
        return False

    # Check typedef name
    typedef_name = lv_type.typedef_name or ""
    if "error" in typedef_name.lower():
        return True

    # Check field names for error cluster pattern
    if lv_type.fields:
        field_names = {f.name.lower() for f in lv_type.fields}
        error_fields = {"status", "code", "source"}
        if error_fields <= field_names:
            return True

    return False


def _is_class_refnum(lv_type: LVType, class_name: str) -> bool:
    """Check if type is a class refnum matching the given class.

    Args:
        lv_type: The type to check
        class_name: The class name to match (e.g., "TestCase" or "TestCase.lvclass")

    Returns:
        True if this is a UDClassInst refnum for the specified class
    """
    if lv_type.underlying_type != "Refnum":
        return False
    if lv_type.ref_type != "UDClassInst":
        return False
    if not lv_type.classname:
        return False

    # Normalize for comparison (remove .lvclass suffix, case-insensitive)
    class_base = class_name.lower().replace(".lvclass", "")
    refnum_class = lv_type.classname.lower().replace(".lvclass", "")

    return class_base == refnum_class


def get_default_for_control_type(control_type: str) -> str:
    """Get default for a front panel control type (stdXxx).

    Args:
        control_type: Control class like "stdNum", "stdString", "stdClust"

    Returns:
        Python expression string for the default value
    """
    if control_type in ("stdNum", "stdNumeric"):
        return "0"
    elif control_type == "stdString":
        return "''"
    elif control_type == "stdBool":
        return "False"
    elif control_type == "stdPath":
        return "Path()"
    elif control_type == "stdClust":
        return "{}"  # Generic cluster - caller should check for error cluster
    elif control_type == "stdArray":
        return "[]"
    elif control_type in ("stdRing", "stdEnum"):
        return "0"
    elif control_type == "stdRefNum":
        return "None"
    else:
        return "None"
