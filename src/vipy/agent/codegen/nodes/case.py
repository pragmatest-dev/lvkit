"""Code generator for case structures (if/elif/match-case)."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import CaseFrame, Operation

from ..ast_utils import parse_expr
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
            selector_var = "selector"  # Fallback

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

        # Build if statement
        if_stmt = ast.If(
            test=parse_expr(selector_var),
            body=if_body,
            orelse=else_body,
        )
        statements.append(if_stmt)

        # Handle output tunnels
        output_bindings = self._bind_output_tunnels(node, ctx)
        bindings.update(output_bindings)

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

        # Build match statement
        match_stmt = ast.Match(
            subject=parse_expr(selector_var),
            cases=cases,
        )
        statements.append(match_stmt)

        # Handle output tunnels
        output_bindings = self._bind_output_tunnels(node, ctx)
        bindings.update(output_bindings)

        return CodeFragment(
            statements=statements,
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

    def _bind_output_tunnels(
        self, node: Operation, ctx: CodeGenContext
    ) -> dict[str, str]:
        """Bind output tunnel terminals to variable names.

        Case structure tunnels connect outer terminals to variables
        computed inside the cases.

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

            # Check if inner terminal has a value
            inner_var = ctx.resolve(inner_term)
            if inner_var and outer_term:
                # Bind outer terminal to same variable as inner
                # This allows downstream operations to use the value
                bindings[outer_term] = inner_var

        return bindings
