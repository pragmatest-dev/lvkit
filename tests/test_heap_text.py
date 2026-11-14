"""Saved values in the heap XML: small ones as byte entities, large ones as
zlib+base64 -- both must decode back to the exact bytes."""

from __future__ import annotations

import os

from lvkit.heap_text import (
    COMPRESS_MIN_BYTES,
    COMPRESSED_PREFIX,
    encode_default_data,
)
from lvkit.parser.utils import decode_xml_entities_to_bytes, strip_surrounding_quotes


def _round_trip(content: bytes) -> tuple[str, bytes]:
    text = encode_default_data(content)
    return text, decode_xml_entities_to_bytes(strip_surrounding_quotes(text))


def test_small_value_is_plain_entities_and_exact() -> None:
    content = b"\x00\x00\x00\x04abc\xff\"&<>\x7f"
    text, back = _round_trip(content)
    assert COMPRESSED_PREFIX not in text and "&#x00;" in text
    assert back == content


def test_large_sparse_value_is_compressed_and_exact() -> None:
    content = b"\x00" * (COMPRESS_MIN_BYTES * 200) + b"\x01\x02tail"
    text, back = _round_trip(content)
    assert text.startswith(f'"{COMPRESSED_PREFIX}')
    assert len(text) < len(content) // 100
    assert back == content


def test_large_incompressible_value_is_exact() -> None:
    content = os.urandom(COMPRESS_MIN_BYTES + 5)
    assert _round_trip(content)[1] == content


def test_small_value_that_looks_like_the_prefix_is_not_misread() -> None:
    content = COMPRESSED_PREFIX.encode() + b"AAAA"
    text, back = _round_trip(content)
    assert text.startswith(f'"{COMPRESSED_PREFIX}')  # compressed, so unambiguous
    assert back == content
