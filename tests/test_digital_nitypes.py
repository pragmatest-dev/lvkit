"""Digital data and a digital waveform in generated Python are a ``nitypes``
``DigitalWaveform``, held uncompressed: Bundle and Unbundle By Name and constants
build and read them, transitions expand into samples, and each case runs the
generated Python."""

from __future__ import annotations

import numpy
import pytest
from nitypes.waveform import DigitalWaveform

from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.timestamp_value import TIMESTAMP_FLAVOR, timestamp_from_parts
from tests.native_vi import array, generate, node, scalar

TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)
DBL = scalar("NumFloat64")
TRANSITIONS_TYPE = array("NumUInt32")
DATA_TYPE = array("NumUInt8", 2)


def flavor_type(flavor: str) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="MeasureData", measure_flavor=flavor
    )


DIGITAL_DATA = flavor_type("Digitaldata")
DIGITAL_WAVEFORM = flavor_type("DigitalWaveform")

# NI's Figure 7 (Compressing Digital Data in Digital Tables): seven samples, kept
# as four rows labelled 0, 2, 5, 6.
FIGURE_7_ROWS = [
    [0, 1, 0, 1],
    [0, 1, 0, 1],
    [1, 1, 1, 1],
    [1, 1, 1, 1],
    [1, 1, 1, 1],
    [0, 0, 0, 0],
    [0, 0, 0, 0],
]
FIGURE_7_COMPRESSED = [[0, 1, 0, 1], [1, 1, 1, 1], [0, 0, 0, 0], [0, 0, 0, 0]]
FIGURE_7_TRANSITIONS = [0, 2, 5, 6]


def _terminals(node_id: str, direction: str, fields: dict[int, LVType]):
    return [
        Terminal(
            id=f"{node_id}:{10 + index}",
            index=10 + index,
            direction=direction,
            nmux_role="list",
            nmux_field_index=index,
            lv_type=lv_type,
        )
        for index, lv_type in fields.items()
    ]


def _bundle(node_id: str, wave: LVType, fields: dict[int, LVType]):
    bundle = node(node_id, None, [(0, "output", wave)], node_type="nMux")
    bundle.terminals[0].nmux_role = "agg"
    bundle.terminals.extend(_terminals(node_id, "input", fields))
    return bundle


def _unbundle(node_id: str, wave: LVType, fields: dict[int, LVType]):
    unbundle = node(node_id, None, [(0, "input", wave)], node_type="nMux")
    unbundle.terminals[0].nmux_role = "agg"
    unbundle.terminals.extend(_terminals(node_id, "output", fields))
    return unbundle


def _digital_data_round_trip():
    """Build digital data from controls and unbundle it again."""
    fields = {0: TRANSITIONS_TYPE, 1: DATA_TYPE}
    return generate(
        {"transitions": TRANSITIONS_TYPE, "data": DATA_TYPE},
        {
            "dd": DIGITAL_DATA,
            "transitions_out": TRANSITIONS_TYPE,
            "data_out": DATA_TYPE,
        },
        [_bundle("bd", DIGITAL_DATA, fields), _unbundle("ub", DIGITAL_DATA, fields)],
        [
            ("fp:transitions", "bd:10"),
            ("fp:data", "bd:11"),
            ("bd:0", "ub:0"),
            ("bd:0", "fp:dd"),
            ("ub:10", "fp:transitions_out"),
            ("ub:11", "fp:data_out"),
        ],
    )


@pytest.mark.parametrize(
    ("transitions", "rows"),
    [
        (FIGURE_7_TRANSITIONS, FIGURE_7_COMPRESSED),
        ([], FIGURE_7_ROWS),
    ],
    ids=["compressed", "uncompressed"],
)
def test_ni_figure_7_is_seven_samples_compressed_or_not(transitions, rows) -> None:
    result = _digital_data_round_trip().function(transitions, rows)
    assert type(result.dd) is DigitalWaveform
    assert result.dd.dtype == numpy.dtype(numpy.uint8)
    assert result.dd.sample_count == 7
    assert result.data_out == FIGURE_7_ROWS
    assert result.transitions_out == []


def test_the_first_column_is_the_most_significant_line() -> None:
    result = _digital_data_round_trip().function([], [[1, 0, 0, 0], [0, 0, 1, 0]])
    # Line 0, the least significant bit, is the last column; line 3 the first.
    assert [result.dd.signals[line].data.tolist() for line in range(4)] == [
        [0, 0],
        [0, 1],
        [0, 0],
        [1, 0],
    ]
    assert result.dd.data.tolist() == [[1, 0, 0, 0], [0, 0, 1, 0]]
    assert result.data_out == [[1, 0, 0, 0], [0, 0, 1, 0]]


def test_states_beyond_zero_and_one_are_kept() -> None:
    result = _digital_data_round_trip().function([], [[2, 7, 3, 5]])
    assert result.data_out == [[2, 7, 3, 5]]


