"""Code validation pipeline for generated Python."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def deduplicate_imports(code: str) -> str:
    """Remove duplicate import statements from Python code.

    LLMs often generate duplicate imports. This cleans them up while
    preserving order and handling both 'import X' and 'from X import Y'.
    """
    lines = code.split("\n")
    seen_imports: set[str] = set()
    result_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Check if this is an import line
        if stripped.startswith("import ") or stripped.startswith("from "):
            # Normalize for comparison (remove extra spaces)
            normalized = " ".join(stripped.split())
            if normalized in seen_imports:
                continue  # Skip duplicate
            seen_imports.add(normalized)
        result_lines.append(line)

    return "\n".join(result_lines)


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
    4. Completeness check - verify all primitives/SubVIs are handled
    """

    def __init__(self, config: ValidatorConfig) -> None:
        self.config = config

    def validate(
        self,
        code: str,
        module_name: str,
        expected_primitives: list[str] | None = None,
        expected_subvis: list[str] | None = None,
        expected_input_count: int | None = None,
        expected_output_count: int | None = None,
    ) -> ValidationResult:
        """Run full validation pipeline.

        Args:
            code: Python source code to validate
            module_name: Name for the module (used in temp files)
            expected_primitives: Primitive function names that should be used
            expected_subvis: SubVI names that should be called or stubbed
            expected_input_count: Number of inputs the function should have
            expected_output_count: Number of outputs the function should return

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

        # 2. Signature check - reject dict-wrapper anti-pattern
        signature_errors = self._check_signature(
            code, expected_input_count, expected_output_count
        )
        errors.extend(signature_errors)

        # 3. Import resolution (medium cost)
        if self.config.check_imports:
            import_errors = self._check_imports(code)
            errors.extend(import_errors)

        # 4. Type checking with mypy (slowest, run last)
        if self.config.check_types and not errors:
            type_result = self._check_types(code, module_name)
            errors.extend(type_result.errors)
            warnings.extend(type_result.warnings)

        # 5. Completeness check - verify primitives and SubVIs are handled
        if not errors:
            completeness_errors = self._check_completeness(
                code, expected_primitives or [], expected_subvis or []
            )
            errors.extend(completeness_errors)

        # 6. NamedTuple check - verify return type uses NamedTuple
        if expected_output_count and expected_output_count > 0:
            namedtuple_errors = self._check_namedtuple(code, expected_output_count)
            errors.extend(namedtuple_errors)

        # 7. Docstring check - verify function has proper docstring
        docstring_warnings = self._check_docstring(code)
        warnings.extend(docstring_warnings)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_signature(
        self,
        code: str,
        expected_input_count: int | None,
        expected_output_count: int | None = None,
    ) -> list[ValidationError]:
        """Check function signature for anti-patterns.

        Rejects:
        - Single `inputs: dict` parameter when multiple inputs expected
        - Return type of `dict[str, Any]` (should use tuple or named values)
        - Wrong number of return values vs expected outputs
        """
        errors: list[ValidationError] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []  # Syntax errors caught elsewhere

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Check for dict-wrapper input anti-pattern
                params = node.args.args
                if len(params) == 1:
                    param = params[0]
                    if param.annotation:
                        ann = ast.unparse(param.annotation)
                        if "dict" in ann.lower() and param.arg in ("inputs", "input"):
                            if expected_input_count is None or expected_input_count > 1:
                                errors.append(
                                    ValidationError(
                                        category="signature",
                                        message=(
                                            f"Do not wrap inputs in a dict."
                                            f" Use named parameters instead of"
                                            f" '{param.arg}: {ann}'"
                                        ),
                                    )
                                )

                # Check for dict-wrapper output anti-pattern
                if node.returns:
                    ret_ann = ast.unparse(node.returns)
                    if ret_ann.startswith("dict[") or ret_ann == "dict":
                        errors.append(
                            ValidationError(
                                category="signature",
                                message=(
                                    f"Do not return dict."
                                    f" Use tuple or direct values instead of"
                                    f" '-> {ret_ann}'"
                                ),
                            )
                        )

                # NOTE: Output count is checked via NamedTuple field count in
                # _check_namedtuple. Don't check return elements here -
                # NamedTuple returns ONE value containing N fields.

                break  # Only check first function

        return errors

    def _count_return_elements(self, func_node: ast.FunctionDef) -> int | None:
        """Count number of elements in return statement.

        Returns None if can't determine (no return, complex expression, etc.)
        """
        for node in ast.walk(func_node):
            if isinstance(node, ast.Return) and node.value is not None:
                # Check if it's a tuple
                if isinstance(node.value, ast.Tuple):
                    return len(node.value.elts)
                # Single value return
                return 1
        return None

    def _check_completeness(
        self,
        code: str,
        expected_primitives: list[str],
        expected_subvis: list[str],
    ) -> list[ValidationError]:
        """Check that all expected primitives and SubVIs are used.

        Args:
            code: Generated Python code
            expected_primitives: Function names that should be called
            expected_subvis: SubVI names that should be handled

        Returns:
            List of completeness errors
        """
        errors: list[ValidationError] = []

        # Check primitives are used
        for prim_name in expected_primitives:
            # Look for function call pattern
            if f"{prim_name}(" not in code:
                errors.append(
                    ValidationError(
                        category="completeness",
                        message=(
                            f"Primitive '{prim_name}' from VI graph"
                            f" not used in generated code"
                        ),
                    )
                )

        # Check SubVIs are handled (either called or commented as TODO)
        for subvi_name in expected_subvis:
            # Convert to potential function name
            # (same logic as context.py _to_function_name)
            func_name = subvi_name.replace(".vi", "").replace(".VI", "")
            func_name = func_name.replace(" ", "_").replace("-", "_").lower()
            # Remove any non-alphanumeric chars except underscore
            func_name = "".join(c for c in func_name if c.isalnum() or c == "_")
            # Check if it's called, defined, or mentioned in a comment
            if func_name not in code.lower() and subvi_name not in code:
                errors.append(
                    ValidationError(
                        category="completeness",
                        message=(
                            f"SubVI '{subvi_name}' from VI graph"
                            f" not handled in generated code"
                        ),
                    )
                )

        return errors

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

        # Check for known bad import patterns FIRST
        bad_patterns = self._check_bad_import_patterns(code)
        if bad_patterns:
            errors.extend(bad_patterns)

        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Already caught by syntax check
            return errors

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

    def _check_bad_import_patterns(self, code: str) -> list[ValidationError]:
        """Check for known incorrect import patterns.

        Common LLM mistakes:
        - `from typing import Path` (Path is from pathlib)
        - `from pathlib import Optional` (Optional is from typing)
        - `from typing import tuple, dict` (these are builtins)
        """
        errors: list[ValidationError] = []

        # Path should be from pathlib, not typing
        if re.search(r'from\s+typing\s+import\s+[^#\n]*\bPath\b', code):
            errors.append(ValidationError(
                category="import",
                message="Invalid import: Path should be from pathlib, not typing",
            ))

        # typing names imported from pathlib
        typing_names = ['Optional', 'Any', 'List', 'Dict', 'Tuple', 'Union', 'Callable']
        for name in typing_names:
            if re.search(rf'from\s+pathlib\s+import\s+[^#\n]*\b{name}\b', code):
                errors.append(ValidationError(
                    category="import",
                    message=(
                        f"Invalid import: {name} should be from typing, not pathlib"
                    ),
                ))

        # Built-in types imported from typing (Python 3.9+)
        builtin_types = ['tuple', 'list', 'dict', 'set', 'frozenset']
        for name in builtin_types:
            if re.search(rf'from\s+typing\s+import\s+[^#\n]*\b{name}\b', code):
                errors.append(ValidationError(
                    category="import",
                    message=f"Invalid import: {name} is a builtin, not from typing",
                ))

        return errors

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
                    "--disable-error-code=no-any-return",  # Stubs may return Any
                    "--disable-error-code=type-arg",  # Allow dict without type params
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

    def _check_namedtuple(
        self, code: str, expected_output_count: int
    ) -> list[ValidationError]:
        """Check that function returns a NamedTuple with correct field count.

        Args:
            code: Python source code
            expected_output_count: Number of fields expected in NamedTuple

        Returns:
            List of validation errors
        """
        errors: list[ValidationError] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        # Find NamedTuple class definition
        namedtuple_class = None
        namedtuple_fields = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Check if it's a NamedTuple
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "NamedTuple":
                        namedtuple_class = node.name
                        # Count fields (class body with AnnAssign)
                        for item in node.body:
                            if isinstance(item, ast.AnnAssign) and isinstance(
                                item.target, ast.Name
                            ):
                                namedtuple_fields.append(item.target.id)
                        break

        if not namedtuple_class:
            errors.append(ValidationError(
                category="namedtuple",
                message=(
                    f"Missing NamedTuple class for return type."
                    f" Define a class like 'class FuncResult(NamedTuple):'"
                    f" with {expected_output_count} fields."
                ),
            ))
            return errors

        # Check field count (allow extra fields like error_out)
        if len(namedtuple_fields) < expected_output_count:
            errors.append(ValidationError(
                category="namedtuple",
                message=(
                    f"NamedTuple '{namedtuple_class}' has"
                    f" {len(namedtuple_fields)} fields but expected"
                    f" at least {expected_output_count}."
                ),
            ))

        return errors

    def _check_docstring(self, code: str) -> list[ValidationError]:
        """Check that function has proper docstring with Args and Returns.

        Args:
            code: Python source code

        Returns:
            List of validation warnings (not errors)
        """
        warnings: list[ValidationError] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                docstring = ast.get_docstring(node)
                if not docstring:
                    warnings.append(ValidationError(
                        category="docstring",
                        message=f"Function '{node.name}' is missing a docstring.",
                    ))
                    continue

                # Check for Args section
                if "Args:" not in docstring and "args:" not in docstring.lower():
                    if len(node.args.args) > 0:
                        warnings.append(ValidationError(
                            category="docstring",
                            message=(
                                f"Function '{node.name}' docstring"
                                f" is missing Args section."
                            ),
                        ))

                # Check for Returns section
                if node.returns:
                    ret_type = ast.unparse(node.returns)
                    if (
                        ret_type != "None"
                        and "Returns:" not in docstring
                        and "returns:" not in docstring.lower()
                    ):
                        warnings.append(ValidationError(
                            category="docstring",
                            message=(
                                f"Function '{node.name}' docstring"
                                f" is missing Returns section."
                            ),
                        ))

                break  # Only check first public function

        return warnings


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
