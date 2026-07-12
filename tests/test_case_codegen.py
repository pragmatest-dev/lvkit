"""Tests for case structure code generation helpers.

Focuses on _pre_declare_outputs correctness — the function that emits
`var = None` before match/if statements for variables that may not be
assigned in all branches.
"""

from __future__ import annotations

import ast

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes.case import _pre_declare_outputs
from lvkit.models import CaseOperation, Terminal, Tunnel
from tests.helpers import make_ctx, make_graph_with_edge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _case_node(
    *,
    input_tunnel_outer: str | None = None,
    output_tunnel_outer: str | None = None,
    input_tunnel_inner: str | None = None,
    output_tunnel_inner: str | None = None,
    extra_terminals: list[Terminal] | None = None,
) -> CaseOperation:
    """Build a minimal CaseOperation for _pre_declare_outputs tests."""
    terminals: list[Terminal] = []
    tunnels: list[Tunnel] = []

    if input_tunnel_outer and input_tunnel_inner:
        terminals.append(Terminal(id=input_tunnel_outer, index=0, direction="input"))
        tunnels.append(Tunnel(
            outer_terminal_uid=input_tunnel_outer,
            inner_terminal_uid=input_tunnel_inner,
            tunnel_type="lpTun",
        ))

    if output_tunnel_outer and output_tunnel_inner:
        terminals.append(Terminal(id=output_tunnel_outer, index=1, direction="output"))
        tunnels.append(Tunnel(
            outer_terminal_uid=output_tunnel_outer,
            inner_terminal_uid=output_tunnel_inner,
            tunnel_type="lpTun",
        ))

    terminals.extend(extra_terminals or [])

    return CaseOperation(
        id="case1", name="Case", labels=[],
        terminals=terminals,
        tunnels=tunnels,
        selector_terminal=None,
    )


def _declared_names(stmts: list[ast.stmt]) -> set[str]:
    """Extract variable names from `name = None` assignment statements."""
    names = set()
    for s in stmts:
        if (
            isinstance(s, ast.Assign)
            and len(s.targets) == 1
            and isinstance(s.targets[0], ast.Name)
            and isinstance(s.value, ast.Constant)
            and s.value.value is None
        ):
            names.add(s.targets[0].id)
    return names


# ---------------------------------------------------------------------------
# Output tunnel outers must not suppress pre-declaration
# ---------------------------------------------------------------------------


