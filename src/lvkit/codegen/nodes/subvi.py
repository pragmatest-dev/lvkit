"""Code generator for SubVI calls."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import NoReturn

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import LVTypeKind, Terminal, TerminalDirection
from lvkit.primitive_resolver import TerminalResolutionNeeded
from lvkit.vilib_resolver import (
    CatalogPaneMismatch,
    ResolutionContext,
    VIEntry,
    VILibResolutionNeeded,
    VILibResolver,
    derive_python_location,
    get_resolver,
)

from ..ast_utils import (
    parse_expr,
    result_field_name,
    to_function_name,
    to_var_name,
)
from ..callee_names import callee_names
from ..class_model import accessor_of, member_shape
from ..context import CodeGenContext, terminal_default_expression
from ..error_path import is_error_wire_vi
from ..errors import UnknownDefault
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver
from ..value_copy import is_by_value_object, owned_input
from ..vilib_pane import check_against_pane
from . import accessor_call, error_vi, method_call, vilib_op
from .template_inputs import template_reads


def generate(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a SubVI call."""
    subvi_name = node.name or ""
    if not subvi_name:
        raise ConversionGap(
            "A VI node has no name, so the VI it calls is unknown", node
        )

    # The vi.lib error VIs (Merge Errors, Clear Errors, Error Cluster From Error
    # Code, the error handlers) are exception handling, not calls of a module.
    catalog_path = ctx.catalog_path(node)
    if is_error_wire_vi(catalog_path, get_resolver()):
        return error_vi.generate(node, ctx)

    # A VI of a class is a method of its Python class; a property accessor is
    # the attribute or property.
    member = _class_member(node, ctx)
    if member is not None:
        found = accessor_of(ctx.graph, member) if ctx.graph is not None else None
        if found is not None:
            return accessor_call.generate(node, ctx, *found)
        assert ctx.graph is not None
        shape = member_shape(ctx.graph, member)
        assert shape is not None
        return method_call.generate(node, ctx, shape)

    # Look up VI — polymorphic variant if selected, otherwise the recorded path
    vilib_vi = None
    if node.poly_variant_name:
        vilib_vi = _vilib_resolver(ctx).resolve_call(
            catalog_path, node.poly_variant_name, wrapper_fallback=False
        )
        if not vilib_vi:
            # Variant not in JSON — fail or emit inline raise
            _raise_vilib_resolution(node, ctx, vilib_vi=None)
        vilib_vi = _checked_against_pane(vilib_vi, node, ctx)

    else:
        vilib_vi = _get_vilib_vi(node, ctx)

    # A catalog VI the generator writes from the graph.
    if vilib_vi and vilib_vi.op:
        return vilib_op.generate(node, ctx, vilib_vi)

    # Check for inline replacement first
    if vilib_vi and vilib_vi.python_code and vilib_vi.inline:
        return _generate_inline(node, ctx, vilib_vi)

    # The module of the VI the call names. A polymorphic call whose polymorphic
    # VI the caller does not record calls the variant it resolves to.
    callee_name, callee_key = subvi_name, node.callee_key
    if callee_key is None and node.variant_key is not None:
        callee_key = node.variant_key
        callee_name = (node.qualified_name or subvi_name).rsplit(":", 1)[-1]
    elif (
        node.variant_key is not None
        and vilib_vi is not None
        and vilib_vi.python_code is not None
    ):
        # The catalog gives the selected variant its own module.
        callee_key = node.variant_key
        callee_name = (node.qualified_name or subvi_name).rsplit(":", 1)[-1]
    elif callee_key is None and node.qualified_path and node.qualified_name:
        # The caller records a path (a `<vilib>` or `<extravilib>` VI with no
        # configured root): the absent VI is named by its qualified name, the
        # identity its stub is keyed by.
        callee_name = node.qualified_name
    parts = build_call(node, ctx, callee_name, callee_key, vilib_vi)
    stmt = ast.Assign(
        targets=[ast.Name(id=parts.result_var, ctx=ast.Store())],
        value=parts.call,
    )
    return CodeFragment(statements=[*parts.prelude, stmt], bindings=parts.bindings)


