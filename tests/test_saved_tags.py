"""The tags a VI or a library saves (``parser/saved_tags.py``): the bytes of a
``VITS`` block and the properties of a library file are read."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.models import SavedTag
from lvkit.parser.saved_tags import read_property_tags, read_vi_tags

# The VITS block of a VI saved by LabVIEW 20 with two tags: Localized, a String
# "Don't Localize", and NI.LV.All.SourceOnly, a Boolean TRUE. Each tag is its name
# (a 4-byte length and the bytes), a 4-byte size and a flattened Variant of that size.
VITS_HEX = (
    "00000002000000094c6f63616c697a656400000038200080000000000100164030ffffffff0d"
    "52696e67546578742e54657874000100000000000e446f6e2774204c6f63616c697a65000000"
    "00000000144e492e4c562e416c6c2e536f757263654f6e6c7900000015200080000000000100"
    "040021000100000100000000"
)
LOCALIZED = "Localized"
SOURCE_ONLY = "NI.LV.All.SourceOnly"


def saved_from_bytes(tmp_path: Path, hex_text: str = VITS_HEX) -> list[SavedTag]:
    vits = tmp_path / "vi_VITS.bin"
    vits.write_bytes(bytes.fromhex(hex_text))
    return read_vi_tags(vits)


def test_the_vits_block_is_read(tmp_path: Path) -> None:
    tags = saved_from_bytes(tmp_path)
    assert [t.name for t in tags] == [LOCALIZED, SOURCE_ONLY]
    assert [(t.type_text(), t.value_text()) for t in tags] == [
        ("String", '"Don\'t Localize"'),
        ("TF", "True"),
    ]
    assert all(t.undecoded is None for t in tags)


def test_a_value_that_does_not_read_is_kept_undecoded(tmp_path: Path) -> None:
    broken = (
        "00000002"
        "00000001" + b"a".hex() + "00000003" + "616263"
        "00000001" + b"b".hex() + "00000003" + "616263"
    )
    tags = saved_from_bytes(tmp_path, broken)
    assert [t.name for t in tags] == ["a", "b"]
    assert all(t.value is None for t in tags)
    assert tags[0].undecoded == "its 3-byte Variant is in a form lvkit does not read"


def test_a_block_that_ends_inside_a_tag_keeps_the_tags_before_it(
    tmp_path: Path,
) -> None:
    tags = saved_from_bytes(tmp_path, VITS_HEX[: len(VITS_HEX) - 20])
    assert [t.name for t in tags] == [LOCALIZED, SOURCE_ONLY]
    assert tags[1].undecoded == "its value ends past the end of the block"
    assert saved_from_bytes(tmp_path, "00000000") == []


def test_the_properties_of_a_library_file_are_its_tags() -> None:
    root = ET.fromstring(
        '<Library LVVersion="20008000">'
        '<Property Name="NI.Lib.Version" Type="Str">1.0.0.2</Property>'
        '<Property Name="NI.LV.All.SourceOnly" Type="Bool">true</Property>'
        '<Property Name="NI_IconEditor" Type="Str">49 49 0 255</Property>'
        '<Property Name="NI.Lib.SourceVersion" Type="Int">7</Property>'
        '<Item Name="x.vi" Type="VI" URL="x.vi"/>'
        "</Library>"
    )
    tags = read_property_tags(root)
    assert [(t.name, t.type_text(), t.value_text()) for t in tags] == [
        ("NI.Lib.Version", "String", "'1.0.0.2'"),
        ("NI.LV.All.SourceOnly", "TF", "True"),
        ("NI_IconEditor", "String", "'11\\x00ÿ'"),
        ("NI.Lib.SourceVersion", None, None),
    ]
    assert tags[3].undecoded == "its property type Int has no read"


# The ``LVClassPrivateDataDfltData`` variants of two corpus VIs (``vi_VITS.bin``
# values, saved by LabVIEW 20 and 17). The first is the default private data of a
# class whose one field, ``History``, holds a class object: its descriptors are a
# class refnum (type code 0x70, kind 0x1E, the qualified name
# ``lv_icon.lvlib:History.lvlib:History.lvclass``) in a cluster, and the value is
# the flattened object (level 1, the name section padded to 4, version 0). The
# second is a class whose one field is a TCP connection refnum (kind 5).
HISTORY_DEFAULT_VARIANT = (
    "2000800000000002003e4070001e00002d0d6c765f69636f6e2e6c766c69620d486973746f72"
    "792e6c766c69620f486973746f72792e6c76636c6173730007486973746f727900184050000100"
    "000e556e646f205265646f20436f72650000010001000000012d0d6c765f69636f6e2e6c766c69"
    "620d486973746f72792e6c766c69620f486973746f72792e6c76636c61737300000000000000"
    "0000000000000000"
)
TCP_DEFAULT_VARIANT = (
    "17008000000000020010407000050854435020436f6e6e0000144050000100000b434c492e6c76"
    "636c617373000100010000000000000000"
)
DEFAULT_DATA = "LVClassPrivateDataDfltData"


def _tag_of(variant_hex: str, tmp_path: Path) -> SavedTag:
    variant = bytes.fromhex(variant_hex)
    block = (
        struct.pack(">I", 1)
        + struct.pack(">I", len(DEFAULT_DATA))
        + DEFAULT_DATA.encode()
        + struct.pack(">I", len(variant))
        + variant
    )
    [tag] = saved_from_bytes(tmp_path, block.hex())
    return tag


def test_a_class_default_data_holding_a_class_object_reads(tmp_path: Path) -> None:
    tag = _tag_of(HISTORY_DEFAULT_VARIANT, tmp_path)
    assert tag.undecoded is None
    assert tag.value is not None
    assert tag.value.value == {"History": "History.lvclass"}


def test_a_class_default_data_holding_a_refnum_reads(tmp_path: Path) -> None:
    tag = _tag_of(TCP_DEFAULT_VARIANT, tmp_path)
    assert tag.undecoded is None
    assert tag.value is not None
    assert list(tag.value.value) == ["TCP Conn"]
