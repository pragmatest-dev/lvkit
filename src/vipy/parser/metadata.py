"""VI metadata parsing - SubVI refs, polymorphic detection, etc."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .models import SubVIPathRef
from .types import parse_typedef_refs


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

    # Check for PolyVI type in VCTP section
    poly_type = root.find(".//VCTP//TypeDesc[@Type='PolyVI']")
    if poly_type is None:
        return result

    result["is_polymorphic"] = True

    # Extract selector strings from CPST section
    cpst_section = root.find(".//CPST/Section")
    if cpst_section is not None:
        for string_elem in cpst_section.findall("String"):
            if string_elem.text and string_elem.text.strip():
                result["selectors"].append(string_elem.text.strip())

    # Extract variant VI names from LIvi VIVI elements
    for vivi in root.findall(".//LIvi//VIVI/LinkSaveQualName/String"):
        if vivi.text:
            result["variants"].append(vivi.text)

    return result


def parse_subvi_paths(xml_path: Path | str) -> list[SubVIPathRef]:
    """Parse SubVI path references from the main VI XML.

    The LIvi section contains VIVI elements with LinkSavePathRef that
    specify where to find SubVIs relative to vilib or userlib.

    Args:
        xml_path: Path to the main .xml file (not BDHb)

    Returns:
        List of SubVIPathRef with path hints for each SubVI
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    refs: list[SubVIPathRef] = []

    for vivi in root.findall(".//LIvi//VIVI"):
        qual_name = vivi.find("LinkSaveQualName/String")
        if qual_name is None or not qual_name.text:
            continue
        name = qual_name.text

        path_ref = vivi.find("LinkSavePathRef")
        if path_ref is None:
            continue

        path_parts = [s.text for s in path_ref.findall("String") if s.text]
        if not path_parts:
            continue

        is_vilib = len(path_parts) > 0 and path_parts[0] == "<vilib>"
        is_userlib = len(path_parts) > 0 and path_parts[0] == "<userlib>"

        refs.append(SubVIPathRef(
            name=name,
            path_tokens=path_parts,
            is_vilib=is_vilib,
            is_userlib=is_userlib,
        ))

    return refs
