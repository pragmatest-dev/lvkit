#!/usr/bin/env python3
"""Generate code using AST builder without LLM.

Uses the new AST-based code generation (builder.py), not skeleton.
"""

from __future__ import annotations

import argparse
import ast
import sys

sys.setrecursionlimit(10000)
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.agent.codegen import build_module
from vipy.agent.codegen.ast_utils import to_function_name, to_module_name
from vipy.memory_graph import InMemoryVIGraph
from vipy.vilib_resolver import get_resolver as get_vilib_resolver


def _sanitize_lib_name(name: str) -> str:
    """Sanitize a library/class name to a valid Python package name."""
    result = name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    return result


def to_library_name(
    vi_name: str,
    graph: InMemoryVIGraph | None = None,
    vilib_resolver: object | None = None,
) -> str | None:
    """Determine output subdirectory from VI library membership.

    Uses the graph's metadata (library, qualified_name) when available,
    falls back to parsing the VI name string.

    Returns None for top-level VIs with no library membership — these
    go in the output root directory.

    Examples:
        "GraphicalTestRunner.lvlib:Get Settings Path.vi" -> "graphicaltestrunner"
        "MyClass.lvclass:Init.vi" -> "myclass"
        "Build Path__ogtk.vi" -> "openg"
        "DAQmx Start Task.vi" (vilib impl) -> "vilib"
        "In.vi" (no library) -> None  (output root)
    """
    # 1. Check graph metadata for library membership
    if graph is not None:
        meta = graph._vi_metadata.get(vi_name, {})
        library = meta.get("library")
        if library:
            return _sanitize_lib_name(library) or None

    # 2. Check qualified name in VI name string (lvlib or lvclass prefix)
    if ".lvlib:" in vi_name:
        library = vi_name.split(":")[0].replace(".lvlib", "")
        return _sanitize_lib_name(library) or None
    elif ".lvclass:" in vi_name:
        library = vi_name.split(":")[0].replace(".lvclass", "")
        return _sanitize_lib_name(library) or None

    # 3. OpenG naming convention
    if "__ogtk" in vi_name:
        return "openg"

    # 4. Known vilib implementation — goes under vilib/
    if vilib_resolver is not None and hasattr(vilib_resolver, "has_implementation"):
        if vilib_resolver.has_implementation(vi_name):
            return "vilib"

    # 5. No library membership — top-level output root
    return None


def get_output_path(
    output_dir: Path,
    vi_name: str,
    create_dirs: bool = True,
    graph: InMemoryVIGraph | None = None,
    vilib_resolver: object | None = None,
) -> tuple[Path, str | None]:
    """Get output path and library name for a VI.

    Returns (path, library_name) where path includes library subdirectory
    if the VI belongs to a library, or the output root if it doesn't.
    """
    module_name = to_module_name(vi_name)
    library_name = to_library_name(vi_name, graph=graph, vilib_resolver=vilib_resolver)

    if library_name:
        lib_dir = output_dir / library_name
        if create_dirs:
            lib_dir.mkdir(parents=True, exist_ok=True)
            # Ensure library __init__.py exists
            init_path = lib_dir / "__init__.py"
            if not init_path.exists():
                init_path.write_text(f'"""Package for {library_name} library."""\n')
        return (lib_dir / f"{module_name}.py", library_name)
    else:
        return (output_dir / f"{module_name}.py", None)


def create_import_resolver(
    package_name: str,
    output_dir: Path,
    vi_paths: dict[str, Path],
    graph: InMemoryVIGraph | None = None,
    vilib_resolver: object | None = None,
) -> callable:
    """Create an import resolver for a VI.

    Args:
        package_name: Name of the output package (e.g., "get_settings_path")
        output_dir: Root output directory
        vi_paths: Dict mapping fully qualified VI names to their output paths
        graph: Memory graph for library metadata lookup
        vilib_resolver: VILib resolver for vilib membership check

    Returns:
        Callable that takes a SubVI name and returns the correct import statement
    """
    def resolver(subvi_name: str) -> str:
        func_name = to_function_name(subvi_name)

        # Look up the dependency's path
        if subvi_name in vi_paths:
            dep_path = vi_paths[subvi_name]
        else:
            # Not in our paths - compute it
            dep_path, _ = get_output_path(
                output_dir, subvi_name, create_dirs=False,
                graph=graph, vilib_resolver=vilib_resolver,
            )

        # Build absolute package import
        dep_module = dep_path.stem  # filename without .py
        dep_library = to_library_name(
            subvi_name, graph=graph, vilib_resolver=vilib_resolver,
        )

        if dep_library:
            return f"from {package_name}.{dep_library}.{dep_module} import {func_name}"
        else:
            return f"from {package_name}.{dep_module} import {func_name}"

    return resolver


