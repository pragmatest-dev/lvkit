"""Regression tests for localized LabVIEW text."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.models import LVType
from lvkit.parser.metadata import parse_iuse_from_libd
from lvkit.parser.vi import _decode_default_data, _decode_element
from lvkit.text_encoding import (
    decode_labview_text,
    labview_text_encoding,
    normalize_extracted_xml,
)


def _byte_entities(data: bytes) -> str:
    return "".join(f"&#x{value:02X};" for value in data)


def test_normalize_extracted_xml_restores_native_text(tmp_path: Path) -> None:
    path = tmp_path / "中文.xml"
    mojibake = "当前状态".encode("gbk").decode("mac_roman")
    path.write_text(
        f'<RSRC Encoding="mac_roman"><Name>{mojibake}</Name><Note>🙂</Note></RSRC>',
        encoding="utf-8",
    )

    normalize_extracted_xml(path, "gbk")

    root = ET.parse(path).getroot()
    assert root.get("Encoding") == "gbk"
    assert root.findtext("Name") == "当前状态"
    assert root.findtext("Note") == "🙂"


def test_normalize_resets_decoder_at_xml_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "binary.xml"
    dangling_lead_byte = b"\x81".decode("mac_roman")
    path.write_text(
        f"<Root><Value><![CDATA[{dangling_lead_byte}]]></Value>"
        "<Next>intact</Next></Root>",
        encoding="utf-8",
    )

    normalize_extracted_xml(path, "gbk")

    root = ET.parse(path).getroot()
    assert root.findtext("Value") == "�"
    assert root.findtext("Next") == "intact"


def test_decode_labview_text_uses_requested_code_page() -> None:
    assert decode_labview_text("打开连接".encode("gbk"), "gbk") == "打开连接"


def test_windows_uses_active_ansi_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lvkit.text_encoding.sys.platform", "win32")
    monkeypatch.setattr(
        "lvkit.text_encoding._windows_ansi_encoding",
        lambda: "cp936",
    )
    assert labview_text_encoding() == "cp936"


def test_string_defaults_and_constants_use_labview_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lvkit.text_encoding.labview_text_encoding",
        lambda: "gbk",
    )
    encoded = "打开连接".encode("gbk")
    data = len(encoded).to_bytes(4, "big") + encoded

    assert (
        _decode_default_data(
            _byte_entities(data),
            "stdString",
        )
        == '"打开连接"'
    )
    assert _decode_element(
        data,
        LVType(kind="primitive", underlying_type="String"),
    ) == ("'打开连接'", len(data))


def test_libd_names_use_labview_encoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lvkit.text_encoding.labview_text_encoding",
        lambda: "gbk",
    )
    class_name = "流场控制.lvclass".encode("gbk")
    vi_name = "初始化.vi".encode("gbk")
    uid = 42
    data = (
        b"IUVI\x00\x02"
        + bytes([len(class_name)])
        + class_name
        + bytes([len(vi_name)])
        + vi_name
        + b"\x00\x00\x00\x01"
        + struct.pack(">I", uid)
        + b"PTH0"
    )
    path = tmp_path / "sample_LIbd.bin"
    path.write_bytes(data)

    assert parse_iuse_from_libd(path) == {str(uid): "流场控制.lvclass:初始化.vi"}


def test_env_override_beats_platform_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # LVKIT_TEXT_ENCODING lets a VI saved in another locale be read on this box;
    # it must win even over the platform's own code page.
    monkeypatch.setattr("lvkit.text_encoding.sys.platform", "win32")
    monkeypatch.setattr(
        "lvkit.text_encoding._windows_ansi_encoding",
        lambda: "cp1252",
    )
    monkeypatch.setenv("LVKIT_TEXT_ENCODING", "cp936")
    assert labview_text_encoding() == "cp936"

    monkeypatch.delenv("LVKIT_TEXT_ENCODING", raising=False)
    assert labview_text_encoding() == "cp1252"


def test_normalize_skips_pure_ascii(tmp_path: Path) -> None:
    # Pure ASCII is identical under mac_roman and every target, so the fast path
    # leaves the file byte-for-byte untouched (no transcode, no rewrite).
    path = tmp_path / "ascii.xml"
    original = '<RSRC Encoding="mac_roman"><Name>error out</Name></RSRC>'
    path.write_text(original, encoding="utf-8")

    normalize_extracted_xml(path, "gbk")  # non-mac_roman target, ASCII content

    assert path.read_text(encoding="utf-8") == original
