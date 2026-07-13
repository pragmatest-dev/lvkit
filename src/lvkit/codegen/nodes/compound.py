"""Code generator for compound operations (cpdArith, aBuild, aInit, aReplace,
aInsert, aReshape)."""

from __future__ import annotations

import ast

from lvkit.models import LVType, PrimitiveOperation, Terminal

from ..ast_utils import build_assign, parse_expr, parse_stmt, to_var_name, uint_mask
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import CodeGenError

_MUTABLE_KINDS = ("array", "cluster")


def _is_mutable(term: Terminal | None) -> bool:
    """True if the terminal's LabVIEW type is a mutable aggregate.

    Array/cluster values need an independent copy per array slot;
    sharing one Python object reference across slots (e.g. `[x] * n`)
    would let mutating one slot corrupt every other slot.
    """
    return bool(
        term is not None
        and term.lv_type is not None
        and term.lv_type.kind in _MUTABLE_KINDS
    )


def _is_boolean(term: object) -> bool:
    """Check whether a terminal carries a LabVIEW Boolean value."""
    lv_type = getattr(term, "lv_type", None)
    return bool(lv_type is not None and lv_type.underlying_type == "Boolean")


def _is_integer(term: object) -> bool:
    """Check whether a terminal carries a LabVIEW integer value."""
    lv_type = getattr(term, "lv_type", None)
    ut = (lv_type.underlying_type or "") if lv_type is not None else ""
    return ut.startswith("NumInt") or ut.startswith("NumUInt")


def _invert_expr(
    expr: ast.expr, boolean: bool, lv_type: LVType | None = None
) -> ast.expr:
    """Wrap an expression with the terminal's "Not" invert.

    Boolean -> logical ``not``; integer -> bitwise ``~``, masked to the type
    width for unsigned ints (Python's ``~`` would otherwise yield a negative)."""
    if boolean:
        return ast.UnaryOp(op=ast.Not(), operand=expr)
    inv: ast.expr = ast.UnaryOp(op=ast.Invert(), operand=expr)
    mask = uint_mask(lv_type)
    if mask is not None:
        inv = ast.BinOp(
            left=inv, op=ast.BitAnd(), right=ast.Constant(value=mask)
        )
    return inv


