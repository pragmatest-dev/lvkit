"""The library a Call Library Function Node names, read from the VI's ``NEXF`` and
``DSEF`` link records: the name as typed and the path LabVIEW recorded."""

from __future__ import annotations

import struct
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import GraphNode
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


def _loaded(
    tmp_path: Path,
    nexf: str = "",
    dsef: str = "",
    extra: str = "",
    node_name: str = "DoWork",
) -> tuple[InMemoryVIGraph, str]:
    """The Call Library Function Node of the one-node VI, named ``node_name`` in
    its diagram, with the given records in its main XML."""
    tables = (
        f"<LIbd><Section><BDHP>{nexf}</BDHP></Section></LIbd>" if nexf else ""
    ) + (f"<VIDS><Section>{dsef}</Section></VIDS>" if dsef else "")
    tables += extra
    main = _MAIN_XML.format(return_label="").replace("</LVSR>", tables + "</LVSR>")
    bd = _BD_XML.replace(
        '<nodeName>"DoWork"</nodeName>', f'<nodeName>"{node_name}"</nodeName>'
    )
    (tmp_path / "Clfn_BDHb.xml").write_text(bd)
    (tmp_path / "Clfn.xml").write_text(main)
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Clfn_BDHb.xml")
    return graph, graph.list_vis()[0]


def _node(tmp_path: Path, *args: str, **kwargs: str) -> GraphNode:
    graph, vi_name = _loaded(tmp_path, *args, **kwargs)
    return graph._graph.nodes[f"{vi_name}::{_NODE_UID}"]["node"]  # noqa: SLF001


def _gap_reasons(tmp_path: Path, *args: str, **kwargs: str) -> list[str]:
    from lvkit.codegen.builder import build_module
    from lvkit.codegen.gaps import Gap

    graph, vi_name = _loaded(tmp_path, *args, **kwargs)
    gaps: list[Gap] = []
    build_module(graph.get_vi_context(vi_name), vi_name, graph=graph, gaps=gaps)
    return [gap.reason for gap in gaps]

def _call(
    tmp_path: Path, nexf: str = "", dsef: str = "", extra: str = ""
) -> ExtFuncCall:
    """The call of the one-node VI, with the given records in its main XML."""
    call = _node(tmp_path, nexf, dsef, extra).ext_func
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


# ------------------------------------------------- a node name cut to 31 characters

_LONG = "ps3000aSetTriggerChannelConditions"
_LONG_V2 = "ps3000aSetTriggerChannelConditionsV2"
_CUT = _LONG[:31]


def test_the_cut_name_is_31_characters_and_shared_by_both_functions() -> None:
    assert (len(_CUT), _LONG_V2[:31]) == (31, _CUT)


@pytest.mark.parametrize("function", [_LONG, _LONG_V2])
def test_a_nexf_record_gives_the_whole_function_name(
    tmp_path: Path, function: str
) -> None:
    nexf = _record("NEXF", "lib.dll", ["lib.dll"], function, [20])
    node = _node(tmp_path, nexf, node_name=_CUT)
    assert node.ext_func is not None
    assert node.ext_func.function == function
    assert node.name == f"lib.dll:{function}"


def test_a_name_that_was_not_cut_is_unchanged(tmp_path: Path) -> None:
    nexf = _record("NEXF", "lib.dll", ["lib.dll"], "DoWork", [20])
    node = _node(tmp_path, nexf)
    assert node.ext_func is not None
    assert (node.ext_func.function, node.name) == ("DoWork", "lib.dll:DoWork")


def test_a_data_space_record_completes_a_cut_name_it_alone_fits(
    tmp_path: Path,
) -> None:
    dsef = _record("DSEF", "lib.*", ["", "lib.dll"], _LONG, [0xF720])
    node = _node(tmp_path, dsef=dsef, node_name=_CUT)
    assert node.ext_func is not None
    assert node.ext_func.function == _LONG
    assert node.ext_func.library == "lib.*"
    assert node.name == f"lib.dll:{_LONG}"


def test_a_cut_name_that_fits_two_functions_names_neither(tmp_path: Path) -> None:
    dsef = _record("DSEF", "lib.*", ["", "lib.dll"], _LONG, [1]) + _record(
        "DSEF", "lib.*", ["", "lib.dll"], _LONG_V2, [2]
    )
    node = _node(tmp_path, dsef=dsef, node_name=_CUT)
    assert node.ext_func is not None
    assert (node.ext_func.function, node.ext_func.library) == (_CUT, "lib.dll")


def test_an_exact_name_wins_over_a_longer_function_it_is_a_prefix_of(
    tmp_path: Path,
) -> None:
    name = "ps6000aGetStreamingLatestValues"
    dsef = _record("DSEF", "lib.*", ["", "lib.dll"], name, [1]) + _record(
        "DSEF", "lib.*", ["", "lib.dll"], name + "V2", [2]
    )
    node = _node(tmp_path, dsef=dsef, node_name=name)
    assert node.ext_func is not None
    assert (node.ext_func.function, node.ext_func.library) == (name, "lib.*")
    assert node.ext_func.cut_name is None


def test_generate_calls_the_whole_function_name(tmp_path: Path) -> None:
    nexf = _record("NEXF", "lib.dll", ["lib.dll"], _LONG, [20])
    (reason,) = _gap_reasons(tmp_path, nexf, node_name=_CUT)
    assert reason.startswith(f"Call Library Function Node lib.dll:{_LONG} ")


def test_generate_makes_an_ambiguous_cut_name_a_gap(tmp_path: Path) -> None:
    dsef = _record("DSEF", "lib.*", ["", "lib.dll"], _LONG, [1]) + _record(
        "DSEF", "lib.*", ["", "lib.dll"], _LONG_V2, [2]
    )
    (reason,) = _gap_reasons(tmp_path, dsef=dsef, node_name=_CUT)
    assert reason == (
        f"The Call Library Function Node cannot be called: the DLL function name "
        f"{_CUT!r} is cut to 31 characters and matches {_LONG}, {_LONG_V2}"
    )


def test_generate_makes_an_unmatched_31_character_name_a_gap(tmp_path: Path) -> None:
    (reason,) = _gap_reasons(tmp_path, node_name=_CUT)
    assert reason == (
        f"The Call Library Function Node cannot be called: the DLL function name "
        f"{_CUT!r} may be cut to 31 characters and matches no link record of the VI"
    )


def test_a_short_name_is_not_taken_for_a_cut_prefix(tmp_path: Path) -> None:
    dsef = _record("DSEF", "lib.*", ["", "lib.dll"], "DoWorkV2", [1])
    node = _node(tmp_path, dsef=dsef)
    assert node.ext_func is not None
    assert (node.ext_func.function, node.ext_func.library) == ("DoWork", "lib.dll")


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
