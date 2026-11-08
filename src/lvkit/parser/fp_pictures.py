"""The pictures a front-panel control's parts embed.

A part whose picture id is positive names a picture stored in the VI itself (a
negative id is one of LabVIEW's built-in shapes, see ``render/front_panel/
controls/style.py``). The VI's ``<stem>.xml`` lists each as a ``MNGI`` section
whose index is the picture id and whose file is a PNG
(``image_resources.PANEL_PICTURE_BLOCK``). ``attach_panel_pictures`` reads the
ones the controls name and sets each control's ``pictures``.
"""

from __future__ import annotations

from pathlib import Path

from .image_resources import PANEL_PICTURE_BLOCK, carve_png, resource_sections
from .models import ParsedFPControl, ParsedFPPart


def _part_ids(parts: list[ParsedFPPart]) -> set[int]:
    ids: set[int] = set()
    for part in parts:
        ids.update(i for i in part.image_ids if i > 0)
        ids |= _part_ids(part.parts)
    return ids


def attach_panel_pictures(fp_xml: Path, controls: list[ParsedFPControl]) -> None:
    """Set ``pictures`` of every control (and of each cluster field) to the PNG
    of each positive picture id its parts name. An id the VI stores no picture
    for is left out."""
    main_xml = fp_xml.parent / f"{fp_xml.stem.replace('_FPHb', '')}.xml"
    ready = resource_sections(main_xml, (PANEL_PICTURE_BLOCK,))
    carved = resource_sections(main_xml, ("DSIM",))
    if not ready and not carved:
        return
    cache: dict[int, bytes] = {}

    def picture(picture_id: int) -> bytes | None:
        if picture_id not in cache:
            png = None
            section = ready.get(picture_id)
            if section is not None and section.exists():
                png = section.read_bytes()
            else:
                section = carved.get(picture_id)
                if section is not None and section.exists():
                    png = carve_png(section.read_bytes())
            if png is None:
                return None
            cache[picture_id] = png
        return cache[picture_id]

    def attach(ctrl: ParsedFPControl) -> None:
        for picture_id in _part_ids(ctrl.parts):
            png = picture(picture_id)
            if png is not None:
                ctrl.pictures[picture_id] = png
        for child in ctrl.children:
            attach(child)

    for ctrl in controls:
        attach(ctrl)
