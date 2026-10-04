"""Regression tests for localized LabVIEW text."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.models import LVType, LVTypeKind
from lvkit.parser.metadata import parse_iuse_from_libd
from lvkit.parser.vi import _decode_default_data, _decode_element
from lvkit.text_encoding import (
    decode_labview_text,
    labview_text_encoding,
    normalize_extracted_xml,
    vers_language_encoding,
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


def test_vers_language_encoding_maps_documented_values():
    """pylabview wiki's Blocks.md ("vers" section) -- the only values ever
    actually seen. 0 is deliberately unmapped: LabVIEW 20+ always writes 0
    regardless of the real authoring locale (the wiki notes non-zero was only
    seen in LV 8.0.0f5-19.0.0f5), so 0 is ambiguous, not a real "English"
    signal."""
    assert vers_language_encoding(33) == "cp936"  # Chinese
    assert vers_language_encoding(14) == "cp932"  # Japanese
    assert vers_language_encoding(23) == "cp949"  # Korean
    assert vers_language_encoding(1) == "cp1252"  # French
    assert vers_language_encoding(3) == "cp1252"  # German
    assert vers_language_encoding(0) is None
    assert vers_language_encoding(99) is None


def test_labview_text_encoding_prefers_vers_language_over_platform_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VI's own recorded Language beats the reading machine's platform
    guess -- it names the ACTUAL save-time locale instead of assuming this
    machine's own locale matches it (true even on Windows: GetACP() is the
    reader's own regional setting, not the VI's)."""
    monkeypatch.delenv("LVKIT_TEXT_ENCODING", raising=False)
    monkeypatch.setattr("lvkit.text_encoding.sys.platform", "win32")
    monkeypatch.setattr(
        "lvkit.text_encoding._windows_ansi_encoding", lambda: "cp1252"
    )
    assert labview_text_encoding(vers_language=33) == "cp936"
    # An unrecognized/zero Language falls through to the platform default.
    assert labview_text_encoding(vers_language=0) == "cp1252"


def test_labview_text_encoding_env_override_beats_vers_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit LVKIT_TEXT_ENCODING override is the reader's own, more
    specific instruction -- it wins even over a real recorded Language."""
    monkeypatch.setenv("LVKIT_TEXT_ENCODING", "gbk")
    assert labview_text_encoding(vers_language=14) == "gbk"


def test_non_windows_non_mac_defaults_to_cp1252_not_reader_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real corpus VI's legacy front-panel caption recorded a degree sign
    as a raw Windows-1252 byte (0xB0) -- this reading machine's own OS locale
    (UTF-8 on Linux) has nothing to do with the VI's save-time encoding, and
    decoding that byte as UTF-8 corrupted it to U+FFFD. LabVIEW's own most
    common legacy Windows default (cp1252) recovers it; the reader's locale
    is pure coincidence."""
    monkeypatch.setattr("lvkit.text_encoding.sys.platform", "linux")
    monkeypatch.delenv("LVKIT_TEXT_ENCODING", raising=False)
    assert labview_text_encoding() == "cp1252"
    assert bytes([0xB0]).decode("mac_roman").encode("mac_roman").decode(
        labview_text_encoding()
    ) == "°"


def test_string_codegen_is_byte_faithful_display_uses_labview_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LabVIEW String is a byte array: the CODEGEN value is byte-faithful
    (latin-1, so binary bytes survive losslessly into generated code), while the
    human-readable DISPLAY value is codepage-decoded. Two separate values, like
    Terminal.name (codegen) vs display_name (display)."""
    from lvkit.graph.construction import decode_constant
    from lvkit.parser.models import ParsedConstant

    monkeypatch.setattr(
        "lvkit.text_encoding.labview_text_encoding",
        lambda: "gbk",
    )
    encoded = "打开连接".encode("gbk")
    data = len(encoded).to_bytes(4, "big") + encoded
    string_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")

    # Codegen decoders are byte-faithful (latin-1): the raw bytes round-trip.
    byte_faithful = encoded.decode("latin-1")
    assert _decode_default_data(_byte_entities(data), "stdString") == (
        '"' + byte_faithful.replace("\\", "\\\\").replace('"', '\\"') + '"',
        None,
    )
    assert _decode_element(data, string_type) == (
        repr(byte_faithful),
        len(data),
        repr(byte_faithful),
    )

    # A string constant carries BOTH: codegen value (byte-faithful) and
    # display_value (codepage-decoded, readable CJK).
    _, value, display_value = decode_constant(
        ParsedConstant(uid="c", type_desc="", value=data.hex()),
        lv_type=string_type,
    )
    assert value == repr(byte_faithful)
    assert display_value == repr("打开连接")


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
