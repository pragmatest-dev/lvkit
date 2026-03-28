"""Function builder for VI-to-Python conversion.

Builds function signatures, bodies, and return statements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast_utils import to_function_name
from .dataflow import DataFlowTracer
from .imports import ImportBuilder


@dataclass
class Parameter:
    """A function parameter."""
    name: str
    type_hint: str
    default: str | None = None
    required: bool = True


@dataclass
class OutputField:
    """A named output field."""
    name: str
    type_hint: str
    source_var: str | None = None  # What variable provides this value


@dataclass
class FunctionDef:
    """A complete function definition."""
    name: str
    params: list[Parameter]
    return_type: str
    docstring: str
    body_lines: list[str]
    result_class_name: str | None = None
    result_fields: list[OutputField] = field(default_factory=list)


class FunctionBuilder:
    """Builds Python function definitions from VI data.

    Handles:
    - Function signature with typed parameters
    - NamedTuple result type
    - Docstring generation
    - Return statement with traced sources
    """

    TYPE_MAP = {
        "Path": "Path",
        "String": "str",
        "Boolean": "bool",
        "NumInt32": "int",
        "NumInt16": "int",
        "NumFloat64": "float",
        "NumFloat32": "float",
        "Array": "list",
        "Cluster": "dict",
        "Void": "None",
        "path": "Path",
        "string": "str",
        "str": "str",
        "bool": "bool",
        "boolean": "bool",
        "int": "int",
        "float": "float",
    }

    # Map LabVIEW control types to type names
    CONTROL_TYPE_MAP = {
        "stdPath": "Path",
        "stdString": "String",
        "stdBool": "Boolean",
        "stdNum": "NumFloat64",
        "stdInt32": "NumInt32",
        "stdInt16": "NumInt16",
        "stdFloat64": "NumFloat64",
        "stdFloat32": "NumFloat32",
        "stdArray": "Array",
        "stdClust": "Cluster",
    }

    DEFAULT_VALUES = {
        "str": '""',
        "int": "0",
        "float": "0.0",
        "bool": "False",
        "list": "[]",
        "dict": "{}",
        "Path": 'Path(".")',
        "None": "None",
        "Any": "None",
    }

    def __init__(self, tracer: DataFlowTracer, imports: ImportBuilder):
        """Initialize builder.

        Args:
            tracer: DataFlowTracer for resolving output sources
            imports: ImportBuilder for adding required imports
        """
        self.tracer = tracer
        self.imports = imports

    def build(
        self,
        vi_name: str,
        inputs: list[dict],
        outputs: list[dict],
        body_lines: list[str],
    ) -> FunctionDef:
        """Build a complete function definition.

        Args:
            vi_name: VI name for function name derivation
            inputs: VI input definitions
            outputs: VI output definitions
            body_lines: Generated body code lines

        Returns:
            FunctionDef ready for rendering
        """
        func_name = to_function_name(vi_name)
        result_class = self._to_class_name(vi_name) + "Result"

        # Build parameters
        params = self._build_params(inputs)

        # Build output fields with traced sources
        result_fields = self._build_outputs(outputs)

        # Build docstring
        docstring = self._build_docstring(vi_name)

        # Determine return type
        if result_fields:
            return_type = result_class
            self.imports.add_namedtuple()
        else:
            return_type = "None"
            result_class = None

        return FunctionDef(
            name=func_name,
            params=params,
            return_type=return_type,
            docstring=docstring,
            body_lines=body_lines,
            result_class_name=result_class,
            result_fields=result_fields,
        )

    def _build_params(self, inputs: list[dict]) -> list[Parameter]:
        """Build function parameters from VI inputs."""
        params = []

        for inp in inputs:
            name = self._to_var_name(inp.get("name", "input"))
            # Use LVType if available, otherwise fall back to manual mapping
            lv_type_obj = inp.get("lv_type")
            if lv_type_obj:
                type_hint = lv_type_obj.to_python()
            else:
                type_hint = self._map_type(inp.get("type", "Any"))
            wiring_rule = inp.get("wiring_rule", 0)
            default = inp.get("default_value")

            required = wiring_rule == 1  # 1 = Required

            if not required and default is None:
                default = self.DEFAULT_VALUES.get(type_hint, "None")

            params.append(Parameter(
                name=name,
                type_hint=type_hint,
                default=default if not required else None,
                required=required,
            ))

        # Sort: required first, then optional
        params.sort(key=lambda p: (0 if p.required else 1, p.name))

        return params

    def _build_outputs(self, outputs: list[dict]) -> list[OutputField]:
        """Build output fields with traced sources."""
        fields = []

        for out in outputs:
            name = self._to_var_name(out.get("name", "output"))
            # Use LVType (unified type system) if available
            lv_type_obj = out.get("lv_type")
            if lv_type_obj:
                type_hint = lv_type_obj.to_python()
            # Fallback: manual mapping
            else:
                lv_type = out.get("type")
                if not lv_type:
                    control_type = out.get("control_type")
                    if control_type:
                        lv_type = self.CONTROL_TYPE_MAP.get(control_type, "Any")
                    else:
                        lv_type = "Any"
                type_hint = self._map_type(lv_type)
            out_id = out.get("id")

            # Find the terminal for this output and trace its source
            source_var = None

            # First try: look for child terminal with parent_id == out_id
            for term in self.tracer._context.get("terminals", []):
                if term.get("parent_id") == out_id:
                    source_var = self.tracer.resolve_source(term.get("id"))
                    break

            # Second try: out_id might BE the terminal ID directly
            if source_var is None and out_id is not None:
                source_var = self.tracer.resolve_source(out_id)

            fields.append(OutputField(
                name=name,
                type_hint=type_hint,
                source_var=source_var,
            ))

        return fields

    def _build_docstring(self, vi_name: str) -> str:
        """Build docstring from VI name."""
        # Convert VI name to readable description
        name = vi_name.replace(".vi", "").replace(".VI", "")
        name = name.replace("_", " ").replace("-", " ")
        # Capitalize first letter of each word
        words = name.split()
        if words:
            return " ".join(words) + "."
        return "Generated function."

    def _to_class_name(self, name: str) -> str:
        """Convert VI name to PascalCase class name."""
        name = name.replace(".vi", "").replace(".VI", "")
        if ":" in name:
            name = name.split(":")[-1]
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(w.capitalize() for w in words) or "VI"

    def _to_var_name(self, name: str) -> str:
        """Convert terminal name to Python variable name."""
        if not name:
            return "value"
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "var_" + result
        return result or "value"

    def _map_type(self, lv_type: str) -> str:
        """Map LabVIEW type to Python type."""
        return self.TYPE_MAP.get(lv_type, "Any")

    def render_result_class(self, func_def: FunctionDef) -> list[str]:
        """Render the NamedTuple result class."""
        if not func_def.result_class_name:
            return []

        lines = [f"class {func_def.result_class_name}(NamedTuple):"]
        for result_field in func_def.result_fields:
            lines.append(f"    {result_field.name}: {result_field.type_hint}")

        if not func_def.result_fields:
            lines.append("    pass")

        return lines

    def render_signature(self, func_def: FunctionDef) -> str:
        """Render the function signature."""
        params = []
        for p in func_def.params:
            if p.default is not None:
                params.append(f"{p.name}: {p.type_hint} = {p.default}")
            else:
                params.append(f"{p.name}: {p.type_hint}")

        params_str = ", ".join(params)
        return f"def {func_def.name}({params_str}) -> {func_def.return_type}:"

    def render_return(self, func_def: FunctionDef) -> str:
        """Render the return statement."""
        if not func_def.result_class_name:
            return "return None"

        parts = []
        for result_field in func_def.result_fields:
            if result_field.source_var:
                parts.append(f"{result_field.name}={result_field.source_var}")
            else:
                # No traced source - use field name as fallback
                # This indicates a bug in tracing, but keeps code valid
                parts.append(f"{result_field.name}={result_field.name}")

        args = ", ".join(parts)
        return f"return {func_def.result_class_name}({args})"
