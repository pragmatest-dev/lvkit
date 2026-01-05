"""Deterministic skeleton generator from VI graph.

Generates Python code from VI graph data using direct data flow connections.
The graph tells us exactly how values flow between nodes - we use this to
generate correct Python with proper types, enum members, and NamedTuple access.

The LLM only fills in truly unknown parts (unmapped primitives).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..graph_types import Operation, Wire
from ..primitive_resolver import PrimitiveTerminal
from ..primitive_resolver import get_resolver as get_primitive_resolver
from ..vilib_resolver import VILibResolver

if TYPE_CHECKING:
    from .context import VISignature


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
    pre_statements: list[str] = field(default_factory=list)  # statements to emit before assignment


@dataclass
class SkeletonInput:
    """An input parameter for the skeleton."""
    name: str
    type_hint: str
    wiring_rule: int = 0  # 0=Invalid, 1=Required, 2=Recommended, 3=Optional
    default_value: str | None = None  # Python literal for default


@dataclass
class SkeletonDep:
    """A dependency for import generation."""
    vi_name: str  # Original VI name
    func_name: str  # Python function name
    is_vilib: bool  # From vi.lib
    is_converted: bool  # Already converted
    result_class: str | None = None  # Result NamedTuple name
    enums: list[str] = field(default_factory=list)  # Enum types to import


@dataclass
class Skeleton:
    """Complete skeleton for a VI."""
    function_name: str
    inputs: list[SkeletonInput]
    outputs: list[tuple[str, str]]  # (name, type)
    namedtuple_name: str
    constants: list[tuple[str, str, str]]  # (var_name, value, type)
    operations: list[SkeletonOp]
    unknowns: list[int]  # primitive IDs we don't know
    output_sources: dict[str, str] = field(default_factory=dict)  # output_name -> source_var
    dependencies: list[SkeletonDep] = field(default_factory=list)  # SubVI dependencies


class SkeletonGenerator:
    """Generate deterministic Python skeleton from VI graph context."""

    def __init__(
        self,
        converted_deps: dict[str, VISignature] | None = None,
        vilib_resolver: VILibResolver | None = None,
    ):
        self.converted_deps = converted_deps or {}
        self.vilib_resolver = vilib_resolver or VILibResolver()
        self._var_counter = 0
        self._vars: dict[str, SkeletonVar] = {}
        self._enum_imports: set[str] = set()  # Track enum types to import

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

        # Build dependencies from subvi_calls
        dependencies: list[SkeletonDep] = []
        seen_deps: set[str] = set()
        for call in vi_context.get("subvi_calls", []):
            subvi_name = call.get("vi_name", "")
            if subvi_name in seen_deps:
                continue
            seen_deps.add(subvi_name)

            func_name = self._to_function_name(subvi_name)
            result_class = self._to_class_name(subvi_name) + "Result"
            vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)

            # Collect enums used by this SubVI
            enums = []
            if vilib_vi:
                for term in vilib_vi.terminals:
                    if term.enum and term.enum not in enums:
                        enums.append(term.enum)

            dependencies.append(SkeletonDep(
                vi_name=subvi_name,
                func_name=func_name,
                is_vilib=vilib_vi is not None and vilib_vi.python_code is not None,
                is_converted=subvi_name in self.converted_deps,
                result_class=result_class,
                enums=enums,
            ))

        # Process inputs - these become function parameters
        inputs: list[SkeletonInput] = []
        for inp in vi_context.get("inputs", []):
            name = self._to_var_name(inp.name or "input")
            type_hint = self._map_type(inp.type or "Any")
            wiring_rule = inp.wiring_rule
            default_value = inp.default_value

            inputs.append(SkeletonInput(
                name=name,
                type_hint=type_hint,
                wiring_rule=wiring_rule,
                default_value=default_value,
            ))
            # Register input node and its terminals
            self._vars[inp.id] = SkeletonVar(name, type_hint, "input")
            # Find terminals for this input (terminals is a list of dicts)
            for term in vi_context.get("terminals", []):
                if term.get("parent_id") == inp.id:
                    self._terminal_to_var[term.get("id", "")] = name

        # Process outputs
        outputs = []
        for out in vi_context.get("outputs", []):
            name = self._to_var_name(out.name or "output")
            # Use LVType if available, otherwise fall back to manual mapping
            if out.lv_type:
                type_hint = out.lv_type.to_python()
            else:
                lv_type = out.type or self._map_control_type(out.control_type)
                type_hint = self._map_type(lv_type) if lv_type else "Any"
            outputs.append((name, type_hint))

        # Build operation lookup for enum resolution
        ops_by_id = {op.id: op for op in vi_context.get("operations", [])}

        # Process constants - resolve enums via data flow
        constants = []
        self._enum_imports = set()
        used_const_names: set[str] = set()  # Track used names to avoid collisions

        for const in vi_context.get("constants", []):
            const_id = const.id
            raw_value = str(const.value) if const.value is not None else ""
            # Extract underlying type from LVType if available
            lv_type_str = ""
            if const.lv_type:
                lv_type_str = const.lv_type.underlying_type or ""
            type_hint = self._map_type(lv_type_str)
            const_label = const.name

            # Try to resolve enum via data flow connection
            enum_value = self._resolve_constant_enum(
                const_id, raw_value, vi_context.get("data_flow", []), ops_by_id
            )

            if enum_value:
                # Enum resolved - use it directly
                value = enum_value
                var_name = self._to_var_name(enum_value.split(".")[-1].lower())
            else:
                # Format value based on type
                value = self._format_constant(raw_value, "", type_hint, lv_type_str)

                # Derive name from: label > destination terminal > type-based fallback
                var_name = self._derive_constant_name(
                    const_id, const_label, type_hint, vi_context, ops_by_id
                )

            # Ensure unique name
            base_name = var_name
            counter = 1
            while var_name in used_const_names:
                var_name = f"{base_name}_{counter}"
                counter += 1
            used_const_names.add(var_name)

            constants.append((var_name, value, type_hint))
            self._vars[const_id] = SkeletonVar(var_name, type_hint, "constant")
            # Register constant terminals (terminals is a list of dicts)
            for term in vi_context.get("terminals", []):
                if term.get("parent_id") == const_id:
                    self._terminal_to_var[term.get("id", "")] = var_name

        # Build execution order from data flow
        # First pass: register all operation output terminals with semantic var names
        op_counter = 0
        for op in vi_context.get("operations", []):
            op_id = op.id
            labels = op.labels
            prim_id = op.primResID

            # Get output terminals sorted by index (only wired ones)
            output_terms = sorted(
                [t for t in op.terminals
                 if t.direction == "output"
                 and self._is_terminal_wired(t.id)],
                key=lambda t: t.index
            )

            # For SubVIs, look up vilib terminal names for output field access
            if "SubVI" in labels:
                subvi_name = op.name or ""
                vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
                result_var = self._to_var_name(subvi_name.replace(".vi", "")) + "_result"

                for term in output_terms:
                    term_id = term.id
                    term_index = term.index

                    # Look up field name from vilib
                    field_name = None
                    if vilib_vi:
                        for vt in vilib_vi.terminals:
                            if vt.index == term_index and vt.direction == "out":
                                field_name = self._to_var_name(vt.name)
                                break

                    if field_name:
                        # SubVI output accessed as result.field
                        self._terminal_to_var[term_id] = f"{result_var}.{field_name}"
                    else:
                        # Fallback for non-vilib SubVIs
                        self._terminal_to_var[term_id] = f"{result_var}.output_{term_index}"

            elif "Primitive" in labels:
                # Get output terminal names from primitive resolver (PrimitiveTerminal objects)
                prim_resolver = get_primitive_resolver()
                resolved = prim_resolver.resolve(prim_id=prim_id)
                terminal_names: dict[int, str] = {}
                if resolved:
                    for t in resolved.terminals:
                        if t.direction == "out":
                            terminal_names[t.index] = self._to_var_name(t.name or "")

                for term in output_terms:
                    term_id = term.id
                    term_index = term.index
                    graph_name = term.name
                    if graph_name:
                        out_var = self._to_var_name(graph_name)
                    elif term_index in terminal_names:
                        out_var = terminal_names[term_index]
                    else:
                        out_var = f"p{prim_id}_{term_index}"
                    self._terminal_to_var[term_id] = out_var

            else:
                # Generic operation
                for i, term in enumerate(output_terms):
                    term_id = term.id
                    graph_name = term.name
                    if graph_name:
                        out_var = self._to_var_name(graph_name)
                    else:
                        out_var = f"op{op_counter}_{i}"
                    self._terminal_to_var[term_id] = out_var

            op_counter += 1

        operations = []
        unknowns = []

        # Topologically sort operations based on data dependencies
        sorted_ops = self._topological_sort_ops(vi_context.get("operations", []), vi_context)

        for op in sorted_ops:
            labels = op.labels
            op_id = op.id

            # Get input/output variable names from terminals
            op_inputs = []
            op_outputs = []

            # Sort terminals by index for consistent ordering
            terminals = sorted(op.terminals, key=lambda t: t.index)

            for term in terminals:
                term_id = term.id
                # Only include wired terminals
                if not self._is_terminal_wired(term_id):
                    continue
                if term.direction == "input":
                    # Find source variable from data flow
                    source_var = self._find_source_var(term_id, vi_context)
                    op_inputs.append(source_var or "???")
                elif term.direction == "output":
                    out_var = self._terminal_to_var.get(term_id, f"v_{op_id}_{term.index}")
                    op_outputs.append(out_var)

            if "SubVI" in labels:
                # SubVI call returns a NamedTuple result
                subvi_name = op.name or ""
                result_var = self._to_var_name(subvi_name.replace(".vi", "")) + "_result"

                # Check if we have vilib implementation
                vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
                func_name = self._to_function_name(subvi_name)

                if subvi_name in self.converted_deps:
                    sig = self.converted_deps[subvi_name]
                    python_expr = f"{sig.function_name}({', '.join(op_inputs)})"
                elif vilib_vi and vilib_vi.python_code:
                    # vilib VI with implementation
                    python_expr = f"{func_name}({', '.join(op_inputs)})"
                else:
                    python_expr = f"{func_name}({', '.join(op_inputs)})  # ??? not converted"

                # SubVI has single result variable (fields accessed via _terminal_to_var)
                operations.append(SkeletonOp(
                    op_id=op_id,
                    op_type="subvi",
                    prim_id=None,
                    name=subvi_name,
                    inputs=op_inputs,
                    outputs=[result_var],  # Single result var, fields tracked separately
                    python_expr=python_expr,
                ))

            elif "Primitive" in labels:
                prim_id = op.primResID
                prim_resolver = get_primitive_resolver()
                resolved = prim_resolver.resolve(prim_id=prim_id)

                if resolved and resolved.python_code:
                    # Build input map: terminal name -> value from data flow
                    input_map = self._build_primitive_input_map(
                        op, op_inputs, resolved.terminals, vi_context
                    )

                    pre_statements: list[str] = []
                    if isinstance(resolved.python_code, dict):
                        # Dict format: {output_name: expr, ...}
                        python_expr, generated_outputs, pre_statements = self._handle_dict_primitive_hint(
                            resolved.python_code, input_map, op, resolved.terminals
                        )
                        # Override op_outputs with generated ones
                        if generated_outputs:
                            op_outputs = generated_outputs
                    else:
                        # String format: single expression
                        python_expr = self._substitute_primitive_hint(
                            resolved.python_code, input_map
                        )

                    if "???" in python_expr or (
                        isinstance(resolved.python_code, str) and
                        python_expr == resolved.python_code
                    ):
                        # Couldn't substitute - mark as unknown
                        if prim_id:
                            unknowns.append(prim_id)
                else:
                    # Unknown primitive - placeholder
                    python_expr = f"PRIMITIVE_{prim_id}({', '.join(op_inputs)})  # ???"
                    pre_statements = []
                    if prim_id:
                        unknowns.append(prim_id)

                operations.append(SkeletonOp(
                    op_id=op_id,
                    op_type="primitive",
                    prim_id=prim_id,
                    name=resolved.name if resolved else op.name,
                    inputs=op_inputs,
                    outputs=op_outputs,
                    python_expr=python_expr,
                    pre_statements=pre_statements,
                ))

            elif "Loop" in labels:
                loop_type = op.loop_type or "for"
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
                cond_type = op.node_type or "case"
                operations.append(SkeletonOp(
                    op_id=op_id,
                    op_type="conditional",
                    prim_id=None,
                    name=cond_type,
                    inputs=op_inputs,
                    outputs=op_outputs,
                    python_expr=f"# ??? {cond_type} structure",
                ))

        # Trace which operation outputs connect to VI outputs
        output_sources: dict[str, str] = {}
        for out in vi_context.get("outputs", []):
            out_name = self._to_var_name(out.name or "output")
            out_id = out.id

            # First try: output ID itself is the terminal (FP terminal)
            if out_id in self._flow_map:
                src_term = self._flow_map[out_id]["src_terminal"]
                if src_term in self._terminal_to_var:
                    output_sources[out_name] = self._terminal_to_var[src_term]
                    continue

            # Second try: find child terminal with parent_id == out_id
            for term in vi_context.get("terminals", []):
                if term.get("parent_id") == out_id:
                    term_id = term.get("id", "")
                    if term_id in self._flow_map:
                        src_term = self._flow_map[term_id]["src_terminal"]
                        if src_term in self._terminal_to_var:
                            output_sources[out_name] = self._terminal_to_var[src_term]
                    break

        return Skeleton(
            function_name=function_name,
            inputs=inputs,
            outputs=outputs,
            namedtuple_name=namedtuple_name,
            constants=constants,
            operations=operations,
            unknowns=list(set(unknowns)),
            output_sources=output_sources,
            dependencies=dependencies,
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

        # Generate imports from dependencies
        if skeleton.dependencies:
            for dep in skeleton.dependencies:
                # Build import names: function, result class, enums
                names = [dep.func_name, dep.result_class]
                names.extend(dep.enums)
                names_str = ", ".join(n for n in names if n)
                lines.append(f"from .{dep.func_name} import {names_str}")
            lines.append("")

        # NamedTuple for outputs
        if skeleton.outputs:
            lines.append(f"class {skeleton.namedtuple_name}(NamedTuple):")
            for name, type_hint in skeleton.outputs:
                lines.append(f"    {name}: {type_hint}")
            lines.append("")

        # Function signature - required inputs first, then optional with defaults
        required = [inp for inp in skeleton.inputs if inp.wiring_rule == 1]
        optional = [inp for inp in skeleton.inputs if inp.wiring_rule != 1]

        params = []
        for inp in required:
            params.append(f"{inp.name}: {inp.type_hint}")
        for inp in optional:
            default = inp.default_value or self._default_for_type(inp.type_hint)
            params.append(f"{inp.name}: {inp.type_hint} = {default}")

        params_str = ", ".join(params)
        return_type = skeleton.namedtuple_name if skeleton.outputs else "None"
        lines.append(f"def {skeleton.function_name}({params_str}) -> {return_type}:")

        # Docstring placeholder
        lines.append('    """??? Add docstring."""')

        # Constants
        if skeleton.constants:
            lines.append("    # Constants")
            for var_name, value, _ in skeleton.constants:
                lines.append(f"    {var_name} = {value}")
            lines.append("")

        # Operations
        lines.append("    # Operations (in data flow order)")
        for op in skeleton.operations:
            # Emit pre-statements first (e.g., _body from dict hints)
            for stmt in op.pre_statements:
                lines.append(f"    {stmt}")
            # Then emit the assignment
            if op.outputs:
                if len(op.outputs) == 1:
                    lines.append(f"    {op.outputs[0]} = {op.python_expr}")
                else:
                    outputs_str = ", ".join(op.outputs)
                    lines.append(f"    {outputs_str} = {op.python_expr}")
            else:
                lines.append(f"    {op.python_expr}")

        # Return statement - use traced output sources
        lines.append("")
        if skeleton.outputs:
            output_parts = []
            for name, _ in skeleton.outputs:
                source_var = skeleton.output_sources.get(name, "???")
                output_parts.append(f"{name}={source_var}")
            output_vars = ", ".join(output_parts)
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

    def _topological_sort_ops(self, operations: list, vi_context: dict) -> list:
        """Sort operations in execution order based on data dependencies.

        An operation can execute when all its input wires have data.
        This means: operation B depends on A if any of B's inputs come from A's outputs.
        """
        # Build dependency graph: op_id -> set of op_ids it depends on
        op_by_id = {op.id: op for op in operations}
        dependencies: dict[str, set[str]] = {op.id: set() for op in operations}

        # Map output terminal IDs to their parent operation IDs
        output_to_op: dict[str, str] = {}
        for op in operations:
            op_id = op.id
            for term in op.terminals:
                if term.direction == "output":
                    output_to_op[term.id] = op_id

        # For each operation, find which operations provide its inputs
        for op in operations:
            op_id = op.id
            for term in op.terminals:
                if term.direction == "input":
                    term_id = term.id
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
                    if in_degree[other_id] == 0 and other_id not in [r.id for r in result]:
                        queue.append(other_id)

        # Add any remaining (might have cycles or disconnected)
        for op in operations:
            if op not in result:
                result.append(op)

        return result

    def _build_primitive_input_map(
        self,
        op: Operation,
        op_inputs: list[str],
        prim_terminals: list[PrimitiveTerminal],
        vi_context: dict,
    ) -> dict[str, str]:
        """Build map from primitive terminal names to actual input values.

        Uses data flow to match wired terminals to their values.
        Matches by POSITION among wired inputs, not by index value,
        since primitive terminal indices in graph may differ from definition.

        Args:
            op: The Operation dataclass from vi_context
            op_inputs: Input values in wired order
            prim_terminals: Terminal definitions from primitive resolver
            vi_context: Full VI context

        Returns:
            Dict mapping terminal name -> value (e.g., {"path": "directory_path"})
        """
        result: dict[str, str] = {}

        # Get input terminals from primitive definition, sorted by index
        input_terminals = [t for t in prim_terminals if t.direction == "in"]
        input_terminals.sort(key=lambda t: t.index)

        # Match by position: 1st wired input -> 1st defined input terminal
        for i, value in enumerate(op_inputs):
            if i < len(input_terminals):
                name = input_terminals[i].name or ""
                # Normalize name for substitution
                key = self._to_var_name(name)
                result[key] = value
                # Also store with spaces/original form
                result[name.lower().replace(" ", "_")] = value

        return result

    def _substitute_primitive_hint(
        self,
        python_hint: str,
        input_map: dict[str, str],
    ) -> str:
        """Substitute terminal names in python hint with actual values.

        Args:
            python_hint: Template like "appended_path = base_path / name"
            input_map: Terminal name -> value mapping

        Returns:
            Substituted expression like "dir_result.path / suffix"
        """
        result = python_hint

        # Strip assignment if present (we generate our own output var)
        if "=" in result and not any(op in result for op in ["==", "!=", "<=", ">="]):
            # Find first = that's not part of comparison
            eq_pos = result.find("=")
            if eq_pos > 0 and result[eq_pos-1] not in "!<>" and result[eq_pos+1] != "=":
                result = result[eq_pos + 1:].strip()

        # Strip trailing comment
        if "#" in result:
            result = result[:result.find("#")].strip()

        # Sort by length (longest first) to avoid partial replacements
        for name, value in sorted(input_map.items(), key=lambda x: -len(x[0])):
            if not name:
                continue
            # Replace word-bounded occurrences (case-sensitive to avoid replacing
            # Python builtins like 'Path' when substituting terminal name 'path')
            pattern = r'\b' + re.escape(name) + r'\b'
            result = re.sub(pattern, value, result)

        return result

    def _handle_dict_primitive_hint(
        self,
        hint_dict: dict[str, str],
        input_map: dict[str, str],
        op: Operation,
        prim_terminals: list[PrimitiveTerminal],
    ) -> tuple[str, list[str], list[str]]:
        """Handle dict-format primitive hints for multi-output primitives.

        Args:
            hint_dict: {output_name: expression, "_body": optional side effect}
            input_map: Input terminal name -> value mapping
            op: The operation dict
            prim_terminals: Terminal definitions from primitive

        Returns:
            (python_expression, [output_var_names], [pre_statements])
        """
        # Get wired output terminals from the operation
        wired_outputs = []
        for term in op.terminals:
            if term.direction == "output" and self._is_terminal_wired(term.id):
                term_index = term.index
                # Find matching primitive terminal by index (PrimitiveTerminal objects)
                for pt in prim_terminals:
                    if pt.index == term_index and pt.direction == "out":
                        output_name = self._to_var_name(pt.name or "")
                        wired_outputs.append((term_index, output_name, term.id))
                        break

        # Sort by index for consistent ordering
        wired_outputs.sort(key=lambda x: x[0])

        # Build expressions for wired outputs
        expressions = []
        output_vars = []

        for idx, output_name, term_id in wired_outputs:
            # Look up expression in hint dict
            expr = hint_dict.get(output_name)
            if not expr:
                # Try with underscores/spaces normalized
                # Also strip trailing underscores - hint keys use them to avoid
                # Python keywords (e.g., "is_" for terminal "is?")
                for key in hint_dict:
                    normalized_key = self._to_var_name(key).rstrip("_")
                    normalized_output = output_name.rstrip("_")
                    if normalized_key == normalized_output:
                        expr = hint_dict[key]
                        break

            if expr:
                # Substitute input variables (case-sensitive)
                substituted = expr
                for name, value in sorted(input_map.items(), key=lambda x: -len(x[0])):
                    if name:
                        pattern = r'\b' + re.escape(name) + r'\b'
                        substituted = re.sub(pattern, value, substituted)
                expressions.append(substituted)
                output_vars.append(output_name)
                # Register in terminal_to_var for downstream use
                self._terminal_to_var[term_id] = output_name
            else:
                expressions.append(f"???  # no hint for {output_name}")
                output_vars.append(output_name)

        # Handle _body (side effect) - emit as separate pre-statement
        pre_statements = []
        body = hint_dict.get("_body")
        if body:
            # Substitute inputs in body (case-sensitive)
            for name, value in sorted(input_map.items(), key=lambda x: -len(x[0])):
                if name:
                    pattern = r'\b' + re.escape(name) + r'\b'
                    body = re.sub(pattern, value, body)
            pre_statements.append(body)

        # Generate the expression (without body - that's in pre_statements)
        if len(expressions) == 0:
            return "pass  # no outputs wired", [], pre_statements
        elif len(expressions) == 1:
            return expressions[0], output_vars, pre_statements
        else:
            return ", ".join(expressions), output_vars, pre_statements

    def _build_data_flow_map(self, vi_context: dict) -> None:
        """Build mapping from destination terminals to source terminals."""
        self._flow_map = {}  # dest_terminal_id -> source info
        self._wired_terminals: set[str] = set()  # All terminals with wires
        for flow in vi_context.get("data_flow", []):
            dest_id = flow.to_terminal_id
            src_id = flow.from_terminal_id
            if dest_id and src_id:
                self._flow_map[dest_id] = {
                    "src_terminal": src_id,
                    "src_parent_id": flow.from_parent_id,
                    "src_parent_name": flow.from_parent_name,
                    "src_parent_labels": flow.from_parent_labels or [],
                }
                # Track both ends as wired
                self._wired_terminals.add(src_id)
                self._wired_terminals.add(dest_id)

    def _is_terminal_wired(self, term_id: str) -> bool:
        """Check if a terminal has any wire connected."""
        return term_id in self._wired_terminals

    def _find_source_var(
        self, term_id: str, vi_context: dict, visited: set[str] | None = None
    ) -> str | None:
        """Find the source variable for a terminal from data flow.

        Recursively traces through tunnel connections to find the original source.
        """
        # Prevent infinite loops
        if visited is None:
            visited = set()
        if term_id in visited:
            return None
        visited.add(term_id)

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

        # Recursively trace through tunnels and other connections
        # This handles cases like: input -> tunnel_outer -> tunnel_inner -> primitive
        result = self._find_source_var(src_term, vi_context, visited)
        if result:
            return result

        return None

    def _resolve_constant_enum(
        self,
        const_id: str,
        raw_value: str,
        data_flow: list[Wire],
        ops_by_id: dict[str, Operation],
    ) -> str | None:
        """Resolve a constant to an enum member via data flow.

        Traces where this constant flows to. If it connects to a SubVI terminal
        that has an enum type, resolves the numeric value to the enum member.

        Args:
            const_id: ID of the constant node
            raw_value: Raw value of the constant (e.g., "7")
            data_flow: List of Wire dataclass instances
            ops_by_id: Operations indexed by ID

        Returns:
            Enum expression like "SystemDirectoryType.PUBLIC_APP_DATA" or None
        """
        # Find where this constant flows to
        for flow in data_flow:
            if flow.from_parent_id != const_id:
                continue

            dest_op_id = flow.to_parent_id
            dest_term_id = flow.to_terminal_id
            dest_op = ops_by_id.get(dest_op_id)

            if not dest_op:
                continue

            # Only handle SubVI calls
            if "SubVI" not in dest_op.labels:
                continue

            subvi_name = dest_op.name or ""
            vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
            if not vilib_vi:
                continue

            # Find the destination terminal index
            dest_term_index = None
            for term in dest_op.terminals:
                if term.id == dest_term_id:
                    dest_term_index = term.index
                    break

            if dest_term_index is None:
                continue

            # Find vilib terminal with matching index
            for vilib_term in vilib_vi.terminals:
                if vilib_term.index == dest_term_index and vilib_term.enum:
                    # Found enum terminal - resolve value to member
                    enum_name = vilib_term.enum

                    # Find member with matching value
                    try:
                        int_value = int(raw_value)
                    except (ValueError, TypeError):
                        continue

                    # enum_values is list[tuple[int, str]] on VITerminal
                    if vilib_term.enum_values:
                        for val, member_name in vilib_term.enum_values:
                            if val == int_value:
                                self._enum_imports.add(enum_name)
                                return f"{enum_name}.{member_name}"

        return None

    def _derive_constant_name(
        self,
        const_id: str,
        label: str | None,
        type_hint: str,
        vi_context: dict,
        ops_by_id: dict[str, Operation],
    ) -> str:
        """Derive a meaningful name for a constant.

        Priority:
        1. Use label if present
        2. Use destination terminal name (from vilib or primitive resolver)
        3. Fall back to type-based naming

        Args:
            const_id: ID of the constant node
            label: Label from the constant (if any)
            type_hint: Python type hint
            vi_context: Full VI context
            ops_by_id: Operations indexed by ID (Operation dataclass instances)

        Returns:
            A meaningful variable name
        """
        # 1. Use label if present
        if label:
            return self._to_var_name(label)

        # 2. Find destination terminal name via data flow
        for flow in vi_context.get("data_flow", []):
            if flow.from_parent_id != const_id:
                continue

            dest_op_id = flow.to_parent_id
            dest_term_id = flow.to_terminal_id
            dest_op = ops_by_id.get(dest_op_id)

            if not dest_op:
                continue

            # Find destination terminal index
            dest_term_index = None
            for term in dest_op.terminals:
                if term.id == dest_term_id:
                    dest_term_index = term.index
                    break

            if dest_term_index is None:
                continue

            labels = dest_op.labels

            # Try vilib for SubVI terminal names
            if "SubVI" in labels:
                subvi_name = dest_op.name or ""
                vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
                if vilib_vi:
                    for vt in vilib_vi.terminals:
                        if vt.index == dest_term_index and vt.direction == "in":
                            return self._to_var_name(vt.name)

            # Try primitive resolver for primitive terminal names
            if "Primitive" in labels:
                prim_id = dest_op.primResID
                if prim_id:
                    prim_resolver = get_primitive_resolver()
                    resolved = prim_resolver.resolve(prim_id=prim_id)
                    if resolved:
                        for t in resolved.terminals:
                            if t.index == dest_term_index and t.direction == "in":
                                term_name = t.name
                                if term_name:
                                    return self._to_var_name(term_name)

        # 3. Fall back to type-based naming
        type_prefix = {
            "Path": "path",
            "str": "text",
            "int": "num",
            "float": "value",
            "bool": "flag",
            "list": "items",
            "dict": "data",
        }
        return type_prefix.get(type_hint, "const")

    def _format_constant(
        self,
        raw_value: str,
        python_hint: str,
        type_hint: str,
        lv_type: str,
    ) -> str:
        """Format a constant value as valid Python.

        Args:
            raw_value: The raw value from the graph
            python_hint: Optional Python hint (may be description, not code)
            type_hint: Mapped Python type hint
            lv_type: Original LabVIEW type

        Returns:
            Valid Python expression as string
        """
        # Check if python_hint is actual Python code (not a description)
        if python_hint:
            # Descriptions often contain "on Windows" or "on Unix"
            if " on Windows" in python_hint or " on Unix" in python_hint:
                # This is a description, not code - extract the concept
                # e.g., "os.environ['PROGRAMDATA'] on Windows, '/usr/local/share' on Unix"
                return f"None  # TODO: {python_hint}"
            # Check if it looks like valid Python (starts with quote, number, or identifier)
            stripped = python_hint.strip()
            if stripped and (
                stripped[0] in "\"'0123456789-" or
                stripped.startswith("Path(") or
                stripped.startswith("[") or
                stripped.startswith("{") or
                stripped.startswith("True") or
                stripped.startswith("False") or
                stripped.startswith("None")
            ):
                return python_hint

        # String constants - always quote
        if lv_type == "String" or type_hint == "str":
            return repr(raw_value)

        # Path constants
        if lv_type == "Path" or type_hint == "Path":
            return f"Path({repr(raw_value)})"

        # Numeric constants
        if type_hint in ("int", "float"):
            try:
                # Try to parse as number
                if "." in raw_value:
                    return str(float(raw_value))
                return str(int(raw_value))
            except (ValueError, TypeError):
                return repr(raw_value)

        # Boolean
        if type_hint == "bool" or lv_type == "Boolean":
            return "True" if raw_value.lower() in ("true", "1") else "False"

        # Default: quote it as string
        if raw_value:
            return repr(raw_value)
        return "None"

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

    def _to_var_name(self, name: str | None) -> str:
        """Convert terminal name to Python variable name."""
        if not name:
            return "value"
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "var_" + result
        return result or "value"

    def _map_control_type(self, control_type: str | None) -> str | None:
        """Map LabVIEW control type to a type string that _map_type understands.

        control_type values come from the front panel control definitions.
        """
        if not control_type:
            return None
        control_map = {
            "stdPath": "Path",
            "stdString": "String",
            "stdBool": "Boolean",
            "stdNum": "NumFloat64",  # Default to float for numeric
            "stdInt32": "NumInt32",
            "stdInt16": "NumInt16",
            "stdFloat64": "NumFloat64",
            "stdFloat32": "NumFloat32",
            "stdArray": "Array",
            "stdClust": "Cluster",
        }
        return control_map.get(control_type)

    def _map_type(self, lv_type: str) -> str:
        """Map LabVIEW type to Python type."""
        type_map = {
            # Standard LabVIEW type names
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
            # Lowercase variants from graph
            "path": "Path",
            "string": "str",
            "str": "str",
            "bool": "bool",
            "boolean": "bool",
            "int": "int",
            "float": "float",
        }
        return type_map.get(lv_type, "Any")

    def _default_for_type(self, type_hint: str) -> str:
        """Get default value literal for a Python type."""
        defaults = {
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
        return defaults.get(type_hint, "None")


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
