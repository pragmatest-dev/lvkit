"""Parser package - parse LabVIEW VI XML into structured representation.

Main entry point: parse_vi() returns ParsedVI with all components.
"""

# Re-export all public APIs
from ..models import Tunnel
from .defaults import parse_dfds
from .front_panel import (
    extract_fp_terminals,
    parse_connector_pane,
    parse_connector_pane_types,
)
from .metadata import (
    parse_polymorphic_info,
    parse_subvi_paths,
    parse_vi_metadata,
)
from .models import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedConnectorPaneSlot,
    ParsedConstant,
    ParsedDefaultValue,
    ParsedFPControl,
    ParsedFPTerminal,
    ParsedFrontPanel,
    ParsedLoopStructure,
    ParsedNode,
    ParsedResolvedTypeDefValue,
    ParsedSubVIPathRef,
    ParsedTerminalInfo,
    ParsedTypeDefRef,
    ParsedVI,
    ParsedVIMetadata,
    ParsedWire,
    ParsedWiringRule,
)
from .type_resolution import (
    load_enum_reference,
    parse_type_chain,
    parse_type_map,
    parse_typedef_refs,
    resolve_type,
    resolve_type_to_typedef,
    resolve_typedef_value,
)
from .vi import parse_vi

# Backward compatibility alias
TunnelMapping = Tunnel

__all__ = [
    # Main entry point
    "parse_vi",
    "ParsedVI",
    "ParsedVIMetadata",
    # Models
    "ParsedBlockDiagram",
    "ParsedConnectorPane",
    "ParsedConnectorPaneSlot",
    "ParsedConstant",
    "ParsedDefaultValue",
    "ParsedFPControl",
    "ParsedFPTerminal",
    "ParsedFrontPanel",
    "ParsedLoopStructure",
    "ParsedNode",
    "ParsedResolvedTypeDefValue",
    "ParsedSubVIPathRef",
    "ParsedTerminalInfo",
    "Tunnel",
    "TunnelMapping",  # Backward compat alias for Tunnel
    "ParsedTypeDefRef",
    "ParsedWire",
    "ParsedWiringRule",
    # Front panel
    "extract_fp_terminals",
    "parse_connector_pane",
    "parse_connector_pane_types",
    # Metadata
    "parse_polymorphic_info",
    "parse_subvi_paths",
    "parse_vi_metadata",
    # Types
    "load_enum_reference",
    "parse_dfds",
    "parse_type_chain",
    "parse_type_map",
    "parse_typedef_refs",
    "resolve_type",
    "resolve_type_to_typedef",
    "resolve_typedef_value",
]
