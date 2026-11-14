"""LabVIEW digital data and digital waveforms as ``nitypes`` ``DigitalWaveform``.

Digital data is a table of line states: ``data`` has a row per stored sample and
a column per line, "with the most significant bit of data in the first column
and the least significant bit of data in the last column" (NI, Get Digital Data
Components), and ``transitions`` gives the sample at which each row starts. A
table may be compressed: NI's "Compressing Digital Data in Digital Tables"
(Figure 7) shows rows 0..6 = 0101, 0101, 1111, 1111, 1111, 0000, 0000 stored as
the rows 0101, 1111, 0000, 0000 labelled 0, 2, 5, 6, so a compressed table keeps
a row for the last sample. With no transitions every row is one sample (NI's
forum: a ramp VI leaves them unset).

Python holds all digital data uncompressed, as a ``DigitalWaveform`` (without
timing for a bare digital data value) with no transitions to read back. The
columns keep LabVIEW's order: ``nitypes`` puts line 0, "the least significant
bit", in the last column and the highest line in the first, as NI's digital data
does, so each line keeps its number.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.measure_data import measure_data_flavor

if TYPE_CHECKING:
    from .context import CodeGenContext

DIGITAL_CLASS = "DigitalWaveform"
DIGITAL_DATA_FLAVOR = "Digitaldata"

TRANSITIONS = "transitions"
DATA = "data"

# The module-level function a Bundle or constant calls to build digital data.
DIGITAL_DATA_FUNCTION = "digital_data"

_HELPER_SOURCE = '''
def digital_data(transitions, data):
    """Digital data as an uncompressed DigitalWaveform. ``data`` holds one row of
    line states per stored sample, the most significant line first; a sample
    takes the row with the last transition at or before it, up to the sample of
    the last transition. With no transitions each row is one sample."""
    rows = [list(row) for row in data]
    width = len(rows[0]) if rows else 0
    if any(len(row) != width for row in rows):
        raise ValueError("digital data rows have different numbers of lines")
    if len(transitions) > 0:
        if len(transitions) != len(rows):
            raise ValueError(
                f"digital data has {len(rows)} rows but {len(transitions)} transitions"
            )
        if transitions[0] != 0:
            raise ValueError("the first digital data transition must be 0")
        if any(b <= a for a, b in zip(transitions, transitions[1:])):
            raise ValueError("digital data transitions must be strictly increasing")
        ends = [*transitions[1:], transitions[-1] + 1]
        samples = []
        for row, start, end in zip(rows, transitions, ends):
            samples.extend([row] * (end - start))
        rows = samples
    lines = numpy.array(rows, dtype=numpy.uint8).reshape(len(rows), width)
    return nitypes.waveform.DigitalWaveform.from_lines(lines, numpy.uint8)
'''

IMPORTS = frozenset({"import nitypes.waveform", "import numpy"})


def is_digital_flavor(flavor: str) -> bool:
    """Whether a MeasureData ``flavor`` is held as a ``DigitalWaveform``."""
    entry = measure_data_flavor(flavor)
    return (
        entry is not None
        and entry.nitypes is not None
        and entry.nitypes.class_name == DIGITAL_CLASS
    )


def is_digital_data(flavor: str) -> bool:
    """Whether ``flavor`` is bare digital data (no timing or attributes)."""
    return flavor == DIGITAL_DATA_FLAVOR


def digital_data_function(ctx: CodeGenContext) -> str:
    """Register the function that builds digital data and return its name."""
    (definition,) = ast.parse(_HELPER_SOURCE).body
    assert isinstance(definition, ast.FunctionDef)
    ctx.imports.update(IMPORTS)
    return ctx.add_module_function(definition)


def digital_data_read(waveform: str, component: str) -> str:
    """The ``transitions`` or ``data`` of the digital data held in the
    ``DigitalWaveform`` variable ``waveform``: no transitions, and the rows with
    the most significant line first, as they were built."""
    if component == TRANSITIONS:
        return "[]"
    if component == DATA:
        return f"{waveform}.data.tolist()"
    raise AssertionError(f"not a digital data component: {component!r}")


def digital_y_read(waveform: str) -> str:
    """The digital data (Y) of the digital waveform variable ``waveform``."""
    return f"nitypes.waveform.DigitalWaveform.from_lines({waveform}.data)"


def empty_digital_data() -> str:
    """Digital data with no samples and no lines."""
    return (
        "nitypes.waveform.DigitalWaveform.from_lines("
        "numpy.zeros((0, 0), numpy.uint8), numpy.uint8)"
    )


def digital_waveform_build(y: str, t0: str, dt: str, attributes: str | None) -> str:
    """A new digital waveform of the digital data expression ``y`` with a regular
    timing from ``t0`` and ``dt`` and, when it has any, extended properties."""
    timing = (
        "nitypes.waveform.Timing.create_with_regular_interval("
        f"nitypes.bintime.TimeDelta({dt}), "
        f"nitypes.time.convert_datetime(nitypes.bintime.DateTime, {t0}))"
    )
    extended = f", extended_properties={attributes}" if attributes else ""
    return (
        f"nitypes.waveform.DigitalWaveform.from_lines("
        f"({y}).data, numpy.uint8, timing={timing}{extended})"
    )
