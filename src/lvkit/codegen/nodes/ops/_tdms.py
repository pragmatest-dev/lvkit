"""What the four TDM Streaming functions share: the terminal indices of TDMS
Open, Write, Set Properties and Close, and the npTDMS import.

Each function is a catalog entry under two primResIDs: the one a VI of the
corpus carries (2012, 2010, 2014, 2013, read off the terminals' ``parmIndex``)
and the one the VI-Scripting node list numbers sequentially (24112, 24110,
24114, 24113). The two entries carry the same function, with the same terminals
under different indices, so a handler names a terminal by its role and this
table says which index that role has.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenPane:
    """TDMS Open: ``operation`` picks open, create or read-only; the rest are
    the file's format."""

    file_path: int
    operation: int
    byte_order: int
    file_format_version: int
    create_index_file: int
    disable_buffering: int
    refnum_out: int


@dataclass(frozen=True)
class WritePane:
    """TDMS Write."""

    refnum: int
    data_layout: int
    group_name: int
    channel_names: int
    data: int
    refnum_out: int
    group_name_out: int
    channel_names_out: int


@dataclass(frozen=True)
class SetPropertiesPane:
    """TDMS Set Properties."""

    refnum: int
    group_name: int
    channel_name: int
    property_names: int
    property_values: int
    refnum_out: int
    group_name_out: int
    channel_name_out: int


@dataclass(frozen=True)
class ClosePane:
    """TDMS Close."""

    refnum: int
    file_path_out: int


OPEN_PANES = {
    2012: OpenPane(11, 10, 9, 7, 5, 6, 3),
    24112: OpenPane(8, 7, 6, 4, 2, 3, 1),
}
WRITE_PANES = {
    2010: WritePane(0, 1, 5, 7, 9, 4, 6, 8),
    24110: WritePane(0, 1, 3, 5, 7, 2, 4, 6),
}
SET_PROPERTIES_PANES = {
    2014: SetPropertiesPane(11, 10, 9, 7, 5, 3, 2, 1),
    24114: SetPropertiesPane(9, 8, 7, 5, 4, 3, 2, 1),
}
CLOSE_PANES = {
    2013: ClosePane(11, 3),
    24113: ClosePane(3, 1),
}

# The import every generated TDMS node writes.
NPTDMS_IMPORT = "import nptdms"