def _class_member(node: VINode, ctx: CodeGenContext) -> str | None:
    """The key of the VI a call names when it is a member of a loaded class: the
    callee, or the variant of a polymorphic VI the call selects."""
    if ctx.graph is None:
        return None
    key = node.variant_key or node.callee_key
    if key is not None and ctx.graph.owning_class_key(key) is not None:
        return key
    return None


@dataclass(frozen=True)
class CallParts:
    """One call of a generated VI: the call, the variable its result is
    assigned to, and the node's output terminals bound to that result."""

    func_name: str
    call: ast.Call
    result_var: str
    bindings: dict[str, str]
    prelude: list[ast.stmt] = field(default_factory=list)


def build_call(
    node: VINode,
    ctx: CodeGenContext,
    callee_name: str,
    callee_key: str | None,
    vilib_vi: VIEntry | None,
) -> CallParts:
    """The call of the VI ``callee_key`` from ``node``: its function imported,
    each wired input passed by the callee's parameter name, each output read from
    the result by the callee's output name."""
    func_name = ctx.import_function(callee_name, callee_key)
    result_var = ctx.make_output_var(f"{func_name}_result", node.id)
    prelude: list[ast.stmt] = []
    args, keywords = _build_arguments(node, ctx, vilib_vi, prelude)
    if keywords:
        call = ast.Call(
            func=ast.Name(id=func_name, ctx=ast.Load()),
            args=[],
            keywords=[
                ast.keyword(arg=k, value=_to_ast_value(v)) for k, v in keywords.items()
            ],
        )
    else:
        call = ast.Call(
            func=ast.Name(id=func_name, ctx=ast.Load()),
            args=[_to_ast_value(a) for a in args],
            keywords=[],
        )
    return CallParts(
        func_name=func_name,
        call=call,
        result_var=result_var,
        bindings=_build_output_bindings(node, result_var, vilib_vi, ctx),
        prelude=prelude,
    )


