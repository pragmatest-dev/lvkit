"""Bundle and Unbundle By Name on a waveform: its components are read from, and a
new value is built as, a ``nitypes`` waveform (``codegen/waveform_values.py``)."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.measure_data import measure_data_field_name
from lvkit.models import Terminal

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..waveform_values import (
    ATTRIBUTES,
    DT,
    IMPORTS,
    T0,
    Y,
    attributes_read_source,
    attributes_write_source,
    build_expression,
    default_components,
    read_expression,
    waveform_form,
)


def measure_component(term: Terminal, flavor: str, node: PrimitiveNode) -> str:
    """The Python name of the waveform or digital data component a Bundle/Unbundle
    By Name terminal selects, from the type's built-in layout."""
    name = measure_data_field_name(flavor, term.nmux_field_index)
    if name is None:
        raise ConversionGap(
            f"The element {term.nmux_field_index} of {node.name!r} is not a "
            f"component of a {flavor} that lvkit knows the name of",
            node,
        )
    return to_var_name(name)


def _statements(source: str) -> list[ast.stmt]:
    return ast.parse(source).body


def unbundle(
    node: PrimitiveNode,
    list_out: list[Terminal],
    agg_var: str,
    flavor: str,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Each component an Unbundle By Name takes out of the waveform ``agg_var``."""
    waveform_form(flavor, node)
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    imports = set(IMPORTS)
    waveform = agg_var
    if not waveform.isidentifier():
        waveform = ctx.claim_name("waveform")
        statements.append(build_assign(waveform, parse_expr(agg_var)))
    for term in sorted(list_out, key=lambda t: t.index):
        component = measure_component(term, flavor, node)
        if component != ATTRIBUTES:
            bindings[term.id] = read_expression(waveform, component)
            continue
        name = ctx.make_output_var("waveform_attributes", node.id, term.id)
        source, names = attributes_read_source(waveform, name)
        statements.extend(_statements(source))
        ctx.import_variant(*names)
        bindings[term.id] = name
    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


def bundle(
    node: PrimitiveNode,
    list_in: list[Terminal],
    agg_out: list[Terminal],
    source: str | None,
    flavor: str,
    ctx: CodeGenContext,
) -> CodeFragment:
    """A Bundle By Name on a waveform: a new waveform holding the wired
    components and, for the others, those of the waveform wired in (``source``)
    or the default of their type. NI's Build Waveform page says only that an
    unwired waveform input makes a new waveform "based on the components you
    wire"."""
    form = waveform_form(flavor, node)
    statements: list[ast.stmt] = []
    wired: dict[str, str] = {}
    for term in sorted(list_in, key=lambda t: t.index):
        value = ctx.wired_input(term.id, node)
        if value is not None:
            wired[measure_component(term, flavor, node)] = value
    base = source
    if base is not None and not base.isidentifier():
        base = ctx.claim_name("waveform")
        statements.append(build_assign(base, parse_expr(source or "None")))
    values = default_components()
    for component in values:
        if component in wired:
            values[component] = wired[component]
        elif base is not None:
            values[component] = read_expression(base, component)
    attributes: str | None = None
    if ATTRIBUTES in wired:
        attributes = ctx.claim_name("extended_properties")
        statements.extend(
            _statements(attributes_write_source(wired[ATTRIBUTES], attributes))
        )
    elif base is not None:
        attributes = f"dict({base}.extended_properties)"
    var = ctx.make_output_var("waveform", node.id, agg_out[0].id if agg_out else None)
    statements.append(
        build_assign(
            var,
            parse_expr(
                build_expression(
                    form,
                    values[Y],
                    values[T0],
                    values[DT],
                    attributes,
                )
            ),
        )
    )
    return CodeFragment(
        statements=statements,
        bindings={t.id: var for t in agg_out},
        imports=set(IMPORTS),
    )
