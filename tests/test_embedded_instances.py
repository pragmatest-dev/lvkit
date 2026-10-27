"""A call to a malleable VI is recorded as an embedded instance named by a GUID;
the instance's own link table says which VI it is an instance of.

Hand-built instances stand in for the extraction step, so these tests need no
sample corpus; ``test_vi_file_types_corpus.py`` covers real callers.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.embedded_instances import read_instance_sources
from lvkit.parser.link_table import (
    LinkTableError,
    LinkTableVersion,
    decode_instance_source,
)
from lvkit.parser.metadata import parse_instance_files

_VERSION = LinkTableVersion(major=21, minor=0, bugfix=0, stage=4)


def _pascal(text: str) -> bytes:
    raw = text.encode("ascii")
    return bytes([len(raw)]) + raw


def _align(buf: bytearray, boundary: int) -> None:
    while len(buf) % boundary:
        buf.append(0)


def _path(tokens: list[str]) -> bytes:
    body = struct.pack(">HH", 1, len(tokens)) + b"".join(_pascal(t) for t in tokens)
    return b"PTH0" + struct.pack(">I", len(body)) + body


def _table(records: list[tuple[bytes, list[str], list[str]]]) -> bytes:
    """A link table: each record is (ident, qualified name parts, path tokens),
    introduced by the 2-byte next-link marker, its name aligned to 4 and its path
    to 2, followed by a u32 flag."""
    buf = bytearray(b"\x00\x01LVIN")
    for ident, parts, tokens in records:
        buf += b"\x00\x02" + ident
        _align(buf, 4)
        buf += struct.pack(">I", len(parts)) + b"".join(_pascal(p) for p in parts)
        _align(buf, 2)
        buf += _path(tokens) + struct.pack(">I", 4)
    return bytes(buf)


def test_decode_instance_source_reads_first_vivi_after_ivov() -> None:
    data = _table(
        [
            (b"IVOV", [], [""]),
            (b"VIVI", ["Adapt.vim"], ["", "", "Helpers", "Adapt.vim"]),
            (b"VICC", ["Lib.lvlib", "Type.ctl"], ["", "Type.ctl"]),
            (b"VIVI", ["Other.vi"], ["", "", "Other.vi"]),
        ]
    )

    link = decode_instance_source(data, _VERSION)

    assert link is not None
    assert link.name_parts == ("Adapt.vim",)
    assert link.path_tokens == ("", "", "Helpers", "Adapt.vim")


def test_decode_instance_source_keeps_a_library_qualified_name() -> None:
    data = _table(
        [
            (b"IVOV", [], [""]),
            (b"VIVI", ["Lib.lvlib", "Adapt.vim"], ["", "", "Adapt.vim"]),
        ]
    )

    link = decode_instance_source(data, _VERSION)

    assert link is not None
    assert link.name_parts == ("Lib.lvlib", "Adapt.vim")


def test_decode_instance_source_is_none_without_ivov_or_vivi() -> None:
    assert (
        decode_instance_source(_table([(b"VIVI", ["A.vi"], ["", "A.vi"])]), _VERSION)
        is None
    )
    assert decode_instance_source(_table([(b"IVOV", [], [""])]), _VERSION) is None


def test_decode_instance_source_rejects_a_truncated_record() -> None:
    data = _table([(b"IVOV", [], [""]), (b"VIVI", ["Adapt.vim"], ["", "Adapt.vim"])])
    with pytest.raises(LinkTableError, match="VIVI record"):
        decode_instance_source(data[:-40], _VERSION)


_INSTANCE_XML = """<?xml version='1.0' encoding='utf-8'?>
<RSRC Type="LVIN">
  <LVSR><Section Index="0" Name="{name}">
    <Version Major="21" Minor="0" Bugfix="0" Stage="release" Build="0" />
  </Section></LVSR>
  <LIvi><Section Index="0" Format="inline"><LVIN>
{links}
  </LVIN></Section></LIvi>
</RSRC>
"""

_IVOV = '<IVOV LinkSaveFlag="3"><LinkSaveQualName /></IVOV>'


def _vivi(names: list[str], tokens: list[str]) -> str:
    qual = "".join(f"<String>{n}</String>" for n in names)
    path = "".join(f"<String>{t}</String>" if t else "<String />" for t in tokens)
    return (
        "<VIVI LinkSaveFlag='4'>"
        f"<LinkSaveQualName>{qual}</LinkSaveQualName>"
        f"<LinkSavePathRef>{path}</LinkSavePathRef></VIVI>"
    )


def _names_bin(names: list[str]) -> bytes:
    slots = b"".join(_pascal(n).ljust(256, b"\x00") for n in names)
    return struct.pack(">I", len(names)) + slots


def _caller(tmp_path: Path, names: list[str]) -> list[str]:
    """The caller's ``VINS`` sidecar files, written beside ``tmp_path``."""
    files = [f"vi_VINS{i}.bin" for i in range(len(names) + 1)]
    (tmp_path / files[0]).write_bytes(_names_bin(names))
    for file_name in files[1:]:
        (tmp_path / file_name).write_bytes(b"embedded")
    return files


