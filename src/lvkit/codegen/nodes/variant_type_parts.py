"""The type-introspection VIs of ``vi.lib/Utility/Data Type/`` (NI_Data Type.lvlib,
Data Type Parsing palette) that read the parts of the type a variant records:
Get Array Information, Get Cluster Information and Get Numeric Information.

NI, "Get Array Information" / "Get Cluster Information" / "Get Numeric Information"
(labview-api-ref, vi-lib/utility/data-type): each "returns an error if variant does
not contain" an array / a cluster / a numeric; the type outputs are the data types
of the elements ("array element data type", "cluster elements"), the dimensions
("number of dimensions", "array lengths": Type Variable, Fixed or Bounded and Size,
which is 0 for a Variable dimension), the enum's items ("enum names"), the units and
the numeric ``representation``. The generated code reads them from the variant's
type with ``variants.read_type`` (``type_reader.py``) when it runs; a data type
output is a ``Variant`` of that type holding its default, the type alone.

The code of the error the wrong kind of variant returns is not on NI's page or in
the forum threads searched (forums.ni.com "Get Array Information vi decoding the
output", "Possible bug NI_Data Type.lvlib Get Cluster Information.vi"); the catalog
entries leave it the generic error code, an unsettled default. A number with a
unit, a fixed-point number and the Bounded dimension kind are not read
(``NotImplementedError`` where the node runs). A refnum, a waveform or a LabVIEW
class is not recorded in any type string lvkit writes (the type of a refnum or
of a waveform is its code alone), so Get Refnum Information and Get Waveform
Information are gaps (``gap_refnum``, ``gap_waveform``).
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.lv_type_codes import TD_FULL_TYPE
from lvkit.models import LVType, LVTypeKind, Terminal, TerminalDirection
from lvkit.variant_type_items import representation_values
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import field_attribute_names
from ..cluster_members import is_anonymous_cluster
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .grpc_pane import terminal_of, wired

_UNIT_CODES = tuple(
    int(TD_FULL_TYPE[name].value)
    for name in (
        "UnitFloat32",
        "UnitFloat64",
        "UnitFloatExt",
        "UnitComplex64",
        "UnitComplex128",
        "UnitComplexExt",
    )
)


def _output(
    node: VINode, entry: VIEntry, param: str, ctx: CodeGenContext
) -> Terminal | None:
    """The call's output ``param`` when it is wired, else None."""
    term = terminal_of(node, entry, param, TerminalDirection.OUTPUT)
    return term if term is not None and ctx.is_wired(term.id) else None


def _reading(
    node: VINode,
    entry: VIEntry,
    ctx: CodeGenContext,
    kinds: tuple[str, ...],
    noun: str,
    unread_codes: tuple[int, ...] = (),
) -> tuple[str, str]:
    """The variable of the type information and the statements that read the
    variant's type into it and refuse a variant that is not ``kinds``; a type with
    one of ``unread_codes`` is not read (``NotImplementedError``)."""
    variant = wired(node, entry, "variant", ctx)
    if variant is None:
        raise ConversionGap("no variant is wired to it", node)
    ctx.import_variant("read_type", "type_default")
    recorded = ctx.make_output_var("variant_type", node.id)
    info = ctx.make_output_var("type_info", node.id)
    shown = f"{recorded}.name if {recorded} is not None else 'an empty variant'"
    source = f"{recorded} = {variant}.type\n"
    if unread_codes:
        source += (
            f"if {recorded} is not None and {recorded}.code in {unread_codes!r}:\n"
            "    raise NotImplementedError(\n"
            f"        f'{entry.name}: {{{recorded}.name}} is a number with a unit, "
            "which is not read'\n"
            "    )\n"
        )
    source += (
        f"{info} = read_type({recorded})\n"
        f"if {info}.kind not in {kinds!r}:\n"
        f"    raise ValueError(f'{entry.name}: {{{shown}}} is not {noun}')\n"
    )
    return info, source


def _data_type(info_type: str) -> str:
    """A variant of the type ``info_type`` (an ``LVType`` expression) holding its
    default."""
    return f"Variant(type_default({info_type}), {info_type})"


def _finish(source: str, bindings: dict[str, str]) -> CodeFragment:
    return CodeFragment(statements=ast.parse(source).body, bindings=bindings)


