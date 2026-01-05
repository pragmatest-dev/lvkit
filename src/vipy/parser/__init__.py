"""Parser package - parse LabVIEW VI XML into structured representation.

Main entry point: parse_vi() returns ParsedVI with all components.
"""

# Re-export all public APIs
from ..graph_types import Tunnel
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
    BlockDiagram,
    ConnectorPane,
    ConnectorPaneSlot,
    Constant,
    DefaultValue,
    FPControl,
    FPTerminal,
    FrontPanel,
    LoopStructure,
    Node,
    ParsedVI,
    ResolvedTypeDefValue,
    SubVIPathRef,
    TerminalInfo,
    TypeDefRef,
    VIMetadata,
    Wire,
    WiringRule,
)
from .types import (
    load_enum_reference,
    parse_dfds,
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
    "VIMetadata",
    # Models
    "BlockDiagram",
    "ConnectorPane",
    "ConnectorPaneSlot",
    "Constant",
    "DefaultValue",
    "FPControl",
    "FPTerminal",
    "FrontPanel",
    "LoopStructure",
    "Node",
    "ResolvedTypeDefValue",
    "SubVIPathRef",
    "TerminalInfo",
    "Tunnel",
    "TunnelMapping",  # Backward compat alias for Tunnel
    "TypeDefRef",
    "Wire",
    "WiringRule",
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
