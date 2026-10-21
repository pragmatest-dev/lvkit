"""A VI that calls a .NET assembly (issue #161).

The assembly is a dependency of the VI (its ``DNVA`` / ``DNDA`` link records) and
each terminal of a .NET node records its .NET type (the ``DotNetItemType`` of a
``dnetItemInfo``). The records and XML below are built from a reported sample: no
.NET VI is in the corpus, so the graph-level checks put the sample's records and
types on ``tests/corpus/issues/149/SV Sample.vi``.
"""

from __future__ import annotations

import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.dotnet_types import DotNetAssembly, DotNetType, DotNetTypeError
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import NetlistModule, build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.load_mode import LoadMode
from lvkit.models import LVType, LVTypeKind
from lvkit.parser import vi as vi_parser
from lvkit.parser.link_table import (
    DotNetAssemblyRecordFailure,
    DotNetAssemblyRecordKind,
    DotNetAssemblyTable,
    LinkTableVersion,
    decode_dotnet_assemblies,
)
from lvkit.parser.models import ParsedTerminalInfo, ParsedType
from lvkit.parser.vi import _process_element_terminals

from .test_link_table import V12, V20, V86, _pth0
from .test_lvnet_roundtrip import _first_mismatch

SV_SAMPLE = Path(__file__).parent / "corpus" / "issues" / "149" / "SV Sample.vi"

MCCDAQ = DotNetAssembly("MccDaq", "3.0.0.0", "neutral", "a37eefcf5c6ca10a")
MCCDAQ_ASSEMBLY = (
    "MccDaq, Version=3.0.0.0, Culture=neutral, PublicKeyToken=a37eefcf5c6ca10a"
)
MCCDAQ_PARTS = [b"MccDaq", b"3.0.0.0", b"neutral", b"a37eefcf5c6ca10a"]

# The three terminal types of the reported sample.
INVENTORY_TYPE = f"MccDaq.DaqDeviceDescriptor[], {MCCDAQ_ASSEMBLY}"
INTERFACE_TYPE = f"MccDaq.DaqDeviceInterface, {MCCDAQ_ASSEMBLY}"
ERROR_INFO_TYPE = f"MccDaq.ErrorInfo, {MCCDAQ_ASSEMBLY}"


def item_type_hex(qualified: str) -> str:
    """A ``DotNetItemType`` value: the name's bytes, hex-encoded, NUL-terminated."""
    return (qualified.encode("ascii") + b"\x00").hex().upper()


def _head(
    offset: int,
    ident: bytes,
    parts: list[bytes],
    version: LinkTableVersion,
    *,
    typed: bool,
) -> bytearray:
    buf = bytearray(b"\x00\x02" + ident)

    def pad(boundary: int) -> None:
        while (offset + len(buf)) % boundary:
            buf.append(0)

    pad(4)
    buf += struct.pack(">I", len(parts))
    for part in parts:
        buf += bytes([len(part)]) + part
    pad(2)
    buf += _pth0([parts[0]] if parts else [])
    if version.at_least(8, 6, 0, 1):
        buf += struct.pack(">I", 0)
    else:
        buf += bytes([0])
    if typed:
        buf += struct.pack(">H", 0)  # type id
        if version.at_least(14, 0, 0, 3):
            buf += bytes([0x00])
        else:
            buf += b"\x00" * (4 + 8 + 4 + 4 + 4)
    return buf


def build_dnda_record(
    *,
    offset: int,
    parts: list[bytes] | None = None,
    node_uids: list[int],
    version: LinkTableVersion = V20,
) -> bytes:
    """One ``DNDA`` record as the module docstring of ``parser/link_table.py``
    lays it out, placed at absolute ``offset``."""
    name_parts = MCCDAQ_PARTS if parts is None else parts
    buf = _head(offset, b"DNDA", name_parts, version, typed=True)
    if version.at_least(12, 0, 0, 3):
        buf += struct.pack(">I", 0)  # typed-link flags
    buf += struct.pack(">I", len(node_uids))
    for uid in node_uids:
        buf += struct.pack(">I", uid)
    if version.at_least(10, 0, 0, 1):
        while (offset + len(buf)) % 2:
            buf.append(0)
        buf += _pth0([name_parts[0]] if name_parts else [])
    return bytes(buf)


