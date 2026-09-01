"""VI metadata parsing - SubVI refs, polymorphic detection, etc."""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from lvkit.text_encoding import decode_labview_text

from .models import ParsedDependencyRef
from .type_resolution import parse_typedef_refs

# lock_state string values -- MUST mirror graph.models.LockState.value exactly
# (parser/ never imports graph/, per graph/models.py's module docstring, so the
# graph layer re-wraps these plain strings into the LockState enum).
LOCK_UNLOCKED = "unlocked"
LOCK_LOCKED = "locked"
LOCK_PASSWORD_PROTECTED = "password_protected"

# priority string values -- MUST mirror graph.models.Priority.value exactly
# (same parser/graph split as LOCK_* above).
PRIORITY_BACKGROUND = "background"
PRIORITY_NORMAL = "normal"
PRIORITY_ABOVE_NORMAL = "above_normal"
PRIORITY_HIGH = "high"
PRIORITY_TIME_CRITICAL = "time_critical"
PRIORITY_SUBROUTINE = "subroutine"

# reentrancy string values -- MUST mirror graph.models.Reentrancy.value exactly.
REENTRANCY_NON_REENTRANT = "non_reentrant"
REENTRANCY_SHARED_CLONE = "shared_clone"
REENTRANCY_PREALLOCATED_CLONE = "preallocated_clone"

# exec_system string values -- MUST mirror graph.models.ExecSystem.value exactly.
EXEC_SYSTEM_SAME_AS_CALLER = "same_as_caller"
EXEC_SYSTEM_USER_INTERFACE = "user_interface"
EXEC_SYSTEM_STANDARD = "standard"
EXEC_SYSTEM_INSTRUMENT_IO = "instrument_io"
EXEC_SYSTEM_DATA_ACQUISITION = "data_acquisition"
EXEC_SYSTEM_OTHER_1 = "other_1"
EXEC_SYSTEM_OTHER_2 = "other_2"

# typedef_status string values -- MUST mirror graph.models.TypedefStatus.value
# exactly.
TYPEDEF_STATUS_NOT_A_TYPEDEF = "not_a_typedef"
TYPEDEF_STATUS_TYPEDEF = "typedef"
TYPEDEF_STATUS_STRICT_TYPEDEF = "strict_typedef"

# Priority: LVSR ``Priority`` is a 0-indexed FILE FORMAT code -- NOT the
# VI-Server scripting enum -- NI-doc + corpus verified (e.g. JKI-EasyXML's
# "Fast Parser/Get Children.vi" has Priority="5" alongside IsSubroutine="1").
_PRIORITY_BY_CODE: dict[int, str] = {
    0: PRIORITY_BACKGROUND,
    1: PRIORITY_NORMAL,
    2: PRIORITY_ABOVE_NORMAL,
    3: PRIORITY_HIGH,
    4: PRIORITY_TIME_CRITICAL,
    5: PRIORITY_SUBROUTINE,
}

# ExecSystem: LVSR ``PrefExecSyst`` -- NI-doc + corpus verified (e.g.
# LabVIEW-OOP-Classes' "DAQ/Digital Input/DI_class/Update All.vi" has
# PrefExecSyst="3" -> data_acquisition).
_EXEC_SYSTEM_BY_CODE: dict[int, str] = {
    -1: EXEC_SYSTEM_SAME_AS_CALLER,
    0: EXEC_SYSTEM_USER_INTERFACE,
    1: EXEC_SYSTEM_STANDARD,
    2: EXEC_SYSTEM_INSTRUMENT_IO,
    3: EXEC_SYSTEM_DATA_ACQUISITION,
    4: EXEC_SYSTEM_OTHER_1,
    5: EXEC_SYSTEM_OTHER_2,
}


def _priority_from_code(code: int | None) -> str:
    """0-indexed LVSR ``Priority`` code -> faithful string value. Unknown or
    absent codes fall back to the dataclass default (normal)."""
    if code is None:
        return PRIORITY_NORMAL
    return _PRIORITY_BY_CODE.get(code, PRIORITY_NORMAL)


