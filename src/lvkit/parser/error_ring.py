"""The configuration of an Error Ring XNode, decoded from its ``<StateData>``.

An Error Ring (an XNode whose recorded library, ``parser/xnode_links.py``, is
``ERROR_RING_PATH``) keeps its configuration as one blob of big-endian fields
(``docs/_internal/design/labview-binary-format.md``, "Error Ring"):

    string  the ring's short label         ("6005: Attributes don't match ...")
    string  the ring's full label          ("6005: Attributes don't match ...")
    int32   error code
    string  the code's range               ("LabVIEW", "Custom", "No Error")
    string  the description typed for it  ("" for a code with NI's own text)
    uint8   1 = error, 0 = warning
    uint32  how many format inputs the description takes
    ...     one record per format input (not decoded)
    uint8   1 = include the call chain
    uint8   (not read)

A string is a uint32 byte count and that many bytes. The format-input records
are not decoded (their layout is not known from the corpus samples), so the two
closing bytes are read from the end of a ring that has format inputs; a ring
without them must end right after its count and two closing bytes. A blob that
does not read as these fields gives no configuration; the code generator
reports that ring as a gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..text_encoding import decode_labview_text

# The recorded path of the Error Ring's XNode library.
ERROR_RING_PATH = "<vilib>/ErrorRing/Error Ring.xnode"


@dataclass(frozen=True)
class ErrorRing:
    """What an Error Ring's error out holds: ``code``; ``description`` (the
    custom text, a Format Into String format when ``format_inputs`` > 0, or ""
    for a code whose text is NI's own); ``is_warning``; ``include_call_chain``;
    and ``label``, the text the ring shows."""

    code: int
    description: str
    is_warning: bool
    include_call_chain: bool
    format_inputs: int
    # The text the ring shows, as LabVIEW stores it (cut with "..." when long):
    # "6005: Attributes don't match VI prototy...".
    label: str = ""


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise ValueError("past the end")
        chunk = self.data[self.pos : self.pos + size]
        self.pos += size
        return chunk

    def uint(self, size: int) -> int:
        return int.from_bytes(self.take(size), "big")

    def flag(self) -> bool:
        value = self.uint(1)
        if value > 1:
            raise ValueError(f"flag byte {value}")
        return value == 1

    def string(self) -> str:
        return decode_labview_text(self.take(self.uint(4)))


def decode_error_ring(state_data_hex: str | None) -> ErrorRing | None:
    """The configuration in an Error Ring's ``<StateData>``, or ``None`` when the
    blob does not read as its fields (see the module docstring)."""
    if not state_data_hex:
        return None
    try:
        reader = _Reader(bytes.fromhex(state_data_hex))
        label = reader.string()
        reader.string()
        code = int.from_bytes(reader.take(4), "big", signed=True)
        reader.string()
        description = reader.string()
        is_error = reader.flag()
        format_inputs = reader.uint(4)
        trailer = _Reader(reader.data[-2:])
        include_call_chain = trailer.flag()
        trailer.flag()
    except ValueError:
        return None
    # With no format inputs the two trailing flags follow the count directly.
    if format_inputs == 0 and reader.pos + 2 != len(reader.data):
        return None
    return ErrorRing(
        code=code,
        description=description,
        is_warning=not is_error,
        include_call_chain=include_call_chain,
        format_inputs=format_inputs,
        label=label,
    )