def build_dnva_record(
    *,
    offset: int,
    parts: list[bytes] | None = None,
    version: LinkTableVersion = V20,
) -> bytes:
    """One ``DNVA`` record, placed at absolute ``offset``."""
    name_parts = MCCDAQ_PARTS if parts is None else parts
    buf = _head(offset, b"DNVA", name_parts, version, typed=False)
    if version.at_least(10, 0, 0, 1):
        while (offset + len(buf)) % 2:
            buf.append(0)
        buf += _pth0([name_parts[0]] if name_parts else [])
    return bytes(buf)


def test_a_dnda_record_names_the_assembly_and_every_node_using_it() -> None:
    data = build_dnda_record(offset=0, node_uids=[2204, 156, 3000])
    table = decode_dotnet_assemblies(data, V20)
    assert table.failures == []
    (link,) = table.links
    assert link.assembly == MCCDAQ
    assert link.node_uids == (2204, 156, 3000)
    assert link.record is DotNetAssemblyRecordKind.DNDA


def test_a_dnva_record_names_the_assembly() -> None:
    table = decode_dotnet_assemblies(build_dnva_record(offset=0), V20)
    assert table.failures == []
    (link,) = table.links
    assert link.assembly == MCCDAQ
    assert link.node_uids == ()
    assert link.record is DotNetAssemblyRecordKind.DNVA


@pytest.mark.parametrize("version", [V20, V12, V86], ids=["25.0", "12.0", "8.6"])
@pytest.mark.parametrize("lead", [0, 1, 3])
def test_records_follow_alignment_and_version_layout(
    version: LinkTableVersion, lead: int
) -> None:
    dnva = build_dnva_record(offset=lead, version=version)
    dnda = build_dnda_record(
        offset=lead + len(dnva), node_uids=[7, 8], version=version
    )
    data = b"\x00" * lead + dnva + dnda
    table = decode_dotnet_assemblies(data, version)
    assert table.failures == []
    assert [(k.record, k.assembly, k.node_uids) for k in table.links] == [
        (DotNetAssemblyRecordKind.DNVA, MCCDAQ, ()),
        (DotNetAssemblyRecordKind.DNDA, MCCDAQ, (7, 8)),
    ]


def test_the_assembly_is_spelled_as_dotnet_spells_it() -> None:
    assert MCCDAQ.qualified == MCCDAQ_ASSEMBLY


def test_a_name_without_four_parts_is_a_failure_not_skipped() -> None:
    data = build_dnda_record(offset=0, parts=[b"MccDaq", b"3.0.0.0"], node_uids=[1])
    table = decode_dotnet_assemblies(data, V20)
    assert table.links == []
    (failure,) = table.failures
    assert "has 2 parts, expected name, version, culture, public key token" in (
        failure.reason
    )
    assert failure.reason.startswith("DNDA record at offset 2:")


def test_a_truncated_record_is_a_failure() -> None:
    data = build_dnda_record(offset=0, node_uids=[1, 2, 3])[:-25]
    table = decode_dotnet_assemblies(data, V20)
    assert table.links == []
    assert "runs past the end of the table" in table.failures[0].reason


def test_a_version_older_than_the_layout_is_a_failure_not_an_exception() -> None:
    data = build_dnva_record(offset=0, version=V86)
    table = decode_dotnet_assemblies(data, LinkTableVersion(8, 2, 0, 4))
    assert table.links == []
    assert "older than the oldest supported layout (8.5)" in table.failures[0].reason


def test_a_failed_record_does_not_stop_its_neighbours() -> None:
    bad = build_dnva_record(offset=0, parts=[b"MccDaq"])
    good = build_dnda_record(offset=len(bad), node_uids=[5])
    table = decode_dotnet_assemblies(bad + good, V20)
    assert [k.assembly for k in table.links] == [MCCDAQ]
    assert len(table.failures) == 1


def test_a_table_without_assembly_records_is_empty() -> None:
    assert decode_dotnet_assemblies(b"\x00\x02IUVI" + b"\x00" * 40, V20) == (
        DotNetAssemblyTable(links=[], failures=[])
    )


# --- the .NET type of a terminal ---


def test_the_three_reported_item_types_decode() -> None:
    inventory = DotNetType.from_item_type(item_type_hex(INVENTORY_TYPE))
    assert inventory.qualified == INVENTORY_TYPE
    assert inventory.name == "MccDaq.DaqDeviceDescriptor[]"
    assert inventory.assembly == MCCDAQ_ASSEMBLY
    interface = DotNetType.from_item_type(item_type_hex(INTERFACE_TYPE))
    assert interface.name == "MccDaq.DaqDeviceInterface"
    error_info = DotNetType.from_item_type(item_type_hex(ERROR_INFO_TYPE))
    assert error_info.name == "MccDaq.ErrorInfo"


