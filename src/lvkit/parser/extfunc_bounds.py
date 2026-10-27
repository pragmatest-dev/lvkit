"""Geometry of a Call Library Function Node's rows, read straight from the XML.

Kept free of the parser's model imports so ``layout`` can use it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

EXT_FUNC_ARG_CLASS = "extFuncArg"


def row_pairs(elem: ET.Element) -> list[tuple[ET.Element, str, str]]:
    """Each parameter/return row as ``(extFuncArg DCO, input uid, output uid)``."""
    pairs: list[tuple[ET.Element, str, str]] = []
    for dco in elem.findall(
        f"./termList/SL__arrayElement/dco[@class='{EXT_FUNC_ARG_CLASS}']"
    ):
        sides = [t.get("uid") for t in dco.findall("termList/SL__arrayElement")]
        if len(sides) == 2 and sides[0] and sides[1]:
            pairs.append((dco, sides[0], sides[1]))
    return pairs


def extfunc_mirrored_bounds(
    elem: ET.Element,
) -> dict[str, tuple[int, int, int, int]]:
    """``(top, left, bottom, right)`` of each row's output terminal, which has
    no ``<termBounds>`` of its own: the input side's rect mirrored across the
    node's width (the path and error pairs record both sides, and their right
    rects are exactly this mirror image)."""
    node_bounds = elem.findtext("bounds")
    if not node_bounds:
        return {}
    _, node_left, _, node_right = (int(v) for v in node_bounds.strip("()").split(","))
    width = node_right - node_left
    mirrored: dict[str, tuple[int, int, int, int]] = {}
    for dco, _, output_uid in row_pairs(elem):
        text = dco.findtext("termBounds")
        if not text:
            continue
        top, left, bottom, right = (int(v) for v in text.strip("()").split(","))
        mirrored[output_uid] = (top, width - right, bottom, width - left)
    return mirrored
