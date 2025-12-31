"""Constants used across the vipy codebase."""

from __future__ import annotations

# === XML Element Class Names ===
# Node types on the block diagram
NODE_CLASS_PRIM = "prim"
NODE_CLASS_SUBVI = "iUse"
NODE_CLASS_POLY_SUBVI = "polyIUse"  # Polymorphic SubVI call
NODE_CLASS_WHILE_LOOP = "whileLoop"
NODE_CLASS_FOR_LOOP = "forLoop"
NODE_CLASS_SELECT = "select"
NODE_CLASS_CASE_STRUCT = "caseStruct"
NODE_CLASS_SEQ = "seq"
NODE_CLASS_EVENT_STRUCT = "eventStruct"
NODE_CLASS_PROP_NODE = "propNode"

# All node classes that contain operations
OPERATION_NODE_CLASSES = (
    NODE_CLASS_PRIM,
    NODE_CLASS_SUBVI,
    NODE_CLASS_POLY_SUBVI,
    NODE_CLASS_WHILE_LOOP,
    NODE_CLASS_FOR_LOOP,
    NODE_CLASS_SELECT,
    NODE_CLASS_CASE_STRUCT,
    NODE_CLASS_SEQ,
    NODE_CLASS_EVENT_STRUCT,
    NODE_CLASS_PROP_NODE,
)

# Loop node classes
LOOP_NODE_CLASSES = (NODE_CLASS_WHILE_LOOP, NODE_CLASS_FOR_LOOP)

# Conditional/case node classes
CONDITIONAL_NODE_CLASSES = (NODE_CLASS_SELECT, NODE_CLASS_CASE_STRUCT)

# Tunnel/shift register DCO classes (inside loop terminal dco elements)
TUNNEL_CLASS_LEFT_SR = "lSR"  # Left shift register (input, persists across iterations)
TUNNEL_CLASS_RIGHT_SR = "rSR"  # Right shift register (output, persists across iterations)
TUNNEL_CLASS_LOOP_TUNNEL = "lpTun"  # Loop tunnel (simple pass-through)
TUNNEL_CLASS_LMAX = "lMax"  # Accumulator/max output

# Shift register node (contains inner tunnel terminals)
NODE_CLASS_SHIFT_REG = "sRN"  # Shift register node - holds inner ends of tunnels

# All tunnel types that create outer↔inner terminal mappings
TUNNEL_DCO_CLASSES = (
    TUNNEL_CLASS_LEFT_SR,
    TUNNEL_CLASS_RIGHT_SR,
    TUNNEL_CLASS_LOOP_TUNNEL,
    TUNNEL_CLASS_LMAX,
)

# Node classes that have terminals (for terminal extraction)
TERMINAL_CONTAINER_CLASSES = OPERATION_NODE_CLASSES + (NODE_CLASS_SHIFT_REG,)

# Terminal-related classes
TERMINAL_CLASS = "term"
FP_TERMINAL_CLASS = "fPTerm"
CONSTANT_DCO_CLASS = "bDConstDCO"
FP_DCO_CLASS = "fPDCO"
MULTI_LABEL_CLASS = "multiLabel"

# === Terminal Flags ===
# When objFlags has this bit set, the terminal receives data (input to the node)
TERMINAL_INPUT_FLAG = 0x8000  # 32768 - indicates terminal RECEIVES data (input to node)


# === File Extensions and Patterns ===
VI_EXTENSION = ".vi"
LVLIB_EXTENSION = ".lvlib"
LVCLASS_EXTENSION = ".lvclass"
LVPROJ_EXTENSION = ".lvproj"

# XML file suffixes from pylabview
BD_XML_SUFFIX = "_BDHb.xml"  # Block diagram
FP_XML_SUFFIX = "_FPHb.xml"  # Front panel
MAIN_XML_SUFFIX = ".xml"  # Main VI metadata


# === Known LabVIEW System Directory Types ===
# Format: type_id -> (name, windows_env, unix_path)
SYSTEM_DIR_TYPES: dict[int, tuple[str, str, str]] = {
    0: ("User Home", "USERPROFILE", "HOME"),
    1: ("User Desktop", "USERPROFILE + '/Desktop'", "HOME + '/Desktop'"),
    2: ("User Documents", "USERPROFILE + '/Documents'", "HOME + '/Documents'"),
    3: ("User Application Data", "APPDATA", "HOME + '/.config'"),
    4: ("User Preferences", "APPDATA", "HOME + '/.config'"),
    5: ("User Temporary", "TEMP", "/tmp"),
    6: ("Public Documents", "PUBLIC + '/Documents'", "/usr/share"),
    7: ("Public Application Data", "PROGRAMDATA", "/usr/local/share"),
    8: ("Public Preferences", "PROGRAMDATA", "/etc"),
    9: ("System Core Libraries", "SYSTEMROOT + '/System32'", "/usr/lib"),
    10: ("System Installed Libraries", "PROGRAMFILES", "/usr/local/lib"),
    11: ("Application Files", "PROGRAMFILES", "/opt"),
    12: ("Boot Volume Root", "SYSTEMDRIVE", "/"),
}


# === Helper Functions ===

def derive_fp_xml_path(bd_xml_path: str) -> str:
    """Derive front panel XML path from block diagram XML path."""
    return bd_xml_path.replace(BD_XML_SUFFIX, FP_XML_SUFFIX)


def derive_main_xml_path(bd_xml_path: str) -> str:
    """Derive main XML path from block diagram XML path."""
    return bd_xml_path.replace(BD_XML_SUFFIX, MAIN_XML_SUFFIX)


def derive_vi_name(bd_xml_path: str) -> str:
    """Derive VI name from block diagram XML filename."""
    import os
    basename = os.path.basename(bd_xml_path)
    return basename.replace(BD_XML_SUFFIX, "")