def test_a_generic_type_splits_at_the_comma_outside_its_brackets() -> None:
    generic = (
        "System.Collections.Generic.List`1[[System.String, mscorlib, "
        "Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089]], "
        "mscorlib, Version=4.0.0.0, Culture=neutral, "
        "PublicKeyToken=b77a5c561934e089"
    )
    dn_type = DotNetType.from_qualified(generic)
    assert dn_type.name == generic.split("]], ")[0] + "]]"
    assert dn_type.assembly.startswith("mscorlib, Version=4.0.0.0")


def test_a_name_that_is_not_hex_is_an_error() -> None:
    with pytest.raises(DotNetTypeError, match="not hex-encoded"):
        DotNetType.from_item_type("4D63ZZ")
    with pytest.raises(DotNetTypeError, match="no type name"):
        DotNetType.from_item_type("00")


def _refnum() -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="DotNet"
    )


def test_the_descriptor_shows_the_dotnet_type() -> None:
    inventory = DotNetType.from_qualified(INVENTORY_TYPE)
    interface = DotNetType.from_qualified(INTERFACE_TYPE)
    error_info = DotNetType.from_qualified(ERROR_INFO_TYPE)
    assert _refnum().type_descriptor() == "DotNet refnum"
    refnum = _refnum()
    refnum.dotnet_type = error_info
    assert refnum.type_descriptor() == "DotNet refnum{MccDaq.ErrorInfo}"
    array = LVType(
        kind=LVTypeKind.ARRAY,
        dimensions=1,
        element_type=_refnum(),
        dotnet_type=inventory,
    )
    assert array.type_descriptor() == "DotNet refnum{MccDaq.DaqDeviceDescriptor[]}"
    enum_parameter = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32", dotnet_type=interface
    )
    assert enum_parameter.type_descriptor() == "MccDaq.DaqDeviceInterface"


# --- reading the type from a node's DCO ---


def _invoke_node(dco_in_term: str, node_lists: str = "") -> ET.Element:
    return ET.fromstring(
        f"""
<SL__arrayElement class="invokeNode" uid="10">
  <termList elements="1">
    <SL__arrayElement class="term" uid="11">
      <objFlags>0</objFlags>
      {dco_in_term}
    </SL__arrayElement>
  </termList>
  {node_lists}
</SL__arrayElement>"""
    )


def _dnet_dco(
    hex_text: str, info: str = 'SL__arrayElement class="dnetItemInfo"'
) -> str:
    tag = info.split()[0]
    return f"""
<dco class="invokeItem" uid="12">
  <objFlags>1</objFlags>
  <typeDesc>TypeID(0)</typeDesc>
  <privDataList elements="1">
    <{info} uid="13">
      <DotNetItemDataSize>9</DotNetItemDataSize>
      <DotNetItemType>{hex_text}</DotNetItemType>
    </{tag}>
  </privDataList>
</dco>"""


def _terminal_type(elem: ET.Element) -> ParsedType:
    terminals: dict[str, ParsedTerminalInfo] = {}
    _process_element_terminals(elem, set(), {"11"}, {0: _refnum()}, terminals, {})
    parsed = terminals["11"].parsed_type
    assert parsed is not None
    return parsed


def test_a_dco_with_a_dnet_item_info_types_its_terminal() -> None:
    elem = _invoke_node(_dnet_dco(item_type_hex(INVENTORY_TYPE)))
    parsed = _terminal_type(elem)
    assert parsed.dotnet_type == DotNetType.from_qualified(INVENTORY_TYPE)


def test_a_bare_dco_reference_finds_the_definition_in_the_node() -> None:
    lists = f"""<dcoList elements="1">
      <SL__arrayElement class="invokeItem" uid="12">
        <objFlags>1</objFlags>
        <typeDesc>TypeID(0)</typeDesc>
        <privDataList elements="1">
          <SL__arrayElement class="dnetItemInfo" uid="13">
            <DotNetItemType>{item_type_hex(ERROR_INFO_TYPE)}</DotNetItemType>
          </SL__arrayElement>
        </privDataList>
      </SL__arrayElement>
    </dcoList>"""
    parsed = _terminal_type(_invoke_node('<dco uid="12" />', lists))
    assert parsed.dotnet_type == DotNetType.from_qualified(ERROR_INFO_TYPE)