def array_information(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """One call of ``Get Array Information.vi``."""
    info, source = _reading(node, entry, ctx, ("array",), "an array")
    bindings: dict[str, str] = {}
    element = _output(node, entry, "array_element_data_type", ctx)
    if element is not None:
        var = ctx.make_output_var("array_element_data_type", node.id)
        source += f"{var} = {_data_type(f'{info}.element')}\n"
        bindings[element.id] = var
    count = _output(node, entry, "number_of_dimensions", ctx)
    if count is not None:
        var = ctx.make_output_var("number_of_dimensions", node.id)
        source += f"{var} = len({info}.dimensions)\n"
        bindings[count.id] = var
    lengths = _output(node, entry, "array_lengths", ctx)
    if lengths is not None:
        var = ctx.make_output_var("array_lengths", node.id)
        row = _length_row(lengths.lv_type, node)
        source += f"{var} = [{row} for variable, size in {info}.dimensions]\n"
        bindings[lengths.id] = var
    return _finish(source, bindings)


def _length_row(lv_type: LVType | None, node: VINode) -> str:
    """The expression of one ``Array Lengths`` element (Type, Size) for a
    dimension ``(variable, size)``, in the form its cluster has."""
    row = lv_type.element_type if lv_type is not None else None
    if (
        row is None
        or row.kind != LVTypeKind.CLUSTER
        or not row.fields
        or len(row.fields) != 2
        or row.fields[0].type is None
        or row.fields[0].type.values is None
    ):
        raise ConversionGap(
            "the Array Lengths output is not recorded as a cluster of Type "
            "(an enum) and Size",
            node,
        )
    labels = row.fields[0].type.values
    if "Variable" not in labels or "Fixed" not in labels:
        raise ConversionGap(
            "the Type of the Array Lengths output has no Variable and Fixed items",
            node,
        )
    variable, fixed = labels["Variable"].value, labels["Fixed"].value
    kind = f"({variable} if variable else {fixed})"
    if is_anonymous_cluster(row):
        return f"({kind}, size)"
    names = field_attribute_names(f.name for f in row.fields)
    return f"SimpleNamespace({names[0]}={kind}, {names[1]}=size)"


def cluster_information(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """One call of ``Get Cluster Information.vi``."""
    info, source = _reading(node, entry, ctx, ("cluster",), "a cluster")
    bindings: dict[str, str] = {}
    elements = _output(node, entry, "cluster_elements", ctx)
    if elements is not None:
        var = ctx.make_output_var("cluster_elements", node.id)
        source += f"{var} = [{_data_type('member')} for member in {info}.fields]\n"
        bindings[elements.id] = var
    return _finish(source, bindings)


def numeric_information(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """One call of ``Get Numeric Information.vi``."""
    info, source = _reading(
        node, entry, ctx, ("number", "enum"), "a numeric", _UNIT_CODES
    )
    bindings: dict[str, str] = {}
    names = _output(node, entry, "enum_names", ctx)
    if names is not None:
        var = ctx.make_output_var("enum_names", node.id)
        source += f"{var} = list({info}.items)\n"
        bindings[names.id] = var
    units = _output(node, entry, "units", ctx)
    representation = _output(node, entry, "representation", ctx)
    if units is not None:
        var = ctx.make_output_var("units", node.id)
        source += f"{var} = []\n"
        bindings[units.id] = var
    if representation is not None:
        table = (
            representation_values(representation.lv_type)
            if representation.lv_type is not None
            else None
        )
        if table is None:
            raise ConversionGap(
                "the Representation output is not recorded as the items of "
                "the numeric representation enum",
                node,
            )
        var = ctx.make_output_var("representation", node.id)
        source += (
            f"{var} = {table!r}.get({info}.number)\n"
            f"if {var} is None:\n"
            f"    raise NotImplementedError(\n"
            f"        f'{entry.name}: the number type {{{info}.number}} has no "
            "representation item'\n"
            f"    )\n"
        )
        bindings[representation.id] = var
    return _finish(source, bindings)


def gap_refnum(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """Get Refnum Information: a refnum's kind is in no type string lvkit writes
    (a refnum is recorded by its code alone), so the call is a gap."""
    raise ConversionGap(
        "a variant of a refnum records only the refnum's code, not its kind or "
        "strict type, so Get Refnum Information cannot read them",
        node,
    )


def gap_waveform(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """Get Waveform Information: a waveform is in no type string lvkit writes (it
    is recorded by its code alone), so the call is a gap."""
    raise ConversionGap(
        "a variant of a waveform records only the waveform's code, not the type of "
        "its Y array, so Get Waveform Information cannot read it",
        node,
    )
