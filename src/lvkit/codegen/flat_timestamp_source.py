"""The Python that flattens a time stamp and reads it back.

NI ("How LabVIEW Stores Data in Memory"): a time stamp is a 128-bit fixed-point
value, an I64 count of whole seconds since 1904-01-01 00:00:00 UTC and a U64 count
of 2^-64 second fractions; flattened data is big-endian (NI's "Flattened Data").
The value in Python is a UTC ``hightime.datetime`` (``lvkit.timestamp_value``); a
time stamp outside what it holds reads back as ``None``.

Each template is the source of one module-level function; ``{name}`` is its
generated name and ``{order}`` the ``struct`` byte-order prefix.
"""

from __future__ import annotations

TIMESTAMP_IMPORTS = ("import hightime", "from datetime import UTC")

FLATTEN_TIMESTAMP = """def {name}(value):
    delta = value - hightime.datetime(1904, 1, 1, tzinfo=UTC)
    seconds = delta.days * 86400 + delta.seconds
    yoctoseconds = (
        delta.microseconds * 10**18 + delta.femtoseconds * 10**9 + delta.yoctoseconds
    )
    fraction = (yoctoseconds * 2**64 + 10**24 // 2) // 10**24
    if fraction == 2**64:
        seconds, fraction = seconds + 1, 0
    return struct.pack({order!r} + 'qQ', seconds, fraction)
"""

UNFLATTEN_TIMESTAMP = """def {name}(data, pos):
    seconds, fraction = struct.unpack_from({order!r} + 'qQ', data, pos)
    yoctoseconds = (10**24 * fraction + 2**63) // 2**64
    try:
        value = (
            hightime.datetime(1904, 1, 1, tzinfo=UTC)
            + hightime.timedelta(seconds=seconds)
            + hightime.timedelta(yoctoseconds=yoctoseconds)
        )
    except OverflowError:
        value = None
    return value, pos + 16
"""
