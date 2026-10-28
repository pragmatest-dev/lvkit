"""The library a Call Library Function Node names, read from the VI's ``NEXF`` and
``DSEF`` link records: the name as typed and the path LabVIEW recorded."""

from __future__ import annotations

import struct
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.parser.extfunc_prototype import ExtFuncCall, LibraryPathKind
from lvkit.parser.link_table import (
    ExtFuncLink,
    LinkTableError,
    LinkTableVersion,
    decode_ext_func_links,
)
from tests.test_ext_func_terminals import _BD_XML, _MAIN_XML
from tests.test_link_table import V12, V20, V82, _pth0

_NODE_UID = 20


def _strings(tokens: list[str]) -> str:
    return "".join(
        f"<String>{escape(t)}</String>" if t else "<String />" for t in tokens
    )


def _record(
    tag: str,
    name: str,
    tokens: list[str],
    function: str,
    offsets: list[int],
    path_type: int = 1,
) -> str:
    offsets_xml = "".join(f"<Offset>{o:#06x}</Offset>" for o in offsets)
    return (
        f'<{tag} LinkSaveFlag="0"><LinkSaveQualName><String>{name}</String>'
        f'</LinkSaveQualName><LinkSavePathRef Ident="PTH0" TpVal="{path_type}">'
        f"{_strings(tokens)}</LinkSavePathRef><LinkOffsetList>{offsets_xml}"
        f"</LinkOffsetList><ExtFuncLinkStr>{function}</ExtFuncLinkStr></{tag}>"
    )


def _call(
    tmp_path: Path, nexf: str = "", dsef: str = "", extra: str = ""
) -> ExtFuncCall:
    """The node of the one-node VI, with the given records in its main XML."""
    tables = (
        f"<LIbd><Section><BDHP>{nexf}</BDHP></Section></LIbd>" if nexf else ""
    ) + (f"<VIDS><Section>{dsef}</Section></VIDS>" if dsef else "")
    tables += extra
    main = _MAIN_XML.format(return_label="").replace("</LVSR>", tables + "</LVSR>")
    (tmp_path / "Clfn_BDHb.xml").write_text(_BD_XML)
    (tmp_path / "Clfn.xml").write_text(main)
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Clfn_BDHb.xml")
    vi_name = graph.list_vis()[0]
    call = graph._graph.nodes[f"{vi_name}::{_NODE_UID}"]["node"].ext_func  # noqa: SLF001
    assert call is not None
    return call


def test_a_node_without_a_record_keeps_the_library_its_own_path_ends_in(
    tmp_path: Path,
) -> None:
    call = _call(tmp_path)
    assert (call.library, call.library_path_tokens) == ("lib.dll", ())
    assert call.library_path_kind is LibraryPathKind.BY_NAME


def test_a_nexf_record_gives_the_typed_name_and_the_recorded_path(
    tmp_path: Path,
) -> None:
    nexf = _record("NEXF", "vendorlib.*", ["", "", "vendorlib.dll"], "DoWork", [20])
    call = _call(tmp_path, nexf)
    assert call.library == "vendorlib.*"
    assert call.library_path_tokens == ("", "", "vendorlib.dll")
    assert call.library_path_kind is LibraryPathKind.RELATIVE_TO_VI


def test_a_nexf_record_is_matched_by_the_node_uid_not_the_function(
    tmp_path: Path,
) -> None:
    other = _record("NEXF", "other.dll", ["", "other.dll"], "DoWork", [99])
    mine = _record("NEXF", "mine.dll", ["", "mine.dll"], "DoWork", [20, 30])
    call = _call(tmp_path, other + mine)
    assert call.library == "mine.dll"


@pytest.mark.parametrize(
    "tokens,path_type,kind",
    [
        (["kernel32.dll"], 1, LibraryPathKind.BY_NAME),
        (["", "lib.dll"], 1, LibraryPathKind.RELATIVE_TO_VI),
        (["<resource>", "lib.dll"], 0, LibraryPathKind.OTHER),
        (["C:", "dir", "lib.dll"], 0, LibraryPathKind.OTHER),
    ],
)
def test_path_kinds(
    tmp_path: Path, tokens: list[str], path_type: int, kind: LibraryPathKind
) -> None:
    nexf = _record("NEXF", "lib.dll", tokens, "DoWork", [20], path_type)
    assert _call(tmp_path, nexf).library_path_kind is kind


def test_a_data_space_record_is_matched_by_library_and_function(
    tmp_path: Path,
) -> None:
    dsef = _record("DSEF", "lib.*", ["", "sub", "lib.dll"], "DoWork", [0xF720])
    call = _call(tmp_path, dsef=dsef)
    assert call.library == "lib.*"
    assert call.library_path_tokens == ("", "sub", "lib.dll")


