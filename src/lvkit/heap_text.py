"""The text form of a saved value (``DefaultData``) in the extracted heap XML.

XML cannot hold a NUL or most control bytes even inside CDATA, so a binary value
is written as printable ASCII plus ``&#xNN;`` byte entities -- six characters
for a byte that is often zero. A large value (a graph's preallocated buffer is
hundreds of megabytes, almost all zero) is instead written as
``lvkit-zlib:<base64 of the zlib-compressed bytes>``, which is small and
lossless. :func:`decode_default_data` is the inverse of :func:`encode_default_data`;
the parser's ``decode_xml_entities_to_bytes`` calls it.
"""

from __future__ import annotations

import base64
import zlib

COMPRESSED_PREFIX = "lvkit-zlib:"

# A value at least this many bytes is written compressed.
COMPRESS_MIN_BYTES = 64 * 1024

# One text form per byte value, built once: printable ASCII (minus the wrapping
# quote and XML/CDATA-special bytes) stays literal; everything else is its exact
# byte entity, so a value round-trips byte-for-byte through CDATA.
_BYTE_TEXT = tuple(
    chr(b)
    if 0x20 <= b <= 0x7E and b not in (0x22, 0x26, 0x3C, 0x3E)
    else f"&#x{b:02x};"
    for b in range(256)
)

_PREFIX_BYTES = COMPRESSED_PREFIX.encode("ascii")


def encode_default_data(content: bytes) -> str:
    """The quoted text of a saved value's bytes. A value whose own text would
    begin with the compression prefix is compressed too, so a compressed form is
    never mistaken for a plain one."""
    if len(content) >= COMPRESS_MIN_BYTES or content.startswith(_PREFIX_BYTES):
        packed = base64.b64encode(zlib.compress(content)).decode("ascii")
        return f'"{COMPRESSED_PREFIX}{packed}"'
    return '"' + "".join(map(_BYTE_TEXT.__getitem__, content)) + '"'


def decode_default_data(text: str) -> bytes | None:
    """The bytes of a compressed value's text (quotes already stripped), or
    ``None`` when ``text`` is the plain entity form."""
    if not text.startswith(COMPRESSED_PREFIX):
        return None
    return zlib.decompress(base64.b64decode(text[len(COMPRESSED_PREFIX) :]))
