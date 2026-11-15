"""A loop's border terminals (iteration, count, stop) are found by CLASS.

Shared by the node parser (``nodes/loop.py``) and the layout (``layout.py``) so
both find the same terminals. The class names the terminal; the element tag
differs between VIs (a While Loop's stop terminal is ``loopTestDCO`` in most and
``loopEndDCO`` in others)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

# Loop border DCO class -> the glyph kind the renderer draws for it. The count
# terminal (lMax, N) is a graph Terminal, so the layout carries geometry only for
# the others (``GEOMETRY_ONLY_CLASSES``).
BORDER_DCO_KIND = {
    "lCnt": "i",
    "lMax": "N",
    "lTst": "cond",
}


# Border classes with no graph Terminal: the layout alone supplies their geometry.
GEOMETRY_ONLY_CLASSES = ("lCnt", "lTst")


def border_dco(loop_elem: ET.Element, dco_class: str) -> ET.Element | None:
    """The loop's own border terminal of class ``dco_class`` (a direct child).

    A border terminal is either recorded in full (a child carrying the class) or
    as a bare reference (``<loopLimitDCO uid="N"/>``, no class) to the terminal
    ``<dco class=... uid="N">`` recorded in the loop's border-terminal nodes; the
    reference is matched to its class through that uid."""
    full = loop_elem.find(f"*[@class='{dco_class}']")
    if full is not None:
        return full
    for ref in loop_elem.findall("*[@uid]"):
        if ref.get("class") is None and not len(ref):
            uid = ref.get("uid")
            match = f".//dco[@class='{dco_class}'][@uid='{uid}']"
            if loop_elem.find(match) is not None:
                return ref
    return None
