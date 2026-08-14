"""Unified VI parsing - single entry point for all VI components.

Architecture:
- parse_vi() is the single entry point
- Returns ParsedVI containing all components
- Pure XML extraction, no external lookups
- Resolution/enrichment happens in lvkit.graph (InMemoryVIGraph)
"""

from __future__ import annotations

import json
import logging
import re
import struct
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.extractor import extract_vi_xml
from lvkit.models import LVType
from lvkit.text_encoding import decode_labview_text

from .conp_types import conp_sidecar_path, decode_conp_terminals
from .constants import (
    FP_TERMINAL_CLASS,
    MULTI_LABEL_CLASS,
    NODE_CLASS_COMMENT,
    NODE_CLASS_CPD_ARITH,
    NODE_CLASS_SHIFT_REG,
    OPERATION_NODE_CLASSES,
    STRUCTURE_NODE_CLASSES,
    TERMINAL_CLASS,
    TERMINAL_CONTAINER_CLASSES,
)
from .flags import is_indicator, is_inverted_terminal, is_output_terminal
from .front_panel import (
    _lvtype_to_parsed,
    extract_fp_terminals,
    parse_connector_pane,
    parse_connector_pane_labels,
)
from .layout import Layout, _icon_for_heap, build_layout_from_root
from .metadata import parse_iuse_from_libd
from .models import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedConstant,
    ParsedDependencyRef,
    ParsedFPControl,
    ParsedFPTerminal,
    ParsedFrontPanel,
    ParsedNode,
    ParsedTerminalInfo,
    ParsedVI,
    ParsedVIMetadata,
    ParsedWire,
    SelectorTable,
)
from .node_types import parse_node
from .nodes import (
    extract_case_structures,
    extract_constants,
    extract_decompose_structures,
    extract_disable_structures,
    extract_event_structures,
    extract_flat_sequences,
    extract_loops,
    is_disable_structure,
    parse_selector_tables,
)
from .type_mapping import parse_type_map_rich
from .type_resolution import resolve_type_rich
from .utils import (
    clean_labview_string,
    decode_xml_entities_to_bytes,
    extract_label,
    safe_int,
    strip_surrounding_quotes,
)

# A single corrupt VI can export a front-panel heap that balloons past 1 GB (a
# bad LVVariant DataFill); parsing that into an ElementTree can OOM the host.
# The FP heap only feeds the OPTIONAL connector pane / front panel, so above
# this cap we skip it and degrade rather than crash. Real FP heaps are well
# under this.
_MAX_FP_HEAP_BYTES = 256 * 1024 * 1024

logger = logging.getLogger(__name__)


def _load_node_dco_maps() -> dict[str, dict[str, int]]:
    """Load DCO maps from primitives.json node_types terminals.

    Builds {node_class: {dco_ref_tag: terminal_index}} from terminals
    that have a dco_ref field. Same terminal structure as primitives.

    Returns: {node_class: {dco_ref_tag: terminal_index}}
    """
    from .._data import data_dir as _bundled_data_dir
    primitives_path = _bundled_data_dir() / "primitives.json"
    if not primitives_path.exists():
        return {}
    with open(primitives_path, encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for node_type, info in data.get("node_types", {}).items():
        dco_map = {}
        for t in info.get("terminals", []):
            ref = t.get("dco_ref")
            if ref:
                dco_map[ref] = t["index"]
        if dco_map:
            result[node_type] = dco_map
    return result


# Loaded once at import time
_NODE_DCO_MAP: dict[str, dict[str, int]] = _load_node_dco_maps()


def parse_vi(
    vi_path: Path | str | None = None,
    *,
    bd_xml: Path | str | None = None,
    fp_xml: Path | str | None = None,
    main_xml: Path | str | None = None,
    layout: bool = False,
) -> ParsedVI:
    """Parse a VI file into all components.

    This is the single entry point for VI parsing. Returns a ParsedVI
    containing metadata, block diagram, front panel, and connector pane.

    Args:
        vi_path: Path to .vi file (extracts XML automatically)
        bd_xml: Path to *_BDHb.xml (for direct XML parsing)
        fp_xml: Path to *_FPHb.xml (optional)
        main_xml: Path to main *.xml (optional)
        layout: also decode block-diagram GEOMETRY (node/terminal/wire bounds)
            from the same parsed heap and attach it as ``ParsedVI.layout``. Off
            by default — codegen needs no positions. Rendering passes True so
            geometry comes from this one read instead of a second heap parse.

    Returns:
        ParsedVI with all components
    """
    # Extract XML from VI file if needed
    if vi_path is not None and bd_xml is None:
        bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)

    if bd_xml is None:
        raise ValueError("Either vi_path or bd_xml must be provided")

    bd_xml = Path(bd_xml)

    # Guard: skip a pathologically-large front-panel heap so one corrupt VI
    # can't OOM the host (see _MAX_FP_HEAP_BYTES). Dropping fp_xml here makes the
    # block-diagram, front-panel, and connector-pane parses below all skip it.
    if fp_xml is not None:
        try:
            fp_size = Path(fp_xml).stat().st_size
        except OSError:
            fp_size = 0
        if fp_size > _MAX_FP_HEAP_BYTES:
            warnings.warn(
                f"{bd_xml.name}: front-panel heap is {fp_size // (1024 * 1024)} MB "
                f"(> {_MAX_FP_HEAP_BYTES // (1024 * 1024)} MB cap, likely a corrupt "
                "LVVariant DataFill) — skipping it; connector pane unavailable.",
                stacklevel=2,
            )
            fp_xml = None

    # Derive source .vi path. Prefer the explicit vi_path argument since BD XML
    # may now live in a temp cache dir rather than next to the source file.
    if vi_path is not None:
        source_path_str = str(Path(vi_path).resolve())
    else:
        source_path = bd_xml.with_name(bd_xml.name.replace("_BDHb.xml", ".vi"))
        source_path_str = str(source_path) if source_path.exists() else None

    # Parse metadata from main XML
    metadata = _parse_metadata(main_xml, source_path_str)

    # Parse case-structure selector-value tables from the dataspace XML. These
    # carry the real per-frame selector labels, which the block-diagram heap
    # does not (see parse_selector_tables / _apply_selector_tables).
    selector_tables = _parse_selector_tables(main_xml)

    # Parse block diagram (+ optional geometry from the SAME parsed heap)
    block_diagram, bd_layout = _parse_block_diagram(
        bd_xml, fp_xml, metadata.type_map, selector_tables,
        want_layout=layout,
    )

    # Parse front panel
    if source_path_str:
        vi_label = Path(source_path_str).name
    else:
        # No resolvable source .vi path (e.g. parsed from cached XML with no
        # vi_path given) -- fall back to the BD XML's own name, stripped of
        # its cache-file suffix so warnings still read as a VI name.
        vi_label = bd_xml.name.replace("_BDHb.xml", ".vi")
    unresolved_uids: set[str] = set()
    front_panel = _parse_front_panel(
        fp_xml, block_diagram, metadata.type_map, unresolved_uids=unresolved_uids,
    )

    # Parse connector pane
    connector_pane = None
    if fp_xml:
        fp_xml_path = Path(fp_xml)
        if fp_xml_path.exists():
            connector_pane = parse_connector_pane(fp_xml_path)

    # A control whose own FP-heap label didn't resolve gets one more chance:
    # the VCTP flat type table (in the main XML) records a Label
    # independently of the FP heap, and sometimes survives when the FP
    # copy doesn't -- see _recover_or_warn_unresolved_labels. Only settles
    # on the control_<uid> placeholder (with a warning) once that also
    # comes up empty.
    if unresolved_uids:
        _recover_or_warn_unresolved_labels(
            front_panel, connector_pane, main_xml, vi_label, unresolved_uids,
        )

    return ParsedVI(
        metadata=metadata,
        block_diagram=block_diagram,
        front_panel=front_panel,
        connector_pane=connector_pane,
        layout=bd_layout,
    )


