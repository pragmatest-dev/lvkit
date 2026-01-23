"""Type resolution and mapping for parser module.

This module re-exports all type-related functions for backward compatibility.
The actual implementations are split across:
- type_mapping.py: Type parsing from XML (parse_type_map_rich, parse_vctp_types)
- type_resolution.py: Type resolution and lookup (resolve_type, parse_type_chain)
- defaults.py: Default value parsing (parse_dfds)
"""

from __future__ import annotations

# Re-export from type_mapping
from .type_mapping import (
    parse_type_map_rich,
    parse_vctp_types,
)

# Re-export from type_resolution
from .type_resolution import (
    load_enum_reference,
    parse_type_chain,
    parse_type_map,
    parse_typedef_refs,
    resolve_type,
    resolve_type_rich,
    resolve_type_to_typedef,
    resolve_typedef_value,
)

# Re-export from defaults
from .defaults import (
    parse_dfds,
)

__all__ = [
    # Type mapping
    "parse_type_map_rich",
    "parse_vctp_types",
    # Type resolution
    "parse_type_map",
    "resolve_type",
    "resolve_type_rich",
    "parse_type_chain",
    "resolve_type_to_typedef",
    "load_enum_reference",
    "resolve_typedef_value",
    "parse_typedef_refs",
    # Defaults
    "parse_dfds",
]
