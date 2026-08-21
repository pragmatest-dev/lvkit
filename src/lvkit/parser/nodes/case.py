"""Case structure parsing."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from lvkit.models import CaseFrame, SelectorRange, Tunnel
from lvkit.text_encoding import decode_labview_text

from ..constants import TERMINAL_CLASS
from ..models import ParsedCaseStructure, ParsedTerminalInfo, SelectorTable
from .base import (
    extract_tunnel_mapping,
    frame_inner_node_uids,
    parse_displayed_frame,
)
from .disable import is_structure_boundary

# Tunnel DCO classes used in case structures
CASE_TUNNEL_CLASSES = ("csTun",)  # Case structure tunnel

# Selector DCO classes
SELECTOR_DCO_CLASSES = ("cSelDCO", "caseSel")

# All tunnel DCO classes
ALL_TUNNEL_CLASSES = ("csTun", "selTun", "commentTun")

# objFlags bit marking a string case structure as "Case Insensitive Match".
_CASE_INSENSITIVE_BIT = 24


def _objflags_bit(elem: ET.Element, bit: int) -> bool:
    """Whether ``elem``'s ``objFlags`` integer has ``bit`` set."""
    of = elem.findtext("objFlags")
    if of and of.lstrip("-").isdigit():
        return bool(int(of) >> bit & 1)
    return False


def _find_own_descendants(
    elem: ET.Element,
    class_name: str,
) -> list[ET.Element]:
    """Find elements with class_name, stopping at nested structure boundaries.

    Walks the XML subtree but does NOT recurse into nested structure elements
    (caseStruct, select, forLoop, a nested Disable structure, etc.), so only
    elements belonging to THIS structure are returned.
    """
    results: list[ET.Element] = []

    def _walk(e: ET.Element) -> None:
        for child in e:
            child_class = child.get("class", "")
            if child_class == class_name:
                results.append(child)
            # Stop at nested structure boundaries
            if not is_structure_boundary(child):
                _walk(child)

    _walk(elem)
    return results


def extract_case_structures(
    root: ET.Element,
    terminal_info: dict[str, ParsedTerminalInfo] | None = None,
    selector_tables: list[SelectorTable] | None = None,
) -> list[ParsedCaseStructure]:
    """Extract case structures with frame mappings.

    Handles both class='caseStruct' and class='select' elements.

    Args:
        root: XML root element
        terminal_info: Terminal info dict (uid → ParsedTerminalInfo) for type lookup
        selector_tables: Stored selector-value tables from the dataspace DFDS
            (parsed from the main ``*.xml``). When present and consistent, they
            supply the real per-frame selector values that are absent from the
            block-diagram heap.

    Returns:
        List of ParsedCaseStructure with frame mappings
    """
    case_structures: list[ParsedCaseStructure] = []

    # Find caseStruct and select elements
    case_elems = list(root.findall(".//*[@class='caseStruct']"))
    case_elems.extend(root.findall(".//*[@class='select']"))

    for case_elem in case_elems:
        case_uid = case_elem.get("uid")
        if not case_uid:
            continue

        cs = _extract_one_case_structure(case_elem, case_uid, terminal_info)
        if cs:
            case_structures.append(cs)

    if selector_tables:
        _apply_selector_tables(case_structures, selector_tables)

    return case_structures


