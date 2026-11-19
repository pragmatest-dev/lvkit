"""A LabVIEW waveform as a value of NI's ``nitypes`` package.

An analog or complex waveform in generated Python is an ``AnalogWaveform`` or a
``ComplexWaveform`` (the package nidaqmx's own waveforms are): ``t0`` and ``dt``
are its regular timing, ``Y`` its samples and ``attributes`` its extended
properties. The class and sample dtype of each flavor come from
``data/measure_data.json``; a flavor with no ``nitypes`` form (an extended-precision
sample) is a gap with the reason the table gives. Digital data and digital
waveforms are in ``digital_values``.

The components read out as the values LabVIEW's own wires carry: ``t0`` a
``hightime.datetime`` (UTC, converted from NI's 1904-epoch 64.64 bintime
without rounding), ``dt`` the float seconds, ``Y`` a list, ``attributes`` a
``Variant`` of the named attributes. A waveform with no timestamp or interval
reads the default of its type.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.measure_data import NitypesForm, measure_data_flavor
from lvkit.models import LVType, LVTypeKind
from lvkit.timestamp_value import EPOCH_1904_EXPRESSION

from .variants_module import lv_type_expression

# The imports the expressions below name.
IMPORTS = frozenset(
    {
        "import nitypes.bintime",
        "import nitypes.time",
        "import nitypes.waveform",
        "import numpy",
    }
)

# The components a waveform's Bundle/Unbundle By Name terminal selects, as the
# Python names ``nmux._measure_component`` gives them.
T0 = "t0"
DT = "dt"
Y = "y"
ATTRIBUTES = "attributes"


def waveform_form(flavor: str, node: PrimitiveNode | None = None) -> NitypesForm:
    """The ``nitypes`` class and dtype a waveform of ``flavor`` is held in; a gap
    naming the reason when it has none."""
    entry = measure_data_flavor(flavor)
    if entry is not None and entry.nitypes is not None:
        return entry.nitypes
    reason = entry.nitypes_gap if entry is not None else None
    raise ConversionGap(
        f"A {flavor} is not converted: "
        f"{reason or 'lvkit has no nitypes form for this type'}",
        node,
    )


def read_expression(waveform: str, component: str) -> str:
    """The value of ``component`` (``t0``, ``dt`` or ``y``) of the waveform
    variable ``waveform``. A waveform with no start time or no regular interval
    has the default of the component's type."""
    if component == T0:
        return (
            f"(nitypes.time.convert_datetime(hightime.datetime, "
            f"{waveform}.timing.start_time) if {waveform}.timing.has_start_time "
            f"else {EPOCH_1904_EXPRESSION})"
        )
    if component == DT:
        return (
            f"({waveform}.timing.sample_interval.total_seconds() "
            f"if {waveform}.timing.has_sample_interval else 0.0)"
        )
    if component == Y:
        return f"{waveform}.raw_data.tolist()"
    raise AssertionError(f"not a waveform component: {component!r}")


def attributes_read_source(waveform: str, name: str) -> tuple[str, tuple[str, ...]]:
    """The statements that bind ``name`` to the ``Variant`` whose attributes are
    the extended properties of the waveform variable ``waveform``, and the names
    they take from the ``variants`` module. nitypes keeps no LabVIEW type for a
    property, so a Boolean, a double and a string read as those; an integer is
    the one value whose LabVIEW integer type is unknown, so it raises."""
    kinds = [
        lv_type_expression(LVType(LVTypeKind.PRIMITIVE, underlying_type=t))
        for t in ("Boolean", "NumFloat64", "String", "NumInt64")
    ]
    boolean, double, string, integer = (kind.text for kind in kinds)
    source = (
        f"{name} = Variant()\n"
        f"for _key, _value in {waveform}.extended_properties.items():\n"
        f"    if isinstance(_value, bool):\n"
        f"        _kind = {boolean}\n"
        f"    elif isinstance(_value, float):\n"
        f"        _kind = {double}\n"
        f"    elif isinstance(_value, str):\n"
        f"        _kind = {string}\n"
        f"    elif isinstance(_value, int):\n"
        f"        _kind = {integer}\n"
        f"    else:\n"
        f"        raise NotImplementedError(\n"
        f"            f'The waveform attribute {{_key!r}} holds {{_value!r}}: '\n"
        f"            'nitypes keeps only a Boolean, a number or a string')\n"
        f"    {name}.attributes[_key] = Variant(_value, _kind)\n"
    )
    return source, tuple(n for kind in kinds for n in kind.names)


def attributes_write_source(variant: str, name: str) -> str:
    """The statements that bind ``name`` to the dict of extended properties from
    the attributes of the ``Variant`` ``variant``. nitypes holds only a Boolean,
    a number or a string for a property, so an attribute of another type
    raises."""
    return (
        f"{name} = {{}}\n"
        f"for _key, _attribute in {variant}.attributes.items():\n"
        f"    if not isinstance(_attribute.value, (bool, int, float, str)):\n"
        f"        raise NotImplementedError(\n"
        f"            f'The waveform attribute {{_key!r}} holds '\n"
        f"            f'{{type(_attribute.value).__name__}}: nitypes keeps only a '\n"
        f"            'Boolean, a number or a string for an attribute')\n"
        f"    {name}[_key] = _attribute.value\n"
    )


def build_expression(
    form: NitypesForm,
    y: str,
    t0: str,
    dt: str,
    attributes: str | None,
) -> str:
    """A new waveform of ``form`` from the expressions of its samples, start
    time (a ``hightime.datetime``), interval in seconds and, when it has any,
    its extended properties (a dict)."""
    timing = (
        "nitypes.waveform.Timing.create_with_regular_interval("
        f"nitypes.bintime.TimeDelta({dt}), "
        f"nitypes.time.convert_datetime(nitypes.bintime.DateTime, {t0}))"
    )
    extended = f", extended_properties={attributes}" if attributes else ""
    return (
        f"nitypes.waveform.{form.class_name}.from_array_1d("
        f"{y}, numpy.{form.dtype}, timing={timing}{extended})"
    )


def default_components() -> dict[str, str]:
    """The expression of each component a new waveform takes when it is not
    wired: no samples, the 1904 epoch and a zero interval."""
    return {Y: "[]", T0: EPOCH_1904_EXPRESSION, DT: "0.0"}