def _parse_metadata(
    main_xml_path: Path | str | None,
    source_path: str | None,
) -> ParsedVIMetadata:
    """Parse VI metadata from main XML."""
    if main_xml_path is None:
        return ParsedVIMetadata(source_path=source_path)

    main_xml = Path(main_xml_path)
    if not main_xml.exists():
        return ParsedVIMetadata(source_path=source_path)

    main_tree = ET.parse(main_xml)
    main_root = main_tree.getroot()

    # Extract qualified name from LVIN or LVSR
    qualified_name: str | None = None
    lvin = main_root.find(".//LIvi/Section/LVIN")
    if lvin is not None:
        qualified_name = lvin.get("Unk1")
    if not qualified_name:
        lvsr = main_root.find(".//LVSR/Section")
        if lvsr is not None:
            qualified_name = lvsr.get("Name")

    # The VI's OWN ownership chain (``<LIBN>`` "Library Names") — the owning
    # .lvlib/.lvclass, outermost first. Self-described in the VI's binary, so a
    # class member .vi carries it even in isolation. DISPLAY only (see
    # ParsedVIMetadata.owning_libraries) — the bare ``qualified_name`` above
    # remains the resolution key.
    owning_libraries = [
        e.text for e in main_root.findall(".//LIBN/Section/Library") if e.text
    ]

    # Extract SubVI info
    (
        subvi_qualified_names,
        iuse_to_qualified_name,
        dependency_refs,
    ) = _extract_subvi_info(main_root, qualified_name)

    # Fallback for older VIs (pre-LV9): pylabview cannot parse their LIbd
    # section, so BDHP/IUVI elements are absent from the XML. Read the raw
    # _LIbd.bin binary directly to recover the iUse UID → qualified name map.
    if not iuse_to_qualified_name:
        libd_bin = main_xml.with_name(main_xml.stem + "_LIbd.bin")
        if libd_bin.exists():
            iuse_to_qualified_name = parse_iuse_from_libd(libd_bin)

    # Parse type map
    type_map = parse_type_map_rich(main_xml)

    return ParsedVIMetadata(
        qualified_name=qualified_name,
        owning_libraries=owning_libraries,
        source_path=source_path,
        type_map=type_map or {},
        subvi_qualified_names=subvi_qualified_names,
        iuse_to_qualified_name=iuse_to_qualified_name,
        dependency_refs=dependency_refs,
    )


def _parse_selector_tables(
    main_xml_path: Path | str | None,
) -> list[SelectorTable]:
    """Parse case selector-value tables from the main dataspace XML."""
    if main_xml_path is None:
        return []
    main_xml = Path(main_xml_path)
    if not main_xml.exists():
        return []
    return parse_selector_tables(ET.parse(main_xml).getroot())


def _parse_block_diagram(
    bd_xml: Path,
    fp_xml: Path | str | None,
    type_map: dict[int, LVType] | None,
    selector_tables: list[SelectorTable] | None = None,
    *,
    want_layout: bool = False,
) -> tuple[ParsedBlockDiagram, Layout | None]:
    """Parse block diagram from BD XML.

    When ``want_layout`` is set, also decode the diagram's geometry from the
    SAME parsed ``root`` (no second read) and return it alongside — the parser
    owning both semantics and positions from one pass.
    """
    tree = ET.parse(bd_xml)
    root = tree.getroot()

    nodes = _extract_nodes(root)
    constants = extract_constants(root)
    wires = _extract_wires(root)
    fp_terminals = extract_fp_terminals(root, fp_xml, type_map)
    enum_labels = _extract_enum_labels(root)
    srn_to_structure: dict[str, str] = {}
    terminal_info = _extract_terminal_info(
        root, constants, fp_terminals, wires, type_map,
        srn_to_structure=srn_to_structure,
    )
    loops = extract_loops(root)
    case_structures = extract_case_structures(
        root, terminal_info, selector_tables,
    )
    flat_sequences = extract_flat_sequences(root)
    decompose_structures = extract_decompose_structures(root)
    disable_structures = extract_disable_structures(root)
    event_structures = extract_event_structures(root, fp_xml)

    bd = ParsedBlockDiagram(
        nodes=nodes,
        constants=constants,
        wires=wires,
        fp_terminals=fp_terminals,
        enum_labels=enum_labels,
        terminal_info=terminal_info,
        loops=loops,
        case_structures=case_structures,
        flat_sequences=flat_sequences,
        decompose_structures=decompose_structures,
        disable_structures=disable_structures,
        event_structures=event_structures,
        srn_to_structure=srn_to_structure,
    )
    layout = (
        build_layout_from_root(root, icon_png=_icon_for_heap(Path(bd_xml)))
        if want_layout
        else None
    )
    return bd, layout


def _parse_front_panel(
    fp_xml: Path | str | None,
    block_diagram: ParsedBlockDiagram,
    type_map: dict[int, LVType] | None = None,
    unresolved_uids: set[str] | None = None,
) -> ParsedFrontPanel:
    """Parse front panel from FP XML.

    ``unresolved_uids``, if given, collects the uid of every control whose
    own label didn't resolve (see ``_placeholder_control_name``) so a caller
    can attempt a second-source recovery once the connector pane is also
    parsed.
    """
    if unresolved_uids is None:
        unresolved_uids = set()
    if fp_xml is None:
        return ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 400, 600))

    fp_xml_path = Path(fp_xml)
    if not fp_xml_path.exists():
        return ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 400, 600))

    tree = ET.parse(fp_xml_path)
    root = tree.getroot()

    # Build indicator UIDs from block diagram for accurate is_indicator detection
    indicator_dco_uids: set[str] = set()
    for fp_term in block_diagram.fp_terminals:
        if fp_term.is_indicator:
            indicator_dco_uids.add(fp_term.fp_dco_uid)

    controls = []

    # Get panel bounds
    pbounds_elem = root.find("pBounds")
    if pbounds_elem is not None and pbounds_elem.text:
        panel_bounds = _parse_bounds(pbounds_elem.text)
    else:
        panel_bounds = (0, 0, 400, 600)

    # Find all front panel data control objects (fPDCO)
    for fpdco in root.findall(".//*[@class='fPDCO']"):
        uid = fpdco.get("uid", "")

        # Get the data display object (ddo) which has the control type
        ddo = fpdco.find("ddo")
        if ddo is None:
            for child in fpdco:
                child_class = child.get("class", "")
                if child_class.startswith("std") or child_class == "typeDef":
                    ddo = child
                    break

        if ddo is None:
            continue

        # Extract default data
        default_value = None
        default_elem = fpdco.find("DefaultData")
        if default_elem is not None and default_elem.text:
            # Strip only the wrapping quotes — NOT clean_labview_string, which
            # deletes &#xNN; byte-entities (including the null bytes in a
            # string/numeric/path default's length prefix) before
            # _decode_default_data's decode_xml_entities_to_bytes ever sees
            # them, corrupting the value (task #78).
            raw_data = strip_surrounding_quotes(default_elem.text)
            control_type = ddo.get("class", "unknown")

            # Resolve type for array/cluster decoding
            lv_type = None
            type_desc_elem = fpdco.find("typeDesc")
            if type_desc_elem is not None and type_desc_elem.text and type_map:
                lv_type = resolve_type_rich(type_desc_elem.text, type_map)

            default_value = _decode_default_data(raw_data, control_type, lv_type)

        control = _parse_ddo(
            ddo, uid, indicator_dco_uids, default_value,
            unresolved_uids=unresolved_uids,
        )
        if control:
            control.ddo_uid = ddo.get("uid")
            controls.append(control)

    return ParsedFrontPanel(
        controls=controls,
        panel_bounds=panel_bounds,
    )