def _extract_one_case_structure(
    case_elem: ET.Element,
    case_uid: str,
    terminal_info: dict[str, ParsedTerminalInfo] | None = None,
) -> ParsedCaseStructure | None:
    """Extract a single case structure from an XML element."""
    selector_terminal_uid: str | None = None
    selector_type: str | None = None
    selector_vctp_index: int | None = None
    frames: list[CaseFrame] = []
    tunnels: list[Tunnel] = []

    # Case Insensitive Match (string selectors): the select node's objFlags
    # bit 24. Default matching is case-SENSITIVE (bit clear); LabVIEW 2015+
    # shows an "A=a" badge at the bottom-left when this is enabled. Verified
    # against the sample corpus: set only on the one string case that must
    # match case-insensitively (Draw Image's file-extension switch, which
    # has no lowercasing before it), clear on the empty-vs-nonempty string
    # checks where case is irrelevant.
    case_insensitive = _objflags_bit(case_elem, _CASE_INSENSITIVE_BIT)

    # Count frames first (needed for selTun expansion)
    diag_list = case_elem.find("diagramList")
    num_frames = 0
    if diag_list is not None:
        num_frames = len(diag_list.findall("SL__arrayElement[@class='diag']"))

    # Find selector terminal and tunnels
    term_list_elem = case_elem.find("termList")
    if term_list_elem is not None:
        for term_elem in term_list_elem.findall(
            f"SL__arrayElement[@class='{TERMINAL_CLASS}']"
        ):
            term_uid = term_elem.get("uid")

            # Check for selector DCO
            if term_uid and selector_terminal_uid is None:
                for sel_cls in SELECTOR_DCO_CLASSES:
                    dco = term_elem.find(f"dco[@class='{sel_cls}']")
                    if dco is not None:
                        selector_terminal_uid = term_uid
                        selector_type = _infer_selector_type(dco)
                        selector_vctp_index = _parse_type_id(
                            dco.findtext("typeDesc"),
                        )
                        break

            # Check for tunnel DCO
            dco = term_elem.find("dco")
            if dco is not None:
                dco_class = dco.get("class", "")
                if dco_class in ALL_TUNNEL_CLASSES:
                    new_tunnels = _extract_case_tunnels(
                        dco,
                        dco_class,
                        term_uid,
                        num_frames,
                    )
                    tunnels.extend(new_tunnels)

    # Extract caseSel tunnels from sRN nodes inside this case's diagrams.
    # These route shift register values across the case boundary.
    # caseSel termList: [...inner_per_frame..., outer_structural]
    # IMPORTANT: use _find_own_descendants to avoid picking up caseSel
    # elements from nested case structures.
    for case_sel in _find_own_descendants(case_elem, "caseSel"):
        cs_tl = case_sel.find("termList")
        if cs_tl is not None:
            term_refs: list[str] = [
                uid for e in cs_tl.findall("SL__arrayElement") if (uid := e.get("uid"))
            ]
            if len(term_refs) >= 2:
                outer_uid = term_refs[-1]
                for inner_uid in term_refs[:-1]:
                    tunnels.append(
                        Tunnel(
                            outer_terminal_uid=outer_uid,
                            inner_terminal_uid=inner_uid,
                            tunnel_type="caseSel",
                        )
                    )

    # Extract commentTun tunnels from comment nodes (annotations).
    # commentTun passes data through transparently — same layout as selTun.
    for comment_tun in _find_own_descendants(case_elem, "commentTun"):
        ct_tl = comment_tun.find("termList")
        if ct_tl is not None:
            term_refs: list[str] = [
                uid for e in ct_tl.findall("SL__arrayElement") if (uid := e.get("uid"))
            ]
            if len(term_refs) >= 2:
                outer_uid = term_refs[-1]
                for inner_uid in term_refs[:-1]:
                    tunnels.append(
                        Tunnel(
                            outer_terminal_uid=outer_uid,
                            inner_terminal_uid=inner_uid,
                            tunnel_type="commentTun",
                        )
                    )

    # Resolve selector type from the terminal's actual wire type.
    # This is the source of truth — overrides the DCO-based guess.
    if selector_terminal_uid and terminal_info:
        ti = terminal_info.get(selector_terminal_uid)
        if ti and ti.parsed_type:
            selector_type = _type_name_to_selector_type(
                ti.parsed_type.type_name,
            )

    # Extract selector ranges from SelectRangeArray32, keyed by diagramIdx.
    # A frame can match SEVERAL ranges (e.g. ``1, 3, 5..8``); LabVIEW stores
    # each as start/end (a single value has start == end), plus a
    # startRangeType/endRangeType per endpoint. Type 0 means the endpoint is a
    # real literal value; a nonzero type means the endpoint is SYMBOLIC and
    # the numeric start/end is filler (LabVIEW writes INT_MIN/INT_MAX) — this
    # is how error-cluster frames encode "No Error" (a symbolic degenerate
    # point) and the catch-all "Error"/default frame (a symbolic full-domain
    # span). We preserve literal ranges verbatim — the label is reconstructed
    # from ranges, there is no stored label — and resolve symbolic ranges
    # below instead of surfacing their filler numbers.
    ranges_by_diag: dict[int, list[SelectorRange]] = {}
    no_error_diags: set[int] = set()
    default_symbolic_diags: set[int] = set()
    raw_ranges_by_diag: dict[int, list[tuple[int, int, int, int]]] = {}
    select_range = case_elem.find("SelectRangeArray32")
    if select_range is not None:
        for sr_elem in select_range.findall("SL__arrayElement[@class='SelectorRange']"):
            start = sr_elem.findtext("start")
            end = sr_elem.findtext("end")
            diag_idx = sr_elem.findtext("diagramIdx")
            if start is None or diag_idx is None:
                continue
            start_type = sr_elem.findtext("startRangeType")
            end_type = sr_elem.findtext("endRangeType")
            raw_ranges_by_diag.setdefault(int(diag_idx), []).append(
                (
                    int(start),
                    int(end) if end is not None else int(start),
                    int(start_type) if start_type is not None else 0,
                    int(end_type) if end_type is not None else 0,
                )
            )

    for diag_idx, entries in raw_ranges_by_diag.items():
        if all(st == 0 and et == 0 for _, _, st, et in entries):
            # Fully-literal frame — every endpoint is a real value. This is
            # the common case (enum/int/bool/string); behavior unchanged.
            ranges_by_diag[diag_idx] = [
                SelectorRange(start=s, end=e) for s, e, _, _ in entries
            ]
            continue

        if len(entries) == 1:
            s, e, st, et = entries[0]
            if st != 0 and et != 0:
                if s == e:
                    # Symbolic degenerate point — error-cluster "No Error".
                    no_error_diags.add(diag_idx)
                else:
                    # Symbolic full-domain span — the catch-all
                    # "Error"/default frame.
                    default_symbolic_diags.add(diag_idx)
                continue
            if (st != 0) != (et != 0):
                # One-sided symbolic — a genuine open integer range. Keep
                # only the literal endpoint's value; the symbolic side is
                # filler and must never be surfaced.
                ranges_by_diag[diag_idx] = [
                    SelectorRange(
                        start=s,
                        end=e,
                        open_start=st != 0,
                        open_end=et != 0,
                    )
                ]
                continue

        # Defensive fallback for any other combination (not observed in the
        # corpus): preserve the literal numbers, matching prior behavior.
        ranges_by_diag[diag_idx] = [
            SelectorRange(start=s, end=e) for s, e, _, _ in entries
        ]

    # For string selectors, the start values in SelectRangeArray32 are
    # indices into SelectStringArray (hex-encoded string labels).
    string_labels: list[str] = []
    if selector_type == "string":
        ssa = case_elem.find("SelectStringArray")
        if ssa is not None:
            for item in ssa.findall("SL__arrayElement"):
                hex_text = item.text or ""
                try:
                    string_labels.append(decode_labview_text(bytes.fromhex(hex_text)))
                except ValueError:
                    string_labels.append(hex_text)

    # Detect default case: SelectDefaultCase holds the hex diagram index of
    # the default frame (FF = none). When it is absent/FF but a diagram has
    # NO selector range, that diagram IS the implicit default (it catches all
    # values the explicit frames don't) — LabVIEW labels it "Default". Missing
    # this is what made non-boolean default frames fall through to "False".
    default_diag_idx: int | None = None
    default_case_elem = case_elem.findtext("SelectDefaultCase")
    if default_case_elem and default_case_elem.upper() != "FF":
        try:
            default_diag_idx = int(default_case_elem, 16)
        except ValueError:
            pass
    if default_diag_idx is None and default_symbolic_diags:
        # A symbolic full-domain range (rule 3 above) IS the default frame —
        # fold it in before the "missing range" heuristic below, since a
        # symbolic frame has no entry in ``ranges_by_diag`` either and would
        # otherwise be indistinguishable from a No-Error frame there.
        default_diag_idx = next(iter(default_symbolic_diags))
    if default_diag_idx is None:
        handled = set(ranges_by_diag) | no_error_diags | default_symbolic_diags
        missing = [i for i in range(num_frames) if i not in handled]
        if missing:
            default_diag_idx = missing[0]

    # Extract diagram frames (cases)
    if diag_list is not None:
        for idx, diag_elem in enumerate(
            diag_list.findall("SL__arrayElement[@class='diag']")
        ):
            is_default = idx == default_diag_idx or idx in default_symbolic_diags
            ranges = ranges_by_diag.get(idx, [])
            resolved_selector: str | None = None
            if idx in no_error_diags:
                # Symbolic degenerate point → the canonical No-Error value
                # that ``op_walk.is_no_error_selector``/``_selector_label``
                # already recognize (renders "No Error", green border).
                resolved_selector = "0"
            elif not is_default and ranges:
                sv = ranges[0].end if ranges[0].open_start else ranges[0].start
                if selector_type == "boolean":
                    resolved_selector = "True" if sv == 1 else "False"
                elif selector_type == "string" and sv < len(string_labels):
                    resolved_selector = string_labels[sv]
                else:
                    # Integer, enum, error — semantic identity is the raw
                    # first-range start (or, for an open-start range, the
                    # literal end); faithful display is built later from
                    # ``selector_ranges`` against the resolved selector type.
                    resolved_selector = str(sv)

            frame = _extract_frame(
                diag_elem,
                idx,
                resolved_selector,
                is_default,
                selector_type,
            )
            if frame:
                # Ranges are display metadata for numeric/enum selectors; a
                # boolean/string frame's ``selector_value`` already is the
                # display token, and the default/no-error frame has no range.
                if not is_default and selector_type not in ("boolean", "string"):
                    frame.selector_ranges = ranges
                frames.append(frame)

    return ParsedCaseStructure(
        uid=case_uid,
        selector_terminal_uid=selector_terminal_uid,
        selector_type=selector_type,
        selector_vctp_index=selector_vctp_index,
        # Case Insensitive Match only applies to string selectors.
        case_insensitive=case_insensitive and selector_type == "string",
        frames=frames,
        tunnels=tunnels,
        # The displayed frame from the case node's own heap ``dIdx`` (range-
        # checked against the frame list the renderer indexes). This is the
        # reliable FALLBACK: the dataspace selector-table correlation
        # (_apply_selector_tables) OVERRIDES it when it succeeds, but aborts on
        # any case/table count mismatch -- leaving this value, which is why
        # boolean cases (no table) and multi-case VIs still open on the right
        # frame. See issue #30.
        displayed_frame=parse_displayed_frame(case_elem, len(frames)),
    )


