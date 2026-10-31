"""Which terminal of a signal is its source.

A signal lists the terminals it joins with the source usually first. When the
first one is declared an input by its own DCO and another terminal of the signal
is declared an output, that output is the source (see
``docs/_internal/design/labview-binary-format.md``)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .flags import is_output_terminal
from .utils import safe_int

# DCO classes whose own ``objFlags`` bit 0 says whether the terminal is an output
# (a primitive's parameter, a Bundle/Unbundle field, a subVI call's terminal).
_DIRECTED_DCO_CLASSES = frozenset({"parm", "nmxDCO", "iUseDCO"})


def declared_outputs(root: ET.Element) -> dict[str, bool]:
    """For each terminal whose DCO defines its direction, whether it is an
    output. A DCO with no ``objFlags`` is an input (every unwired terminal of a
    primitive studied agrees)."""
    declared: dict[str, bool] = {}
    for term in root.iter("SL__arrayElement"):
        uid = term.get("uid")
        dco = term.find("dco")
        if (
            term.get("class") == "term"
            and uid
            and dco is not None
            and dco.get("class") in _DIRECTED_DCO_CLASSES
            and dco.find("typeDesc") is not None
        ):
            declared[uid] = is_output_terminal(safe_int(dco.find("objFlags")))
    return declared


def source_first(terms: list[str], declared: dict[str, bool]) -> list[str]:
    """``terms`` with the declared output first when the first one is declared an
    input; the other terminals keep their order."""
    if declared.get(terms[0]) is not False:
        return terms
    source = next((i for i, t in enumerate(terms) if declared.get(t) is True), None)
    if source is None:
        return terms
    return [terms[source], *terms[:source], *terms[source + 1 :]]
