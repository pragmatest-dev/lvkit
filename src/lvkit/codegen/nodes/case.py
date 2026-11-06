"""Code generator for case structures (if/elif/match-case)."""

from __future__ import annotations

import ast
import copy
import keyword
from dataclasses import dataclass, field

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.core import kind_display
from lvkit.graph.models import AnyGraphNode, CaseStructureNode
from lvkit.graph.op_walk import error_code_ranges
from lvkit.graph.operations import frame_key, position_of
from lvkit.lv_type_codes import is_boolean_type_name
from lvkit.models import (
    CaseFrame,
    LVType,
    SelectorRange,
    TunnelTerminal,
)

from ..ast_utils import (
    build_assign,
    parse_expr,
    to_var_name,
    type_default_expr,
)
from ..context import CodeGenContext
from ..error_flow import error_occurred, no_error_occurred
from ..error_path import is_error_selector
from ..fragment import CodeFragment

# A per-frame view: the frame metadata paired with the graph nodes it contains.
_OpsByFrame = dict[int, list[AnyGraphNode]]


@dataclass
class _OutputTunnel:
    """One case output tunnel: an outer terminal whose data leaves the case,
    plus one inner terminal per frame that wires it. Each frame's inner is the
    value that frame sends out; a frame absent from ``inner_by_frame`` doesn't
    wire the tunnel and falls back to LabVIEW "Use Default If Unwired"."""

    outer_uid: str
    lv_type: LVType | None
    # frame_key of the frame -> that frame's inner terminal uid
    inner_by_frame: dict[object, str] = field(default_factory=dict)


def _output_tunnels(
    node: CaseStructureNode, ctx: CodeGenContext
) -> list[_OutputTunnel]:
    """Collect the output tunnels of a case (outer terminals whose data flows
    OUT of the structure), each with its per-frame inner terminals."""
    outer_by_id = {t.id: t for t in node.terminals}
    tunnels: dict[str, _OutputTunnel] = {}
    for tunnel in ctx.tunnels(node):
        outer_uid = tunnel.outer_terminal_uid
        inner_uid = tunnel.inner_terminal_uid
        if not outer_uid or not inner_uid:
            continue
        outer = outer_by_id.get(outer_uid)
        if outer is None or outer.direction != "output":
            continue
        ot = tunnels.get(outer_uid)
        if ot is None:
            ot = _OutputTunnel(outer_uid=outer_uid, lv_type=outer.lv_type)
            tunnels[outer_uid] = ot
        inner = outer_by_id.get(inner_uid)
        frame_val = inner.frame if isinstance(inner, TunnelTerminal) else None
        ot.inner_by_frame[frame_val] = inner_uid
    return list(tunnels.values())


def _resolve_output_tunnels(
    node: CaseStructureNode,
    frame_bodies: list[tuple[CaseFrame, list[ast.stmt]]],
    ctx: CodeGenContext,
) -> tuple[dict[str, str], list[ast.stmt]]:
    """Lower every output tunnel now that all frame bodies exist.

    A case output tunnel is a MERGE of the value each frame sends out. When
    every frame sends the SAME value (a pass-through, or an in-place mutation
    that keeps the same variable), the tunnel is that value directly -- no merge
    variable, matching the value's own name. When frames DIVERGE, a single merge
    variable is introduced, assigned in each frame from that frame's inner
    source (``_append_frame_merges``-style), and pre-declared to the type
    default for any frame that doesn't wire it.

    Returns ``(outer_uid -> variable)`` bindings for every output tunnel and
    the pre-declaration statements for the divergent merges only.
    """
    bindings: dict[str, str] = {}
    divergent: dict[str, str] = {}  # outer_uid -> merge var (needs pre-declare)
    n_frames = len(node.frames)
    # ``frame_bodies`` is in the order the branches are written (a Boolean case
    # writes True first), not frame order, so a frame's key comes from its
    # place in ``node.frames``.
    body_by_frame: dict[object, list[ast.stmt]] = {
        frame_key(frame, position_of(node.frames, frame)): body
        for frame, body in frame_bodies
    }

    for ot in _output_tunnels(node, ctx):
        vals: dict[object, str | None] = {
            fv: ctx.resolve(inner) for fv, inner in ot.inner_by_frame.items()
        }
        distinct = {v for v in vals.values() if v}
        # Every frame must both HAVE an inner terminal AND resolve it to a real
        # value. A frame whose inner resolves to None is unwired (a `pass`
        # frame, "use default if unwired") and its value must come from the
        # pre-declared default below — aliasing to the other frames' value would
        # reference a variable that frame never assigns (UnboundLocalError).
        wires_every_frame = (
            None not in ot.inner_by_frame
            and len(ot.inner_by_frame) >= n_frames
            and all(vals.get(fv) is not None for fv in ot.inner_by_frame)
        )
        # Degenerate merge: one value produced by every frame — alias the outer
        # straight to that value, no merge variable.
        if len(distinct) == 1 and wires_every_frame:
            bindings[ot.outer_uid] = next(iter(distinct))
            continue

        var = ctx.output_tunnel_var_name(ot.outer_uid, node.id) or ctx.make_output_var(
            "case_output", ot.outer_uid
        )
        for fv in ot.inner_by_frame:
            body = body_by_frame.get(fv)
            if body is None:
                continue
            val = vals.get(fv)
            if val and val != var:
                body.append(build_assign(var, parse_expr(val)))
        bindings[ot.outer_uid] = var
        divergent[ot.outer_uid] = var

    pre_decls = _pre_declare_outputs(node, divergent, ctx)
    return bindings, pre_decls


