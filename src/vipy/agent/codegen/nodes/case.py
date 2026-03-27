"""Code generator for case structures (if/elif/match-case)."""

from __future__ import annotations

import ast
import warnings
from typing import TYPE_CHECKING

from vipy.graph_types import CaseFrame, Operation

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class CaseCodeGen(NodeCodeGen):
    """Generate code for LabVIEW case structures.

    Generates Python match-case (Python 3.10+) for integer/enum selectors,
    or if-elif-else for boolean selectors.
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a case structure node.

        Args:
            node: Operation with case_frames and selector_terminal
            ctx: Code generation context

        Returns:
            CodeFragment with match-case or if-elif statements
        """
        if not node.case_frames:
            return CodeFragment.empty()

        # Error cluster selectors: detect via type, not resolved name.
        # In LabVIEW, error clusters are values; in Python, exceptions.
        if self._is_error_selector_by_type(node, ctx):
            return self._generate_error_case(node, ctx)

        # Get selector value
        selector_var = None
        if node.selector_terminal:
            selector_var = ctx.resolve(node.selector_terminal)

        if not selector_var:
            selector_var = self._fallback_selector(node, ctx)

        # Determine if this is boolean or multi-case
        if self._is_boolean_selector(node.case_frames):
            return self._generate_if_else(node, selector_var, ctx)
        else:
            return self._generate_match_case(node, selector_var, ctx)

    def _is_boolean_selector(self, frames: list[CaseFrame]) -> bool:
        """Check if case structure uses boolean selector."""
        selector_values = {str(f.selector_value) for f in frames}
        # Boolean if only True/False (with optional Default)
        bool_values = {"True", "False", "Default", "true", "false", "default"}
        return selector_values <= bool_values and len(frames) <= 3

    def _generate_if_else(
        self, node: Operation, selector_var: str, ctx: CodeGenContext
    ) -> CodeFragment:
        """Generate if-else statement for boolean selector.

        Args:
            node: Case structure operation
            selector_var: Variable name for selector
            ctx: Code generation context

        Returns:
            CodeFragment with if-else
        """
        statements: list[ast.stmt] = []
        bindings: dict[str, str] = {}
        all_imports: set[str] = set()

        # Bind input tunnels so inner operations can resolve them
        self._bind_input_tunnels(node, ctx)

        # Find true and false frames
        true_frame = None
        false_frame = None
        default_frame = None

        for frame in node.case_frames:
            val = str(frame.selector_value).lower()
            if val == "true":
                true_frame = frame
            elif val == "false":
                false_frame = frame
            elif "default" in val:
                default_frame = frame

        # Build if body
        if_body: list[ast.stmt] = []
        if true_frame:
            inner_fragment = self._generate_frame_body(true_frame, ctx)
            if_body = inner_fragment.statements or [ast.Pass()]
            bindings.update(inner_fragment.bindings)
            all_imports.update(inner_fragment.imports)
        else:
            if_body = [ast.Pass()]

        # Build else body
        else_body: list[ast.stmt] = []
        else_frame = false_frame or default_frame
        if else_frame:
            inner_fragment = self._generate_frame_body(else_frame, ctx)
            else_body = inner_fragment.statements or [ast.Pass()]
            bindings.update(inner_fragment.bindings)
            all_imports.update(inner_fragment.imports)
        else:
            else_body = [ast.Pass()]

        # Handle output tunnels and pre-declare variables that exit the case
        output_bindings = self._bind_output_tunnels(node, ctx)
        bindings.update(output_bindings)
        pre_decls = self._pre_declare_outputs(node, output_bindings, ctx)
        statements.extend(pre_decls)

        # Simplify pass-only branches:
        # if x: pass; else: work() → if not x: work()
        # if x: work(); else: pass → if x: work()
        if_is_pass = len(if_body) == 1 and isinstance(if_body[0], ast.Pass)
        else_is_pass = len(else_body) == 1 and isinstance(else_body[0], ast.Pass)

        if if_is_pass and not else_is_pass:
            # Negate condition, use else body as if body
            if_stmt = ast.If(
                test=ast.UnaryOp(
                    op=ast.Not(), operand=parse_expr(selector_var)
                ),
                body=else_body,
                orelse=[],
            )
        elif else_is_pass and not if_is_pass:
            # Drop empty else
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
        self, node: Operation, selector_var: str, ctx: CodeGenContext
    ) -> CodeFragment:
        """Generate match-case statement (Python 3.10+).

        Args:
            node: Case structure operation
            selector_var: Variable name for selector
            ctx: Code generation context

        Returns:
            CodeFragment with match-case
        """
        bindings: dict[str, str] = {}
        all_imports: set[str] = set()

        # Bind input tunnels so inner operations can resolve them
        self._bind_input_tunnels(node, ctx)

        # Build match cases
        cases: list[ast.match_case] = []

        for frame in node.case_frames:
            # Build pattern
            selector_str = str(frame.selector_value)
            pattern: ast.pattern
            if frame.is_default or selector_str.lower() == "default":
                # Default case uses wildcard pattern
                pattern = ast.MatchAs(pattern=None, name=None)
            else:
                # Try to parse selector value
                pattern = self._build_match_pattern(selector_str)

            # Build case body
            inner_fragment = self._generate_frame_body(frame, ctx)
            body = inner_fragment.statements or [ast.Pass()]
            bindings.update(inner_fragment.bindings)
            all_imports.update(inner_fragment.imports)

            cases.append(ast.match_case(pattern=pattern, guard=None, body=body))

        # Handle output tunnels and pre-declare variables that exit the case
        output_bindings = self._bind_output_tunnels(node, ctx)
        bindings.update(output_bindings)
        pre_decls = self._pre_declare_outputs(node, output_bindings, ctx)

        # Build match statement
        match_stmt = ast.Match(
            subject=parse_expr(selector_var),
            cases=cases,
        )

        return CodeFragment(
            statements=pre_decls + [match_stmt],
            bindings=bindings,
            imports=all_imports,
        )

    def _build_match_pattern(self, selector_value: str) -> ast.pattern:
        """Build AST pattern for match case.

        Args:
            selector_value: Selector value string ("0", "1", "Red", etc.)

        Returns:
            AST pattern node
        """
        # Try to parse as integer
        try:
            int_val = int(selector_value)
            return ast.MatchValue(value=ast.Constant(value=int_val))
        except ValueError:
            pass

        # Handle string value
        return ast.MatchValue(value=ast.Constant(value=selector_value))

    def _generate_frame_body(
        self, frame: CaseFrame, ctx: CodeGenContext
    ) -> CodeFragment:
        """Generate code for a single case frame.

        Uses the same tiered topological sort as the top-level body
        so that inner operations respect data dependencies (e.g., a
        case selector depending on a sibling primitive result).

        Args:
            frame: Case frame with operations
            ctx: Code generation context

        Returns:
            CodeFragment with frame statements
        """
        # Lazy import: builder → get_codegen → case creates a cycle
        # at module level. Safe inside method.
        from ..builder import generate_body

        body = generate_body(frame.operations, ctx)

        # Collect bindings that were set during body generation
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

    @staticmethod
    def _fallback_selector(node: Operation, ctx: CodeGenContext) -> str:
        """Try to derive a meaningful selector name when resolve() fails.

        Traces the wire source to find the parent operation name and
        builds a variable name from it. Falls back to "selector" only
        when no information is available.
        """
        from ..ast_utils import to_var_name

        sel_term = node.selector_terminal
        flow = ctx.get_source(sel_term) if sel_term else None
        if flow:
            if flow.src_parent_name:
                return to_var_name(flow.src_parent_name)
            # Try labels
            for label in flow.src_parent_labels:
                if label not in ("Primitive", "operation"):
                    return to_var_name(label)
        return "selector"

    @staticmethod
    def _is_error_selector(selector_var: str) -> bool:
        """Check if resolved selector name looks like an error cluster.

        Fallback heuristic — prefer _is_error_selector_by_type().
        """
        lower = selector_var.lower()
        return "error" in lower and (
            "in" in lower or "out" in lower or "no_error" in lower
        )

    @staticmethod
    def _is_error_selector_by_type(
        node: Operation, ctx: CodeGenContext,
    ) -> bool:
        """Check if the selector terminal carries an error cluster type.

        Error clusters have fields ['status', 'code', 'source'].
        This is more reliable than checking the resolved variable name
        because error output terminals are often unbound in Python
        (exceptions replace error cluster values).
        """
        sel_id = node.selector_terminal
        if not sel_id or ctx.graph is None:
            return False
        # Find the selector terminal on this node
        for term in node.terminals:
            if term.id == sel_id and term.lv_type:
                lv = term.lv_type
                if lv.kind == "cluster" and lv.fields:
                    field_names = {f.name for f in lv.fields}
                    if {"status", "code", "source"} <= field_names:
                        return True
        return False

    def _generate_error_case(
        self, node: Operation, ctx: CodeGenContext,
    ) -> CodeFragment:
        """Generate code for an error-cluster case structure.

        In LabVIEW, error clusters are values checked by case structures.
        In Python, errors are exceptions — no value to test. We emit only
        the no-error frame body (the happy path). If the error frame had
        operations, we add a comment noting the omitted logic.

        96% of error frames are empty (45/47 in TestCase.lvclass).
        """
        self._bind_input_tunnels(node, ctx)

        # Identify no-error and error frames
        no_error_frame = None
        error_frame = None
        for frame in node.case_frames:
            val = str(frame.selector_value).lower()
            if val in ("true", "no error", "0"):
                no_error_frame = frame
            elif val in ("false", "error", "1", "default"):
                error_frame = frame

        if no_error_frame is None and node.case_frames:
            no_error_frame = node.case_frames[0]

        if no_error_frame is None:
            return CodeFragment.empty()

        statements: list[ast.stmt] = []

        # Log if the error frame had real operations (AST can't emit
        # comments, so we just note it at generation time).
        if (
            error_frame is not None
            and error_frame.operations
            and len(error_frame.operations) > 0
        ):
            op_names = ", ".join(
                op.name or op.node_type or "?"
                for op in error_frame.operations
            )
            warnings.warn(
                f"LV error frame omitted in {node.id}: {op_names}",
                stacklevel=2,
            )

        # Emit no-error frame body
        no_error_fragment = self._generate_frame_body(no_error_frame, ctx)
        statements.extend(no_error_fragment.statements or [])

        output_bindings = self._bind_output_tunnels(node, ctx)
        bindings = dict(no_error_fragment.bindings)
        bindings.update(output_bindings)

        return CodeFragment(
            statements=statements,
            bindings=bindings,
            imports=no_error_fragment.imports,
        )

    def _pre_declare_outputs(
        self,
        node: Operation,
        output_bindings: dict[str, str],
        ctx: CodeGenContext,
    ) -> list[ast.stmt]:
        """Pre-declare output tunnel variables before the case structure.

        In LabVIEW, every output tunnel guarantees a value exits regardless
        of which frame runs. In Python, variables assigned inside an if/else
        branch don't exist if that branch didn't execute. Pre-declaring with
        None ensures the variable is always in scope.

        Skips variables that are already in scope (function parameters,
        pass-through tunnels where input == output variable name).
        """
        param_names = {to_var_name(inp.name or "") for inp in ctx.vi_inputs}
        # Collect input tunnel variable names (already in scope)
        input_vars: set[str] = set()
        for tunnel in node.tunnels:
            outer_var = ctx.resolve(tunnel.outer_terminal_uid)
            if outer_var:
                input_vars.add(outer_var)

        pre_decls: list[ast.stmt] = []
        declared: set[str] = set()
        for _outer_term, var_name in output_bindings.items():
            if not var_name or var_name == "None":
                continue
            if var_name in param_names:
                continue  # Function parameter — already in scope
            if var_name in input_vars:
                continue  # Pass-through — same var enters and exits
            if var_name in declared:
                continue
            declared.add(var_name)
            pre_decls.append(
                build_assign(var_name, ast.Constant(value=None))
            )

        return pre_decls

    def _bind_input_tunnels(
        self, node: Operation, ctx: CodeGenContext
    ) -> None:
        """Bind input tunnel inner terminals to their outer values.

        For each tunnel, resolves the outer terminal and binds all
        inner terminals to the same value. This allows inner operations
        to resolve their inputs through the tunnel chain.
        """
        for tunnel in node.tunnels:
            outer_term = tunnel.outer_terminal_uid
            inner_term = tunnel.inner_terminal_uid
            if not outer_term or not inner_term:
                continue
            outer_var = ctx.resolve(outer_term)
            if outer_var:
                ctx.bind(inner_term, outer_var)

    def _bind_output_tunnels(
        self, node: Operation, ctx: CodeGenContext
    ) -> dict[str, str]:
        """Bind output tunnel terminals to variable names.

        Case structure tunnels connect outer terminals to variables
        computed inside the cases. Each outer terminal may have multiple
        inner terminals (one per frame). We take the first non-None
        resolved value to avoid a later frame's None overwriting a
        valid binding from an earlier frame.

        Args:
            node: Case structure operation
            ctx: Code generation context

        Returns:
            Dict of terminal_id -> variable_name bindings
        """
        bindings: dict[str, str] = {}

        for tunnel in node.tunnels:
            outer_term = tunnel.outer_terminal_uid
            inner_term = tunnel.inner_terminal_uid
            if not outer_term or not inner_term:
                continue

            # Skip if we already have a real value for this outer terminal
            if outer_term in bindings and bindings[outer_term] != "None":
                continue

            # Try inner → outer (standard: value produced inside case exits)
            inner_var = ctx.resolve(inner_term)
            if inner_var:
                bindings[outer_term] = inner_var
            else:
                # Try outer → inner (caseSel: value on case boundary
                # enters sRN inside the case frame)
                outer_var = ctx.resolve(outer_term)
                if outer_var:
                    ctx.bind(inner_term, outer_var)

        return bindings
