"""Generate ordered attribute accesses for explicit Property Nodes."""

from __future__ import annotations

import ast

from lvkit.control_properties import control_property, is_value_row
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode, VINode
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.models import PropertyDef, Terminal

from ..ast_utils import parse_expr, to_var_name
from ..called_vi import run_call
from ..context import CodeGenContext
from ..default_objects import DefaultObjectProperty, default_object_property
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..panel_hooks import property_stmt
from . import class_property_node, vi_reference
from .base import (
    DefaultObjectError,
    resolve_ref_input,
    unwired_ref_class,
)
from .ctrl_val import report_panel_error
from .local_variable import read_control, write_control


def _no_python_equivalent(node: PrimitiveNode, prop: PropertyDef) -> ConversionGap:
    """The gap of a property row on a wired reference that has no Python reading
    or writing: it names the property as the diagram shows it and the class."""
    class_name = node.object_name or ""
    on_class = f" on a {class_name} reference" if class_name else ""
    return ConversionGap(
        f"Property '{prop.name}'{on_class} has no Python equivalent", node
    )


def _callees(ctx: CodeGenContext) -> str:
    """A tuple expression of the functions of the VIs this VI calls, in diagram
    order without repeats, and the imports that bring them in."""
    names: list[str] = []
    if ctx.graph is not None and ctx.vi_name:
        for other in ctx.graph.iter_nodes(ctx.vi_name):
            if not isinstance(other, VINode):
                continue
            run = run_call(other, ctx)
            # A call that runs no variant carries its own gap.
            if run.key is not None and run.gap is None:
                name = ctx.import_function(run.name, run.key)
                if name != ctx.function_name and name not in names:
                    names.append(name)
    tail = "," if len(names) == 1 else ""
    return f"({', '.join(names)}{tail})"


def _default_object_expression(
    ctx: CodeGenContext, value: DefaultObjectProperty
) -> str:
    """``value.python`` with the generating VI's function and callees written in."""
    expression = value.python.replace("{function}", ctx.this_vi_expression())
    if "{callees}" in expression:
        expression = expression.replace("{callees}", _callees(ctx))
    return expression


def _default_object_read(
    node: PrimitiveNode, class_name: str, prop: PropertyDef, term: Terminal
) -> DefaultObjectProperty:
    """The Python value of a property read on the default object of
    ``class_name``, found by the property's code path; a write, or a property with
    no documented Python value, is a gap naming the class and the property as the
    diagram shows it."""
    if term.direction != "output":
        raise DefaultObjectError(
            f"Property '{prop.name}' is written on the default {class_name} "
            "object, which has no Python equivalent",
            node,
        )
    value = default_object_property(
        class_name, tuple(seg.code for seg in prop.segments)
    )
    if value is None:
        raise DefaultObjectError(
            f"Property '{prop.name}' of the default {class_name} object has "
            "no Python equivalent",
            node,
        )
    return value