def _extract_case_tunnels(
    dco: ET.Element,
    dco_class: str,
    outer_terminal_uid: str | None,
    num_frames: int,
) -> list[Tunnel]:
    """Extract tunnel(s) from a case structure DCO.

    For csTun: simple [inner, outer] layout → 1 Tunnel.
    For selTun: per-frame layout [frame0_inner, frame1_inner, ..., outer]
    → one Tunnel per frame.
    """
    if dco_class == "csTun":
        return extract_tunnel_mapping(dco, dco_class)

    # selTun: per-frame inner terminals
    dco_term_list = dco.find("termList")
    if dco_term_list is None:
        return []

    term_refs: list[str] = [
        uid for e in dco_term_list.findall("SL__arrayElement") if (uid := e.get("uid"))
    ]

    # Layout: [frame0_inner, frame1_inner, ..., outer_self]
    # Last ref is the outer terminal (same as the parent terminal UID)
    if len(term_refs) < 2:
        return []

    outer_uid = term_refs[-1]  # Last is outer
    inner_refs = term_refs[:-1]  # Rest are per-frame inners

    tunnels = []
    for inner_uid in inner_refs:
        tunnels.append(
            Tunnel(
                outer_terminal_uid=outer_uid,
                inner_terminal_uid=inner_uid,
                tunnel_type=dco_class,
            )
        )

    return tunnels


