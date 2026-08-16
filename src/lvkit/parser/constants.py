"""Parser-internal constants for LabVIEW XML element class names and flags."""

from __future__ import annotations

import os

# === XML Element Class Names ===
# Node types on the block diagram
NODE_CLASS_PRIM = "prim"
NODE_CLASS_SUBVI = "iUse"
NODE_CLASS_POLY_SUBVI = "polyIUse"  # Polymorphic SubVI call
NODE_CLASS_DYN_SUBVI = "dynIUse"  # Dynamic dispatch VI call (class method)
NODE_CLASS_CALL_PARENT = "callParentDynIUse"  # Call Parent Method (super() call)
NODE_CLASS_CALL_BY_REF = "callByRefNode"  # Call By Reference node
NODE_CLASS_WHILE_LOOP = "whileLoop"
NODE_CLASS_FOR_LOOP = "forLoop"
NODE_CLASS_SELECT = "select"
NODE_CLASS_CASE_STRUCT = "caseStruct"
NODE_CLASS_SEQ = "seq"
NODE_CLASS_SEQUENCE = "sequence"  # Older LV versions use "sequence" instead of "seq"
NODE_CLASS_EVENT_STRUCT = "eventStruct"
# An Event Structure frame's data/filter node (inner-left "Event Data Node" /
# inner-right "Event Filter Node" — same heap class for both, see
# parser/nodes/event.py). Structurally identical to nMux (dcoAgg + named
# dcoList fields) — registered as an operation node (not a tunnel) so it
# parses via _EventDataNodeHandler instead of falling into the generic
# unknown-node capture (which drew it as an oversized, mis-positioned box —
# task #75).
NODE_CLASS_EVENT_DATA_NODE = "eventDataNode"
# Feedback Node master/read side (owns leftFeedback output + initFeedback init)
NODE_CLASS_FEEDBACK_MASTER = "hiddenFBNode"
# Feedback Node write side (owns rightFeedback input; linked to the master)
NODE_CLASS_FEEDBACK_SLAVE = "slaveFBInputNode"
NODE_CLASS_PROP_NODE = "propNode"
NODE_CLASS_INVOKE_NODE = "invokeNode"
NODE_CLASS_CPD_ARITH = "cpdArith"  # Compound arithmetic (e.g., Or of multiple booleans)
NODE_CLASS_ARRAY_BUILD = "aBuild"  # Array builder node
NODE_CLASS_ARRAY_INIT = "aInit"  # Initialize Array (element + sizes -> array)
NODE_CLASS_FLAT_SEQ = "flatSequence"  # Flat sequence structure
NODE_CLASS_PRINTF = "printf"  # Format String primitive
NODE_CLASS_SCANF = "scanf"  # Scan From String primitive (variable terminals)
NODE_CLASS_NMUX = "nMux"  # Node Multiplexer (selector)
NODE_CLASS_ARRAY_DELETE = "aDelete"  # Delete From Array
NODE_CLASS_ARRAY_INDEX = "aIndx"  # Index Array (expandable)
NODE_CLASS_ARRAY_REPLACE = "aReplace"  # Replace Array Subset
NODE_CLASS_ARRAY_INSERT = "aInsert"  # Insert Into Array
NODE_CLASS_ARRAY_RESHAPE = "aReshape"  # Reshape Array
NODE_CLASS_CONCAT = "concat"  # Concatenate (strings/arrays)
NODE_CLASS_SUBSET = "subset"  # Array/String Subset
NODE_CLASS_MERGE_ERRORS = "mergeErrors"  # Merge Errors
NODE_CLASS_OH_EXT = "oHExt"  # Obtain/Release Semaphore
NODE_CLASS_MUX = "mux"  # Multiplexer (bundle at structure boundary)
NODE_CLASS_DEMUX = "demux"  # Demultiplexer (unbundle at structure boundary)
NODE_CLASS_CTL_REF_CONST = "ctlRefConst"  # Control reference constant
NODE_CLASS_GREF = "gRef"  # Local Variable reference
NODE_CLASS_STAT_VI_REF = "statVIRef"  # Static VI Reference constant
NODE_CLASS_FORMULA = "fBox"  # Formula Node (embedded C-like script)
# In Place Element Structure (IPES)
NODE_CLASS_DECOMPOSE_RECOMPOSE = "decomposeRecomposeStructure"

# Node classes whose ``list``-role terminals (see NMuxHandler-family parsing)
# are accessed BY NAME -- a real ``<i>`` field index into the aggregate
# cluster/class's field list -- rather than positionally: the nMux "Node
# Multiplexer" class itself, and ``decomposeClusterNode`` (the In Place
# Element Structure's cluster border node, same dcoAgg/dcoList/poser shape,
# see parser/node_types.py::DecomposeClusterHandler). Deliberately EXCLUDES
# ``mux``/``demux`` (loop/structure-boundary bundlers -- structurally
# identical SelectNode shape, but positional/sequential indices, never a
# genuine by-name field lookup -- see _MuxHandler/_DemuxHandler) and
# ``eventDataNode`` (also genuinely by-name, but resolved through its own
# EventDataGlyph path in render, never through netlist/diff/describe).
# Shared by render's Bundle/Unbundle-By-Name glyph selection and
# graph.op_walk's canonical nMux lane-name resolver so both agree on exactly
# which node classes get field-name (not index) treatment.
NMUX_BY_NAME_NODE_CLASSES = frozenset({NODE_CLASS_NMUX, "decomposeClusterNode"})

# Node classes that are explicitly ignored during parsing.
# These are known LabVIEW elements with no Python equivalent and no dataflow
# output — they are intentionally not converted to graph nodes.
NODE_CLASS_COMMENT = "commentNode"  # Block diagram annotation / labeled wire section
SKIP_NODE_CLASSES: frozenset[str] = frozenset({NODE_CLASS_COMMENT})

