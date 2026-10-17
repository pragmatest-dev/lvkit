"""Block-diagram link-table decoding (``IUVI`` records of ``_LIbd.bin``)."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.extractor import extract_vi_xml
from lvkit.parser.link_table import (
    IUseLink,
    LinkTableError,
    LinkTableVersion,
    decode_iuse_links,
    qualified_link_name,
    read_iuse_links,
)

SAMPLES = Path(".lvkit/cache/samples")
LV_ICON = SAMPLES / "ni-labview-icon-editor/resource/plugins/lv_icon.vi"

V20 = LinkTableVersion(20, 0, 0, 4)
V12 = LinkTableVersion(12, 0, 0, 4)
V86 = LinkTableVersion(8, 6, 0, 4)


def _pth0(tokens: list[bytes], tpval: int = 1) -> bytes:
    body = struct.pack(">HH", tpval, len(tokens)) + b"".join(
        bytes([len(t)]) + t for t in tokens
    )
    return b"PTH0" + struct.pack(">I", len(body)) + body


def build_iuvi_record(
    *,
    offset: int,
    name_parts: list[bytes],
    path_tokens: list[bytes],
    node_uids: list[int],
    link_flag: int = 0,
    version: LinkTableVersion = V20,
) -> bytes:
    """One ``IUVI`` record laid out as pylabview writes it, placed at absolute
    ``offset`` in the sidecar (the alignment steps count from the sidecar's
    start). Returns the bytes from the ``00 02`` item marker through the
    record's end."""
    buf = bytearray(b"\x00\x02IUVI")

    def pad(boundary: int) -> None:
        while (offset + len(buf)) % boundary:
            buf.append(0)

    pad(4)
    buf += struct.pack(">I", len(name_parts))
    for part in name_parts:
        buf += bytes([len(part)]) + part
    pad(2)
    buf += _pth0(path_tokens)
    if version.at_least(8, 6, 0, 1):
        buf += struct.pack(">I", link_flag)
    elif version.at_least(8, 5, 0, 1):
        buf += bytes([link_flag])
    buf += struct.pack(">H", 0)  # type id
    if version.at_least(14, 0, 0, 3):
        buf += bytes([0x42])
    else:
        buf += struct.pack(">IQ", 1, 0) + b"\x00" * 4 + b"\x00" * 4 + b"\x00" * 4
    if version.at_least(12, 0, 0, 3):
        buf += struct.pack(">I", 0)
    buf += struct.pack(">I", len(node_uids))
    for uid in node_uids:
        buf += struct.pack(">I", uid)
    buf += _pth0([], tpval=0)
    buf += b"\x00"  # empty iuse string
    pad(2)
    return bytes(buf)


def test_decodes_a_one_part_name_with_every_uid() -> None:
    data = b"\x00\x01BDHP" + build_iuvi_record(
        offset=6,
        name_parts=[b"Data.vi"],
        path_tokens=[b"", b"NIIconEditor", b"Global Variables", b"Data.vi"],
        node_uids=[22767, 23163, 18698, 19484],
    )
    assert decode_iuse_links(data, V20) == [
        IUseLink(
            name_parts=("Data.vi",),
            path_tokens=("", "NIIconEditor", "Global Variables", "Data.vi"),
            link_flag=0,
            node_uids=(22767, 23163, 18698, 19484),
        )
    ]


@pytest.mark.parametrize("version", [V20, V12, V86])
@pytest.mark.parametrize("lead", [0, 1, 2, 3])
def test_follows_alignment_and_version_layout(
    version: LinkTableVersion, lead: int
) -> None:
    prefix = b"\x00" * lead
    first = build_iuvi_record(
        offset=len(prefix),
        name_parts=[b"Lib.lvlib", b"Odd.vi", b"x"],
        path_tokens=[b"", b"a.vi"],
        node_uids=[7],
        link_flag=2,
        version=version,
    )
    second_offset = len(prefix) + len(first)
    second = build_iuvi_record(
        offset=second_offset,
        name_parts=[b"B.vi"],
        path_tokens=[b"", b"", b"B.vi"],
        node_uids=[],
        version=version,
    )
    links = decode_iuse_links(prefix + first + second, version)
    assert [link.name_parts for link in links] == [
        ("Lib.lvlib", "Odd.vi", "x"),
        ("B.vi",),
    ]
    assert [link.node_uids for link in links] == [(7,), ()]
    assert links[0].link_flag == (2 if version.at_least(8, 5, 0, 1) else 0)


def test_a_version_older_than_the_oldest_layout_is_an_error() -> None:
    data = build_iuvi_record(
        offset=0, name_parts=[b"A.vi"], path_tokens=[b"A.vi"], node_uids=[1]
    )
    with pytest.raises(LinkTableError, match="older than the oldest supported"):
        decode_iuse_links(data, LinkTableVersion(8, 0, 0, 4))


def test_a_malformed_record_is_an_error_not_skipped() -> None:
    data = build_iuvi_record(
        offset=0, name_parts=[b"A.vi"], path_tokens=[b"A.vi"], node_uids=[1]
    )
    with pytest.raises(LinkTableError):
        decode_iuse_links(data[:-12], V20)


def test_a_table_without_iuvi_records_is_empty() -> None:
    assert decode_iuse_links(b"\x00\x01BDHP\x00\x00\x00\x00", V20) == []


def test_qualified_link_name_rules() -> None:
    assert qualified_link_name(["A.lvclass", "m.vi"], 0, None) == "A.lvclass:m.vi"
    assert qualified_link_name(["\nToTable.vi"], 0, None) == "ToTable.vi"
    assert (
        qualified_link_name(["Data.vi"], 2, "lv_icon.lvlib") == "lv_icon.lvlib:Data.vi"
    )
    assert qualified_link_name(["Data.vi"], 0, "lv_icon.lvlib") == "Data.vi"
    assert qualified_link_name(["", " "], 0, None) is None


def _sidecar(vi: Path) -> tuple[Path, LinkTableVersion]:
    _bd, _fp, main_xml = extract_vi_xml(vi)
    assert main_xml is not None
    version = ET.parse(main_xml).getroot().find(".//LVSR/Section/Version")
    assert version is not None
    return main_xml.with_name(main_xml.stem + "_LIbd.bin"), LinkTableVersion.from_lvsr(
        version
    )


@pytest.mark.needs_samples
def test_real_sidecar_lists_every_node_using_a_global_vi() -> None:
    libd, version = _sidecar(LV_ICON)
    by_name = {link.name_parts: link for link in read_iuse_links(libd, version)}
    data = by_name[("Data.vi",)]
    assert data.node_uids == (22767, 23163, 18698, 19484)
    assert data.path_tokens == ("", "NIIconEditor", "Global Variables", "Data.vi")


@pytest.mark.needs_samples
def test_real_sidecar_reads_a_node_per_subvi_call_not_just_the_first() -> None:
    libd, version = _sidecar(
        SAMPLES / "ni-labview-icon-editor/resource/plugins/lv_icon.vi"
    )
    links = read_iuse_links(libd, version)
    assert len(links) > 10
    assert any(len(link.node_uids) > 1 for link in links)
    assert all(link.path_tokens[-1].endswith(".vi") for link in links)
