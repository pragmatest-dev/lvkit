"""A ``DataFill`` of a VI's default data flattens to the bytes a stored value
has, so the decoders of ``ConstValue`` / ``DefaultData`` read it unchanged. A tag
whose bytes are not proven is reported, never guessed."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from lvkit.parser.data_fill import (
    DefaultDataSpace,
    flatten_data_fill,
    read_default_data_space,
    type_default_bytes,
)
from tests.native_vi import array, scalar

SHIFT = 2


def _space(*slots: tuple[str, str | None]) -> DefaultDataSpace:
    """The data space of ``slots``: the n-th is the data-space type ``SHIFT + n``,
    a ``DataFill`` of the given XML or, when that is None, a type that holds its
    default (``TMFBit11``)."""
    clients = "".join(
        '<Client TMFBit11="1" TMFBit13="1" />'
        if fill is None
        else '<Client TMFBit13="1" />'
        for _, fill in slots
    )
    fills = "".join(
        f'<DataFill TypeID="{SHIFT + n}">{fill}</DataFill>'
        for n, (_, fill) in enumerate(slots)
        if fill is not None
    )
    return read_default_data_space(
        ET.fromstring(
            f'<RSRC><TM80><Section Index="0" IndexShift="{SHIFT}">{clients}'
            f"</Section></TM80><DFDS><Section>{fills}</Section></DFDS></RSRC>"
        )
    )


def _fill(xml: str) -> ET.Element:
    return ET.fromstring(f'<DataFill TypeID="2">{xml}</DataFill>')


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        ("<I8>-2</I8>", "fe"),
        ("<I16>-2</I16>", "fffe"),
        ("<I32>7</I32>", "00000007"),
        ("<I64>-1</I64>", "ffffffffffffffff"),
        ("<U8>200</U8>", "c8"),
        ("<U16>513</U16>", "0201"),
        ("<U32>6</U32>", "00000006"),
        ("<U64>5</U64>", "0000000000000005"),
        ("<SGL>1.5</SGL>", "3fc00000"),
        ("<DBL>48000000</DBL>", "4186e36000000000"),
        ("<Boolean>1</Boolean>", "01"),
        ("<Boolean>0</Boolean>", "00"),
        ("<String />", "00000000"),
        ("<String>ab</String>", "000000026162"),
        ("<String><![CDATA[a&#x00;b]]></String>", "00000003610062"),
        ("<EB>64</EB>", "40"),
        ("<EW>2</EW>", "0002"),
        ("<EL>1</EL>", "00000001"),
        ("<Array><dim>0</dim></Array>", "00000000"),
        ("<Array><dim>2</dim><I16>1</I16><I16>2</I16></Array>", "0000000200010002"),
        ("<Cluster><U8>1</U8><String>x</String></Cluster>", "010000000178"),
        (
            "<SpecialDSTMCluster><Array><dim>0</dim></Array></SpecialDSTMCluster>",
            "00000000",
        ),
        ("<TypeDef><EW>1</EW></TypeDef>", "0001"),
        ('<Path Ident="PTH0" TpVal="0" />', "505448300000000400000000"),
        (
            '<Path Ident="PTH0" TpVal="1"><String>a</String><String>bc</String></Path>',
            "5054483000000009000100020161026263",
        ),
    ],
)
def test_a_data_fill_flattens_to_the_bytes_a_stored_value_has(
    xml: str, expected: str
) -> None:
    flat = flatten_data_fill(_fill(xml))
    assert flat.unsupported_tag is None
    assert flat.data is not None and flat.data.hex() == expected


@pytest.mark.parametrize(
    ("xml", "tag"),
    [
        ("<EXT>1.5</EXT>", "EXT"),
        ('<VisaRef StoredAs="String">COM1</VisaRef>', "VisaRef"),
        ("<UsrDefndTag>dev1</UsrDefndTag>", "UsrDefndTag"),
        ('<Path Ident="PTH0" TpVal="3" />', "Path"),
        (
            "<Array><dim>1</dim><dim>2</dim><U8>0</U8><U8>0</U8></Array>",
            "Array of 2 dimensions",
        ),
        ("<Cluster><U8>1</U8><LVObjCtl>0</LVObjCtl></Cluster>", "LVObjCtl"),
        ("<RepeatedBlock><I32>0</I32></RepeatedBlock>", "RepeatedBlock"),
    ],
)
def test_a_tag_that_cannot_be_proven_is_reported_not_guessed(
    xml: str, tag: str
) -> None:
    flat = flatten_data_fill(_fill(xml))
    assert flat.data is None
    assert flat.unsupported_tag == tag


def test_a_string_is_encoded_with_the_encoding_of_the_vi() -> None:
    fill = _fill("<String>é</String>")
    assert flatten_data_fill(fill, "cp1252").data == b"\x00\x00\x00\x01\xe9"
    assert flatten_data_fill(fill, "cp932").unsupported_tag == "String"


def test_the_default_of_a_type_is_zero_empty_or_a_cluster_of_those() -> None:
    assert type_default_bytes(scalar("NumInt32")) == bytes(4)
    assert type_default_bytes(scalar("NumUInt8")) == bytes(1)
    assert type_default_bytes(scalar("NumFloat64")) == bytes(8)
    assert type_default_bytes(scalar("Boolean")) == bytes(1)
    assert type_default_bytes(scalar("String")) == bytes(4)
    assert type_default_bytes(array("NumInt32")) == bytes(4)
    assert type_default_bytes(array("NumInt32", 2)) == bytes(8)
    assert type_default_bytes(scalar("Refnum")) == bytes(4)
    assert type_default_bytes(replace(scalar("Refnum"), classname="C.lvclass")) is None
    assert type_default_bytes(replace(scalar("Refnum"), ref_type="VisaRef")) is None
    assert type_default_bytes(scalar("Path")) == b"PTH0\x00\x00\x00\x04" + bytes(4)
    assert len(type_default_bytes(scalar("LVVariant")) or b"") == 20
    assert type_default_bytes(scalar("NumFloatExt")) is None
    assert type_default_bytes(None) is None


def test_the_data_space_reads_the_shift_the_slots_and_the_fills() -> None:
    space = _space(("a", "<U32>6</U32>"), ("b", None), ("c", "<U8>1</U8>"))
    assert space.index_shift == SHIFT
    assert sorted(space.fills) == [SHIFT, SHIFT + 2]
    assert space.default_value_types == frozenset({SHIFT + 1})
    assert space.fill_value("0").value_hex == "00000006"
    assert space.fill_value("2").value_hex == "01"
    assert space.fill_value("1").gap is not None
    assert space.fill_value(None).gap is None
    assert read_default_data_space(ET.fromstring("<RSRC />")).fill_value("0") == (
        None,
        None,
    )


def test_a_nan_keeps_the_bits_its_text_gives() -> None:
    nan = "nan (0x7FFFFFFFFFFFFFFF)"
    assert flatten_data_fill(_fill(f"<DBL>{nan}</DBL>")).data == bytes.fromhex(
        "7fffffffffffffff"
    )
    single = flatten_data_fill(_fill("<SGL>nan (0x7FC00000)</SGL>"))
    assert single.data == bytes.fromhex("7fc00000")
    assert flatten_data_fill(_fill("<DBL>-inf</DBL>")).data == bytes.fromhex(
        "fff0000000000000"
    )
