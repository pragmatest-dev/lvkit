"""The tags a VI, a library or a class saves with its file (``SavedTag``).

A tag is a name and a value (a LabVIEW Variant). The Tag methods of the VI Server
read and write them: Get Tag, Set Tag and Remove Tag on a VI reference, Get Lib
Tag, Set Lib Tag and Delete Lib Tag on a Library reference
(``codegen/nodes/tag_methods.py``). The format is in
``docs/_internal/design/labview-binary-format.md`` ("Saved tags").

- A VI saves its tags in the ``VITS`` block, which pylabview leaves as the
  ``*_VITS.bin`` file beside the main XML (``read_vi_tags``).
- A ``.lvlib`` or ``.lvclass`` file saves them as ``<Property>`` elements of its
  root, in one namespace with the library's own properties (``read_property_tags``).

A value this module cannot decode is kept as an undecoded tag (``SavedTag.undecoded``
says why), so reading that tag later is an error naming it, and every other tag of
the file is still read.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from lvkit.models import LVType, LVTypeKind, SavedTag, VariantData

from .vi import _decode_element

_VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
_STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_BOOLEAN = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")

# The text of a string property that holds bytes: each byte as a decimal number,
# separated by spaces.
_BYTES_TEXT = re.compile(r"\d{1,3}( \d{1,3})+")


def _lstr(data: bytes, pos: int) -> tuple[bytes, int] | None:
    """The string of a 4-byte length and its bytes at ``pos``, and the position
    after it; None when the data ends first."""
    if pos + 4 > len(data):
        return None
    size = int.from_bytes(data[pos : pos + 4], "big")
    end = pos + 4 + size
    if end > len(data):
        return None
    return data[pos + 4 : end], end


def read_vi_tags_beside(main_xml: Path) -> list[SavedTag]:
    """The tags of the VI whose main XML is ``main_xml``: its ``*_VITS.bin``
    sidecar, none when the VI saves none."""
    vits = main_xml.with_name(main_xml.stem + "_VITS.bin")
    return read_vi_tags(vits) if vits.exists() else []


def read_vi_tags(vits: Path) -> list[SavedTag]:
    """The tags of the ``VITS`` block in ``vits`` (the ``*_VITS.bin`` file): a
    4-byte tag count, then for each tag its name (a 4-byte length and the bytes)
    and its value (a 4-byte size and a flattened Variant of that size). A tag that
    does not read ends the list with an undecoded tag, because the position of
    the ones after it is lost."""
    data = vits.read_bytes()
    if len(data) < 4:
        return []
    count = int.from_bytes(data[:4], "big")
    pos = 4
    tags: list[SavedTag] = []
    for _ in range(count):
        named = _lstr(data, pos)
        if named is None:
            return tags
        name = named[0].decode("latin-1")
        pos = named[1]
        sized = _lstr(data, pos)
        if sized is None:
            tags.append(
                SavedTag(name, None, "its value ends past the end of the block")
            )
            return tags
        variant, pos = sized
        tags.append(_variant_tag(name, variant))
    return tags


def _variant_tag(name: str, variant: bytes) -> SavedTag:
    """The tag ``name`` whose flattened Variant is ``variant``."""
    _, consumed, held = _decode_element(variant, _VARIANT)
    if not isinstance(held, VariantData) or consumed != len(variant):
        reason = f"its {len(variant)}-byte Variant is in a form lvkit does not read"
        return SavedTag(name, None, reason)
    return SavedTag(name, held)


def _string_value(text: str) -> str:
    """The Python literal of a string property's value: the bytes it holds as
    decimal numbers, else its text."""
    if _BYTES_TEXT.fullmatch(text) and all(int(n) < 256 for n in text.split(" ")):
        return repr(bytes(int(n) for n in text.split(" ")).decode("latin-1"))
    return repr(text)


def _boolean_value(text: str) -> str:
    return "True" if text.strip().lower() == "true" else "False"


# The property types whose value is read, with the type of the Variant the tag holds
# and the literal of its value. Another type (``Int``, ``UInt``, ``Bin``, ``Xml``)
# records no width or layout, so its tags stay undecoded.
_PROPERTY_TYPES: dict[str, tuple[LVType, Callable[[str], str]]] = {
    "Str": (_STRING, _string_value),
    "Bool": (_BOOLEAN, _boolean_value),
}


def read_property_tags(root: ET.Element) -> list[SavedTag]:
    """The ``<Property>`` children of the root of a ``.lvlib`` or ``.lvclass`` file
    as tags, in file order."""
    tags: list[SavedTag] = []
    for prop in root.findall("Property"):
        name = prop.get("Name")
        if not name:
            continue
        kind = prop.get("Type") or ""
        read = _PROPERTY_TYPES.get(kind)
        if read is None:
            reason = f"its property type {kind or '(none)'} has no read"
            tags.append(SavedTag(name, None, reason))
            continue
        lv_type, literal = read
        tags.append(SavedTag(name, VariantData(lv_type, literal(prop.text or ""))))
    return tags