def test_data_space_offsets_are_not_node_uids(tmp_path: Path) -> None:
    dsef = _record("DSEF", "lib.*", ["", "lib.dll"], "OtherFunction", [20])
    assert _call(tmp_path, dsef=dsef).library == "lib.dll"


def test_two_data_space_records_that_differ_name_no_path(tmp_path: Path) -> None:
    dsef = _record("DSEF", "lib.*", ["", "a", "lib.dll"], "DoWork", [1]) + _record(
        "DSEF", "lib.*", ["", "b", "lib.dll"], "DoWork", [2]
    )
    call = _call(tmp_path, dsef=dsef)
    assert (call.library, call.library_path_tokens) == ("lib.dll", ())


def test_a_node_record_wins_over_a_data_space_record(tmp_path: Path) -> None:
    nexf = _record("NEXF", "lib.*", ["", "near", "lib.dll"], "DoWork", [20])
    dsef = _record("DSEF", "lib.dll", ["", "far", "lib.dll"], "DoWork", [1])
    assert _call(tmp_path, nexf, dsef).library_path_tokens == ("", "near", "lib.dll")


# ------------------------------------------------------------- the sidecar


def build_nexf_record(
    *,
    offset: int,
    name: bytes,
    path_tokens: list[bytes],
    function: bytes,
    node_uids: list[int],
    version: LinkTableVersion = V20,
    path_type: int = 1,
) -> bytes:
    """One ``NEXF`` record as pylabview writes it, placed at absolute ``offset``."""
    buf = bytearray(b"\x00\x02NEXF")

    def pad(boundary: int) -> None:
        while (offset + len(buf)) % boundary:
            buf.append(0)

    pad(4)
    buf += struct.pack(">I", 1) + bytes([len(name)]) + name
    pad(2)
    buf += _pth0(path_tokens, tpval=path_type)
    if version.at_least(8, 6, 0, 1):
        buf += struct.pack(">I", 0)
    elif version.at_least(8, 5, 0, 1):
        buf += b"\x00"
    buf += struct.pack(">I", len(node_uids))
    for uid in node_uids:
        buf += struct.pack(">I", uid)
    buf += bytes([len(function)]) + function
    if (len(function) + 1) % 2:
        buf += b"\x00"
    buf += b"\x00\x01"  # properties 3 and 4
    if version.at_least(11, 0, 0, 3):
        buf += b"\x01"
    return bytes(buf)


@pytest.mark.parametrize("version", [V20, V12])
@pytest.mark.parametrize("lead", [0, 1, 2, 3])
def test_the_sidecar_record_is_decoded_at_any_alignment(
    version: LinkTableVersion, lead: int
) -> None:
    prefix = b"\x00" * lead
    first = build_nexf_record(
        offset=len(prefix),
        name=b"vendorlib.*",
        path_tokens=[b"", b"", b"vendorlib.dll"],
        function=b"DoWork",
        node_uids=[938, 940],
        version=version,
    )
    second = build_nexf_record(
        offset=len(prefix) + len(first),
        name=b"kernel32.dll",
        path_tokens=[b"kernel32.dll"],
        function=b"Odd",
        node_uids=[7],
        version=version,
    )
    assert decode_ext_func_links(prefix + first + second, version) == [
        ExtFuncLink(
            ("vendorlib.*",), 1, ("", "", "vendorlib.dll"), "DoWork", (938, 940)
        ),
        ExtFuncLink(("kernel32.dll",), 1, ("kernel32.dll",), "Odd", (7,)),
    ]


def test_a_sidecar_older_than_the_oldest_layout_is_an_error() -> None:
    data = build_nexf_record(
        offset=0,
        name=b"a.dll",
        path_tokens=[b"a.dll"],
        function=b"f",
        node_uids=[1],
        version=V82,
    )
    with pytest.raises(LinkTableError):
        decode_ext_func_links(data, LinkTableVersion(7, 1, 0, 4))


def test_a_truncated_record_is_an_error() -> None:
    data = build_nexf_record(
        offset=0, name=b"a.dll", path_tokens=[b"a.dll"], function=b"f", node_uids=[1]
    )
    with pytest.raises(LinkTableError):
        decode_ext_func_links(data[:14], V20)


def test_a_node_is_matched_from_the_raw_sidecar(tmp_path: Path) -> None:
    record = build_nexf_record(
        offset=0,
        name=b"vendorlib.*",
        path_tokens=[b"", b"libs", b"vendorlib.dll"],
        function=b"DoWork",
        node_uids=[_NODE_UID],
    )
    (tmp_path / "Clfn_LIbd.bin").write_bytes(record)
    version = (
        '<LVSR><Section><Version Major="20" Minor="0" Bugfix="0" '
        'Stage="release" Build="0" /></Section></LVSR>'
    )
    call = _call(tmp_path, extra=version)
    assert call.library == "vendorlib.*"
    assert call.library_path_tokens == ("", "libs", "vendorlib.dll")
