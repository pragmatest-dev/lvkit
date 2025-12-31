#!/usr/bin/env python3
"""Generate code using AST builder without LLM.

Uses the new AST-based code generation (builder.py), not skeleton.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.memory_graph import InMemoryVIGraph
from vipy.vilib_resolver import get_resolver as get_vilib_resolver
from vipy.agent.codegen import build_module


def to_function_name(vi_name: str) -> str:
    name = vi_name.replace(".vi", "").replace(".VI", "")
    if ":" in name:
        name = name.split(":")[-1]
    result = name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    if result and not result[0].isalpha():
        result = "vi_" + result
    return result or "vi_function"


def to_module_name(vi_name: str) -> str:
    if ":" in vi_name:
        vi_name = vi_name.split(":")[-1]
    vi_name = vi_name.replace(".vi", "").replace(".VI", "")
    result = vi_name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    return result or "module"


def is_polymorphic_variant(vi_name: str, all_vis: list[str]) -> tuple[bool, str | None]:
    """Check if VI is a polymorphic variant.

    Returns (is_variant, base_name) where base_name is the polymorphic wrapper VI name.
    """
    # Pattern: "Base - Variant__suffix.vi" or "Base - Variant.vi"
    # Look for " - " in the name which indicates a variant
    name = vi_name.replace(".vi", "").replace(".VI", "")

    if " - " not in name:
        return False, None

    # Extract potential base name (everything before " - ")
    parts = name.split(" - ")
    base = parts[0]

    # Check if the base + .vi exists in the VI list
    for suffix in ["__ogtk", ""]:
        base_vi = f"{base}{suffix}.vi"
        if base_vi in all_vis and base_vi != vi_name:
            return True, base_vi

    return False, None


def get_polymorphic_groups(order: list[str]) -> dict[str, list[str]]:
    """Group VIs by polymorphic wrapper.

    Returns dict mapping wrapper VI name to list of variant VI names.
    """
    groups: dict[str, list[str]] = {}

    for vi_name in order:
        is_variant, base_name = is_polymorphic_variant(vi_name, order)
        if is_variant and base_name:
            if base_name not in groups:
                groups[base_name] = []
            groups[base_name].append(vi_name)

    return groups


def generate_polymorphic_module(
    wrapper_name: str,
    variants: list[str],
    graph: InMemoryVIGraph,
    vilib_resolver,
) -> str:
    """Generate a module containing all variants and wrapper."""
    lines = [
        '"""Polymorphic VI module."""',
        "from __future__ import annotations",
        "from pathlib import Path",
        "from typing import Any, NamedTuple",
        "",
    ]

    # Generate each variant
    variant_funcs = []
    variant_result_classes = []  # Track result class names
    all_inputs: dict[int, dict] = {}
    all_outputs: dict[int, dict] = {}

    for variant_name in variants:
        vi_context = graph.get_vi_context(variant_name)
        func_name = to_function_name(variant_name)
        variant_funcs.append(func_name)

        try:
            code = build_module(vi_context, variant_name, graph.get_vi_context)
            # Extract just the function and result class (skip imports)
            tree = ast.parse(code)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    lines.append("")
                    lines.append(ast.unparse(node))
                    variant_result_classes.append(node.name)
                elif isinstance(node, ast.FunctionDef):
                    lines.append("")
                    lines.append(ast.unparse(node))

            # Collect inputs/outputs for union signature
            for inp in vi_context.get("inputs", []):
                idx = inp.get("slot_index", inp.get("index", len(all_inputs)))
                if idx not in all_inputs:
                    all_inputs[idx] = inp
            for out in vi_context.get("outputs", []):
                idx = out.get("slot_index", out.get("index", len(all_outputs)))
                if idx not in all_outputs:
                    all_outputs[idx] = out

        except Exception as e:
            lines.append(f"# ERROR generating {variant_name}: {e}")

    # Generate wrapper function
    wrapper_func = to_function_name(wrapper_name)

    # Build parameter list from union of variant inputs
    params = []
    for idx in sorted(all_inputs.keys()):
        inp = all_inputs[idx]
        name = inp.get("name", f"arg_{idx}")
        var_name = name.lower().replace(" ", "_").replace("-", "_")
        var_name = "".join(c for c in var_name if c.isalnum() or c == "_")
        if var_name and not var_name[0].isalpha():
            var_name = "p_" + var_name
        params.append(f"{var_name}: Any = None")

    param_str = ", ".join(params) if params else ""

    # Determine return type from outputs - use first variant's result class
    if variant_result_classes:
        returns = variant_result_classes[0]
    elif all_outputs:
        returns = "Any"
    else:
        returns = "None"

    # Generate wrapper with runtime type dispatch
    lines.append("")
    lines.append("")
    lines.append(f"def {wrapper_func}({param_str}) -> {returns}:")
    lines.append(f'    """Polymorphic wrapper for {wrapper_name}."""')

    if variant_funcs and params:
        param_names = [p.split(":")[0].strip() for p in params]
        call_args = ", ".join(f"{n}={n}" for n in param_names)
        first_param = param_names[0] if param_names else None

        # Categorize variants by type (array vs traditional/scalar)
        array_variants = [f for f in variant_funcs if "array" in f.lower()]
        traditional_variants = [f for f in variant_funcs if "traditional" in f.lower()]
        other_variants = [f for f in variant_funcs if f not in array_variants and f not in traditional_variants]

        # Generate type-based dispatch
        if array_variants and (traditional_variants or other_variants):
            # Have both array and non-array variants - dispatch on type
            lines.append(f"    if isinstance({first_param}, (list, tuple)):")
            lines.append(f"        return {array_variants[0]}({call_args})")
            lines.append("    else:")
            fallback = traditional_variants[0] if traditional_variants else (other_variants[0] if other_variants else variant_funcs[0])
            lines.append(f"        return {fallback}({call_args})")
        else:
            # Only one type of variant - call first one
            lines.append(f"    return {variant_funcs[0]}({call_args})")
    elif variant_funcs:
        lines.append(f"    return {variant_funcs[0]}()")
    else:
        lines.append("    pass")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate code using AST builder (no LLM)")
    parser.add_argument("input", help="VI file to convert")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--search-path", action="append", dest="search_paths",
                        default=[], help="Additional search paths")
    args = parser.parse_args()

    output_dir = Path(args.output)

    # Clean output directory
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading VI: {args.input}")

    graph = InMemoryVIGraph()
    search_paths = [Path(p) for p in args.search_paths]
    graph.load_vi(args.input, search_paths=search_paths)

    order = graph.get_conversion_order()
    print(f"\nConversion order ({len(order)} VIs):")

    vilib_resolver = get_vilib_resolver()

    # Identify polymorphic groups
    poly_groups = get_polymorphic_groups(order)
    poly_variants = set()
    for variants in poly_groups.values():
        poly_variants.update(variants)

    generated = []

    for i, vi_name in enumerate(order, 1):
        # Skip variants - they'll be generated with their wrapper
        if vi_name in poly_variants:
            print(f"  [{i}/{len(order)}] {vi_name}")
            print(f"         -> (included in polymorphic wrapper)")
            continue

        is_stub = graph.is_stub_vi(vi_name)
        has_vilib = vilib_resolver.has_implementation(vi_name) if is_stub else False

        module_name = to_module_name(vi_name)
        output_path = output_dir / f"{module_name}.py"

        print(f"  [{i}/{len(order)}] {vi_name}")

        if is_stub and has_vilib:
            # Generate vilib implementation
            code = vilib_resolver.get_implementation(vi_name)
            output_path.write_text(code)
            print(f"         -> vilib: {output_path.name}")
            generated.append((vi_name, output_path, "vilib"))

        elif is_stub:
            # Generate stub
            func_name = to_function_name(vi_name)
            code = f'''"""Stub: {vi_name}."""
from __future__ import annotations
from typing import Any

def {func_name}(*args, **kwargs) -> Any:
    raise NotImplementedError("{vi_name}")
'''
            output_path.write_text(code)
            print(f"         -> stub: {output_path.name}")
            generated.append((vi_name, output_path, "stub"))

        elif vi_name in poly_groups:
            # Generate polymorphic module with all variants
            variants = poly_groups[vi_name]
            try:
                code = generate_polymorphic_module(vi_name, variants, graph, vilib_resolver)
                ast.parse(code)  # Validate syntax
                output_path.write_text(code)
                print(f"         -> polymorphic: {output_path.name} ({len(variants)} variants)")
                generated.append((vi_name, output_path, "ast"))
            except SyntaxError as e:
                error_path = output_dir / f"{module_name}.error.py"
                error_path.write_text(f"# SYNTAX ERROR: {e}\n\n{code}")
                print(f"         -> SYNTAX ERROR: {error_path.name}")
                generated.append((vi_name, error_path, "error"))
            except Exception as e:
                print(f"         -> FAILED: {e}")
                import traceback
                traceback.print_exc()
                generated.append((vi_name, None, "failed"))

        else:
            # Use AST builder for regular VIs
            vi_context = graph.get_vi_context(vi_name)

            try:
                code = build_module(vi_context, vi_name, graph.get_vi_context)

                # Validate syntax
                ast.parse(code)
                output_path.write_text(code)
                print(f"         -> AST: {output_path.name}")
                generated.append((vi_name, output_path, "ast"))

            except SyntaxError as e:
                error_path = output_dir / f"{module_name}.error.py"
                error_path.write_text(f"# SYNTAX ERROR: {e}\n\n{code}")
                print(f"         -> SYNTAX ERROR: {error_path.name}")
                generated.append((vi_name, error_path, "error"))

            except Exception as e:
                print(f"         -> FAILED: {e}")
                generated.append((vi_name, None, "failed"))

    # Generate __init__.py
    init_lines = ['"""Generated package."""', ""]
    for vi_name, path, status in generated:
        if path and status in ("vilib", "ast"):
            func_name = to_function_name(vi_name)
            module_name = to_module_name(vi_name)
            init_lines.append(f"from .{module_name} import {func_name}")
    init_lines.append("")
    (output_dir / "__init__.py").write_text("\n".join(init_lines))

    print(f"\nOutput: {output_dir}")
    print(f"  vilib: {sum(1 for _, _, s in generated if s == 'vilib')}")
    print(f"  ast:   {sum(1 for _, _, s in generated if s == 'ast')}")
    print(f"  stub:  {sum(1 for _, _, s in generated if s == 'stub')}")
    print(f"  error: {sum(1 for _, _, s in generated if s == 'error')}")


if __name__ == "__main__":
    main()
