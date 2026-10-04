"""Unit tests for the XNode ``<StateData>`` string decoders (#107 follow-up):
``_xnode_state_method_name``/``_xnode_state_row_names``/
``_xnode_state_resource_name`` in ``lvkit.parser.node_types``.

Real ``<StateData>`` is an opaque, node-kind-specific binary blob (type
descriptors, enum item lists, a resource GUID, ...) with no public format
spec -- these tests pin the three verified, narrow signals pulled out of it:
a leading method name ("Invoke Method" nodes only), every per-row
parameter/property name (each recorded TWICE -- either adjacently, or split
into "all first copies, then all second copies"), and a leading UNPAIRED
resource/module identifier (e.g. "Mod4") when one is present (see
``XNodeNode``'s own docstring for the real-corpus verification -- RT_v1.vi's
"Raw data to RT.Configure" node, FPGA_v1.vi's "Antenna Status"/"Longitude"
property nodes, and FPGA_v1.vi's "Mod1/AI0"/"Mod1/AI2" FPGA I/O Node).
Synthetic blobs here mirror that exact shape (4-byte-big-endian-length-
prefixed UTF-8 strings) without pasting the real (much longer) hex dumps.
"""

from __future__ import annotations

import struct

from lvkit.parser.node_types import (
    _strip_xnode_resource_guid,
    _xnode_class_method_name,
    _xnode_state_filtered_strings,
    _xnode_state_method_name,
    _xnode_state_resource_name,
    _xnode_state_row_names,
    _xnode_state_strings,
)


def _pack(s: str) -> bytes:
    data = s.encode("utf-8")
    return struct.pack(">I", len(data)) + data


def _hex(*parts: bytes) -> str:
    return b"".join(parts).hex()


def test_method_name_is_the_first_string():
    blob = _hex(_pack("Run"), b"\x00\x01\x02\x03", _pack("Configure FIFO"))
    assert _xnode_state_method_name(blob) == "Run"


def test_method_name_absent_blob_is_empty():
    assert _xnode_state_method_name(None) == ""
    assert _xnode_state_method_name("") == ""


def test_method_name_non_string_leading_bytes_is_empty():
    # A "Read/Write Control"/"Open FPGA VI Reference" node's leading bytes
    # aren't a string at all -- must be "", never a garbled guess.
    blob = _hex(b"\x00\x00\x00\x00\xff\xff\xff\xff")
    assert _xnode_state_method_name(blob) == ""


def test_row_names_finds_adjacent_identical_pairs():
    # Mirrors FPGA_v1.vi's real "FPGA I/O Property Node" shape: a leading
    # singleton (the "Mod4.{GUID}" resource identifier -- never paired),
    # then one pair per row, each separated by unrelated binary noise.
    blob = _hex(
        _pack("Mod4.{SOME-GUID}"),
        b"\x00" * 6,
        _pack("Antenna Status"),
        b"\xff" * 40,
        _pack("Antenna Status"),
        b"\x00" * 10,
        _pack("Satellites Available"),
        b"\xff" * 20,
        _pack("Satellites Available"),
    )
    assert _xnode_state_row_names(blob) == ["Antenna Status", "Satellites Available"]


def test_row_names_keeps_the_longer_of_a_prefix_pair():
    # Mirrors "Longitude" -> "Longitude (°)": the real display name adds
    # a unit suffix on its second occurrence -- keep that richer copy.
    blob = _hex(_pack("Longitude"), b"\x00" * 8, _pack("Longitude (°)"))
    assert _xnode_state_row_names(blob) == ["Longitude (°)"]


def test_row_names_unpaired_singleton_is_not_a_row():
    # A string with no matching second occurrence anywhere nearby (an enum
    # item, a class name, ...) is incidental metadata, not a row.
    blob = _hex(_pack("Normal"), b"\x00" * 4, _pack("Unrelated"))
    assert _xnode_state_row_names(blob) == []


def test_row_names_excludes_the_method_name_itself():
    # The invoked method is ALSO recorded as a row-name pair elsewhere in the
    # same blob (real-corpus-verified), in the SAME split "all first copies,
    # then all second copies" layout as FPGA_v1.vi's real "Mod1/AI0"/
    # "Mod1/AI2" node -- XNodeHandler.parse filters the method out
    # afterward; this pins that _xnode_state_row_names alone does NOT do the
    # filtering (that's the caller's job, since only Invoke Method nodes
    # have one), and that non-adjacent pairs are found in first-occurrence
    # order.
    blob = _hex(
        _pack("Run"),
        b"\x00" * 4,
        _pack("Requested Depth"),
        b"\x00" * 4,
        _pack("Run"),
        b"\x00" * 4,
        _pack("Requested Depth"),
    )
    names = _xnode_state_row_names(blob)
    assert names == ["Run", "Requested Depth"]


def test_row_names_absent_blob_is_empty_list():
    assert _xnode_state_row_names(None) == []
    assert _xnode_state_row_names("") == []


def test_state_strings_ignores_non_utf8_and_non_printable_runs():
    noise = struct.pack(">I", 3) + b"\x00\x01\x02"
    blob = _hex(_pack("Valid"), b"\xff\xfe\xfd\xfc", noise)
    assert _xnode_state_strings(bytes.fromhex(blob)) == ["Valid"]


def test_row_names_pairs_non_adjacent_occurrences():
    # Mirrors FPGA_v1.vi's real "FPGA I/O Node" shape ("Mod1/AI0"/"Mod1/AI2"):
    # ALL first copies, then ALL second copies -- not back-to-back pairs.
    blob = _hex(
        _pack("Mod1/AI0"),
        b"\x00" * 6,
        _pack("Mod1/AI2"),
        b"\xff" * 6,
        _pack("Mod1/AI0"),
        b"\x00" * 6,
        _pack("Mod1/AI2"),
    )
    assert _xnode_state_row_names(blob) == ["Mod1/AI0", "Mod1/AI2"]