def _extract_frame(
    diag_elem: ET.Element,
    index: int,
    selector_value: str | None = None,
    is_default: bool = False,
    selector_type: str | None = None,
) -> CaseFrame | None:
    """Extract a single case frame from a diagram element.

    Args:
        diag_elem: Diagram element containing the case operations
        index: Index of the frame in the diagramList
        selector_value: Pre-resolved selector value from SelectRangeArray
        is_default: Whether this frame is the default case
        selector_type: Resolved selector category ("boolean", "string", ...);
            gates the boolean fallback so non-boolean cases don't get fake
            True/False tokens (the real values arrive via the dataspace
            SelectorTable correlation)

    Returns:
        Frame or None if invalid
    """
    # Use pre-resolved selector value when available
    if not selector_value:
        # Fallback: try diagram element's selStr attribute
        selector_value = diag_elem.get("selStr", "")

    if not selector_value:
        sel_str_elem = diag_elem.find("selStr")
        if sel_str_elem is not None and sel_str_elem.text:
            selector_value = sel_str_elem.text

    # Last resort: no stored label in the heap. For a boolean selector the
    # frames ARE False/True by index; for any other type, emitting True/False
    # is wrong (that was the #82 bug), so use the frame index as a neutral,
    # unique placeholder — the dataspace SelectorTable overrides it when it
    # correlates.
    if not selector_value:
        if selector_type == "boolean" or selector_type is None:
            selector_value = "True" if index == 1 else "False"
        else:
            selector_value = str(index)

    if is_default:
        selector_value = "Default"

    # Operations directly on this frame's diagram (nodeList) PLUS any structure
    # that LabVIEW lists only in the diagram's zPlaneList (a nested flat sequence
    # box lives there, not in nodeList — see frame_inner_node_uids). We do NOT
    # recurse into nested structures — each parses its own frames.
    inner_node_uids = frame_inner_node_uids(diag_elem)

    return CaseFrame(
        selector_value=selector_value,
        inner_node_uids=inner_node_uids,
        is_default=is_default,
    )


