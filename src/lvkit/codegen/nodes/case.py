"""Code generator for case structures (if/elif/match-case)."""

from __future__ import annotations

import ast
import copy

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.core import kind_display
from lvkit.graph.models import CaseStructureNode
from lvkit.graph.op_walk import error_code_ranges
from lvkit.lv_type_codes import is_boolean_type_name
from lvkit.models import CaseFrame, SelectorRange

from ..ast_utils import parse_expr, to_var_name
from ..context import CodeGenContext
from ..error_flow import error_occurred, no_error_occurred
from ..error_path import is_error_selector
from ..fragment import CodeFragment
from .frame_structure import (
    OpsByFrame,
    bind_input_tunnels,
    frame_operations,
    generate_frame_body,
    resolve_output_tunnels,
)


def generate(node: CaseStructureNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a case structure node."""
    if not node.frames:
        return CodeFragment.empty()

    ops_by_frame = frame_operations(node, ctx)

    if is_error_selector(node):
        return _generate_error_case(node, ctx, ops_by_frame)

    selector_var = None
    if node.selector_terminal:
        selector_var = ctx.wired_input(node.selector_terminal, node)

    if not selector_var:
        selector_var = _fallback_selector(node, ctx)

    selector_type = _selector_underlying_type(node)
    if is_boolean_type_name(selector_type):
        return _generate_if_else(node, selector_var, ctx, ops_by_frame)
    if selector_type is None and _has_placeholder_frame(node):
        raise ConversionGap(
            "The case structure's selector type is unknown, so the values its "
            "frames match are unknown",
            node,
        )
    return _generate_match_case(node, selector_var, ctx, ops_by_frame)


def _has_placeholder_frame(node: CaseStructureNode) -> bool:
    """Whether a non-default frame records no value to match: no strings, no
    ranges and no Boolean value."""
    return any(
        not frame.is_default
        and not frame.selector_strings
        and not frame.selector_ranges
        and frame.boolean_value is None
        for frame in node.frames
    )


def _selector_underlying_type(node: CaseStructureNode) -> str | None:
    """The underlying type name of the case structure's selector terminal
    (``Boolean``, ``String``, ...), or None when it has no resolved type."""
    for term in node.terminals:
        if term.id == node.selector_terminal and term.lv_type:
            return term.lv_type.underlying_type
    return None


def _generate_if_else(
    node: CaseStructureNode,
    selector_var: str,
    ctx: CodeGenContext,
    ops_by_frame: OpsByFrame,
) -> CodeFragment:
    """Generate if-else statement for boolean selector."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    bind_input_tunnels(node, ctx)

    true_frame = None
    false_frame = None
    default_frame = None

    for frame in node.frames:
        if frame.is_default:
            default_frame = frame
        elif frame.boolean_value is True:
            true_frame = frame
        elif frame.boolean_value is False:
            false_frame = frame
        else:
            raise ConversionGap(
                "A frame of a Boolean case structure records neither True nor "
                "False as its value",
                node,
            )

    frame_bodies: list[tuple[CaseFrame, list[ast.stmt]]] = []
    if_body: list[ast.stmt] = []
    if true_frame:
        inner_fragment = generate_frame_body(node, true_frame, ops_by_frame, ctx)
        if_body = inner_fragment.statements
        all_imports.update(inner_fragment.imports)
        frame_bodies.append((true_frame, if_body))

    else_body: list[ast.stmt] = []
    else_frame = false_frame or default_frame
    if else_frame:
        inner_fragment = generate_frame_body(node, else_frame, ops_by_frame, ctx)
        else_body = inner_fragment.statements
        all_imports.update(inner_fragment.imports)
        frame_bodies.append((else_frame, else_body))

    output_bindings, pre_decls = resolve_output_tunnels(node, frame_bodies, ctx)
    bindings.update(output_bindings)
    statements.extend(pre_decls)

    if not if_body:
        if_body.append(ast.Pass())
    if not else_body:
        else_body.append(ast.Pass())

    if_is_pass = len(if_body) == 1 and isinstance(if_body[0], ast.Pass)
    else_is_pass = len(else_body) == 1 and isinstance(else_body[0], ast.Pass)

    if if_is_pass and not else_is_pass:
        if_stmt = ast.If(
            test=ast.UnaryOp(op=ast.Not(), operand=parse_expr(selector_var)),
            body=else_body,
            orelse=[],
        )
    elif else_is_pass and not if_is_pass:
        if_stmt = ast.If(
            test=parse_expr(selector_var),
            body=if_body,
            orelse=[],
        )
    else:
        if_stmt = ast.If(
            test=parse_expr(selector_var),
            body=if_body,
            orelse=else_body,
        )
    statements.append(if_stmt)

    return CodeFragment(
        statements=statements,
        bindings=bindings,
        imports=all_imports,
    )


def _generate_match_case(
    node: CaseStructureNode,
    selector_var: str,
    ctx: CodeGenContext,
    ops_by_frame: OpsByFrame,
) -> CodeFragment:
    """Generate match-case statement (Python 3.10+)."""
    bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    bind_input_tunnels(node, ctx)

    # Generate every frame body first: the output-tunnel merge is resolved
    # afterwards (it needs each frame's produced value) and may append a merge
    # assignment into these same body lists.
    frame_bodies: list[tuple[CaseFrame, list[ast.stmt]]] = []
    patterns: list[tuple[ast.pattern, ast.expr | None]] = []
    is_string = _is_string_selector(node)
    for frame in node.frames:
        if frame.is_default:
            pattern: ast.pattern = ast.MatchAs(pattern=None, name=None)
            guard: ast.expr | None = None
        else:
            pattern, guard = _build_frame_pattern(frame, selector_var, is_string)
        patterns.append((pattern, guard))

        inner_fragment = generate_frame_body(node, frame, ops_by_frame, ctx)
        frame_bodies.append((frame, inner_fragment.statements))
        all_imports.update(inner_fragment.imports)

    output_bindings, pre_decls = resolve_output_tunnels(node, frame_bodies, ctx)
    bindings.update(output_bindings)

    cases: list[ast.match_case] = []
    # A bare wildcard (`case _:`, no guard) matches everything, so Python
    # requires it LAST — any later case is "unreachable". The default frame can
    # sit anywhere in node.frames, so hold its case aside and append it after
    # the rest. (A guarded wildcard `case _ if …:` is NOT a catch-all and stays
    # in place.)
    default_case: ast.match_case | None = None
    for (frame, body), (pattern, guard) in zip(frame_bodies, patterns, strict=True):
        if not body:
            body.append(ast.Pass())
        match_case = ast.match_case(pattern=pattern, guard=guard, body=body)
        is_bare_wildcard = (
            isinstance(pattern, ast.MatchAs)
            and pattern.pattern is None
            and pattern.name is None
            and guard is None
        )
        if is_bare_wildcard:
            default_case = match_case
        else:
            cases.append(match_case)

    if default_case is not None:
        cases.append(default_case)

    match_stmt = ast.Match(
        subject=parse_expr(selector_var),
        cases=cases,
    )

    return CodeFragment(
        statements=pre_decls + [match_stmt],
        bindings=bindings,
        imports=all_imports,
    )


def _is_string_selector(node: CaseStructureNode) -> bool:
    """Whether the case structure's selector terminal carries a String."""
    return _selector_underlying_type(node) == "String"


def _build_match_pattern(selector_value: str, is_string: bool) -> ast.pattern:
    """Build AST pattern for a single display token: a string selector matches
    the string itself; any other selector matches the integer when it parses."""
    if not is_string:
        try:
            return ast.MatchValue(value=ast.Constant(value=int(selector_value)))
        except ValueError:
            pass
    return ast.MatchValue(value=ast.Constant(value=selector_value))


def _range_clause(selector_var: str, r: SelectorRange) -> str:
    """The Python comparison for one selector range. An open side has no bound
    (its stored number is filler), so it compares one-sided."""
    if r.open_start and r.open_end:
        return "True"
    if r.open_start:
        return f"{selector_var} <= {r.end}"
    if r.open_end:
        return f"{selector_var} >= {r.start}"
    if r.is_single:
        return f"{selector_var} == {r.start}"
    return f"{r.start} <= {selector_var} <= {r.end}"


def _build_frame_pattern(
    frame: CaseFrame,
    selector_var: str,
    is_string: bool,
) -> tuple[ast.pattern, ast.expr | None]:
    """Build the match pattern (and optional guard) for one non-default frame.

    A frame can match several selector values: a string selector matches a set
    of strings (``"jpe" | "jpeg" | "jpg"``); a numeric/enum selector matches a
    set of singletons and/or ranges. Singletons become an OR-pattern; any true
    range (closed or open-ended) is expressed as a ``case _ if`` guard,
    OR-combined with the singletons.
    """
    # String selector: OR of the matched string literals.
    if frame.selector_strings:
        pats: list[ast.pattern] = [
            ast.MatchValue(value=ast.Constant(value=s)) for s in frame.selector_strings
        ]
        return (pats[0] if len(pats) == 1 else ast.MatchOr(patterns=pats)), None

    # Numeric/enum selector with faithful ranges.
    if frame.selector_ranges:
        singles = [r for r in frame.selector_ranges if r.is_single]
        if len(singles) == len(frame.selector_ranges):
            pats = [ast.MatchValue(value=ast.Constant(value=r.start)) for r in singles]
            return (pats[0] if len(pats) == 1 else ast.MatchOr(patterns=pats)), None
        # At least one true range → wildcard pattern with a boolean guard.
        clauses = [_range_clause(selector_var, r) for r in frame.selector_ranges]
        guard = parse_expr(" or ".join(clauses))
        return ast.MatchAs(pattern=None, name=None), guard

    # Fallback: the single display token.
    return _build_match_pattern(str(frame.selector_value), is_string), None


def _fallback_selector(
    node: CaseStructureNode,
    ctx: CodeGenContext,
) -> str:
    """Try to derive a meaningful selector name when resolve() fails."""
    sel_term = node.selector_terminal
    flow = ctx.get_source(sel_term) if sel_term else None
    if flow:
        if flow.src_parent_name:
            return to_var_name(flow.src_parent_name)
        pk = flow.src_parent_kind
        if pk and pk not in ("primitive", "operation"):
            return to_var_name(kind_display(pk))
    return "selector"


def _generate_error_case(
    node: CaseStructureNode,
    ctx: CodeGenContext,
    ops_by_frame: OpsByFrame,
) -> CodeFragment:
    """Generate an error case structure from the error on its selector wire.

    The No Error frame is the ``else`` of ``if <error occurred>:``. In the
    ``if`` the frames that store error codes are an ``if``/``elif`` chain on the
    error's code, in frame order, and the Default frame is its last ``else``. An
    Error frame that stores only the status (no code) catches every error the
    code frames do not, as the Default frame does. A
    selector wire that carries no error runs only the No Error frame."""
    bind_input_tunnels(node, ctx)
    selector = (
        ctx.wired_input(node.selector_terminal, node)
        if node.selector_terminal
        else None
    )
    error = selector or "None"

    frame_bodies: list[tuple[CaseFrame, list[ast.stmt]]] = []
    imports: set[str] = set()
    for frame in node.frames:
        fragment = generate_frame_body(node, frame, ops_by_frame, ctx)
        frame_bodies.append((frame, fragment.statements))
        imports.update(fragment.imports)
    bindings, pre_decls = resolve_output_tunnels(node, frame_bodies, ctx)

    no_error: list[ast.stmt] = []
    branches: list[tuple[str, list[ast.stmt]]] = []
    default: list[ast.stmt] = []
    for frame, body in frame_bodies:
        codes = error_code_ranges(frame)
        if frame.is_no_error:
            no_error = body
            # A No Error frame that also stores codes runs for those codes too.
            if codes:
                branches.append((_code_test(error, codes), copy.deepcopy(body)))
        elif codes and not frame.is_default:
            branches.append((_code_test(error, codes), body))
        else:
            default = body

    if error == "None":
        return CodeFragment(
            statements=pre_decls + no_error, bindings=bindings, imports=imports
        )
    if not no_error and not default and not branches:
        return CodeFragment(statements=pre_decls, bindings=bindings, imports=imports)
    on_error: list[ast.stmt] = default
    for test, body in reversed(branches):
        on_error = [
            ast.If(test=parse_expr(test), body=body or [ast.Pass()], orelse=on_error)
        ]
    if on_error:
        statement = ast.If(
            test=parse_expr(error_occurred(error)), body=on_error, orelse=no_error
        )
    else:
        statement = ast.If(
            test=parse_expr(no_error_occurred(error)),
            body=no_error or [ast.Pass()],
            orelse=[],
        )
    return CodeFragment(
        statements=pre_decls + [statement], bindings=bindings, imports=imports
    )


def _code_test(error: str, codes: list[SelectorRange]) -> str:
    """The condition that the code of the error ``error`` is in ``codes``."""
    return " or ".join(_range_clause(f"{error}.code", r) for r in codes)
