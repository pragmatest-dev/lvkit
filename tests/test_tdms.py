"""TDMS Open (2012), Set Properties (2014), Write (2010) and Close (2013), written
with npTDMS.

Each test builds a tiny diagram of the four nodes in NI's order, generates its
Python and runs it on real files, then reads the file back with npTDMS.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    PATH,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

nptdms = pytest.importorskip("nptdms")
numpy = pytest.importorskip("numpy")

NAME = "Log Data.vi"
TDMS_REF = '<TypeDesc Type="Refnum" RefType="TDMSFile" Format="inline" />'
DBL = '<TypeDesc Type="NumFloat64" Prop1="0" Format="inline" />'
I16 = '<TypeDesc Type="NumInt16" Prop1="0" Format="inline" />'
I32_XML = '<TypeDesc Type="NumInt32" Prop1="0" Format="inline" />'
WAVEFORM = '<TypeDesc Type="MeasureData" Flavor="Float64Waveform" />'


def _array(element: int) -> str:
    return (
        '<TypeDesc Type="Array" Format="inline">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        f'<TypeDesc TypeID="{element}" /></TypeDesc>'
    )


def _chain(
    project: Project,  # noqa: F811
    *,
    operation: int,
    byte_order: int | None = None,
    element: str = DBL,
    properties: bool = True,
    write: bool = True,
    version: int | None = None,
    index_file: bool | None = None,
    name_nodes: bool = True,
    level: str = "file",
    data_type_xml: str | None = None,
    layout: int | None = None,
) -> None:
    """TDMS Open(path, operation) -> Set Properties -> Write -> Close, the error
    wire through all four; ``path``, the properties, the group, the channel and
    the data are controls."""
    vi = Vi(NAME)
    ref_type = vi.add_type(TDMS_REF)
    element_type = vi.add_type(element)
    data_type = vi.add_type(
        data_type_xml.replace("{ELEMENT}", str(element_type))
        if data_type_xml
        else _array(element_type)
    )
    names_type = vi.add_type(_array(STR))
    path = vi.control("path", PATH, REQUIRED)
    names = vi.control("names", names_type, REQUIRED)
    values = vi.control("values", names_type, REQUIRED)
    group = vi.control("group", STR, REQUIRED)
    channels = vi.control("channels", names_type, REQUIRED)
    data = vi.control("data", data_type, REQUIRED)
    channel = vi.control("channel", STR, REQUIRED) if "channel" in level else None
    error_in = vi.error_in()
    path_out = vi.indicator("path out", PATH)
    error_out = vi.error_out()

    opened = vi.primitive(
        2012,
        [
            ("file path", 11, PATH, False),
            ("operation", 10, I32, False),
            ("byte order", 9, I32, False),
            ("error in", 8, ERR, False),
            ("file format version", 7, I32, False),
            ("disable buffering", 6, BOOL, False),
            ("create index file", 5, BOOL, False),
            ("tdms file out", 3, ref_type, True),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(path, opened["file path"])
    vi.wire(vi.int_constant(operation), opened["operation"])
    if byte_order is not None:
        vi.wire(vi.int_constant(byte_order), opened["byte order"])
    if version is not None:
        vi.wire(vi.int_constant(version), opened["file format version"])
    if index_file is not None:
        vi.wire(vi.bool_constant(index_file), opened["create index file"])
    vi.wire(error_in, opened["error in"])
    ref, err = opened["tdms file out"], opened["error out"]

    if properties:
        props = vi.primitive(
            2014,
            [
                ("tdms file", 11, ref_type, False),
                ("group name", 10, STR, False),
                ("channel name", 9, STR, False),
                ("error in", 8, ERR, False),
                ("property names", 7, names_type, False),
                ("property values", 5, names_type, False),
                ("tdms file out", 3, ref_type, True),
                ("group name out", 2, STR, True),
                ("channel name out", 1, STR, True),
                ("error out", 0, ERR, True),
            ],
        )
        vi.wire(ref, props["tdms file"])
        if level not in ("file", "channel_only"):
            vi.wire(group, props["group name"])
        if channel is not None:
            vi.wire(channel, props["channel name"])
        vi.wire(names, props["property names"])
        vi.wire(values, props["property values"])
        vi.wire(err, props["error in"])
        ref, err = props["tdms file out"], props["error out"]

    if write:
        written = vi.primitive(
            2010,
            [
                ("tdms file", 0, ref_type, False),
                ("data layout", 1, I32, False),
                ("group name in", 5, STR, False),
                ("channel name(s) in", 7, names_type, False),
                ("data", 9, data_type, False),
                ("error in", 11, ERR, False),
                ("tdms file out", 4, ref_type, True),
                ("group name out", 6, STR, True),
                ("channel name(s) out", 8, names_type, True),
                ("error out", 15, ERR, True),
            ],
        )
        vi.wire(ref, written["tdms file"])
        if name_nodes:
            vi.wire(group, written["group name in"])
            vi.wire(channels, written["channel name(s) in"])
        vi.wire(data, written["data"])
        if layout is not None:
            vi.wire(vi.int_constant(layout), written["data layout"])
        vi.wire(err, written["error in"])
        ref, err = written["tdms file out"], written["error out"]

    closed = vi.primitive(
        2013,
        [
            ("tdms file", 11, ref_type, False),
            ("error in", 8, ERR, False),
            ("file path out", 3, PATH, True),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(ref, closed["tdms file"])
    vi.wire(err, closed["error in"])
    vi.wire(closed["file path out"], path_out)
    vi.wire(closed["error out"], error_out)
    project.add(vi)


def test_a_file_is_created_with_properties_and_data(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=2, element=I16)
    package = project.generate(NAME)
    assert (package.root / "requirements.txt").read_text() == "npTDMS\nnumpy\n"
    target = tmp_path / "run.tdms"
    result = package.function(
        target,
        ["Rating", "Serial"],
        ["35", "98464"],
        "UUT",
        ["Run 1"],
        [1, 2, 300],
    )
    assert (result.error_out, result.path_out) == (None, target)
    with nptdms.TdmsFile.open(target) as tdms:
        assert dict(tdms.properties) == {"Rating": "35", "Serial": "98464"}
        channel = tdms["UUT"]["Run 1"]
        assert list(channel[:]) == [1, 2, 300]
        assert channel.data_type is nptdms.types.Int16
    assert (tmp_path / "run.tdms_index").exists()


def test_the_group_and_channel_names_default_to_untitled(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=2, properties=False)
    package = project.generate(NAME)
    target = tmp_path / "plain.tdms"
    package.function(target, [], [], "G", [], [0.5, 1.5])
    with nptdms.TdmsFile.open(target) as tdms:
        assert list(tdms["G"]["Untitled"][:]) == [0.5, 1.5]
        assert tdms["G"]["Untitled"].data_type is nptdms.types.DoubleFloat


@pytest.mark.parametrize("level", ["file", "group", "channel"])
def test_properties_are_set_on_the_file_the_group_or_the_channel(
    project: Project,  # noqa: F811
    tmp_path: Path,
    level: str,
) -> None:
    """NI: both names with values: the channel; only the group name: the group;
    neither: the file."""
    _chain(project, operation=2, level=level, write=False)
    package = project.generate(NAME)
    target = tmp_path / f"{level}.tdms"
    extra = {"channel": "C"} if level == "channel" else {}
    package.function(target, ["Rating"], ["35"], "G", [], [], **extra)
    with nptdms.TdmsFile.open(target) as tdms:
        assert dict(tdms.properties) == ({"Rating": "35"} if level == "file" else {})
        if level != "file":
            assert dict(tdms["G"].properties) == (
                {"Rating": "35"} if level == "group" else {}
            )
        if level == "channel":
            assert dict(tdms["G"]["C"].properties) == {"Rating": "35"}


def test_unwired_names_are_untitled(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=2, properties=False, name_nodes=False)
    package = project.generate(NAME)
    target = tmp_path / "none.tdms"
    result = package.function(target, [], [], "ignored", ["ignored"], [7.0])
    assert result.error_out is None
    with nptdms.TdmsFile.open(target) as tdms:
        assert list(tdms["Untitled"]["Untitled"][:]) == [7.0]


def test_a_file_with_another_extension_gets_tdms_appended(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=3, properties=False, write=False)
    package = project.generate(NAME)
    result = package.function(tmp_path / "log.dat", [], [], "G", [], [])
    assert result.path_out == tmp_path / "log.dat.tdms"
    assert (tmp_path / "log.dat.tdms").exists()


def test_open_or_create_appends_to_the_file(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=1, properties=False)
    package = project.generate(NAME)
    target = tmp_path / "twice.tdms"
    for chunk in ([1.0, 2.0], [3.0]):
        assert package.function(target, [], [], "G", ["C"], chunk).error_out is None
    with nptdms.TdmsFile.open(target) as tdms:
        assert list(tdms["G"]["C"][:]) == [1.0, 2.0, 3.0]
        assert tdms.tdms_version == 4713


def test_the_file_format_version_and_the_index_file_are_the_writers(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(
        project, operation=2, properties=False, version=0, index_file=False
    )
    package = project.generate(NAME)
    target = tmp_path / "v1.tdms"
    package.function(target, [], [], "G", ["C"], [1.0])
    assert nptdms.TdmsFile.read(target).tdms_version == 4712
    assert not (tmp_path / "v1.tdms_index").exists()


def test_open_read_only_opens_the_file_and_close_closes_it(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=4, properties=False, write=False)
    package = project.generate(NAME)
    assert "nptdms.TdmsFile.open(" in package.source
    target = tmp_path / "existing.tdms"
    with nptdms.TdmsWriter(str(target)) as writer:
        writer.write_segment([nptdms.ChannelObject("G", "C", numpy.arange(3.0))])
    result = package.function(target, [], [], "G", [], [])
    assert (result.error_out, result.path_out) == (None, target)


def test_a_missing_file_is_error_7_and_close_still_runs(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=0, properties=False)
    package = project.generate(NAME)
    result = package.function(tmp_path / "missing.tdms", [], [], "G", ["C"], [1.0])
    assert (result.error_out.status, result.error_out.code) == (True, 7)
    assert result.error_out.source == "TDMS Open"
    assert result.path_out == Path("")
    assert not (tmp_path / "missing.tdms").exists()


def test_create_on_an_existing_file_is_error_10(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=3, properties=False)
    package = project.generate(NAME)
    target = tmp_path / "there.tdms"
    target.write_bytes(b"")
    result = package.function(target, [], [], "G", ["C"], [1.0])
    assert result.error_out.code == 10


@pytest.mark.parametrize("byte_order", [0, 1])
def test_a_byte_order_other_than_little_endian_is_a_gap(
    project: Project,  # noqa: F811
    tmp_path: Path,
    byte_order: int,
) -> None:
    _chain(project, operation=2, byte_order=byte_order, properties=False)
    package = project.generate(NAME)
    reason = (
        "TDMS Open with a byte order other than little-endian: npTDMS writes "
        "little-endian only"
    )
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function(tmp_path / "x.tdms", [], [], "G", ["C"], [1.0])


def test_the_default_byte_order_is_not_a_gap(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=2, byte_order=2, properties=False)
    package = project.generate(NAME)
    result = package.function(tmp_path / "le.tdms", [], [], "G", ["C"], [1.0])
    assert result.error_out is None


def test_a_byte_order_does_not_matter_when_no_file_is_created(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=4, byte_order=0, properties=False, write=False)
    package = project.generate(NAME)
    target = tmp_path / "ro.tdms"
    with nptdms.TdmsWriter(str(target)) as writer:
        writer.write_segment([nptdms.ChannelObject("G", "C", numpy.arange(2.0))])
    assert package.function(target, [], [], "G", [], []).error_out is None


def test_a_channel_name_without_a_group_name_is_a_gap(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=2, level="channel_only", write=False)
    package = project.generate(NAME)
    reason = "TDMS Set Properties with a channel name and no group name"
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=reason):
        package.function(tmp_path / "x.tdms", [], [], "G", [], [], channel="C")


@pytest.mark.parametrize(
    ("data_type", "reason"),
    [
        (
            '<TypeDesc Type="NumInt32" Prop1="0" Format="inline" />',
            "TDMS Write of .* is not converted: only a 1D or 2D array is",
        ),
        (
            '<TypeDesc Type="MeasureData" Flavor="DigitalWaveform" />',
            "TDMS Write of a DigitalWaveform is not converted: NI's TDMS Write "
            "page accepts it but does not document how its lines are stored",
        ),
        (
            '<TypeDesc Type="MeasureData" Flavor="Complex128Waveform" />',
            "TDMS Write of a Complex128Waveform is not converted: NI's TDMS "
            "Write page lists complex numbers only as arrays",
        ),
        (
            '<TypeDesc Type="MeasureData" Flavor="Dynamicdata" />',
            "A Dynamicdata is not converted",
        ),
    ],
)
def test_the_data_that_has_no_channel_is_a_gap(
    project: Project,  # noqa: F811
    tmp_path: Path,
    data_type: str | None,
    reason: str,
) -> None:
    _chain(
        project,
        operation=2,
        properties=False,
        element=I32_XML,
        data_type_xml=data_type.replace("{element}", "{ELEMENT}"),
    )
    package = project.generate(NAME)
    assert re.search(reason, package.source)
    with pytest.raises(NotImplementedError, match=reason):
        package.function(tmp_path / "x.tdms", [], [], "G", ["C"], [])


def _waveform(samples: list[float], t0: datetime, dt: float, name: str | None = None):
    import nitypes.bintime
    import nitypes.time
    import nitypes.waveform

    timing = nitypes.waveform.Timing.create_with_regular_interval(
        nitypes.bintime.TimeDelta(dt),
        nitypes.time.convert_datetime(nitypes.bintime.DateTime, t0),
    )
    wave = nitypes.waveform.AnalogWaveform.from_array_1d(
        samples, numpy.float64, timing=timing
    )
    if name is not None:
        wave.extended_properties["NI_ChannelName"] = name
    return wave


START = datetime(2026, 11, 15, 8, 30, 0, tzinfo=UTC)


def test_a_waveform_is_a_channel_with_its_timing_properties(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    pytest.importorskip("nitypes")
    _chain(
        project,
        operation=1,
        properties=False,
        element=WAVEFORM,
        data_type_xml=WAVEFORM,
    )
    package = project.generate(NAME)
    assert "NotImplementedError" not in package.source
    target = tmp_path / "wave.tdms"
    first = _waveform([1.0, 2.0, 3.0], START, 0.5, "Volts")
    package.function(target, [], [], "G", [], first)
    second = _waveform([4.0, 5.0], START + timedelta(seconds=99), 0.25, "Volts")
    result = package.function(target, [], [], "G", [], second)
    assert result.error_out is None
    with nptdms.TdmsFile.open(target) as tdms:
        channel = tdms["G"]["Volts"]
        assert list(channel[:]) == [1.0, 2.0, 3.0, 4.0, 5.0]
        assert channel.properties["wf_increment"] == 0.5
        assert channel.properties["wf_start_offset"] == 0.0
        assert channel.properties["wf_samples"] == 3
        assert channel.properties["NI_ChannelName"] == "Volts"
        start = channel.properties["wf_start_time"]
        assert numpy.datetime64(start) == numpy.datetime64("2026-11-15T08:30:00")


def test_a_waveform_with_no_name_is_untitled_and_a_wired_name_wins(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    pytest.importorskip("nitypes")
    _chain(
        project,
        operation=1,
        properties=False,
        element=WAVEFORM,
        data_type_xml=WAVEFORM,
    )
    package = project.generate(NAME)
    target = tmp_path / "wave.tdms"
    package.function(target, [], [], "G", [], _waveform([1.0], START, 1.0))
    package.function(target, [], [], "G", ["Named"], _waveform([2.0], START, 1.0, "X"))
    with nptdms.TdmsFile.open(target) as tdms:
        assert [c.name for c in tdms["G"].channels()] == ["Untitled", "Named"]


def test_an_array_of_waveforms_is_a_channel_each(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    pytest.importorskip("nitypes")
    _chain(
        project,
        operation=1,
        properties=False,
        element=WAVEFORM,
    )
    package = project.generate(NAME)
    target = tmp_path / "waves.tdms"
    waves = [_waveform([1.0], START, 1.0), _waveform([2.0, 3.0], START, 2.0, "B")]
    package.function(target, [], [], "G", ["A"], waves)
    with nptdms.TdmsFile.open(target) as tdms:
        assert [c.name for c in tdms["G"].channels()] == ["A", "B"]
        assert tdms["G"]["B"].properties["wf_increment"] == 2.0
        assert list(tdms["G"]["B"][:]) == [2.0, 3.0]


def _array_2d(element: int | str) -> str:
    return (
        '<TypeDesc Type="Array" Format="inline">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        f'<TypeDesc TypeID="{element}" /></TypeDesc>'
    )


def test_a_decimated_2d_array_is_a_channel_per_row(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(
        project,
        operation=1,
        properties=False,
        element=DBL,
        data_type_xml=_array_2d("{ELEMENT}"),
    )
    package = project.generate(NAME)
    assert "NotImplementedError" not in package.source
    target = tmp_path / "rows.tdms"
    result = package.function(
        target, [], [], "G", ["A", "B"], [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    )
    assert result.error_out is None
    with nptdms.TdmsFile.open(target) as tdms:
        assert [c.name for c in tdms["G"].channels()] == ["A", "B"]
        assert list(tdms["G"]["A"][:]) == [1.0, 2.0, 3.0]
        assert list(tdms["G"]["B"][:]) == [4.0, 5.0, 6.0]


def test_an_interleaved_2d_array_is_a_channel_per_column(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(
        project,
        operation=1,
        properties=False,
        element=DBL,
        data_type_xml=_array_2d("{ELEMENT}"),
        layout=1,
    )
    package = project.generate(NAME)
    target = tmp_path / "columns.tdms"
    package.function(target, [], [], "G", ["A"], [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    with nptdms.TdmsFile.open(target) as tdms:
        assert [c.name for c in tdms["G"].channels()] == [
            "A",
            "Untitled1",
            "Untitled2",
        ]
        assert list(tdms["G"]["A"][:]) == [1.0, 4.0]
        assert list(tdms["G"]["Untitled2"][:]) == [3.0, 6.0]


def test_a_1d_array_with_an_interleaved_layout_is_a_run_time_gap(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=1, properties=False, element=DBL, layout=1)
    package = project.generate(NAME)
    with pytest.raises(NotImplementedError, match="interleaved data layout"):
        package.function(tmp_path / "x.tdms", [], [], "G", [], [1.0, 2.0])


def test_a_1d_array_with_a_decimated_layout_is_one_channel(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(project, operation=1, properties=False, element=DBL, layout=0)
    package = project.generate(NAME)
    target = tmp_path / "one.tdms"
    package.function(target, [], [], "G", [], [1.0, 2.0])
    with nptdms.TdmsFile.open(target) as tdms:
        assert list(tdms["G"]["Untitled"][:]) == [1.0, 2.0]


def test_an_array_of_timestamps_is_a_timestamp_channel(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    hightime = pytest.importorskip("hightime")
    _chain(
        project,
        operation=1,
        properties=False,
        element='<TypeDesc Type="MeasureData" Flavor="TimeStamp" />',
    )
    package = project.generate(NAME)
    assert "NotImplementedError" not in package.source
    target = tmp_path / "stamps.tdms"
    stamps = [
        hightime.datetime(2026, 11, 15, 8, 30, 0, tzinfo=UTC),
        hightime.datetime(2026, 11, 15, 8, 30, 1, 500000, tzinfo=UTC),
    ]
    package.function(target, [], [], "G", ["T"], stamps)
    with nptdms.TdmsFile.open(target) as tdms:
        channel = tdms["G"]["T"]
        assert channel.data_type is nptdms.types.TimeStamp
        assert list(channel[:]) == [
            numpy.datetime64("2026-11-15T08:30:00.000000"),
            numpy.datetime64("2026-11-15T08:30:01.500000"),
        ]


def test_a_2d_array_of_timestamps_is_a_timestamp_channel_per_row(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    hightime = pytest.importorskip("hightime")
    _chain(
        project,
        operation=1,
        properties=False,
        element='<TypeDesc Type="MeasureData" Flavor="TimeStamp" />',
        data_type_xml=_array_2d("{ELEMENT}"),
    )
    package = project.generate(NAME)
    target = tmp_path / "stamps2.tdms"
    t = hightime.datetime(2026, 11, 15, 8, 30, 0, tzinfo=UTC)
    package.function(target, [], [], "G", ["A", "B"], [[t], [t]])
    with nptdms.TdmsFile.open(target) as tdms:
        assert [c.name for c in tdms["G"].channels()] == ["A", "B"]
        assert tdms["G"]["B"].data_type is nptdms.types.TimeStamp


def test_an_array_of_complex_numbers_is_a_complex_channel(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    _chain(
        project,
        operation=1,
        properties=False,
        element='<TypeDesc Type="NumComplex128" Prop1="0" Format="inline" />',
    )
    package = project.generate(NAME)
    target = tmp_path / "complex.tdms"
    package.function(target, [], [], "G", ["Z"], [1 + 2j, 3 - 4j])
    with nptdms.TdmsFile.open(target) as tdms:
        channel = tdms["G"]["Z"]
        assert channel.data_type is nptdms.types.ComplexDoubleFloat
        assert list(channel[:]) == [1 + 2j, 3 - 4j]


def test_a_3d_array_is_a_gap(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    three_d = (
        '<TypeDesc Type="Array" Format="inline">'
        + '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />' * 3
        + '<TypeDesc TypeID="{ELEMENT}" /></TypeDesc>'
    )
    _chain(project, operation=1, properties=False, element=DBL, data_type_xml=three_d)
    package = project.generate(NAME)
    assert "TDMS Write of a 3D array is not converted" in package.source