def _type_name_to_selector_type(type_name: str) -> str | None:
    """Map a ParsedType.type_name to a selector type category.

    Args:
        type_name: From ParsedTerminalInfo.parsed_type.type_name
            e.g. "Boolean", "String", "NumInt32", "Enum", "Cluster"

    Returns:
        "boolean", "integer", "string", "enum", "error", or None
    """
    tn = type_name.lower()
    if tn == "boolean":
        return "boolean"
    if tn == "string":
        return "string"
    if tn.startswith("num") or tn in ("i32", "u32", "i16", "u16", "i8", "u8"):
        return "integer"
    if "enum" in tn:
        return "enum"
    if tn == "cluster":
        # Error cluster selector
        return "error"
    return None


def _parse_type_id(type_desc: str | None) -> int | None:
    """Parse the integer from a ``typeDesc`` text like ``TypeID(42)``."""
    if not type_desc:
        return None
    m = re.search(r"TypeID\((\d+)\)", type_desc)
    return int(m.group(1)) if m else None


def parse_selector_tables(main_root: ET.Element) -> list[SelectorTable]:
    """Extract case-structure selector-value tables from the dataspace XML.

    LabVIEW stores each case structure's per-frame selector values once, in the
    default-data space (the main ``*.xml``), as a ``DataFill`` with the
    :class:`SelectorTable` cluster shape. pylabview emits each such default
    twice (an edit-time and a run-time copy); we deduplicate by content and
    return the surviving tables sorted by ``DataFill`` ``TypeID`` — the order in
    which LabVIEW assigned them, matching the case structures' selector-type
    VCTP order (see :func:`_apply_selector_tables`).
    """
    tables: list[SelectorTable] = []
    seen: set[tuple[object, ...]] = set()
    for df in main_root.iter("DataFill"):
        tid = df.get("TypeID")
        cluster = df.find("Cluster")
        if tid is None or cluster is None:
            continue
        table = _decode_selector_table(int(tid), cluster)
        if table is None:
            continue
        key = (tuple(table.ranges), tuple(table.strings))
        if key in seen:
            continue
        seen.add(key)
        tables.append(table)
    tables.sort(key=lambda t: t.type_id)
    return tables