# === Helper functions ===


# Node-SHAPED heap elements (they carry a class + bounds + termList) that are
# NOT dataflow operation nodes, so the generic "unknown node" capture below must
# skip them: shift-register nodes (handled via their structure), sequence frames
# (structure frames, not nodes), and free-label comment nodes.
_NON_OPERATION_NODE_CLASSES = frozenset({
    NODE_CLASS_SHIFT_REG, "sequenceFrame", "commentNode",
})


def _is_generic_operation_node(elem: ET.Element) -> bool:
    """An unhandled but node-shaped block-diagram element — has a ``class``, a
    ``bounds``, and a ``termList``, and its class is neither in the handled
    allowlist nor a known non-operation node. Captured generically so it renders
    as a labelled box with its terminals (and its wires connect) instead of
    being silently dropped — codegen still fails loudly on it."""
    cls = elem.get("class")
    return bool(
        cls
        and cls not in OPERATION_NODE_CLASSES
        and cls not in _NON_OPERATION_NODE_CLASSES
        and elem.find("bounds") is not None
        and elem.find("termList") is not None
    )


def _extract_nodes(root: ET.Element) -> list[ParsedNode]:
    """Extract nodes from the block diagram using node type factory.

    Single tree walk: bucket every element by its ``class`` and collect the
    generic-sweep candidates in one pass, then emit in ``OPERATION_NODE_CLASSES``
    order. This replaces a per-class ``.//*[@class=X]`` findall (one full
    descendant scan *per allowlisted class*) — identical nodes and order, but one
    tree traversal instead of ~len(OPERATION_NODE_CLASSES).
    """
    allowed = frozenset(OPERATION_NODE_CLASSES)
    by_class: dict[str, list[ET.Element]] = {}
    generic: list[ET.Element] = []
    disable_elems: list[ET.Element] = []
    for elem in root.iter():
        # `.//*[@class=X]` matches descendants only — exclude root from buckets.
        if elem is not root:
            cls = elem.get("class")
            if cls is not None and cls in allowed:
                by_class.setdefault(cls, []).append(elem)
            elif cls == NODE_CLASS_COMMENT and is_disable_structure(elem):
                disable_elems.append(elem)
        # matches `root.iter("SL__arrayElement")` (includes root if it matched).
        if elem.tag == "SL__arrayElement":
            generic.append(elem)

    nodes: list[ParsedNode] = []
    seen_uids: set[str] = set()

    for cls in OPERATION_NODE_CLASSES:
        for elem in by_class.get(cls, ()):
            node = parse_node(elem)
            nodes.append(node)
            if node.uid:
                seen_uids.add(node.uid)

    # Disable structures (class="commentNode" with subdiagrams) — same
    # tree-shaped-node treatment as case/loop/sequence structures. Gated by
    # is_disable_structure above since a plain free-text comment never
    # carries a diagramList and must stay a no-op (SKIP_NODE_CLASSES).
    for elem in disable_elems:
        node = parse_node(elem)
        nodes.append(node)
        if node.uid:
            seen_uids.add(node.uid)

    # Generic capture: node-shaped elements the allowlist above misses (e.g.
    # decimate, interLeave, extFunc, exprNode). parse_node falls back to
    # GenericHandler for unknown classes, so they become real ParsedNodes that
    # render as boxes with wired terminals rather than vanishing.
    for elem in generic:
        uid = elem.get("uid")
        if uid and uid not in seen_uids and _is_generic_operation_node(elem):
            nodes.append(parse_node(elem))
            seen_uids.add(uid)

    return nodes


def _extract_wires(root: ET.Element) -> list[ParsedWire]:
    """Extract wires (signals) from the block diagram."""
    wires = []

    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        uid = sig.get("uid") or ""
        terms: list[str] = [
            t_uid
            for t in sig.findall("termList/SL__arrayElement")
            if (t_uid := t.get("uid"))
        ]

        if len(terms) >= 2:
            source = terms[0]
            for i, dest in enumerate(terms[1:]):
                wires.append(ParsedWire(
                    uid=f"{uid}_{i}" if i > 0 else uid,
                    from_term=source,
                    to_term=dest,
                ))

    return wires


def _extract_enum_labels(root: ET.Element) -> dict[str, list[str]]:
    """Extract enum/ring labels from the XML.

    Parses multi-label buffers like '(10)"Label1""Label2""Label3"'
    where labels are quoted strings.
    """
    enums: dict[str, list[str]] = {}
    for multi_label in root.findall(f".//*[@class='{MULTI_LABEL_CLASS}']"):
        buf = multi_label.find("buf")
        if buf is not None and buf.text:
            # Extract all quoted strings using regex
            labels = re.findall(r'"([^"]*)"', buf.text)
            if labels:
                uid = multi_label.get("uid")
                if uid:
                    enums[uid] = labels
    return enums


def _build_lptun_inner_type_map(root: ET.Element) -> dict[str, str]:
    """Map each loop-tunnel dco uid -> its INNER face's typeDesc text.

    A loop tunnel's ``dco class="lpTun"`` is defined ONCE, on the OUTER
    boundary ``<term>``; its direct ``<typeDesc>`` is the OUTER face's type. The
    INNER face is a separate ``<term>`` on the loop-body diagram that carries
    only a BARE ``<dco uid=.../>`` back-reference to that same dco — with no
    type of its own. The inner face's real type lives in the lpTun dco's nested
    ``<innerLpTunDCO>``'s ``<typeDesc>``. Keying it by the shared dco uid lets
    the inner term resolve its own type (see the fallback in
    ``_process_element_terminals``). For an auto-indexing tunnel the inner is
    the ELEMENT and the outer the ARRAY (different TypeIDs, both explicit in the
    file); for a last-value/pass-through tunnel they coincide.
    """
    inner_types: dict[str, str] = {}
    for dco in root.iter("dco"):
        if dco.get("class") != "lpTun":
            continue
        uid = dco.get("uid")
        inner_dco = dco.find("innerLpTunDCO")
        if not uid or inner_dco is None:
            continue
        type_desc = inner_dco.find("typeDesc")
        if type_desc is not None and type_desc.text:
            inner_types[uid] = type_desc.text
    return inner_types


