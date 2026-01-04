#!/usr/bin/env python3
"""Deterministic HTML documentation generator for LabVIEW VIs.

Usage:
    python scripts/generate_docs.py <vi_or_library_path> <output_dir> [--search-path PATH ...]
"""
import sys
import argparse
from pathlib import Path

# Add src to path if running as script
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.memory_graph import InMemoryVIGraph
from vipy.structure import parse_lvlib, parse_lvclass
from vipy.docs.html_generator import HTMLDocGenerator
from vipy.docs.utils import generate_dependency_description


def collect_library_vis(library_path: Path) -> list[Path]:
    """Collect all VI paths from a .lvlib library."""
    library = parse_lvlib(library_path)
    base_path = library_path.parent

    vi_paths = []
    for member in library.members:
        if member.member_type == "VI":
            vi_path = base_path / member.url
            if vi_path.exists():
                vi_paths.append(vi_path.resolve())

    return vi_paths


def collect_class_vis(class_path: Path) -> list[Path]:
    """Collect all method VIs from a .lvclass class."""
    lvclass = parse_lvclass(class_path)
    base_path = class_path.parent

    vi_paths = []
    for method in lvclass.methods:
        if method.vi_path:
            vi_path = base_path / method.vi_path
            if vi_path.exists():
                vi_paths.append(vi_path.resolve())

    return vi_paths


def collect_directory_vis(dir_path: Path) -> list[Path]:
    """Collect all .vi files recursively from a directory."""
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    vi_paths = []
    for vi_file in dir_path.rglob("*.vi"):
        if vi_file.is_file():
            vi_paths.append(vi_file.resolve())

    return vi_paths


def collect_subvi_names(operations: list) -> list[str]:
    """Recursively collect SubVI names from operations including inner nodes.

    Args:
        operations: List of Operation dataclasses
    """
    names = []
    for op in operations:
        if "SubVI" in op.labels and op.name:
            names.append(op.name)
        if op.inner_nodes:
            names.extend(collect_subvi_names(op.inner_nodes))
    return names


def build_cross_references(graph: InMemoryVIGraph) -> dict:
    """Build caller/callee cross-reference maps."""
    callers: dict[str, list[str]] = {}
    callees: dict[str, list[str]] = {}

    for vi_name in graph.list_vis():
        callers[vi_name] = []
        callees[vi_name] = []

    for vi_name in graph.list_vis():
        try:
            vi_context = graph.get_vi_context(vi_name)
            subvi_names = collect_subvi_names(vi_context.get("operations", []))

            for subvi_name in subvi_names:
                if subvi_name not in callees[vi_name]:
                    callees[vi_name].append(subvi_name)

                if subvi_name not in callers:
                    callers[subvi_name] = []
                if vi_name not in callers[subvi_name]:
                    callers[subvi_name].append(vi_name)
        except Exception:
            continue

    return {"callers": callers, "callees": callees}


