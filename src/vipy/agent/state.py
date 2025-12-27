"""Conversion state tracking for the agent loop."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConvertedModule:
    """Tracks a successfully converted VI/class/library."""

    name: str
    output_path: Path
    module_name: str  # Python module name (for imports)
    exports: list[str]  # Function/class names exported
    signature: str  # Primary function/class signature
    imports: list[str]  # Required import statements


class ConversionState:
    """Tracks conversion progress across VIs.

    Maintains state about which VIs have been converted successfully,
    allowing dependent VIs to generate proper import statements.
    """

    def __init__(self) -> None:
        self._converted: dict[str, ConvertedModule] = {}
        self._failed: set[str] = set()

    def mark_converted(self, name: str, output_path: Path) -> None:
        """Mark a VI as successfully converted.

        Parses the generated file to extract signature and exports.
        """
        code = output_path.read_text()
        exports = self._extract_exports(code)
        signature = self._extract_primary_signature(code)
        imports = self._extract_imports(code)

        self._converted[name] = ConvertedModule(
            name=name,
            output_path=output_path,
            module_name=output_path.stem,
            exports=exports,
            signature=signature,
            imports=imports,
        )

    def mark_failed(self, name: str) -> None:
        """Mark a VI as failed to convert."""
        self._failed.add(name)

    def is_converted(self, name: str) -> bool:
        """Check if a VI has been successfully converted."""
        return name in self._converted

    def is_failed(self, name: str) -> bool:
        """Check if a VI failed to convert."""
        return name in self._failed

    def get_module(self, name: str) -> ConvertedModule | None:
        """Get the converted module info for a VI."""
        return self._converted.get(name)

    def get_signature(self, name: str) -> str:
        """Get the primary signature for a converted VI."""
        module = self._converted.get(name)
        return module.signature if module else ""

    def get_import_statement(self, name: str, from_module: str | None = None) -> str:
        """Get import statement for a converted VI.

        Args:
            name: Name of the converted VI
            from_module: Module requesting the import (for relative imports)

        Returns:
            Import statement like 'from .module import function'
        """
        module = self._converted.get(name)
        if not module:
            return ""

        # Build relative import
        if module.exports:
            primary_export = module.exports[0]
            return f"from .{module.module_name} import {primary_export}"

        return f"from . import {module.module_name}"

    def get_all_converted(self) -> list[ConvertedModule]:
        """Get all successfully converted modules."""
        return list(self._converted.values())

    def get_all_failed(self) -> list[str]:
        """Get all failed VI names."""
        return list(self._failed)

    def _extract_exports(self, code: str) -> list[str]:
        """Extract public function/class names from code."""
        exports = []
        try:
            tree = ast.parse(code)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    exports.append(node.name)
                elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    exports.append(node.name)
        except SyntaxError:
            pass
        return exports

    def _extract_primary_signature(self, code: str) -> str:
        """Extract the primary function/class signature.

        Returns the first public function or class definition.
        """
        try:
            tree = ast.parse(code)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    return self._format_function_signature(node)
                elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    return self._format_class_signature(node)
        except SyntaxError:
            pass
        return ""

    def _format_function_signature(self, node: ast.FunctionDef) -> str:
        """Format a function definition as a signature string."""
        args = []
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {ast.unparse(arg.annotation)}"
            args.append(arg_str)

        # Handle *args and **kwargs
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")

        returns = ""
        if node.returns:
            returns = f" -> {ast.unparse(node.returns)}"

        return f"def {node.name}({', '.join(args)}){returns}"

    def _format_class_signature(self, node: ast.ClassDef) -> str:
        """Format a class definition as a signature string."""
        bases = [ast.unparse(base) for base in node.bases]
        if bases:
            return f"class {node.name}({', '.join(bases)})"
        return f"class {node.name}"

    def _extract_imports(self, code: str) -> list[str]:
        """Extract all import statements from code."""
        imports = []
        try:
            tree = ast.parse(code)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports.append(ast.unparse(node))
        except SyntaxError:
            pass
        return imports


@dataclass
class ConversionProgress:
    """Overall conversion progress summary."""

    total: int
    converted: int
    failed: int
    pending: int

    @property
    def success_rate(self) -> float:
        """Return success rate as percentage."""
        if self.total == 0:
            return 0.0
        return (self.converted / self.total) * 100


def get_progress(state: ConversionState, total_vis: int) -> ConversionProgress:
    """Get conversion progress from state."""
    converted = len(state.get_all_converted())
    failed = len(state.get_all_failed())
    return ConversionProgress(
        total=total_vis,
        converted=converted,
        failed=failed,
        pending=total_vis - converted - failed,
    )