def _process_element_terminals(
    elem: ET.Element,
    wire_sources: set[str],
    wire_sinks: set[str],
    type_map: dict[int, LVType] | None,
    terminal_info: dict[str, ParsedTerminalInfo],
    lptun_inner_types: dict[str, str],
    fp_term_parent: dict[str, str] | None = None,
) -> None:
    """Extract terminals from a single TERMINAL_CONTAINER_CLASSES element.

    ``fp_term_parent`` (optional — record-only, doesn't affect ``term_list``
    processing below) records the REAL containing element for any
    ``class="fPTerm"`` child of this element's ``termList`` — e.g. an sRN
    (shift-register/border-terminal group; ``NODE_CLASS_SHIFT_REG`` is one of
    ``TERMINAL_CONTAINER_CLASSES``) holds an event structure's registered
    event-source control this way. An ``fPTerm`` isn't a wireable ``"term"``
    (it's a whole front-panel control's on-diagram GLYPH), so it's excluded
    from ``term_list`` below and gets no ``ParsedTerminalInfo`` here — but
    without this, ``_extract_terminal_info``'s later FP-terminal fallback has
    no real container to attribute it to and stamps a bogus self-referential
    ``parent_uid`` (the terminal's own uid), silently losing this control's
    structure/frame containment for good (task: VI Tester About.vi frame-gating).
    """
    elem_uid = elem.get("uid") or ""
    elem_class = elem.get("class", "")

    if fp_term_parent is not None:
        for fp_term in elem.findall(
            f"./termList/SL__arrayElement[@class='{FP_TERMINAL_CLASS}']",
        ):
            fp_uid = fp_term.get("uid")
            if fp_uid and elem_uid:
                fp_term_parent.setdefault(fp_uid, elem_uid)

    term_list = elem.findall(
        f"./termList/SL__arrayElement[@class='{TERMINAL_CLASS}']",
    )

    for list_position, term in enumerate(term_list):
        term_uid = term.get("uid")
        if not term_uid:
            continue

        dco = term.find("dco")
        dco_uid = dco.get("uid") if dco is not None else None
        dco_class = dco.get("class", "") if dco is not None else ""

        # Get terminal index from dco.
        # Primitives use "parmIndex", SubVIs use "paramIdx".
        # Missing paramIdx = 0 (XML omits the default value).
        # Missing parmIndex on primitives = genuinely unknown (-1).
        parm_index = -1
        if dco is not None:
            for idx_field in ("parmIndex", "paramIdx"):
                idx_elem = dco.find(idx_field)
                if idx_elem is not None and idx_elem.text:
                    parm_index = int(idx_elem.text)
                    break
            else:
                # No index field found. XML omits parmIndex when it's 0.
                # Applies to SubVI calls AND primitives.
                if elem_class in (
                    "iUse", "polyIUse", "dynIUse", "callParentDynIUse",
                    "callByRefNode", "prim",
                ):
                    parm_index = 0

        # callByRefNode frame terminals (hGrowCItem DCOs: error in/out,
        # VI ref in/out) have no paramIdx and get negative indices so they
        # don't collide with the callee iUseDCO terminals' paramIdx values.
        if elem_class == "callByRefNode" and dco_class == "hGrowCItem":
            parm_index = -(list_position + 1)

        # For specialized node classes (aDelete, aIndx, etc.),
        # resolve index from named DCO references on the parent node.
        if parm_index == -1 and dco_uid and elem_class in _NODE_DCO_MAP:
            dco_map = _NODE_DCO_MAP[elem_class]
            for ref_tag, ref_index in dco_map.items():
                ref_elem = elem.find(ref_tag)
                if ref_elem is not None:
                    # Direct ref: element has uid matching dco
                    if ref_elem.get("uid") == dco_uid:
                        parm_index = ref_index
                        break
                    # List ref (dcoList, lengthDCOList): children
                    # have uids. Position in list = dimension.
                    # Stride by number of list-type refs to interleave.
                    for pos, child in enumerate(ref_elem):
                        if child.get("uid") == dco_uid:
                            # Count how many list-type refs exist
                            # to determine stride for interleaving
                            n_lists = sum(
                                1 for rt in dco_map
                                if (rt_elem := elem.find(rt)) is not None
                                and len(rt_elem) > 0
                            )
                            parm_index = ref_index + (pos * max(n_lists, 1))
                            break
                if parm_index >= 0:
                    break

        # Last resort: use list position as index.
        # Covers sRN terminals, printf expandable terminals, and any
        # other terminal type without explicit parmIndex in the XML.
        # The termList order IS the natural index.
        if parm_index == -1:
            parm_index = list_position

        # Determine direction from wire connectivity
        if term_uid in wire_sources:
            is_output = True
        elif term_uid in wire_sinks:
            is_output = False
        else:
            # Unwired terminal — direction from the DCO's objFlags bit 0 ONLY
            # when a DCO is present. The term-level <objFlags> bit 0 is
            # overloaded/unreliable for direction; a corpus study over 992
            # unwired ground-truth terminals (scripts/study_unwired_direction.py)
            # scored dco_only 100% vs the old `term_flags | dco_flags` 99.8%
            # (wrong exactly on the noisy bit-0 case, e.g. Search/Split String)
            # vs term_only ~40%. With NO DCO there is no authoritative signal
            # (all 992 studied terminals had one), so fall back to the term-level
            # flags — consulting the available data beats silently defaulting to
            # input (safe_int(None) == 0 == input).
            if dco is not None:
                is_output = is_output_terminal(safe_int(dco.find("objFlags")))
            else:
                is_output = is_output_terminal(safe_int(term.find("objFlags")))

        # Resolve TypeID to ParsedType
        type_desc_elem = term.find(".//typeDesc")
        type_desc_str = (
            type_desc_elem.text if type_desc_elem is not None
            else None
        )
        if not type_desc_str and dco_uid:
            # Some node classes (e.g. aReshape) declare the terminal's real
            # dco definition (with its own typeDesc) at the *node* level
            # under named tags (srcDCO, dcoAgg, dcoList/...), and the
            # termList entry's <dco> is a bare uid reference with no
            # embedded type of its own. Resolve by following the uid to
            # wherever the real definition lives in the node's subtree.
            for candidate in elem.iter():
                if candidate is dco or candidate.get("uid") != dco_uid:
                    continue
                candidate_type_desc = candidate.find("typeDesc")
                if candidate_type_desc is not None and candidate_type_desc.text:
                    type_desc_str = candidate_type_desc.text
                    break
        if not type_desc_str and dco_uid and dco_uid in lptun_inner_types:
            # Loop-tunnel INNER face: its <term> (on the loop-body diagram)
            # holds only a bare <dco uid=.../> back-ref to the lpTun dco defined
            # on the OUTER boundary term — so the search above (scoped to this
            # element's own subtree) can't find it. The inner face's real type
            # lives in that dco's <innerLpTunDCO><typeDesc>, indexed by dco uid.
            # This types the inner face directly from the file (the ELEMENT type
            # for an indexing tunnel), so the graph never guesses it by racing
            # wire propagation. See _build_lptun_inner_type_map.
            type_desc_str = lptun_inner_types[dco_uid]
        parsed_type = None
        if type_desc_str and type_map:
            lv_type = resolve_type_rich(type_desc_str, type_map)
            parsed_type = _lvtype_to_parsed(lv_type)

        # Extract terminal label from dco or terminal element
        term_name = None
        if dco is not None:
            term_name = extract_label(dco)
        if not term_name:
            term_name = extract_label(term)

        # Per-terminal "Not" flag: bit 16 (0x00010000) set in the
        # terminal's DCO objFlags. This bit only means "invert" for
        # Compound Arithmetic nodes; other primitives (e.g. Increment)
        # reuse the same bit for an unrelated purpose, so only extract
        # it when the containing element is cpdArith.
        inverted = False
        if elem_class == NODE_CLASS_CPD_ARITH:
            dco_flags_elem = dco.find("objFlags") if dco is not None else None
            inverted = is_inverted_terminal(safe_int(dco_flags_elem))

        terminal_info[term_uid] = ParsedTerminalInfo(
            uid=term_uid,
            parent_uid=elem_uid,
            index=parm_index,
            is_output=is_output,
            parsed_type=parsed_type,
            name=term_name,
            inverted=inverted,
        )


