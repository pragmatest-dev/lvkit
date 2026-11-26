"""A Merge Signals node (class ``mergeSignal``) generates the list of waveforms NI
says composes its dynamic data wire: each input becomes its waveforms, in input
order. The generated Python is run; an input kind NI does not specify is a gap
that says why."""

from __future__ import annotations

import numpy
import pytest
from nitypes.waveform import AnalogWaveform

from tests.error_diagrams import (
    Package,
    Project,
    Vi,
    project,  # noqa: F401
)

NAME = "Merge.vi"
DYNAMIC = '<TypeDesc Type="MeasureData" Flavor="Dynamicdata" />'
WAVEFORM = '<TypeDesc Type="MeasureData" Flavor="Float64Waveform" />'
DIGITAL = '<TypeDesc Type="MeasureData" Flavor="DigitalWaveform" />'


def _array(vi: Vi, element: str, dimensions: int = 1) -> int:
    item = vi.add_type(element)
    dims = "<Dimension />" * dimensions
    return vi.add_type(
        f'<TypeDesc Type="Array">{dims}<TypeDesc TypeID="{item}" /></TypeDesc>'
    )


def _merge(project: Project, inputs: list[tuple[str, str]]) -> Package:  # noqa: F811
    """Generate a VI whose controls ``(label, type)`` all feed one Merge Signals
    node, whose output is the indicator ``out``."""
    vi = Vi(NAME)
    dynamic = vi.add_type(DYNAMIC)
    types = {
        "dbl": vi.add_type('<TypeDesc Type="NumFloat64" />'),
        "i32": vi.add_type('<TypeDesc Type="NumInt32" />'),
        "bool": vi.add_type('<TypeDesc Type="Boolean" />'),
        "wave": vi.add_type(WAVEFORM),
        "dynamic": dynamic,
    }
    types["dbl[]"] = _array(vi, '<TypeDesc Type="NumFloat64" />')
    types["i32[]"] = _array(vi, '<TypeDesc Type="NumInt32" />')
    types["dbl[][]"] = _array(vi, '<TypeDesc Type="NumFloat64" />', 2)
    types["bool[]"] = _array(vi, '<TypeDesc Type="Boolean" />')
    types["wave[]"] = _array(vi, WAVEFORM)
    types["digital"] = vi.add_type(DIGITAL)
    controls = {label: vi.control(label, types[kind]) for label, kind in inputs}
    slots = [("out", 0, dynamic, True)] + [
        (label, index, dynamic, False)
        for index, (label, kind) in enumerate(inputs, start=1)
    ]
    terms, uids = vi.terminals("mergeSignalDCO", slots, "unusedIndex")
    uid = vi.next_uid()
    vi.scope.nodes.append(
        f'<SL__arrayElement class="mergeSignal" uid="{uid}">'
        f'<termList elements="{len(slots)}">{terms}</termList>'
        "<bounds>(0, 0, 30, 30)</bounds></SL__arrayElement>"
    )
    out = vi.indicator("out", dynamic, ddo_class="stdMeasureData")
    vi.wire(uids["out"], out)
    for label, terminal in controls.items():
        vi.wire(terminal, uids[label])
    project.add(vi)
    return project.generate(NAME)


def _samples(waveform: AnalogWaveform) -> list[float]:
    return [float(v) for v in waveform.raw_data]


def test_two_arrays_merge_to_two_waveforms_in_order(
    project: Project,  # noqa: F811
) -> None:
    package = _merge(project, [("a", "dbl[]"), ("b", "dbl[]")])
    assert package.gaps == []
    result = package.function(a=[1.0, 2.0, 3.0], b=[4.0, 5.0]).out
    assert len(result) == 2
    assert all(isinstance(w, AnalogWaveform) for w in result)
    assert _samples(result[0]) == [1.0, 2.0, 3.0]
    assert _samples(result[1]) == [4.0, 5.0]
    assert "list[nitypes.waveform.AnalogWaveform]" in package.source


def test_scalar_is_a_one_sample_waveform_ahead_of_the_array(
    project: Project,  # noqa: F811
) -> None:
    package = _merge(project, [("x", "dbl"), ("a", "dbl[]")])
    result = package.function(x=7.5, a=[1.0, 2.0]).out
    assert [_samples(w) for w in result] == [[7.5], [1.0, 2.0]]


def test_integers_become_double_samples(project: Project) -> None:  # noqa: F811
    package = _merge(project, [("n", "i32"), ("a", "i32[]")])
    result = package.function(n=3, a=[1, 2]).out
    assert [_samples(w) for w in result] == [[3.0], [1.0, 2.0]]
    assert all(w.dtype == numpy.dtype("float64") for w in result)


def test_waveform_and_waveform_array_expand_to_their_waveforms(
    project: Project,  # noqa: F811
) -> None:
    package = _merge(project, [("w", "wave"), ("ws", "wave[]"), ("a", "dbl[]")])
    one = AnalogWaveform.from_array_1d([1.0], numpy.float64)
    two = AnalogWaveform.from_array_1d([2.0, 2.0], numpy.float64)
    three = AnalogWaveform.from_array_1d([3.0], numpy.float64)
    result = package.function(w=one, ws=[two, three], a=[9.0]).out
    assert result[:3] == [one, two, three]
    assert _samples(result[3]) == [9.0]
    assert len(result) == 4


def test_dynamic_data_input_adds_its_waveforms(project: Project) -> None:  # noqa: F811
    package = _merge(project, [("d", "dynamic"), ("a", "dbl[]")])
    one = AnalogWaveform.from_array_1d([1.0], numpy.float64)
    two = AnalogWaveform.from_array_1d([2.0], numpy.float64)
    result = package.function(d=[one, two], a=[5.0]).out
    assert result[:2] == [one, two]
    assert _samples(result[2]) == [5.0]


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("bool", "does not say what sample a Boolean is"),
        ("bool[]", "does not say what sample a Boolean is"),
        ("dbl[][]", "columns as channels and with rows as channels"),
        ("digital", "holds analog waveforms"),
    ],
)
def test_input_kind_ni_does_not_specify_is_a_gap_with_its_reason(
    project: Project,  # noqa: F811
    kind: str,
    reason: str,
) -> None:
    package = _merge(project, [("a", "dbl[]"), ("b", kind)])
    assert len(package.gaps) == 1
    message = str(package.gaps[0])
    assert "Merge Signals of" in message
    assert reason in message
