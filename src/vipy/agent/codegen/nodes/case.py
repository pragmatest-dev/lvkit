"""Code generator for case structures (if/elif/match-case)."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import CaseFrame, Operation

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen, get_codegen

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

        # Get selector value
        selector_var = None
        if node.selector_terminal:
            selector_var = ctx.resolve(node.selector_terminal)

        if not selector_var:
            selector_var = self._fallback_selector(node, ctx)

        # Error cluster selectors: Python uses exceptions instead.
        # Emit only the "no error" frame body without the if/else guard.
        if self._is_error_selector(selector_var):
            return self._generate_error_unwrap(node, ctx)

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
        statements: list[ast.stmt] = []
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

        Args:
            frame: Case frame with operations
            ctx: Code generation context

        Returns:
            CodeFragment with frame statements
        """
        statements: list[ast.stmt] = []
        bindings: dict[str, str] = {}
        all_imports: set[str] = set()

        for op in frame.operations:
            codegen = get_codegen(op)
            fragment = codegen.generate(op, ctx)

            # Add statements to body
            statements.extend(fragment.statements)

            # Merge bindings (inner operations provide values)
            for term_id, var_name in fragment.bindings.items():
                ctx.bind(term_id, var_name)
                bindings[term_id] = var_name

            # Collect imports
            all_imports.update(fragment.imports)

        return CodeFragment(
            statements=statements,
            bindings=bindings,
            imports=all_imports,
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
        """Check if selector comes from an error cluster.

        In LabVIEW, case structures on error clusters check no-error vs error.
        In Python, exceptions handle this — no guard needed.
        """
        lower = selector_var.lower()
        return "error" in lower and (
            "in" in lower or "out" in lower or "no_error" in lower
        )

    def _generate_error_unwrap(
        self, node: Operation, ctx: CodeGenContext
    ) -> CodeFragment:
        """Generate code for error-selector case: emit "no error" frame only.

        Python exceptions replace LabVIEW's error cluster branching.
        We emit the "no error" (True/first) frame body directly.
        """
        # Bind input tunnels
        self._bind_input_tunnels(node, ctx)

        # Find the "no error" frame (typically True or first frame)
        target_frame = None
        for frame in node.case_frames:
            val = str(frame.selector_value).lower()
            if val in ("true", "no error", "0"):
                target_frame = frame
                break
        if target_frame is None and node.case_frames:
            target_frame = node.case_frames[0]

        if target_frame is None:
            return CodeFragment.empty()

        inner_fragment = self._generate_frame_body(target_frame, ctx)

        # Handle output tunnels
        output_bindings = self._bind_output_tunnels(node, ctx)
        bindings = dict(inner_fragment.bindings)
        bindings.update(output_bindings)

        return CodeFragment(
            statements=inner_fragment.statements or [],
            bindings=bindings,
            imports=inner_fragment.imports,
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
