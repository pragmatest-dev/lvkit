"""A class's private-data control saved inside its ``.lvclass`` file.

A source-only ``.lvclass`` whose private-data control is not a file of its own
keeps the control in its ``NI.LVClass.FlattenedPrivateDataCTL`` XML property, as
a LabVIEW packed string. Decoded, that string holds the control's own ``.ctl``
file, byte for byte (its ``RSRC`` container with its ``VCTP``/``FPHb``/link
blocks), after a short wrapper. :func:`embedded_private_data_copy` writes those
bytes to the cache, so the control is read the way every ``.ctl`` is read
(``graph/loading.py``'s ``load_typedef``), at its path inside the class.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from lvkit.cache_paths import _slug, global_cache_root
from lvkit.extractor import atomic_write_bytes

_PROPERTY_RE = re.compile(
    r'<Property Name="NI\.LVClass\.FlattenedPrivateDataCTL"[^>]*>(.*?)</Property>',
    re.S,
)

# The RSRC container signature pylabview's RSRCHeader.checkSanity() accepts:
# fmtver>=3 files use the CRLF form, older ones a NUL-padded form.
_RSRC_MAGICS = (b"RSRC\r\n", b"RSRC\x00\x00")


def decode_flattened_ctl_val(val_text: str) -> bytes:
    """Decode a LabVIEW packed-string XML property value to raw bytes.

    Each character encodes 6 bits (``ord(ch) - 0x21``, alphabet ``'!'``..).
    """
    s = html.unescape(val_text)
    out = bytearray()
    bits = 0
    acc = 0
    for ch in s:
        v = ord(ch) - 0x21
        if v < 0 or v > 63:
            continue
        acc = (acc << 6) | v
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


def _find_embedded_rsrc(raw: bytes) -> bytes | None:
    """The control's file inside a decoded property: the bytes from its RSRC
    signature onward (the wrapper before it varies in length), or None when
    the property holds no RSRC container."""
    found = [i for magic in _RSRC_MAGICS if (i := raw.find(magic)) != -1]
    return raw[min(found) :] if found else None


def embedded_private_data_control(lvclass_path: Path) -> bytes | None:
    """The bytes of the private-data control the ``.lvclass`` at
    ``lvclass_path`` keeps inside itself, or None when it keeps none."""
    text = lvclass_path.read_text(encoding="utf-8", errors="replace")
    match = _PROPERTY_RE.search(text)
    if match is None:
        return None
    return _find_embedded_rsrc(decode_flattened_ctl_val(match.group(1)))


def embedded_private_data_copy(lvclass_path: Path, ctl_name: str) -> Path | None:
    """A cache file holding the private-data control the ``.lvclass`` keeps
    inside itself, named ``ctl_name`` (the name the class records for it), or
    None when the class keeps none. The file is rewritten on every call, so it
    always matches the class file."""
    data = embedded_private_data_control(lvclass_path)
    if data is None:
        return None
    copy = global_cache_root() / "lvclass" / _slug(lvclass_path.resolve()) / ctl_name
    atomic_write_bytes(copy, data)
    return copy