def _reentrancy(is_reentrant: bool, pooled: bool) -> str:
    """``IsReentrant``/``PooledReentrancy`` -> faithful string value.
    ``IsReentrant=0`` ignores ``pooled`` entirely -- a sticky leftover bit
    when non-reentrant, not a real pooling choice."""
    if not is_reentrant:
        return REENTRANCY_NON_REENTRANT
    return REENTRANCY_SHARED_CLONE if pooled else REENTRANCY_PREALLOCATED_CLONE


def _exec_system_from_code(code: int | None) -> str:
    """LVSR ``PrefExecSyst`` code -> faithful string value. Unknown or absent
    codes fall back to the dataclass default (same_as_caller)."""
    if code is None:
        return EXEC_SYSTEM_SAME_AS_CALLER
    return _EXEC_SYSTEM_BY_CODE.get(code, EXEC_SYSTEM_SAME_AS_CALLER)


def _typedef_status(is_typedef: bool, is_strict: bool) -> str:
    """``TypeDefVI``/``StrictTypeDefVI`` -> faithful string value: (0, 0) ->
    not_a_typedef, (1, 0) -> typedef, (1, 1) -> strict_typedef. (0, 1) never
    occurs."""
    if is_strict:
        return TYPEDEF_STATUS_STRICT_TYPEDEF
    if is_typedef:
        return TYPEDEF_STATUS_TYPEDEF
    return TYPEDEF_STATUS_NOT_A_TYPEDEF


# A BD ``<Password Hash>`` this shallow (empty-string, all-zero, or the MD5 of
# the empty string) is a stubbed/no-password placeholder, not a real password.
_EMPTY_PASSWORD_HASHES = frozenset(
    {
        "",
        "0" * 32,
        "d41d8cd98f00b204e9800998ecf8427e",
    }
)