def prepare_vi_documentation_data(
    vi_name: str, graph: InMemoryVIGraph, cross_refs: dict, poly_groups: dict
) -> dict:
    """Prepare all data needed for one VI documentation page.

    Args:
        vi_name: Name of the VI
        graph: InMemoryVIGraph containing the VI
        cross_refs: Cross-reference dictionary from build_cross_references
        poly_groups: Polymorphic groups from graph.get_polymorphic_groups()
    """
    from vipy.graph_types import FPTerminalNode, Constant, Operation, Wire

    vi_context = graph.get_vi_context(vi_name)

    # Get dataclasses (not dicts)
    inputs_dc = graph.get_inputs(vi_name)
    outputs_dc = graph.get_outputs(vi_name)
    operations_dc = graph.get_operations(vi_name)
    constants_dc = graph.get_constants(vi_name)
    dataflow_dc = graph.get_wires(vi_name)

    # Extract controls with enum formatting
    controls = []
    for inp in inputs_dc:
        default_val = inp.default_value
        if default_val is not None and inp.lv_type and inp.lv_type.values:
            try:
                int_value = int(default_val)
                for name, enum_val in inp.lv_type.values.items():
                    if enum_val.value == int_value:
                        default_val = name
                        break
            except (ValueError, TypeError, AttributeError):
                pass

        controls.append({
            "name": inp.name or f"input_{inp.slot_index}",
            "type": inp.type or "Any",
            "default_value": default_val,
        })

    # Extract indicators
    indicators = []
    for out in outputs_dc:
        indicators.append({
            "name": out.name or f"output_{out.slot_index}",
            "type": out.type or "Any",
        })

    # Build graph dict with dataclasses (NOT dicts)
    graph_data = {
        "inputs": inputs_dc,
        "outputs": outputs_dc,
        "operations": operations_dc,
        "constants": constants_dc,
        "data_flow": dataflow_dc,
    }

    # Extract dependencies
    subvi_names = collect_subvi_names(vi_context.get("operations", []))
    dependencies = {
        name: generate_dependency_description(name, graph)
        for name in dict.fromkeys(subvi_names)
    }

    callers = cross_refs["callers"].get(vi_name, [])

    # Check if this is a polymorphic wrapper VI
    is_poly = vi_name in poly_groups
    poly_variants = poly_groups.get(vi_name, []) if is_poly else []

    # If polymorphic, gather variant info
    variant_params = []
    if is_poly and poly_variants:
        for variant_name in poly_variants:
            try:
                variant_inputs = graph.get_inputs(variant_name)
                variant_outputs = graph.get_outputs(variant_name)
                variant_params.append({
                    "name": variant_name,
                    "inputs": [{"name": inp.name, "type": inp.type} for inp in variant_inputs],
                    "outputs": [{"name": out.name, "type": out.type} for out in variant_outputs],
                })
            except Exception:
                pass  # Skip variants that can't be loaded

    return {
        "vi_name": vi_name,
        "controls": controls,
        "indicators": indicators,
        "graph": graph_data,
        "dependencies": dependencies,
        "callers": callers,
        "is_polymorphic": is_poly,
        "poly_variants": poly_variants,
        "variant_params": variant_params,
    }


