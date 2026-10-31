"""Code generator for LabVIEW primitives."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import NoReturn

from lvkit.graph.core import uid_of
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import lay_out_node
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.parser.constants import OPERATION_NODE_CLASSES
from lvkit.parser.node_types import get_display_name
from lvkit.primitive_resolver import (
    PrimitiveResolutionNeeded,
    PrimitiveTerminal,
    ResolvedPrimitive,
    TerminalResolutionNeeded,
    get_resolver,
)
from lvkit.python_numeric import UNSIGNED_INTEGERS, fixed_width
from lvkit.terminal_default import DefaultBehavior

from ..ast_utils import (
    build_assign,
    parse_expr,
    parse_stmt,
    to_var_name,
)
from ..context import CodeGenContext, terminal_default_expression
from ..elementwise import arrayify
from ..errors import CodeGenError, TerminalNotOnCall, UnknownDefault
from ..fragment import CodeFragment
from .template_inputs import template_reads


def _array_operand_ranks(
    node: PrimitiveNode, input_map: Mapping[str, str]
) -> dict[str, int]:
    """The dimension count of each array-typed input, keyed by the source text its
    operand takes in the template (normalized the way the expression is parsed)."""
    ranks: dict[str, int] = {}
    for term in node.terminals:
        if (
            term.direction == "input"
            and term.lv_type is not None
            and term.lv_type.kind == LVTypeKind.ARRAY
        ):
            operand = input_map.get(f"in_{term.index}")
            if operand:
                ranks[ast.unparse(parse_expr(operand))] = term.lv_type.dimensions or 1
    return ranks


def _paren_if_compound(expr: str) -> str:
    """Wrap an inlined operand in parens if it is a compound expression.

    Operand substitution into a template is string-level, so an inlined
    sub-expression must keep its precedence: an upstream ``z | x`` dropped into
    ``in_1 & in_2`` has to stay ``(z | x) & …``, not collapse to ``z | x & …``
    (``&`` binds tighter than ``|``). Wrapping is always safe — redundant parens
    never change meaning — and also fixes ``~in_1`` / ``in_1[0]`` / etc."""
    try:
        node = ast.parse(expr, mode="eval").body
    except (SyntaxError, ValueError):
        return expr
    if isinstance(
        node,
        (
            ast.BoolOp,
            ast.BinOp,
            ast.UnaryOp,
            ast.Compare,
            ast.IfExp,
            ast.Lambda,
            ast.NamedExpr,
            ast.Await,
        ),
    ):
        return f"({expr})"
    return expr


def _int_input_terminal(node: PrimitiveNode) -> Terminal | None:
    """First input terminal carrying a LabVIEW integer type, else None.

    LabVIEW's boolean-logic prims (And/Or/Not) are BITWISE on integer operands;
    this drives that dispatch."""
    for t in node.terminals:
        if t.direction != "input" or t.lv_type is None:
            continue
        ut = _element_underlying(t.lv_type)
        if ut.startswith("NumInt") or ut.startswith("NumUInt"):
            return t
    return None


def _element_underlying(lv_type: LVType) -> str:
    """The underlying type name of a scalar, or of an array's elements."""
    while lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        lv_type = lv_type.element_type
    return lv_type.underlying_type or ""


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a primitive node."""
    prim_id = node.prim_id

    # Merge Errors (prim 2147; not 2401, which is Swap Values -- #59) is a
    # structural signal, not a code node. In the exception model, error
    # merging happens via try/except on future.result() calls — the
    # primitive itself produces no code.
    if prim_id == 2147:
        return CodeFragment.empty()

    # Get primitive hint.
    # Specialized node_types (subset, aBuild, etc.) take priority over
    # prim_id because some primResIDs are shared between different
    # functions (e.g., 1516 = both Array Subset and Select, distinguished
    # by XML class). Generic "prim" nodes use prim_id lookup.
    # If node_type resolves but has no usable code, fall through to prim_id.
    resolver = get_resolver()
    resolved = None
    node_type = getattr(node, "node_type", None)
    if node_type and node_type != "prim":
        resolved = resolver.resolve_by_node_type(node_type)
        # Fall through if node_type resolved but has no code -- unless it carries
        # an ``op`` tag, whose generator-owned handler supplies python_code below.
        if resolved and not resolved.python_code and not resolved.op:
            resolved = None
    if not resolved and prim_id is not None:
        resolved = resolver.resolve(prim_id=prim_id)
    if not resolved:
        # A node class captured GENERICALLY (not in the handled allowlist —
        # e.g. decimate, interLeave, extFunc, exprNode) reaches here with no
        # primResID. A gap via raise_unknown rather than silently drop it
        # to empty code. A class in OPERATION_NODE_CLASSES that still lacks a
        # handler keeps prior behavior rather than being flagged unknown.
        is_generic_unknown = bool(
            prim_id is None
            and node_type
            and node_type != "prim"
            and node_type not in OPERATION_NODE_CLASSES
        )
        if prim_id is None and not is_generic_unknown:
            # A node class with no generator yet makes nothing; that is a gap when
            # a wire leaves it, since the wire then carries no value.
            if any(
                t.direction == "output" and ctx.is_wired(t.id) for t in node.terminals
            ):
                raise_unknown(node, prim_id, ctx)
            return CodeFragment.empty()
        raise_unknown(node, prim_id, ctx)

    # Generator-owned op handler: a neutral ``op`` tag in the data maps to a
    # Python emission template here (codegen/nodes/ops/), keeping target-language
    # translations in the generator instead of as strings in the data file. Only
    # fires when the data carries no python_code, so existing JSON templates win.
    # A fragment handler emits whole statements for shapes one template can't
    # express; it returns None to defer to the template.
    if resolved.op and not resolved.python_code:
        from .ops import get_op_fragment, get_op_template

        op_fragment = get_op_fragment(resolved.op)
        if op_fragment is not None and resolved.confidence not in (
            "unknown",
            "placeholder",
        ):
            fragment = op_fragment(node, ctx)
            if fragment is not None:
                return fragment

        op_template = get_op_template(resolved.op)
        if op_template is not None:
            resolved.python_code = op_template(node, resolved)

    # A primitive known by name but with no implementation raises where it runs.
    if resolved.confidence == "placeholder":
        raise_placeholder(node, resolved, ctx)

    # LabVIEW And/Or/Not are BITWISE on integer operands. When an input carries
    # an integer type, prefer the primitive's integer template; Not (1064) needs
    # a width-masked complement no static template can express, so build it here.
    int_in = _int_input_terminal(node)
    if int_in is not None:
        if prim_id == 1064:  # Not -> ~x, masked to width for UNSIGNED ints
            ut = _element_underlying(int_in.lv_type) if int_in.lv_type else ""
            expr = fixed_width("~in_1", ut) if ut in UNSIGNED_INTEGERS else "~in_1"
            if ut in UNSIGNED_INTEGERS:
                resolved.imports = [*(resolved.imports or []), "import ctypes"]
            if isinstance(resolved.python_code, dict) and resolved.python_code:
                key = next(iter(resolved.python_code))
                resolved.python_code = {key: expr}
            else:
                resolved.python_code = expr
        elif resolved.python_code_int is not None:
            resolved.python_code = resolved.python_code_int

    # Check if primitive is truly unknown (no code, unknown confidence, or comment)
    code = resolved.python_code if resolved else None
    is_unknown = (
        not resolved
        or not code
        or resolved.confidence == "unknown"
        or (isinstance(code, str) and code.strip().startswith("#"))
    )
    if is_unknown:
        # Unknown primitive - emit explicit error
        raise_unknown(node, prim_id, ctx)

    # Resolve input values from context (use resolved terminals for names)
    input_map = _build_input_map(node, ctx, resolved)

    # Detect passthrough outputs BEFORE allocating variable names.
    # Passthroughs (template is just `in_N`) bind directly to the input
    # variable — no assignment, no make_output_var() allocation.
    passthrough_bindings: dict[str, str] = {}
    passthrough_term_ids: set[str] = set()
    if isinstance(resolved.python_code, dict):
        passthrough_bindings, passthrough_term_ids = _detect_passthroughs(
            node,
            resolved.python_code,
            input_map,
            ctx,
            resolved,
        )

    # Get wired output terminals (excluding passthroughs)
    wired_outputs = _get_wired_outputs(
        node,
        resolved,
        ctx,
        skip_term_ids=passthrough_term_ids,
    )

    # Numeric primitives are element-wise over arrays. When this op is flagged
    # elementwise and an operand is an array, broadcast its operators.
    operand_ranks = (
        _array_operand_ranks(node, input_map) if resolved.elementwise else None
    )

    # Build code based on code type
    if isinstance(resolved.python_code, dict):
        fragment = _build_dict_hint(
            node,
            resolved.python_code,
            input_map,
            wired_outputs,
            ctx,
            resolved,
            operand_ranks,
        )
    else:
        fragment = _build_string_hint(
            resolved.python_code or "",
            input_map,
            wired_outputs,
            ctx,
            resolved,
            operand_ranks,
        )

    # Merge passthrough bindings
    fragment.bindings.update(passthrough_bindings)

    # Record array-typed output variables so a final pass can broadcast
    # operators over them even after single-use expression inlining.
    for term in node.terminals:
        if (
            term.direction == "output"
            and term.lv_type is not None
            and term.lv_type.kind == LVTypeKind.ARRAY
        ):
            bound = fragment.bindings.get(term.id)
            if bound and bound.isidentifier():
                ctx.array_vars[bound] = term.lv_type.dimensions or 1

    # Add imports from primitive definition (normalize bare module names)
    if resolved.imports:
        for imp in resolved.imports:
            if not imp.startswith(("import ", "from ")):
                fragment.imports.add(f"import {imp}")
            else:
                fragment.imports.add(imp)

    return fragment


def _expandable_base(term_index: int, expandable_indices: set[int]) -> int | None:
    """Return the expandable-group base index ``term_index`` belongs to, else None.

    Expanded terminals sit at a stride from their base (e.g. base=2 for an index
    expanded to 2D gives indices 2, 4), so a terminal belongs to a group when it
    IS the base or lands an exact multiple of the stride beyond it.
    """
    for base in expandable_indices:
        if term_index == base or (
            term_index > base
            and (term_index - base) % max(len(expandable_indices), 1) == 0
        ):
            return base
    return None


# Errors are exceptions, so an error input with nothing wired to it holds no error.
NO_ERROR = "None"


def _catalog_default(
    terminal: PrimitiveTerminal, resolved: ResolvedPrimitive
) -> str | None:
    """The Python expression for what the catalog says an unwired input holds: its
    documented value, or the expression the primitive's own generator writes for
    its documented behaviour; None when the catalog gives neither."""
    if terminal.type == "ErrorCluster":
        return NO_ERROR
    if terminal.default is not None:
        return terminal_default_expression(terminal.default, terminal.type)
    if terminal.default_behavior is not None:
        from .ops import get_op_behavior

        return get_op_behavior(resolved, terminal.default_behavior)
    return None


def _reads_input(
    template: str,
    index: int,
    name: str,
    laid_out_slot: dict[int, int],
    expandable_indices: set[int],
) -> bool:
    """Whether the function's code reads the input at ``index``."""
    if _expandable_base(index, expandable_indices) is not None:
        return True
    return template_reads(
        template,
        f"in_{index}",
        f"in_{laid_out_slot.get(index, index)}",
        name,
        to_var_name(name),
    )