def test_the_info_element_may_be_named_by_its_tag() -> None:
    elem = _invoke_node(_dnet_dco(item_type_hex(INTERFACE_TYPE), info="dnetItemInfo"))
    parsed = _terminal_type(elem)
    assert parsed.dotnet_type.name == "MccDaq.DaqDeviceInterface"


def test_a_dco_without_dnet_info_keeps_its_type() -> None:
    elem = _invoke_node(
        '<dco class="invokeItem" uid="12"><typeDesc>TypeID(0)</typeDesc>'
        '<privDataList elements="0" /></dco>'
    )
    parsed = _terminal_type(elem)
    assert parsed.dotnet_type is None
    assert parsed.ref_type == "DotNet"


def test_an_unreadable_item_type_keeps_the_terminal_type_and_says_why() -> None:
    elem = _invoke_node(_dnet_dco("4D63ZZ"))
    terminals: dict[str, ParsedTerminalInfo] = {}
    _process_element_terminals(elem, set(), {"11"}, {0: _refnum()}, terminals, {})
    parsed = terminals["11"].parsed_type
    assert parsed is not None and parsed.dotnet_type is None
    reason = terminals["11"].dotnet_type_unreadable
    assert reason is not None and "not hex-encoded" in reason


# --- the VI-level views ---


def _stand_in_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every Shared Variable node terminal of the fixture the sample's
    ``ErrorInfo`` type and the VI the sample's assembly records."""

    def item_type(
        elem: ET.Element, dco: ET.Element | None, dco_uid: str | None
    ) -> tuple[DotNetType | None, str | None]:
        if elem.get("class") == "sharedVariable":
            return DotNetType.from_qualified(ERROR_INFO_TYPE), None
        return None, None

    dnva = build_dnva_record(offset=0)
    dnda = build_dnda_record(offset=len(dnva), node_uids=[1033, 1112])
    table = decode_dotnet_assemblies(dnva + dnda, V20)
    assert table.failures == []
    monkeypatch.setattr(vi_parser, "_dotnet_item_type", item_type)
    monkeypatch.setattr(vi_parser, "_read_dotnet_assemblies", lambda root, xml: table)


def _load() -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(SV_SAMPLE, LoadMode.MINIMAL, search_paths=[SV_SAMPLE.parent])
    return graph, graph.list_vis()[0]


def test_a_vi_without_dotnet_records_has_no_assembly() -> None:
    graph, vi = _load()
    assembly_facet = graph.get_dotnet_assemblies(vi)
    assert assembly_facet.assemblies == []
    assert assembly_facet.unreadable == []
    assert ".NET" not in describe_vi(graph, vi)
    assert "assembly" not in render_lvnet(
        build_netlist_from_graph(graph, vi), display_name="SV Sample.vi"
    )


def test_describe_lists_the_assembly_under_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stand_in_types(monkeypatch)
    graph, vi = _load()
    assert graph.get_dotnet_assemblies(vi).assemblies == [MCCDAQ]
    text = describe_vi(graph, vi)
    assert f"  {MCCDAQ_ASSEMBLY} (.NET assembly)" in text


def test_lvnet_lists_the_assembly_under_uses_and_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stand_in_types(monkeypatch)
    graph, vi = _load()
    module = build_netlist_from_graph(graph, vi)
    for verbose in (False, True):
        text = render_lvnet(module, display_name="SV Sample.vi", verbose=verbose)
        assert f"    assembly {MCCDAQ_ASSEMBLY}\n" in text
        parsed = parse_lvnet(text)
        assert any(d.kind == "assembly" for d in parsed.uses)
    assert "MccDaq.ErrorInfo" in text
    rebuilt: NetlistModule = reconstruct_module(parse_lvnet(text))
    assert render_lvnet(rebuilt, display_name="SV Sample.vi", verbose=True) == text
    mismatch = _first_mismatch(netlist_signature(module), netlist_signature(rebuilt))
    assert mismatch is None


def test_an_unreadable_terminal_type_is_reported_with_the_unreadable_assemblies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "the .NET type of a node terminal is unreadable: bad name"
    monkeypatch.setattr(
        vi_parser,
        "_dotnet_item_type",
        lambda elem, dco, dco_uid: (
            (None, reason) if elem.get("class") == "sharedVariable" else (None, None)
        ),
    )
    graph, vi = _load()
    assert graph.get_dotnet_assemblies(vi).unreadable == [reason]
    assert f"(.NET unreadable: {reason})" in describe_vi(graph, vi)


