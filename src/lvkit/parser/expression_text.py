"""Read an Expression Node's formula from the VI's text record.

A VI saved by an older LabVIEW keeps a diagram label's text in a numbered text
record (``<textRec>28</textRec>``, the number in hex) that pylabview leaves as
the raw sidecar ``<stem>_TRec<n>.bin`` (n in decimal) next to the block-diagram
XML. The record is a 72-byte header, a big-endian 4-byte text length at offset
72, then the text.
"""

from __future__ import annotations

import struct
from pathlib import Path

from .models import ParsedNode
from .node_types import ExpressionNode

_LENGTH_OFFSET = 72
_TEXT_OFFSET = _LENGTH_OFFSET + 4


def read_text_record(bd_xml: Path, record: int) -> str | None:
    """The text of text record ``record`` of the VI whose block-diagram XML is
    ``bd_xml``, or ``None`` when the sidecar is missing or its length does not
    match its text."""
    sidecar = bd_xml.with_name(bd_xml.name.replace("_BDHb.xml", f"_TRec{record}.bin"))
    if not sidecar.is_file():
        return None
    data = sidecar.read_bytes()
    if len(data) < _TEXT_OFFSET:
        return None
    (length,) = struct.unpack(">I", data[_LENGTH_OFFSET:_TEXT_OFFSET])
    if len(data) != _TEXT_OFFSET + length:
        return None
    return data[_TEXT_OFFSET:].decode("latin-1").strip()


def resolve_expression_texts(nodes: list[ParsedNode], bd_xml: Path) -> None:
    """Fill in, in place, the formula of each Expression Node whose text is in
    a text record."""
    for node in nodes:
        if (
            isinstance(node, ExpressionNode)
            and node.script is None
            and node.text_record is not None
        ):
            node.script = read_text_record(bd_xml, node.text_record)