def _build_input_map(
    node: PrimitiveNode, ctx: CodeGenContext, resolved: ResolvedPrimitive | None
) -> dict[str, str]:
    """Build mapping from terminal names to resolved variable names.

    Uses primitive resolver terminal names when node terminals lack names.
    Matches by connector pane index (sparse — not sequential).
    An unwired input takes the catalog's documented value or the expression the
    function writes for its documented behaviour. One the code never reads is
    ``None``; one it reads with neither documented is a gap.
    """
    input_map = {}
    # Expandable groups: base_index → list of resolved values (in dimension order)
    expandable_groups: dict[int, list[str]] = {}

    # Build index → (name, default expression) dict from resolved terminals
    resolved_inputs: dict[int, tuple[str, str | None]] = {}
    behaviors: dict[int, DefaultBehavior | None] = {}
    # Inputs whose default is a documented behaviour, written in terms of the
    # function's other inputs (``in_<index>``).
    behavior_written: set[int] = set()
    expandable_indices: set[int] = set()
    if resolved and resolved.terminals:
        for rt in resolved.terminals:
            if rt.direction == "in":
                default = _catalog_default(rt, resolved)
                resolved_inputs[rt.index] = (rt.name or "", default)
                behaviors[rt.index] = rt.default_behavior
                if rt.default is None and default is not None:
                    behavior_written.add(rt.index)
                if getattr(rt, "expandable", False):
                    expandable_indices.add(rt.index)

    # The catalog terminal each node terminal repeats, for a node resized to its
    # array types.
    laid_out = (
        lay_out_node(resolved.terminals, node.terminals)
        if resolved and resolved.terminals
        else None
    )
    laid_out_slot = {t.index: t.slot for t in laid_out or []}

    # Check which terminal indices the template actually references
    template_str = str(resolved.python_code) if resolved else ""
    template_refs = set(re.findall(r"\bin_(\d+)\b", template_str))

    behavior_operands: list[str] = []
    for term in node.terminals:
        if term.direction != "input":
            continue

        # Skip error cluster inputs unless the template references
        # them (e.g. Merge Errors processes error data as values).
        # An error terminal belonging to an EXPANDABLE group is also data to
        # this primitive (Merge Errors folds N error clusters), but the template
        # reaches it via {expandable_inputs} rather than a literal in_N — so it
        # must survive the skip or every extra error input is silently dropped.
        if term.is_error_cluster:
            if (
                str(term.index) not in template_refs
                and _expandable_base(term.index, expandable_indices) is None
            ):
                continue

        term_id = term.id
        term_index = term.index
        term_name = term.name or ""
        default_value = None

        # Match by connector pane index (sparse dict lookup)
        if term_index in resolved_inputs:
            resolved_name, default_value = resolved_inputs[term_index]
            if not term_name:
                term_name = resolved_name
        elif laid_out_slot.get(term_index) in resolved_inputs:
            # An input added by resizing the node holds what the catalog terminal
            # it repeats holds.
            default_value = resolved_inputs[laid_out_slot[term_index]][1]

        # Resolve from context. A wired data input always has a variable (a constant
        # that is a gap is bound to None; its function raises where it starts); an
        # error cluster has none, errors being exceptions.
        value = ctx.resolve(term_id)
        if not value and ctx.is_wired(term_id) and not term.is_error_cluster:
            value = ctx.resolve(term_id, allow_none=True)
            if not value:
                raise CodeGenError(
                    f"The wired input {term_name or term_index!r} of "
                    f"{node.name!r} has no variable (from {_sources(ctx, term_id)})",
                    node,
                )
        if value:
            # Wired terminal with -1 index: resolution failure
            if term_index == -1:
                _raise_terminal_resolution(
                    node,
                    term,
                    resolved,
                    ctx,
                )
            resolved_value = value
        elif default_value is not None:
            resolved_value = default_value
        elif term.is_error_cluster:
            resolved_value = NO_ERROR
        elif _reads_input(
            template_str, term_index, term_name, laid_out_slot, expandable_indices
        ):
            function = resolved.name if resolved and resolved.name else node.name
            raise UnknownDefault(
                function or "a function",
                term_name or str(term_index),
                node,
                behaviors.get(laid_out_slot.get(term_index, term_index)),
            )
        else:
            resolved_value = "None"  # the code never reads it

        # Expandable terminal: collect into group by base index.
        # Expanded terminals have indices that are offset from the base
        # (e.g., base=2 for index, expanded 2D gives indices 2, 4).
        base_idx = _expandable_base(term_index, expandable_indices)
        matched_expandable = base_idx is not None
        if base_idx is not None:
            expandable_groups.setdefault(base_idx, []).append(resolved_value)

        if not matched_expandable:
            # Parenthesize a compound operand so its precedence survives the
            # string-level substitution into an operator template.
            operand = _paren_if_compound(resolved_value)
            if (
                not value
                and laid_out_slot.get(term_index, term_index) in behavior_written
            ):
                behavior_operands.append(operand)
            # Add index-based key so templates can use in_1, in_2 etc.
            input_map[f"in_{term_index}"] = operand
            if term_name:
                input_map[term_name] = operand
                input_map[to_var_name(term_name)] = operand

    # A documented behaviour written in terms of other inputs takes their operands.
    for operand in behavior_operands:
        written = re.sub(
            r"\bin_(\d+)\b",
            lambda m: input_map.get(m.group(0), m.group(0)),
            operand,
        )
        for key, current in list(input_map.items()):
            if current == operand:
                input_map[key] = written

    # Add expandable placeholders for template substitution.
    # Single group: {expandable_inputs} for backward compat.
    # Multiple groups: {name_values} per group (e.g., {index_values}).
    if len(expandable_groups) == 1:
        values = list(expandable_groups.values())[0]
        input_map["expandable_inputs"] = ", ".join(values)
    elif expandable_groups:
        for base_idx, values in expandable_groups.items():
            name = resolved_inputs.get(base_idx, ("expandable",))[0]
            key = to_var_name(name) + "_values"
            input_map[key] = ", ".join(values)
    # Also expose each expandable group under its bare terminal NAME, so a
    # template that references the input by name works in the common 1-D case
    # (Array Subset's `array[index:index + length]` -- index/length are
    # "expandable" for N-D, but a single dimension resolves to one value each).
    for base_idx, values in expandable_groups.items():
        name = resolved_inputs.get(base_idx, ("",))[0]
        if name and name not in input_map:
            joined = ", ".join(values)
            input_map[name] = joined
            input_map[to_var_name(name)] = joined

    # An input the code reads that the call does not have: the catalog numbers the
    # function's terminals differently from the call.
    if resolved and resolved.terminals:
        node_indices = {t.index for t in node.terminals}
        for rt in resolved.terminals:
            if (
                rt.direction == "in"
                and rt.index not in node_indices
                and template_reads(
                    template_str,
                    f"in_{rt.index}",
                    rt.name or "",
                    to_var_name(rt.name or ""),
                )
            ):
                raise TerminalNotOnCall(
                    resolved.prim_id or "",
                    resolved.name or "a function",
                    rt.name or str(rt.index),
                    rt.type,
                    node,
                )

    return input_map