def _decode_selector_table(
    type_id: int,
    cluster: ET.Element,
) -> SelectorTable | None:
    """Decode one ``DataFill`` cluster into a SelectorTable, or None if it does
    not have the selector-table shape."""
    kids = list(cluster)
    # Shape: I32 displayed_frame, I32 range_count, Array ranges, Array strings, ...
    if len(kids) < 4:
        return None
    if kids[0].tag != "I32" or kids[1].tag != "I32":
        return None
    if kids[2].tag != "Array" or kids[3].tag != "Array":
        return None
    range_clusters = kids[2].findall("Cluster")
    ranges: list[tuple[int, int, int]] = []
    for rc in range_clusters:
        fields = list(rc)
        if [f.tag for f in fields] != ["I32", "I32", "U8", "U8", "I16"]:
            return None
        start = int(fields[0].text or "0")
        end = int(fields[1].text or "0")
        diag = int(fields[4].text or "0")
        ranges.append((start, end, diag))
    # A genuine selector table always has at least one range.
    if not ranges:
        return None
    strings = [s.text or "" for s in kids[3].findall("String")]
    displayed = int(kids[0].text or "0")
    return SelectorTable(
        type_id=type_id,
        displayed_frame=displayed,
        ranges=ranges,
        strings=strings,
    )


def _apply_selector_tables(
    cases: list[ParsedCaseStructure],
    tables: list[SelectorTable],
) -> None:
    """Correlate dataspace selector tables to case structures and apply values.

    The correlation is deterministic and self-checking, never a guess: cases
    ordered by their selector-type VCTP index (``selector_vctp_index``) line up
    one-to-one with tables ordered by ``DataFill`` TypeID, because LabVIEW
    assigns both indices in the same DCO-enumeration pass. Boolean cases store
    no table (their True/False frames are implicit), so they are excluded from
    the correlation and keep their existing labels.

    Application only proceeds if the counts match AND every zipped pair is
    kind-consistent (a string table iff a string case) AND every frame index /
    displayed frame lies in range. Any inconsistency aborts the WHOLE
    application (leaving fallback values) rather than risk a wrong label.
    """
    corr_cases = [
        c
        for c in cases
        if c.selector_type != "boolean" and c.selector_vctp_index is not None
    ]
    if len(corr_cases) != len(tables):
        return
    corr_cases.sort(key=lambda c: c.selector_vctp_index or 0)

    # Validate every pair before mutating anything.
    for case, table in zip(corr_cases, tables):
        is_string = case.selector_type == "string"
        if is_string != table.has_strings:
            return
        n_frames = len(case.frames)
        # Full range check (matches the diag check below and
        # parse_displayed_frame) -- a negative displayed_frame is invalid too.
        if not (0 <= table.displayed_frame < n_frames):
            return
        for _start, _end, diag in table.ranges:
            if not (0 <= diag < n_frames):
                return

    for case, table in zip(corr_cases, tables):
        _apply_one_table(case, table)


def _apply_one_table(case: ParsedCaseStructure, table: SelectorTable) -> None:
    """Overwrite a case's frame selector values from its correlated table."""
    case.displayed_frame = table.displayed_frame
    covered: set[int] = {diag for _s, _e, diag in table.ranges}
    for idx, frame in enumerate(case.frames):
        my_ranges = [(s, e) for s, e, d in table.ranges if d == idx]
        if not my_ranges:
            # No value maps here → this is the implicit Default frame.
            frame.is_default = True
            frame.selector_value = "Default"
            frame.selector_ranges = []
            frame.selector_strings = []
            continue
        frame.is_default = idx not in covered  # never, but keep flag honest
        if table.has_strings:
            strings: list[str] = []
            for start, end in my_ranges:
                for i in range(start, end + 1):
                    if 0 <= i < len(table.strings):
                        strings.append(table.strings[i])
            frame.selector_strings = strings
            frame.selector_ranges = []
            frame.selector_value = strings[0] if strings else str(idx)
        else:
            frame.selector_ranges = [
                SelectorRange(start=s, end=e) for s, e in my_ranges
            ]
            frame.selector_strings = []
            first = my_ranges[0]
            frame.selector_value = (
                str(first[0]) if first[0] == first[1] else f"{first[0]}..{first[1]}"
            )


def _infer_selector_type(dco: ET.Element) -> str | None:
    """Fallback: infer selector type from cSelDCO's typeDesc element.

    Used when terminal_info is not available.
    """
    type_elem = dco.find("typeDesc")
    if type_elem is not None:
        type_text = type_elem.text or ""
        type_lower = type_text.lower()

        if "bool" in type_lower:
            return "boolean"
        elif "int" in type_lower or "i32" in type_lower or "u32" in type_lower:
            return "integer"
        elif "enum" in type_lower:
            return "enum"
        elif "string" in type_lower:
            return "string"

    return None
