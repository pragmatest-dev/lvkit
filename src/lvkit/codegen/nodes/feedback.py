"""Code generator for the Feedback Node.

A Feedback Node stores the value written to it and gives it back the next time
the node runs: the next loop iteration, or the next call of the VI. NI's help
("Feedback Node", "Passing the Last Value from the Previous Execution to the
Feedback Node"): the initializer sets the first value; with the initializer on
the node, the node initializes once, when the VI loads, "the initial value is the
last value from the previous execution" on every later run, and an unwired
initializer starts from the type's default; with the initializer moved to the
left edge of a loop, the node "initialize[s] every time the loop executes".

The file stores the node as two nodes: the master (the output the node reads, and
its initializer) and the slave (the input it writes). Each generates one half:

- The master reads the stored value into the variable its output is bound to.
- The slave writes the stored value from its input. It runs after the master
  (``feedback_master_of``, used by the dataflow sort), so a read always sees the
  value of the run before.

Where the value is stored:

- On the node (the initializer is on the node, wired or not): a module variable
  the function starts from, kept between calls (``add_state_global``), seeded with
  the type's default. A wired initializer is written into it on the first call:
  a second module variable says whether that happened.
- On a loop's border: a local variable of the function, set from the initializer
  wire (or the type's default) just before the loop (``bind_loop_initializer``,
  called by the loop's generator), so each execution of the loop starts again.

A delay of more than one and an enable terminal are gaps."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, PrimitiveNode
from lvkit.models import Terminal, Tunnel

from ..ast_utils import build_assign, node_owned_name, parse_expr
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..type_default import default_if_unset, state_seed

_STATE_PREFIX = "_lv_feedback"
_STARTED_PREFIX = "_lv_feedback_started"


def feedback_master_of(node: AnyGraphNode, ctx: CodeGenContext) -> str | None:
    """The id of the master of the Feedback Node whose write side (slave) is
    ``node``; None for every other node, and for a master."""
    if ctx.graph is None:
        return None
    info = ctx.graph.get_feedback_info(node.id)
    if info is None or info[0]:
        return None
    return info[1]


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """One half of a Feedback Node: the read (master) or the write (slave)."""
    assert ctx.graph is not None
    info = ctx.graph.get_feedback_info(node.id)
    assert info is not None
    is_master, partner_uid, _ = info
    master = node if is_master else _partner(node, partner_uid, ctx)
    reason = _unsupported(master, ctx)
    if reason is not None:
        if is_master:
            raise ConversionGap(reason, node)
        # The master reports the gap; the write has nothing to store into.
        return CodeFragment.empty()
    if is_master:
        return _read(master, ctx)
    return _write(node, master, ctx)


def bind_loop_initializer(
    tunnel: Tunnel,
    outer_type_default: ast.expr,
    ctx: CodeGenContext,
    inner_ctx: CodeGenContext,
) -> list[ast.stmt]:
    """The statements before a loop that start the value of a Feedback Node
    whose initializer sits on the loop's border: the variable that holds it is
    bound to the tunnel's inner face, where the master and slave inside the
    loop find it."""
    outer = tunnel.outer_terminal_uid
    inner = tunnel.inner_terminal_uid
    assert outer is not None and inner is not None
    wired = ctx.resolve(outer)
    start = parse_expr(wired) if wired else outer_type_default
    variable = ctx.make_output_var("feedback", outer)
    inner_ctx.bind(inner, variable)
    return [build_assign(variable, start)]


def _partner(
    node: PrimitiveNode, partner_uid: str | None, ctx: CodeGenContext
) -> PrimitiveNode:
    assert ctx.graph is not None
    master = ctx.graph.get_graph_node(partner_uid) if partner_uid else None
    if not isinstance(master, PrimitiveNode):
        raise CodeGenError(
            f"The write side of a Feedback Node, {node.id!r}, has no read side"
        )
    return master


def _unsupported(master: PrimitiveNode, ctx: CodeGenContext) -> str | None:
    """Why the Feedback Node cannot be converted, or None when it can."""
    assert ctx.graph is not None
    info = ctx.graph.get_feedback_info(master.id)
    assert info is not None
    _, partner_uid, delay = info
    if delay is not None and delay > 1:
        return (
            f"A Feedback Node with a delay of {delay} stores {delay} values and "
            "returns the oldest; only a delay of 1 is converted"
        )
    slave = ctx.graph.get_graph_node(partner_uid) if partner_uid else None
    other = ctx.graph.get_feedback_other_terminals(master.id)
    if slave is not None:
        other += ctx.graph.get_feedback_other_terminals(slave.id)
    if other:
        return (
            "A Feedback Node with a terminal beyond its input, output and "
            f"initializer ({', '.join(other)}; an enable terminal) is not converted"
        )
    return None


def _output(master: PrimitiveNode) -> Terminal:
    return next(t for t in master.terminals if t.direction == "output")


def _state_name(master: PrimitiveNode, ctx: CodeGenContext) -> str:
    return node_owned_name(_STATE_PREFIX, ctx.function_name, master.id)


def _loop_variable(master: PrimitiveNode, ctx: CodeGenContext) -> str | None:
    """The local variable that holds the value when the initializer is on the
    loop's border; None when it is on the node."""
    assert ctx.graph is not None
    border = ctx.graph.get_feedback_init_tunnel(master.id)
    if border is None:
        return None
    variable = ctx.resolve(border[1])
    if variable is None:
        raise CodeGenError(
            f"The initializer on the loop border of Feedback Node {master.id!r} "
            "was not given a variable"
        )
    return variable


def _read(master: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    out = _output(master)
    result = ctx.make_output_var("feedback", master.id, terminal_id=out.id)
    held = _loop_variable(master, ctx)
    statements: list[ast.stmt] = []
    if held is None:
        held = _state_name(master, ctx)
        ctx.add_state_global(held, state_seed(out.lv_type), owner=master.id)
        names = [held]
        init = next((t for t in master.terminals if t.direction == "input"), None)
        start = ctx.wired_input(init.id, master) if init is not None else None
        if start is not None:
            started = node_owned_name(_STARTED_PREFIX, ctx.function_name, master.id)
            ctx.add_state_global(started, ast.Constant(value=False), owner=master.id)
            names.append(started)
            statements.append(ast.Global(names=names))
            statements.append(
                ast.If(
                    test=ast.UnaryOp(
                        op=ast.Not(), operand=ast.Name(id=started, ctx=ast.Load())
                    ),
                    body=[
                        build_assign(held, parse_expr(start)),
                        build_assign(started, ast.Constant(value=True)),
                    ],
                    orelse=[],
                )
            )
        else:
            statements.append(ast.Global(names=names))
        statements.extend(
            default_if_unset(held, out.lv_type, ctx, "the Feedback Node's value")
        )
    statements.append(build_assign(result, ast.Name(id=held, ctx=ast.Load())))
    return CodeFragment(statements=statements, bindings={out.id: result})


def _write(
    node: PrimitiveNode, master: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment:
    source = next(t for t in node.terminals if t.direction == "input")
    value = ctx.wired_input(source.id, node)
    if value is None:
        return CodeFragment.empty()
    held = _loop_variable(master, ctx) or _state_name(master, ctx)
    return CodeFragment(statements=[build_assign(held, parse_expr(value))])
