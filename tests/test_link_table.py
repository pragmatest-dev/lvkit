"""Block-diagram link-table decoding (``IUVI`` records of ``_LIbd.bin``)."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.extractor import extract_vi_xml
from lvkit.models import SharedVariableKind
from lvkit.parser.link_table import (
    IUseLink,
    LinkTableError,
    LinkTableVersion,
    SharedVariableLink,
    decode_iuse_links,
    decode_shared_variable_links,
    qualified_link_name,
    read_iuse_links,
    read_shared_variable_links,
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


def _string32(text: str) -> bytes:
    raw = text.encode("ascii")
    return struct.pack(">I", len(raw)) + raw


def build_hpvl_record(
    *,
    offset: int,
    library: bytes,
    variable: bytes,
    persistent_id: str,
    node_uids: list[int],
    variable_type: str,
    name_parts: list[bytes] | None = None,
) -> bytes:
    """One ``HpVL`` record laid out as LabVIEW 25.0 writes it (see the
    ``parser/link_table.py`` module docstring), placed at absolute ``offset`` in
    the sidecar. Returns the bytes from the ``00 02`` item marker through the
    record's end."""
    buf = bytearray(b"\x00\x02HpVL")

    def pad(boundary: int) -> None:
        while (offset + len(buf)) % boundary:
            buf.append(0)

    pad(4)
    names = name_parts if name_parts is not None else [library, variable]
    buf += struct.pack(">I", len(names))
    for part in names:
        buf += bytes([len(part)]) + part
    pad(2)
    buf += _pth0([], tpval=0)
    buf += struct.pack(">I", 0)  # link flag
    buf += struct.pack(">H", 0)  # type id
    buf += bytes([0])  # no link reference follows (>= 14.0.0.3)
    item_type = b"NI_Variable_Variable"
    buf += bytes([len(item_type)]) + item_type
    buf += _string32("My Computer")
    buf += _string32(persistent_id)
    buf += struct.pack(">I", len(node_uids))
    for uid in node_uids:
        buf += struct.pack(">I", uid)
    buf += _string32("{6F4D119C-CAB7-415A-A879-AFA62CFCD56D}")
    buf += bytes.fromhex("000200030100000001")
    buf += _string32(variable_type)
    buf += bytes.fromhex("000000000003")
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


SV_SAMPLE = Path(__file__).parent / "corpus" / "issues" / "149" / "SV Sample.vi"
SV_PERSISTENT_ID = "{F7E00465-D283-4032-9E36-1D82BDC3CC46}"


def test_decodes_a_shared_variable_record_with_every_uid() -> None:
    data = b"\x00\x01BDHP" + build_hpvl_record(
        offset=6,
        library=b"SV Sample.lvlib",
        variable=b"Value",
        persistent_id=SV_PERSISTENT_ID,
        node_uids=[1033, 1112, 977],
        variable_type="Global",
    )
    assert decode_shared_variable_links(data, V20) == [
        SharedVariableLink(
            library_parts=("SV Sample.lvlib",),
            variable_name="Value",
            kind=SharedVariableKind.SINGLE_PROCESS,
            persistent_id=SV_PERSISTENT_ID,
            item_type="NI_Variable_Variable",
            target="My Computer",
            node_uids=(1033, 1112, 977),
        )
    ]


@pytest.mark.parametrize("lead", [0, 1, 2, 3])
def test_shared_variable_records_follow_each_other(lead: int) -> None:
    prefix = b"\x00" * lead
    first = build_hpvl_record(
        offset=len(prefix),
        library=b"A.lvlib",
        variable=b"One",
        persistent_id="{1}",
        node_uids=[1],
        variable_type="Global",
    )
    second = build_hpvl_record(
        offset=len(prefix) + len(first),
        library=b"Bee.lvlib",
        variable=b"Two",
        persistent_id="{2}",
        node_uids=[2, 3],
        variable_type="Network",
    )
    links = decode_shared_variable_links(prefix + first + second, V20)
    assert [(link.library_qualified_name, link.variable_name) for link in links] == [
        ("A.lvlib", "One"),
        ("Bee.lvlib", "Two"),
    ]
    assert [link.kind for link in links] == [
        SharedVariableKind.SINGLE_PROCESS,
        SharedVariableKind.NETWORK,
    ]
    assert [link.node_uids for link in links] == [(1,), (2, 3)]


def test_a_record_of_another_kind_is_skipped_and_its_neighbours_read() -> None:
    hpvl = build_hpvl_record(
        offset=0,
        library=b"A.lvlib",
        variable=b"One",
        persistent_id="{1}",
        node_uids=[1],
        variable_type="Global",
    )
    other = b"\x00\x02HpVr" + b"\x00" * 40
    iuvi = build_iuvi_record(
        offset=len(hpvl) + len(other),
        name_parts=[b"B.vi"],
        path_tokens=[b"B.vi"],
        node_uids=[9],
    )
    data = hpvl + other + iuvi
    assert [link.variable_name for link in decode_shared_variable_links(data, V20)] == [
        "One"
    ]
    assert [link.name_parts for link in decode_iuse_links(data, V20)] == [("B.vi",)]


def test_an_unknown_shared_variable_type_is_an_error_not_skipped() -> None:
    data = build_hpvl_record(
        offset=0,
        library=b"A.lvlib",
        variable=b"One",
        persistent_id="{1}",
        node_uids=[1],
        variable_type="Elsewhere",
    )
    with pytest.raises(LinkTableError, match="unknown Shared Variable type"):
        decode_shared_variable_links(data, V20)


def test_a_truncated_shared_variable_record_is_an_error() -> None:
    data = build_hpvl_record(
        offset=0,
        library=b"A.lvlib",
        variable=b"One",
        persistent_id="{1}",
        node_uids=[1],
        variable_type="Global",
    )
    with pytest.raises(LinkTableError):
        decode_shared_variable_links(data[:-20], V20)


def test_a_name_without_a_library_is_an_error() -> None:
    data = build_hpvl_record(
        offset=0,
        library=b"",
        variable=b"",
        name_parts=[b"One"],
        persistent_id="{1}",
        node_uids=[1],
        variable_type="Global",
    )
    with pytest.raises(LinkTableError, match="lacks a library and a variable"):
        decode_shared_variable_links(data, V20)


def test_real_sidecar_names_the_library_and_every_node_of_the_variable() -> None:
    libd, version = _sidecar(SV_SAMPLE)
    (link,) = read_shared_variable_links(libd, version)
    assert link == SharedVariableLink(
        library_parts=("SV Sample.lvlib",),
        variable_name="Value",
        kind=SharedVariableKind.SINGLE_PROCESS,
        persistent_id=SV_PERSISTENT_ID,
        item_type="NI_Variable_Variable",
        target="My Computer",
        node_uids=(1033, 1112, 977),
    )
    # the same ID the project records for the variable
    lvproj = SV_SAMPLE.with_suffix(".lvproj").read_text(encoding="utf-8-sig")
    assert f"varPersistentID:{SV_PERSISTENT_ID}" in lvproj
    assert read_iuse_links(libd, version) == []