# All node classes that contain operations (and therefore have terminals)
OPERATION_NODE_CLASSES = (
    NODE_CLASS_PRIM,
    NODE_CLASS_SUBVI,
    NODE_CLASS_POLY_SUBVI,
    NODE_CLASS_DYN_SUBVI,
    NODE_CLASS_WHILE_LOOP,
    NODE_CLASS_FOR_LOOP,
    NODE_CLASS_SELECT,
    NODE_CLASS_CASE_STRUCT,
    NODE_CLASS_SEQ,
    NODE_CLASS_SEQUENCE,
    NODE_CLASS_EVENT_STRUCT,
    NODE_CLASS_PROP_NODE,
    NODE_CLASS_INVOKE_NODE,
    NODE_CLASS_CPD_ARITH,
    NODE_CLASS_ARRAY_BUILD,
    NODE_CLASS_ARRAY_INIT,
    NODE_CLASS_FLAT_SEQ,
    NODE_CLASS_PRINTF,
    NODE_CLASS_SCANF,
    NODE_CLASS_NMUX,
    NODE_CLASS_ARRAY_DELETE,
    NODE_CLASS_ARRAY_INDEX,
    NODE_CLASS_ARRAY_REPLACE,
    NODE_CLASS_ARRAY_INSERT,
    NODE_CLASS_ARRAY_RESHAPE,
    NODE_CLASS_CONCAT,
    NODE_CLASS_SUBSET,
    NODE_CLASS_MERGE_ERRORS,
    NODE_CLASS_OH_EXT,
    NODE_CLASS_MUX,
    NODE_CLASS_DEMUX,
    NODE_CLASS_CTL_REF_CONST,
    NODE_CLASS_GREF,
    NODE_CLASS_STAT_VI_REF,
    NODE_CLASS_FORMULA,
    NODE_CLASS_CALL_PARENT,
    NODE_CLASS_CALL_BY_REF,
    NODE_CLASS_DECOMPOSE_RECOMPOSE,
    NODE_CLASS_EVENT_DATA_NODE,
    # Feedback Node master/slave pair (z^-N state element) — previously reached
    # only via the generic-operation sweep; now typed by their own handlers, so
    # they must be whitelisted here (else _extract_nodes silently drops them).
    NODE_CLASS_FEEDBACK_MASTER,
    NODE_CLASS_FEEDBACK_SLAVE,
    # Inner decompose/recompose node types (inside IPES structures)
    "decomposeClusterNode",
    "decomposeArrayNode",
    "decomposeDataValRefNode",
    "decomposeMatchNode",
)

# Loop node classes
LOOP_NODE_CLASSES = (NODE_CLASS_WHILE_LOOP, NODE_CLASS_FOR_LOOP)

# All structure classes that contain inner diagrams
STRUCTURE_NODE_CLASSES = frozenset(
    {
        NODE_CLASS_WHILE_LOOP,
        NODE_CLASS_FOR_LOOP,
        NODE_CLASS_SELECT,
        NODE_CLASS_CASE_STRUCT,
        NODE_CLASS_FLAT_SEQ,
        NODE_CLASS_SEQ,
        NODE_CLASS_SEQUENCE,
        NODE_CLASS_EVENT_STRUCT,
        NODE_CLASS_DECOMPOSE_RECOMPOSE,
    }
)

# Conditional/case node classes
CONDITIONAL_NODE_CLASSES = (NODE_CLASS_SELECT, NODE_CLASS_CASE_STRUCT)

# Tunnel/shift register DCO classes (inside loop terminal dco elements)
TUNNEL_CLASS_LEFT_SR = "lSR"  # Left shift register (input)
TUNNEL_CLASS_RIGHT_SR = "rSR"  # Right shift register (output)
TUNNEL_CLASS_LOOP_TUNNEL = "lpTun"  # Loop tunnel (simple pass-through)
TUNNEL_CLASS_LMAX = "lMax"  # For-loop N (iteration-count) INPUT terminal (loopLimitDCO)
TUNNEL_CLASS_SEQ_TUN = "seqTun"  # Sequence tunnel (pass-through between frames)
TUNNEL_CLASS_FLAT_SEQ_TUN = "flatSeqTun"  # Flat seq tunnel (with mate)
# IPES cluster/array/DVR tunnel
TUNNEL_CLASS_DECOMPOSE_RECOMPOSE = "decomposeRecomposeTunnel"

# Shift register node (contains inner tunnel terminals)
NODE_CLASS_SHIFT_REG = "sRN"  # Shift register node - holds inner ends of tunnels

# All tunnel types that create outer↔inner terminal mappings
TUNNEL_DCO_CLASSES = (
    TUNNEL_CLASS_LEFT_SR,
    TUNNEL_CLASS_RIGHT_SR,
    TUNNEL_CLASS_LOOP_TUNNEL,
    TUNNEL_CLASS_LMAX,
    TUNNEL_CLASS_SEQ_TUN,
    TUNNEL_CLASS_FLAT_SEQ_TUN,
    TUNNEL_CLASS_DECOMPOSE_RECOMPOSE,
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
# From pylabview LVparts.py OBJ_FLAGS:
# Bit 0 (isIndicator) = 0x1 - when set, terminal is an OUTPUT (indicator)
# When bit 0 is NOT set, terminal is an INPUT (control)
TERMINAL_OUTPUT_FLAG = 0x1  # Bit 0 - isIndicator - terminal outputs data


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
    basename = os.path.basename(bd_xml_path)
    return basename.replace(BD_XML_SUFFIX, "")
