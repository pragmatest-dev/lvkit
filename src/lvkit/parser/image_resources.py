"""Embedded-picture / decoration resource resolution — LabVIEW's PICC
(picture) and DSIM (dynamic-size image) resource sections.

A heap element's ``<ImageResID>`` (when positive) or ``<ImageInternalsResID>``
names one of these sections by index. The VI's top-level ``<stem>.xml`` (the
sibling of ``<stem>_BDHb.xml`` extracted by pylabview) records the index -> file
mapping::

    <PICC><Section Index="2" File="..._PICC2.bin"/><Section Index="3" .../></PICC>
    <DSIM><Section Index="4" File="..._DSIM.bin"/></DSIM>

This module resolves that map (``resource_sections``/``resources_for_heap``)
and carves the embedded PNG out of a DSIM section's raw bytes (``carve_png``).
See ``docs/_internal/design/labview-binary-format.md`` for the verified byte
layout (issue #82).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# The two resource-section kinds a decoration's ImageResID/ImageInternalsResID
# can name — a picture (DSIM) or a picture-internals blob (PICC). Any other
# top-level block in the VI's resource XML is unrelated and ignored.
_SECTION_BLOCKS = ("PICC", "DSIM")


def resource_sections(main_xml: Path) -> dict[int, Path]:
    """Section Index -> resource file, from a VI's top-level ``<stem>.xml``.

    Missing file or malformed XML -> ``{}`` (no resources), never a crash --
    most VIs have no picture/decoration content at all.
    """
    try:
        root = ET.parse(main_xml).getroot()
    except (OSError, ET.ParseError):
        return {}
    sections: dict[int, Path] = {}
    for block_tag in _SECTION_BLOCKS:
        block = root.find(block_tag)
        if block is None:
            continue
        for sec in block.findall("Section"):
            idx, file_name = sec.get("Index"), sec.get("File")
            if idx is None or not file_name:
                continue
            try:
                sections[int(idx)] = main_xml.parent / file_name
            except ValueError:
                continue
    return sections


def resources_for_heap(bd_xml: Path) -> dict[int, Path]:
    """The resource-section map for a ``_BDHb.xml`` heap path's sibling
    ``<stem>.xml`` (same naming convention as ``layout._icon_for_heap``): ``{}``
    when that file doesn't exist (the common case)."""
    main_xml = bd_xml.parent / f"{bd_xml.stem.replace('_BDHb', '')}.xml"
    return resource_sections(main_xml) if main_xml.exists() else {}


def carve_png(data: bytes) -> bytes | None:
    """The first embedded PNG in a DSIM section's raw bytes, or ``None``.

    A DSIM section stores the picture behind a small binary preamble (size/
    scaling fields LabVIEW itself uses) — the PNG payload starts at its own
    signature and ends at the first ``IEND`` chunk's CRC (+8 bytes past the
    ``IEND`` tag). Verified on the issue #82 repro: the PNG at offset 46,
    sliced through ``IEND``, reproduces the embedded picture exactly."""
    start = data.find(_PNG_SIGNATURE)
    if start < 0:
        return None
    iend = data.find(b"IEND", start)
    if iend < 0:
        return None
    return data[start : iend + 8]
