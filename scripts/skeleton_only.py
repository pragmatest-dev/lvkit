#!/usr/bin/env python3
"""Generate skeletons without LLM - for testing the conversion flow.

This script follows the ConversionAgent flow but stops after skeleton generation.
It generates:
1. vilib implementation files for stub VIs with vilib support
2. Skeleton files for non-stub VIs (no LLM refinement)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.memory_graph import InMemoryVIGraph
from vipy.vilib_resolver import get_resolver as get_vilib_resolver
from vipy.agent.skeleton import SkeletonGenerator
from vipy.agent.context import VISignature
from vipy.agent.state import ConversionState, ConvertedModule


def to_function_name(vi_name: str) -> str:
    """Convert VI name to Python function name."""
    name = vi_name.replace(".vi", "").replace(".VI", "")
    if ":" in name:
        name = name.split(":")[-1]
    result = name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    if result and not result[0].isalpha():
        result = "vi_" + result
    return result or "vi_function"


def to_module_name(vi_name: str) -> str:
    """Convert VI name to module name."""
    # Extract just the VI name part
    if ":" in vi_name:
        vi_name = vi_name.split(":")[-1]
    vi_name = vi_name.replace(".vi", "").replace(".VI", "")
    result = vi_name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    return result or "module"


def main():
    parser = argparse.ArgumentParser(description="Generate skeletons without LLM")
    parser.add_argument("input", help="VI file to convert")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--search-path", action="append", dest="search_paths",
                        default=[], help="Additional search paths")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading VI: {args.input}")
    print(f"Search paths: {args.search_paths}")

    # Load the VI and its dependencies
    graph = InMemoryVIGraph()
    search_paths = [Path(p) for p in args.search_paths]
    graph.load_vi(args.input, search_paths=search_paths)

    # Get conversion order (dependencies first)
    order = graph.get_conversion_order()
    print(f"\nConversion order ({len(order)} VIs):")

    vilib_resolver = get_vilib_resolver()
    state = ConversionState()

    for i, vi_name in enumerate(order, 1):
        is_stub = graph.is_stub_vi(vi_name)
        has_vilib = vilib_resolver.has_implementation(vi_name) if is_stub else False

        print(f"  [{i}/{len(order)}] {vi_name}")
        print(f"         stub={is_stub}, vilib={has_vilib}")

        module_name = to_module_name(vi_name)
        output_path = output_dir / f"{module_name}.py"

        if is_stub and has_vilib:
            # Generate vilib implementation
            code = vilib_resolver.get_implementation(vi_name)
            output_path.write_text(code)
            print(f"         -> Generated vilib implementation: {output_path.name}")

            # Track in state
            func_name = to_function_name(vi_name)
            state.mark_converted(
                vi_name,
                output_path,
                library_name=None,
            )

        elif is_stub:
            # Generate stub (NotImplementedError)
            func_name = to_function_name(vi_name)
            code = f'''"""Stub for missing SubVI: {vi_name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def {func_name}(*args, **kwargs) -> Any:
    """Stub for missing SubVI."""
    raise NotImplementedError("Missing SubVI: {vi_name}")
'''
            output_path.write_text(code)
            print(f"         -> Generated stub: {output_path.name}")
            state.mark_converted(vi_name, output_path, library_name=None)

        else:
            # Generate skeleton (non-stub VI)
            vi_context = graph.get_vi_context(vi_name)

            # Get converted deps as VISignatures
            converted_deps = {}
            for dep_name in graph.get_vi_dependencies(vi_name):
                if state.is_converted(dep_name):
                    module = state.get_module(dep_name)
                    if module:
                        converted_deps[dep_name] = VISignature(
                            name=dep_name,
                            module_name=module.module_name,
                            function_name=module.exports[0] if module.exports else to_function_name(dep_name),
                            signature="",
                            import_statement=f"from .{module.module_name} import {module.exports[0] if module.exports else to_function_name(dep_name)}",
                        )

            # Generate skeleton
            gen = SkeletonGenerator(converted_deps, vilib_resolver)
            skeleton = gen.generate(vi_context, vi_name)
            code = gen.to_python(skeleton)

            # Validate syntax
            try:
                ast.parse(code)
                output_path.write_text(code)
                print(f"         -> Generated skeleton: {output_path.name}")

                func_name = to_function_name(vi_name)
                state.mark_converted(vi_name, output_path, library_name=None)

            except SyntaxError as e:
                print(f"         -> SYNTAX ERROR in skeleton: {e}")
                # Still write it for inspection
                error_path = output_dir / f"{module_name}.error.py"
                error_path.write_text(f"# SYNTAX ERROR: {e}\n\n{code}")
                print(f"         -> Saved with errors: {error_path.name}")
                state.mark_failed(vi_name)

    # Generate __init__.py
    init_lines = ['"""Generated package."""', "", "from __future__ import annotations", ""]
    for vi_name in order:
        if state.is_converted(vi_name):
            module = state.get_module(vi_name)
            if module and module.exports:
                for export in module.exports:
                    init_lines.append(f"from .{module.module_name} import {export}")
    init_lines.append("")
    (output_dir / "__init__.py").write_text("\n".join(init_lines))

    print(f"\nOutput written to: {output_dir}")
    print(f"Generated {sum(1 for v in order if state.is_converted(v))} files")


if __name__ == "__main__":
    main()
