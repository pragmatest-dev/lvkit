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

# A BD ``<Password Hash>`` this shallow (empty-string, all-zero, or the MD5 of
# the empty string) is a stubbed/no-password placeholder, not a real password.
_EMPTY_PASSWORD_HASHES = frozenset({
    "",
    "0" * 32,
    "d41d8cd98f00b204e9800998ecf8427e",
})


def get_qualified_name(xml_path: Path | str) -> str | None:
    """Fast extraction of just the qualified name from main XML.

    Use this for checking visited set before full parsing.

    Args:
        xml_path: Path to the main .xml file (not BDHb)

    Returns:
        Qualified name like "Library.lvlib:VI.vi" or None if not found
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Try LIvi section first (most reliable for library VIs)
    lvin = root.find(".//LIvi/Section/LVIN")
    if lvin is not None:
        qualified = lvin.get("Unk1")
        if qualified:
            return qualified

    # Fall back to LVSR name
    lvsr = root.find(".//LVSR/Section")
    if lvsr is not None:
        return lvsr.get("Name")

    return None


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

    # Get VI name from LVSR section
    lvsr = root.find(".//LVSR/Section")
    if lvsr is not None:
        metadata["name"] = lvsr.get("Name", "unknown")

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

    # Fall back to name if no qualified_name found
    if "qualified_name" not in metadata and "name" in metadata:
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
    ``toolbar``/``instance``/``code`` dicts. The graph layer
    (``graph.loading``) builds the typed dataclasses from this, wrapping
    ``lock_state`` into ``graph.models.LockState``.

    ``lock_state`` derivation (VI Properties -> Protection, a tri-state):
    read the LVSR ``<Library Protected="0|1">`` (NOT the ``<LIBN>`` owning-
    library name, a *different* ``<Library>`` element with no ``Protected``
    attribute) plus whether a real BD ``<Password Hash>`` is present.
    ``Protected=0`` -> unlocked; ``Protected=1`` + no real password ->
    locked; ``Protected=1`` + real password -> password_protected. There is
    no unlocked-with-password state.

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
        "code": {},
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
        "reentrant": _bool_attr(execution, "IsReentrant"),
        "reentrancy_pooled": _bool_attr(execution, "PooledReentrancy"),
        "priority": _int_attr(execution, "Priority"),
        "preferred_system": _int_attr(execution, "PrefExecSyst"),
        "is_subroutine": _bool_attr(execution, "IsSubroutine"),
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

    result["code"] = {
        "is_typedef": _bool_attr(execution, "TypeDefVI"),
        "is_strict_typedef": _bool_attr(execution, "StrictTypeDefVI"),
        "dynamic_dispatch": _bool_attr(execution, "DynamicDispatch"),
        "source_only": _bool_attr(execution2, "SourceOnly"),
        "has_no_block_diagram": _bool_attr(execution, "HasNoBD"),
        "is_instance_vi": _bool_attr(execution2, "InstanceVI"),
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


def parse_subvi_paths(xml_path: Path | str) -> list[ParsedDependencyRef]:
    """Parse dependency path references from the main VI XML (VIVI only).

    .. deprecated::
        This function only walks VIVI elements and is retained for test
        introspection.  Production code should use ``parse_vi()`` which
        calls ``_extract_subvi_info`` and returns the full
        ``ParsedVIMetadata.dependency_refs`` list (covering all LIvi link
        element types: VIVI, VIPI, VILB, FPPI, DDPI, VICC, etc.).

    Args:
        xml_path: Path to the main .xml file (not BDHb)

    Returns:
        List of ParsedDependencyRef with path hints for each VIVI dependency
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    refs: list[ParsedDependencyRef] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    for vivi in root.findall(".//LIvi//VIVI"):
        # Extract qualified name from all strings in LinkSaveQualName
        qual_name_strings = vivi.findall("LinkSaveQualName/String")
        if not qual_name_strings:
            continue

        qual_parts = [s.text for s in qual_name_strings if s.text]
        if not qual_parts:
            continue
        qualified_name = ":".join(qual_parts)
        name = qual_parts[-1]

        path_ref = vivi.find("LinkSavePathRef")
        if path_ref is None:
            continue

        # Preserve empty strings — they are the '..' navigation markers.
        path_tokens = [
            s.text if s.text is not None else ""
            for s in path_ref.findall("String")
        ]
        if not path_tokens:
            continue

        key: tuple[str, tuple[str, ...]] = (qualified_name, tuple(path_tokens))
        if key in seen:
            continue
        seen.add(key)

        refs.append(ParsedDependencyRef(
            name=name,
            path_tokens=path_tokens,
            qualified_name=qualified_name,
        ))

    return refs


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
        vi_name = decode_labview_text(data[p : p + vi_len])

        if not vi_name.endswith(".vi"):
            continue  # sanity check — skip malformed records

        qualified = f"{class_name}:{vi_name}"

        # UID: the 4 bytes just before the second PTH0, preceded by \x00\x00\x00\x01
        window = data[m.end() : record_end]
        uid_m = re.search(b"\x00\x00\x00\x01(.{4})PTH0", window, re.DOTALL)
        if uid_m:
            uid = struct.unpack(">I", uid_m.group(1))[0]
            result[str(uid)] = qualified

    return result