def _bound_value(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """A Property Node bound to a control or indicator of the VI whose rows are
    each ``Value`` or a write of a listed control property
    (``control_properties.json``). A ``Value`` row of a control reads the
    control's variable or assigns it, the same as a Local Variable of the control
    (``local_variable``); a listed write calls ``live_io.set_property``
    (``panel_hooks``).
    A node with any other row, or a ``Value`` row of an indicator, is a gap."""
    graph = ctx.graph
    bound = graph.bound_terminal(node) if graph is not None else None
    control = graph.bound_control(node) if graph is not None else None
    class_name = node.object_name or ""
    rows = correlate_property_terminals(
        node.properties, node.terminals, node.property_value_terminal_ids
    )
    supported = bound is not None and bool(rows)
    for row, term in rows:
        codes = [seg.code for seg in row.segments]
        if is_value_row(class_name, codes):
            supported = supported and control is not None
        else:
            listed = control_property(class_name, codes)
            supported = (
                supported
                and listed is not None
                and term is not None
                and term.direction == "input"
            )
    if bound is None or not supported:
        raise ConversionGap(
            "Implicit property reference requires a binding model: only a Property "
            "Node of a control or indicator of the VI whose rows are Value (of a "
            "control) and the listed control properties (written) is supported",
            node,
        )
    variable = ctx.resolve(control.id) if control is not None else None
    if control is not None and variable is None:
        raise ConversionGap(f"The control {control.name!r} has no variable", node)
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    for row, term in rows:
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        codes = [seg.code for seg in row.segments]
        if term.direction == "input":
            if not ctx.is_wired(term.id):
                raise ConversionGap(
                    "Unwired property write default is unqualified", node
                )
            value = ctx.wired_input(term.id, node)
            if value is None:
                raise CodeGenError("Property write value is unresolved", node)
        elif term.direction == "output":
            value = ""
        else:
            raise CodeGenError("Property value direction is unresolved", node)
        if is_value_row(class_name, codes):
            if control is None or variable is None:
                raise CodeGenError("Value row of a node without a control", node)
            if term.direction == "output":
                statements += read_control(node, control, variable, ctx)
                bindings[term.id] = variable
            else:
                statements += write_control(control, variable, value, ctx)
        else:
            listed = control_property(class_name, codes)
            hooks = ctx.panel_hooks
            if listed is None:
                raise CodeGenError("Property row is not a listed property", node)
            if hooks is None or bound.id not in hooks.names:
                raise ConversionGap(
                    f"The {listed.name!r} property of {bound.name!r} is written, "
                    "and this function has no panel hooks to report it to",
                    node,
                )
            statements.append(property_stmt(hooks, bound.id, listed, value))
    return CodeFragment(statements=statements, bindings=bindings)


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Use saved dcoList identities and order, separately from fixed ports.

    A row on a wired reference is Python only where Python has the value: a VI
    reference's Name, Path, Description and Exec.State
    (``vi_reference.vi_property_read``);
    every other read, and every write, is a gap naming the property and the
    reference's class. Implicit control bindings are rejected unless the node is
    bound to a control of the VI. Error-cluster terminals are
    not read here: the node's error out is its error in, and the node is
    skipped when an error comes in (``error_flow.guard``).
    """
    properties = node.properties or []
    value_ids = node.property_value_terminal_ids
    if len(value_ids) != len(properties):
        raise CodeGenError("Property value terminal identities are unresolved", node)
    if len(set(value_ids)) != len(value_ids):
        raise CodeGenError("Property value terminal identities are duplicated", node)
    if len({term.id for term in node.terminals}) != len(node.terminals):
        raise CodeGenError("Property node terminal identities are duplicated", node)
    if node.bound_control_uid:
        return _bound_value(node, ctx)
    if ctx.graph is not None and class_property_node.is_class_property_node(node):
        return class_property_node.generate(node, ctx)
    fixed = [term for term in node.terminals if term.id not in value_ids]
    default_class = unwired_ref_class(node, ctx)
    ref_var = "" if default_class else resolve_ref_input(node, ctx)

    statements: list[ast.stmt] = []
    imports: set[str] = set()
    bindings: dict[str, str] = {}
    attr_names: dict[str, str] = {}
    panel_read = False
    for prop, term in correlate_property_terminals(
        properties, node.terminals, value_ids
    ):
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        segment_names = [seg.name for seg in prop.segments]
        if not segment_names or not all(segment_names):
            raise ConversionGap("Property name is unresolved", node)
        attr_name = to_var_name("_".join(segment_names))
        if attr_name in attr_names and attr_names[attr_name] != prop.name:
            raise ConversionGap("Normalized property names collide", node)
        attr_names[attr_name] = prop.name
        if default_class is not None:
            value_expr = _default_object_read(node, default_class, prop, term)
            var_name = ctx.make_output_var(attr_name, term.id, term.id)
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=parse_expr(_default_object_expression(ctx, value_expr)),
                )
            )
            imports.update(value_expr.imports)
            bindings[term.id] = var_name
        elif term.direction == "output":
            # Reads execute even when their result is unwired. Repeated accesses
            # to the same property need distinct snapshots, not one shared name.
            var_name = ctx.make_output_var(f"{ref_var}_{attr_name}", term.id, term.id)
            reflected = vi_reference.vi_property_read(node, prop, ref_var, ctx)
            if reflected is None:
                raise _no_python_equivalent(node, prop)
            panel_read = panel_read or vi_reference.reads_panel(node, prop)
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=reflected,
                )
            )
            bindings[term.id] = var_name
        elif term.direction == "input":
            if not ctx.is_wired(term.id):
                raise ConversionGap(
                    "Unwired property write default is unqualified", node
                )
            raise _no_python_equivalent(node, prop)
        else:
            raise CodeGenError("Property value direction is unresolved", node)

    for term in fixed:
        # The error out is the error in (``error_flow.guard``); nothing to bind.
        if term.is_error_cluster:
            continue
        if term.direction != "output" or not ctx.is_wired(term.id):
            continue
        if default_class is not None:
            raise DefaultObjectError(
                f"Property Node reference output on the default {default_class} "
                "object has no Python equivalent",
                node,
            )
        if (
            term.index == 1
            and term.lv_type
            and term.lv_type.underlying_type == "Refnum"
        ):
            bindings[term.id] = ref_var
        else:
            raise ConversionGap("Property flow-through output is unresolved", node)
    if not statements:
        statements.append(
            ast.Expr(value=ast.Constant(value="# Property Node: no properties"))
        )
    if panel_read:
        # A read of the referenced VI's front panel fails with the panel's own
        # error (1026, 1035): the node's error out, as for Ctrl Val.
        fragment = report_panel_error(node, ctx, statements, bindings)
        fragment.imports = imports
        return fragment
    return CodeFragment(statements=statements, bindings=bindings, imports=imports)
