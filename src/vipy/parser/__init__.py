"""Parser package - parse LabVIEW VI XML into structured representation.

This package provides backward-compatible exports from the original parser.py.
"""

# Re-export all public APIs for backward compatibility
from ..graph_types import Tunnel
from .block_diagram import parse_block_diagram
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
    FPTerminal,
    LoopStructure,
    Node,
    ResolvedTypeDefValue,
    SubVIPathRef,
    TerminalInfo,
    TypeDefRef,
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

# Backward compatibility alias (after all imports)
TunnelMapping = Tunnel

__all__ = [
    # Models
    "BlockDiagram",
    "ConnectorPane",
    "ConnectorPaneSlot",
    "Constant",
    "DefaultValue",
    "FPTerminal",
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
    # Block diagram
    "parse_block_diagram",
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