def _generate_inline(
    node: VINode, ctx: CodeGenContext, vilib_vi: VIEntry
) -> CodeFragment:
    """Generate inline Python code instead of function call.

    Uses {placeholder} syntax for both inputs and outputs:
    - Inputs: {param} replaced with wired value expression
    - Outputs: {param} replaced with unique variable name, then bound

    Example template: "{size} = len({array})"
    Becomes: "get_array_size_0_size = len(my_array)"
    With binding: size_terminal_id -> "get_array_size_0_size"
    """
    assert vilib_vi.python_code is not None
    template = vilib_vi.python_code
    func_name = to_function_name(node.name or "inline")

    # Build index → param name mappings for inputs and outputs
    vilib_inputs: dict[int, str] = {}
    vilib_outputs: dict[int, str] = {}
    for vt in vilib_vi.terminals:
        if vt.index is None:
            continue
        param_key = vt.python_param or to_var_name(vt.name)
        if vt.direction == TerminalDirection.INPUT:
            vilib_inputs[vt.index] = param_key
        elif vt.direction == TerminalDirection.OUTPUT:
            vilib_outputs[vt.index] = param_key

    # Build default values from the catalog's documented terminal defaults
    vilib_defaults: dict[str, str] = {}
    for vt in vilib_vi.terminals:
        if vt.direction == TerminalDirection.INPUT and vt.default is not None:
            param_key = vt.python_param or to_var_name(vt.name)
            vilib_defaults[param_key] = terminal_default_expression(vt.default, vt.type)

    # Substitute input placeholders with wired values
    for term in ctx.enriched_terminals(node):
        if term.direction != "input":
            continue

        term_index = term.index
        term_id = term.id
        value = ctx.wired_input(term_id, node)

        if term_index in vilib_inputs:
            param_key = vilib_inputs[term_index]
            placeholder = "{" + param_key + "}"
            if not value and template_reads(template, placeholder):
                if term.is_error_cluster:
                    value = "None"
                elif param_key in vilib_defaults:
                    value = vilib_defaults[param_key]
                else:
                    raise UnknownDefault(vilib_vi.name, term.name or param_key, node)
            template = template.replace(placeholder, value or "None")

    # Substitute any remaining unresolved input placeholders with defaults
    for param_key, default in vilib_defaults.items():
        placeholder = "{" + param_key + "}"
        if placeholder in template:
            template = template.replace(placeholder, default)

    # Check for unresolved input placeholders — same as vilib resolution
    unresolved_inputs = {
        m
        for m in re.findall(r"\{(\w+)\}", template)
        if m not in set(vilib_outputs.values())
    }
    if unresolved_inputs:
        _raise_vilib_resolution(node, ctx, vilib_vi=vilib_vi)

    # Build ref_terminals passthrough map: output_param -> input variable
    ref_passthrough: dict[str, str] = {}
    if vilib_vi.ref_terminals:
        for out_param, passthrough_spec in vilib_vi.ref_terminals.items():
            if passthrough_spec.startswith("passthrough_from:"):
                in_param = passthrough_spec[len("passthrough_from:") :]
                # Find the input variable that was substituted
                for term in ctx.enriched_terminals(node):
                    if term.direction != "input":
                        continue
                    if term.index in vilib_inputs:
                        if vilib_inputs[term.index] == in_param:
                            resolved = ctx.wired_input(term.id, node)
                            if resolved:
                                ref_passthrough[out_param] = resolved
                            break

    # Substitute output placeholders with unique variable names
    # and build bindings
    bindings = {}
    output_var_map: dict[str, str] = {}  # param_key -> generated var name

    for term in ctx.enriched_terminals(node):
        if term.direction != "output":
            continue
        # Skip error cluster outputs — Python uses exceptions
        if term.is_error_cluster:
            continue

        term_index = term.index
        term_id = term.id

        if term_index in vilib_outputs:
            param_key = vilib_outputs[term_index]
            # Check for ref passthrough - bind to same input variable
            if param_key in ref_passthrough:
                bindings[term_id] = ref_passthrough[param_key]
                output_var_map[param_key] = ref_passthrough[param_key]
            elif "{" + param_key + "}" in template:
                # Output appears in template — will be assigned
                var_name = f"{func_name}_{param_key}"
                output_var_map[param_key] = var_name
                bindings[term_id] = var_name
            elif ctx.is_wired(term_id):
                # Output not in template but wired downstream — bind to None
                bindings[term_id] = "None"
            # else: not in template, not wired — skip entirely
        else:
            # Output not in vilib definition - bind to None
            bindings[term_id] = "None"

    # Replace output placeholders in template
    for param_key, var_name in output_var_map.items():
        placeholder = "{" + param_key + "}"
        template = template.replace(placeholder, var_name)

    # Parse as statement(s) - supports multi-line
    try:
        parsed_mod = ast.parse(template, mode="exec")
        statements: list[ast.stmt] = parsed_mod.body
    except SyntaxError:
        # Fallback: wrap as expression statement
        parsed_expr = ast.parse(template, mode="eval")
        assert isinstance(parsed_expr, ast.Expression)
        statements = [ast.Expr(value=parsed_expr.body)]

    # Build imports from vilib imports
    imports = set(vilib_vi.imports) if vilib_vi.imports else set()

    return CodeFragment(
        statements=statements,
        bindings=bindings,
        imports=imports,
    )


def _vilib_resolver(ctx: CodeGenContext | None) -> VILibResolver:
    """The vi.lib resolver this run reads: its own copy once it has learned, else
    the shared catalog."""
    return ctx.learned_vilib[0] if ctx and ctx.learned_vilib else get_resolver()


def _checked_against_pane(entry: VIEntry, node: VINode, ctx: CodeGenContext) -> VIEntry:
    """``entry`` completed from the connector pane of the VI the call names when
    that VI is loaded; a catalog terminal that disagrees with the pane is a gap."""
    key = ctx.graph.loaded_callee_key(node) if ctx.graph is not None else None
    if ctx.graph is None or key is None:
        return entry
    inputs, _, outputs, _ = ctx.graph.vi_interface(key)
    check = check_against_pane(entry, [*inputs, *outputs])
    if check.mismatches:
        raise CatalogPaneMismatch(node.name or entry.name, check.mismatches)
    return check.entry


