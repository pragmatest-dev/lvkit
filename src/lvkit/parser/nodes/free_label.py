"""Free-label (block-diagram comment) extraction.

A free label is a heap ``class="label"`` element with its own
``textRec/text`` and ``bounds`` sitting directly in a diagram's
``zPlaneList`` -- LabVIEW's "Free Label" decoration. It is a DIFFERENT
element from a node's own OWNED label/caption (also ``class="label"``, but
nested inside that node's ``partsList`` -- see ``extract_label``/
``extract_caption`` in ``parser/utils.py``): a free label is never inside a
``partsList``, so scanning only ``zPlaneList``/``nodeList`` DIRECT children
naturally excludes every owned label without any class-name disambiguation.

The walk mirrors ``parser/layout.py``'s ``_LayoutBuilder.walk``/``_visit``
recursion (zPlaneList, then nodeList, then descend into a structure's own
``diagramList``/``sequenceList``) so a label nested inside a loop/case/
sequence frame is found too -- but unlike that module, this one carries no
offsets: each label's ``bounds`` is its own local heap rect (absolute
placement for rendering is ``Layout.node_bounds``, keyed by uid, which the
graph layer looks up separately).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..models import ParsedFreeLabel
from ..utils import clean_labview_string

__all__ = ["extract_free_labels"]


def extract_free_labels(root: ET.Element) -> list[ParsedFreeLabel]:
    """Every diagram-level free label under ``root``.

    ``root`` may be the heap wrapper (with a ``<root>`` diag child) or the
    ``<root>`` diagram itself -- both are handled, mirroring
    ``build_layout_from_root``.
    """
    diag = root.find("root")
    if diag is None:
        diag = root
    labels: list[ParsedFreeLabel] = []
    _walk_diag(diag, labels)
    return labels


def _walk_diag(diag: ET.Element, labels: list[ParsedFreeLabel]) -> None:
    zp = diag.find("zPlaneList")
    if zp is not None:
        for elem in zp.findall("SL__arrayElement"):
            _visit(elem, labels)
    nl = diag.find("nodeList")
    if nl is not None:
        for elem in nl.findall("SL__arrayElement"):
            _visit(elem, labels)


def _visit(elem: ET.Element, labels: list[ParsedFreeLabel]) -> None:
    if elem.get("class") == "label":
        label = _parse_label(elem)
        if label is not None:
            labels.append(label)

    # A structure's inner frame(s): a single diagramList (loop/case/disable/
    # event bodies) or a sequenceList (flat/stacked sequence frames) -- same
    # two shapes _LayoutBuilder._visit recurses into.
    dlist = elem.find("diagramList")
    if dlist is not None:
        for d in dlist.findall("SL__arrayElement"):
            if d.get("class") == "diag":
                _walk_diag(d, labels)
        return

    seqlist = elem.find("sequenceList")
    if seqlist is not None:
        for frame in seqlist.findall("SL__arrayElement"):
            fdl = frame.find("diagramList")
            if fdl is None:
                continue
            for d in fdl.findall("SL__arrayElement"):
                if d.get("class") == "diag":
                    _walk_diag(d, labels)


def _parse_label(elem: ET.Element) -> ParsedFreeLabel | None:
    uid = elem.get("uid")
    text_el = elem.find("textRec/text")
    bounds = _parse_bounds(elem.find("bounds"))
    if not uid or text_el is None or text_el.text is None or bounds is None:
        return None
    text = clean_labview_string(text_el.text)
    if not text:
        return None
    bg_el = elem.find("bgColor")
    bg_color = bg_el.text.strip() if bg_el is not None and bg_el.text else None
    attach_el = elem.find("attachment")
    attach_uid = attach_el.get("uid") if attach_el is not None else None
    return ParsedFreeLabel(
        uid=uid,
        text=text,
        bounds=bounds,
        bg_color=bg_color,
        attach_uid=attach_uid,
    )


def _parse_bounds(elem: ET.Element | None) -> tuple[int, int, int, int] | None:
    """Parse a LabVIEW ``(top, left, bottom, right)`` rect element."""
    if elem is None or not elem.text:
        return None
    try:
        nums = [int(x.strip()) for x in elem.text.strip("()").split(",")]
    except ValueError:
        return None
    if len(nums) != 4:
        return None
    return (nums[0], nums[1], nums[2], nums[3])
