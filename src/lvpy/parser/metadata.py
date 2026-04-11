"""VI metadata parsing - SubVI refs, polymorphic detection, etc."""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .models import ParsedSubVIPathRef
from .type_resolution import parse_typedef_refs


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

    # Get library name from LIBN section
    lib_elem = root.find(".//LIBN/Section/Library")
    if lib_elem is not None and lib_elem.text:
        metadata["library"] = lib_elem.text

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

    # Parse typedef references
    metadata["typedef_refs"] = parse_typedef_refs(root)

    # Check if this is a polymorphic VI
    poly_info = parse_polymorphic_info(root)
    if poly_info["is_polymorphic"]:
        metadata["is_polymorphic"] = True
        metadata["poly_variants"] = poly_info["variants"]
        metadata["poly_selectors"] = poly_info["selectors"]

    return metadata


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


def parse_subvi_paths(xml_path: Path | str) -> list[ParsedSubVIPathRef]:
    """Parse SubVI path references from the main VI XML.

    The LIvi section contains VIVI elements with LinkSavePathRef that
    specify where to find SubVIs relative to vilib or userlib.

    Args:
        xml_path: Path to the main .xml file (not BDHb)

    Returns:
        List of ParsedSubVIPathRef with path hints for each SubVI
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    refs: list[ParsedSubVIPathRef] = []

    for vivi in root.findall(".//LIvi//VIVI"):
        # Extract qualified name from all strings in LinkSaveQualName
        qual_name_strings = vivi.findall("LinkSaveQualName/String")
        if not qual_name_strings:
            continue

        # Build qualified name (e.g., "Library.lvlib:VI.vi")
        qual_parts = [s.text for s in qual_name_strings if s.text]
        if not qual_parts:
            continue
        qualified_name = ":".join(qual_parts)

        # Find last non-empty string (the actual VI name) for unqualified lookup
        name = None
        for s in reversed(qual_name_strings):
            if s.text and s.text.endswith(".vi"):
                name = s.text
                break
        if not name:
            continue

        path_ref = vivi.find("LinkSavePathRef")
        if path_ref is None:
            continue

        path_parts = [s.text for s in path_ref.findall("String") if s.text]
        if not path_parts:
            continue

        is_vilib = len(path_parts) > 0 and path_parts[0] == "<vilib>"
        is_userlib = len(path_parts) > 0 and path_parts[0] == "<userlib>"

        refs.append(ParsedSubVIPathRef(
            name=name,
            path_tokens=path_parts,
            is_vilib=is_vilib,
            is_userlib=is_userlib,
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

    Both names are pascal strings: 1-byte length prefix + data (mac_roman).
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
        class_name = data[p : p + class_len].decode("mac_roman", errors="replace")
        p += class_len

        # Pascal string 2: VI name (e.g. "TestCase_Init.vi")
        if p >= record_end:
            continue
        vi_len = data[p]
        p += 1
        if p + vi_len > record_end or not vi_len:
            continue
        vi_name = data[p : p + vi_len].decode("mac_roman", errors="replace")

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