class TestPreDeclareOutputs:
    def test_output_is_pre_declared(self):
        """Basic case: output binding becomes a pre-declaration."""
        ctx = make_ctx("out_outer")
        node = _case_node(
            output_tunnel_outer="out_outer", output_tunnel_inner="out_inner",
        )
        output_bindings = {"out_outer": "result_var"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        assert "result_var" in _declared_names(stmts)

    def test_output_tunnel_outer_with_bfs_path_to_inner_still_predeclared(self):
        """Regression: output tunnel outer that resolves via BFS to inner value
        must NOT suppress its own pre-declaration.

        Before the fix, ctx.resolve(out_outer) would traverse the self-loop edge
        out_inner → out_outer and find 'frame_var', adding it to input_vars.
        That caused the pre-declaration to be skipped, breaking the code when
        the variable wasn't in scope before the case structure.
        """
        # Set up graph: out_inner → out_outer
        # (self-loop edge, like real case output tunnels)
        graph = make_graph_with_edge("out_inner", "out_outer", "n_inner", "n_outer")
        ctx = CodeGenContext(graph=graph)
        # Simulate: inner was bound during frame body execution
        ctx.bind("out_inner", "frame_var")

        node = _case_node(
            output_tunnel_outer="out_outer", output_tunnel_inner="out_inner",
        )
        output_bindings = {"out_outer": "frame_var"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        # frame_var MUST be pre-declared even though BFS from out_outer finds it
        assert "frame_var" in _declared_names(stmts)

    def test_input_tunnel_outer_var_not_predeclared(self):
        """Variable that flows in via an input tunnel is already defined — skip it."""
        ctx = make_ctx("in_outer", "in_inner")
        ctx.bind("in_outer", "upstream_val")

        node = _case_node(input_tunnel_outer="in_outer", input_tunnel_inner="in_inner")
        # upstream_val is both an output binding AND comes from input tunnel
        output_bindings = {"in_outer": "upstream_val"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        # upstream_val is already defined (it's an input) — don't re-declare it
        assert "upstream_val" not in _declared_names(stmts)

    def test_vi_input_param_not_predeclared(self):
        """Function parameters are always in scope — don't pre-declare them."""
        ctx = make_ctx("out_outer")
        ctx.vi_inputs = [
            Terminal(id="param_t", index=0, direction="input", name="myParam"),
        ]
        node = _case_node(
            output_tunnel_outer="out_outer", output_tunnel_inner="out_inner",
        )
        output_bindings = {"out_outer": "myparam"}  # to_var_name("myParam") = "myparam"

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        assert "myparam" not in _declared_names(stmts)

    def test_each_var_predeclared_once(self):
        """Duplicate var_names in output_bindings produce only one pre-declaration."""
        ctx = make_ctx("t1", "t2")
        node = _case_node(extra_terminals=[
            Terminal(id="t1", index=0, direction="output"),
            Terminal(id="t2", index=1, direction="output"),
        ])
        output_bindings = {"t1": "shared_var", "t2": "shared_var"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        names = _declared_names(stmts)
        assert names == {"shared_var"}


# ---------------------------------------------------------------------------
# Keywords and non-identifiers must be excluded
# ---------------------------------------------------------------------------


class TestPreDeclareSkipsInvalidTargets:
    def test_true_not_predeclared(self):
        """'True' is a keyword — cannot assign to it."""
        ctx = make_ctx()
        node = _case_node()
        output_bindings = {"t1": "True"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        assert "True" not in _declared_names(stmts)

    def test_false_not_predeclared(self):
        """'False' is a keyword — cannot assign to it."""
        ctx = make_ctx()
        node = _case_node()
        output_bindings = {"t1": "False"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        assert "False" not in _declared_names(stmts)

    def test_none_not_predeclared(self):
        """'None' is a keyword — cannot assign to it."""
        ctx = make_ctx()
        node = _case_node()
        output_bindings = {"t1": "None"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        assert "None" not in _declared_names(stmts)

    def test_float_literal_not_predeclared(self):
        """'0.0' is not a valid identifier — cannot appear on left of assignment."""
        ctx = make_ctx()
        node = _case_node()
        output_bindings = {"t1": "0.0"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        # No statements at all (not even something broken)
        assert not stmts

    def test_integer_literal_not_predeclared(self):
        """Numeric string '42' is not a valid identifier."""
        ctx = make_ctx()
        node = _case_node()
        output_bindings = {"t1": "42"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        assert not stmts

    def test_valid_identifier_is_predeclared(self):
        """Control: a normal identifier IS pre-declared."""
        ctx = make_ctx("out_t")
        node = _case_node(extra_terminals=[
            Terminal(id="out_t", index=0, direction="output"),
        ])
        output_bindings = {"out_t": "my_result"}

        stmts = _pre_declare_outputs(node, output_bindings, ctx)

        assert "my_result" in _declared_names(stmts)


# ---------------------------------------------------------------------------
# _build_frame_pattern (#82): OR-patterns for multi-value string/int frames,
# guards for true ranges.
# ---------------------------------------------------------------------------

from lvkit.codegen.nodes.case import _build_frame_pattern  # noqa: E402
from lvkit.models import CaseFrame, SelectorRange  # noqa: E402


def _pattern_src(frame: CaseFrame, selector_var: str = "sel") -> str:
    pattern, guard = _build_frame_pattern(frame, selector_var)
    case = ast.match_case(pattern=pattern, guard=guard, body=[ast.Pass()])
    mod = ast.Match(subject=ast.Name(id=selector_var, ctx=ast.Load()), cases=[case])
    return ast.unparse(ast.fix_missing_locations(
        ast.Module(body=[mod], type_ignores=[])))


class TestBuildFramePattern:
    def test_single_string(self):
        f = CaseFrame(selector_value="bmp", selector_strings=["bmp"])
        assert "case 'bmp':" in _pattern_src(f)

    def test_multi_string_or_pattern(self):
        f = CaseFrame(
            selector_value="jpe", selector_strings=["jpe", "jpeg", "jpg"])
        assert "case 'jpe' | 'jpeg' | 'jpg':" in _pattern_src(f)

    def test_multi_singleton_int_or_pattern(self):
        f = CaseFrame(selector_value="1", selector_ranges=[
            SelectorRange(start=1, end=1), SelectorRange(start=4, end=4),
            SelectorRange(start=8, end=8)])
        assert "case 1 | 4 | 8:" in _pattern_src(f)

    def test_true_range_uses_guard(self):
        f = CaseFrame(selector_value="2..3", selector_ranges=[
            SelectorRange(start=2, end=3)])
        src = _pattern_src(f)
        assert "case _ if 2 <= sel <= 3:" in src

    def test_mixed_singletons_and_range_guard(self):
        f = CaseFrame(selector_value="1", selector_ranges=[
            SelectorRange(start=1, end=1), SelectorRange(start=5, end=7)])
        src = _pattern_src(f)
        assert "case _ if sel == 1 or 5 <= sel <= 7:" in src


# ---------------------------------------------------------------------------
# Type-default pre-declaration for output tunnels (#2 / Use Default If Unwired):
# an unwired frame emits the LabVIEW type default, not None.
# ---------------------------------------------------------------------------

from lvkit.codegen.ast_utils import default_value_expr  # noqa: E402
from lvkit.models import LVType, TunnelTerminal  # noqa: E402


def _lv(underlying, kind="primitive"):
    return LVType(kind=kind, underlying_type=underlying)


class TestDefaultValueExpr:
    def test_scalar_defaults(self):
        assert ast.unparse(default_value_expr(_lv("NumInt32"))) == "0"
        assert ast.unparse(default_value_expr(_lv("NumFloat64"))) == "0.0"
        assert ast.unparse(default_value_expr(_lv("Boolean"))) == "False"
        assert ast.unparse(default_value_expr(_lv("String"))) == "''"

    def test_enum_default_is_zero(self):
        assert ast.unparse(default_value_expr(_lv("", kind="enum"))) == "0"

    def test_complex_and_unknown_default_none(self):
        assert ast.unparse(default_value_expr(_lv("", kind="array"))) == "None"
        assert ast.unparse(default_value_expr(None)) == "None"


class TestPreDeclareOutputsTypeDefault:
    def _pre(self, underlying):
        outer = TunnelTerminal(
            id="o", index=0, direction="output", boundary="outer",
            tunnel_type="csTun", lv_type=_lv(underlying),
        )
        node = CaseOperation(
            id="c", name="Case", labels=[], selector_terminal=None,
            terminals=[outer],
            tunnels=[Tunnel(outer_terminal_uid="o", inner_terminal_uid="i",
                            tunnel_type="csTun")],
        )
        ctx = make_ctx()
        stmts = _pre_declare_outputs(node, {"o": "result"}, ctx)
        mod = ast.fix_missing_locations(ast.Module(body=stmts, type_ignores=[]))
        return ast.unparse(mod)

    def test_int_tunnel_predeclared_zero(self):
        assert self._pre("NumInt32") == "result = 0"

    def test_bool_tunnel_predeclared_false(self):
        assert self._pre("Boolean") == "result = False"

    def test_string_tunnel_predeclared_empty(self):
        assert self._pre("String") == "result = ''"