def test_json_carries_the_assembly_and_the_full_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stand_in_types(monkeypatch)
    graph, vi = _load()
    body = netlist_to_dict(build_netlist_from_graph(graph, vi), verbose=True)
    assert {"kind": "assembly", "qualified": MCCDAQ_ASSEMBLY, "path": None} in body[
        "dependencies"
    ]
    dumped = json.dumps(body)
    assert f'"dotnet_type": "{ERROR_INFO_TYPE}"' in dumped


def test_an_unreadable_record_is_reported_in_every_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = 'DNDA record at offset 2: bad "name"'
    table = DotNetAssemblyTable(
        links=[], failures=[DotNetAssemblyRecordFailure(reason)]
    )
    monkeypatch.setattr(vi_parser, "_read_dotnet_assemblies", lambda root, xml: table)
    graph, vi = _load()
    assert f"(.NET unreadable: {reason})" in describe_vi(graph, vi)
    module = build_netlist_from_graph(graph, vi)
    body = netlist_to_dict(module, verbose=True)
    assert {
        "kind": "assembly",
        "qualified": "",
        "path": None,
        "unreadable": reason,
    } in body["dependencies"]
    for verbose in (False, True):
        text = render_lvnet(module, display_name="SV Sample.vi", verbose=verbose)
        expected = (
            '    assembly (unreadable "DNDA record at offset 2: bad \\"name\\"")\n'
        )
        assert expected in text
        parsed = parse_lvnet(text)
        assert [d.unreadable for d in parsed.uses if d.kind == "assembly"] == [reason]
    rebuilt = reconstruct_module(parse_lvnet(text))
    assert [d.unreadable for d in rebuilt.dependencies if d.unreadable] == [reason]
    assert render_lvnet(rebuilt, display_name="SV Sample.vi", verbose=True) == text
    mismatch = _first_mismatch(netlist_signature(module), netlist_signature(rebuilt))
    assert mismatch is None


def test_a_decoded_record_in_the_main_xml_is_read(tmp_path: Path) -> None:
    root = ET.fromstring(
        """
<LVSR><LIbd><BDHP><DNDA LinkSaveFlag="0">
  <LinkSaveQualName><String>MccDaq</String><String>3.0.0.0</String>
    <String>neutral</String><String>a37eefcf5c6ca10a</String></LinkSaveQualName>
  <LinkOffsetList><Offset>0x0000089C</Offset></LinkOffsetList>
</DNDA></BDHP></LIbd>
<LIvi><DNVA><LinkSaveQualName><String>MccDaq</String></LinkSaveQualName></DNVA>
</LIvi></LVSR>"""
    )
    table = vi_parser._read_dotnet_assemblies(root, tmp_path / "vi.xml")
    assert [(k.record, k.assembly, k.node_uids) for k in table.links] == [
        (DotNetAssemblyRecordKind.DNDA, MCCDAQ, (0x89C,))
    ]
    assert len(table.failures) == 1
    assert table.failures[0].reason.startswith("DNVA record:")


def test_records_in_the_sidecars_are_read(tmp_path: Path) -> None:
    root = ET.fromstring(
        '<VI><LVSR><Section><Version Major="25" Minor="0" Bugfix="0" '
        'Stage="release" /></Section></LVSR></VI>'
    )
    xml = tmp_path / "vi.xml"
    (tmp_path / "vi_LIbd.bin").write_bytes(
        b"\x00" * 3 + build_dnda_record(offset=3, node_uids=[2204])
    )
    (tmp_path / "vi_LIvi.bin").write_bytes(build_dnva_record(offset=0))
    table = vi_parser._read_dotnet_assemblies(root, xml)
    assert table.failures == []
    assert sorted((k.record.value, k.assembly) for k in table.links) == [
        ("DNDA", MCCDAQ),
        ("DNVA", MCCDAQ),
    ]


def test_a_sidecar_record_without_a_version_is_reported(tmp_path: Path) -> None:
    (tmp_path / "vi_LIvi.bin").write_bytes(build_dnva_record(offset=0))
    table = vi_parser._read_dotnet_assemblies(
        ET.fromstring("<VI />"), tmp_path / "vi.xml"
    )
    assert table.links == []
    assert "records no version" in table.failures[0].reason
