"""Reading the type map, heap types and default data of a LabVIEW 8.0 - 8.5 VI.

The byte fixtures in ``tests/data`` are whole sections cut from real files:

* ``lv85_unitinfo_*``: ``TM80``, ``DTHP`` and ``DFDS`` (the inflated section) of
  ``PicoScope3000UnitInfo.vi`` (saved by LabVIEW 8.5), from the PicoScope
  LabVIEW examples.
* ``lv82_trim_*``: the same sections of ``Trim Whitespace__JKI_RIGHT_CLICK_BUILD_
  SUPPORT.vi`` (saved by LabVIEW 8.2), from the JKI VI Tester.

The readers are built into pylabview by ``lvkit._pylabview_lv8``; each test builds
the real pylabview block over the section bytes, with a stand-in for the VI that
holds only what the block asks of it.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pylabview.LVblock as lv_block  # type: ignore[import-untyped]
import pylabview.LVmisc as lv_misc  # type: ignore[import-untyped]
import pylabview.LVrsrcontainer as lv_rsrc  # type: ignore[import-untyped]
import pytest
from pylabview.LVdatatype import (  # type: ignore[import-untyped]
    TD_FULL_TYPE,
    TM_FLAGS,
)
from pylabview.LVxml import ET  # type: ignore[import-untyped]

from lvkit import extractor  # noqa: F401  (installs the pylabview patches)

DATA = Path(__file__).parent / "data"

_VERSION_85 = lv_misc.decodeVersion(0x08508002)
_VERSION_82 = lv_misc.decodeVersion(0x08208002)


class _Vi:
    """The part of a pylabview VI that the blocks under test ask for."""

    src_fname = "fixture.vi"
    dataSource = "rsrc"
    textEncoding = "mac_roman"

    def __init__(self, version: dict[str, Any]) -> None:
        self.blocks: dict[bytes, Any] = {}
        self._version = version

    def getFileVersion(self) -> dict[str, Any]:  # noqa: N802 -- pylabview's name
        return self._version

    @staticmethod
    def _key(ident: str | bytes) -> bytes:
        return ident.encode() if isinstance(ident, str) else ident

    def get(self, ident: str | bytes) -> Any:
        return self.blocks.get(self._key(ident))

    def get_or_raise(self, ident: str | bytes) -> Any:
        if self._key(ident) not in self.blocks:
            raise LookupError(f"Block {ident!r} not found in RSRC file.")
        return self.blocks[self._key(ident)]

    def get_one_of(self, *idents: str | bytes) -> Any:
        return next((self.get(i) for i in idents if self.get(i) is not None), None)


def _vi(version: dict[str, Any]) -> Any:
    return _Vi(version)


def _po() -> argparse.Namespace:
    return argparse.Namespace(
        verbose=0,
        print_map=None,
        typedesc_list_limit=4095,
        array_data_limit=(2**28) - 1,
        store_as_data_above=4095,
    )


def _block(cls: Any, vi: Any, data: bytes) -> Any:
    """A real pylabview block of class ``cls`` whose section 0 holds ``data``."""
    block = cls(vi, _po())
    block.ident = cls.__name__.encode()
    section = block.createSection()
    section.start.section_idx = 0
    section.block_pos = 0
    block.sections[0] = section
    block.active_section_num = 0
    block.size = len(data)
    section.raw_data = data
    section.raw_data_updated = True
    vi.blocks[block.ident] = block
    return block


def _read(name: str) -> bytes:
    return (DATA / name).read_bytes()


def _ready(version: dict[str, Any], prefix: str, *, dfds: bool = False) -> Any:
    vi = _vi(version)
    _block(lv_block.TM80, vi, _read(f"{prefix}_TM80.bin"))
    if dfds:
        _block(lv_block.DFDS, vi, _read(f"{prefix}_DFDS.bin"))
    return vi


def _types_of(vi: Any) -> list[tuple[int, int, Any]]:
    entries = vi.blocks[b"TM80"].getTypeMap()
    return [(entry.index, entry.flags, entry.td) for entry in entries]


def test_tm80_85_pool_and_pairs_are_read_to_the_end() -> None:
    vi = _ready(_VERSION_85, "lv85_unitinfo")
    block = vi.blocks[b"TM80"]
    entries = _types_of(vi)
    section = block.sections[0]
    assert not section.parse_failed
    assert len(section.content_pool) == 50
    assert len(entries) == 19
    assert section.indexShift == 0
    # A pair's position is its TypeID.
    assert [index for index, _, _ in entries] == list(range(19))


def test_tm80_85_pair_names_its_type_and_flags() -> None:
    vi = _ready(_VERSION_85, "lv85_unitinfo")
    index, flags, td = _types_of(vi)[15]
    assert index == 15
    assert flags == TM_FLAGS.TMFBit13
    assert td.fullType() == TD_FULL_TYPE.NumInt16
    assert td.index == 47


def test_tm80_85_pair_without_a_fill_is_marked_with_the_default_flag() -> None:
    vi = _ready(_VERSION_85, "lv85_unitinfo")
    _, flags, td = _types_of(vi)[16]
    assert flags & TM_FLAGS.TMFBit11
    assert td.fullType() == TD_FULL_TYPE.String


def test_tm80_85_exports_an_inline_section_like_the_pooled_form() -> None:
    vi = _ready(_VERSION_85, "lv85_unitinfo")
    block = vi.blocks[b"TM80"]
    block.parseData()
    section_elem = ET.Element("Section")
    block.exportXMLSectionData(section_elem, 0, block.sections[0], "x")
    assert section_elem.get("IndexShift") == "0"
    clients = section_elem.findall("Client")
    assert len(clients) == 19
    assert clients[15].get("TMFBit13") == "1"
    assert clients[16].get("TMFBit11") == "1"
    comments = [
        child.text.strip()
        for child in section_elem
        if child.tag is ET.Comment and child.text
    ]
    assert comments[15] == "TypeID 15: NumInt16"
    assert comments[16] == "TypeID 16: String"
    assert b"VCTP" not in vi.blocks


def test_dthp_85_reads_the_pool_and_the_heap_type_table() -> None:
    vi = _vi(_VERSION_85)
    block = _block(lv_block.DTHP, vi, _read("lv85_unitinfo_DTHP.bin"))
    block.parseData()
    section = block.sections[0]
    assert not section.parse_failed
    assert len(section.content_pool) == 37
    assert len(section.topLevel) == 54
    assert all(index < 37 for index in section.topLevel)
    # Written as the sidecar `parser.heap_types` reads, not as a type slice.
    assert section.storage_format == "raw"


def test_dfds_85_reads_the_fill_of_each_type_that_has_one() -> None:
    vi = _ready(_VERSION_85, "lv85_unitinfo", dfds=True)
    block = vi.blocks[b"DFDS"]
    block.parseData()
    section = block.sections[0]
    assert not section.parse_failed
    fills = {df.index: df for df in section.content}
    # The 12 fills use the whole inflated section.
    assert len(fills) == 12
    section_elem = ET.Element("Section")
    block.exportXMLSectionData(section_elem, 0, section, "x")
    by_id = {fill.get("TypeID"): fill for fill in section_elem.findall("DataFill")}
    assert by_id["15"].findtext("I16") == "256"
    assert by_id["17"].findtext("I32") == "7"
    assert "16" not in by_id  # the String marked as holding its default
    assert b"VCTP" not in vi.blocks


def test_dfds_82_reads_the_two_regex_strings() -> None:
    vi = _ready(_VERSION_82, "lv82_trim", dfds=True)
    block = vi.blocks[b"DFDS"]
    block.parseData()
    section = block.sections[0]
    assert not section.parse_failed
    section_elem = ET.Element("Section")
    block.exportXMLSectionData(section_elem, 0, section, "x")
    by_id = {fill.get("TypeID"): fill for fill in section_elem.findall("DataFill")}
    assert by_id["12"].findtext("String") == r"[\t\n\r\s]*$"
    assert by_id["15"].findtext("String") == r"^[\t\n\r\s]*"


def test_dthp_82_reads_the_pool_and_the_heap_type_table() -> None:
    vi = _vi(_VERSION_82)
    block = _block(lv_block.DTHP, vi, _read("lv82_trim_DTHP.bin"))
    block.parseData()
    section = block.sections[0]
    assert not section.parse_failed
    assert len(section.content_pool) == 7
    assert len(section.topLevel) == 30


def test_the_file_version_comes_from_vers_when_lvsr_gives_none() -> None:
    zero = lv_misc.decodeVersion(0x0)
    vi = SimpleNamespace(
        blocks={
            b"LVSR": SimpleNamespace(getVersion=lambda: zero),
            b"vers": SimpleNamespace(
                sections={4: object()}, getVersion=lambda number: _VERSION_82
            ),
        },
        get_one_of=lambda *idents: next(
            (vi.blocks[i] for i in (b"LVSR", b"vers") if i in vi.blocks), None
        ),
    )
    assert lv_rsrc.VI.getFileVersion(vi) == _VERSION_82


def test_the_file_version_stays_lvsr_s_when_lvsr_gives_one() -> None:
    vi = SimpleNamespace(
        blocks={
            b"LVSR": SimpleNamespace(getVersion=lambda: _VERSION_85),
            b"vers": SimpleNamespace(
                sections={4: object()}, getVersion=lambda number: _VERSION_82
            ),
        },
        get_one_of=lambda *idents: vi.blocks[b"LVSR"],
    )
    assert lv_rsrc.VI.getFileVersion(vi) == _VERSION_85


def test_a_9_0_type_map_is_read_with_pylabview_s_own_layout() -> None:
    # count 2, IndexShift 5, then two flag words (the 9.0 layout).
    data = (
        (2).to_bytes(2, "big")
        + (5).to_bytes(2, "big")
        + int(TM_FLAGS.TMFBit13).to_bytes(2, "big")
        + int(TM_FLAGS.TMFBit11).to_bytes(2, "big")
    )
    vi = _vi(lv_misc.decodeVersion(0x09008002))
    block = _block(lv_block.TM80, vi, data)
    block.parseData()
    section = block.sections[0]
    assert not section.parse_failed
    assert section.indexShift == 5
    assert section.content == [TM_FLAGS.TMFBit13, TM_FLAGS.TMFBit11]


@pytest.mark.parametrize("version", [_VERSION_82, _VERSION_85])
def test_a_corrupt_pool_fails_the_section_instead_of_the_run(
    version: dict[str, Any],
) -> None:
    vi = _vi(version)
    block = _block(lv_block.TM80, vi, _read("lv85_unitinfo_TM80.bin")[:300])
    block.parseData()
    assert block.sections[0].parse_failed