def _detect_passthroughs(
    node: PrimitiveNode,
    hint: dict[str, str],
    input_map: dict[str, str],
    ctx: CodeGenContext,
    resolved: ResolvedPrimitive | None,
) -> tuple[dict[str, str], set[str]]:
    """Detect output terminals that are pure passthroughs.

    A passthrough is when the template expression is just `in_N` — the
    output IS the input. For these, bind the output terminal directly to
    the input variable instead of allocating a new name.

    Pairs each output with its expression by the output's ORDINAL among all
    outputs (matching _build_dict_hint), so an unwired earlier output does not
    shift the remaining expressions.
    """
    bindings: dict[str, str] = {}
    skip_ids: set[str] = set()

    exprs = [v for k, v in hint.items() if k not in ("_body", "_import")]
    ordinals = _output_index_ordinals(
        node, resolved, [k for k in hint if k not in ("_body", "_import")]
    )

    # Build resolved output name lookup
    resolved_outputs: dict[int, str] = {}
    if resolved and resolved.terminals:
        for rt in resolved.terminals:
            if rt.direction == "out":
                resolved_outputs[rt.index] = rt.name or ""

    for term in node.terminals:
        if term.direction != "output":
            continue
        if term.is_error_cluster:
            continue
        if not ctx.is_wired(term.id):
            continue
        term_name = term.name or ""
        if not term_name and term.index in resolved_outputs:
            term_name = resolved_outputs[term.index]
        expr_ordinal = ordinals.get(term.index)
        if expr_ordinal is None or expr_ordinal >= len(exprs):
            continue
        expr_template = exprs[expr_ordinal]

        # Case 1: bare input reference (in_N) — identity passthrough
        if re.match(r"^in_\d+$", expr_template):
            resolved_var = input_map.get(expr_template)
            if (
                resolved_var
                and resolved_var.isidentifier()
                and resolved_var not in ("None", "True", "False")
            ):
                bindings[term.id] = resolved_var
                skip_ids.add(term.id)
            continue

        # Case 2: single-use simple expression — inline into consumer
        # If this output has exactly one consumer and the expression is
        # simple (no function calls, no string literals), bind the
        # substituted expression directly. Turns
        # `equal_478 = x == y; if equal_478:` into `if x == y:`.
        # Skip if hint has _body — the _body creates variables that
        # output expressions depend on (e.g., Match Pattern's _m).
        if "_body" in hint:
            continue
        if ctx.graph is None:
            continue
        consumers = ctx.graph.outgoing_edges(term.id)
        if len(consumers) != 1:
            continue
        substituted = _substitute_template(expr_template, input_map, resolved)
        # Only inline simple expressions — no parens (function calls),
        # no quotes (string literals), no brackets (subscripts)
        if any(c in substituted for c in "('\"["):
            continue
        bindings[term.id] = substituted
        skip_ids.add(term.id)

    return bindings, skip_ids