def _ops_by_frame(node: CaseStructureNode, ctx: CodeGenContext) -> _OpsByFrame:
    """Map each frame (by identity) to the operation-kind graph nodes it
    contains."""
    return {id(frame): ops for frame, ops in ctx.frame_children(node)}


def generate(node: CaseStructureNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a case structure node."""
    if not node.frames:
        return CodeFragment.empty()

    ops_by_frame = _ops_by_frame(node, ctx)

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
    ops_by_frame: _OpsByFrame,
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
        inner_fragment = _generate_frame_body(node, true_frame, ops_by_frame, ctx)
        if_body = inner_fragment.statements
        all_imports.update(inner_fragment.imports)
        frame_bodies.append((true_frame, if_body))

    else_body: list[ast.stmt] = []
    else_frame = false_frame or default_frame
    if else_frame:
        inner_fragment = _generate_frame_body(node, else_frame, ops_by_frame, ctx)
        else_body = inner_fragment.statements
        all_imports.update(inner_fragment.imports)
        frame_bodies.append((else_frame, else_body))

    output_bindings, pre_decls = _resolve_output_tunnels(node, frame_bodies, ctx)
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
    ops_by_frame: _OpsByFrame,
) -> CodeFragment:
    """Generate match-case statement (Python 3.10+)."""
    bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    _bind_input_tunnels(node, ctx)

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

        inner_fragment = _generate_frame_body(node, frame, ops_by_frame, ctx)
        frame_bodies.append((frame, inner_fragment.statements))
        all_imports.update(inner_fragment.imports)

    output_bindings, pre_decls = _resolve_output_tunnels(node, frame_bodies, ctx)
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


def _generate_frame_body(
    node: CaseStructureNode,
    frame: CaseFrame,
    ops_by_frame: _OpsByFrame,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for a single case frame's contained operations."""
    operations = ops_by_frame.get(id(frame), [])
    body = ctx.generate_body(
        operations, (node.id, frame_key(frame, position_of(node.frames, frame)))
    )

    bindings: dict[str, str] = {}
    for op in operations:
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
    ops_by_frame: _OpsByFrame,
) -> CodeFragment:
    """Generate an error case structure from the error on its selector wire.

    The No Error frame is the ``else`` of ``if <error occurred>:``. In the
    ``if`` the frames that store error codes are an ``if``/``elif`` chain on the
    error's code, in frame order, and the Default frame is its last ``else``. An
    Error frame that stores only the status (no code) catches every error the
    code frames do not, as the Default frame does. A
    selector wire that carries no error runs only the No Error frame."""
    _bind_input_tunnels(node, ctx)
    selector = (
        ctx.wired_input(node.selector_terminal, node)
        if node.selector_terminal
        else None
    )
    error = selector or "None"

    frame_bodies: list[tuple[CaseFrame, list[ast.stmt]]] = []
    imports: set[str] = set()
    for frame in node.frames:
        fragment = _generate_frame_body(node, frame, ops_by_frame, ctx)
        frame_bodies.append((frame, fragment.statements))
        imports.update(fragment.imports)
    bindings, pre_decls = _resolve_output_tunnels(node, frame_bodies, ctx)

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


def _pre_declare_outputs(
    node: CaseStructureNode,
    output_bindings: dict[str, str],
    ctx: CodeGenContext,
) -> list[ast.stmt]:
    """Pre-declare output tunnel variables before the case structure."""
    param_names = ctx.parameter_var_names()
    # Collect vars from INPUT tunnel outers only.  Output tunnel outers are
    # resolved via BFS through the inner graph (self-loops, IPES output
    # terminals) and would incorrectly mark frame-computed variables as
    # "already defined", suppressing needed pre-declarations.
    input_vars: set[str] = set()
    outer_id_to_term = {t.id: t for t in node.terminals}
    for tunnel in ctx.tunnels(node):
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
        default = type_default_expr(outer.lv_type if outer else None)
        pre_decls.append(build_assign(var_name, default))

    return pre_decls


def _bind_input_tunnels(
    node: CaseStructureNode,
    ctx: CodeGenContext,
) -> None:
    """Bind input tunnel inner terminals to their outer values.

    INPUT tunnels only: an output tunnel's value flows the other way (from the
    per-frame inner producers out to the outer), so binding its inners here
    would resolve the outer BACKWARD through the inners to a pass-through value
    and clobber the real producer -- output tunnels are lowered as merges (see
    :func:`_output_merges`)."""
    outer_by_id = {t.id: t for t in node.terminals}
    for tunnel in ctx.tunnels(node):
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid
        if not outer_term or not inner_term:
            continue
        outer = outer_by_id.get(outer_term)
        if outer is not None and outer.direction != "input":
            continue
        outer_var = ctx.resolve(outer_term)
        if outer_var:
            ctx.bind(inner_term, outer_var)
