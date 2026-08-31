"""Tests for First Call? (primResID 1083) code generation.

Mirrors tests/test_queue_codegen.py's pattern: build a PrimitiveOperation
by hand, generate + COMPILE AND EXECUTE the fragment (plus its registered
module-level global), and assert on real runtime behavior across
multiple calls -- not just that the code parses.
"""

from __future__ import annotations

import ast

from lvkit.codegen.nodes import first_call
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal
from tests.helpers import make_ctx


def _first_call_op(node_id: str = "fc1") -> PrimitiveNode:
    return PrimitiveNode(
        vi_path="test.vi",
        id=node_id,
        name="First Call?",
        kind="primitive",
        node_type="prim",
        prim_id=first_call.FIRST_CALL_PRIM_ID,
        terminals=[
            Terminal(id=f"{node_id}.first_call", index=0, direction="output"),
        ],
    )


class TestDispatch:
    """First Call? is a generic ``class="prim"`` node distinguished only
    by primResID -- reachable via the primResID dispatch guard in
    nodes/__init__.py, not via node_type (which is just "prim")."""

    def test_dispatch_guard_routes_to_first_call_module(self):
        from lvkit.codegen.nodes import generate as generate_node

        op = _first_call_op()
        ctx = make_ctx(*(t.id for t in op.terminals))
        fragment = generate_node(op, ctx)
        assert fragment.statements
        assert len(ctx.module_globals) == 1


class TestModuleGlobalRegistration:
    def test_registers_flag_seeded_true(self):
        op = _first_call_op()
        ctx = make_ctx(*(t.id for t in op.terminals))
        first_call.generate(op, ctx)

        assert len(ctx.module_globals) == 1
        name = next(iter(ctx.module_globals))
        assert name.startswith("_lv_first_call_")
        stmt = ctx.module_globals[name]
        ast.fix_missing_locations(stmt)
        assert ast.unparse(stmt) == f"{name} = True"

    def test_registration_idempotent_by_name(self):
        """Two generate() calls for the SAME node id (e.g. a repeated
        codegen pass) must not double-register the module global."""
        op = _first_call_op()
        ctx = make_ctx(*(t.id for t in op.terminals))
        first_call.generate(op, ctx)
        first_call.generate(op, ctx)
        assert len(ctx.module_globals) == 1

    def test_different_call_sites_get_distinct_globals(self):
        op1 = _first_call_op("fc1")
        op2 = _first_call_op("fc2")
        ctx = make_ctx(*(t.id for t in (*op1.terminals, *op2.terminals)))
        first_call.generate(op1, ctx)
        first_call.generate(op2, ctx)
        assert len(ctx.module_globals) == 2


class TestExecutesAndPersistsAcrossCalls:
    """The real correctness bar: compile the generated statements into a
    function and call it repeatedly, proving True fires exactly once."""

    def test_true_on_first_call_false_thereafter(self):
        op = _first_call_op()
        ctx = make_ctx(*(t.id for t in op.terminals))
        fragment = first_call.generate(op, ctx)
        global_name = next(iter(ctx.module_globals))
        var_name = fragment.bindings[op.terminals[0].id]

        func_def = ast.FunctionDef(
            name="run",
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=[
                *fragment.statements,
                ast.Return(value=ast.Name(id=var_name, ctx=ast.Load())),
            ],
            decorator_list=[],
        )
        module = ast.Module(
            body=[ctx.module_globals[global_name], func_def],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        ns: dict = {}
        exec(compile(module, "<test>", "exec"), ns)  # noqa: S102
        run = ns["run"]

        assert run() is True
        assert run() is False
        assert run() is False
        assert ns[global_name] is False