def _output_index_ordinals(
    node: PrimitiveNode,
    resolved: ResolvedPrimitive | None,
    hint_keys: list[str] | None = None,
) -> dict[int, int]:
    """Map each output terminal INDEX to the position of its expression.

    Dict-format ``python_code`` names its expressions by output. When every key
    names an output of the catalog entry, each output takes the expression its
    name keys. Otherwise the expressions are taken to be in output-terminal order
    (the output list, sorted by connector-pane index), and the ordinal is the
    position in that list. Either way a wired output is paired independent of how
    many outputs are wired: pairing by position among *wired* outputs would shift
    expressions whenever an earlier output is left unwired.

    The expression list mirrors the resolved (JSON) output order, so prefer
    those indices; fall back to the node's own output terminals when the node
    carries no resolution.
    """
    if resolved and resolved.terminals and hint_keys:
        by_name = {
            to_var_name(rt.name or ""): rt.index
            for rt in resolved.terminals
            if rt.direction == "out"
        }
        keyed = [to_var_name(key) for key in hint_keys]
        if len(set(keyed)) == len(keyed) and all(k in by_name for k in keyed):
            return {by_name[key]: pos for pos, key in enumerate(keyed)}
    if resolved and resolved.terminals:
        out_indices = sorted(
            {rt.index for rt in resolved.terminals if rt.direction == "out"}
        )
    else:
        out_indices = sorted(
            {t.index for t in node.terminals if t.direction == "output"}
        )
    return {idx: pos for pos, idx in enumerate(out_indices)}