def _get_vilib_vi(node: VINode, ctx: CodeGenContext) -> VIEntry | None:
    """The catalog entry of the VI a call records a path for, checked against the
    VI's own connector pane when it is loaded.

    If the VI is found but has no terminal indices, raises VILibResolutionNeeded
    with context to help resolve the indices.
    """
    try:
        resolver = _vilib_resolver(ctx)
        recorded_path = ctx.catalog_path(node)

        vi = resolver.resolve_recorded(recorded_path)
        catalog_name = resolver.catalog_file_name(recorded_path)
        if vi is None or catalog_name is None:
            return None
        vi = _checked_against_pane(vi, node, ctx)

        # Check if caller's WIRED terminals are missing indices
        # Only require indices for terminals that have actual wires
        has_terminals = bool(vi.terminals)
        if has_terminals and node and ctx:
            # Get indices the caller is actually wiring (not just connector slots)
            caller_indices = set()
            for term in ctx.enriched_terminals(node):
                term_id = term.id
                term_index = term.index
                # Only count terminals that have wires connected
                if term_id and term_index is not None and ctx.is_wired(term_id):
                    caller_indices.add(term_index)
            # Get indices we have in vilib
            vilib_indices = {t.index for t in vi.terminals if t.index is not None}
            # Missing = caller wires indices we don't have mapped
            missing_indices = bool(caller_indices - vilib_indices)
        else:
            missing_indices = False

        if missing_indices:
            # Auto-update vilib JSON with observed terminals
            # Filter to only wired terminals
            wired_node_terminals = (
                [term for term in ctx.enriched_terminals(node) if ctx.is_wired(term.id)]
                if node and ctx
                else []
            )

            if wired_node_terminals:
                # Learn the observed terminals into this run's own copy of the
                # catalog.
                if not ctx.learned_vilib:
                    ctx.learned_vilib.append(get_resolver().fork())
                vi = ctx.learned_vilib[0].auto_update_terminals(
                    vi_name=catalog_name,
                    wired_terminals=wired_node_terminals,
                    caller_vi=ctx.vi_name,
                )
                # Successfully updated - continue with updated VI

        return vi
    except ImportError:
        return None


def is_call_frame_terminal(node: VINode, term: Terminal) -> bool:
    """True for a terminal of a Call By Reference node that is not a terminal of
    the VI it calls: the VI reference and error terminals, numbered below zero."""
    return (
        node.node_type == "callByRefNode" and term.index is not None and term.index < 0
    )


def _raise_terminal_resolution(
    subvi_name: str,
    direction: str,
    term: Terminal,
    ctx: CodeGenContext | None,
    vilib_vi: VIEntry | None,
) -> NoReturn:
    """Raise TerminalResolutionNeeded for an unresolvable terminal name."""
    raise TerminalResolutionNeeded(
        prim_id=subvi_name or "unknown",
        prim_name=subvi_name or "unknown",
        terminal_direction=direction,
        terminal_type=(term.lv_type.underlying_type if term.lv_type else None),
        available=[],
        vi_name=ctx.vi_name if ctx else None,
        kind="vilib" if vilib_vi is not None else "subvi",
    )


def _catalog_defaults(vilib_vi: VIEntry | None) -> dict[int, str]:
    """The Python expression of the default each input terminal of the catalog
    entry documents (``terminal_default_expression``), by index. Only a call that
    runs the entry's Python takes them: a call of a generated module already
    carries the control's own default."""
    if vilib_vi is None or vilib_vi.python_code is None:
        return {}
    return {
        t.index: terminal_default_expression(t.default, t.type)
        for t in vilib_vi.terminals
        if t.direction == TerminalDirection.INPUT
        and t.index is not None
        and t.default is not None
    }


