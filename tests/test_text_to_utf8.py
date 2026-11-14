"""Text to UTF-8 (23066) and its reverse (23067): strings are bytes (latin-1) in
a Windows code page (``encoding``; -1 is the system's) or in UTF-8."""

from __future__ import annotations

import locale

import pytest

from lvkit.primitive_resolver import get_resolver
from tests.prim_vi import Pin, generate, scalar


def _convert(prim_id: int, encoding_wired: bool = True):
    controls = {"text": scalar("String")}
    pins = [
        Pin(11, "input", scalar("String"), source="text"),
        Pin(
            10,
            "input",
            scalar("NumInt32"),
            source="encoding" if encoding_wired else None,
        ),
        Pin(3, "output", scalar("String"), sink="out"),
    ]
    if encoding_wired:
        controls["encoding"] = scalar("NumInt32")
    return generate(prim_id, "Text to UTF-8", pins, controls, {"out": scalar("String")})


def test_the_catalog_layouts_are_the_callers() -> None:
    for prim_id in (23066, 23067):
        entry = get_resolver().resolve(prim_id=prim_id)
        assert entry is not None
        assert [(t.index, t.direction) for t in entry.terminals] == [
            (0, "output"),
            (3, "output"),
            (8, "input"),
            (10, "input"),
            (11, "input"),
        ]


def test_text_to_utf8_from_windows_1252() -> None:
    source, fn = _convert(23066)
    assert "lvkit" not in source
    assert fn("caf\xe9", 1252).out == "caf\xc3\xa9"
    assert fn("\x80", 1252).out == "\xe2\x82\xac"
    assert fn("plain", 1252).out == "plain"


def test_text_to_utf8_in_another_code_page() -> None:
    _, fn = _convert(23066)
    assert fn("\xe9", 1251).out == "\xd0\xb9"


def test_utf8_to_text_in_windows_1252() -> None:
    _, fn = _convert(23067)
    assert fn("caf\xc3\xa9", 1252).out == "caf\xe9"
    assert fn("\xe2\x82\xac", 1252).out == "\x80"


def test_minus_one_and_unwired_encoding_are_the_system_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252"
    )
    _, wired = _convert(23066)
    assert wired("\x80", -1).out == "\xe2\x82\xac"
    _, unwired = _convert(23066, encoding_wired=False)
    assert unwired("\x80").out == "\xe2\x82\xac"
    _, back = _convert(23067, encoding_wired=False)
    assert back("\xe2\x82\xac").out == "\x80"


def test_to_probe_string_is_a_gap_with_its_reason() -> None:
    _, fn = generate(
        2413,
        "To Probe String",
        [
            Pin(7, "input", scalar("String"), source="value"),
            Pin(3, "output", scalar("String"), sink="out"),
        ],
        {"value": scalar("String")},
        {"out": scalar("String")},
    )
    with pytest.raises(NotImplementedError, match="no NI page"):
        fn("x")