def _get_wired_outputs(
    node: PrimitiveNode,
    resolved: ResolvedPrimitive | None,
    ctx: CodeGenContext,
    skip_term_ids: set[str] | None = None,
) -> list[tuple[str, str, str, int]]:
    """Get (terminal_id, terminal_name, var_name, terminal_index) for wired outputs.

    Matches by connector pane index (sparse dict lookup).
    Terminal names in the primitive JSON should be valid Python identifiers.
    """
    # Build index → name dict from resolved terminals
    resolved_outputs: dict[int, str] = {}
    expandable_out_index: int | None = None
    if resolved and resolved.terminals:
        for rt in resolved.terminals:
            if rt.direction == "out":
                resolved_outputs[rt.index] = rt.name or ""
                if getattr(rt, "expandable", False):
                    expandable_out_index = rt.index

    outputs = []
    for term in node.terminals:
        if term.direction != "output":
            continue

        # Skip passthrough terminals (already bound by _detect_passthroughs)
        if skip_term_ids and term.id in skip_term_ids:
            continue

        # Skip error terminals — detected by actual type, not JSON labels.
        # Polymorphic prims reuse IDs with different semantics, so JSON
        # error labels can be wrong (e.g., 8003 is Variant To Data, not
        # Open/Create/Replace File).
        if term.is_error_cluster:
            continue

        # Skip unwired outputs — no consumer, no assignment needed
        if not ctx.is_wired(term.id):
            continue

        term_id = term.id
        term_index = term.index
        term_name = term.name or ""

        # Match by connector pane index (sparse dict lookup)
        if not term_name and term_index in resolved_outputs:
            term_name = resolved_outputs[term_index]

        # Expandable output: accept all terminals mapped to expandable index.
        # ``_{len(outputs)}`` distinguishes several expandable outputs WITHIN one
        # node; route through make_output_var so the name is also unique ACROSS
        # nodes (four single-output Index Array nodes would otherwise all be
        # ``element_0`` and clobber each other -- MD5's a/b/c/d extraction).
        if expandable_out_index is not None and term_index == expandable_out_index:
            base_name = resolved_outputs.get(expandable_out_index, "element")
            var_name = ctx.make_output_var(
                f"{to_var_name(base_name)}_{len(outputs)}",
                node.id,
                terminal_id=term_id,
            )
            outputs.append((term_id, term_name or base_name, var_name, term_index))
            continue

        # Output with -1 index and no name: resolution failure
        if term_index == -1 and not term_name:
            _raise_terminal_resolution(
                node,
                term,
                resolved,
                ctx,
            )

        var_name = (
            ctx.make_output_var(term_name, node.id, terminal_id=term_id)
            if term_name
            else f"out_{term_index}"
        )
        outputs.append((term_id, term_name, var_name, term_index))

    return outputs