def generate_compound_arith(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for compound arithmetic (cpdArith).

    Combines multiple inputs with a single operation (OR, AND, ADD, MULTIPLY,
    XOR). Each input (and the output) may independently be inverted
    ("Not"). When the terminals are Boolean, ``add``/``multiply`` translate
    to logical OR/AND respectively.
    """
    terminals = node.terminals
    operation = node.operation or "or"

    inputs = [t for t in terminals if t.direction == "input"]
    outputs = [t for t in terminals if t.direction == "output"]

    if not outputs:
        return CodeFragment()

    output_term = outputs[0]
    output_id = output_term.id
    boolean = _is_boolean(output_term) or any(_is_boolean(t) for t in inputs)
    # LabVIEW And/Or/Xor are bitwise on integer operands. Only go bitwise when
    # an operand is KNOWN to be an integer; unknown/boolean stays logical (both
    # idiomatic and behaviour-preserving).
    bitwise = not boolean and (
        _is_integer(output_term) or any(_is_integer(t) for t in inputs)
    )

    # Boolean-context translation: add -> or, multiply -> and.
    if boolean:
        if operation == "add":
            operation = "or"
        elif operation == "multiply":
            operation = "and"

    sorted_inputs = sorted(inputs, key=lambda t: t.index)
    input_exprs = []
    input_names = []
    for inp in sorted_inputs:
        val = ctx.resolve(inp.id)
        if val:
            expr = parse_expr(val)
            if inp.inverted:
                expr = _invert_expr(expr, boolean, inp.lv_type)
                val = ast.unparse(expr)
            input_exprs.append(val)
            input_names.append(val)

    var_name = _make_arith_var_name(operation, input_names)

    if not input_exprs:
        default_value = False if operation in ("or", "and") else 0
        stmt = build_assign(var_name, ast.Constant(value=default_value))
        return CodeFragment(
            statements=[stmt],
            bindings={output_id: var_name},
        )

    if len(input_exprs) == 1:
        combined = parse_expr(input_exprs[0])
        if output_term.inverted:
            combined = _invert_expr(combined, boolean, output_term.lv_type)
            stmt = build_assign(var_name, combined)
            return CodeFragment(
                statements=[stmt],
                bindings={output_id: var_name},
            )
        return CodeFragment(bindings={output_id: input_exprs[0]})

    combined = parse_expr(input_exprs[0])

    if operation == "or":
        for expr_str in input_exprs[1:]:
            right = parse_expr(expr_str)
            # integer operands -> BITWISE or (LabVIEW polymorphism); else logical
            if bitwise:
                combined = ast.BinOp(left=combined, op=ast.BitOr(), right=right)
            else:
                combined = ast.BoolOp(op=ast.Or(), values=[combined, right])
    elif operation == "and":
        for expr_str in input_exprs[1:]:
            right = parse_expr(expr_str)
            if bitwise:
                combined = ast.BinOp(left=combined, op=ast.BitAnd(), right=right)
            else:
                combined = ast.BoolOp(op=ast.And(), values=[combined, right])
    elif operation == "add":
        for expr_str in input_exprs[1:]:
            combined = ast.BinOp(
                left=combined,
                op=ast.Add(),
                right=parse_expr(expr_str),
            )
    elif operation == "multiply":
        for expr_str in input_exprs[1:]:
            combined = ast.BinOp(
                left=combined,
                op=ast.Mult(),
                right=parse_expr(expr_str),
            )
    elif operation == "xor":
        for expr_str in input_exprs[1:]:
            right = parse_expr(expr_str)
            if boolean:
                combined = ast.Compare(
                    left=combined,
                    ops=[ast.NotEq()],
                    comparators=[right],
                )
            else:
                combined = ast.BinOp(
                    left=combined,
                    op=ast.BitXor(),
                    right=right,
                )
    else:
        for expr_str in input_exprs[1:]:
            combined = ast.BoolOp(
                op=ast.Or(),
                values=[combined, parse_expr(expr_str)],
            )

    if output_term.inverted:
        combined = _invert_expr(combined, boolean, output_term.lv_type)

    stmt = build_assign(var_name, combined)
    return CodeFragment(
        statements=[stmt],
        bindings={output_id: var_name},
    )


def _make_arith_var_name(operation: str, input_names: list[str]) -> str:
    """Generate a semantic variable name for compound arithmetic."""
    if operation in ("or", "and"):
        stop_keywords = {
            "stop", "done", "exit", "quit", "end", "finish", "complete"
        }
        for name in input_names:
            if any(kw in name.lower() for kw in stop_keywords):
                return "should_stop"
        return "should_stop"

    if operation == "add" and input_names:
        return "total"

    return "combined"


def generate_array_build(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for array building (aBuild).

    LabVIEW's Build Array concatenates/appends inputs:
    - Array inputs are concatenated directly (a + b)
    - Scalar inputs are wrapped in a list ([x]) before concatenation

    This produces `existing + [new_element]` rather than `[existing, new_element]`.
    """
    terminals = node.terminals

    inputs = [t for t in terminals if t.direction == "input"]
    outputs = [t for t in terminals if t.direction == "output"]

    if not outputs:
        return CodeFragment()

    output_term = outputs[0]
    output_id = output_term.id

    parts: list[ast.expr] = []
    input_names: list[str] = []
    for inp in sorted(inputs, key=lambda t: t.index):
        val = ctx.resolve(inp.id)
        if val:
            input_names.append(val)
            is_array = inp.lv_type is not None and inp.lv_type.kind == "array"
            if is_array:
                # Concatenate array-typed inputs directly
                parts.append(parse_expr(val))
            else:
                # Wrap scalar/cluster inputs as a single-element list
                parts.append(ast.List(elts=[parse_expr(val)], ctx=ast.Load()))
        else:
            parts.append(ast.List(elts=[ast.Constant(value=None)], ctx=ast.Load()))

    var_name = _make_array_var_name(input_names)

    if not parts:
        expr: ast.expr = ast.List(elts=[], ctx=ast.Load())
    elif len(parts) == 1:
        expr = parts[0]
    else:
        expr = parts[0]
        for part in parts[1:]:
            expr = ast.BinOp(left=expr, op=ast.Add(), right=part)

    stmt = build_assign(var_name, expr)

    return CodeFragment(
        statements=[stmt],
        bindings={output_id: var_name},
    )


def _make_array_var_name(input_names: list[str]) -> str:
    """Generate a semantic variable name for array building."""
    if not input_names:
        return "items"

    first = input_names[0]
    base = to_var_name(first).rstrip("0123456789_")

    if base and len(base) > 2:
        common = all(base in to_var_name(n) for n in input_names[:3])
        if common:
            if base.endswith("y") and not base.endswith(("ay", "ey", "oy", "uy")):
                return base[:-1] + "ies"
            elif base.endswith(("s", "x", "z", "ch", "sh")):
                return base + "es"
            else:
                return base + "s"

    return "items"


def generate_array_init(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for Initialize Array (aInit).

    LabVIEW's Initialize Array builds an N-dimensional array from one
    "element" input (terminal index 0) and one "dimension size" input per
    dimension (terminal index >= 2, ascending index = outermost to
    innermost, matching LabVIEW's terminal order when the node is resized
    to add dimensions). Uses nested list comprehensions rather than
    `[element] * n`: at 2-D+, `[[x] * d1] * d0` would alias every row to
    the SAME inner list object, so mutating one row would corrupt every
    row. A comprehension re-evaluates (and rebuilds) each row.

    A mutable element (array/cluster) is independently deep-copied into
    every slot, matching LabVIEW's copy-on-write array-of-values
    semantics; scalar elements share the (immutable) value directly.
    """
    terminals = node.terminals
    inputs = sorted(
        (t for t in terminals if t.direction == "input"), key=lambda t: t.index,
    )
    outputs = [t for t in terminals if t.direction == "output"]
    if not outputs:
        return CodeFragment()
    output_term = outputs[0]

    element_term = next((t for t in inputs if t.index < 2), None)
    dim_terms = [t for t in inputs if t.index >= 2]

    imports: set[str] = set()

    elem_val = ctx.resolve(element_term.id) if element_term is not None else None
    elem_expr: ast.expr
    if elem_val:
        elem_expr = parse_expr(elem_val)
        if _is_mutable(element_term):
            imports.add("import copy")
            elem_expr = ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id="copy", ctx=ast.Load()),
                    attr="deepcopy",
                    ctx=ast.Load(),
                ),
                args=[elem_expr],
                keywords=[],
            )
    else:
        elem_expr = ast.Constant(value=None)

    dim_exprs: list[ast.expr] = []
    for dt in dim_terms:
        val = ctx.resolve(dt.id)
        dim_exprs.append(parse_expr(val) if val else ast.Constant(value=0))

    if not dim_exprs:
        expr: ast.expr = ast.List(elts=[], ctx=ast.Load())
    else:
        expr = elem_expr
        for dim_expr in reversed(dim_exprs):
            expr = ast.ListComp(
                elt=expr,
                generators=[
                    ast.comprehension(
                        target=ast.Name(id="_", ctx=ast.Store()),
                        iter=ast.Call(
                            func=ast.Name(id="range", ctx=ast.Load()),
                            args=[dim_expr],
                            keywords=[],
                        ),
                        ifs=[],
                        is_async=0,
                    )
                ],
            )

    var_name = ctx.make_output_var(
        "initialized_array", node.id, terminal_id=output_term.id,
    )
    stmt = build_assign(var_name, expr)

    return CodeFragment(
        statements=[stmt],
        bindings={output_term.id: var_name},
        imports=imports,
    )


def generate_array_replace(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for Replace Array Subset (aReplace).

    Per LabVIEW's connector pane: terminal 0 = array in, 1 = output
    array, 2 = new element (or subarray), 3 = index.

    Replace Array Subset never changes the array's length: a replacement
    that would extend past the original array's bounds is clipped to
    fit, and an index at/past the end of the array is a no-op (NI docs:
    "the sub array is cropped to fit ... will not insert elements that
    are outside the bounds of the original array"). This handles both
    forms in one formula:
    - scalar "new element" -> replaces exactly the one element at index
    - array-typed "new element" (subset) -> replaces a contiguous run
      starting at index, clipped to the array's existing length
    """
    by_index = {t.index: t for t in node.terminals}
    array_term = by_index.get(0)
    new_elem_term = by_index.get(2)
    index_term = by_index.get(3)
    outputs = [t for t in node.terminals if t.direction == "output"]
    if not outputs or array_term is None:
        return CodeFragment()
    output_term = outputs[0]

    array_val = ctx.resolve(array_term.id) or "[]"
    index_val = ctx.resolve(index_term.id) if index_term else None
    index_val = index_val or "0"

    is_subset = _is_array_type(new_elem_term)
    new_elem_val = ctx.resolve(new_elem_term.id) if new_elem_term else None
    if new_elem_val is None:
        new_elem_val = "[]" if is_subset else "None"
    subset_str = new_elem_val if is_subset else f"[{new_elem_val}]"

    body_str = (
        f"_n = max(0, min(len({subset_str}), len({array_val}) - ({index_val})))"
    )
    expr_str = (
        f"{array_val}[:{index_val}] + ({subset_str})[:_n] "
        f"+ {array_val}[({index_val}) + _n:]"
    )

    var_name = ctx.make_output_var(
        "output_array", node.id, terminal_id=output_term.id,
    )
    body_stmt = parse_stmt(body_str)
    assign_stmt = build_assign(var_name, parse_expr(expr_str))

    return CodeFragment(
        statements=[body_stmt, assign_stmt],
        bindings={output_term.id: var_name},
    )


def _is_array_type(term: Terminal | None) -> bool:
    """True if the terminal's LabVIEW type is an array."""
    return bool(
        term is not None and term.lv_type is not None and term.lv_type.kind == "array"
    )


def generate_array_insert(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for Insert Into Array (aInsert).

    Per the real connector-pane layout (verified against Reserve cDAQ.vi's
    aInsDCO terminals -- terminal 2 resolves to a plain numeric type and
    terminal 3 resolves to the array's own element type, e.g. a String
    array's terminal 3 is String): terminal 0 = array in, 1 = output
    array; among the remaining INPUT terminals sorted by ascending index,
    the LAST one is always "new element/subarray" and every terminal
    before it is an "index" input (LabVIEW shows one index terminal per
    array dimension when the node is resized, but "you can wire only one
    index input" at a time -- NI docs, Insert Into Array). This also
    matches the NI docs' own Inputs/Outputs prose order (array, index,
    new element/subarray, output array).

    NOTE: primitives.json's aInsert template labels terminal 2 "element"
    and terminal 3 "index" (with a "-1" default on the index) -- that
    template is unused (this dedicated handler intercepts before it) and
    is transposed relative to the terminal ROLES actually observed in
    real dataflow; left as-is (dead code) rather than edited, matching
    how aInit/aReplace's equally-unused templates were left untouched.

    Only the 1-D case (a single index terminal) is verified against real
    sample data; N-D insert along an inner axis (more than one index
    terminal present) is out of scope here and raises a diagnostic
    instead of guessing.

    Semantics (NI docs): if no index is wired, the new element/subarray
    is appended to the end of the array. If the (wired) index is beyond
    the array's current length, the function does NOT insert anything --
    unlike Replace Array Subset, this is a true no-op, not a clip.
    """
    inputs = sorted(
        (t for t in node.terminals if t.direction == "input"), key=lambda t: t.index,
    )
    outputs = [t for t in node.terminals if t.direction == "output"]
    if not outputs or len(inputs) < 2:
        return CodeFragment()
    output_term = outputs[0]

    array_term = inputs[0]
    index_terms = inputs[1:-1]
    element_term = inputs[-1]

    if len(index_terms) > 1:
        raise CodeGenError(
            f"Insert Into Array with {len(index_terms)} index terminals "
            "(N-D array, inner-axis insert) is not supported -- only the "
            "1-D case (a single index terminal) is verified against real "
            "sample data.",
            node,
        )

    array_val = ctx.resolve(array_term.id) or "[]"
    index_term = index_terms[0] if index_terms else None
    index_val = ctx.resolve(index_term.id) if index_term else None
    idx_str = index_val if index_val else f"len({array_val})"

    is_subset = _is_array_type(element_term)
    elem_val = ctx.resolve(element_term.id)
    if elem_val is None:
        elem_val = "[]" if is_subset else "None"
    items_str = elem_val if is_subset else f"[{elem_val}]"

    expr_str = (
        f"({array_val}[:{idx_str}] + ({items_str}) + {array_val}[{idx_str}:]) "
        f"if 0 <= ({idx_str}) <= len({array_val}) else list({array_val})"
    )

    var_name = ctx.make_output_var(
        "output_array", node.id, terminal_id=output_term.id,
    )
    stmt = build_assign(var_name, parse_expr(expr_str))

    return CodeFragment(
        statements=[stmt],
        bindings={output_term.id: var_name},
    )


def generate_array_reshape(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for Reshape Array (aReshape).

    Reshape Array flattens ALL elements of the source array (regardless
    of its own dimensionality) in row-major order, then re-chunks that
    flat sequence into the shape given by the "dimension size" input(s)
    -- one input per dimension of the OUTPUT, ascending index = outermost
    to innermost (same convention as aInit's dimension terminals).
    Verified against a real 2-D-to-1-D reshape in OpenG's "1D Array of
    VArrays to MultiD Array.vi" (2-D source array, ONE dimension-size
    terminal, 1-D output) -- confirming dimension-terminal COUNT tracks
    the requested OUTPUT rank, not the source's rank.

    If the source has fewer elements than the requested shape needs,
    LabVIEW pads with the array element type's default (zero) value; if
    it has more, the extras are truncated. This is implemented generically
    for any source rank (flattening is a straightforward nested
    comprehension for any known depth) but only 1-D and 2-D TARGET shapes
    are implemented -- higher-rank targets raise a diagnostic instead of
    guessing at the reshape-nesting code, since no 3-D+ sample exists to
    verify against.
    """
    inputs = sorted(
        (t for t in node.terminals if t.direction == "input"), key=lambda t: t.index,
    )
    outputs = [t for t in node.terminals if t.direction == "output"]
    if not outputs or not inputs:
        return CodeFragment()
    output_term = outputs[0]

    array_term = inputs[0]
    dim_terms = [t for t in inputs if t.index >= 2]
    target_ndim = len(dim_terms)
    if target_ndim == 0:
        raise CodeGenError(
            "Reshape Array has no dimension-size terminal to reshape "
            f"into (node {node.id}).",
            node,
        )
    if target_ndim > 2:
        raise CodeGenError(
            f"Reshape Array with {target_ndim} target dimensions is not "
            "supported -- only 1-D and 2-D target shapes are implemented "
            "(no 3-D+ sample exists to verify the reshape-nesting code "
            "against).",
            node,
        )

    lv_type = array_term.lv_type
    if lv_type is None or lv_type.kind != "array" or not lv_type.dimensions:
        raise CodeGenError(
            "Reshape Array requires the source array's resolved LabVIEW "
            f"type (to know its dimensionality) for node {node.id}, but "
            "type info is unavailable for this terminal.",
            node,
        )
    source_ndim = lv_type.dimensions

    array_val = ctx.resolve(array_term.id) or "[]"
    flat_expr = _build_flatten_expr(array_val, source_ndim)

    dim_exprs: list[ast.expr] = []
    for dt in dim_terms:
        val = ctx.resolve(dt.id)
        dim_exprs.append(parse_expr(val) if val else ast.Constant(value=0))

    default_expr = _reshape_default_element(lv_type.element_type)

    flat_var = "_flat"
    flat_stmt = build_assign(flat_var, flat_expr)

    needed_expr = dim_exprs[0]
    for dim_expr in dim_exprs[1:]:
        needed_expr = ast.BinOp(left=needed_expr, op=ast.Mult(), right=dim_expr)
    needed_var = "_needed"
    needed_stmt = build_assign(needed_var, needed_expr)

    pad_expr_str = (
        f"{flat_var}[:{needed_var}] + "
        f"[{ast.unparse(default_expr)}] * max(0, {needed_var} - len({flat_var}))"
    )
    pad_stmt = build_assign(flat_var, parse_expr(pad_expr_str))

    var_name = ctx.make_output_var(
        "reshaped_array", node.id, terminal_id=output_term.id,
    )

    if target_ndim == 1:
        final_stmt = build_assign(var_name, ast.Name(id=flat_var, ctx=ast.Load()))
    else:
        d0_str = ast.unparse(dim_exprs[0])
        d1_str = ast.unparse(dim_exprs[1])
        expr_str = (
            f"[{flat_var}[_ri*{d1_str}:(_ri+1)*{d1_str}] "
            f"for _ri in range({d0_str})]"
        )
        final_stmt = build_assign(var_name, parse_expr(expr_str))

    return CodeFragment(
        statements=[flat_stmt, needed_stmt, pad_stmt, final_stmt],
        bindings={output_term.id: var_name},
    )


def _build_flatten_expr(array_val: str, source_ndim: int) -> ast.expr:
    """Row-major-flatten a (possibly N-D nested-list) array expression.

    LabVIEW nested arrays already flatten in traversal (row-major) order
    when walked as Python nested lists, so a chain of ``for`` clauses over
    successive nesting levels reproduces LabVIEW's flatten order exactly.
    """
    base = parse_expr(array_val)
    if source_ndim <= 1:
        return ast.Call(
            func=ast.Name(id="list", ctx=ast.Load()), args=[base], keywords=[],
        )

    loop_vars = [f"_lv{i}" for i in range(source_ndim)]
    generators = []
    for i, var in enumerate(loop_vars):
        iter_expr = base if i == 0 else ast.Name(id=loop_vars[i - 1], ctx=ast.Load())
        generators.append(
            ast.comprehension(
                target=ast.Name(id=var, ctx=ast.Store()),
                iter=iter_expr,
                ifs=[],
                is_async=0,
            )
        )
    return ast.ListComp(
        elt=ast.Name(id=loop_vars[-1], ctx=ast.Load()),
        generators=generators,
    )


def _reshape_default_element(elem_type: LVType | None) -> ast.expr:
    """Python literal AST for a LabVIEW element type's default (zero) value.

    Used to pad a reshaped array when the source has fewer elements than
    the requested shape -- LabVIEW pads with the array element type's
    default rather than growing/erroring.
    """
    if elem_type is None:
        return ast.Constant(value=None)

    kind = elem_type.kind
    underlying = elem_type.underlying_type

    if kind == "array":
        return ast.List(elts=[], ctx=ast.Load())
    if kind == "cluster":
        return ast.Constant(value=None)
    if kind in ("enum", "ring"):
        return ast.Constant(value=0)
    if kind == "primitive":
        if underlying == "String":
            return ast.Constant(value="")
        if underlying == "Boolean":
            return ast.Constant(value=False)
        if underlying in (
            "NumInt8", "NumInt16", "NumInt32", "NumInt64",
            "NumUInt8", "NumUInt16", "NumUInt32", "NumUInt64",
        ):
            return ast.Constant(value=0)
        if underlying in ("NumFloat32", "NumFloat64"):
            return ast.Constant(value=0.0)

    return ast.Constant(value=None)
