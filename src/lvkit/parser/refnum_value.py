"""How a saved refnum value is decoded.

A refnum's value is a 4-byte handle (NI "Flattened Data", "Refnums"), except a
class value (a flattened object) and the tag refnums below. A handle of 0 is Not
a Refnum and reads as ``NULL_REFNUM``; any other handle reads as
``Refnum(<handle>)``.
"""

from __future__ import annotations

from lvkit.models import LVType

# Refnums whose value is not a 4-byte handle but flattened strings (NI "Flattened
# Data", "Refnums": type codes 0xE, 0xF and 0x15 store "a flattened string" holding
# the refnum tag; 0x1D, with the tag, three more fields). Keyed by RefType.
TAG_REFNUM_TYPES = frozenset({"VisaRef", "IVIRef", "UsrDefndTag", "UsrDefTagFlt"})
TAG_FLATTENED_REFNUM_TYPE = "UsrDefTagFlt"

# The value of a refnum whose handle is 0: Not a Refnum.
NULL_REFNUM = "None"


def holds_handle(lv_type: LVType | None) -> bool:
    """Whether a value of ``lv_type`` is a refnum handle: a refnum that is not a
    class value or a tag refnum."""
    return (
        lv_type is not None
        and lv_type.underlying_type == "Refnum"
        and not lv_type.classname
        and lv_type.ref_type not in TAG_REFNUM_TYPES
    )


def handle_text(handle: int) -> str:
    """The value a refnum handle reads as."""
    return f"Refnum({handle})" if handle else NULL_REFNUM