def _build_arguments(
    node: VINode,
    ctx: CodeGenContext,
    vilib_vi: VIEntry | None,
    prelude: list[ast.stmt],
) -> tuple[list[str], dict[str, str]]:
    """Build input arguments, using vilib names if available. An argument that is
    a class object or named cluster the callee may change is copied first (into
    ``prelude``) when something else can still read it."""
    subvi_name = node.name or ""

    vilib_inputs = _build_vilib_terminal_map(
        vilib_vi,
        TerminalDirection.INPUT,
    )
    catalog_defaults = _catalog_defaults(vilib_vi)

    args = []
    keywords: dict[str, str] = {}
    names = callee_names(ctx, node)

    for term in ctx.enriched_terminals(node):
        if term.direction != "input" or is_call_frame_terminal(node, term):
            continue

        term_id = term.id
        term_index = term.index
        term_name = term.name or ""
        value = (
            owned_input(ctx, term, node, prelude)
            if is_by_value_object(term.lv_type)
            else ctx.wired_input(term_id, node)
        )
        wired = value is not None

        # Skip error cluster terminals — Python uses exceptions
        if term.is_error_cluster:
            continue

        # An unwired input of a catalog VI runs with the default the catalog (or
        # the loaded VI's control) gives it; any other unwired input uses the
        # called function's own default.
        if value is None:
            value = catalog_defaults.get(term_index)
        if value is None:
            if names is not None and term_index in names.required:
                raise ConversionGap(
                    f"The call of {subvi_name!r} leaves its required input "
                    f"{term_name or names.parameters[term_index]!r} unwired",
                    node,
                )
            continue

        # Determine parameter name with priority:
        # 1. vilib python_param name
        # 2. Terminal name (enriched from callee FP by _enrich_subvi_terminals_typed)
        if vilib_inputs and term_index in vilib_inputs:
            # vilib knows the correct parameter name
            param_name = to_var_name(vilib_inputs[term_index])
        elif names is not None and term_index in names.parameters:
            param_name = names.parameters[term_index]
        elif term_name:
            # Use terminal name from node
            param_name = to_var_name(term_name)
        else:
            _raise_terminal_resolution(subvi_name, "input", term, ctx, vilib_vi)

        # Check if this parameter is an enum typedef - generate enum reference
        final_value = (
            _resolve_enum_value(value, term, vilib_vi, subvi_name, node.callee_key, ctx)
            if vilib_vi and wired
            else value
        )

        keywords[param_name] = final_value

    return args, keywords


def _resolve_enum_value(
    value: str,
    term: Terminal,
    vilib_vi: VIEntry,
    subvi_name: str,
    callee_key: str | None,
    ctx: CodeGenContext,
) -> str:
    """Resolve enum constant to enum reference if applicable.

    Args:
        value: The resolved value (e.g., "7")
        term: Terminal object with type info
        vilib_vi: VI entry with terminal typedef info
        subvi_name: The called VI's name
        callee_key: The called VI's identity; its module defines the enum class
        ctx: Code generation context

    Returns:
        Either the original value or an enum reference like "EnumName.MEMBER"
    """
    # Only process constant integers
    if not value.isdigit() and not (value.startswith("-") and value[1:].isdigit()):
        return value

    int_value = int(value)
    term_index = term.index

    # Find this terminal in vilib
    vilib_term = None
    for vt in vilib_vi.terminals:
        if vt.index == term_index and vt.direction == TerminalDirection.INPUT:
            vilib_term = vt
            break

    if not vilib_term or not vilib_term.type:
        return value

    # Check if this terminal references an enum typedef
    if not vilib_term.type.endswith(".ctl"):
        return value

    # Get the LVType from vilib resolver
    resolver = _vilib_resolver(ctx)
    lv_type = resolver.resolve_type(vilib_term.type)

    if not lv_type or lv_type.kind != LVTypeKind.ENUM or not lv_type.values:
        return value

    # Reverse lookup: find enum member with this value
    for member_name, enum_val in lv_type.values.items():
        if enum_val.value == int_value:
            # Found it! Generate enum reference
            # Derive Python location from typedef_name
            if lv_type.typedef_name:
                package, class_name = derive_python_location(lv_type.typedef_name)
                # Add import for this enum
                if isinstance(ctx.import_resolver, ImportResolver):
                    # The enum class sits in the module of the called VI
                    module = ctx.import_resolver.function(
                        subvi_name, callee_key
                    ).statement
                    if module:
                        ctx.add_import(
                            module.rsplit(" import ", 1)[0] + f" import {class_name}"
                        )
                else:
                    ctx.add_import(f"from {package} import {class_name}")
                return f"{class_name}.{member_name}"
            return str(int_value)

    # Value not in enum - return as-is
    return value


