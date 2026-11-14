"""How a Scan From String node saved in the older form pairs its terminals.

Such a node lists the DCOs of its fixed terminals (format string, input string,
remaining string, initial scan location, offset past scan, error in, error out)
in ``permDCOList``, like any node of the older form, and each pair of
``default N`` and ``output N`` terminals as one item of ``dcoList``; its
``termList`` holds only bare ``<dco uid=.../>`` back-references
(``parser/vi._inline_dco_definitions`` replaces them with the definitions).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .flags import TERMINAL_IS_OUTPUT
from .utils import safe_int


def mark_paired_output(
    dco: ET.Element, term: ET.Element, definition: ET.Element
) -> None:
    """A ``default N`` / ``output N`` pair is one DCO listing two terminals, the
    input first and the output second, and its flags are the input's. The
    second terminal's ``dco`` gets the output bit."""
    listed = definition.find("termList")
    if listed is None or len(listed) != 2:
        return
    if listed[1].get("uid") != term.get("uid"):
        return
    flags = ET.Element("objFlags")
    flags.text = str(safe_int(definition.find("objFlags")) | TERMINAL_IS_OUTPUT)
    for old in dco.findall("objFlags"):
        dco.remove(old)
    dco.append(flags)
