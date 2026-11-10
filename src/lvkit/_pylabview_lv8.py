"""Read support for the sections pylabview cannot read in a LabVIEW 8.0 - 8.5 VI.

LabVIEW 8.6 moved every type descriptor into one shared pool (``VCTP``). Before
that, a VI keeps its type descriptors inline, in the sections that use them:

* ``TM80`` (the data space's type map) is a pool laid out like ``VCTP`` (a u32
  count, then that many length-prefixed type descriptors), followed by a
  ``u2p2`` count of ``(type descriptor index, flags)`` pairs. A pair's position
  is the ``TypeID`` its object's ``tdOffset`` names; there is no ``IndexShift``.
* ``DTHP`` (the heap types) is the same pool followed by a ``u2p2`` count of
  type descriptor indexes, one per heap ``TypeID``.
* ``DFDS`` (the default data) is read by walking the ``TM80`` pairs in order,
  exactly as pylabview does for 9.0; only its type map comes from ``TM80``'s own
  pool.

pylabview reads none of them (its ``TM80`` and ``DTHP`` read the 9.0 layout, its
``DFDS`` needs ``VCTP``), and an 8.2 file's ``LVSR`` is shorter than its layout so
the file version reads 0.0.0. :func:`install_lv8_sections` teaches it all four,
reusing pylabview's own type descriptor and data fill classes unchanged. A file
of 8.6 or later reads through pylabview's own code, untouched.

``DTHP`` is read but still written as ``*_DTHP.bin``: the 9.0 ``TypeDescSlice``
cannot hold the per-``TypeID`` table of an 8.x file, and
``parser.heap_types.decode_heap_types`` reads that table from the sidecar.

Like the other patches this is read-only: a patched pylabview must not write a
``.vi`` back out.
"""

from __future__ import annotations

import types
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pylabview.LVblock as _lv_block  # type: ignore[import-untyped]
import pylabview.LVmisc as _lv_misc  # type: ignore[import-untyped]
import pylabview.LVrsrcontainer as _lv_rsrc  # type: ignore[import-untyped]

_VERS_VERSION_ATTR = "_lvkit_vers_version"
_installed = False


def _vers_version(vi: Any) -> Any:
    """The file version the ``vers`` block records (None when it has none)."""
    if hasattr(vi, _VERS_VERSION_ATTR):
        return getattr(vi, _VERS_VERSION_ATTR)
    version = None
    setattr(vi, _VERS_VERSION_ATTR, None)  # reading ``vers`` may ask for the version
    block = vi.blocks.get(b"vers")
    if block is not None:
        for section_num in sorted(block.sections):
            try:
                version = block.getVersion(section_num)
                break
            except Exception:  # noqa: BLE001 -- an unreadable block records nothing
                continue
    setattr(vi, _VERS_VERSION_ATTR, version)
    return version


def _has_inline_types(vi: Any) -> bool:
    """Whether ``vi`` keeps its type descriptors inline (8.0.0.1 up to the
    release that introduced ``VCTP``, 8.6.0.1) rather than in a ``VCTP`` pool."""
    pool = vi.blocks.get(b"VCTP")
    if pool is not None and not isinstance(pool, _InlineTypePool):
        return False
    version = vi.getFileVersion()
    return _lv_misc.isGreaterOrEqVersion(
        version, 8, 0, 0, 1
    ) and _lv_misc.isSmallerVersion(version, 8, 6, 0, 1)


class _InlineTypePool:
    """What ``VCTP`` answers for an inline-types file: the ``TM80`` type map's
    types by ``TypeID``. pylabview's data fill classes ask for a ``VCTP`` and
    its ``TM80`` export asks it for each type's name."""

    def __init__(self, tm80_section: Any) -> None:
        self._section = tm80_section

    def getContent(self) -> list[Any]:  # noqa: N802 -- pylabview's name
        return list(self._section.content_pool)

    def getTopType(self, type_id: int) -> Any:  # noqa: N802 -- pylabview's name
        type_index = getattr(self._section, "type_index", [])
        pool = getattr(self._section, "content_pool", [])
        if not 0 <= type_id < len(type_index):
            return None
        index = type_index[type_id]
        return pool[index].nested if index < len(pool) else None


@contextmanager
def _type_pool(vi: Any, tm80: Any) -> Iterator[None]:
    """Make ``VCTP`` answer for ``vi`` while the ``with`` body runs, when it keeps
    its types inline."""
    if b"VCTP" in vi.blocks or not _has_inline_types(vi):
        yield
        return
    section = tm80.sections[tm80.active_section_num]
    vi.blocks[b"VCTP"] = _InlineTypePool(section)
    try:
        yield
    finally:
        del vi.blocks[b"VCTP"]