def _walk_and_extract_terminals(
    elem: ET.Element,
    wire_sources: set[str],
    wire_sinks: set[str],
    type_map: dict[int, LVType] | None,
    terminal_info: dict[str, ParsedTerminalInfo],
    srn_to_structure: dict[str, str],
    current_structure_uid: str | None,
    lptun_inner_types: dict[str, str],
    fp_term_parent: dict[str, str] | None = None,
) -> None:
    """Walk XML tree, extracting terminals and tracking sRN containment."""
    elem_uid = elem.get("uid")
    elem_class = elem.get("class", "")

    # A Disable structure's own boundary terminals (commentTun) live in its
    # DIRECT termList, exactly like a case structure's csTun/selTun — but
    # commentNode isn't in TERMINAL_CONTAINER_CLASSES (a plain comment has no
    # terminals worth extracting), so it needs its own is_disable_structure
    # gate here, same as the structure-context check below.
    is_disable_elem = elem_class == NODE_CLASS_COMMENT and is_disable_structure(elem)

    # Extract terminals from this element if it's a terminal container — known
    # operation nodes, a Disable structure's own boundary terminals, or a
    # generically-captured unknown node (so its wires still connect through
    # the placeholder box).
    if elem_uid and (elem_class in TERMINAL_CONTAINER_CLASSES
                     or is_disable_elem
                     or _is_generic_operation_node(elem)):
        _process_element_terminals(
            elem, wire_sources, wire_sinks, type_map, terminal_info,
            lptun_inner_types, fp_term_parent,
        )

    # Record sRN → structure containment
    if elem_uid and elem_class == NODE_CLASS_SHIFT_REG and current_structure_uid:
        srn_to_structure[elem_uid] = current_structure_uid

    # Update structure context for children
    if elem_uid and (elem_class in STRUCTURE_NODE_CLASSES or is_disable_elem):
        next_structure_uid = elem_uid
    else:
        next_structure_uid = current_structure_uid

    # Recurse into children
    for child in elem:
        _walk_and_extract_terminals(
            child, wire_sources, wire_sinks, type_map,
            terminal_info, srn_to_structure, next_structure_uid,
            lptun_inner_types, fp_term_parent,
        )


def _extract_terminal_info(
    root: ET.Element,
    constants: list[ParsedConstant],
    fp_terminals: list[ParsedFPTerminal],
    wires: list[ParsedWire],
    type_map: dict[int, LVType] | None = None,
    srn_to_structure: dict[str, str] | None = None,
) -> dict[str, ParsedTerminalInfo]:
    """Extract detailed terminal info for graph-native representation.

    Walks the XML tree hierarchically to preserve structure containment.
    Populates srn_to_structure (if provided) mapping sRN UIDs to their
    containing structure UIDs.
    """
    terminal_info: dict[str, ParsedTerminalInfo] = {}
    if srn_to_structure is None:
        srn_to_structure = {}
    # FP terminal (control-on-diagram-glyph) uid -> its REAL containing
    # element's uid (typically an sRN — see _process_element_terminals).
    # Used below so an FP terminal placed inside a structure frame (e.g. an
    # Event Structure's registered event-source control) gets a real
    # parent_uid instead of a bogus self-reference.
    fp_term_parent: dict[str, str] = {}

    # Build wire connectivity maps for direction inference
    wire_sources: set[str] = {w.from_term for w in wires}
    wire_sinks: set[str] = {w.to_term for w in wires}

    # Loop-tunnel dco uid -> inner-face typeDesc, so a tunnel's inner <term>
    # (a bare dco back-ref, typeless on its own) can resolve its real type.
    lptun_inner_types = _build_lptun_inner_type_map(root)

    # Walk XML hierarchically — preserves structure containment for sRN nodes
    _walk_and_extract_terminals(
        root, wire_sources, wire_sinks, type_map,
        terminal_info, srn_to_structure, None, lptun_inner_types,
        fp_term_parent,
    )

    # Constants have a single output terminal
    for const in constants:
        if const.uid not in terminal_info:
            parsed_type = None
            if const.type_desc and type_map:
                lv_type = resolve_type_rich(const.type_desc, type_map)
                parsed_type = _lvtype_to_parsed(lv_type)

            terminal_info[const.uid] = ParsedTerminalInfo(
                uid=const.uid,
                parent_uid=const.uid,
                index=0,
                is_output=True,
                parsed_type=parsed_type,
            )

    # Front panel terminals. parent_uid is the REAL containing element
    # (an sRN, when this control's glyph is placed inside a structure frame
    # — see fp_term_parent above) — falling back to the terminal's own uid
    # (the previous, self-referential behavior) only when it was never found
    # nested in any terminal container's termList.
    for fp_term in fp_terminals:
        if fp_term.uid not in terminal_info:
            terminal_info[fp_term.uid] = ParsedTerminalInfo(
                uid=fp_term.uid,
                parent_uid=fp_term_parent.get(fp_term.uid, fp_term.uid),
                index=0,
                is_output=not fp_term.is_indicator,
                parsed_type=fp_term.parsed_type,
                name=fp_term.name,
            )

    return terminal_info


def _resolve_qualified_name(
    elem: ET.Element,
    caller_library: str | None,
) -> str | None:
    """Resolve qualified name from an element with LinkSaveQualName.

    Handles LinkSaveFlag to determine if same-library qualification is needed.

    Args:
        elem: Element with LinkSaveQualName and LinkSaveFlag attributes
        caller_library: Library name of the calling VI, for same-library refs

    Returns:
        Qualified name string, or None if no name found
    """
    strings = [s.text for s in elem.findall("LinkSaveQualName/String") if s.text]
    if not strings:
        return None

    # Strip control characters and XML entities from all strings
    strings = [clean_labview_string(s) for s in strings]
    strings = [s for s in strings if s]  # Remove any that became empty
    if not strings:
        return None

    link_save_flag = elem.get("LinkSaveFlag", "0")
    # Flag "2" means same-library reference - qualify with caller's library
    if link_save_flag == "2" and caller_library and len(strings) == 1:
        return f"{caller_library}:{strings[0]}"
    return ":".join(strings)


