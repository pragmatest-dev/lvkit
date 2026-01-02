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
            # Comment out all lines of the error message
            error_msg = str(e).replace('\n', '\n# ')
            lines.append(f"# ERROR generating {variant_name}: {error_msg}")

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
    parser.add_argument("--generate-ui", action="store_true", help="Generate NiceGUI wrappers")
    parser.add_argument("--ui-vilib", action="store_true", help="Generate UI for vilib VIs")
    parser.add_argument("--ui-primitives", action="store_true", help="Generate UI for primitives")
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

    # Identify polymorphic groups (from VI metadata, not heuristics)
    poly_groups = graph.get_polymorphic_groups()
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
        has_vilib = vilib_resolver.has_implementation(vi_name)
        has_inline = vilib_resolver.has_inline(vi_name)

        module_name = to_module_name(vi_name)
        output_path = output_dir / f"{module_name}.py"

        print(f"  [{i}/{len(order)}] {vi_name}")

        if has_inline:
            # Skip - inlined at call sites by AST builder
            print(f"         -> (inlined at call sites)")
            continue

        if has_vilib:
            # Generate vilib/openg implementation
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
            # Check if all variants are inlined
            variants = poly_groups[vi_name]
            all_inlined = all(vilib_resolver.has_inline(v) for v in variants)
            if all_inlined:
                print(f"         -> (polymorphic, all variants inlined)")
                continue

            # Generate polymorphic module with all variants
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

    # Save terminal observations for incremental collection
    from vipy.terminal_collector import get_collector
    collector = get_collector()
    if collector.data["observations"]:
        collector.save()
        print(f"\nTerminal observations collected for {len(collector.data['observations'])} VI(s)")
        print(f"  Saved to: {collector.pending_file}")

    # Generate UI wrappers if requested
    if args.generate_ui:
        from vipy.agent.context import ContextBuilder

        print("\nGenerating UI wrappers...")
        ui_count = 0
        for vi_name, path, status in generated:
            if not path:
                continue

            # Filter by status based on flags
            if status == "vilib" and not args.ui_vilib:
                continue
            if status not in ("vilib", "ast"):
                continue

            # Get inputs/outputs - use vilib resolver for vilib VIs
            ui_inputs = []
            ui_outputs = []

            if status == "vilib":
                # Get terminal info from vilib resolver
                vilib_ctx = vilib_resolver.get_context(vi_name)
                if vilib_ctx:
                    for term in vilib_ctx.get("terminals", []):
                        name = term.get("name", "")
                        term_type = term.get("type") or "Any"
                        if term.get("direction") == "in":
                            ui_inputs.append((name, term_type))
                        elif term.get("direction") == "out":
                            ui_outputs.append((name, term_type))
            else:
                # Get from graph context for AST-generated VIs
                vi_context = graph.get_vi_context(vi_name)
                for inp in vi_context.get("inputs", []):
                    name = inp.name or "input"
                    ctrl_type = inp.control_type or "Any"
                    ui_inputs.append((name, ctrl_type))

                for out in vi_context.get("outputs", []):
                    name = out.name or "output"
                    ctrl_type = out.control_type or "Any"
                    ui_outputs.append((name, ctrl_type))

            if ui_inputs or ui_outputs:
                module_name = to_module_name(vi_name)
                func_name = to_function_name(vi_name)
                # Get enum definitions for vilib VIs
                enums = {}
                if status == "vilib":
                    vilib_ctx = vilib_resolver.get_context(vi_name)
                    if vilib_ctx:
                        for term in vilib_ctx.get("terminals", []):
                            # Check for inline enum_values first
                            if term.get("enum_values"):
                                # Use display name as key (ContextBuilder._to_var_name will convert it)
                                display_name = term.get("name", "")
                                enums[display_name] = term.get("enum_values")
                            # Fall back to named enum reference
                            elif term.get("enum"):
                                enum_name = term.get("enum")
                                all_enums = vilib_resolver.get_enums()
                                if enum_name in all_enums:
                                    enum_def = all_enums[enum_name]
                                    display_name = term.get("name", "")
                                    enums[display_name] = [
                                        (v["value"], name)
                                        for name, v in enum_def.get("values", {}).items()
                                    ]

                ui_code = ContextBuilder.build_ui_wrapper(
                    vi_name=vi_name,
                    module_name=module_name,
                    function_name=func_name,
                    inputs=ui_inputs,
                    outputs=ui_outputs,
                    enums=enums,
                )
                ui_path = output_dir / f"{module_name}_ui.py"
                ui_path.write_text(ui_code)
                ui_count += 1

        print(f"  Generated {ui_count} UI wrappers")

        # Copy app.py template
        app_template = Path(__file__).parent.parent / "src" / "vipy" / "explorer.py"
        if app_template.exists():
            shutil.copy(app_template, output_dir / "app.py")
            print(f"  Copied app.py - run with: python {output_dir}/app.py")


if __name__ == "__main__":
    main()
