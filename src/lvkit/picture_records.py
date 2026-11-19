"""The drawing a stored LabVIEW Picture holds, read as records.

A Picture's stored bytes (a Picture constant's, a control's saved default, the
Picture inside flattened data) are a ``u32`` length and then that many bytes of
records, with no header: each record is a big-endian ``u16`` operation number, a
``u32`` payload length and the payload. Deduced from the corpus (the records of
every non-empty Picture of the ni-labview-icon-editor sample add up exactly to
their stored length: 3,102 bytes, one record; 272 bytes, eight records); the
operation numbers and their names are the list at labviewwiki.org "Picture data
type" ("Operation numbers"), which gives no byte layout for any of them.

The only layout read here is that of operation 29, "copy pixmap". Deduced from
the corpus, where its payload is 24 header bytes and then the pixels:

* ``u32`` pixel byte count (3072), ``u32`` 0, ``u16`` 32, ``u16`` 32, ``u16`` 24
  (bits per pixel), then 10 bytes this does not read;
* the pixels: ``width x height`` 24-bit red, green, blue values, row by row (the
  one sample is 32 x 32 and all white).

Not confirmed, so not read: which of the two 32s is the width (the sample is
square, so a non-square pixmap is left undecoded), where the pixmap goes (it is
placed at the top-left corner), and what the 10 header bytes hold.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass

# labviewwiki.org "Picture data type": the operation numbers it lists.
PICTURE_OPERATIONS: Mapping[int, str] = {
    0: "new font",
    4: "set pixel",
    5: "move pen",
    6: "move pen to",
    7: "draw line",
    8: "draw line to",
    9: "rect frame",
    10: "rect fill",
    11: "oval frame",
    12: "oval fill",
    13: "arc frame",
    14: "arc fill",
    15: "gray out rect",
    16: "poly line",
    17: "poly line fill",
    18: "text box",
    22: "set rect mask",
    24: "round rect frame",
    25: "round rect fill",
    29: "copy pixmap",
    40: "copy pixmap with mask",
    41: "select pen",
    48: "copy blended pixmap",
}

COPY_PIXMAP = 29
RECORD_HEADER = struct.Struct(">HI")
PIXMAP_HEADER = struct.Struct(">IIHHH")
# The header of a copy pixmap payload: the fields above and 10 bytes not read.
PIXMAP_HEADER_SIZE = 24
BITS_PER_PIXEL = 24


@dataclass(frozen=True)
class PictureRecord:
    """One drawing operation of a Picture."""

    opcode: int
    payload: bytes

    @property
    def label(self) -> str:
        """The operation number, then its name when the wiki lists one."""
        name = PICTURE_OPERATIONS.get(self.opcode)
        return f"{self.opcode} {name}" if name else str(self.opcode)


@dataclass(frozen=True)
class Pixmap:
    """The pixels of a copy pixmap record: 24-bit red, green, blue, row by row."""

    width: int
    height: int
    pixels: bytes


def read_pixmap(payload: bytes) -> Pixmap | str:
    """The pixmap a copy pixmap record holds, or why it is not read."""
    if len(payload) < PIXMAP_HEADER_SIZE:
        return f"its payload is {len(payload)} bytes, short of its 24 byte header"
    count, _, width, height, depth = PIXMAP_HEADER.unpack_from(payload)
    if depth != BITS_PER_PIXEL:
        return f"its pixels are {depth} bits each, not 24"
    if width != height:
        return (
            f"it is {width} x {height}: which of the two is the width is not "
            "confirmed by a pixmap that is not square"
        )
    pixels = payload[PIXMAP_HEADER_SIZE:]
    if count != len(pixels) or count != width * height * 3:
        return (
            f"its {width} x {height} pixels do not fill its stored {count} bytes "
            f"({len(pixels)} follow the header)"
        )
    return Pixmap(width, height, pixels)


@dataclass(frozen=True)
class PictureData:
    """A Picture that holds drawing: its records in stored order."""

    records: tuple[PictureRecord, ...]

    @property
    def byte_count(self) -> int:
        """The stored size of the drawing, without its length prefix."""
        return sum(RECORD_HEADER.size + len(r.payload) for r in self.records)

    def __str__(self) -> str:
        shown = ", ".join(f"{r.label} ({len(r.payload)} bytes)" for r in self.records)
        return f"<Picture, {self.byte_count} bytes: {shown}>"

    def pixmap(self) -> Pixmap | None:
        """The one copy pixmap this is, else None (see ``undecoded``)."""
        if self.undecoded() is not None:
            return None
        result = read_pixmap(self.records[0].payload)
        return result if isinstance(result, Pixmap) else None

    def undecoded(self) -> str | None:
        """Why this cannot be written as an image, else None: it is exactly one
        copy pixmap record whose pixels are read."""
        others = [r for r in self.records if r.opcode != COPY_PIXMAP]
        if others:
            labels = ", ".join(dict.fromkeys(r.label for r in others))
            return f"its drawing operations {labels} are not decoded"
        if len(self.records) != 1:
            return (
                f"it has {len(self.records)} copy pixmap records, and where each "
                "is placed is not confirmed"
            )
        result = read_pixmap(self.records[0].payload)
        if isinstance(result, str):
            return f"its copy pixmap record is not read: {result}"
        return None

    def reason(self) -> str:
        why = self.undecoded()
        suffix = f" ({why})" if why else ""
        return f"a Picture of {self.byte_count} bytes{suffix}"


def walk_picture(data: bytes) -> PictureData | None:
    """The records of the stored drawing ``data`` (its length prefix removed), or
    None when the record lengths do not end exactly at its end."""
    records: list[PictureRecord] = []
    offset = 0
    while offset < len(data):
        if offset + RECORD_HEADER.size > len(data):
            return None
        opcode, length = RECORD_HEADER.unpack_from(data, offset)
        offset += RECORD_HEADER.size
        if offset + length > len(data):
            return None
        records.append(PictureRecord(opcode, bytes(data[offset : offset + length])))
        offset += length
    return PictureData(tuple(records))
