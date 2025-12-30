"""Deterministic skeleton generator from VI graph.

Generates Python AST from Neo4j graph data, with placeholders for
unknown primitives that the LLM fills in.

The key insight: we have enough information to generate ~80% of the code
deterministically. The LLM's job shrinks to filling gaps.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import VISignature


# Primitive ID -> Python operation mapping
# Built from observation - extend as we encounter more
PRIMITIVE_OPS: dict[int, str] = {
    # Path operations
    1419: "Path({0}) / {1}",  # Build Path
    1420: "{0}.parent, {0}.name",  # Strip Path

    # String operations
    1446: "{0} + {1}",  # Concatenate Strings
    1447: "len({0})",  # String Length
    1448: "{0}[{1}:{2}]",  # String Subset

    # Numeric operations
    1284: "{0} + {1}",  # Add
    1285: "{0} - {1}",  # Subtract
    1286: "{0} * {1}",  # Multiply
    1287: "{0} / {1}",  # Divide

    # Comparison
    1297: "{0} == {1}",  # Equal
    1298: "{0} != {1}",  # Not Equal
    1299: "{0} > {1}",  # Greater Than
    1300: "{0} < {1}",  # Less Than

    # Boolean
    1301: "{0} and {1}",  # And
    1302: "{0} or {1}",  # Or
    1303: "not {0}",  # Not

    # Array operations
    1330: "{0}[{1}]",  # Index Array
    1331: "len({0})",  # Array Size
    1332: "{0} + [{1}]",  # Build Array (append)
}


@dataclass
class SkeletonVar:
    """A variable in the skeleton."""
    name: str
    type_hint: str
    source: str  # "input", "constant", "operation", "subvi"


@dataclass
class SkeletonOp:
    """An operation in the skeleton."""
    op_id: str
    op_type: str  # "primitive", "subvi", "loop", "conditional"
    prim_id: int | None
    name: str | None
    inputs: list[str]  # variable names
    outputs: list[str]  # variable names
    python_expr: str | None  # deterministic expression or None for ???


@dataclass
class Skeleton:
    """Complete skeleton for a VI."""
    function_name: str
    inputs: list[tuple[str, str]]  # (name, type)
    outputs: list[tuple[str, str]]  # (name, type)
    namedtuple_name: str
    constants: list[tuple[str, str, str]]  # (var_name, value, type)
    operations: list[SkeletonOp]
    unknowns: list[int]  # primitive IDs we don't know


class SkeletonGenerator:
    """Generate deterministic Python skeleton from VI graph context."""

    def __init__(
        self,
        converted_deps: dict[str, VISignature] | None = None,
    ):
        self.converted_deps = converted_deps or {}
        self._var_counter = 0
        self._vars: dict[str, SkeletonVar] = {}

    def generate(self, vi_context: dict, vi_name: str) -> Skeleton:
        """Generate skeleton from VI context.

        Args:
            vi_context: Context from graph.get_vi_context()
            vi_name: Name of the VI

        Returns:
            Skeleton with deterministic code and ??? placeholders
        """
        self._var_counter = 0
        self._vars = {}  # Maps node/terminal ID -> SkeletonVar
        self._terminal_to_var = {}  # Maps terminal ID -> variable name

        # Build terminal-to-source mapping from data flow
        self._build_data_flow_map(vi_context)

        # Extract function name
        function_name = self._to_function_name(vi_name)
        namedtuple_name = self._to_class_name(vi_name) + "Result"

        # Process inputs - these become function parameters
        inputs = []
        for inp in vi_context.get("inputs", []):
            name = self._to_var_name(inp.get("name", "input"))
            type_hint = self._map_type(inp.get("type", "Any"))
            inputs.append((name, type_hint))
            # Register input node and its terminals
            self._vars[inp.get("id", "")] = SkeletonVar(name, type_hint, "input")
            # Find terminals for this input
            for term in vi_context.get("terminals", []):
                if term.get("parent_id") == inp.get("id"):
                    self._terminal_to_var[term.get("id", "")] = name

        # Process outputs
        outputs = []
        for out in vi_context.get("outputs", []):
            name = self._to_var_name(out.get("name", "output"))
            type_hint = self._map_type(out.get("type", "Any"))
            outputs.append((name, type_hint))

        # Process constants - these become local variables
        constants = []
        for const in vi_context.get("constants", []):
            var_name = f"c_{len(constants)}"
            raw_value = const.get("python") or const.get("value", "None")
            type_hint = self._map_type(const.get("type", ""))

            # Format value properly for Python
            if const.get("python"):
                # Already has Python hint - use as-is but may need wrapping
                value = raw_value
            elif type_hint == "str" or const.get("type") == "String":
                # Quote strings
                value = repr(raw_value)
            elif type_hint == "Path" or "path" in raw_value.lower():
                # Path constants
                value = f"Path({repr(raw_value)})"
            else:
                value = raw_value

            constants.append((var_name, value, type_hint))
            self._vars[const.get("id", "")] = SkeletonVar(var_name, type_hint, "constant")
            # Register constant terminals
            for term in vi_context.get("terminals", []):
                if term.get("parent_id") == const.get("id"):
                    self._terminal_to_var[term.get("id", "")] = var_name

        # Build execution order from data flow
        # First pass: register all operation output terminals with short var names
        op_counter = 0
        for op in vi_context.get("operations", []):
            op_id = op.get("id", "")
            labels = op.get("labels", [])

            # Create a short prefix based on operation type
            if "SubVI" in labels:
                prefix = self._to_var_name(op.get("name", "subvi"))[:8]
            elif "Primitive" in labels:
                prim_id = op.get("primResID", 0)
                prefix = f"p{prim_id}"
            else:
                prefix = f"op{op_counter}"

            for term in op.get("terminals", []):
                if term.get("direction") == "output" and term.get("type") != "Void":
                    term_id = term.get("id", "")
                    out_var = f"{prefix}_{term.get('index', 0)}"
                    self._terminal_to_var[term_id] = out_var

            op_counter += 1

        operations = []
        unknowns = []

        # Topologically sort operations based on data dependencies
        sorted_ops = self._topological_sort_ops(vi_context.get("operations", []), vi_context)

        for op in sorted_ops:
            labels = op.get("labels", [])
            op_id = op.get("id", "")

            # Get input/output variable names from terminals
            op_inputs = []
            op_outputs = []

            # Sort terminals by index for consistent ordering
            terminals = sorted(op.get("terminals", []), key=lambda t: t.get("index", 0))

            for term in terminals:
                term_id = term.get("id", "")
                if term.get("direction") == "input" and term.get("type") != "Void":
                    # Find source variable from data flow
                    source_var = self._find_source_var(term_id, vi_context)
                    op_inputs.append(source_var or "???")
                elif term.get("direction") == "output" and term.get("type") != "Void":
                    out_var = self._terminal_to_var.get(term_id, f"v_{op_id}_{term.get('index', 0)}")
                    op_outputs.append(out_var)

            if "SubVI" in labels:
                # SubVI call - deterministic if converted
                subvi_name = op.get("name", "")
                if subvi_name in self.converted_deps:
                    sig = self.converted_deps[subvi_name]
                    python_expr = f"{sig.function_name}({', '.join(op_inputs)})"
                else:
                    func_name = self._to_function_name(subvi_name)
                    python_expr = f"{func_name}({', '.join(op_inputs)})  # ??? not converted"

                operations.append(SkeletonOp(
                    op_id=op_id,
                    op_type="subvi",
                    prim_id=None,
                    name=subvi_name,
                    inputs=op_inputs,
                    outputs=op_outputs,
                    python_expr=python_expr,
                ))

            elif "Primitive" in labels:
                prim_id = op.get("primResID")

                if prim_id in PRIMITIVE_OPS:
                    # Known primitive - generate deterministic code
                    template = PRIMITIVE_OPS[prim_id]
                    try:
                        python_expr = template.format(*op_inputs)
                    except (IndexError, KeyError):
                        python_expr = f"PRIMITIVE_{prim_id}({', '.join(op_inputs)})  # ???"
                        unknowns.append(prim_id)
                else:
                    # Unknown primitive - placeholder
                    python_expr = f"PRIMITIVE_{prim_id}({', '.join(op_inputs)})  # ???"
                    if prim_id:
                        unknowns.append(prim_id)

                operations.append(SkeletonOp(
                    op_id=op_id,
                    op_type="primitive",
                    prim_id=prim_id,
                    name=op.get("name"),
                    inputs=op_inputs,
                    outputs=op_outputs,
                    python_expr=python_expr,
                ))

            elif "Loop" in labels:
                loop_type = op.get("type", "for")
                operations.append(SkeletonOp(
                    op_id=op_id,
                    op_type="loop",
                    prim_id=None,
                    name=loop_type,
                    inputs=op_inputs,
                    outputs=op_outputs,
                    python_expr=f"# ??? {loop_type} loop",
                ))

            elif "Conditional" in labels:
                cond_type = op.get("type", "case")
                operations.append(SkeletonOp(
                    op_id=op_id,
                    op_type="conditional",
                    prim_id=None,
                    name=cond_type,
                    inputs=op_inputs,
                    outputs=op_outputs,
                    python_expr=f"# ??? {cond_type} structure",
                ))

        return Skeleton(
            function_name=function_name,
            inputs=inputs,
            outputs=outputs,
            namedtuple_name=namedtuple_name,
            constants=constants,
            operations=operations,
            unknowns=list(set(unknowns)),
        )

    def to_python(self, skeleton: Skeleton) -> str:
        """Convert skeleton to Python source code.

        Args:
            skeleton: Generated skeleton

        Returns:
            Python source code with ??? placeholders
        """
        lines = [
            '"""Generated skeleton - LLM fills ??? placeholders."""',
            "",
            "from __future__ import annotations",
            "",
            "from pathlib import Path",
            "from typing import Any, NamedTuple",
            "",
        ]

        # Add SubVI imports
        for sig in self.converted_deps.values():
            lines.append(sig.import_statement)
        if self.converted_deps:
            lines.append("")

        # NamedTuple for outputs
        if skeleton.outputs:
            lines.append(f"class {skeleton.namedtuple_name}(NamedTuple):")
            for name, type_hint in skeleton.outputs:
                lines.append(f"    {name}: {type_hint}")
            lines.append("")

        # Function signature
        params = ", ".join(f"{n}: {t}" for n, t in skeleton.inputs)
        return_type = skeleton.namedtuple_name if skeleton.outputs else "None"
        lines.append(f"def {skeleton.function_name}({params}) -> {return_type}:")

        # Docstring placeholder
        lines.append(f'    """??? Add docstring."""')

        # Constants
        if skeleton.constants:
            lines.append("    # Constants")
            for var_name, value, _ in skeleton.constants:
                lines.append(f"    {var_name} = {value}")
            lines.append("")

        # Operations
        lines.append("    # Operations (in data flow order)")
        for op in skeleton.operations:
            if op.outputs:
                if len(op.outputs) == 1:
                    lines.append(f"    {op.outputs[0]} = {op.python_expr}")
                else:
                    outputs_str = ", ".join(op.outputs)
                    lines.append(f"    {outputs_str} = {op.python_expr}")
            else:
                lines.append(f"    {op.python_expr}")

        # Return statement
        lines.append("")
        if skeleton.outputs:
            output_vars = ", ".join(f"{n}=???" for n, _ in skeleton.outputs)
            lines.append(f"    return {skeleton.namedtuple_name}({output_vars})")
        else:
            lines.append("    return None")

        # Add unknowns summary at end
        if skeleton.unknowns:
            lines.append("")
            lines.append("# ??? UNKNOWN PRIMITIVES - LLM must implement:")
            for prim_id in skeleton.unknowns:
                lines.append(f"#   - PRIMITIVE_{prim_id}: ???")

        return "\n".join(lines)

    def _topological_sort_ops(self, operations: list[dict], vi_context: dict) -> list[dict]:
        """Sort operations in execution order based on data dependencies.

        An operation can execute when all its input wires have data.
        This means: operation B depends on A if any of B's inputs come from A's outputs.
        """
        # Build dependency graph: op_id -> set of op_ids it depends on
        op_by_id = {op.get("id"): op for op in operations}
        dependencies: dict[str, set[str]] = {op.get("id"): set() for op in operations}

        # Map output terminal IDs to their parent operation IDs
        output_to_op: dict[str, str] = {}
        for op in operations:
            op_id = op.get("id")
            for term in op.get("terminals", []):
                if term.get("direction") == "output":
                    output_to_op[term.get("id", "")] = op_id

        # For each operation, find which operations provide its inputs
        for op in operations:
            op_id = op.get("id")
            for term in op.get("terminals", []):
                if term.get("direction") == "input":
                    term_id = term.get("id", "")
                    # Find source of this input from data flow
                    if term_id in self._flow_map:
                        src_term = self._flow_map[term_id]["src_terminal"]
                        if src_term in output_to_op:
                            dep_op_id = output_to_op[src_term]
                            if dep_op_id != op_id:  # Don't depend on self
                                dependencies[op_id].add(dep_op_id)

        # Kahn's algorithm for topological sort
        result = []
        in_degree = {op_id: len(deps) for op_id, deps in dependencies.items()}
        queue = [op_id for op_id, deg in in_degree.items() if deg == 0]

        while queue:
            # Pick operation with no remaining dependencies
            op_id = queue.pop(0)
            if op_id in op_by_id:
                result.append(op_by_id[op_id])

            # Reduce in-degree for operations that depend on this one
            for other_id, deps in dependencies.items():
                if op_id in deps:
                    in_degree[other_id] -= 1
                    if in_degree[other_id] == 0 and other_id not in [r.get("id") for r in result]:
                        queue.append(other_id)

        # Add any remaining (might have cycles or disconnected)
        for op in operations:
            if op not in result:
                result.append(op)

        return result

    def _build_data_flow_map(self, vi_context: dict) -> None:
        """Build mapping from destination terminals to source terminals."""
        self._flow_map = {}  # dest_terminal_id -> source_terminal_id
        for flow in vi_context.get("data_flow", []):
            dest_id = flow.get("to_terminal_id")
            src_id = flow.get("from_terminal_id")
            if dest_id and src_id:
                self._flow_map[dest_id] = {
                    "src_terminal": src_id,
                    "src_parent_id": flow.get("from_parent_id"),
                    "src_parent_name": flow.get("from_parent_name"),
                    "src_parent_labels": flow.get("from_parent_labels", []),
                }

    def _find_source_var(self, term_id: str, vi_context: dict) -> str | None:
        """Find the source variable for a terminal from data flow."""
        if term_id not in self._flow_map:
            return None

        flow = self._flow_map[term_id]
        src_term = flow["src_terminal"]

        # Check if we already have a var for this terminal
        if src_term in self._terminal_to_var:
            return self._terminal_to_var[src_term]

        # Check if source is a constant or input node
        src_parent_id = flow["src_parent_id"]
        if src_parent_id in self._vars:
            return self._vars[src_parent_id].name

        # For constants/inputs, also check by parent labels
        labels = flow.get("src_parent_labels", [])
        if "Constant" in labels or "Control" in labels or "Input" in labels:
            src_name = flow.get("src_parent_name", "")
            if src_name:
                return self._to_var_name(src_name)

        return None

    def _new_var(self, prefix: str) -> str:
        """Generate a new variable name."""
        self._var_counter += 1
        return f"{prefix}_{self._var_counter}"

    def _to_function_name(self, name: str) -> str:
        """Convert VI name to Python function name."""
        name = name.replace(".vi", "").replace(".VI", "")
        if ":" in name:
            name = name.split(":")[-1]
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "vi_" + result
        return result or "vi_function"

    def _to_class_name(self, name: str) -> str:
        """Convert VI name to PascalCase class name."""
        name = name.replace(".vi", "").replace(".VI", "")
        if ":" in name:
            name = name.split(":")[-1]
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(w.capitalize() for w in words) or "VI"

    def _to_var_name(self, name: str) -> str:
        """Convert terminal name to Python variable name."""
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "var_" + result
        return result or "value"

    def _map_type(self, lv_type: str) -> str:
        """Map LabVIEW type to Python type."""
        type_map = {
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
        }
        return type_map.get(lv_type, "Any")


def generate_skeleton(
    vi_context: dict,
    vi_name: str,
    converted_deps: dict[str, VISignature] | None = None,
) -> str:
    """Generate Python skeleton from VI context.

    Args:
        vi_context: Context from graph.get_vi_context()
        vi_name: Name of the VI
        converted_deps: Already-converted SubVI signatures

    Returns:
        Python source code with ??? placeholders for LLM
    """
    gen = SkeletonGenerator(converted_deps)
    skeleton = gen.generate(vi_context, vi_name)
    return gen.to_python(skeleton)