def test_row_names_strips_the_resource_guid_suffix():
    # A paired row name can ALSO carry the ".{GUID}" resource suffix
    # (FPGA_v1.vi's real "Mod1/AI0.{GUID}") -- LabVIEW's own drawer never
    # shows it.
    blob = _hex(
        _pack("Mod1/AI0.{A117026A-AD51-4C63-8341-085C842E9D22}"),
        b"\x00" * 4,
        _pack("Mod1/AI0.{A117026A-AD51-4C63-8341-085C842E9D22}"),
    )
    assert _xnode_state_row_names(blob) == ["Mod1/AI0"]


def test_strip_xnode_resource_guid():
    assert (
        _strip_xnode_resource_guid("Mod4.{305F45FE-A073-4761-80F9-BAED2D98EABE}")
        == "Mod4"
    )
    assert _strip_xnode_resource_guid("Antenna Status") == "Antenna Status"


def test_resource_name_is_the_leading_unpaired_singleton():
    # Mirrors FPGA_v1.vi's real "Antenna Status" node: a leading "Mod4.{GUID}"
    # that never recurs, followed by paired row names.
    blob = _hex(
        _pack("Mod4.{305F45FE-A073-4761-80F9-BAED2D98EABE}"),
        b"\x00" * 6,
        _pack("Antenna Status"),
        b"\xff" * 6,
        _pack("Antenna Status"),
    )
    assert _xnode_state_resource_name(blob) == "Mod4"


def test_resource_name_empty_when_the_leading_string_pairs_up():
    # Mirrors FPGA_v1.vi's real "Mod1/AI0"/"Mod1/AI2" node -- no leading
    # singleton, so no resource header (LabVIEW draws a BLANK header band).
    blob = _hex(
        _pack("Mod1/AI0"),
        b"\x00" * 4,
        _pack("Mod1/AI2"),
        b"\x00" * 4,
        _pack("Mod1/AI0"),
        b"\x00" * 4,
        _pack("Mod1/AI2"),
    )
    assert _xnode_state_resource_name(blob) == ""


def test_resource_name_absent_blob_is_empty_string():
    assert _xnode_state_resource_name(None) == ""
    assert _xnode_state_resource_name("") == ""


def _fifo_interface_block(type_name: str) -> bytes:
    return b"".join(
        (
            _pack(_CONTAINER_INTERFACE_MARKER),
            _pack("<Interface><DataType><Type>i16</Type></DataType></Interface>"),
            _pack(type_name),
        )
    )


_CONTAINER_INTERFACE_MARKER = "ContainerInterface"


def test_filtered_strings_strips_a_container_interface_triplet():
    # Mirrors FPGA_v1.vi's real "FIFO Write" node: the literal protocol name,
    # its XML interface descriptor, and the general container-type name
    # right after it are never a display label.
    blob = bytes.fromhex(
        _hex(_pack("Element"), b"\x00" * 4, _pack("Element"))
    ) + _fifo_interface_block("FPGA FIFO")
    assert _xnode_state_filtered_strings(blob) == ["Element", "Element"]


def test_filtered_strings_strips_generic_ref_labels():
    blob = bytes.fromhex(_hex(_pack("ref in"), b"\x00" * 4, _pack("Element")))
    assert _xnode_state_filtered_strings(blob) == ["Element"]


def test_header_echoed_inside_reference_blocks_is_still_the_header():
    # Mirrors FPGA_v1.vi's real "FIFO Write" node: "Raw data to RT" occurs
    # once leading, then once more inside EACH of the two reference
    # terminals' own (stripped) interface metadata -- 3 total occurrences,
    # not the ordinary-row count of 2 -- and must never leak into row_names.
    blob = (
        bytes.fromhex(_hex(_pack("Raw data to RT")))
        + b"\x00" * 4
        + bytes.fromhex(_hex(_pack("Element"), b"\x00" * 4, _pack("Write")))
        + b"\x00" * 4
        + bytes.fromhex(_hex(_pack("Element")))
        + b"\x00" * 4
        + _fifo_interface_block("FPGA FIFO")
        + bytes.fromhex(_hex(_pack("Raw data to RT")))
        + b"\x00" * 4
        + _fifo_interface_block("FPGA FIFO")
        + bytes.fromhex(_hex(_pack("Raw data to RT")))
    )
    hexblob = blob.hex()
    assert _xnode_state_resource_name(hexblob) == "Raw data to RT"
    assert _xnode_state_row_names(hexblob) == ["Element"]


def test_class_method_name_is_the_class_names_last_word_when_present():
    # Mirrors FPGA_v1.vi's real "FIFO Write" node (cross-checked against
    # raph's own screenshot of it): the class name's own last word, found
    # verbatim in StateData, is the method row -- same drawer slot as
    # "Invoke Method"'s own method row.
    blob = _hex(
        _pack("Element"), b"\x00" * 4, _pack("Write"), b"\x00" * 4, _pack("Element")
    )
    assert _xnode_class_method_name("FIFO Write", blob) == "Write"


def test_class_method_name_empty_when_the_word_never_appears():
    # "Open FPGA VI Reference" has no separate method row -- "Reference"
    # never appears in StateData, so this must not guess from the class
    # name's own English wording alone.
    blob = _hex(_pack("some other data"))
    assert _xnode_class_method_name("Open FPGA VI Reference", blob) == ""


def test_class_method_name_empty_for_a_single_word_class():
    assert _xnode_class_method_name("xNode", "") == ""
