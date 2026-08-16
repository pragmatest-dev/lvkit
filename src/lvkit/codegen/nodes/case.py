"""Code generator for case structures (if/elif/match-case)."""

from __future__ import annotations

import ast
import keyword
import logging

from lvkit.graph.core import kind_display
from lvkit.models import CaseFrame, CaseOperation, _is_error_cluster

from ..ast_utils import (
    build_assign,
    default_value_expr,
    parse_expr,
    to_var_name,
)
from ..context import CodeGenContext
from ..fragment import CodeFragment

logger = logging.getLogger(__name__)


def generate(node: CaseOperation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a case structure node."""
    if not node.frames:
        return CodeFragment.empty()

    if _is_error_selector_by_type(node, ctx):
        return _generate_error_case(node, ctx)

    selector_var = None
    if node.selector_terminal:
        selector_var = ctx.resolve(node.selector_terminal)

    if not selector_var:
        selector_var = _fallback_selector(node, ctx)

    if _is_boolean_selector(node.frames):
        return _generate_if_else(node, selector_var, ctx)
    return _generate_match_case(node, selector_var, ctx)


def _is_boolean_selector(frames: list[CaseFrame]) -> bool:
    """Check if case structure uses boolean selector."""
    selector_values = {str(f.selector_value) for f in frames}
    bool_values = {"True", "False", "Default", "true", "false", "default"}
    return selector_values <= bool_values and len(frames) <= 3


def _generate_if_else(
    node: CaseOperation,
    selector_var: str,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate if-else statement for boolean selector."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    _bind_input_tunnels(node, ctx)

    true_frame = None
    false_frame = None
    default_frame = None

    for frame in node.frames:
        val = str(frame.selector_value).lower()
        if val == "true":
            true_frame = frame
        elif val == "false":
            false_frame = frame
        elif "default" in val:
            default_frame = frame

    if_body: list[ast.stmt] = []
    if true_frame:
        inner_fragment = _generate_frame_body(true_frame, ctx)
        if_body = inner_fragment.statements or [ast.Pass()]
        bindings.update(inner_fragment.bindings)
        all_imports.update(inner_fragment.imports)
    else:
        if_body = [ast.Pass()]

    else_body: list[ast.stmt] = []
    else_frame = false_frame or default_frame
    if else_frame:
        inner_fragment = _generate_frame_body(else_frame, ctx)
        else_body = inner_fragment.statements or [ast.Pass()]
        bindings.update(inner_fragment.bindings)
        all_imports.update(inner_fragment.imports)
    else:
        else_body = [ast.Pass()]

    output_bindings = _bind_output_tunnels(node, ctx)
    bindings.update(output_bindings)
    pre_decls = _pre_declare_outputs(node, output_bindings, ctx)
    statements.extend(pre_decls)

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
    node: CaseOperation,
    selector_var: str,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate match-case statement (Python 3.10+)."""
    bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    _bind_input_tunnels(node, ctx)

    cases: list[ast.match_case] = []

    for frame in node.frames:
        selector_str = str(frame.selector_value)
        if frame.is_default or selector_str.lower() == "default":
            pattern: ast.pattern = ast.MatchAs(pattern=None, name=None)
            guard: ast.expr | None = None
        else:
            pattern, guard = _build_frame_pattern(frame, selector_var)

        inner_fragment = _generate_frame_body(frame, ctx)
        body = inner_fragment.statements or [ast.Pass()]
        bindings.update(inner_fragment.bindings)
        all_imports.update(inner_fragment.imports)

        cases.append(ast.match_case(pattern=pattern, guard=guard, body=body))

    output_bindings = _bind_output_tunnels(node, ctx)
    bindings.update(output_bindings)
    pre_decls = _pre_declare_outputs(node, output_bindings, ctx)

    match_stmt = ast.Match(
        subject=parse_expr(selector_var),
        cases=cases,
    )

    return CodeFragment(
        statements=pre_decls + [match_stmt],
        bindings=bindings,
        imports=all_imports,
    )


def _build_match_pattern(selector_value: str) -> ast.pattern:
    """Build AST pattern for a single match value (int if it parses, else str)."""
    try:
        int_val = int(selector_value)
        return ast.MatchValue(value=ast.Constant(value=int_val))
    except ValueError:
        pass
    return ast.MatchValue(value=ast.Constant(value=selector_value))


def _build_frame_pattern(
    frame: CaseFrame,
    selector_var: str,
) -> tuple[ast.pattern, ast.expr | None]:
    """Build the match pattern (and optional guard) for one non-default frame.

    A frame can match several selector values: a string selector matches a set
    of strings (``"jpe" | "jpeg" | "jpg"``); a numeric/enum selector matches a
    set of singletons and/or closed ranges. Singletons become an OR-pattern;
    any true range (``start != end``) is expressed as a ``case _ if`` guard,
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
        singles = [r for r in frame.selector_ranges if r.start == r.end]
        spans = [r for r in frame.selector_ranges if r.start != r.end]
        if not spans:
            pats = [ast.MatchValue(value=ast.Constant(value=r.start)) for r in singles]
            return (pats[0] if len(pats) == 1 else ast.MatchOr(patterns=pats)), None
        # At least one true range → wildcard pattern with a boolean guard.
        clauses: list[str] = [f"{selector_var} == {r.start}" for r in singles]
        clauses += [f"{r.start} <= {selector_var} <= {r.end}" for r in spans]
        guard = parse_expr(" or ".join(clauses))
        return ast.MatchAs(pattern=None, name=None), guard

    # Fallback: the single display token.
    return _build_match_pattern(str(frame.selector_value)), None


def _generate_frame_body(
    frame: CaseFrame,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for a single case frame."""
    body = ctx.generate_body(frame.operations)

    bindings: dict[str, str] = {}
    for op in frame.operations:
        for term in op.terminals:
            if term.direction == "output":
                var = ctx.resolve(term.id)
                if var:
                    bindings[term.id] = var

    return CodeFragment(
        statements=body,
        bindings=bindings,
        imports=set(),
    )


def _fallback_selector(
    node: CaseOperation,
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


def _is_error_selector_by_type(
    node: CaseOperation,
    ctx: CodeGenContext,
) -> bool:
    """Check if the selector terminal carries an error cluster type."""
    sel_id = node.selector_terminal
    if not sel_id or ctx.graph is None:
        return False
    for term in node.terminals:
        if term.id == sel_id and term.lv_type:
            return _is_error_cluster(term.lv_type)
    return False


def _generate_error_case(
    node: CaseOperation,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for an error-cluster case structure.

    Emits only the no-error frame body (happy path).
    96% of error frames are empty (45/47 in TestCase.lvclass).
    """
    _bind_input_tunnels(node, ctx)

    no_error_frame = None
    error_frame = None
    for frame in node.frames:
        val = str(frame.selector_value).lower()
        if val in ("false", "no error", "0"):
            no_error_frame = frame
        elif val in ("true", "error", "1", "default"):
            error_frame = frame

    if no_error_frame is None and node.frames:
        no_error_frame = node.frames[0]

    if no_error_frame is None:
        return CodeFragment.empty()

    statements: list[ast.stmt] = []

    if (
        error_frame is not None
        and error_frame.operations
        and len(error_frame.operations) > 0
    ):
        op_names = ", ".join(op.display_name for op in error_frame.operations)
        logger.info("LV error frame omitted in %s: %s", node.id, op_names)

    no_error_fragment = _generate_frame_body(no_error_frame, ctx)
    statements.extend(no_error_fragment.statements or [])

    output_bindings = _bind_output_tunnels(node, ctx)
    bindings = dict(no_error_fragment.bindings)
    bindings.update(output_bindings)

    return CodeFragment(
        statements=statements,
        bindings=bindings,
        imports=no_error_fragment.imports,
    )


def _pre_declare_outputs(
    node: CaseOperation,
    output_bindings: dict[str, str],
    ctx: CodeGenContext,
) -> list[ast.stmt]:
    """Pre-declare output tunnel variables before the case structure."""
    param_names = {to_var_name(inp.name or "") for inp in ctx.vi_inputs}
    # Collect vars from INPUT tunnel outers only.  Output tunnel outers are
    # resolved via BFS through the inner graph (self-loops, IPES output
    # terminals) and would incorrectly mark frame-computed variables as
    # "already defined", suppressing needed pre-declarations.
    input_vars: set[str] = set()
    outer_id_to_term = {t.id: t for t in node.terminals}
    for tunnel in node.tunnels:
        outer_uid = tunnel.outer_terminal_uid
        outer_term = outer_id_to_term.get(outer_uid)
        if outer_term and outer_term.direction != "input":
            continue
        outer_var = ctx.resolve(outer_uid)
        if outer_var:
            input_vars.add(outer_var)

    pre_decls: list[ast.stmt] = []
    declared: set[str] = set()
    for _outer_term, var_name in output_bindings.items():
        if not var_name or var_name == "None":
            continue
        if var_name in param_names:
            continue
        if var_name in input_vars:
            continue
        if var_name in declared:
            continue
        # Skip non-identifier strings (e.g. "0.0") and Python keywords
        # (True, False, None) — these cannot appear on the left of an assignment.
        if not var_name.isidentifier() or keyword.iskeyword(var_name):
            continue
        declared.add(var_name)
        # LabVIEW's "Use Default If Unwired": a frame that doesn't wire this
        # output tunnel emits the TYPE default (0/False/""/…), not None. Seed
        # the pre-declaration with that default so an unwired frame is faithful.
        # (Always-wired tunnels overwrite it, so this is safe in all cases.)
        outer = outer_id_to_term.get(_outer_term)
        default = default_value_expr(outer.lv_type if outer else None)
        pre_decls.append(build_assign(var_name, default))

    return pre_decls


def _bind_input_tunnels(
    node: CaseOperation,
    ctx: CodeGenContext,
) -> None:
    """Bind input tunnel inner terminals to their outer values."""
    for tunnel in node.tunnels:
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid
        if not outer_term or not inner_term:
            continue
        outer_var = ctx.resolve(outer_term)
        if outer_var:
            ctx.bind(inner_term, outer_var)


def _bind_output_tunnels(
    node: CaseOperation,
    ctx: CodeGenContext,
) -> dict[str, str]:
    """Bind output tunnel terminals to variable names."""
    bindings: dict[str, str] = {}

    for tunnel in node.tunnels:
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid
        if not outer_term or not inner_term:
            continue

        if outer_term in bindings and bindings[outer_term] != "None":
            continue

        inner_var = ctx.resolve(inner_term)
        if inner_var:
            bindings[outer_term] = inner_var
        else:
            outer_var = ctx.resolve(outer_term)
            if outer_var:
                ctx.bind(inner_term, outer_var)

    return bindings