def _read_pool(block: Any, section_num: int, bldata: Any) -> None:
    """Read the type descriptor pool at the start of ``block``'s section into
    ``section.content_pool`` (each descriptor parsed, owned by the pool)."""
    section = block.sections[section_num]
    section.content = []
    parse_descriptor = types.MethodType(
        _lv_block.TypeDescListBase.parseRSRCTypeDesc, block
    )
    count = int.from_bytes(bldata.read(4), byteorder="big", signed=False)
    if count > block.po.typedesc_list_limit:
        raise AttributeError(f"type descriptor count {count} exceeds the limit")
    position = bldata.tell()
    for index in range(count):
        _, length = parse_descriptor(section_num, bldata, index, position)
        position += length
    bldata.seek(position)
    section.content_pool = section.content
    for client in section.content_pool:
        client.nested.setOwningList(section.content_pool)
    for client in section.content_pool:
        client.nested.parseData()


def _u2p2(bldata: Any) -> int:
    """A ``u2p2`` number (2 bytes, or 4 with the top bit set); the section
    ending inside one is an error."""
    head = bldata.read(2)
    if len(head) < 2:
        raise EOFError("the section ends inside a number")
    value = int.from_bytes(head, byteorder="big")
    if value & 0x8000:
        tail = bldata.read(2)
        if len(tail) < 2:
            raise EOFError("the section ends inside a number")
        value = ((value & 0x7FFF) << 16) | int.from_bytes(tail, byteorder="big")
    return value


def _read_tm80(self: Any, section_num: int, bldata: Any) -> None:
    section = self.sections[section_num]
    _read_pool(self, section_num, bldata)
    pairs = [(_u2p2(bldata), _u2p2(bldata)) for _ in range(_u2p2(bldata))]
    section.indexShift = 0
    section.type_index = [index for index, _ in pairs]
    section.content = [flags for _, flags in pairs]


def _read_dthp(self: Any, section_num: int, bldata: Any) -> None:
    section = self.sections[section_num]
    _read_pool(self, section_num, bldata)
    section.topLevel = [_u2p2(bldata) for _ in range(_u2p2(bldata))]
    section.storage_format = "raw"


def install_lv8_sections() -> None:
    """Teach pylabview the sections of a LabVIEW 8.0 - 8.5 VI. Idempotent."""
    global _installed
    if _installed:
        return

    # The file version: the 8.2 ``LVSR`` is too short to read, so take it from
    # ``vers`` when ``LVSR`` gives none.
    _orig_file_version = _lv_rsrc.VI.getFileVersion

    def _file_version(self: Any) -> Any:
        version = _orig_file_version(self)
        if version["major"] != 0:
            return version
        return _vers_version(self) or version

    _file_version.__wrapped__ = _orig_file_version  # type: ignore[attr-defined]
    _lv_rsrc.VI.getFileVersion = _file_version

    # TM80: the pool and the (type, flags) pairs.
    _orig_tm80_parse = _lv_block.TM80.parseRSRCSectionData

    def _tm80_parse(self: Any, section_num: int, bldata: Any) -> None:
        if _has_inline_types(self.vi):
            _read_tm80(self, section_num, bldata)
        else:
            _orig_tm80_parse(self, section_num, bldata)

    _tm80_parse.__wrapped__ = _orig_tm80_parse  # type: ignore[attr-defined]
    _lv_block.TM80.parseRSRCSectionData = _tm80_parse

    def _with_pool(original: Any) -> Any:
        def method(self: Any, *args: Any, **kwargs: Any) -> Any:
            with _type_pool(self.vi, self):
                return original(self, *args, **kwargs)

        method.__wrapped__ = original  # type: ignore[attr-defined]
        return method

    _lv_block.TM80.getTypeMap = _with_pool(_lv_block.TM80.getTypeMap)
    _lv_block.TM80.getTypeEntry = _with_pool(_lv_block.TM80.getTypeEntry)
    _lv_block.TM80.exportXMLSectionData = _with_pool(
        _lv_block.TM80.exportXMLSectionData
    )

    # DTHP: the pool and the heap TypeID table.
    _orig_dthp_parse = _lv_block.DTHP.parseRSRCSectionData

    def _dthp_parse(self: Any, section_num: int, bldata: Any) -> None:
        if _has_inline_types(self.vi):
            _read_dthp(self, section_num, bldata)
        else:
            _orig_dthp_parse(self, section_num, bldata)

    _dthp_parse.__wrapped__ = _orig_dthp_parse  # type: ignore[attr-defined]
    _lv_block.DTHP.parseRSRCSectionData = _dthp_parse

    # DFDS: its type map is ``TM80``'s; its array fills ask for a ``VCTP``.
    _orig_dfds_parse = _lv_block.DFDS.parseRSRCSectionData

    def _dfds_parse(self: Any, section_num: int, bldata: Any) -> None:
        tm80 = self.vi.blocks.get(b"TM80")
        if tm80 is None:
            _orig_dfds_parse(self, section_num, bldata)
            return
        tm80.parseData(section_num=tm80.active_section_num)
        with _type_pool(self.vi, tm80):
            _orig_dfds_parse(self, section_num, bldata)

    _dfds_parse.__wrapped__ = _orig_dfds_parse  # type: ignore[attr-defined]
    _lv_block.DFDS.parseRSRCSectionData = _dfds_parse

    _installed = True
