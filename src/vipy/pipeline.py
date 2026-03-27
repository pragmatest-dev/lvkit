"""AST-based Python code generation pipeline for LabVIEW VIs.

This module exposes the core generation logic as a library API.
Scripts and CLI commands delegate to :func:`generate_python`.
"""

from __future__ import annotations

import ast
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vipy.agent.codegen import build_module
from vipy.agent.codegen.ast_utils import to_function_name, to_module_name
from vipy.memory_graph import InMemoryVIGraph
from vipy.vilib_resolver import get_resolver as get_vilib_resolver

# Increase recursion limit for deeply nested VIs
sys.setrecursionlimit(10000)


@dataclass
class GenerationResult:
    """Summary of a generation run."""

    vilib: int = 0
    ast: int = 0
    stub: int = 0
    error: int = 0
    generated: list[tuple[str, Path | None, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utility helpers (also used by generate_docs and other callers)
# ---------------------------------------------------------------------------


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
        meta = graph._vi_metadata.get(vi_name)
        library = meta.library if meta else None
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
    caller_library: str | None = None,
) -> Any:
    """Create an import resolver for a VI.

    Args:
        package_name: Name of the output package (e.g., "get_settings_path")
        output_dir: Root output directory
        vi_paths: Dict mapping fully qualified VI names to their output paths
        graph: Memory graph for library metadata lookup
        vilib_resolver: VILib resolver for vilib membership check
        caller_library: Library subdirectory of the calling VI (e.g., "testcaselvclass")

    Returns:
        Callable that takes a SubVI name and returns the correct import statement
    """
    def resolver(subvi_name: str) -> str:
        func_name = to_function_name(subvi_name)

        # Resolve to qualified name for library lookup
        qualified = subvi_name
        if graph:
            resolved_name = graph.resolve_vi_name(subvi_name)
            if resolved_name:
                qualified = resolved_name

        # Look up the dependency's path
        if qualified in vi_paths:
            dep_path = vi_paths[qualified]
        elif subvi_name in vi_paths:
            dep_path = vi_paths[subvi_name]
        else:
            dep_path, _ = get_output_path(
                output_dir, qualified, create_dirs=False,
                graph=graph, vilib_resolver=vilib_resolver,
            )

        dep_module = dep_path.stem
        dep_library = to_library_name(
            qualified, graph=graph, vilib_resolver=vilib_resolver,
        )

        # Build relative import: go up from caller, down to dependency
        up = ".." if caller_library else "."
        down = f"{dep_library}.{dep_module}" if dep_library else dep_module
        return f"from {up}{down} import {func_name}"

    return resolver


# ---------------------------------------------------------------------------
# Polymorphic module generation
# ---------------------------------------------------------------------------


def _generate_polymorphic_module(
    wrapper_name: str,
    variants: list[str],
    graph: InMemoryVIGraph,
    vilib_resolver: Any,
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
    variant_funcs: list[str] = []
    variant_result_classes: list[str] = []  # Track result class names
    all_inputs: dict[int, Any] = {}
    all_outputs: dict[int, Any] = {}

    for variant_name in variants:
        vi_context = graph.get_vi_context(variant_name)
        func_name = to_function_name(variant_name)
        variant_funcs.append(func_name)

        try:
            variant_lib = to_library_name(
                variant_name, graph=graph, vilib_resolver=vilib_resolver,
            )
            import_resolver = create_import_resolver(
                    package_name, output_dir, vi_paths,
                    graph=graph, vilib_resolver=vilib_resolver,
                    caller_library=variant_lib,
                )
            code = build_module(
                vi_context, vi_name=variant_name,
                import_resolver=import_resolver, graph=graph,
            )
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
            for inp in vi_context.inputs:
                idx = getattr(inp, "slot_index", getattr(inp, "index", len(all_inputs)))
                if idx not in all_inputs:
                    all_inputs[idx] = inp
            for out in vi_context.outputs:
                idx = getattr(
                    out, "slot_index",
                    getattr(out, "index", len(all_outputs)),
                )
                if idx not in all_outputs:
                    all_outputs[idx] = out

        except Exception as e:
            # Comment out all lines of the error message
            error_msg = str(e).replace('\n', '\n# ')
            lines.append(f"# ERROR generating {variant_name}: {error_msg}")

    # Generate wrapper function
    wrapper_func = to_function_name(wrapper_name)

    # Build parameter list from union of variant inputs
    params: list[str] = []
    for idx in sorted(all_inputs.keys()):
        inp = all_inputs[idx]
        name = getattr(inp, "name", None) or f"arg_{idx}"
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
        other_variants = [
            f for f in variant_funcs
            if f not in array_variants and f not in traditional_variants
        ]

        # Generate type-based dispatch
        if array_variants and (traditional_variants or other_variants):
            # Have both array and non-array variants - dispatch on type
            lines.append(f"    if isinstance({first_param}, (list, tuple)):")
            lines.append(f"        return {array_variants[0]}({call_args})")
            lines.append("    else:")
            if traditional_variants:
                fallback = traditional_variants[0]
            elif other_variants:
                fallback = other_variants[0]
            else:
                fallback = variant_funcs[0]
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


# ---------------------------------------------------------------------------
# lvclass helpers
# ---------------------------------------------------------------------------


def _resolve_vi_path(cls_dir: Path, relative_path: str) -> Path | None:
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


def _find_parent_class(child_path: Path, parent_name: str) -> Path | None:
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


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


def generate_python(
    input_path: Path | str,
    output_dir: Path | str,
    search_paths: list[Path] | None = None,
    expand_subvis: bool = True,
) -> dict:
    """Generate Python from VI files.

    Args:
        input_path: Path to .vi, .lvlib, .lvclass, or directory
        output_dir: Output directory for generated Python
        search_paths: Additional paths for SubVI resolution
        expand_subvis: If True, recursively load SubVIs

    Returns:
        Summary dict with keys: vilib, ast, stub, error counts
    """
    input_path = Path(input_path)
    output_dir_root = Path(output_dir)

    # Create VI-named subfolder within output directory
    vi_folder_name = input_path.stem
    vi_folder_name = re.sub(r"[^\w]", "_", vi_folder_name).lower()
    vi_folder_name = re.sub(r"_+", "_", vi_folder_name).strip("_")

    output_dir_resolved = output_dir_root / vi_folder_name

    # Clean output directory
    if output_dir_resolved.exists():
        shutil.rmtree(output_dir_resolved)
    output_dir_resolved.mkdir(parents=True, exist_ok=True)

    print(f"Loading: {input_path}")

    graph = InMemoryVIGraph()
    search_path_list = list(search_paths) if search_paths else []

    # Detect input type and load appropriately
    if input_path.suffix.lower() == ".lvclass":
        graph.load_lvclass(str(input_path), search_paths=search_path_list)
    elif input_path.suffix.lower() == ".lvlib":
        graph.load_lvlib(str(input_path), search_paths=search_path_list)
    elif input_path.is_dir():
        graph.load_directory(str(input_path), search_paths=search_path_list)
    else:
        graph.load_vi(str(input_path), search_paths=search_path_list)

    order = graph.get_conversion_order()
    print(f"\nConversion order ({len(order)} VIs):")

    vilib_resolver = get_vilib_resolver()

    # Identify polymorphic groups (from VI metadata, not heuristics)
    poly_groups = graph.get_polymorphic_groups()

    # Structural fallback: detect polymorphic groups by finding VIs whose
    # names follow the pattern "Base (Variant)". Group them under the base
    # name even if the wrapper VI isn't in the conversion order.
    from collections import defaultdict
    _variant_candidates: dict[str, list[str]] = defaultdict(list)
    for vi_name in order:
        if vi_name in poly_groups:
            continue
        name_no_ext = vi_name.replace(".vi", "").replace(".VI", "")
        if "(" in name_no_ext and ")" in name_no_ext:
            # Extract base: "Filter (Array)__sfx" → "Filter__sfx"
            paren_start = name_no_ext.index("(")
            paren_end = name_no_ext.rindex(")")
            base_part = name_no_ext[:paren_start].rstrip()
            suffix_part = name_no_ext[paren_end + 1:]
            wrapper_name = base_part + suffix_part + ".vi"
            _variant_candidates[wrapper_name].append(vi_name)
    for wrapper_name, variants in _variant_candidates.items():
        if len(variants) >= 2 and wrapper_name not in poly_groups:
            poly_groups[wrapper_name] = variants

    poly_variants: set[str] = set()
    for variants in poly_groups.values():
        poly_variants.update(variants)

    # Pre-compute all output paths for import resolution
    vi_paths: dict[str, Path] = {}
    for vi_name in order:
        path, _ = get_output_path(
            output_dir_resolved, vi_name, create_dirs=False,
            graph=graph, vilib_resolver=vilib_resolver,
        )
        vi_paths[vi_name] = path

    # Redirect polymorphic variant paths to their wrapper's path
    for wrapper_name, variants in poly_groups.items():
        wrapper_path = vi_paths.get(wrapper_name)
        if wrapper_path:
            for variant_name in variants:
                vi_paths[variant_name] = wrapper_path

    generated: list[tuple[str, Path | None, str]] = []

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
            output_dir_resolved, vi_name, graph=graph, vilib_resolver=vilib_resolver,
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
                code = _generate_polymorphic_module(
                    vi_name, variants, graph, vilib_resolver,
                    vi_folder_name, output_dir_resolved, vi_paths,
                )
                ast.parse(code)  # Validate syntax
                output_path.write_text(code)
                print(
                    f"         -> polymorphic: {output_path.name}"
                    f" ({len(variants)} variants)"
                )
                generated.append((vi_name, output_path, "ast"))
            except SyntaxError as e:
                error_path = output_dir_resolved / f"{module_name}.error.py"
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
                caller_lib = to_library_name(
                    vi_name, graph=graph, vilib_resolver=vilib_resolver,
                )
                import_resolver = create_import_resolver(
                    vi_folder_name, output_dir_resolved, vi_paths,
                    graph=graph, vilib_resolver=vilib_resolver,
                    caller_library=caller_lib,
                )
                code = build_module(
                    vi_context, vi_name,
                    import_resolver=import_resolver, graph=graph,
                )

                # Validate syntax
                ast.parse(code)
                output_path.write_text(code)
                print(f"         -> AST: {output_path.name}")
                generated.append((vi_name, output_path, "ast"))

            except SyntaxError as e:
                error_path = output_dir_resolved / f"{module_name}.error.py"
                fallback = code if "code" in dir() else "# generation failed"
                error_path.write_text(
                    f"# SYNTAX ERROR: {e}\n\n{fallback}"
                )
                print(f"         -> SYNTAX ERROR: {error_path.name}")
                generated.append((vi_name, error_path, "error"))

            except Exception as e:
                error_path = output_dir_resolved / f"{module_name}.error.py"
                error_path.write_text(f"# ERROR: {e}")
                print(f"         -> FAILED: {e}")
                generated.append((vi_name, error_path, "error"))

    # Generate polymorphic wrappers for structurally-detected groups
    # whose wrapper VI wasn't in the conversion order
    for wrapper_name, variants in poly_groups.items():
        wrapper_path = vi_paths.get(wrapper_name)
        if not wrapper_path:
            wrapper_path, _ = get_output_path(
                output_dir_resolved, wrapper_name, graph=graph,
                vilib_resolver=vilib_resolver,
            )
            vi_paths[wrapper_name] = wrapper_path
        if not wrapper_path.exists():
            try:
                code = _generate_polymorphic_module(
                    wrapper_name, variants, graph, vilib_resolver,
                    vi_folder_name, output_dir_resolved, vi_paths,
                )
                ast.parse(code)
                wrapper_path.write_text(code)
                generated.append((wrapper_name, wrapper_path, "ast"))
            except Exception as e:
                import traceback
                print(f"  [poly wrapper] FAILED for {wrapper_name}: {e}")
                traceback.print_exc()

    # Generate minimal __init__.py (just makes it a package)
    init_path = output_dir_resolved / "__init__.py"
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
            if ctx.inputs or ctx.outputs or ctx.operations:
                method_contexts[method.name] = ctx

        # Build class wrapper with context lookup for SubVI resolution
        class_lib = to_library_name(
            lvclass.name,
            graph=graph, vilib_resolver=vilib_resolver,
        )
        import_resolver = create_import_resolver(
                vi_folder_name, output_dir_resolved, vi_paths,
                graph=graph, vilib_resolver=vilib_resolver,
                caller_library=class_lib,
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
        class_path = output_dir_resolved / class_filename
        class_path.write_text(class_code)
        print(f"\nGenerated class wrapper: {class_filename}")

    print(f"\nOutput: {output_dir_resolved}")

    vilib_count = sum(1 for _, _, s in generated if s == "vilib")
    ast_count = sum(1 for _, _, s in generated if s == "ast")
    stub_count = sum(1 for _, _, s in generated if s == "stub")
    error_count = sum(1 for _, _, s in generated if s == "error")

    print(f"  vilib: {vilib_count}")
    print(f"  ast:   {ast_count}")
    print(f"  stub:  {stub_count}")
    print(f"  error: {error_count}")

    # Save terminal observations for incremental collection
    from vipy.terminal_collector import get_collector
    collector = get_collector()
    if collector.data.get("observations"):
        collector.save()
        obs_count = len(collector.data["observations"])
        print(f"\nTerminal observations collected for {obs_count} VI(s)")
        print(f"  Saved to: {collector.pending_file}")

    return {
        "vilib": vilib_count,
        "ast": ast_count,
        "stub": stub_count,
        "error": error_count,
        "generated": generated,
        "output_dir": output_dir_resolved,
        "graph": graph,
        "vilib_resolver": vilib_resolver,
        "vi_paths": vi_paths,
        "vi_folder_name": vi_folder_name,
    }
