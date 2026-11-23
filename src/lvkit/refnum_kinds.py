"""The kinds of reference (refnum) type, named in LabVIEW's words.

A reference type's kind is stored in the VI as a binary name (``NotifierRef``,
``DotNet``, ``LVObjCtl`` ...), which ``LVType.ref_type`` keeps. Every view shows
the kind in LabVIEW's words instead (``Notifier refnum{DBL}``, ``.NET refnum{...}``),
and lvnet reads those words back. This module is the one table that maps both ways.

A kind NI's public documentation gives no name for has no row: it prints under its
binary name, and a kind word lvkit does not know reads back unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ServerClass(StrEnum):
    """The VI Server class a generic reference (``LVObjCtl``) is of, when its type
    says (``CtlFlags``, ``labview-binary-format.md``)."""

    VI = "VI"
    APPLICATION = "Application"


_REFNUM_CONTROLS = (
    "https://www.ni.com/docs/en-US/bundle/labview/page/types-of-refnum-controls.html"
)
_API_REF = "https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/functions/"
_CREATE_USER_EVENT = _API_REF + "create-user-event.html"
_REGISTER_FOR_EVENTS = _API_REF + "register-for-events.html"
_VISA_OPEN = _API_REF + "visa-open.html"
_REFNUM_TO_SESSION = (
    "https://www.ni.com/docs/en-CC/bundle/teststand-api-reference/page/"
    "lvteststand/refnum-to-session.html"
)


@dataclass(frozen=True)
class RefnumKind:
    """One kind of reference: the binary name (and, for a generic VI Server
    reference, its server class), the words LabVIEW uses, and the NI page that
    gives those words."""

    binary: str
    server_class: ServerClass | None
    words: str
    page: str


REFNUM_KINDS: tuple[RefnumKind, ...] = (
    RefnumKind("LVObjCtl", ServerClass.APPLICATION, "Application", _REFNUM_CONTROLS),
    RefnumKind("LVObjCtl", ServerClass.VI, "VI", _REFNUM_CONTROLS),
    RefnumKind("LVObjCtl", None, "Control", _REFNUM_CONTROLS),
    RefnumKind("Occurrence", None, "Occurrence", _REFNUM_CONTROLS),
    RefnumKind("Menu", None, "Menu", _REFNUM_CONTROLS),
    RefnumKind("DataSocket", None, "DataSocket", _REFNUM_CONTROLS),
    RefnumKind("ByteStream", None, "Byte Stream File", _REFNUM_CONTROLS),
    RefnumKind("DataLog", None, "Data Log File", _REFNUM_CONTROLS),
    RefnumKind("TCPNetConn", None, "TCP Network Connection", _REFNUM_CONTROLS),
    RefnumKind("UDPNetConn", None, "UDP Network Connection", _REFNUM_CONTROLS),
    RefnumKind("IrdaNetConn", None, "IrDA Network Connection", _REFNUM_CONTROLS),
    RefnumKind("BluetoothCon", None, "Bluetooth Network Connection", _REFNUM_CONTROLS),
    RefnumKind("DotNet", None, ".NET", _REFNUM_CONTROLS),
    RefnumKind("AutoRef", None, "Automation", _REFNUM_CONTROLS),
    RefnumKind("Callback", None, "Event Callback", _REFNUM_CONTROLS),
    RefnumKind("Imaq", None, "IMAQ Session", _REFNUM_CONTROLS),
    RefnumKind("TDMSFile", None, "TDMS File", _REFNUM_CONTROLS),
    RefnumKind("Queue", None, "Queue", _REFNUM_CONTROLS),
    RefnumKind("NotifierRef", None, "Notifier", _REFNUM_CONTROLS),
    RefnumKind("DataValueRef", None, "Data Value Reference", _REFNUM_CONTROLS),
    RefnumKind("UserEvent", None, "User Event", _CREATE_USER_EVENT),
    RefnumKind("EventReg", None, "Event Registration", _REGISTER_FOR_EVENTS),
    RefnumKind("VisaRef", None, "VISA Resource Name", _VISA_OPEN),
    RefnumKind("IVIRef", None, "IVI", _REFNUM_TO_SESSION),
)

# The binary name that means "a reference of no particular kind".
GENERIC_BINARY = "Generic"

_WORDS_BY_BINARY: dict[tuple[str, ServerClass | None], str] = {
    (kind.binary, kind.server_class): kind.words for kind in REFNUM_KINDS
}
_BINARY_BY_WORDS: dict[str, tuple[str, ServerClass | None]] = {
    kind.words: (kind.binary, kind.server_class) for kind in REFNUM_KINDS
}


def refnum_kind_words(
    ref_type: str | None, server_class: ServerClass | None
) -> str | None:
    """The words a reference of binary kind ``ref_type`` prints before ``refnum``.

    None for a reference of no particular kind (it prints as bare ``refnum``). A
    kind with no row keeps its binary name. A generic VI Server reference whose
    class is neither the VI nor the application is a control reference.
    """
    if ref_type is None or ref_type == GENERIC_BINARY:
        return None
    words = _WORDS_BY_BINARY.get((ref_type, server_class))
    if words is not None:
        return words
    return _WORDS_BY_BINARY.get((ref_type, None), ref_type)


REFNUM_WORD = "refnum"


def refnum_head(ref_type: str | None, server_class: ServerClass | None) -> str:
    """A reference type's text without what it carries: ``Queue refnum``, or bare
    ``refnum`` for a reference of no particular kind."""
    words = refnum_kind_words(ref_type, server_class)
    return REFNUM_WORD if words is None else f"{words} {REFNUM_WORD}"


def parse_refnum_kind(words: str) -> tuple[str, ServerClass | None]:
    """The binary kind (and server class) a reference's printed words name.

    Words with no row are kept as written, as the binary name.
    """
    return _BINARY_BY_WORDS.get(words, (words, None))