def _build_vilib_terminal_map(
    vilib_vi: VIEntry | None,
    direction: TerminalDirection,
) -> dict[int, str]:
    """Build index → terminal name mapping from vilib VI.

    Prefers python_param if available, otherwise uses terminal name.
    """
    result: dict[int, str] = {}
    if vilib_vi:
        for vt in vilib_vi.terminals:
            if vt.direction == direction and vt.index is not None:
                result[vt.index] = vt.python_param or vt.name
    return result


def _build_output_bindings(
    node: VINode,
    result_var: str,
    vilib_vi: VIEntry | None,
    ctx: CodeGenContext,
) -> dict[str, str]:
    """Build output terminal bindings using vilib names."""
    subvi_name = node.name or ""

    vilib_outputs = _build_vilib_terminal_map(
        vilib_vi,
        TerminalDirection.OUTPUT,
    )

    bindings = {}
    names = callee_names(ctx, node)
    for term in ctx.enriched_terminals(node):
        if term.direction != "output" or is_call_frame_terminal(node, term):
            continue

        term_id = term.id
        term_index = term.index
        term_name = term.name or ""

        # Skip error terminals — Python uses exceptions
        if term.is_error_cluster:
            continue

        # Priority: vilib name > terminal name (enriched from callee)
        if vilib_outputs and term_index in vilib_outputs:
            field = result_field_name(vilib_outputs[term_index])
        elif names is not None and term_index in names.fields:
            field = names.fields[term_index]
        elif term_name:
            field = result_field_name(term_name)
        else:
            _raise_terminal_resolution(subvi_name, "output", term, ctx, vilib_vi)

        bindings[term_id] = f"{result_var}.{field}"

    return bindings


def _to_ast_value(value: str) -> ast.expr:
    """Parse an argument value string into a real AST expression.

    Parsing (rather than wrapping the whole string in a single ``ast.Name``) is
    what makes a dotted access like ``result.field`` an actual ``Attribute``
    node over ``Name('result')``. Otherwise the load of ``result`` is hidden
    inside an opaque name id, and dead-code elimination then wrongly drops
    ``result``'s assignment as unused, leaving the consumer referencing an
    unbound name.
    """
    return parse_expr(value)


def _raise_vilib_resolution(
    node: VINode,
    ctx: CodeGenContext,
    vilib_vi: VIEntry | None,
) -> NoReturn:
    """An unknown or unresolvable vi.lib SubVI call is a gap."""
    context = _build_resolution_context(node, ctx, vilib_vi)
    raise VILibResolutionNeeded(node.name or "", context=context)


def _build_resolution_context(
    node: VINode,
    ctx: CodeGenContext,
    vilib_vi: VIEntry | None,
) -> ResolutionContext:
    """Build context for VILibResolutionNeeded exception.

    Collects wire types from caller's dataflow to help resolve terminal indices.
    """
    poly_selector: str | None = None
    if node.poly_variant_name:
        poly_selector = node.poly_variant_name

    # Collect wire types from dataflow (actual indices being used)
    wire_types: list[str] = []
    for term in ctx.enriched_terminals(node):
        term_id = term.id
        term_index = term.index
        direction = term.direction or "?"

        # Get type info from terminal's lv_type
        type_info = "?"
        if hasattr(term, "lv_type") and term.lv_type:
            lv_type = term.lv_type
            if lv_type.underlying_type:
                type_info = lv_type.underlying_type
            elif lv_type.kind:
                type_info = lv_type.kind

        # Check if wired
        is_wired = ctx.is_wired(term_id) if term_id else False
        wired_str = "wired" if is_wired else "unwired"

        wire_types.append(f"idx_{term_index} ({direction}, {type_info}, {wired_str})")

    # Collect terminal names from vilib if available
    terminal_names: list[str] = []
    if vilib_vi and vilib_vi.terminals:
        terminal_names = [
            f"{t.name} (idx={t.index}, {t.direction})" for t in vilib_vi.terminals
        ]

    return ResolutionContext(
        caller_vi=ctx.vi_name,
        caller_qualified_name=getattr(ctx, "qualified_vi_name", None),
        poly_selector=poly_selector,
        wire_types=wire_types,
        terminal_names=terminal_names,
        qualified_path=getattr(node, "qualified_path", None),
    )
