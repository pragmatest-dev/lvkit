"""The parts of generating a structure whose frames are chosen at run time (a Case
Structure, an Event Structure) that do not depend on how a frame is chosen: the
frame bodies, the input tunnels bound to their outer values and the output tunnels
merged from what each frame sends out."""

from __future__ import annotations

import ast
import keyword
from collections.abc import Sequence
from dataclasses import dataclass, field

from lvkit.graph.models import AnyGraphNode, CaseStructureNode, EventStructureNode
from lvkit.graph.operations import frame_key, position_of
from lvkit.models import CaseFrame, EventFrame, LVType, TunnelTerminal

from ..ast_utils import build_assign, parse_expr, type_default_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment

# The structures whose frame is chosen at run time.
FrameStructure = CaseStructureNode | EventStructureNode

# A per-frame view: the frame metadata paired with the graph nodes it contains.
OpsByFrame = dict[int, list[AnyGraphNode]]


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


def _frame_of(inner: object) -> str | int | None:
    """The key (``frame_key``) of the frame an inner tunnel terminal sits in: the
    frame a Case's terminal records, the position of the frame of an Event
    Structure's."""
    if not isinstance(inner, TunnelTerminal):
        return None
    if inner.frame is not None:
        return inner.frame
    return str(inner.frame_position) if inner.frame_position is not None else None


def _output_tunnels(
    node: FrameStructure, ctx: CodeGenContext
) -> list[_OutputTunnel]:
    """Collect the output tunnels of a structure (outer terminals whose data flows
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
        ot.inner_by_frame[_frame_of(outer_by_id.get(inner_uid))] = inner_uid
    return list(tunnels.values())


def resolve_output_tunnels(
    node: FrameStructure,
    frame_bodies: Sequence[tuple[CaseFrame | EventFrame, list[ast.stmt]]],
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

    pre_decls = pre_declare_outputs(node, divergent, ctx)
    return bindings, pre_decls


def frame_operations(node: FrameStructure, ctx: CodeGenContext) -> OpsByFrame:
    """Map each frame (by identity) to the operation-kind graph nodes it
    contains."""
    return {id(frame): ops for frame, ops in ctx.frame_children(node)}


def generate_frame_body(
    node: FrameStructure,
    frame: CaseFrame | EventFrame,
    ops_by_frame: OpsByFrame,
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




def pre_declare_outputs(
    node: FrameStructure,
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


def bind_input_tunnels(
    node: FrameStructure,
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
