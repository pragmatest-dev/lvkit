"""HTML documentation generation for LabVIEW VIs.

Re-exports ``generate_documents`` so that ``lvpy docs`` can import it
without reaching into ``scripts/``.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from lvpy.docs.html_generator import HTMLDocGenerator
from lvpy.docs.utils import generate_dependency_description
from lvpy.graph import InMemoryVIGraph
from lvpy.models import CaseOperation, SequenceOperation
from lvpy.structure import parse_lvclass, parse_lvlib

# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------


def _collect_library_vis(library_path: Path) -> list[Path]:
    """Collect all VI paths from a .lvlib library."""
    library = parse_lvlib(library_path)
    base_path = library_path.parent

    vi_paths: list[Path] = []
    for member in library.members:
        if member.member_type == "VI":
            vi_path = base_path / member.url
            if vi_path.exists():
                vi_paths.append(vi_path.resolve())

    return vi_paths


def _collect_class_vis(class_path: Path) -> list[Path]:
    """Collect all method VIs from a .lvclass class."""
    lvclass = parse_lvclass(class_path)
    base_path = class_path.parent

    vi_paths: list[Path] = []
    for method in lvclass.methods:
        if method.vi_path:
            vi_path = base_path / method.vi_path
            if vi_path.exists():
                vi_paths.append(vi_path.resolve())
                continue
        # Fallback: look for method VI directly in the class directory
        vi_path = base_path / f"{method.name}.vi"
        if vi_path.exists():
            vi_paths.append(vi_path.resolve())

    return vi_paths


def _collect_directory_vis(dir_path: Path) -> list[Path]:
    """Collect all .vi files recursively from a directory."""
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    vi_paths: list[Path] = []
    for vi_file in dir_path.rglob("*.vi"):
        if vi_file.is_file():
            vi_paths.append(vi_file.resolve())

    return vi_paths


def _collect_icons(graph: InMemoryVIGraph, output_dir: Path) -> dict[str, str]:
    """Collect and copy VI icons to the output directory."""
    icons_dir = output_dir / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    icon_map: dict[str, str] = {}

    for vi_name in graph.list_vis():
        vi_path = graph.get_vi_source_path(vi_name)
        if not vi_path:
            continue

        vi_path = Path(vi_path)

        icon_source = vi_path.parent / f"{vi_path.stem}_ICON.png"
        if not icon_source.exists():
            icon_source = vi_path.parent / f"{vi_path.stem}_icl8.png"

        if icon_source.exists():
            safe_name = vi_name.replace(":", "_").replace("/", "_").replace("\\", "_")
            safe_name = safe_name.replace(" ", "_").replace(".", "_")
            icon_dest = icons_dir / f"{safe_name}.png"
            shutil.copy2(icon_source, icon_dest)
            icon_map[vi_name] = f"../icons/{safe_name}.png"

    return icon_map


def _collect_subvi_names(operations: list) -> list[str]:
    """Recursively collect SubVI names from operations including inner nodes."""
    names: list[str] = []
    for op in operations:
        if "SubVI" in op.labels and op.name:
            names.append(op.name)
        if op.inner_nodes:
            names.extend(_collect_subvi_names(op.inner_nodes))
    return names


def _prepare_vi_documentation_data(
    vi_name: str,
    graph: InMemoryVIGraph,
    poly_groups: dict,
    icon_map: dict[str, str] | None = None,
) -> dict:
    """Prepare all data needed for one VI documentation page."""
    inputs_dc = graph.get_inputs(vi_name)
    outputs_dc = graph.get_outputs(vi_name)
    operations_dc = graph.get_operations(vi_name)
    constants_dc = graph.get_constants(vi_name)
    dataflow_dc = graph.get_wires(vi_name)

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
            "name": inp.name or f"input_{inp.index}",
            "type": inp.python_type(),
            "default_value": default_val,
        })

    indicators = []
    for out in outputs_dc:
        indicators.append({
            "name": out.name or f"output_{out.index}",
            "type": out.python_type(),
        })

    graph_data = {
        "inputs": inputs_dc,
        "outputs": outputs_dc,
        "operations": operations_dc,
        "constants": constants_dc,
        "data_flow": dataflow_dc,
    }

    qualified_deps = set(graph.get_vi_dependencies(vi_name))

    def _extract_subvi_names(ops):
        names = []
        for op in ops:
            if "SubVI" in (op.labels or []) and op.name:
                names.append(op.name)
            names.extend(_extract_subvi_names(op.inner_nodes))
            if isinstance(op, CaseOperation | SequenceOperation):
                for frame in op.frames:
                    names.extend(_extract_subvi_names(frame.operations))
        return names

    op_names = set(_extract_subvi_names(operations_dc))
    for op_name in op_names:
        already_covered = any(
            dep.endswith(f":{op_name}") or dep == op_name
            for dep in qualified_deps
        )
        if not already_covered:
            qualified_deps.add(op_name)
    qualified_deps.discard(vi_name)

    dependencies = {
        dep_name: generate_dependency_description(dep_name, graph)
        for dep_name in sorted(qualified_deps)
    }

    callers = graph.get_vi_dependents(vi_name)

    is_poly = vi_name in poly_groups
    poly_variants = poly_groups.get(vi_name, []) if is_poly else []

    variant_params = []
    if is_poly and poly_variants:
        for variant_name in poly_variants:
            try:
                variant_inputs = graph.get_inputs(variant_name)
                variant_outputs = graph.get_outputs(variant_name)
                variant_params.append({
                    "name": variant_name,
                    "inputs": [
                        {"name": inp.name, "type": inp.python_type()}
                        for inp in variant_inputs
                    ],
                    "outputs": [
                        {"name": out.name, "type": out.python_type()}
                        for out in variant_outputs
                    ],
                })
            except Exception:
                pass

    icon_path = None
    if icon_map and vi_name in icon_map:
        icon_path = str(icon_map[vi_name])

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
        "icon_path": icon_path,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_documents(
    library_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    expand_subvis: bool = True,
) -> str:
    """Generate HTML docs for a LabVIEW library, class, directory, or VI."""
    start_time = time.time()
    library_path_obj = Path(library_path)
    output_dir_obj = Path(output_dir)

    if not library_path_obj.exists():
        raise FileNotFoundError(f"Path not found: {library_path}")

    # Determine input type and collect VI paths
    print("[TIMING] Starting VI discovery...")
    t0 = time.time()
    if library_path_obj.suffix == ".vi":
        doc_type = "vi"
        doc_title = library_path_obj.stem
        vi_paths = [library_path_obj]
    elif library_path_obj.suffix == ".lvlib":
        doc_type = "library"
        doc_title = library_path_obj.stem
        vi_paths = _collect_library_vis(library_path_obj)
    elif library_path_obj.suffix == ".lvclass":
        doc_type = "class"
        doc_title = library_path_obj.stem
        vi_paths = _collect_class_vis(library_path_obj)
    elif library_path_obj.is_dir():
        doc_type = "directory"
        doc_title = library_path_obj.name
        vi_paths = _collect_directory_vis(library_path_obj)
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

    loaded_vis: list[str] = []
    failed_vis: list[str] = []

    for i, vi_path in enumerate(vi_paths, 1):
        print(
            f"[TIMING]   Starting VI {i}/{len(vi_paths)}: "
            f"{vi_path.name}...",
            flush=True,
        )
        vi_start = time.time()
        before_count = len(graph.list_vis())
        try:
            graph.load_vi(
                vi_path,
                expand_subvis=expand_subvis,
                search_paths=search_path_objs or None,
            )
            after_count = len(graph.list_vis())
            new_vis = after_count - before_count
            loaded_vis.append(vi_path.name)
            print(
                f"[TIMING]   Loaded VI {i}/{len(vi_paths)}: {vi_path.name} "
                f"({time.time() - vi_start:.2f}s) - Graph: {before_count} -> "
                f"{after_count} (+{new_vis} new VIs)",
                flush=True,
            )
        except Exception as e:
            failed_vis.append(f"{vi_path.name}: {str(e)}")
            print(
                f"[TIMING]   Failed VI {i}/{len(vi_paths)}: {vi_path.name} - {str(e)}",
                flush=True,
            )

    total_loaded = len(graph.list_vis())
    print(
        f"[TIMING] VI loading complete: {time.time() - t0:.2f}s - "
        f"Loaded {len(loaded_vis)} VIs, expanded to {total_loaded} total"
    )

    if not loaded_vis:
        return "Failed to load any VIs. Errors:\n" + "\n".join(failed_vis)

    # Get polymorphic VI info
    poly_groups = graph.get_polymorphic_groups()
    poly_variant_to_wrapper = graph.get_poly_variant_wrappers()

    # Collect and copy icons
    print("[TIMING] Collecting VI icons...")
    t0 = time.time()
    icon_map = _collect_icons(graph, output_dir_obj)
    print(
        f"[TIMING] Icon collection: {time.time() - t0:.2f}s"
        f" - Found {len(icon_map)} icons"
    )

    # Create HTML generator
    generator = HTMLDocGenerator(output_dir_obj, doc_title, doc_type)
    generator.icon_map = icon_map

    # Generate documentation for each VI
    print(f"[TIMING] Generating HTML pages for {total_loaded} VIs...")
    t0 = time.time()
    all_vis = graph.list_vis()

    generator.all_vis = set(all_vis)

    generated_count = 0

    for i, vi_name in enumerate(all_vis, 1):
        try:
            vi_data = _prepare_vi_documentation_data(
                vi_name, graph, poly_groups, icon_map,
            )
            generator.generate_vi_page(vi_data)
            generated_count += 1
            if i % 50 == 0:
                print(
                    f"[TIMING]   Generated {i}/{total_loaded} pages "
                    f"({time.time() - t0:.2f}s elapsed)"
                )
        except Exception as e:
            failed_vis.append(f"{vi_name}: {str(e)}")
    print(
        f"[TIMING] HTML generation: {time.time() - t0:.2f}s"
        f" - Generated {generated_count} pages"
    )

    # Generate index page - filter out poly variants (only show wrappers)
    print("[TIMING] Generating index page...")
    t0 = time.time()
    vis_for_index = [vi for vi in all_vis if vi not in poly_variant_to_wrapper]
    generator.generate_index_page(vis_for_index)
    print(f"[TIMING] Index generation: {time.time() - t0:.2f}s")

    # Write CSS assets
    print("[TIMING] Writing CSS assets...")
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
