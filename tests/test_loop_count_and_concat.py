"""Shift-register variable names in generated loops: each case generates a loop
from a tiny diagram and runs or inspects the code."""

from __future__ import annotations

import ast

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import loop
from lvkit.graph.models import Wire
from lvkit.models import Tunnel
from tests.test_loop_codegen import _mk_loop


def test_a_shift_register_variable_is_not_taken_by_a_node_output():
    ctx = CodeGenContext.from_wires(
        [Wire.from_terminals(from_terminal_id="seed", to_terminal_id="lsr_outer")]
    )
    ctx.bind("seed", "0")
    loop_op = _mk_loop(
        ctx,
        loop_type="forLoop",
        tunnels=[
            Tunnel(
                outer_terminal_uid="lsr_outer",
                inner_terminal_uid="lsr_inner",
                tunnel_type="lSR",
            ),
            Tunnel(
                outer_terminal_uid="rsr_outer",
                inner_terminal_uid="rsr_inner",
                tunnel_type="rSR",
            ),
        ],
    )
    fragment = loop.generate(loop_op, ctx)
    shift_var = next(
        s.targets[0].id
        for s in fragment.statements
        if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Name)
    )
    assert ctx.make_output_var(shift_var, "other_node") != shift_var
