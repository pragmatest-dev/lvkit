"""Utilities for parsing generated Python code."""

from __future__ import annotations

import ast
import re


def parse_param_display_names(docstring: str) -> dict[str, str]:
    """Parse :param name: "Display Name" from docstring.

    Returns dict mapping code_name -> display_name.
    """
    display_names: dict[str, str] = {}
    # Match :param name: "Display Name" or :param name: 'Display Name'
    for match in re.finditer(r':param\s+(\w+):\s*["\']([^"\']+)["\']', docstring):
        code_name, display_name = match.groups()
        display_names[code_name] = display_name
    return display_names


def parse_output_display_names(docstring: str) -> list[str]:
    """Parse :return: ("Name1", "Name2") from docstring.

    Returns list of display names in order.
    """
    # Match :return: ("Name1", "Name2", ...)
    match = re.search(r':return:\s*\(([^)]+)\)', docstring)
    if match:
        inner = match.group(1)
        names = []
        # Parse quoted strings
        for quoted in re.findall(r'["\']([^"\']+)["\']', inner):
            names.append(quoted)
        return names
    return []


def snake_to_title(name: str) -> str:
    """Convert snake_case to Title Case."""
    return " ".join(word.capitalize() for word in name.split("_"))


def extract_params(func: ast.FunctionDef) -> list[tuple[str, str]]:
    """Extract parameter names and types from function definition.

    Returns list of (name, type) tuples.
    """
    params = []
    for arg in func.args.args:
        name = arg.arg
        if arg.annotation:
            type_str = ast.unparse(arg.annotation)
        else:
            type_str = "Any"
        params.append((name, type_str))
    return params


