"""Code validation pipeline for generated Python."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ValidationError:
    """A validation error from code checking."""

    category: str  # "syntax", "import", "type"
    message: str
    line: int | None = None
    column: int | None = None


@dataclass
class ValidationResult:
    """Result of code validation."""

    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)


@dataclass
class ValidatorConfig:
    """Configuration for the code validator."""

    output_dir: Path
    check_syntax: bool = True
    check_imports: bool = True
    check_types: bool = True
    mypy_timeout: int = 30


class CodeValidator:
    """Validates generated Python code.

    Runs validation in order of increasing cost with early exit:
    1. Syntax check (instant) - ast.parse
    2. Import resolution (fast) - check imports exist
    3. Type checking (slow) - mypy subprocess
    """

    def __init__(self, config: ValidatorConfig) -> None:
        self.config = config

    def validate(self, code: str, module_name: str) -> ValidationResult:
        """Run full validation pipeline.

        Args:
            code: Python source code to validate
            module_name: Name for the module (used in temp files)

        Returns:
            ValidationResult with is_valid flag and any errors
        """
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        # 1. Syntax check (fastest, run first)
        if self.config.check_syntax:
            syntax_errors = self._check_syntax(code)
            if syntax_errors:
                # No point continuing if syntax is broken
                return ValidationResult(is_valid=False, errors=syntax_errors)

        # 2. Import resolution (medium cost)
        if self.config.check_imports:
            import_errors = self._check_imports(code)
            errors.extend(import_errors)

        # 3. Type checking with mypy (slowest, run last)
        if self.config.check_types and not errors:
            type_result = self._check_types(code, module_name)
            errors.extend(type_result.errors)
            warnings.extend(type_result.warnings)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_syntax(self, code: str) -> list[ValidationError]:
        """Check for Python syntax errors using ast.parse."""
        try:
            ast.parse(code)
            return []
        except SyntaxError as e:
            return [
                ValidationError(
                    category="syntax",
                    message=e.msg or "Syntax error",
                    line=e.lineno,
                    column=e.offset,
                )
            ]

    def _check_imports(self, code: str) -> list[ValidationError]:
        """Check that all imports can be resolved."""
        errors: list[ValidationError] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Already caught by syntax check
            return []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not self._can_import(alias.name):
                        errors.append(
                            ValidationError(
                                category="import",
                                message=f"Cannot resolve import: {alias.name}",
                                line=node.lineno,
                            )
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and not self._can_import_from(
                    node.module, [a.name for a in node.names]
                ):
                    errors.append(
                        ValidationError(
                            category="import",
                            message=f"Cannot resolve: from {node.module} import ...",
                            line=node.lineno,
                        )
                    )

        return errors

    def _can_import(self, module_name: str) -> bool:
        """Check if a module can be imported."""
        # Standard library modules
        if module_name in sys.stdlib_module_names:
            return True

        # Check if it's a converted VI in output dir
        vi_path = self.config.output_dir / f"{module_name}.py"
        if vi_path.exists():
            return True

        # Check for package in output dir
        pkg_path = self.config.output_dir / module_name
        if pkg_path.is_dir() and (pkg_path / "__init__.py").exists():
            return True

        # Check installed packages
        try:
            spec = importlib.util.find_spec(module_name)
            return spec is not None
        except (ModuleNotFoundError, ImportError, ValueError):
            return False

    def _can_import_from(self, module: str, names: list[str]) -> bool:
        """Check if from X import Y can be resolved."""
        if module is None:
            return False

        # Relative imports within generated code
        if module.startswith("."):
            # Relative imports are assumed valid within our package
            return True

        return self._can_import(module)

    def _check_types(self, code: str, module_name: str) -> ValidationResult:
        """Run mypy type checking."""
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        # Write to temp file for mypy
        temp_path = self.config.output_dir / f"_temp_{module_name}.py"
        try:
            temp_path.write_text(code)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mypy",
                    "--ignore-missing-imports",
                    "--no-error-summary",
                    "--no-color",
                    str(temp_path),
                ],
                capture_output=True,
                text=True,
                timeout=self.config.mypy_timeout,
            )

            if result.returncode == 0:
                return ValidationResult(is_valid=True)

            # Parse mypy output
            for line in result.stdout.split("\n"):
                error = self._parse_mypy_line(line)
                if error:
                    if "note:" in line.lower():
                        warnings.append(error)
                    else:
                        errors.append(error)

            return ValidationResult(
                is_valid=len(errors) == 0,
                errors=errors,
                warnings=warnings,
            )

        except subprocess.TimeoutExpired:
            return ValidationResult(
                is_valid=False,
                errors=[
                    ValidationError(
                        category="type",
                        message=f"mypy timed out after {self.config.mypy_timeout}s",
                    )
                ],
            )
        except FileNotFoundError:
            # mypy not installed
            return ValidationResult(
                is_valid=True,
                warnings=[
                    ValidationError(
                        category="type",
                        message="mypy not installed, skipping type check",
                    )
                ],
            )
        finally:
            temp_path.unlink(missing_ok=True)

    def _parse_mypy_line(self, line: str) -> ValidationError | None:
        """Parse a mypy output line into a ValidationError."""
        # Format: file.py:line: error: message
        match = re.match(r".*:(\d+):\s*(error|warning|note):\s*(.+)", line)
        if match:
            return ValidationError(
                category="type",
                message=match.group(3),
                line=int(match.group(1)),
            )
        return None


class ErrorFormatter:
    """Formats validation errors for LLM consumption."""

    @staticmethod
    def format(errors: list[ValidationError]) -> str:
        """Format errors into LLM-friendly text.

        Groups errors by category and includes line numbers.
        """
        if not errors:
            return "No errors found."

        # Group by category
        by_category: dict[str, list[ValidationError]] = {}
        for error in errors:
            by_category.setdefault(error.category, []).append(error)

        lines = []
        for category, category_errors in by_category.items():
            lines.append(f"## {category.upper()} ERRORS:")
            for error in category_errors:
                location = ""
                if error.line:
                    location = f" (line {error.line}"
                    if error.column:
                        location += f", column {error.column}"
                    location += ")"
                lines.append(f"  - {error.message}{location}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def format_with_code(
        code: str,
        errors: list[ValidationError],
    ) -> str:
        """Format errors with the problematic code for LLM fixing."""
        error_text = ErrorFormatter.format(errors)

        return f"""The following Python code has errors:

```python
{code}
```

{error_text}

Please fix these errors and return the corrected code.
Keep the same function signature and logic.
Output ONLY the corrected Python code, no explanations."""