def generate_documents(
    library_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    expand_subvis: bool = True,
) -> str:
    """Generate HTML documentation for a LabVIEW library, class, directory, or single VI."""
    import time

    start_time = time.time()
    library_path_obj = Path(library_path)
    output_dir_obj = Path(output_dir)

    if not library_path_obj.exists():
        raise FileNotFoundError(f"Path not found: {library_path}")

    # Determine input type and collect VI paths
    print(f"[TIMING] Starting VI discovery...")
    t0 = time.time()
    if library_path_obj.suffix == ".vi":
        doc_type = "vi"
        doc_title = library_path_obj.stem
        vi_paths = [library_path_obj]
    elif library_path_obj.suffix == ".lvlib":
        doc_type = "library"
        doc_title = library_path_obj.stem
        vi_paths = collect_library_vis(library_path_obj)
    elif library_path_obj.suffix == ".lvclass":
        doc_type = "class"
        doc_title = library_path_obj.stem
        vi_paths = collect_class_vis(library_path_obj)
    elif library_path_obj.is_dir():
        doc_type = "directory"
        doc_title = library_path_obj.name
        vi_paths = collect_directory_vis(library_path_obj)
    else:
        raise ValueError(
            f"Unsupported input type: {library_path}. "
            "Expected .lvlib, .lvclass, .vi, or directory"
        )
    print(f"[TIMING] VI discovery: {time.time() - t0:.2f}s - Found {len(vi_paths)} VIs")

    if not vi_paths:
        return f"No VIs found in {library_path}"

    # Load all VIs into graph
    expand_msg = "expand_subvis=True" if expand_subvis else "expand_subvis=False"
    print(f"[TIMING] Starting VI loading ({expand_msg})...")
    t0 = time.time()
    graph = InMemoryVIGraph()
    search_path_objs = [Path(p) for p in (search_paths or [])]

    loaded_vis = []
    failed_vis = []

    for i, vi_path in enumerate(vi_paths, 1):
        print(f"[TIMING]   Starting VI {i}/{len(vi_paths)}: {vi_path.name}...", flush=True)
        vi_start = time.time()
        before_count = len(graph.list_vis())
        try:
            graph.load_vi(
                vi_path, expand_subvis=expand_subvis, search_paths=search_path_objs or None
            )
            after_count = len(graph.list_vis())
            new_vis = after_count - before_count
            loaded_vis.append(vi_path.name)
            print(f"[TIMING]   Loaded VI {i}/{len(vi_paths)}: {vi_path.name} ({time.time() - vi_start:.2f}s) - Graph: {before_count} → {after_count} (+{new_vis} new VIs)", flush=True)
        except Exception as e:
            failed_vis.append(f"{vi_path.name}: {str(e)}")
            print(f"[TIMING]   Failed VI {i}/{len(vi_paths)}: {vi_path.name} - {str(e)}", flush=True)

    total_loaded = len(graph.list_vis())
    print(f"[TIMING] VI loading complete: {time.time() - t0:.2f}s - Loaded {len(loaded_vis)} VIs, expanded to {total_loaded} total")

    if not loaded_vis:
        return f"Failed to load any VIs. Errors:\n" + "\n".join(failed_vis)

    # Build cross-references
    print(f"[TIMING] Building cross-references...")
    t0 = time.time()
    cross_refs = build_cross_references(graph)
    print(f"[TIMING] Cross-reference building: {time.time() - t0:.2f}s")

    # Get polymorphic VI info
    poly_groups = graph.get_polymorphic_groups()
    poly_variant_to_wrapper = graph.get_poly_variant_wrappers()

    # Create HTML generator
    generator = HTMLDocGenerator(output_dir_obj, doc_title, doc_type)

    # Generate documentation for each VI
    print(f"[TIMING] Generating HTML pages for {total_loaded} VIs...")
    t0 = time.time()
    all_vis = graph.list_vis()

    # Pre-populate all_vis set so dependency links work correctly
    generator.all_vis = set(all_vis)

    generated_count = 0

    for i, vi_name in enumerate(all_vis, 1):
        try:
            vi_data = prepare_vi_documentation_data(vi_name, graph, cross_refs, poly_groups)
            generator.generate_vi_page(vi_data)
            generated_count += 1
            if i % 50 == 0:
                print(f"[TIMING]   Generated {i}/{total_loaded} pages ({time.time() - t0:.2f}s elapsed)")
        except Exception as e:
            failed_vis.append(f"{vi_name}: {str(e)}")
    print(f"[TIMING] HTML generation: {time.time() - t0:.2f}s - Generated {generated_count} pages")

    # Generate index page - filter out poly variants (only show wrappers)
    print(f"[TIMING] Generating index page...")
    t0 = time.time()
    vis_for_index = [vi for vi in all_vis if vi not in poly_variant_to_wrapper]
    generator.generate_index_page(vis_for_index)
    print(f"[TIMING] Index generation: {time.time() - t0:.2f}s")

    # Write CSS assets
    print(f"[TIMING] Writing CSS assets...")
    t0 = time.time()
    generator.write_assets()
    print(f"[TIMING] CSS writing: {time.time() - t0:.2f}s")

    print(f"[TIMING] Total time: {time.time() - start_time:.2f}s")

    # Build summary message
    summary_parts = [
        f"Generated documentation for {doc_title} ({doc_type})",
        f"Output directory: {output_dir_obj.resolve()}",
        f"Total VIs documented: {generated_count}",
        f"Index page: {output_dir_obj / 'index.html'}",
    ]

    if failed_vis:
        summary_parts.append(f"\nWarnings ({len(failed_vis)} VIs skipped):")
        summary_parts.extend(f"  - {err}" for err in failed_vis[:10])
        if len(failed_vis) > 10:
            summary_parts.append(f"  ... and {len(failed_vis) - 10} more")

    return "\n".join(summary_parts)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate HTML documentation for LabVIEW VIs")
    parser.add_argument("library_path", help="Path to .lvlib, .lvclass, .vi file, or directory")
    parser.add_argument("output_dir", help="Output directory for HTML files")
    parser.add_argument("--search-path", action="append", dest="search_paths", help="Search path for dependencies")
    parser.add_argument("--no-expand", action="store_true", help="Don't expand SubVI dependencies")

    args = parser.parse_args()

    try:
        result = generate_documents(
            library_path=args.library_path,
            output_dir=args.output_dir,
            search_paths=args.search_paths,
            expand_subvis=not args.no_expand,
        )
        print("\n" + result)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