def test_digital_data_with_nothing_wired_is_empty() -> None:
    fields = {0: TRANSITIONS_TYPE, 1: DATA_TYPE}
    generated = generate(
        {},
        {"dd": DIGITAL_DATA},
        [_bundle("bd", DIGITAL_DATA, fields)],
        [("bd:0", "fp:dd")],
    )
    result = generated.function().dd
    assert result.sample_count == 0 and result.signal_count == 0


@pytest.mark.parametrize(
    ("transitions", "rows", "message"),
    [
        ([1, 2], [[0], [1]], "first digital data transition must be 0"),
        ([0, 2, 2], [[0], [1], [0]], "strictly increasing"),
        ([0, 3, 2], [[0], [1], [0]], "strictly increasing"),
        ([0, 1], [[0], [1], [0]], "3 rows but 2 transitions"),
        ([], [[0, 1], [1]], "different numbers of lines"),
    ],
)
def test_digital_data_that_cannot_be_expanded_raises(
    transitions, rows, message
) -> None:
    with pytest.raises(ValueError, match=message):
        _digital_data_round_trip().function(transitions, rows)


_T0 = timestamp_from_parts(3_000_000_000, 12345678901234567)


def test_a_digital_waveform_builds_and_unbundles_exactly() -> None:
    data_fields = {0: TRANSITIONS_TYPE, 1: DATA_TYPE}
    wave_fields = {0: TIMESTAMP, 1: DBL, 2: DIGITAL_DATA}
    generated = generate(
        {
            "t0": TIMESTAMP,
            "dt": DBL,
            "transitions": TRANSITIONS_TYPE,
            "data": DATA_TYPE,
        },
        {
            "wf": DIGITAL_WAVEFORM,
            "t0_out": TIMESTAMP,
            "dt_out": DBL,
            "y_out": DIGITAL_DATA,
            "data_out": DATA_TYPE,
        },
        [
            _bundle("dd", DIGITAL_DATA, data_fields),
            _bundle("bd", DIGITAL_WAVEFORM, wave_fields),
            _unbundle("ub", DIGITAL_WAVEFORM, wave_fields),
            _unbundle("yb", DIGITAL_DATA, {1: DATA_TYPE}),
        ],
        [
            ("fp:transitions", "dd:10"),
            ("fp:data", "dd:11"),
            ("fp:t0", "bd:10"),
            ("fp:dt", "bd:11"),
            ("dd:0", "bd:12"),
            ("bd:0", "ub:0"),
            ("bd:0", "fp:wf"),
            ("ub:10", "fp:t0_out"),
            ("ub:11", "fp:dt_out"),
            ("ub:12", "fp:y_out"),
            ("ub:12", "yb:0"),
            ("yb:11", "fp:data_out"),
        ],
    )
    result = generated.function(_T0, 0.25, FIGURE_7_TRANSITIONS, FIGURE_7_COMPRESSED)
    assert type(result.wf) is DigitalWaveform
    assert result.wf.sample_count == 7
    assert result.wf.data.tolist() == FIGURE_7_ROWS
    assert result.t0_out == _T0
    assert result.dt_out == 0.25
    assert type(result.y_out) is DigitalWaveform
    assert result.data_out == FIGURE_7_ROWS


def test_a_digital_data_constant_is_expanded() -> None:
    value = {
        "transitions": [str(t) for t in FIGURE_7_TRANSITIONS],
        "data": [[str(s) for s in row] for row in FIGURE_7_COMPRESSED],
    }
    generated = generate(
        {},
        {"dd": DIGITAL_DATA},
        [],
        [("c1", "fp:dd")],
        constants={"c1": ("", DIGITAL_DATA, value)},
    )
    result = generated.function().dd
    assert type(result) is DigitalWaveform
    assert result.data.tolist() == FIGURE_7_ROWS


def test_a_digital_waveform_constant_has_timing_and_expanded_data() -> None:
    from lvkit.models import VariantData

    value = {
        "t0": _T0,
        "dt": "0.25",
        "Y": {
            "transitions": [str(t) for t in FIGURE_7_TRANSITIONS],
            "data": [[str(s) for s in row] for row in FIGURE_7_COMPRESSED],
        },
        "error": {"status": "False", "code": "0", "source": "''"},
        "attributes": VariantData(None, None, ()),
    }
    generated = generate(
        {},
        {"wf": DIGITAL_WAVEFORM},
        [],
        [("c1", "fp:wf")],
        constants={"c1": ("", DIGITAL_WAVEFORM, value)},
    )
    result = generated.function().wf
    assert type(result) is DigitalWaveform
    assert result.data.tolist() == FIGURE_7_ROWS
    assert result.timing.start_time.to_tuple() == (3_000_000_000, 12345678901234567)
    assert result.timing.sample_interval.total_seconds() == 0.25