def _build_dict_hint(
    node: PrimitiveNode,
    hint: dict[str, str],
    input_map: dict[str, str],
    wired_outputs: list[tuple[str, str, str, int]],
    ctx: CodeGenContext,
    resolved: ResolvedPrimitive | None,
    operand_ranks: Mapping[str, int] | None = None,
) -> CodeFragment:
    """Build code from dict-format hint.

    Dict format:
    - "_body": Optional statement to execute first
    - other keys: output_name → expression (authored in output-terminal order)
    """
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    imports: set[str] = set()

    # Handle _import (add to fragment imports)
    imp = hint.get("_import")
    if imp:
        imports.add(imp)

    # Handle _body (side effect statement)
    body = hint.get("_body")
    if body:
        body_substituted = _substitute_template(body, input_map, resolved)
        statements.append(parse_stmt(body_substituted))

    # Pair each wired output with its expression by the output's ORDINAL among
    # all outputs (dict expressions are authored in output-terminal order), not
    # by its position in the wired-only list — an unwired earlier output must
    # not shift the remaining expressions onto the wrong terminals.
    exprs = [v for k, v in hint.items() if k not in ("_body", "_import")]
    ordinals = _output_index_ordinals(
        node, resolved, [k for k in hint if k not in ("_body", "_import")]
    )
    for i, (term_id, term_name, var_name, term_index) in enumerate(wired_outputs):
        expr_idx = ordinals.get(term_index, i)
        if expr_idx < len(exprs):
            expr_substituted = _substitute_template(
                exprs[expr_idx], input_map, resolved
            )
            expr_ast = parse_expr(expr_substituted)
            if operand_ranks:
                expr_ast, _ = arrayify(expr_ast, operand_ranks)
            statements.append(build_assign(var_name, expr_ast))
            bindings[term_id] = var_name
        else:
            # More outputs than expressions — placeholder
            statements.append(build_assign(var_name, ast.Constant(value=None)))
            bindings[term_id] = var_name

    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