def _extract_subvi_info(
    main_root: ET.Element,
    caller_qualified_name: str | None,
) -> tuple[list[str], dict[str, str], list[ParsedDependencyRef]]:
    """Extract SubVI qualified names, iUse→qname mapping, and dependency path refs."""
    subvi_qualified_names: list[str] = []
    iuse_to_qualified_name: dict[str, str] = {}
    dependency_refs: list[ParsedDependencyRef] = []

    # Get caller's library for qualifying same-library references
    caller_library = None
    if caller_qualified_name and ":" in caller_qualified_name:
        caller_library = caller_qualified_name.split(":")[0]

    # --- Collect dependency path refs from ALL link element types ---
    # Every element that carries both LinkSaveQualName + LinkSavePathRef uses
    # the identical schema. Two scopes carry these:
    #   - LIvi/...   : VIVI/VIPI/VICC/etc. — saved-by-LV reference table
    #   - LIbd/BDHP/ : IUVI/PUPV — block-diagram iUse instance records
    # Some VIs only emit one or the other (e.g., DAQmx callers carry path
    # refs under LIbd/BDHP/IUVI with nothing in LIvi). Walk both.
    _LIVI_LINK_TAGS = (
        "VIVI", "VIPI", "VIPV", "VILB", "FPPI", "DDPI",
        "VICC", "DDPC", "FPPC", "IUVI",
        "BSVR", "SVVI",  # statVIRef link types
    )
    _BDHP_LINK_TAGS = ("IUVI", "PUPV")  # block-diagram iUse path refs
    scopes: list[tuple[str, tuple[str, ...]]] = [
        (".//LIvi//{tag}", _LIVI_LINK_TAGS),
        (".//LIbd//BDHP/{tag}", _BDHP_LINK_TAGS),
    ]
    seen_deps: set[tuple[str, tuple[str, ...]]] = set()
    for xpath_template, tags in scopes:
        for tag in tags:
            for elem in main_root.findall(xpath_template.format(tag=tag)):
                qname = _resolve_qualified_name(elem, caller_library)
                if not qname:
                    continue
                path_ref = elem.find("LinkSavePathRef")
                if path_ref is None:
                    continue
                # Preserve empty strings — they are '..' navigation markers.
                # <String /> (self-closing) has .text = None; map to "".
                path_tokens = [
                    s.text if s.text is not None else ""
                    for s in path_ref.findall("String")
                ]
                if not path_tokens:
                    continue
                key: tuple[str, tuple[str, ...]] = (qname, tuple(path_tokens))
                if key in seen_deps:
                    continue
                seen_deps.add(key)
                name = qname.rsplit(":", 1)[-1]
                dependency_refs.append(ParsedDependencyRef(
                    name=name,
                    path_tokens=path_tokens,
                    qualified_name=qname,
                ))

    # --- SubVI qualified names (for the dep loading loop in graph/loading.py) ---
    # VIVI/VIPI/DyOM/VIPV: SubVI calls. BSVR: statVIRef targets.
    for tag in ("VIVI", "VIPI", "DyOM", "VIPV", "BSVR"):
        for elem in main_root.findall(f".//LIvi//{tag}"):
            qname = _resolve_qualified_name(elem, caller_library)
            if qname:
                subvi_qualified_names.append(qname)

    # --- iUse UID → qualified name from BDHP section ---
    # PUPV first (polymorphic wrapper), IUVI overwrites (resolved variant).
    for tag in ("PUPV", "IUVI"):
        for elem in main_root.findall(f".//LIbd//BDHP/{tag}"):
            qname = _resolve_qualified_name(elem, caller_library)
            if qname:
                for offset_elem in elem.findall("LinkOffsetList/Offset"):
                    if offset_elem.text:
                        uid = str(int(offset_elem.text, 16))
                        iuse_to_qualified_name[uid] = qname

    return subvi_qualified_names, iuse_to_qualified_name, dependency_refs


# === Front panel parsing helpers ===


def _parse_bounds(bounds_str: str) -> tuple[int, int, int, int]:
    """Parse bounds string like '(0, 0, 100, 200)' to tuple."""
    try:
        clean = bounds_str.strip("()")
        parts = [int(x.strip()) for x in clean.split(",")]
        if len(parts) == 4:
            return tuple(parts)  # type: ignore
    except (ValueError, AttributeError):
        pass
    return (0, 0, 100, 200)


def _placeholder_control_name(uid: str, unresolved_uids: set[str]) -> str:
    """Placeholder name for a front-panel control whose own label didn't
    resolve (e.g. a build/repackaging step nulled the label string but left
    the rest of the heap intact — see
    docs/_internal/design/error-indicator-handoff.md).

    ``extract_label`` is object-scoped (never steals a nested object's label,
    e.g. a cluster field's name like "source"), so a miss here means the
    control's OWN label text is genuinely gone, not merely mis-scoped.
    Records ``uid`` so a later pass (once the connector pane is parsed) can
    try the VCTP flat-type-table ``Label`` as a second, independent source
    before settling on this placeholder — see
    ``_recover_or_warn_unresolved_labels``.
    """
    unresolved_uids.add(uid)
    return f"control_{uid}"


def _uid_to_control_map(
    controls: list[ParsedFPControl],
) -> dict[str, ParsedFPControl]:
    """Flatten a front panel's controls (including cluster children,
    recursively) into a uid -> control lookup."""
    result: dict[str, ParsedFPControl] = {}
    for ctrl in controls:
        result[ctrl.uid] = ctrl
        result.update(_uid_to_control_map(ctrl.children))
    return result


def _conp_names_by_slot(main_xml: Path | str | None) -> dict[int, str]:
    """Connector-pane terminal names from the CONP sidecar, keyed by slot.

    The pre-LV9 name source (``*_CONP.bin`` beside the main XML); empty for an
    LV9+ VI (empty CONP) or when the sidecar is absent."""
    if not main_xml:
        return {}
    conp_bin = conp_sidecar_path(main_xml)
    if not conp_bin.exists():
        return {}
    return {
        t.slot: t.name
        for t in decode_conp_terminals(conp_bin.read_bytes())
        if t.name
    }


def _recover_or_warn_unresolved_labels(
    front_panel: ParsedFrontPanel,
    connector_pane: ParsedConnectorPane | None,
    main_xml: Path | str | None,
    vi_label: str,
    unresolved_uids: set[str],
) -> None:
    """Second chance for a control whose own FP-heap label didn't resolve:
    if it's wired to a connector-pane slot, try that slot's VCTP flat-type
    ``Label`` (an independent copy of the name -- see
    ``front_panel.parse_connector_pane_labels``). Only controls that are
    STILL unresolved after that fall back to the ``control_<uid>``
    placeholder, and only THEN do we log -- never silently.

    Sorted iteration over ``unresolved_uids`` keeps this deterministic
    (a set has no stable order) so warning output is byte-reproducible.
    """
    slot_by_uid: dict[str, int] = {}
    if connector_pane:
        for slot in connector_pane.slots:
            if slot.fp_dco_uid:
                slot_by_uid[slot.fp_dco_uid] = slot.index

    labels_by_slot: dict[int, str] = {}
    if main_xml and slot_by_uid:
        labels_by_slot = parse_connector_pane_labels(main_xml)

    # Pre-LV9 VIs have no VCTP flat-type labels (no VCTP at all); their
    # connector-pane terminal names live in CONP — the same block that carries
    # their types (see conp_types). Keyed by the same connector-pane slot, so it
    # is only useful (and only worth decoding) when there IS a connector pane.
    conp_names_by_slot: dict[int, str] = {}
    if main_xml and slot_by_uid:
        conp_names_by_slot = _conp_names_by_slot(main_xml)

    uid_to_control = _uid_to_control_map(front_panel.controls)

    for uid in sorted(unresolved_uids):
        control = uid_to_control.get(uid)
        if control is None:
            continue

        slot_index = slot_by_uid.get(uid)
        recovered = (
            labels_by_slot.get(slot_index)
            or conp_names_by_slot.get(slot_index)
        ) if slot_index is not None else None
        if recovered:
            control.name = recovered
            continue

        logger.warning(
            "%s: control uid=%s has no resolvable label (own partID=16/82 "
            "text empty or absent, and no VCTP flat-type Label either) — "
            "falling back to 'control_%s'",
            vi_label, uid, uid,
        )


