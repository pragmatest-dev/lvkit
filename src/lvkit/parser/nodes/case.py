"""Case structure parsing."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from enum import Enum, IntEnum

from lvkit.lv_type_codes import (
    TD_FULL_TYPE,
    is_boolean_type_name,
    is_integer_type_name,
    is_string_type_name,
)
from lvkit.models import CaseFrame, LVTypeKind, SelectorRange, Tunnel
from lvkit.text_encoding import decode_labview_text

from ..constants import TERMINAL_CLASS
from ..models import (
    ParsedCaseStructure,
    ParsedTerminalInfo,
    ParsedType,
    SelectorKind,
    SelectorTable,
)
from ..utils import heap_color
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



class RangeEndType(IntEnum):
    """How one end of a ``SelectRangeArray32`` entry reads
    (``startRangeType``/``endRangeType``; the element is omitted when 0).

    ``SINGLE`` is a lone value (both ends equal and untyped). ``BOUNDED`` is a
    literal end of a range (``0..2`` is ``(0, 2, BOUNDED, BOUNDED)``). ``OPEN`` is
    an unbounded side: its stored number is filler (the selector type's limit)
    and is never a match value -- ``0..`` is ``(0, 2147483647, BOUNDED, OPEN)``,
    ``..-1`` is ``(-2147483648, -1, OPEN, BOUNDED)``.
    """

    SINGLE = 0
    BOUNDED = 1
    OPEN = 3


class _SymbolicFrame(Enum):
    """A frame whose range entries carry no literal value of their own."""

    NO_ERROR = "no_error"  # error cluster, status False (one open-ended point)


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
    table_index_shift: int = 0,
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
        table_index_shift: the dataspace type map (TM80) ``IndexShift``. Added to
            a case's ``selector_table_offset`` (its ``tdOffset``) it yields that
            case's DataFill TypeID — the direct case→table link.

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
        _apply_selector_tables(case_structures, selector_tables, table_index_shift)

    for cs in case_structures:
        _apply_last_frame_default(cs)

    return case_structures


def _apply_last_frame_default(cs: ParsedCaseStructure) -> None:
    """Default an integer/string case whose selector values live only in the
    dataspace table (pre-2010 VIs) to its LAST frame.

    That format has no ``SelectDefaultCase``, and an integer/string selector has
    an infinite domain, so the case always has a default frame. When no frame is
    value-less, the default is the last one; it may also carry an explicit value
    ("N, Default"), which codegen's ``case _`` catch-all subsumes. Heap-stored
    cases are skipped: their ``SelectDefaultCase`` is authoritative.
    """
    if cs.selector_values_in_heap or cs.selector_type not in (
        SelectorKind.INTEGER,
        SelectorKind.STRING,
    ):
        return
    if not cs.frames or any(f.is_default for f in cs.frames):
        return
    cs.frames[-1].is_default = True


def _frame_ranges(
    entries: list[tuple[int, int, RangeEndType, RangeEndType]],
) -> list[SelectorRange] | _SymbolicFrame:
    """One frame's ``(start, end, startRangeType, endRangeType)`` entries as
    selector ranges, or the symbolic frame they spell.

    An ``OPEN`` end is unbounded: it becomes ``open_start``/``open_end`` and its
    stored number (filler) is never surfaced. A range open on both sides is the
    whole domain (the error cluster's second frame, labeled ``Error ..``). A
    single entry open on one side whose filler equals its bounded end is the
    error cluster's "No Error" point.
    """
    if len(entries) == 1:
        s, e, st, et = entries[0]
        if s == e and RangeEndType.OPEN in (st, et):
            return _SymbolicFrame.NO_ERROR
    return [
        SelectorRange(
            start=s,
            end=e,
            open_start=st == RangeEndType.OPEN,
            open_end=et == RangeEndType.OPEN,
        )
        for s, e, st, et in entries
    ]


def _extract_one_case_structure(
    case_elem: ET.Element,
    case_uid: str,
    terminal_info: dict[str, ParsedTerminalInfo] | None = None,
) -> ParsedCaseStructure | None:
    """Extract a single case structure from an XML element."""
    selector_terminal_uid: str | None = None
    selector_type: SelectorKind | None = None
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

    # Resolve the selector type from the terminal's wire type.
    if selector_terminal_uid and terminal_info:
        ti = terminal_info.get(selector_terminal_uid)
        if ti and ti.parsed_type:
            selector_type = _parsed_type_to_selector_type(ti.parsed_type)

    # Extract selector ranges from SelectRangeArray32, keyed by diagramIdx.
    # A frame can match SEVERAL ranges (e.g. ``1, 3, 5..8``); LabVIEW stores
    # each as start/end (a single value has start == end), plus a
    # RangeEndType per endpoint. An OPEN endpoint is unbounded and its number is
    # filler (the selector type's limit); error-cluster frames encode "No Error"
    # (one open-ended point) and the catch-all "Error" frame (open on both
    # sides) this way. Literal endpoints are preserved verbatim -- the label is
    # reconstructed from ranges -- and symbolic frames resolve in
    # ``_frame_ranges`` instead of surfacing their filler numbers.
    ranges_by_diag: dict[int, list[SelectorRange]] = {}
    no_error_diags: set[int] = set()
    raw_ranges_by_diag: dict[
        int, list[tuple[int, int, RangeEndType, RangeEndType]]
    ] = {}
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
                    RangeEndType(int(start_type or 0)),
                    RangeEndType(int(end_type or 0)),
                )
            )

    for diag_idx, entries in raw_ranges_by_diag.items():
        frame_ranges = _frame_ranges(entries)
        if frame_ranges is _SymbolicFrame.NO_ERROR:
            no_error_diags.add(diag_idx)
        else:
            ranges_by_diag[diag_idx] = frame_ranges

    # For string selectors, the start values in SelectRangeArray32 are
    # indices into SelectStringArray (hex-encoded string labels).
    string_labels: list[str] = []
    if selector_type == SelectorKind.STRING:
        ssa = case_elem.find("SelectStringArray")
        if ssa is not None:
            for item in ssa.findall("SL__arrayElement"):
                hex_text = item.text or ""
                try:
                    string_labels.append(decode_labview_text(bytes.fromhex(hex_text)))
                except ValueError:
                    string_labels.append(hex_text)

    # SelectDefaultCase is a hex diagram index (FF = none). When the heap stores
    # the selector values, the zero-valued field is omitted: integer/string/enum
    # selectors then default to diagram 0, even when it also carries literal
    # selector ranges. When the values live only in the dataspace table, the heap
    # records no default; ``_apply_one_table`` and ``_apply_last_frame_default``
    # decide it.
    values_in_heap = select_range is not None
    default_diag_idx: int | None = None
    default_case_elem = case_elem.findtext("SelectDefaultCase")
    if default_case_elem and default_case_elem.upper() != "FF":
        try:
            default_diag_idx = int(default_case_elem, 16)
        except ValueError:
            pass
    if (
        values_in_heap
        and not default_case_elem
        and default_diag_idx is None
        and selector_type
        in (SelectorKind.INTEGER, SelectorKind.STRING, SelectorKind.ENUM)
    ):
        default_diag_idx = 0
    if default_diag_idx is None:
        handled = set(ranges_by_diag) | no_error_diags
        missing = [i for i in range(num_frames) if i not in handled]
        if missing:
            default_diag_idx = missing[0]

    # Extract diagram frames (cases)
    if diag_list is not None:
        for idx, diag_elem in enumerate(
            diag_list.findall("SL__arrayElement[@class='diag']")
        ):
            is_default = idx == default_diag_idx
            ranges = ranges_by_diag.get(idx, [])
            resolved_selector: str | None = None
            boolean_value: bool | None = None
            is_no_error = False
            if idx in no_error_diags:
                # Symbolic degenerate point: the "No Error" frame, which
                # matches status False (0).
                resolved_selector = "0"
                is_no_error = True
            elif ranges[:1] and ranges[0].open_start and ranges[0].open_end:
                # The whole domain has no value of its own to key the frame by.
                resolved_selector = None
            elif ranges:
                sv = ranges[0].end if ranges[0].open_start else ranges[0].start
                if selector_type == SelectorKind.BOOLEAN:
                    boolean_value = sv == 1
                    resolved_selector = str(boolean_value)
                elif selector_type == SelectorKind.STRING:
                    strings = _range_strings(ranges, string_labels)
                    resolved_selector = strings[0] if strings else None
                else:
                    is_no_error = selector_type == SelectorKind.ERROR and sv == 0
                    # Integer, enum, error — semantic identity is the raw
                    # first-range start (or, for an open-start range, the
                    # literal end); faithful display is built later from
                    # ``selector_ranges`` against the resolved selector type.
                    resolved_selector = str(sv)
            elif (
                selector_type == SelectorKind.ERROR
                and not values_in_heap
                and num_frames == 2
            ):
                # A LabVIEW 8/9 two-frame error case stores no values: by
                # position, frame 0 is "No Error" and frame 1 is "Error" (status
                # 1), the same way an old Boolean case reads False then True.
                is_no_error = idx == 0
                resolved_selector = str(idx)
                ranges = [] if is_no_error else [SelectorRange(start=1, end=1)]

            frame = _extract_frame(
                diag_elem,
                idx,
                resolved_selector,
                is_default,
                selector_type,
                boolean_value,
                is_no_error,
            )
            if frame:
                # A default frame can also have explicit selector values.
                # Numeric/enum ranges and the catch-all flag are independent.
                if selector_type == SelectorKind.STRING:
                    frame.selector_strings = _range_strings(ranges, string_labels)
                elif selector_type != SelectorKind.BOOLEAN:
                    frame.selector_ranges = ranges
                frames.append(frame)

    # tdOffset: the select node's client index into the dataspace type map;
    # +TM80 IndexShift = this case's DataFill selector-table TypeID (the direct
    # case->table link resolved in _apply_selector_tables). Stored as hex.
    td_offset_text = case_elem.findtext("tdOffset")
    selector_table_offset: int | None = None
    if td_offset_text:
        try:
            selector_table_offset = int(td_offset_text, 16)
        except ValueError:
            selector_table_offset = None

    return ParsedCaseStructure(
        uid=case_uid,
        selector_terminal_uid=selector_terminal_uid,
        selector_type=selector_type,
        selector_vctp_index=selector_vctp_index,
        selector_values_in_heap=values_in_heap,
        selector_table_offset=selector_table_offset,
        # Case Insensitive Match only applies to string selectors.
        case_insensitive=case_insensitive and selector_type == SelectorKind.STRING,
        frames=frames,
        tunnels=tunnels,
        # The displayed frame from the case node's own heap ``dIdx`` (range-
        # checked against the frame list the renderer indexes). A case whose
        # heap stores its selector values omits ``dIdx`` when it is 0, and the
        # heap is authoritative for it; a case whose values live only in the
        # dataspace takes its displayed frame from the correlated selector
        # table (_apply_selector_tables), which overrides this value. See
        # issue #30.
        displayed_frame=parse_displayed_frame(
            case_elem, len(frames), absent_is_zero=values_in_heap
        ),
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
    selector_type: SelectorKind | None = None,
    boolean_value: bool | None = None,
    is_no_error: bool = False,
) -> CaseFrame | None:
    """Extract a single case frame from a diagram element.

    Args:
        diag_elem: Diagram element containing the case operations
        index: Index of the frame in the diagramList
        selector_value: Pre-resolved selector value from SelectRangeArray
        is_default: Whether this frame is the default case
        selector_type: Resolved selector category; a Boolean selector's frames
            are False and True by index when no stored value says otherwise,
            any other type's frames take the frame index as a unique placeholder
            until the dataspace SelectorTable correlation supplies the values
        boolean_value: The value a Boolean selector's frame matches, when its
            stored range gave one
        is_no_error: Whether the stored range makes this the "No Error" frame

    Returns:
        Frame or None if invalid
    """
    # Use pre-resolved selector value when available. ``""`` is a real value (the
    # empty string); only ``None`` means "not resolved".
    if selector_value is None:
        # Fallback: try diagram element's selStr attribute
        selector_value = diag_elem.get("selStr")

    if selector_value is None:
        sel_str_elem = diag_elem.find("selStr")
        if sel_str_elem is not None and sel_str_elem.text:
            selector_value = sel_str_elem.text

    # A Boolean case has a False frame then a True frame.
    if (
        selector_type == SelectorKind.BOOLEAN
        and boolean_value is None
        and not is_default
    ):
        boolean_value = index == 1

    # Last resort: no stored label in the heap. The frame index is a neutral,
    # unique placeholder -- the dataspace SelectorTable overrides it when it
    # correlates.
    if selector_value is None:
        if is_default:
            selector_value = "Default"
        elif boolean_value is not None:
            selector_value = str(boolean_value)
        else:
            selector_value = str(index)

    # Operations directly on this frame's diagram (nodeList) PLUS any structure
    # that LabVIEW lists only in the diagram's zPlaneList (a nested flat sequence
    # box lives there, not in nodeList — see frame_inner_node_uids). We do NOT
    # recurse into nested structures — each parses its own frames.
    inner_node_uids = frame_inner_node_uids(diag_elem)

    return CaseFrame(
        selector_value=selector_value,
        inner_node_uids=inner_node_uids,
        is_default=is_default,
        boolean_value=boolean_value,
        is_no_error=is_no_error,
        bg_color=heap_color(diag_elem.findtext("bgColor")),
    )


def _parsed_type_to_selector_type(parsed_type: ParsedType) -> SelectorKind | None:
    """Map a selector terminal's ``ParsedType`` to its selector kind, or None for
    a type no case selector has."""
    if parsed_type.kind == LVTypeKind.ENUM:
        return SelectorKind.ENUM
    if is_boolean_type_name(parsed_type.type_name):
        return SelectorKind.BOOLEAN
    if is_string_type_name(parsed_type.type_name):
        return SelectorKind.STRING
    if is_integer_type_name(parsed_type.type_name):
        return SelectorKind.INTEGER
    if parsed_type.type_name == TD_FULL_TYPE.Cluster.name:
        # Error cluster selector
        return SelectorKind.ERROR
    return None


def _range_strings(ranges: list[SelectorRange], string_labels: list[str]) -> list[str]:
    """The strings a string-selector frame matches: each range's start..end are
    indices into the case's ``SelectStringArray``."""
    return [
        string_labels[i]
        for r in ranges
        for i in range(r.start, r.end + 1)
        if 0 <= i < len(string_labels)
    ]


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


def _table_fits_case(case: ParsedCaseStructure, table: SelectorTable) -> bool:
    """Whether ``table`` is structurally consistent with ``case`` — same kind
    (a string table iff a string case) and every diagram/displayed index in
    range. A negative ``displayed_frame`` is the "none displayed" sentinel."""
    if (case.selector_type == SelectorKind.STRING) != table.has_strings:
        return False
    n_frames = len(case.frames)
    if table.displayed_frame >= n_frames:
        return False
    return all(0 <= diag < n_frames for _s, _e, diag in table.ranges)


def _apply_selector_tables(
    cases: list[ParsedCaseStructure],
    tables: list[SelectorTable],
    index_shift: int = 0,
) -> None:
    """Correlate dataspace selector tables to case structures and apply values.

    PRIMARY (direct link): a case's ``selector_table_offset`` (its select node's
    ``tdOffset``, a TM80 client index) plus the TM80 ``index_shift`` IS the
    TypeID of that case's ``DataFill`` selector table. This maps each case to its
    own table with no positional guessing, so multi-case VIs and orphan tables
    (left by a deleted case, referenced by no surviving case) resolve correctly.

    FALLBACK (positional): for any case the direct link doesn't resolve (no
    ``tdOffset``, or the named table is missing/inconsistent), correlate the
    still-unassigned cases and tables by order — cases by VCTP index against
    tables by TypeID — boolean-inclusive first, then boolean-excluded so a
    table-less boolean can't abort a tabled case's correlation.
    """
    tables_by_id = {t.type_id: t for t in tables}
    applied: set[int] = set()
    used_type_ids: set[int] = set()
    for case in cases:
        if case.selector_table_offset is None:
            continue
        table = tables_by_id.get(case.selector_table_offset + index_shift)
        if table is not None and _table_fits_case(case, table):
            _apply_one_table(case, table)
            applied.add(id(case))
            used_type_ids.add(table.type_id)

    leftover_cases = [c for c in cases if id(c) not in applied]
    leftover_tables = [t for t in tables if t.type_id not in used_type_ids]
    if not leftover_cases or not leftover_tables:
        return
    with_vctp = [c for c in leftover_cases if c.selector_vctp_index is not None]
    non_boolean = [c for c in with_vctp if c.selector_type != SelectorKind.BOOLEAN]
    for case_subset in (with_vctp, non_boolean):
        if _try_apply_selector_tables(case_subset, leftover_tables):
            return


def _try_apply_selector_tables(
    corr_cases: list[ParsedCaseStructure],
    tables: list[SelectorTable],
) -> bool:
    """Validate then apply ``tables`` to ``corr_cases`` (sorted by VCTP index).

    Returns True iff the counts matched and every pair validated (and was
    applied); False leaves everything untouched for the caller to try a
    different case subset.
    """
    if len(corr_cases) != len(tables):
        return False
    ordered = sorted(corr_cases, key=lambda c: c.selector_vctp_index or 0)

    # Validate every pair before mutating anything.
    for case, table in zip(ordered, tables):
        is_string = case.selector_type == SelectorKind.STRING
        if is_string != table.has_strings:
            return False
        n_frames = len(case.frames)
        # A negative displayed_frame is the "no frame displayed" sentinel
        # (valid); only a positive out-of-range index is a mismatch.
        if table.displayed_frame >= n_frames:
            return False
        for _start, _end, diag in table.ranges:
            if not (0 <= diag < n_frames):
                return False

    for case, table in zip(ordered, tables):
        _apply_one_table(case, table)
    return True


def _apply_one_table(case: ParsedCaseStructure, table: SelectorTable) -> None:
    """Apply a case's correlated table: its displayed frame and per-frame
    selector values.

    A case whose heap stores its selector values (``selector_values_in_heap``)
    is left alone: the heap is authoritative for its values, default frame and
    displayed frame, and a positionally paired table may belong to another case.
    """
    if case.selector_values_in_heap:
        return
    # displayed_frame < 0 is the "no frame displayed" sentinel — leave the
    # case's own displayed_frame (from dIdx) rather than overwrite with -1.
    if table.displayed_frame >= 0:
        case.displayed_frame = table.displayed_frame
    for idx, frame in enumerate(case.frames):
        my_ranges = [(s, e) for s, e, d in table.ranges if d == idx]
        if not my_ranges:
            # No value maps here → this is the implicit Default frame.
            frame.is_default = True
            frame.selector_value = "Default"
            frame.selector_ranges = []
            frame.selector_strings = []
            frame.boolean_value = None
            frame.is_no_error = False
            continue
        # The heap recorded no default, so a frame with values is not one.
        frame.is_default = False
        if table.has_strings:
            strings: list[str] = []
            for start, end in my_ranges:
                for i in range(start, end + 1):
                    if 0 <= i < len(table.strings):
                        strings.append(table.strings[i])
            frame.selector_strings = strings
            frame.selector_ranges = []
            frame.selector_value = strings[0] if strings else str(idx)
        elif case.selector_type == SelectorKind.BOOLEAN:
            # A boolean table names True/False by a 0/1 point range per diagram.
            frame.selector_ranges = []
            frame.selector_strings = []
            frame.boolean_value = my_ranges[0][0] == 1
            frame.selector_value = str(frame.boolean_value)
        else:
            frame.selector_ranges = [
                SelectorRange(start=s, end=e) for s, e in my_ranges
            ]
            frame.selector_strings = []
            first = my_ranges[0]
            frame.is_no_error = (
                case.selector_type == SelectorKind.ERROR and first[0] == 0
            )
            frame.selector_value = (
                str(first[0]) if first[0] == first[1] else f"{first[0]}..{first[1]}"
            )
