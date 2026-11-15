"""TDMS Write of a 1D or 2D array of numbers, Booleans, strings or timestamps.

NI, "TDMS Write Function" (``functions/tdms-write.html``): ``data`` is "1D or 2D
array of: signed or unsigned integers, floating-point numbers, including complex
single-precision and double-precision floating-point numbers, timestamps,
Booleans, alphanumeric strings". ``data layout`` "specifies the arrangement of the
data": decimated (the default) "prioritizes channels before samples ... lists all
samples from the first channel, then all samples from the second channel";
interleaved "lists the first sample from every channel, then the second sample
from every channel". A LabVIEW 2D array is read row by row, so a decimated 2D
array has one channel per row and an interleaved one has one per column, which
the forums say too ("TDMS VIs log data grouped in channels according to the rows
of a 2-D array":
https://forums.ni.com/t5/Example-Programs/Write-Single-Data-point-Multiple-Channels-to-TDMS-using-LabVIEW/tac-p/3530642).
A 1D array is one channel when decimated; interleaved, the page does not say how
many channels it holds, so that is a run-time gap.

NI's page names the channels of an array "Untitled, Untitled1, Untitled2..." when
``channel name(s) in`` is unwired or short of names.

The TDMS file format (https://www.ni.com/en/support/documentation/supplemental/07/tdms-file-format-internal-structure.html)
stores a timestamp channel, and npTDMS writes a ``numpy.datetime64`` array as
one (read back as ``datetime64``) and complex arrays as the complex TDMS types.
A timestamp is a ``hightime.datetime``, written to microseconds.
"""

from __future__ import annotations

from dataclasses import dataclass

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_boolean_type_name, is_string_type_name
from lvkit.models import LVTypeKind, Terminal

# The numpy type of a channel of each LabVIEW numeric type: npTDMS picks the
# smallest integer type that holds the values of a list, so the type is written.
NUMPY_TYPES = {
    "NumInt8": "int8",
    "NumInt16": "int16",
    "NumInt32": "int32",
    "NumInt64": "int64",
    "NumUInt8": "uint8",
    "NumUInt16": "uint16",
    "NumUInt32": "uint32",
    "NumUInt64": "uint64",
    "NumFloat32": "float32",
    "NumFloat64": "float64",
    "NumComplex64": "complex64",
    "NumComplex128": "complex128",
}
TIMESTAMP_CONVERT = (
    "numpy.array([_t.astimezone(datetime.timezone.utc).replace(tzinfo=None) "
    "for _t in {}], dtype='datetime64[us]')"
)


@dataclass(frozen=True)
class ChannelData:
    """The array a TDMS Write writes: its dimensions and the expression (``{}``
    is a channel's values) that makes the values npTDMS writes."""

    dimensions: int
    convert: str
    imports: tuple[str, ...]


def channel_data(node: PrimitiveNode, term: Terminal | None) -> ChannelData:
    """What the data of ``term`` is; a gap with the reason when it has no channel."""
    lv_type = term.lv_type if term is not None else None
    if lv_type is None:
        raise ConversionGap("TDMS Write of data of an unknown type", node)
    if lv_type.kind != LVTypeKind.ARRAY:
        raise ConversionGap(
            f"TDMS Write of {term.type_label() if term else 'that type'} is not "
            "converted: only a 1D or 2D array is",
            node,
        )
    dimensions = lv_type.dimensions or 1
    if dimensions > 2:
        raise ConversionGap(
            f"TDMS Write of a {dimensions}D array is not converted: NI's TDMS "
            "Write page accepts a 1D or 2D array",
            node,
        )
    element = lv_type.element_type
    name = element.underlying_type if element else None
    if element is not None and element.is_timestamp:
        return ChannelData(
            dimensions, TIMESTAMP_CONVERT, ("import datetime", "import numpy")
        )
    if name in NUMPY_TYPES:
        return ChannelData(
            dimensions,
            f"numpy.array({{}}, dtype=numpy.{NUMPY_TYPES[name]})",
            ("import numpy",),
        )
    if is_boolean_type_name(name):
        return ChannelData(
            dimensions, "numpy.array({}, dtype=numpy.bool_)", ("import numpy",)
        )
    if is_string_type_name(name):
        return ChannelData(dimensions, "{}", ())
    raise ConversionGap(
        f"TDMS Write of an array of {name} is not converted: npTDMS has no "
        "channel type for it",
        node,
    )


def interleaved_gap_source(layout: str) -> str:
    """The statement that raises when the data layout of a 1D array is
    interleaved."""
    return (
        f"if {layout} == 1:\n"
        "    raise NotImplementedError('TDMS Write of a 1D array with the data "
        f"layout ' + str({layout}) + ' (interleaved) is not converted: NI does "
        "not say how many channels it holds')"
    )


def rows_write_source(
    ref: str,
    group: str,
    data: str,
    layout: str,
    convert: str,
    names: str | None,
    names_is_array: bool,
    channels: str,
    channel: str,
) -> str:
    """The statements that write each channel of the 2D array ``data`` (a row
    each when ``layout`` is decimated, a column each when interleaved) in one
    segment and bind ``channels`` to the channel names written."""
    if names is None:
        given = "[]"
    elif names_is_array:
        given = f"list({names})"
    else:
        given = f"[{names}]"
    return (
        f"{channels} = []\n"
        f"_given_names = {given}\n"
        f"_rows = [list(_row) for _row in {data}]\n"
        f"if {layout} == 1:\n"
        f"    _rows = [list(_column) for _column in zip(*_rows)]\n"
        f"_objects = []\n"
        f"for _index, _row in enumerate(_rows):\n"
        f"    {channel} = (\n"
        f"        _given_names[_index] if _index < len(_given_names) else None\n"
        f"    ) or ('Untitled' if _index == 0 else f'Untitled{{_index}}')\n"
        f"    {ref}[2].add(({group}, {channel}))\n"
        f"    _objects.append(\n"
        f"        nptdms.ChannelObject({group}, {channel}, {convert.format('_row')})\n"
        f"    )\n"
        f"    {channels}.append({channel})\n"
        f"{ref}[0].write_segment(_objects)\n"
    )