def _build_string_hint(
    hint: str,
    input_map: dict[str, str],
    wired_outputs: list[tuple[str, str, str, int]],
    ctx: CodeGenContext,
    resolved: ResolvedPrimitive | None,
    operand_ranks: Mapping[str, int] | None = None,
) -> CodeFragment:
    """Build code from string-format hint."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    imports: set[str] = set()

    # Strip assignment if present in hint
    expr = hint
    if "=" in expr and not any(op in expr for op in ["==", "!=", "<=", ">="]):
        eq_pos = expr.find("=")
        if eq_pos > 0 and expr[eq_pos - 1] not in "!<>" and expr[eq_pos + 1] != "=":
            expr = expr[eq_pos + 1 :].strip()

    # Strip trailing comment
    if "#" in expr:
        expr = expr[: expr.find("#")].strip()

    # Substitute inputs
    expr_substituted = _substitute_template(expr, input_map, resolved)

    expr_ast = parse_expr(expr_substituted)
    if operand_ranks:
        expr_ast, _ = arrayify(expr_ast, operand_ranks)

    # Assign to output variables
    if len(wired_outputs) == 1:
        term_id, _, var_name, _ = wired_outputs[0]
        statements.append(build_assign(var_name, expr_ast))
        bindings[term_id] = var_name
    elif len(wired_outputs) > 1:
        # Multiple outputs - unpack tuple
        var_names = [v for _, _, v, _ in wired_outputs]
        statements.append(
            ast.Assign(
                targets=[
                    ast.Tuple(
                        elts=[ast.Name(id=v, ctx=ast.Store()) for v in var_names],
                        ctx=ast.Store(),
                    )
                ],
                value=expr_ast,
            )
        )
        for term_id, _, var_name, _ in wired_outputs:
            bindings[term_id] = var_name
    else:
        # No outputs - just expression as statement
        statements.append(ast.Expr(value=expr_ast))

    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


def _substitute_template(
    template: str,
    input_map: dict[str, str],
    resolved: ResolvedPrimitive | None = None,
) -> str:
    """Substitute variable names in template string.

    input_map contains terminal names and index-based keys (in_1, in_2)
    mapped to resolved variable names from the dataflow graph.

    Templates should use terminal names or index-based refs (in_1, in_2)
    to reference inputs by their actual wire connections.

    Uses single-pass replacement to avoid double-substitution when
    input names overlap with resolved values (e.g., x→y and y→x).
    """
    # Build a combined pattern matching all names (longest first).
    # Tries {name} placeholder first (consumes braces), then bare \bname\b.
    names = sorted(
        [n for n in input_map if n],
        key=lambda x: -len(x),
    )
    if not names:
        return template

    patterns = []
    for n in names:
        escaped = re.escape(n)
        patterns.append(r"\{" + escaped + r"\}")  # {name} with braces
        # Bare name, but NOT an attribute access: a terminal named "path" or
        # "index" must not clobber the `path` in `os.path.isabs(in_1)` or a
        # `.index` method -- only standalone placeholder identifiers (`array`,
        # `index`, `length` in `array[index:index + length]`) are substituted.
        patterns.append(r"(?<![\w.])" + escaped + r"\b")
    combined = "|".join(patterns)

    def _replace(m: re.Match) -> str:
        text = m.group()
        # Strip braces if matched as {name} placeholder
        key = text.strip("{}") if text.startswith("{") else text
        return input_map[key] if key in input_map else text

    result = re.sub(combined, _replace, template)

    # Every input the template names has a terminal on the node (unwired ones
    # take their default in the input map), so a placeholder left over is a
    # template that names a terminal the node does not have.
    leftover = re.search(r"\bin_(\d+)\b", result)
    if leftover:
        name = resolved.name if resolved else "a primitive"
        raise CodeGenError(
            f"The template of {name!r} names {leftover.group()}, which the node "
            "has no terminal for"
        )

    return result


def _sources(ctx: CodeGenContext, terminal_id: str) -> str:
    """The nodes whose terminals wire into ``terminal_id``, in LabVIEW's words,
    for an error message."""
    assert ctx.graph is not None
    found = []
    for edge in ctx.graph.incoming_edges(terminal_id):
        source = ctx.graph.get_graph_node(edge.node_id)
        if source is None:
            continue
        found.append(f"{source.display_name}, terminal {uid_of(edge.terminal_id)}")
    return ", ".join(found) or "nothing"


def _terminal_signature(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> list[dict[str, str | int | bool | None]]:
    """The FULL connector pane (every terminal, wired AND unwired) as the
    resolution diagnostics carry it — the identity a resolver matches against."""
    return [
        {
            "index": term.index,
            "direction": term.direction,
            "name": term.name,
            "type": term.lv_type.underlying_type if term.lv_type else None,
            "wired": ctx.is_wired(term.id),
        }
        for term in node.terminals
    ]


def _unresolved(
    node: PrimitiveNode,
    prim_id: int | None,
    ctx: CodeGenContext,
    reason: str | None = None,
    placeholder_name: str | None = None,
) -> PrimitiveResolutionNeeded:
    """The gap for ``node``, a primitive with no implementation (known by name
    when ``placeholder_name`` is given) or a node with no primResID.

    The FULL connector pane — node.terminals carries every terminal the heap
    serialized, wired AND unwired, each with its declared type. Wired status is
    marked so the resolver can identify by the whole pane, not just the
    terminals that happen to have wires in this VI."""
    return PrimitiveResolutionNeeded(
        prim_id=prim_id,
        prim_name=placeholder_name or node.name or "unknown",
        terminals=_terminal_signature(node, ctx),
        vi_name=ctx.vi_name,
        qualified_vi_name=ctx.qualified_vi_name,
        node_kind=(
            (get_display_name(node.node_type) if node.node_type else "Node")
            if prim_id is None
            else None
        ),
        reason=reason,
        placeholder=placeholder_name is not None,
    )


def raise_placeholder(
    node: PrimitiveNode,
    resolved: ResolvedPrimitive,
    ctx: CodeGenContext,
) -> NoReturn:
    """A primitive known by name with no implementation is a gap."""
    name = resolved.name or node.name or "unknown"
    raise _unresolved(node, node.prim_id, ctx, placeholder_name=name)


def raise_unknown(
    node: PrimitiveNode,
    prim_id: int | None,
    ctx: CodeGenContext,
    reason: str | None = None,
) -> NoReturn:
    """An unknown primitive (or a node with no primResID) is a gap."""
    raise _unresolved(node, prim_id, ctx, reason)


def _raise_terminal_resolution(
    node: PrimitiveNode,
    term: Terminal,
    resolved: ResolvedPrimitive | None,
    ctx: CodeGenContext,
) -> None:
    """Raise TerminalResolutionNeeded for a specific unresolved terminal.

    The primitive definition exists but this terminal's index is -1.
    Direction and type come from the terminal itself — never fabricated.
    """
    # Filter available resolver terminals to same direction, unassigned
    direction = "in" if term.direction == "input" else "out"
    assigned_indices = {t.index for t in node.terminals if t.index >= 0}
    avail = [
        {"index": rt.index, "name": rt.name, "type": rt.type}
        for rt in (resolved.terminals if resolved else [])
        if rt.direction == direction and rt.index not in assigned_indices
    ]
    raise TerminalResolutionNeeded(
        prim_id=node.prim_id or 0,
        prim_name=node.name or "unknown",
        terminal_direction=term.direction,
        terminal_type=(term.lv_type.underlying_type if term.lv_type else None),
        available=avail,
        vi_name=ctx.vi_name,
    )
