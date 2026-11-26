"""Merge Signals (node class ``mergeSignal``).

NI, "Merge Signals" (``functions/merge-signals.html``): it "merges two or more
supported signals, such as scalar numerics, 1D or 2D arrays of numerics, scalar
Booleans, 1D or 2D arrays of Booleans, waveforms, or 1D arrays of waveforms, into
a single output". "This function outputs a single wire with a dynamic data type.
Internally, an array of waveforms composes this wire. When you merge signals, each
input signal becomes one or more elements in the waveform array." So the output is
the list of waveforms: the concatenation, in input order, of the waveforms each
input becomes. The same list is the value of every dynamic data wire
(``list[nitypes.waveform.AnalogWaveform]``), which is how a dynamic data input
(an Express VI's output, another Merge Signals) is merged: its waveforms, as they
are.

What each input kind becomes:

* a waveform: itself;
* a 1D array of waveforms, or dynamic data: its waveforms;
* a 1D array of numerics: one ``AnalogWaveform`` of those samples, as doubles (NI,
  "Using the Dynamic Data Type": "the scalar data type is a floating-point number
  or a Boolean value");
* a scalar numeric: a one-sample ``AnalogWaveform``. NI does not say what timing a
  scalar gets; lvkit gives one sample with default timing (the maintainer's
  recorded choice). The arrays get default timing the same way.

Gaps, each with the reason: a 2D array (NI's same page lists "2D array of scalars
-- columns are channels" and "2D array of scalars -- rows are channels" and Merge
Signals' page does not say which one its input is), a Boolean or array of Booleans
(NI lists them as supported but does not say what sample a Boolean becomes), a
complex number, a digital or complex waveform (the dynamic data type holds analog
waveforms), a 2D array of waveforms (NI lists 1D arrays) and an input of unknown
type. An unwired input adds nothing.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import (
    BOOLEAN_TYPE_NAMES,
    COMPLEX_TYPE_NAMES,
    INTEGER_TYPE_NAMES,
    REAL_FLOAT_TYPE_NAMES,
)
from lvkit.models import LVType, LVTypeKind, Terminal

from ...ast_utils import parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...output_binding import bind_output
from ...waveform_values import waveform_form
from . import register_op_fragment

ANALOG = "AnalogWaveform"
_ANALOG_WAVEFORM = "nitypes.waveform.AnalogWaveform"
IMPORTS = ("import nitypes.waveform", "import numpy")


_BOOLEAN = "NI lists Booleans as supported but does not say what sample a Boolean is"
_COMPLEX = (
    "NI's dynamic data page names a floating-point number or a Boolean as the "
    "scalar, not a complex number"
)


def _numeric(name: str | None) -> bool:
    return name is not None and (
        name in REAL_FLOAT_TYPE_NAMES or name in INTEGER_TYPE_NAMES
    )


def _describe(lv_type: LVType | None) -> str:
    if lv_type is None:
        return "an input of unknown type"
    if lv_type.kind == LVTypeKind.ARRAY:
        return f"{lv_type.dimensions or 1}D array of {_describe(lv_type.element_type)}"
    if lv_type.underlying_type == "MeasureData":
        return lv_type.measure_flavor or "measure data"
    return lv_type.underlying_type or lv_type.kind.value


def _gap(node: PrimitiveNode, lv_type: LVType | None, why: str) -> ConversionGap:
    return ConversionGap(
        f"Merge Signals of {_describe(lv_type)} is not converted: {why}", node
    )


def _samples(array: str) -> str:
    return f"{_ANALOG_WAVEFORM}.from_array_1d({array}, numpy.float64)"


def _waveforms(node: PrimitiveNode, lv_type: LVType | None, value: str) -> str:
    """The Python item the input ``value`` of type ``lv_type`` adds to the list
    of waveforms: ``name`` for one waveform, ``*name`` for several."""
    if lv_type is None:
        raise _gap(node, None, "its type is not known")
    if lv_type.kind == LVTypeKind.ARRAY:
        element = lv_type.element_type
        name = element.underlying_type if element is not None else None
        if (lv_type.dimensions or 1) != 1:
            why = (
                "NI lists 1D arrays of waveforms"
                if name == "MeasureData"
                else "NI's dynamic data page lists a 2D array of scalars both with "
                "columns as channels and with rows as channels, and Merge Signals' "
                "page does not say which its input is"
            )
            raise _gap(node, lv_type, why)
        if element is not None and name == "MeasureData":
            _check_analog(node, lv_type, element)
            return f"*list({value})"
        if name in BOOLEAN_TYPE_NAMES:
            raise _gap(node, lv_type, _BOOLEAN)
        if name in COMPLEX_TYPE_NAMES:
            raise _gap(node, lv_type, _COMPLEX)
        if _numeric(name):
            return _samples(value)
        raise _gap(node, lv_type, "only numbers and waveforms are merged")
    name = lv_type.underlying_type
    if name == "MeasureData":
        if lv_type.is_dynamic_data:
            return f"*list({value})"
        _check_analog(node, lv_type, lv_type)
        return value
    if name in BOOLEAN_TYPE_NAMES:
        raise _gap(node, lv_type, _BOOLEAN)
    if name in COMPLEX_TYPE_NAMES:
        raise _gap(node, lv_type, _COMPLEX)
    if _numeric(name):
        return _samples(f"[{value}]")
    raise _gap(node, lv_type, "only numbers and waveforms are merged")


def _check_analog(node: PrimitiveNode, shown: LVType, waveform: LVType) -> None:
    """A gap unless ``waveform`` is an analog waveform (the dynamic data type
    holds analog waveforms)."""
    if waveform.is_timestamp:
        raise _gap(node, shown, "only numbers and waveforms are merged")
    form = waveform_form(waveform.measure_flavor or "waveform", node)
    if form.class_name != ANALOG:
        raise _gap(node, shown, "the dynamic data type holds analog waveforms")


def _signal_type(term: Terminal, ctx: CodeGenContext) -> LVType | None:
    """The type of the signal wired to the input ``term``: the type of the
    terminal the wire starts at, since every input of the node is typed as dynamic
    data whatever is wired to it. None when that type is not known."""
    if ctx.graph is None:
        return None
    for _, end in ctx.graph.wire_origins(term.id):
        source = ctx.graph.get_terminal(end.terminal_id)
        if source is not None and source.lv_type is not None:
            return source.lv_type
    return None


@register_op_fragment("MERGE_SIGNALS")
def merge_signals(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    output = next(t for t in node.terminals if t.direction == "output")
    inputs = sorted(
        (t for t in node.terminals if t.direction == "input"),
        key=lambda t: t.index if t.index is not None else -1,
    )
    if not ctx.is_wired(output.id):
        return CodeFragment.empty()
    items = []
    for term in inputs:
        value = ctx.wired_input(term.id, node)
        if value is not None:
            items.append(_waveforms(node, _signal_type(term, ctx), value))
    for statement in IMPORTS:
        ctx.add_import(statement)
    expression = parse_expr(f"[{', '.join(items)}]")
    return bind_output(node, output.id, "combined_signal", expression, ctx)
