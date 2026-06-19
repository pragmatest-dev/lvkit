"""Code generation for Formula Nodes (fBox).

Transpiles the embedded script to a deterministic, self-contained Python
function, injects that function at module level (via the context), and emits
a call to it with the node's resolved input variables, binding each output.
No C compiler, no FFI — the generated module is pure Python.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from lvkit.formula.emit import VarSpec, transpile
from lvkit.models import FormulaOperation

from ..fragment import CodeFragment
from .base import CodeGenError

if TYPE_CHECKING:
    from ..context import CodeGenContext


def _varspecs(op: FormulaOperation) -> list[VarSpec]:
    """Build deduped VarSpecs from the node's terminals.

    A variable wired on both sides appears as two terminals (input + output)
    and becomes a single ``inout`` spec. Array element types come from the
    terminal's resolved LVType.
    """
    by_name: dict[str, dict] = {}
    order: list[str] = []
    for t in op.terminals:
        if not t.name:
            continue
        lt = t.lv_type
        is_array = lt is not None and lt.kind == "array"
        if is_array:
            elem = lt.element_type if lt else None
            lv = elem.underlying_type if elem else None
        else:
            lv = lt.underlying_type if lt else None
        direction = "out" if t.direction == "output" else "in"
        if t.name not in by_name:
            by_name[t.name] = {"lv": lv, "dirs": set(), "arr": is_array}
            order.append(t.name)
        by_name[t.name]["dirs"].add(direction)
    specs: list[VarSpec] = []
    for name in order:
        info = by_name[name]
        if info["lv"] is None:
            raise CodeGenError(
                f"Formula Node variable {name!r} has no resolved type"
            )
        direction = (
            "inout" if info["dirs"] == {"in", "out"} else next(iter(info["dirs"]))
        )
        specs.append(VarSpec(name, info["lv"], direction, info["arr"]))
    return specs


def generate(node: FormulaOperation, ctx: CodeGenContext) -> CodeFragment:
    if not node.script:
        raise CodeGenError(f"Formula Node {node.id} has no script")

    uid = node.id.split("::")[-1]
    suffix = re.sub(r"\W", "_", uid)
    func_name = f"_formula_{suffix}"

    specs = _varspecs(node)
    result = transpile(node.script, specs, func_name=func_name)
    # Inject the generated function at module level (once per node).
    ctx.formula_helpers.append(result.func_def)

    # Map terminals by direction for input resolution / output binding.
    inputs = {t.name: t for t in node.terminals if t.direction == "input" and t.name}
    outputs = {t.name: t for t in node.terminals if t.direction == "output" and t.name}

    input_args: list[str] = []
    for spec in specs:
        if spec.direction in ("in", "inout"):
            term = inputs.get(spec.name)
            val = ctx.resolve(term.id) if term else None
            if val is None:
                val = "[]" if spec.is_array else "0"
            elif spec.is_array:
                # LabVIEW value-copies arrays at a wire branch; the formula
                # node works on its own buffer per terminal. Copy each array
                # arg so the function's in-place writes can't alias the caller's
                # data or another terminal (which corrupts results).
                val = f"list({val})"
            input_args.append(f"{spec.name}={val}")

    bindings: dict[str, str] = {}
    output_lines: list[str] = []
    resvar = f"_fr_{suffix}"
    for spec in specs:
        if spec.direction in ("out", "inout"):
            term = outputs.get(spec.name)
            if term is None:
                continue
            out_var = ctx.make_output_var(spec.name, node.id, terminal_id=term.id)
            bindings[term.id] = out_var
            output_lines.append(f"{out_var} = {resvar}[{spec.name!r}]")

    lines = [
        f"{resvar} = {func_name}({', '.join(input_args)})",
        *output_lines,
    ]
    stmts = ast.parse("\n".join(lines)).body

    return CodeFragment(
        statements=stmts,
        bindings=bindings,
        imports=set(result.imports),
    )