def extract_params_with_display(
    func: ast.FunctionDef, display_names: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Extract parameters with display names.

    Returns list of (code_name, type, display_name).
    """
    params = []
    for arg in func.args.args:
        name = arg.arg
        if arg.annotation:
            type_str = ast.unparse(arg.annotation)
        else:
            type_str = "Any"
        # Use display name from docstring, or convert snake_case to Title Case
        display = display_names.get(name, snake_to_title(name))
        params.append((name, type_str, display))
    return params


def find_namedtuple_fields(tree: ast.Module, class_name: str) -> list[tuple[str, str]] | None:
    """Find NamedTuple class definition and extract its fields.

    Args:
        tree: AST module
        class_name: Name of the NamedTuple class to find

    Returns:
        List of (field_name, field_type) tuples, or None if not found
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            # Check if it inherits from NamedTuple
            is_namedtuple = any(
                isinstance(base, ast.Name) and base.id == "NamedTuple"
                for base in node.bases
            )
            if not is_namedtuple:
                continue

            # Extract annotated fields
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id
                    field_type = ast.unparse(item.annotation)
                    fields.append((field_name, field_type))

            if fields:
                return fields

    return None


def extract_returns(func: ast.FunctionDef, tree: ast.Module | None = None) -> list[tuple[str, str]]:
    """Extract return type and names from function definition.

    Parses NamedTuple class definition to get field names and types.
    Falls back to generic names if no NamedTuple found.

    Returns list of (name, type) tuples.
    """
    if not func.returns:
        return []

    return_annotation = ast.unparse(func.returns)

    if return_annotation == "None":
        return []

    # Try to find NamedTuple class with this name
    if tree:
        namedtuple_fields = find_namedtuple_fields(tree, return_annotation)
        if namedtuple_fields:
            return namedtuple_fields

    # Check for tuple return (multiple outputs) - fallback
    if return_annotation.startswith("tuple["):
        inner = return_annotation[6:-1]  # Remove "tuple[" and "]"
        types = [t.strip() for t in inner.split(",")]
        return [(f"result_{i}", t) for i, t in enumerate(types)]

    # Single return value
    return [("result", return_annotation)]


def extract_returns_with_display(
    func: ast.FunctionDef, tree: ast.Module | None, display_names: list[str]
) -> list[tuple[str, str, str]]:
    """Extract returns with display names.

    Returns list of (code_name, type, display_name).
    """
    if not func.returns:
        return []

    return_annotation = ast.unparse(func.returns)

    if return_annotation == "None":
        return []

    # Try to find NamedTuple class with this name
    fields: list[tuple[str, str]] = []
    if tree:
        namedtuple_fields = find_namedtuple_fields(tree, return_annotation)
        if namedtuple_fields:
            fields = namedtuple_fields

    # Fallback to tuple parsing
    if not fields and return_annotation.startswith("tuple["):
        inner = return_annotation[6:-1]
        types = [t.strip() for t in inner.split(",")]
        fields = [(f"result_{i}", t) for i, t in enumerate(types)]

    # Single return
    if not fields:
        fields = [("result", return_annotation)]

    # Add display names
    result = []
    for i, (code_name, type_str) in enumerate(fields):
        if i < len(display_names):
            display = display_names[i]
        else:
            display = snake_to_title(code_name)
        result.append((code_name, type_str, display))
    return result


def extract_function_signature(
    code: str,
) -> tuple[str | None, list[tuple[str, str, str]], list[tuple[str, str, str]], dict[str, list[tuple[int, str]]]]:
    """Extract function signature from generated Python code.

    Parses the AST to find the main function and extract its
    name, parameters, return type, and any enum-like dict mappings.
    Also extracts display names from docstring.

    Args:
        code: Generated Python code

    Returns:
        Tuple of (function_name, inputs, outputs, enums) where:
        - function_name: Name of the function, or None if parsing failed
        - inputs: List of (code_name, type, display_name) for parameters
        - outputs: List of (code_name, type, display_name) for return values
        - enums: Dict mapping param name to list of (value, label) tuples
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None, [], [], {}

    # Find the first function definition (the main VI function)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_name = node.name
            docstring = ast.get_docstring(node) or ""

            # Extract params and outputs with display names from docstring
            param_display = parse_param_display_names(docstring)
            output_display = parse_output_display_names(docstring)

            inputs = extract_params_with_display(node, param_display)
            outputs = extract_returns_with_display(node, tree, output_display)
            enums = _extract_enums(node, [(n, t) for n, t, _ in inputs])
            return func_name, inputs, outputs, enums

    return None, [], [], {}


def _extract_enums(
    func: ast.FunctionDef,
    params: list[tuple[str, str]],
) -> dict[str, list[tuple[int, str]]]:
    """Extract enum-like dict mappings from function body.

    Looks for dict literals with integer keys that might represent
    enum values for function parameters.

    Args:
        func: Function AST node
        params: List of (name, type) for parameters

    Returns:
        Dict mapping parameter name to list of (value, label) tuples
    """
    enums: dict[str, list[tuple[int, str]]] = {}

    # Get parameter names that are int type (potential enums)
    int_params = {name for name, typ in params if typ in ("int", "int32", "integer")}
    if not int_params:
        return enums

    # Walk the function body looking for dict literals with int keys
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            # Check if all keys are integer constants
            if not node.keys or not all(
                isinstance(k, ast.Constant) and isinstance(k.value, int)
                for k in node.keys
                if k is not None
            ):
                continue

            # Extract key-value pairs
            options: list[tuple[int, str]] = []
            for key, value in zip(node.keys, node.values):
                if key is None:
                    continue
                int_key = key.value  # type: ignore
                # Try to get a meaningful label from the value
                label = _value_to_label(value, int_key)
                options.append((int_key, label))

            if options:
                # Sort by key value
                options.sort(key=lambda x: x[0])
                # Try to match this dict to a parameter
                # For now, assign to first int param that doesn't have enums yet
                for param_name in int_params:
                    if param_name not in enums:
                        enums[param_name] = options
                        break

    return enums


def _value_to_label(value: ast.expr, key: int) -> str:
    """Convert AST value to human-readable label."""
    if isinstance(value, ast.Constant):
        return str(value.value)
    if isinstance(value, ast.Call):
        # e.g., Path.home() -> "Home"
        if isinstance(value.func, ast.Attribute):
            return value.func.attr.replace("_", " ").title()
        if isinstance(value.func, ast.Name):
            return value.func.id.replace("_", " ").title()
    if isinstance(value, ast.BinOp):
        # e.g., Path.home() / 'Desktop' -> try to extract string
        if isinstance(value.right, ast.Constant):
            return str(value.right.value)
    if isinstance(value, ast.Attribute):
        return value.attr.replace("_", " ").title()
    if isinstance(value, ast.Name):
        return value.id.replace("_", " ").title()
    # Fallback to key number
    return f"Option {key}"