def _parse_ddo(
    ddo: ET.Element,
    uid: str,
    indicator_dco_uids: set[str],
    default_data: str | None = None,
    unresolved_uids: set[str] | None = None,
) -> ParsedFPControl | None:
    """Parse a data display object (ddo) into a ParsedFPControl."""
    control_type = ddo.get("class", "unknown")
    if unresolved_uids is None:
        unresolved_uids = set()

    # For typeDef, look inside for the actual control
    if control_type == "typeDef":
        inner_ddo = None
        for child in ddo.findall(".//*"):
            child_class = child.get("class", "")
            if child_class.startswith("std"):
                inner_ddo = child
                break
        if inner_ddo is not None:
            name = extract_label(ddo) or _placeholder_control_name(
                uid, unresolved_uids,
            )
            inner_control = _parse_ddo(
                inner_ddo, uid, indicator_dco_uids, default_data,
                unresolved_uids=unresolved_uids,
            )
            if inner_control:
                inner_control.name = name
                return inner_control
        return None

    # Get bounds
    bounds_elem = ddo.find("bounds")
    if bounds_elem is not None and bounds_elem.text:
        bounds = _parse_bounds(bounds_elem.text)
    else:
        bounds = (0, 0, 100, 200)

    # Get label/name
    name = extract_label(ddo) or _placeholder_control_name(uid, unresolved_uids)

    # Determine if indicator
    if indicator_dco_uids:
        control_is_indicator = uid in indicator_dco_uids
    else:
        flags = safe_int(ddo.find("objFlags"))
        control_is_indicator = is_indicator(flags)

    # Parse children for clusters
    children = []
    if control_type == "stdClust":
        for child_elem in ddo.findall(".//*"):
            child_class = child_elem.get("class", "")
            if child_class.startswith("std") and child_class != "stdClust":
                child_uid = child_elem.get("uid", "")
                if child_uid:
                    child_control = _parse_ddo(
                        child_elem, child_uid, set(), None,
                        unresolved_uids=unresolved_uids,
                    )
                    if child_control:
                        children.append(child_control)

    return ParsedFPControl(
        uid=uid,
        name=name,
        control_type=control_type,
        bounds=bounds,
        is_indicator=control_is_indicator,
        default_value=default_data,
        children=children,
    )


def _decode_default_data(
    raw_data: str,
    control_type: str,
    lv_type: LVType | None = None,
) -> str | None:
    """Decode DefaultData from FPHb XML to a Python literal.

    Uses _decode_element (the single type-aware decoder) when lv_type
    is available. Falls back to control_type dispatch only when no
    type info exists.
    """
    if not raw_data:
        return None

    try:
        raw_bytes = decode_xml_entities_to_bytes(raw_data)
    except (ValueError, UnicodeError):
        return None

    # Use the type-aware decoder when we have type info
    if lv_type is not None:
        decoded, _ = _decode_element(raw_bytes, lv_type)
        if decoded is not None:
            return decoded

    # Fallback: dispatch by control_type string (no type info)
    if raw_bytes.startswith(b'PTH0'):
        return _decode_path_default(raw_bytes)
    if control_type == "stdString" and len(raw_bytes) >= 4:
        return _decode_string_default(raw_bytes)
    if control_type in ("stdNumeric", "stdNum"):
        return _decode_numeric_default(raw_bytes)
    if control_type == "stdBool" and len(raw_bytes) == 1:
        return "True" if raw_bytes[0] else "False"

    return None


def _walk_path(data: bytes) -> tuple[str | None, int]:
    """Walk a ``PTH0`` path DefaultData blob ONCE -> ``(value, bytes_consumed)``.

    The single source for both the path string AND the byte count, so a path
    field inside a cluster stays aligned with the following field (previously the
    value and the consumed count were walked by two divergent loops). Returns
    ``(None, 0)`` when ``data`` is not a ``PTH0`` blob.
    """
    if not data.startswith(b'PTH0'):
        return None, 0
    idx = 12  # PTH0 header
    parts: list[str] = []
    try:
        while idx < len(data):
            str_len = data[idx]
            idx += 1
            if str_len == 0 or idx + str_len > len(data):
                break
            parts.append(decode_labview_text(data[idx : idx + str_len]))
            idx += str_len
    except (IndexError, ValueError):
        pass
    return (f'Path("{"/".join(parts)}")' if parts else None), idx


def _decode_path_default(data: bytes) -> str | None:
    """Decode a LabVIEW path from DefaultData bytes (value only)."""
    return _walk_path(data)[0]


def _decode_string_default(data: bytes) -> str | None:
    """Decode a LabVIEW string from DefaultData bytes."""
    try:
        if len(data) < 4:
            return None
        length = int.from_bytes(data[:4], 'big')
        if len(data) >= 4 + length:
            string_val = decode_labview_text(data[4 : 4 + length])
            escaped = string_val.replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'
    except ValueError:
        pass
    return None


def _decode_numeric_default(data: bytes) -> str | None:
    """Decode a numeric value from DefaultData bytes (no type info -> width is
    inferred from the byte count: 1/2/4 -> integer, 8 -> float-or-integer)."""
    try:
        if len(data) in (1, 2, 4):
            return str(int.from_bytes(data, 'big', signed=True))
        elif len(data) == 8:
            try:
                float_val = struct.unpack('>d', data)[0]
                if float_val == int(float_val):
                    return str(int(float_val))
                return str(float_val)
            except struct.error:
                return str(int.from_bytes(data, 'big', signed=True))
    except ValueError:
        pass
    return None