def test_instance_sources_are_keyed_by_vins_name_and_caller_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ["aaaa.vi", "bbbb.vi", "cccc.vi"]
    files = _caller(tmp_path, names)
    instance_xmls = {
        "vi_VINS1.bin": _INSTANCE_XML.format(
            # The instance's own saved name is not the key: the VINS name list is.
            name="zzzz.vi",
            links=_IVOV + _vivi(["Adapt.vim"], ["", "", "Helpers", "Adapt.vim"]),
        ),
        "vi_VINS2.bin": _INSTANCE_XML.format(
            name="bbbb.vi",
            links=_IVOV
            + _vivi(["Lib.lvlib", "Adapt.vim"], ["", "", "Adapt.vim"])
            + _vivi(["Dep.vi"], ["", "", "Dep.vi"]),
        ),
        # Not an instance of another VI: no instance-to-owner link.
        "vi_VINS3.bin": _INSTANCE_XML.format(
            name="cccc.vi", links=_vivi(["Dep.vi"], ["", "", "Dep.vi"])
        ),
    }

    def fake_extract(path: Path):
        xml = tmp_path / f"{path.stem}.instance.xml"
        xml.write_text(instance_xmls[path.name], encoding="utf-8")
        return xml, None, xml

    monkeypatch.setattr("lvkit.embedded_instances.extract_vi_xml", fake_extract)

    sources = read_instance_sources(tmp_path, files)

    assert sorted(sources) == ["aaaa.vi", "bbbb.vi"]
    first = sources["aaaa.vi"]
    assert (first.name, first.qualified_name) == ("Adapt.vim", "Adapt.vim")
    # One leading empty is the instance's own level inside its caller.
    assert first.path_tokens == ["", "Helpers", "Adapt.vim"]
    second = sources["bbbb.vi"]
    assert (second.name, second.qualified_name) == ("Adapt.vim", "Lib.lvlib:Adapt.vim")
    assert second.path_tokens == ["", "Adapt.vim"]
    # A caller-relative ref resolves like any other recorded dependency.
    caller_file = tmp_path / "Caller.vi"
    assert first.resolve_against(caller_file) == (tmp_path / "Helpers/Adapt.vim")


def test_instance_source_is_read_from_the_raw_table_when_extraction_left_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _caller(tmp_path, ["aaaa.vi"])
    table = _table(
        [
            (b"IVOV", [], [""]),
            (b"VIVI", ["Adapt.vim"], ["", "", "Helpers", "Adapt.vim"]),
        ]
    )
    (tmp_path / "vi_LIvi.bin").write_bytes(table)
    xml = tmp_path / "instance.xml"
    xml.write_text(
        '<RSRC><LVSR><Section Name="aaaa.vi">'
        '<Version Major="21" Minor="0" Bugfix="0" Stage="release" Build="0" />'
        "</Section></LVSR>"
        '<LIvi><Section Index="0" Format="bin" File="vi_LIvi.bin" /></LIvi></RSRC>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lvkit.embedded_instances.extract_vi_xml", lambda _p: (xml, None, xml)
    )

    sources = read_instance_sources(tmp_path, files)

    assert list(sources) == ["aaaa.vi"]
    assert sources["aaaa.vi"].path_tokens == ["", "Helpers", "Adapt.vim"]


def test_caller_without_instances_has_no_sources(tmp_path: Path) -> None:
    assert read_instance_sources(tmp_path, []) == {}


def test_parse_instance_files_lists_vins_sections_in_section_order() -> None:
    root = ET.fromstring(
        "<RSRC><VINS>"
        '<Section Index="2" Format="bin" File="vi_VINS2.bin" />'
        '<Section Index="0" Format="bin" File="vi_VINS0.bin" />'
        '<Section Index="1" Format="bin" File="vi_VINS1.bin" />'
        "</VINS></RSRC>"
    )

    assert parse_instance_files(root) == [
        "vi_VINS0.bin",
        "vi_VINS1.bin",
        "vi_VINS2.bin",
    ]
    assert parse_instance_files(ET.fromstring("<RSRC />")) == []