def generate_polymorphic_module(
    wrapper_name: str,
    variants: list[str],
    graph: InMemoryVIGraph,
    vilib_resolver,
    package_name: str,
    output_dir: Path,
    vi_paths: dict[str, Path],
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
            import_resolver = create_import_resolver(
                    package_name, output_dir, vi_paths,
                    graph=graph, vilib_resolver=vilib_resolver,
                )
            code = build_module(vi_context, variant_name, import_resolver=import_resolver, graph=graph)
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


def resolve_vi_path(cls_dir: Path, relative_path: str) -> Path | None:
    """Resolve VI path from lvclass relative URL.

    Class membership is defined by the lvclass XML - we're just resolving
    the stored path to the actual file. LabVIEW stores paths with extra ../
    that don't match the actual filesystem layout.

    Args:
        cls_dir: Directory containing the lvclass file
        relative_path: Relative path from lvclass (e.g., "../hooks/setUp.vi")

    Returns:
        Resolved path if found, None otherwise
    """
    # Try direct resolution first
    direct = cls_dir / relative_path
    if direct.exists():
        return direct.resolve()

    # LabVIEW stores paths with extra ../ - strip them and resolve from cls_dir
    stripped = relative_path
    while stripped.startswith("../"):
        stripped = stripped[3:]
    if stripped != relative_path:
        from_cls = cls_dir / stripped
        if from_cls.exists():
            return from_cls.resolve()

    return None


def find_parent_class(child_path: Path, parent_name: str) -> Path | None:
    """Find parent class file by searching up the directory tree.

    Args:
        child_path: Path to child .lvclass file
        parent_name: Name of parent class (e.g., "TestCase")

    Returns:
        Path to parent .lvclass file, or None if not found
    """
    # Search pattern: look for ParentName.lvclass or ParentName/ParentName.lvclass
    search_dirs = [
        child_path.parent,  # Same directory
        child_path.parent.parent,  # Parent directory
        child_path.parent.parent.parent,  # Grandparent
    ]

    # Also search in common class locations
    for ancestor in child_path.parents:
        if (ancestor / "Classes").exists():
            search_dirs.append(ancestor / "Classes")
        if (ancestor / "source" / "Classes").exists():
            search_dirs.append(ancestor / "source" / "Classes")

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        # Direct match
        direct = search_dir / f"{parent_name}.lvclass"
        if direct.exists():
            return direct

        # Subdirectory match (ParentName/ParentName.lvclass)
        subdir = search_dir / parent_name / f"{parent_name}.lvclass"
        if subdir.exists():
            return subdir

        # Search recursively (slower but thorough)
        for lvclass in search_dir.rglob(f"{parent_name}.lvclass"):
            return lvclass

    return None


def main():
    parser = argparse.ArgumentParser(description="Generate code using AST builder (no LLM)")
    parser.add_argument("input", help="VI file to convert")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--search-path", action="append", dest="search_paths",
                        default=[], help="Additional search paths")
    parser.add_argument("--generate-ui", action="store_true", help="Generate NiceGUI wrappers")
    parser.add_argument("--ui-vilib", action="store_true", help="Generate UI for vilib VIs")
    parser.add_argument("--ui-primitives", action="store_true", help="Generate UI for primitives")
    parser.add_argument("--auto-update", action="store_true",
                        help="Auto-update vilib registry with terminal info from callers")
    args = parser.parse_args()

    # Create VI-named subfolder within output directory
    input_path = Path(args.input)
    vi_folder_name = input_path.stem  # Get filename without extension
    # Remove special characters and whitespace
    vi_folder_name = re.sub(r"[^\w]", "_", vi_folder_name).lower()
    vi_folder_name = re.sub(r"_+", "_", vi_folder_name).strip("_")

    output_dir = Path(args.output) / vi_folder_name

    # Clean output directory
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading: {args.input}")

    graph = InMemoryVIGraph()
    search_paths = [Path(p) for p in args.search_paths]

    # Detect input type and load appropriately
    if input_path.suffix.lower() == ".lvclass":
        graph.load_lvclass(args.input, search_paths=search_paths)
    elif input_path.suffix.lower() == ".lvlib":
        graph.load_lvlib(args.input, search_paths=search_paths)
    elif input_path.is_dir():
        graph.load_directory(args.input, search_paths=search_paths)
    else:
        graph.load_vi(args.input, search_paths=search_paths)

    order = graph.get_conversion_order()
    print(f"\nConversion order ({len(order)} VIs):")

    vilib_resolver = get_vilib_resolver()

    # Identify polymorphic groups (from VI metadata, not heuristics)
    poly_groups = graph.get_polymorphic_groups()
    poly_variants = set()
    for variants in poly_groups.values():
        poly_variants.update(variants)

    # Pre-compute all output paths for import resolution
    vi_paths: dict[str, Path] = {}
    for vi_name in order:
        path, _ = get_output_path(
            output_dir, vi_name, create_dirs=False,
            graph=graph, vilib_resolver=vilib_resolver,
        )
        vi_paths[vi_name] = path

    generated = []

    for i, vi_name in enumerate(order, 1):
        # Skip variants - they'll be generated with their wrapper
        if vi_name in poly_variants:
            print(f"  [{i}/{len(order)}] {vi_name}")
            print("         -> (included in polymorphic wrapper)")
            continue

        is_stub = graph.is_stub_vi(vi_name)
        has_vilib = vilib_resolver.has_implementation(vi_name)
        has_inline = vilib_resolver.has_inline(vi_name)

        print(f"  [{i}/{len(order)}] {vi_name}")

        if has_inline:
            # Skip - inlined at call sites by AST builder
            print("         -> (inlined at call sites)")
            continue

        # Check polymorphic wrappers early - skip if all variants are inlined
        if vi_name in poly_groups:
            variants = poly_groups[vi_name]
            all_inlined = all(vilib_resolver.has_inline(v) for v in variants)
            if all_inlined:
                print("         -> (polymorphic, all variants inlined)")
                continue

        # Only create directory structure when we're actually going to write a file
        output_path, library_name = get_output_path(
            output_dir, vi_name, graph=graph, vilib_resolver=vilib_resolver,
        )
        module_name = to_module_name(vi_name)

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
            # Generate polymorphic module with all variants
            # (already checked all_inlined above - only reach here if not all inlined)
            variants = poly_groups[vi_name]
            try:
                code = generate_polymorphic_module(vi_name, variants, graph, vilib_resolver, vi_folder_name, output_dir, vi_paths)
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
                import_resolver = create_import_resolver(
                vi_folder_name, output_dir, vi_paths,
                graph=graph, vilib_resolver=vilib_resolver,
            )
                code = build_module(vi_context, vi_name, import_resolver=import_resolver, graph=graph)

                # Validate syntax
                ast.parse(code)
                output_path.write_text(code)
                print(f"         -> AST: {output_path.name}")
                generated.append((vi_name, output_path, "ast"))

            except SyntaxError as e:
                error_path = output_dir / f"{module_name}.error.py"
                error_path.write_text(f"# SYNTAX ERROR: {e}\n\n{code if 'code' in dir() else '# code generation failed'}")
                print(f"         -> SYNTAX ERROR: {error_path.name}")
                generated.append((vi_name, error_path, "error"))

            except Exception as e:
                error_path = output_dir / f"{module_name}.error.py"
                error_path.write_text(f"# ERROR: {e}")
                print(f"         -> FAILED: {e}")
                generated.append((vi_name, error_path, "error"))

    # Generate minimal __init__.py (just makes it a package)
    init_path = output_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text('"""Generated package."""\n')

    # Generate class wrapper if input was an lvclass
    if input_path.suffix.lower() == ".lvclass":
        from vipy.agent.codegen import ClassBuilder, ClassConfig
        from vipy.structure import parse_lvclass

        lvclass = parse_lvclass(input_path)

        # Get method contexts from graph
        method_contexts = {}
        for method in lvclass.methods:
            qualified_name = f"{lvclass.name}.lvclass:{method.name}.vi"
            ctx = graph.get_vi_context(qualified_name)
            if ctx:
                method_contexts[method.name] = ctx

        # Build class wrapper with context lookup for SubVI resolution
        import_resolver = create_import_resolver(
                vi_folder_name, output_dir, vi_paths,
                graph=graph, vilib_resolver=vilib_resolver,
            )
        builder = ClassBuilder(config=ClassConfig())
        module = builder.build_class_module(
            lvclass,
            method_contexts,
            import_resolver=import_resolver,
            graph=graph,
        )
        ast.fix_missing_locations(module)
        class_code = ast.unparse(module)

        # Write class file
        class_filename = to_module_name(lvclass.name) + ".py"
        class_path = output_dir / class_filename
        class_path.write_text(class_code)
        print(f"\nGenerated class wrapper: {class_filename}")

    print(f"\nOutput: {output_dir}")
    print(f"  vilib: {sum(1 for _, _, s in generated if s == 'vilib')}")
    print(f"  ast:   {sum(1 for _, _, s in generated if s == 'ast')}")
    print(f"  stub:  {sum(1 for _, _, s in generated if s == 'stub')}")
    print(f"  error: {sum(1 for _, _, s in generated if s == 'error')}")

    # Save terminal observations for incremental collection
    from vipy.terminal_collector import get_collector
    collector = get_collector()
    if collector.data.get("observations"):
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
                        direction = term.get("direction", "")
                        if direction in ("in", "input"):
                            ui_inputs.append((name, term_type))
                        elif direction in ("out", "output"):
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
                library_name = to_library_name(vi_name, graph=graph, vilib_resolver=vilib_resolver)

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

                # Write UI file to same directory as module
                if library_name:
                    lib_dir = output_dir / library_name
                    lib_dir.mkdir(parents=True, exist_ok=True)
                    ui_path = lib_dir / f"{module_name}_ui.py"
                else:
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