def _decode_element(data: bytes, elem_type: LVType | None) -> tuple[str | None, int]:
    """Decode a single element and return (value, bytes_consumed).

    Handles all LabVIEW types recursively: primitives, enums,
    arrays (with element_type), and clusters (with fields).

    Args:
        data: Bytes starting at this element
        elem_type: Type of the element

    Returns:
        Tuple of (decoded value string, number of bytes consumed)
    """
    if not elem_type or len(data) == 0:
        return None, 0

    underlying = elem_type.underlying_type or ""
    kind = elem_type.kind

    # String (and Tag, which is string-encoded): 4-byte length prefix + data
    if underlying in ("String", "Tag"):
        if len(data) < 4:
            return None, 0
        str_len = int.from_bytes(data[:4], 'big')
        if len(data) < 4 + str_len:
            return None, 0
        string_val = decode_labview_text(data[4 : 4 + str_len])
        escaped = string_val.replace('\\', '\\\\').replace("'", "\\'")
        return f"'{escaped}'", 4 + str_len

    # Boolean: 1 byte in binary data
    if underlying == "Boolean":
        return ("True" if data[0] else "False"), 1

    # Enum: decode as its underlying integer type
    if kind == "enum":
        size = _get_numeric_size(underlying)
        if len(data) < size:
            return None, 0
        val = int.from_bytes(data[:size], 'big')
        return str(val), size

    # Numeric integer types
    if underlying.startswith("NumInt") or underlying.startswith("NumUInt"):
        size = _get_numeric_size(underlying)
        if len(data) < size:
            return None, 0
        signed = underlying.startswith("NumInt")
        val = int.from_bytes(data[:size], 'big', signed=signed)
        return str(val), size

    # Float and complex types. Widths are load-bearing: a complex is TWO
    # components (NumComplex64 = 2x f32 = 8 bytes, NumComplex128 = 2x f64 = 16),
    # and extended floats are 16/32 — under-reading them (the old code read every
    # non-f32 as 8) desyncs the following cluster field.
    if underlying.startswith(("NumFloat", "NumComplex")):
        width = {
            "NumFloat32": 4, "NumFloat64": 8, "NumFloatExt": 16,
            "NumComplex64": 8, "NumComplex128": 16, "NumComplexExt": 32,
        }.get(underlying)
        if width is None or len(data) < width:
            return None, 0
        try:
            if underlying == "NumFloat32":
                val = str(struct.unpack('>f', data[:4])[0])
            elif underlying == "NumFloat64":
                val = str(struct.unpack('>d', data[:8])[0])
            elif underlying == "NumComplex64":
                re, im = struct.unpack('>ff', data[:8])
                val = str(complex(re, im))
            elif underlying == "NumComplex128":
                re, im = struct.unpack('>dd', data[:16])
                val = str(complex(re, im))
            else:  # extended-precision (80/128-bit) — width known, no struct fmt
                val = f"<{underlying}>"
        except struct.error:
            return None, 0
        return val, width

    # Path: PTH0 prefix — one walk yields both the value and the byte count.
    if underlying == "Path":
        value, consumed = _walk_path(data)
        return value or 'Path("")', consumed

    # Array: 4-byte length + elements
    if kind == "array" and elem_type.element_type:
        if len(data) < 4:
            return None, 0
        array_len = int.from_bytes(data[:4], 'big')
        idx = 4
        elements = []
        for _ in range(array_len):
            if idx >= len(data):
                break
            elem_val, consumed = _decode_element(
                data[idx:], elem_type.element_type,
            )
            if elem_val is None:
                break
            # No forward progress (e.g. an undecodable element that returns
            # consumed=0, like a non-PTH0 Path) means the remaining elements
            # can never be decoded either — stop instead of spinning array_len
            # times over the same offset (a misparsed length made this a
            # 290M-iteration runaway that dominated whole-repo index builds).
            # Logged so we can measure how often this fires and fix the ROOT
            # parse cause (why the length/element type is wrong).
            if consumed <= 0:
                logger.warning(
                    "constant-array decode truncated: %d of a claimed %d "
                    "elements decoded before an element (type %r) consumed 0 "
                    "bytes at offset %d — likely a misparsed array length or "
                    "element type; the root parse cause is worth fixing.",
                    len(elements), array_len,
                    getattr(elem_type.element_type, "underlying_type", None)
                    or getattr(elem_type.element_type, "kind", "?"),
                    idx,
                )
                break
            elements.append(elem_val)
            idx += consumed
        return "[" + ", ".join(elements) + "]", idx

    # Cluster: sequential fields
    if kind == "cluster" and elem_type.fields:
        idx = 0
        field_values = {}
        for field in elem_type.fields:
            if idx >= len(data):
                break
            field_val, consumed = _decode_element(
                data[idx:], field.type,
            )
            if field_val is None:
                field_values[field.name] = "None"
            else:
                field_values[field.name] = field_val
            idx += consumed
        items = [f"'{k}': {v}" for k, v in field_values.items()]
        return "{" + ", ".join(items) + "}", idx

    # Refnum: a 4-byte opaque handle. A CLASS/LVObject refnum reads as its CLASS
    # NAME (from the resolved type), never the handle — the handle is a
    # meaningless runtime cookie, and a class ref's on-disk value even embeds the
    # class path AFTER the 4 bytes (a marker byte + two length-prefixed strings +
    # padding, whose alignment isn't reliably derivable), which this fixed 4-byte
    # read does NOT consume. The label comes from the TYPE, so it's right
    # regardless of that under-read; ``size`` stays 4 (unchanged) so cluster
    # field decoding is not perturbed. A plain typed refnum keeps its handle
    # token. Same class-name rule as render.style.lv_type_label.
    if underlying == "Refnum":
        if elem_type.classname:
            # A CLASS/LVObject refnum's on-disk value is a class-name descriptor:
            #   [4-byte class-chain count N][ N x [1-byte block-len]
            #                                     [1-byte name-len][name] ]
            # where block-len is self-inclusive (= 2 + name-len). Consuming only
            # a fixed 4 bytes (the old behavior) left [block-len][name-len][name]
            # in place, which a FOLLOWING cluster field then misread — e.g. an
            # array field read the class-name bytes as a 290M length (the decode
            # runaway). Parse the descriptor so cluster fields stay aligned.
            # Verified across TestCase/TestResult/TestSuite/JUnitXML/LVObject.
            name = elem_type.classname.rsplit(":", 1)[-1]
            if len(data) >= 4:
                n = int.from_bytes(data[:4], 'big')
                idx = 4
                if 0 <= n <= 16:  # a real class chain is short; guards garbage
                    for _ in range(n):
                        if idx >= len(data):
                            break
                        blk = data[idx]  # self-inclusive block length (2+namelen)
                        if blk < 2 or idx + blk > len(data):
                            idx = 4  # malformed descriptor — fall back to 4
                            break
                        idx += blk
                    return name, idx
            return name, min(4, len(data))
        size = min(4, len(data))
        val = int.from_bytes(data[:size], 'big')
        return f"Refnum({val})" if val else "None", size

    # LVVariant: opaque — just report the byte count
    if underlying in ("LVVariant", "Variant"):
        return "Variant()", len(data)

    # MeasureData (timestamp): 16 bytes (8 int + 8 frac)
    if underlying == "MeasureData":
        if len(data) >= 16:
            secs = int.from_bytes(data[:8], 'big', signed=True)
            return f"Timestamp({secs})", 16
        return "Timestamp(0)", len(data)

    return None, 0


def _get_numeric_size(type_name: str) -> int:
    """Byte size for a numeric/enum type name, read from its width digit."""
    if "8" in type_name:
        return 1
    elif "16" in type_name:
        return 2
    elif "32" in type_name:
        return 4
    elif "64" in type_name:
        return 8
    # No width digit — e.g. the reconstructors' bare "Enum"/"Ring", which carry
    # no ordinal width. Warn rather than SILENTLY assume 4 bytes and desync a
    # default-data decode (VCTP enums arrive as "UnitUInt*", so this is a trap
    # for the newer convention, not a path hit today).
    logger.warning(
        "no width digit in numeric/enum type name %r — assuming 4 bytes; a "
        "default-data decode of this type may misalign", type_name,
    )
    return 4

