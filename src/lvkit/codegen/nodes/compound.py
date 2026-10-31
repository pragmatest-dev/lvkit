"""Code generator for compound operations (cpdArith, aBuild, aInit, aReplace,
aInsert, aReshape)."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.lv_type_codes import is_boolean_type_name, is_string_type_name
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.python_numeric import UNSIGNED_INTEGERS, fixed_width

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..native_array import replace_subarray_statements
from .terminal_roles import laid_out_roles

_MUTABLE_KINDS = (LVTypeKind.ARRAY, LVTypeKind.CLUSTER)

# The five Compound Arithmetic modes we generate code for. The parser maps a
# node's objFlags mode enum (bits 16-18) to one of these, or to "unsupported"
# for a code outside the enum -- codegen fails loud on anything outside this
# set rather than silently defaulting to OR.
_CPD_OPERATIONS = frozenset({"add", "or", "and", "multiply", "xor"})


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
    return bool(lv_type is not None and is_boolean_type_name(lv_type.underlying_type))


def _is_integer(term: object) -> bool:
    """Check whether a terminal carries a LabVIEW integer value."""
    lv_type = getattr(term, "lv_type", None)
    ut = (lv_type.underlying_type or "") if lv_type is not None else ""
    return ut.startswith("NumInt") or ut.startswith("NumUInt")


def _invert_expr(
    expr: ast.expr,
    boolean: bool,
    operation: str,
    lv_type: LVType | None,
    ctx: CodeGenContext,
) -> ast.expr:
    """Wrap an expression with a Compound-Arithmetic terminal's "Invert".

    The invert's meaning is mode-dependent (per NI's Compound Arithmetic docs):
    - Boolean operand -> logical ``not`` (any mode);
    - Add mode -> arithmetic negation (``-x``), i.e. subtraction;
    - Multiply mode -> reciprocal (``1 / x``);
    - AND/OR/XOR mode -> bitwise ``~``, wrapped to the type width with ``ctypes``
      for unsigned ints (Python's ``~`` would otherwise yield a negative).
    """
    if boolean:
        return ast.UnaryOp(op=ast.Not(), operand=expr)
    if operation == "add":
        return ast.UnaryOp(op=ast.USub(), operand=expr)
    if operation == "multiply":
        return ast.BinOp(left=ast.Constant(value=1), op=ast.Div(), right=expr)
    inv: ast.expr = ast.UnaryOp(op=ast.Invert(), operand=expr)
    underlying = (lv_type.underlying_type or "") if lv_type else ""
    if underlying in UNSIGNED_INTEGERS:
        ctx.add_import("import ctypes")
        return parse_expr(fixed_width(ast.unparse(inv), underlying))
    return inv


def generate_compound_arith(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for compound arithmetic (cpdArith).

    Combines multiple inputs with a single operation (OR, AND, ADD, MULTIPLY,
    XOR). Each input (and the output) may independently be inverted
    ("Not"). When the terminals are Boolean, ``add``/``multiply`` translate
    to logical OR/AND respectively.
    """
    terminals = node.terminals
    operation = node.operation or "add"
    if operation not in _CPD_OPERATIONS:
        raise ConversionGap(
            f"Compound Arithmetic: operation {operation!r} is not supported. "
            "Its objFlags mode enum (bits 16-18) was outside the known set "
            "add/multiply/and/or/xor. Add the code to "
            "CpdArithHandler.OPERATIONS once verified from real dataflow.",
            node,
        )

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
    for inp in sorted_inputs:
        val = ctx.wired_input(inp.id, node)
        if val:
            expr = parse_expr(val)
            if inp.inverted:
                expr = _invert_expr(expr, boolean, operation, inp.lv_type, ctx)
                val = ast.unparse(expr)
            input_exprs.append(val)

    var_name = ctx.make_output_var(
        _arith_base_name(operation, output_term), node.id, terminal_id=output_id
    )

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
            combined = _invert_expr(
                combined, boolean, operation, output_term.lv_type, ctx
            )
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
    else:  # unreachable: operation validated against _CPD_OPERATIONS above
        raise CodeGenError(
            f"Compound Arithmetic: no combiner for operation {operation!r}.",
            node,
        )

    if output_term.inverted:
        combined = _invert_expr(combined, boolean, operation, output_term.lv_type, ctx)

    stmt = build_assign(var_name, combined)
    return CodeFragment(
        statements=[stmt],
        bindings={output_id: var_name},
    )


def _arith_base_name(operation: str, output_term: Terminal) -> str:
    """A base variable-name stem for a compound-arithmetic output.

    Passed to ``ctx.make_output_var``, which uniquifies it on collision and
    defers to output-tunnel naming when the output feeds a structure boundary
    — so this only needs a reasonable, operation-derived stem, never a fixed
    string. Prefer the output terminal's own name when it carries one.
    """
    name = (output_term.name or "").strip()
    if name:
        return name
    return {"add": "total", "multiply": "product"}.get(operation, "combined")


def generate_array_build(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for array building (aBuild).

    LabVIEW's Build Array concatenates/appends inputs: an array input contributes
    its elements and a scalar input is one element, in terminal order. That is
    one list display, ``[*existing, new_element]``. (A ``+`` between two arrays
    would read as LabVIEW's element-wise Add to the rewrite that broadcasts
    arithmetic over arrays, which has no way to tell the two apart.)
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
        val = ctx.wired_input(inp.id, node)
        if val:
            input_names.append(val)
            is_array = inp.lv_type is not None and inp.lv_type.kind == LVTypeKind.ARRAY
            if is_array:
                # An array input contributes its elements
                parts.append(ast.Starred(value=parse_expr(val), ctx=ast.Load()))
            else:
                # A scalar or cluster input is one element
                parts.append(parse_expr(val))
        else:
            parts.append(ast.Constant(value=None))

    var_name = ctx.make_output_var(
        _make_array_var_name(input_names), node.id, terminal_id=output_id
    )

    expr: ast.expr = ast.List(elts=parts, ctx=ast.Load())

    stmt = build_assign(var_name, expr)

    return CodeFragment(
        statements=[stmt],
        bindings={output_id: var_name},
    )


def generate_concat_strings(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for Concatenate Strings (``concat``).

    LabVIEW's Concatenate Strings joins all input strings, in terminal order,
    into a single output string. A 1D-array-of-strings input contributes all
    of its elements concatenated together (``"".join(arr)``); a scalar string
    input contributes its own value. With no inputs the result is ``""``.
    """
    inputs = [t for t in node.terminals if t.direction == "input"]
    outputs = [t for t in node.terminals if t.direction == "output"]

    if not outputs:
        return CodeFragment()

    output_id = outputs[0].id

    parts: list[ast.expr] = []
    for inp in sorted(inputs, key=lambda t: t.index):
        val = ctx.wired_input(inp.id, node)
        if not val:
            continue
        is_array = inp.lv_type is not None and inp.lv_type.kind == LVTypeKind.ARRAY
        if is_array:
            # A 1D array of strings concatenates its elements.
            parts.append(parse_expr(f'"".join({val})'))
        else:
            parts.append(parse_expr(val))

    if not parts:
        expr: ast.expr = ast.Constant(value="")
    else:
        expr = parts[0]
        for part in parts[1:]:
            expr = ast.BinOp(left=expr, op=ast.Add(), right=part)

    out_name = outputs[0].name
    var_name = ctx.make_output_var(
        out_name or "concatenated_string", node.id, terminal_id=output_id
    )

    return CodeFragment(
        statements=[build_assign(var_name, expr)],
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
    node: PrimitiveNode,
    ctx: CodeGenContext,
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
        (t for t in terminals if t.direction == "input"),
        key=lambda t: t.index,
    )
    outputs = [t for t in terminals if t.direction == "output"]
    if not outputs:
        return CodeFragment()
    output_term = outputs[0]

    element_term = next((t for t in inputs if t.index < 2), None)
    dim_terms = [t for t in inputs if t.index >= 2]

    imports: set[str] = set()

    elem_val = (
        ctx.wired_input(element_term.id, node) if element_term is not None else None
    )
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
        val = ctx.wired_input(dt.id, node)
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
        "initialized_array",
        node.id,
        terminal_id=output_term.id,
    )
    stmt = build_assign(var_name, expr)

    return CodeFragment(
        statements=[stmt],
        bindings={output_term.id: var_name},
        imports=imports,
    )


def generate_array_replace(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for Replace Array Subset (aReplace).

    Terminal layout (the ``aReplace`` entry in ``_meta.json``): input 0 = array
    in, output 1 = output array, then one set per replacement -- a new
    element/subarray followed by one ``index`` input per dimension of the
    array. A resized node carries several sets; they are applied in terminal
    order, each to the result of the one before.

    NI's page (Replace Array Subset): an index that is not wired starts at
    element 0 ("starting with element 0"); "If you specify an index without
    wiring the following indexes, this function continues from the last index
    you specify" -- so each unwired index is 0. The function "crops any
    subarray that is too large to fit" and "If any index is negative or larger
    than the corresponding dimension ... the function has no effect". The rule
    is the same at every rank and is written out as indexing on a copy.

    A new element with fewer dimensions than the array lands along the array's
    last dimensions, the leading indices picking the cell (a row into a 2-D
    array: the first index is the row). The page does not say which axis a
    subarray runs along when only a trailing index is wired, so a subarray
    whose leading indices are not all wired raises a diagnostic.
    """
    roles = laid_out_roles(node, "aReplace")
    array_term = roles[0][0][1]
    output_term = roles[1][0][1]
    rank = _array_rank(array_term)
    if rank is None:
        raise ConversionGap(
            "Replace Array Subset's array input has no known array type", node
        )
    elements = roles.get(2, [])
    if not elements:
        return CodeFragment()

    array_val = ctx.wired_input(array_term.id, node) or "[]"
    statements: list[ast.stmt] = []
    for position, (placed, element_term) in enumerate(elements):
        indices = [
            term
            for index_placed, term in roles.get(3, [])
            if index_placed.set_number == placed.set_number
        ]
        new_rank = _array_rank(element_term) or 0
        if new_rank > rank:
            raise ConversionGap(
                "Replace Array Subset's new element has more dimensions than "
                "the array.",
                node,
            )
        index_vals = [ctx.wired_input(t.id, node) for t in indices]
        leading = rank - new_rank
        if new_rank and not all(index_vals[:leading]):
            raise ConversionGap(
                "Replace Array Subset with a subarray needs the leading "
                f"{leading} index input(s) wired: NI's page does not say along "
                "which axis the subarray runs otherwise.",
                node,
            )
        element_val = ctx.wired_input(element_term.id, node)
        if element_val is None:
            element_val = "[]" if new_rank else "None"
        target = (
            ctx.make_output_var("output_array", node.id, terminal_id=output_term.id)
            if position == len(elements) - 1
            else ctx.make_output_var(f"array_after_{position}", node.id)
        )
        offsets = [value or "0" for value in index_vals]
        statements.extend(
            replace_subarray_statements(
                array_val, element_val, offsets, new_rank, target
            )
        )
        array_val = target
    return CodeFragment(
        statements=statements,
        bindings={output_term.id: array_val},
        imports={"import copy"},
    )


def _array_rank(term: Terminal | None) -> int | None:
    """Dimension count of an array-typed terminal, else None."""
    return array_rank(term.lv_type) if term is not None else None


def generate_array_insert(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for Insert Into Array (aInsert).

    Terminal layout (the ``aInsert`` entry in ``_meta.json``, verified against
    the corpus's aInsert nodes including a 2-D one in Display Client Creation
    Status.vi): input 0 = array in, output 1 = output array, then one ``index``
    input per dimension of the array, then the "new element/subarray" input(s)
    -- several when the node is resized to insert more than one.

    NI docs (Insert Into Array): "You can wire only one index input" -- the
    wired index picks the dimension along which the insert happens. If no
    index is wired, the new element/subarray is appended to the end of the
    array. If the (wired) index is beyond the array's current length, the
    function does NOT insert anything -- unlike Replace Array Subset, this is
    a true no-op, not a clip.

    Several new-element inputs are inserted at the one index in terminal
    order. Only an insert along the first dimension (the first index wired, or
    none wired) is generated. For an insert along another dimension (a column
    into a 2-D array) NI's page says only "LabVIEW pads the resulting array as
    necessary" and does not say which cells are padded or with what, so a
    diagnostic is raised instead of guessing.
    """
    roles = laid_out_roles(node, "aInsert")
    array_term = roles[0][0][1]
    output_term = roles[1][0][1]
    rank = _array_rank(array_term)
    if rank is None:
        raise ConversionGap(
            "Insert Into Array's array input has no known array type, so the "
            "number of index inputs is unknown.",
            node,
        )
    index_terms = [term for _, term in roles.get(2, [])]
    element_terms = [term for _, term in roles.get(3, [])]
    if not element_terms:
        return CodeFragment()

    array_val = ctx.wired_input(array_term.id, node) or "[]"
    index_vals = [ctx.wired_input(t.id, node) for t in index_terms]
    wired = [(axis, value) for axis, value in enumerate(index_vals) if value]
    if len(wired) > 1:
        raise ConversionGap(
            "Insert Into Array with more than one index wired; LabVIEW allows "
            "only one.",
            node,
        )
    axis, index_val = wired[0] if wired else (0, None)
    if axis > 0:
        raise ConversionGap(
            f"Insert Into Array along dimension {axis} (inserting columns or "
            "pages) is not supported: NI's page says only \"LabVIEW pads the "
            'resulting array as necessary" without saying which cells are '
            "padded or with what.",
            node,
        )
    idx_str = index_val if index_val else f"len({array_val})"

    items: list[str] = []
    for element_term in element_terms:
        element_rank = _array_rank(element_term)
        is_subset = element_rank == rank
        elem_val = ctx.wired_input(element_term.id, node)
        if elem_val is None:
            elem_val = "[]" if is_subset else "None"
        items.append(elem_val if is_subset else f"[{elem_val}]")
    items_str = " + ".join(f"({item})" for item in items)

    expr_str = (
        f"({array_val}[:{idx_str}] + {items_str} + {array_val}[{idx_str}:]) "
        f"if 0 <= ({idx_str}) <= len({array_val}) else list({array_val})"
    )

    var_name = ctx.make_output_var(
        "output_array",
        node.id,
        terminal_id=output_term.id,
    )
    stmt = build_assign(var_name, parse_expr(expr_str))

    return CodeFragment(
        statements=[stmt],
        bindings={output_term.id: var_name},
    )


def generate_array_reshape(
    node: PrimitiveNode,
    ctx: CodeGenContext,
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
        (t for t in node.terminals if t.direction == "input"),
        key=lambda t: t.index,
    )
    outputs = [t for t in node.terminals if t.direction == "output"]
    if not outputs or not inputs:
        return CodeFragment()
    output_term = outputs[0]

    array_term = inputs[0]
    dim_terms = [t for t in inputs if t.index >= 2]
    target_ndim = len(dim_terms)
    if target_ndim == 0:
        raise ConversionGap(
            "Reshape Array has no dimension-size terminal to reshape "
            f"into (node {node.id}).",
            node,
        )
    if target_ndim > 2:
        raise ConversionGap(
            f"Reshape Array with {target_ndim} target dimensions is not "
            "supported -- only 1-D and 2-D target shapes are implemented "
            "(no 3-D+ sample exists to verify the reshape-nesting code "
            "against).",
            node,
        )

    lv_type = array_term.lv_type
    if lv_type is None or lv_type.kind != LVTypeKind.ARRAY or not lv_type.dimensions:
        raise ConversionGap(
            "Reshape Array requires the source array's resolved LabVIEW "
            f"type (to know its dimensionality) for node {node.id}, but "
            "type info is unavailable for this terminal.",
            node,
        )
    source_ndim = lv_type.dimensions

    array_val = ctx.wired_input(array_term.id, node) or "[]"
    flat_expr = _build_flatten_expr(array_val, source_ndim)

    dim_exprs: list[ast.expr] = []
    for dt in dim_terms:
        val = ctx.wired_input(dt.id, node)
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
        "reshaped_array",
        node.id,
        terminal_id=output_term.id,
    )

    if target_ndim == 1:
        final_stmt = build_assign(var_name, ast.Name(id=flat_var, ctx=ast.Load()))
    else:
        d0_str = ast.unparse(dim_exprs[0])
        d1_str = ast.unparse(dim_exprs[1])
        expr_str = (
            f"[{flat_var}[_ri*{d1_str}:(_ri+1)*{d1_str}] for _ri in range({d0_str})]"
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
            func=ast.Name(id="list", ctx=ast.Load()),
            args=[base],
            keywords=[],
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

    if kind == LVTypeKind.ARRAY:
        return ast.List(elts=[], ctx=ast.Load())
    if kind == LVTypeKind.CLUSTER:
        return ast.Constant(value=None)
    if kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return ast.Constant(value=0)
    if kind == LVTypeKind.PRIMITIVE:
        if is_string_type_name(underlying):
            return ast.Constant(value="")
        if is_boolean_type_name(underlying):
            return ast.Constant(value=False)
        if underlying in (
            "NumInt8",
            "NumInt16",
            "NumInt32",
            "NumInt64",
            "NumUInt8",
            "NumUInt16",
            "NumUInt32",
            "NumUInt64",
        ):
            return ast.Constant(value=0)
        if underlying in ("NumFloat32", "NumFloat64"):
            return ast.Constant(value=0.0)

    return ast.Constant(value=None)
