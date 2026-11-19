"""TDMS Write of an analog waveform, or a 1D array of them, one channel each.

NI, "TDMS Write Function" (``functions/tdms-write.html``): the data input accepts
"Analog waveform or a 1D array of analog waveforms"; "If you wire waveform data to
the data input, LabVIEW uses waveform names" for the channel names; "When you
write analog or digital waveforms to a new channel, LabVIEW includes the
timestamp information in the waveforms. However, when you append analog or
digital waveforms to an existing channel, LabVIEW might discard the timestamp
information in the new waveforms", so the timing is written once, when the
channel is new. Unwired names are "Untitled", "Untitled1", "Untitled2"...

NI, "TDMS File Format Internal Structure"
(https://www.ni.com/en/support/documentation/supplemental/07/tdms-file-format-internal-structure.html)
and "Writing Data-Management-Ready TDMS Files"
(https://www.ni.com/en/support/documentation/supplemental/12/writing-data-management-ready-tdms-files.html):
a waveform is a numeric channel whose properties are ``wf_start_time`` (the
waveform's start, optional), ``wf_start_offset`` (0), ``wf_increment`` (dt in
seconds) and ``wf_samples`` (the sample count); ``wf_xname`` and
``wf_xunit_string`` "are not set by default in NI LabVIEW". NI's Knowledge Base
and forums (https://knowledge.ni.com/KnowledgeArticleDetails?id=kA03q000001DoihCAC,
https://forums.ni.com/t5/LabVIEW/Log-an-arbitrary-waveform-to-TDMS/td-p/2717791):
the waveform's attributes are added to the channel as properties, and
``NI_ChannelName`` is the waveform's name.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind, Terminal

from ...waveform_values import waveform_form

NAME_ATTRIBUTE = "NI_ChannelName"
ANALOG = "AnalogWaveform"
IMPORTS = ("import datetime", "import nitypes.time")
# Why the waveforms of the other nitypes classes have no channel. NI's TDMS Write
# page lists "Digital waveform" as data but not how its lines are stored (web
# searches of forums.ni.com and NI's TDMS file-format pages found no layout: a
# forum thread, https://forums.ni.com/t5/LabVIEW/TDMS-digital-data/td-p/2822016,
# reports plain TDMS nodes keep only one byte per value, with no line count), and
# lists complex numbers only as arrays, never a complex waveform.
_UNWRITTEN = {
    "DigitalWaveform": (
        "NI's TDMS Write page accepts it but does not document how its lines are "
        "stored in the file"
    ),
    "ComplexWaveform": (
        "NI's TDMS Write page lists complex numbers only as arrays, not a "
        "complex waveform"
    ),
}


def waveform_data(node: PrimitiveNode, term: Terminal | None) -> bool | None:
    """Whether the data of ``term`` is a 1D array of analog waveforms (False: one
    waveform); None when it is not a waveform. A waveform of another kind is a
    gap with the reason."""
    lv_type = term.lv_type if term is not None else None
    if lv_type is None:
        return None
    is_array = lv_type.kind == LVTypeKind.ARRAY
    element = lv_type.element_type if is_array else lv_type
    if element is None or element.underlying_type != "MeasureData":
        return None
    if element.is_timestamp:
        return None
    flavor = element.measure_flavor or "waveform"
    form = waveform_form(flavor, node)
    if form.class_name != ANALOG:
        why = _UNWRITTEN.get(form.class_name, "only an analog waveform is")
        raise ConversionGap(f"TDMS Write of a {flavor} is not converted: {why}", node)
    if is_array and (lv_type.dimensions or 1) != 1:
        raise ConversionGap(
            "TDMS Write of a 2D array of waveforms is not converted: NI's TDMS "
            "Write page accepts an analog waveform or a 1D array of them",
            node,
        )
    return is_array


def waveform_write_source(
    ref: str,
    group: str,
    data: str,
    is_array: bool,
    names: str | None,
    names_is_array: bool,
    channels: str,
    channel: str,
) -> str:
    """The statements that write each waveform of ``data`` to its own channel and
    bind ``channels`` to the list of the channel names written. ``names`` is the
    expression of the wired channel names (None when unwired)."""
    if names is None:
        given = "[]"
    elif names_is_array:
        given = f"list({names})"
    else:
        given = f"[{names}]"
    waveforms = f"list({data})" if is_array else f"[{data}]"
    return (
        f"{channels} = []\n"
        f"_given_names = {given}\n"
        f"for _index, _wave in enumerate({waveforms}):\n"
        f"    {channel} = (\n"
        f"        _given_names[_index] if _index < len(_given_names) else None\n"
        f"    ) or _wave.extended_properties.get('{NAME_ATTRIBUTE}') or (\n"
        f"        'Untitled' if _index == 0 else f'Untitled{{_index}}'\n"
        f"    )\n"
        f"    _properties = {{}}\n"
        f"    if ({group}, {channel}) not in {ref}[2]:\n"
        f"        {ref}[2].add(({group}, {channel}))\n"
        f"        _properties = dict(_wave.extended_properties)\n"
        f"        _properties['wf_start_offset'] = 0.0\n"
        f"        _properties['wf_increment'] = (\n"
        f"            _wave.timing.sample_interval.total_seconds()\n"
        f"            if _wave.timing.has_sample_interval else 0.0\n"
        f"        )\n"
        f"        _properties['wf_samples'] = len(_wave.raw_data)\n"
        f"        if _wave.timing.has_start_time:\n"
        f"            _properties['wf_start_time'] = nitypes.time.convert_datetime(\n"
        f"                datetime.datetime, _wave.timing.start_time\n"
        f"            )\n"
        f"    {ref}[0].write_segment(\n"
        f"        [nptdms.ChannelObject({group}, {channel}, _wave.raw_data, "
        f"_properties)]\n"
        f"    )\n"
        f"    {channels}.append({channel})\n"
    )
