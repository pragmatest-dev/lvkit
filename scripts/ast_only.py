#!/usr/bin/env python3
"""Generate code using AST builder without LLM.

Uses the new AST-based code generation (builder.py), not skeleton.
"""

from __future__ import annotations

import argparse
import ast
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


def main():
    parser = argparse.ArgumentParser(description="Generate code using AST builder (no LLM)")
    parser.add_argument("input", help="VI file to convert")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--search-path", action="append", dest="search_paths",
                        default=[], help="Additional search paths")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading VI: {args.input}")

    graph = InMemoryVIGraph()
    search_paths = [Path(p) for p in args.search_paths]
    graph.load_vi(args.input, search_paths=search_paths)

    order = graph.get_conversion_order()
    print(f"\nConversion order ({len(order)} VIs):")

    vilib_resolver = get_vilib_resolver()
    generated = []

    for i, vi_name in enumerate(order, 1):
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

        else:
            # Use AST builder
            vi_context = graph.get_vi_context(vi_name)

            try:
                code = build_module(vi_context, vi_name)

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
