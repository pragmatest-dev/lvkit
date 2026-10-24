"""pylabview patch (10): the faster ``fullType`` and CDATA escape return exactly
what pylabview's own versions return, for every input."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

import pytest
from pylabview import LVdatatype  # type: ignore[import-untyped]

import lvkit.extractor  # noqa: F401  (installs the pylabview patches)

_ET = ElementTree.__dict__


def _type_object(otype: int) -> Any:
    td = object.__new__(LVdatatype.TDObject)
    td.otype = otype
    return td


def test_full_type_matches_pylabview_for_every_type_code() -> None:
    fast = LVdatatype.TDObject.fullType
    original = fast.__wrapped__
    known = [item.value for item in LVdatatype.TD_FULL_TYPE]
    for otype in [*known, *range(-16, 0x400)]:
        td = _type_object(otype)
        assert fast(td) == original(td)
        assert type(fast(td)) is type(original(td))


_TEXTS = [
    "",
    "plain text",
    "a & b < c > d",
    "tab\there\nnewline",
    "carriage\rreturn",
    *(f"x{chr(c)}y" for c in range(128)),
    "\x00\x01mixed\x1f",
    "ünïcödé ✓",
]


@pytest.mark.parametrize("text", _TEXTS)
def test_cdata_escape_matches_pylabview(text: str) -> None:
    fast = _ET["_escape_cdata"]
    assert fast(text) == fast.__wrapped__(text)


@pytest.mark.parametrize("value", [None, 5, b"bytes"])
def test_cdata_escape_rejects_non_text_like_pylabview(value: object) -> None:
    fast = _ET["_escape_cdata"]
    with pytest.raises(TypeError):
        fast.__wrapped__(value)
    with pytest.raises(TypeError):
        fast(value)