def parse_vi_metadata(xml_path: Path | str) -> dict[str, Any]:
    """Parse the main VI XML file for metadata and SubVI references.

    Args:
        xml_path: Path to the main .xml file (not BDHb)

    Returns:
        Dict with version info, SubVI names, type descriptors, library info, etc.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    metadata: dict[str, Any] = {}

    # The VI's filename is its identity fallback — a VI outside any library IS
    # just its filename, and the binary may carry no explicit LVSR name. NEVER
    # the literal "unknown" (which collapsed distinct VIs onto one key).
    filename = Path(xml_path).stem + ".vi"

    # Get VI name from LVSR section (its own filename when absent).
    lvsr = root.find(".//LVSR/Section")
    metadata["name"] = (lvsr.get("Name") if lvsr is not None else None) or filename

    # Get library name(s) from LIBN section. ``library`` stays the OUTERMOST
    # (the owning .lvlib) as before; ``owning_libraries`` is the full ownership
    # chain (.lvlib -> .lvclass, outermost first) used to build a DISPLAY
    # class-qualified name — the VI is self-describing, so this is present even
    # for an isolated .vi (no class load).
    lib_elems = [e.text for e in root.findall(".//LIBN/Section/Library") if e.text]
    if lib_elems:
        metadata["library"] = lib_elems[0]
        metadata["owning_libraries"] = lib_elems

    # Get qualified name from LIvi section
    lvin = root.find(".//LIvi/Section/LVIN")
    if lvin is not None:
        qualified = lvin.get("Unk1")
        if qualified:
            metadata["qualified_name"] = qualified

    # Get SubVI references from LIvi section
    subvi_refs = []
    for vivi in root.findall(".//LIvi//VIVI/LinkSaveQualName/String"):
        if vivi.text:
            subvi_refs.append(vivi.text)
    metadata["subvi_refs"] = subvi_refs

    # No explicit qualified name in the binary: fall back to the (now always
    # populated) bare name. qualified_name stays the BARE resolution key BY
    # DESIGN — the DISPLAY layer composes the class-qualified form from
    # owning_libraries (see VINode). Never the "unknown" placeholder (which
    # collapsed distinct VIs onto one key).
    if "qualified_name" not in metadata:
        metadata["qualified_name"] = metadata["name"]

    # Get help/documentation data
    strg = root.find(".//STRG/Section/String")
    if strg is not None and strg.text:
        metadata["description"] = strg.text

    dstm = root.find(".//DSTM/Section/String")
    if dstm is not None and dstm.text:
        metadata["description"] = dstm.text

    hlpt = root.find(".//HLPT/Section/String")
    if hlpt is not None and hlpt.text:
        metadata["help_tag"] = hlpt.text

    # User-settable VI Properties (Protection/Execution/…) from <LVSR>.
    metadata.update(_parse_lvsr_properties(root))

    # Parse typedef references
    metadata["typedef_refs"] = parse_typedef_refs(root)

    # Check if this is a polymorphic VI
    poly_info = parse_polymorphic_info(root)
    if poly_info["is_polymorphic"]:
        metadata["is_polymorphic"] = True
        metadata["poly_variants"] = poly_info["variants"]
        metadata["poly_selectors"] = poly_info["selectors"]

    return metadata


def _bool_attr(elem: ET.Element | None, attr: str) -> bool:
    """``element.get(attr) == "1"`` -- False (default) if elem is None or
    the attribute is absent, exactly as the VI Properties dialog's own
    unset-flag default."""
    return elem is not None and elem.get(attr) == "1"


def _int_attr(elem: ET.Element | None, attr: str) -> int | None:
    if elem is None:
        return None
    value = elem.get(attr)
    return int(value) if value is not None else None


def _parse_lvsr_properties(root: ET.Element) -> dict[str, Any]:
    """Parse the VI Properties dialog settings lvkit tracks, from ``<LVSR>``.

    Returns PLAIN nested data (dict-of-dicts, never a dataclass/``Enum`` --
    the parser cannot import ``graph.models``) keyed exactly like
    ``graph.models.VIProperties``'s sub-structs: ``lv_version``/``vi_type``/
    ``lock_state`` at the top level, plus nested ``execution``/``window``/
    ``toolbar``/``instance``/``kind`` dicts (``kind`` feeds
    ``VIProperties.kind``), plus a separate ``health`` dict (feeds the
    sibling ``VIHealth`` facet). The graph layer (``graph.loading``) builds
    the typed dataclasses from this, wrapping ``lock_state`` (and
    ``execution["priority"]``/``["reentrancy"]``/``["exec_system"]``/
    ``kind["typedef_status"]``) into their matching ``graph.models`` Enum.

    ``lock_state`` derivation (VI Properties -> Protection, a tri-state):
    read the LVSR ``<Library Protected="0|1">`` (NOT the ``<LIBN>`` owning-
    library name, a *different* ``<Library>`` element with no ``Protected``
    attribute) plus whether a real BD ``<Password Hash>`` is present.
    ``Protected=0`` -> unlocked; ``Protected=1`` + no real password ->
    locked; ``Protected=1`` + real password -> password_protected. There is
    no unlocked-with-password state.

    ``priority``/``reentrancy``/``exec_system``/``typedef_status`` are
    derived FAITHFUL enum string values (see ``_priority_from_code``/
    ``_reentrancy``/``_exec_system_from_code``/``_typedef_status`` above) --
    NI-doc + corpus verified. The legacy ``IsSubroutine`` flag is redundant
    with ``priority == "subroutine"`` and is no longer read.

    Element paths (verified against a real extracted main .xml -- see
    ``tests/test_vi_properties.py``): ``Execution``/``Execution2``/
    ``Instrument``/``FrontPanel``/``ButtonsHidden``/``Flags0C``/``Flags12``
    are all direct children of ``LVSR/Section``.
    """
    result: dict[str, Any] = {
        "lv_version": None,
        "vi_type": None,
        "lock_state": LOCK_UNLOCKED,
        "execution": {},
        "window": {},
        "toolbar": {},
        "instance": {},
        "kind": {},
        "health": {},
    }

    version = root.find(".//LVSR/Section/Version")
    if version is not None:
        major = version.get("Major")
        minor = version.get("Minor")
        bugfix = version.get("Bugfix")
        if major is not None and minor is not None and bugfix is not None:
            result["lv_version"] = f"{major}.{minor}.{bugfix}"

    instrument = root.find(".//LVSR/Section/Instrument")
    if instrument is not None:
        vi_type = instrument.get("Type")
        if vi_type:
            result["vi_type"] = vi_type

    # The LVSR <Library> carries Protected= — distinct from the <LIBN>
    # <Library> (bare owning-library name text, no Protected attribute).
    protected = False
    for lib in root.findall(".//LVSR//Library"):
        if "Protected" in lib.attrib:
            protected = lib.get("Protected") == "1"
            break

    has_real_password = False
    for pw in root.findall(".//Password"):
        pw_hash = pw.get("Hash")
        if pw_hash and pw_hash.lower() not in _EMPTY_PASSWORD_HASHES:
            has_real_password = True
            break

    if not protected:
        result["lock_state"] = LOCK_UNLOCKED
    elif has_real_password:
        result["lock_state"] = LOCK_PASSWORD_PROTECTED
    else:
        result["lock_state"] = LOCK_LOCKED

    execution = root.find(".//LVSR/Section/Execution")
    execution2 = root.find(".//LVSR/Section/Execution2")
    instrument = root.find(".//LVSR/Section/Instrument")
    result["execution"] = {
        "priority": _priority_from_code(_int_attr(execution, "Priority")),
        "reentrancy": _reentrancy(
            _bool_attr(execution, "IsReentrant"),
            _bool_attr(execution, "PooledReentrancy"),
        ),
        "exec_system": _exec_system_from_code(_int_attr(execution, "PrefExecSyst")),
        "run_when_opened": _bool_attr(execution, "RunOnOpen"),
        "show_fp_when_loaded": _bool_attr(execution, "ShowFPOnLoad"),
        "show_fp_when_called": _bool_attr(execution, "ShowFPOnCall"),
        "close_fp_after_call": _bool_attr(execution, "CloseAfterCall"),
        "auto_preallocate_arrays": _bool_attr(execution, "AllowAutoPrealloc"),
        "inline": _bool_attr(execution2, "ShouldInline"),
        "inlinable": _bool_attr(execution2, "InlinableDiagram"),
        "auto_error_handling": _bool_attr(execution2, "DefaultErrorHandling"),
        "allow_debugging": _bool_attr(instrument, "DebugCapable"),
        "always_calls_parent": _bool_attr(execution2, "AlwaysCallsParent"),
        "print_after_exec": _bool_attr(instrument, "PrintAfterExec"),
    }

    front_panel = root.find(".//LVSR/Section/FrontPanel")
    flags0c = root.find(".//LVSR/Section/Flags0C")
    flags12 = root.find(".//LVSR/Section/Flags12")
    result["window"] = {
        "show_title_bar": _bool_attr(front_panel, "ShowTitleBar"),
        "show_menu_bar": _bool_attr(front_panel, "ShowMenuBar"),
        "show_toolbar": _bool_attr(front_panel, "ToolBarVisible"),
        "show_scrollbar": _int_attr(front_panel, "ShowScrollBar"),
        "auto_center": _bool_attr(front_panel, "AutoCenter"),
        "size_to_screen": _bool_attr(front_panel, "SizeToScreen"),
        "no_runtime_popup_menu": _bool_attr(front_panel, "NoRuntimePopUp"),
        "scale_with_window": _bool_attr(front_panel, "ScaleProportn"),
        "mark_return_button": _bool_attr(front_panel, "MarkReturnBtn"),
        "auto_handle_menus": _bool_attr(flags0c, "AutoHndlMenus"),
        "can_close": _bool_attr(flags12, "WndCanClose"),
        "can_resize": _bool_attr(flags12, "WndCanResize"),
        "can_minimize": _bool_attr(flags12, "WndCanMinimize"),
        "transparent": _bool_attr(flags12, "WndTransparent"),
    }

    buttons_hidden = root.find(".//LVSR/Section/ButtonsHidden")
    result["toolbar"] = {
        "hide_run_button": _bool_attr(buttons_hidden, "RunButton"),
        "hide_abort_button": _bool_attr(buttons_hidden, "AbortButton"),
        "hide_free_run_button": _bool_attr(buttons_hidden, "FreeRunButton"),
    }

    result["instance"] = {
        "is_system_vi": _bool_attr(execution2, "SystemVI"),
        "show_poly_selector": _bool_attr(execution2, "ShowPolySelector"),
        "hide_instance_caption": _bool_attr(execution2, "HideInstanceVICaption"),
        "draw_instance_icon": _bool_attr(execution2, "DrawInstanceIcon"),
        "remote_panel": _bool_attr(execution2, "RemotePanel"),
    }

    result["kind"] = {
        "typedef_status": _typedef_status(
            _bool_attr(execution, "TypeDefVI"),
            _bool_attr(execution, "StrictTypeDefVI"),
        ),
        "dynamic_dispatch": _bool_attr(execution, "DynamicDispatch"),
        "source_only": _bool_attr(execution2, "SourceOnly"),
        "has_no_block_diagram": _bool_attr(execution, "HasNoBD"),
        "is_instance_vi": _bool_attr(execution2, "InstanceVI"),
    }

    result["health"] = {
        "bad_node": _bool_attr(execution, "BadNode"),
        "bad_subvi": _bool_attr(execution, "BadSubVI"),
        "bad_subvi_link": _bool_attr(execution, "BadSubVILink"),
        "bad_compile": _bool_attr(execution, "BadCompile"),
        "broken_poly": _bool_attr(execution, "BrokenPolyVI"),
    }

    return result


def parse_polymorphic_info(root: ET.Element) -> dict[str, Any]:
    """Parse polymorphic VI information from VCTP and CPST sections.

    A polymorphic VI has:
    - Type="PolyVI" in VCTP section
    - CPST section with variant selector strings
    - Multiple SubVI references (variants) in LIvi section

    Args:
        root: Root element of the main VI XML

    Returns:
        Dict with:
        - is_polymorphic: bool
        - variants: list of variant VI names
        - selectors: list of selector strings
    """
    result: dict[str, Any] = {
        "is_polymorphic": False,
        "variants": [],
        "selectors": [],
    }

    # Check for AllowPolyTypeAdapt flag - this indicates a polymorphic wrapper
    # VIs that call polymorphic VIs have PolyVI TypeDesc but AllowPolyTypeAdapt="0"
    exec2 = root.find(".//LVSR//Execution2")
    if exec2 is None or exec2.get("AllowPolyTypeAdapt") != "1":
        # Also check for selector-based polymorphic (ShowPolySelector)
        if exec2 is None or exec2.get("ShowPolySelector") != "1":
            return result

    # Extract selector strings from CPST section (optional - adapt-to-type has none)
    cpst_section = root.find(".//CPST/Section")
    if cpst_section is not None:
        for string_elem in cpst_section.findall("String"):
            if string_elem.text and string_elem.text.strip():
                result["selectors"].append(string_elem.text.strip())

    # Extract variant VI names from LIvi VIVI elements
    for vivi in root.findall(".//LIvi//VIVI/LinkSaveQualName/String"):
        if vivi.text:
            result["variants"].append(vivi.text)

    # A VI is polymorphic if it has the flag AND variant references
    if result["variants"]:
        result["is_polymorphic"] = True

    return result


def parse_iuse_from_libd(libd_path: Path) -> dict[str, str]:
    """Parse iUse UID → qualified VI name from a _LIbd.bin binary.

    Fallback for older LabVIEW VIs (pre-LV9) whose main XML does not contain
    decoded BDHP/IUVI elements. pylabview fails to parse these with:
      "LinkObjIUseToVILink 'IUVI' contains path data of unrecognized class"

    Each IUVI record in the binary has:
      IUVI [4 bytes] \\x00\\x02 [pascal class_name] [pascal vi_name]
           [PTH0 path-to-VI] [...] \\x00\\x00\\x00\\x01 [4-byte iUse UID]
           [PTH0 path-to-class]

    Both names are Pascal strings: 1-byte length prefix + native text data.
    The \\x00\\x02 is a 2-item count sentinel that appears within the first
    8 bytes after the IUVI tag.
    """
    try:
        data = libd_path.read_bytes()
    except OSError:
        return {}

    result: dict[str, str] = {}

    for m in re.finditer(b"IUVI", data):
        pos = m.end()
        record_end = min(pos + 512, len(data))

        # Scan the first 8 bytes after IUVI for \x00\x02 (count = 2 strings)
        count_offset = None
        for i in range(pos, min(pos + 8, record_end - 1)):
            if data[i] == 0x00 and data[i + 1] == 0x02:
                count_offset = i + 2  # skip \x00\x02, point to first pascal string
                break
        if count_offset is None:
            continue

        # Pascal string 1: class/library name (e.g. "TestCase.lvclass")
        p = count_offset
        if p >= record_end:
            continue
        class_len = data[p]
        p += 1
        if p + class_len > record_end:
            continue
        class_name = decode_labview_text(data[p : p + class_len])
        p += class_len

        # Pascal string 2: VI name (e.g. "TestCase_Init.vi")
        if p >= record_end:
            continue
        vi_len = data[p]
        p += 1
        if p + vi_len > record_end or not vi_len:
            continue
        # Strip a spurious leading/trailing whitespace char (a NEWLINE the
        # binary sometimes carries into a decoded member name) -- never part of
        # a real name, and left in it corrupts the ``class:member`` identity.
        vi_name = decode_labview_text(data[p : p + vi_len]).strip()

        if not vi_name.endswith(".vi"):
            continue  # sanity check — skip malformed records

        qualified = f"{class_name.strip()}:{vi_name}"

        # UID: the 4 bytes just before the second PTH0, preceded by \x00\x00\x00\x01
        window = data[m.end() : record_end]
        uid_m = re.search(b"\x00\x00\x00\x01(.{4})PTH0", window, re.DOTALL)
        if uid_m:
            uid = struct.unpack(">I", uid_m.group(1))[0]
            result[str(uid)] = qualified

    return result


def _decode_pth0_components(
    data: bytes, pth0_offset: int, end: int
) -> tuple[list[str], int]:
    """Decode one ``PTH0`` record's pascal-string path components ->
    ``(tokens, next_idx)``.

    ``pth0_offset`` is the byte offset of the ``b"PTH0"`` tag within ``data``;
    ``end`` bounds how far the decode may read (exclusive) -- either
    ``len(data)`` for a whole-file scan or a caller-defined record boundary.
    Reads the component count at ``pth0_offset+10:+12`` (guarded to
    ``0 < ncomp <= 64`` -- a sanity bound against a garbage length), then
    decodes each component as a pascal string (1-byte length prefix + native
    text). Returns ``([], pth0_offset)`` if the record is truncated (would read
    past ``end``) or the count is out of range -- a partial token list is never
    returned, so a truncated record reads as "no components" to every caller.
    ``next_idx`` is the byte offset just past the last decoded component --
    the single source of both the decoded tokens AND the consumed-byte count,
    shared by every caller that needs to keep reading past this record (e.g.
    ``vi.py``'s ``_walk_path``, which uses it to stay aligned with a
    following cluster field).
    """
    if pth0_offset + 12 > end:
        return [], pth0_offset
    ncomp = int.from_bytes(data[pth0_offset + 10 : pth0_offset + 12], "big")
    if not (0 < ncomp <= 64):
        return [], pth0_offset
    idx = pth0_offset + 12
    tokens: list[str] = []
    for _ in range(ncomp):
        if idx >= end:
            return [], pth0_offset
        ln = data[idx]
        idx += 1
        if idx + ln > end:
            return [], pth0_offset
        tokens.append(decode_labview_text(data[idx : idx + ln]))
        idx += ln
    return tokens, idx


def parse_link_path_refs(link_bin: Path) -> list[ParsedDependencyRef]:
    """A recorded PATH for EVERY file a link binary references, from its ``PTH0``
    path records. LabVIEW stores a ``PTH0`` for each dependency it tracks (it is
    what LabVIEW uses to relink / to prompt "find <this file>" when one is
    missing) across ``_LIvi.bin`` (VI-level), ``_LIbd.bin`` (block diagram) and
    ``_LIfp.bin`` (front panel). Each ``PTH0``'s components ARE the path
    (caller-relative; a leading empty pops one level, per
    ``ParsedDependencyRef.resolve_against``) and its LAST component is the
    referenced file's leaf (``*.vi`` / ``*.lvclass`` / ``*.ctl`` / ``*.lvlib``).

    Recovering these gives the loader a recorded path for the referenced CLASSES
    and TYPEDEFS too — not just SubVIs — so the whole dependency closure resolves
    by PATH with no name-search, identically on web and desktop.
    """
    try:
        data = link_bin.read_bytes()
    except OSError:
        return []

    refs: list[ParsedDependencyRef] = []
    seen: set[tuple[str, ...]] = set()
    for m in re.finditer(b"PTH0", data):
        tokens, _next_idx = _decode_pth0_components(data, m.start(), len(data))
        if not tokens:
            continue
        leaf = tokens[-1]
        if not leaf.endswith((".vi", ".lvclass", ".ctl", ".lvlib")):
            continue
        key = (leaf, *tokens)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            ParsedDependencyRef(name=leaf, path_tokens=tokens, qualified_name=leaf)
        )
    return refs


def parse_vipi_from_livi(livi_path: Path) -> list[str]:
    """Parse subVI callee METHOD NAMES from a ``_LIvi.bin`` (VI-level link
    identity).

    ``_LIvi.bin`` is LabVIEW's VI-LEVEL link table — distinct from the
    ``_LIbd.bin`` block-diagram table read by ``parse_iuse_from_libd``. It
    carries a ``VIPI`` record for EVERY subVI the diagram calls, including the
    dynamic-dispatch (``dynIUse``) calls that leave NO ``IUVI`` in ``_LIbd.bin``
    — so it is the only record that names those callees. Each record:

        VIPI [uint32 count] [count name strings] [PTH0 path hint] [trailer]

    ``count`` name strings: 0 = the VI's own class-context record (no callee,
    skipped); 1 = a bare method name whose class is the ``PTH0``'s own class;
    2 = an explicit ``"<Class>.lvclass"`` + method pair. A method name is a
    wrapped pascal string ``[len][0x01][inner_len][text]``; a class name is a
    plain ``[len][text]`` (its text never starts with ``0x01``, so the wrapper
    is detected by the first byte).

    The record's ``PTH0`` locates the class the call is ROOTED ON (the static
    type at the call site) — for a cross-class dynamic dispatch that is NOT
    where the method file lives, so it is an unreliable hint, not a guaranteed
    owner. It is not decoded here: the caller resolves each returned method
    name's owning file against the classes the VI already depends on (see
    ``graph.loading._load_subvi_method_deps``), never from this record's own
    class hint. Returns the ``.vi`` leaf name of each callee.
    """
    try:
        data = livi_path.read_bytes()
    except OSError:
        return []

    names_out: list[str] = []
    starts = [m.start() for m in re.finditer(b"VIPI", data)]
    starts.append(len(data))

    for i in range(len(starts) - 1):
        rec_start, rec_end = starts[i], starts[i + 1]
        p = rec_start + 4  # past the 'VIPI' tag
        if p + 4 > rec_end:
            continue
        count = struct.unpack(">I", data[p : p + 4])[0]
        p += 4
        if count == 0 or count > 4:  # 0 = own-context record; >4 = misparse
            continue

        names: list[str] = []
        for _ in range(count):
            if p >= rec_end:
                break
            slen = data[p]
            p += 1
            if slen == 0 or p + slen > rec_end:
                break
            raw = data[p : p + slen]
            p += slen
            # A method name is wrapped [0x01][inner_len][text]; a class name is
            # a plain pascal string (never starts with 0x01).
            if raw[0] == 0x01 and slen >= 2:
                inner = raw[1]
                text = decode_labview_text(raw[2 : 2 + inner])
            else:
                text = decode_labview_text(raw)
            names.append(text.strip())

        method = next((n for n in names if n.endswith(".vi")), None)
        if method:
            names_out.append(method)

    return names_out
