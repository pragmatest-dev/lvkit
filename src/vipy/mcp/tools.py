"""VI analysis tools for MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..memory_graph import InMemoryVIGraph
from ..structure import parse_lvclass, parse_lvlib
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .html_generator import HTMLDocGenerator
from .schemas import (
    CodeGenResult,
    ControlSchema,
    GeneratedFileSchema,
    GraphEdgeSchema,
    GraphNodeSchema,
    IndicatorSchema,
    VIAnalysisResult,
)


def analyze_vi(
    vi_path: str, search_paths: list[str] | None = None, expand_subvis: bool = True
) -> VIAnalysisResult:
    """Analyze a VI and return structured data.

    Args:
        vi_path: Path to VI file (.vi) or block diagram XML (*_BDHb.xml)
        search_paths: Optional list of search paths for dependencies
        expand_subvis: If True, recursively load all SubVI dependencies (slower but complete).
                      If False, only load this VI (faster but limited cross-references).

    Returns:
        VIAnalysisResult with complete VI structure
    """
    # Load VI with optional dependency expansion
    graph = InMemoryVIGraph()
    search_path_objs = [Path(p) for p in (search_paths or [])]

    vi_path_obj = Path(vi_path)
    if not vi_path_obj.exists():
        raise FileNotFoundError(f"VI file not found: {vi_path}")

    graph.load_vi(vi_path_obj, expand_subvis=expand_subvis, search_paths=search_path_objs or None)

    # Get main VI name - resolve from path
    if vi_path.endswith("_BDHb.xml"):
        vi_name = Path(vi_path).name.replace("_BDHb.xml", ".vi")
    else:
        vi_name = Path(vi_path).name

    # Resolve qualified name if needed
    all_vis = graph.list_vis()
    if vi_name not in all_vis:
        # Try to find by matching filename
        for v in all_vis:
            if v.endswith(vi_name) or v.endswith(":" + vi_name):
                vi_name = v
                break

    # Get VI context
    vi_context = graph.get_vi_context(vi_name)

    # Extract controls with descriptions
    controls = []
    for inp in vi_context.get("inputs", []):
        controls.append(
            ControlSchema(
                name=inp.name or f"input_{inp.slot_index}",
                type=inp.type or "Any",
                default_value=inp.default_value,
                description=_infer_description(inp.name, inp.type, "input"),
                slot_index=inp.slot_index or 0,
            )
        )

    # Extract indicators with descriptions
    indicators = []
    for out in vi_context.get("outputs", []):
        indicators.append(
            IndicatorSchema(
                name=out.name or f"output_{out.slot_index}",
                type=out.type or "Any",
                description=_infer_description(out.name, out.type, "output"),
                slot_index=out.slot_index or 0,
            )
        )

    # Build graph structure
    graph_data = build_graph_structure(vi_name, graph)

    # Generate dependency descriptions
    dependencies = {}
    for op in vi_context.get("operations", []):
        if "SubVI" in op.labels and op.name:
            dep_name = op.name
            if dep_name not in dependencies:  # Avoid duplicates
                dependencies[dep_name] = generate_dependency_description(dep_name, graph)

    # Get execution order
    try:
        execution_order = graph.get_operation_order(vi_name)
    except Exception:
        # If execution order fails, just use empty list
        execution_order = []

    # Generate summary
    summary = _generate_vi_summary(vi_name, controls, indicators, dependencies)

    return VIAnalysisResult(
        vi_name=vi_name,
        summary=summary,
        controls=controls,
        indicators=indicators,
        graph=graph_data,
        dependencies=dependencies,
        execution_order=execution_order,
    )


def generate_dependency_description(subvi_name: str, graph: InMemoryVIGraph) -> str:
    """Generate 1-sentence description of what a SubVI does.

    Priority:
    1. vilib_resolver for known VIs
    2. Infer from VI name
    3. Infer from VI context (inputs/outputs)

    Args:
        subvi_name: Name of the SubVI
        graph: InMemoryVIGraph containing the VI

    Returns:
        One-sentence description
    """
    # First check vilib resolver
    try:
        resolver = get_vilib_resolver()
        vi = resolver.resolve_by_name(subvi_name)
        if vi and vi.description:
            return vi.description
    except Exception:
        pass

    # For stub VIs, infer from name
    if graph.is_stub_vi(subvi_name):
        return _infer_from_name(subvi_name)

    # For loaded VIs, try to infer from context
    try:
        vi_context = graph.get_vi_context(subvi_name)
        return _infer_from_context(subvi_name, vi_context)
    except Exception:
        return _infer_from_name(subvi_name)


def build_graph_structure(vi_name: str, graph: InMemoryVIGraph) -> dict[str, Any]:
    """Build graph nodes and edges for visualization.

    Args:
        vi_name: Name of the VI
        graph: InMemoryVIGraph containing the VI

    Returns:
        Dictionary with "nodes" and "edges" lists
    """
    nodes = []
    edges = []

    # Get VI context
    vi_context = graph.get_vi_context(vi_name)

    # Add input nodes (controls)
    for inp in vi_context.get("inputs", []):
        nodes.append(
            GraphNodeSchema(
                id=inp.id,
                label=f"Control: {inp.name or 'input'}",
                type="control",
                name=inp.name,
                lv_type=inp.type,  # Use string field
            ).model_dump()
        )

    # Add output nodes (indicators)
    for out in vi_context.get("outputs", []):
        nodes.append(
            GraphNodeSchema(
                id=out.id,
                label=f"Indicator: {out.name or 'output'}",
                type="indicator",
                name=out.name,
                lv_type=out.type,  # Use string field
            ).model_dump()
        )

    # Add operation nodes
    for op in vi_context.get("operations", []):
        if "SubVI" in op.labels:
            label = f"SubVI: {op.name or 'Unknown'}"
            node_type = "subvi"
        elif "Primitive" in op.labels:
            prim_name = op.name or f"prim_{op.primResID}"
            label = f"Primitive: {prim_name}"
            node_type = "primitive"
        else:
            label = f"Operation: {op.name or op.id[:8]}"
            node_type = "operation"

        nodes.append(
            GraphNodeSchema(
                id=op.id,
                label=label,
                type=node_type,
                name=op.name,
                prim_id=op.primResID,
            ).model_dump()
        )

    # Add constant nodes
    for const in vi_context.get("constants", []):
        # Format enum display if this is an enum constant
        if const.lv_type and const.lv_type.values:
            # lv_type.values is {name: EnumValue(value=N, ...)}
            member_name = None
            try:
                int_value = int(const.value)
                for name, enum_val in const.lv_type.values.items():
                    if enum_val.value == int_value:
                        member_name = name
                        break
            except (ValueError, TypeError, AttributeError):
                pass
            value_str = member_name if member_name else str(const.value)
        else:
            value_str = str(const.value) if const.value is not None else "None"

        # Truncate long values
        if len(value_str) > 30:
            value_str = value_str[:27] + "..."

        nodes.append(
            GraphNodeSchema(
                id=const.id,
                label=f"Constant: {value_str}",
                type="constant",
                name=value_str,
            ).model_dump()
        )

    # Build set of all node IDs for edge validation
    node_ids = {str(node["id"]) for node in nodes}

    # Add edges (wires) - use parent node IDs, falling back to terminal IDs for constants
    for wire in vi_context.get("data_flow", []):
        # For the source node:
        # - Use from_parent_id if it's a valid node
        # - Otherwise use from_terminal_id (for constants, the terminal IS the node)
        from_id = wire.from_parent_id
        if str(from_id) not in node_ids and wire.from_terminal_id:
            from_id = wire.from_terminal_id

        # For the target node:
        # - Use to_parent_id if it's a valid node
        # - Otherwise use to_terminal_id
        to_id = wire.to_parent_id
        if str(to_id) not in node_ids and wire.to_terminal_id:
            to_id = wire.to_terminal_id

        # Build human-readable labels
        from_label = _get_node_label(wire.from_parent_id, wire.from_parent_name, wire.from_parent_labels)
        to_label = _get_node_label(wire.to_parent_id, wire.to_parent_name, wire.to_parent_labels)

        edges.append(
            GraphEdgeSchema(
                from_node=from_id,
                to_node=to_id,
                from_label=from_label,
                to_label=to_label,
            ).model_dump()
        )

    return {
        "nodes": nodes,
        "edges": edges,
    }


def _infer_description(name: str | None, type_str: str | None, direction: str) -> str:
    """Infer description from name and type."""
    if not name:
        return f"{direction.capitalize()} parameter"
    type_part = f" ({type_str})" if type_str and type_str != "Any" else ""
    return f"{name}{type_part}"


def _generate_vi_summary(
    vi_name: str,
    controls: list[ControlSchema],
    indicators: list[IndicatorSchema],
    dependencies: dict[str, str],
) -> str:
    """Generate brief summary of VI."""
    parts = []
    if controls:
        parts.append(f"takes {len(controls)} input(s)")
    if indicators:
        parts.append(f"returns {len(indicators)} output(s)")
    if dependencies:
        parts.append(f"calls {len(dependencies)} SubVI(s)")

    if parts:
        return f"VI '{vi_name}' - {', '.join(parts)}"
    return f"VI '{vi_name}'"


def _infer_from_name(vi_name: str) -> str:
    """Infer description from VI name."""
    # Extract base name without path/extension
    name = Path(vi_name).stem
    if ":" in name:  # Handle qualified names
        name = name.split(":")[-1]

    name_lower = name.lower()

    # Common patterns
    if "get" in name_lower and "system" in name_lower and "directory" in name_lower:
        return "Retrieves OS-specific system directory paths"
    elif "build" in name_lower and "path" in name_lower:
        return "Constructs file path by combining components"
    elif "strip" in name_lower and "path" in name_lower:
        return "Extracts directory or filename from path"
    elif name_lower.startswith("get "):
        return f"Retrieves {name[4:].lower()} information"
    elif name_lower.startswith("set "):
        return f"Sets {name[4:].lower()} value"
    elif name_lower.startswith("build "):
        return f"Constructs {name[6:].lower()}"
    elif name_lower.startswith("create "):
        return f"Creates {name[7:].lower()}"
    elif "error" in name_lower:
        return f"Handles error {name.lower()}"

    return f"Performs {name.lower()} operation"


def _infer_from_context(vi_name: str, vi_context: dict[str, Any]) -> str:
    """Infer description from VI context."""
    name = Path(vi_name).stem
    if ":" in name:
        name = name.split(":")[-1]

    inputs = vi_context.get("inputs", [])
    outputs = vi_context.get("outputs", [])

    if outputs and not inputs:
        return f"Generates {name.lower()} data"
    elif inputs and not outputs:
        return f"Processes {name.lower()} data"
    elif not inputs and not outputs:
        return f"Performs {name.lower()} operation (no I/O)"

    return f"Performs {name.lower()} operation"


def _get_node_label(
    parent_id: str | None, parent_name: str | None, parent_labels: list[str] | None
) -> str | None:
    """Build human-readable label for wire endpoint."""
    if not parent_id:
        return None

    # Use parent name if available
    if parent_name:
        # For SubVIs, just use the name
        if parent_labels and "SubVI" in parent_labels:
            return parent_name
        return parent_name

    # For primitives, use label type
    if parent_labels:
        if "Primitive" in parent_labels:
            return "Primitive"
        if "Constant" in parent_labels:
            return "Constant"

    return parent_id[:8]  # Truncate ID


# ========== Document Library Helper Functions ==========


def _collect_library_vis(library_path: Path) -> list[Path]:
    """Collect all VI paths from a .lvlib library.

    Args:
        library_path: Path to .lvlib file

    Returns:
        List of absolute paths to VIs in the library
    """
    library = parse_lvlib(library_path)
    base_path = library_path.parent

    vi_paths = []
    for member in library.members:
        if member.member_type == "VI":
            # Resolve relative path from library
            vi_path = base_path / member.url
            if vi_path.exists():
                vi_paths.append(vi_path.resolve())

    return vi_paths


def _collect_class_vis(class_path: Path) -> list[Path]:
    """Collect all method VIs from a .lvclass class.

    Args:
        class_path: Path to .lvclass file

    Returns:
        List of absolute paths to method VIs in the class
    """
    lvclass = parse_lvclass(class_path)
    base_path = class_path.parent

    vi_paths = []
    for method in lvclass.methods:
        # Methods have vi_path attribute
        if method.vi_path:
            vi_path = base_path / method.vi_path
            if vi_path.exists():
                vi_paths.append(vi_path.resolve())

    return vi_paths


def _collect_directory_vis(dir_path: Path) -> list[Path]:
    """Collect all .vi files recursively from a directory.

    Args:
        dir_path: Path to directory

    Returns:
        List of absolute paths to all .vi files found
    """
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    vi_paths = []
    for vi_file in dir_path.rglob("*.vi"):
        if vi_file.is_file():
            vi_paths.append(vi_file.resolve())

    return vi_paths


def _build_cross_references(graph: InMemoryVIGraph) -> dict[str, Any]:
    """Build caller/callee cross-reference maps.

    Args:
        graph: InMemoryVIGraph with all VIs loaded

    Returns:
        Dictionary with "callers" and "callees" maps
    """
    callers: dict[str, list[str]] = {}
    callees: dict[str, list[str]] = {}

    # Initialize maps for all VIs
    for vi_name in graph.list_vis():
        callers[vi_name] = []
        callees[vi_name] = []

    # Build relationships
    for vi_name in graph.list_vis():
        try:
            vi_context = graph.get_vi_context(vi_name)
            for operation in vi_context.get("operations", []):
                if "SubVI" in operation.labels and operation.name:
                    subvi_name = operation.name

                    # Add to callees (this VI calls subvi)
                    if subvi_name not in callees[vi_name]:
                        callees[vi_name].append(subvi_name)

                    # Add to callers (subvi is called by this VI)
                    if subvi_name not in callers:
                        callers[subvi_name] = []
                    if vi_name not in callers[subvi_name]:
                        callers[subvi_name].append(vi_name)
        except Exception:
            # Skip VIs that can't be analyzed
            continue

    return {
        "callers": callers,
        "callees": callees,
    }


def _prepare_vi_documentation_data(
    vi_name: str, graph: InMemoryVIGraph, cross_refs: dict[str, Any]
) -> dict[str, Any]:
    """Prepare all data needed for one VI documentation page.

    Args:
        vi_name: Name of the VI
        graph: InMemoryVIGraph containing the VI
        cross_refs: Cross-reference dictionary from _build_cross_references

    Returns:
        Dictionary with all VI data for HTML generation
    """
    # Get VI context
    vi_context = graph.get_vi_context(vi_name)

    # Extract controls
    controls = []
    for inp in vi_context.get("inputs", []):
        # Format default value with enum member name if applicable
        default_val = inp.default_value
        if default_val is not None and inp.lv_type and inp.lv_type.values:
            # Enum type - look up member name from {name: EnumValue(value=N, ...)}
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
    for out in vi_context.get("outputs", []):
        indicators.append({
            "name": out.name or f"output_{out.slot_index}",
            "type": out.type or "Any",
        })

    # Build graph structure
    graph_data = build_graph_structure(vi_name, graph)

    # Extract dependencies with descriptions
    dependencies = {}
    for op in vi_context.get("operations", []):
        if "SubVI" in op.labels and op.name:
            dep_name = op.name
            if dep_name not in dependencies:
                dependencies[dep_name] = generate_dependency_description(dep_name, graph)

    # Get callers from cross-references
    callers = cross_refs["callers"].get(vi_name, [])

    return {
        "vi_name": vi_name,
        "controls": controls,
        "indicators": indicators,
        "graph": graph_data,
        "dependencies": dependencies,
        "callers": callers,
    }


def generate_documents(
    library_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    expand_subvis: bool = True,
) -> str:
    """Generate HTML documentation for a LabVIEW library, class, directory, or single VI.

    Args:
        library_path: Path to .lvlib, .lvclass, directory, or .vi file
        output_dir: Output directory for HTML files
        search_paths: Optional list of search paths for dependencies
        expand_subvis: If True, load all SubVI dependencies for complete cross-references (slower).
                      If False, only load VIs in the library/directory (faster).

    Returns:
        Summary message with statistics
    """
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
    cross_refs = _build_cross_references(graph)
    print(f"[TIMING] Cross-reference building: {time.time() - t0:.2f}s")

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
            vi_data = _prepare_vi_documentation_data(vi_name, graph, cross_refs)
            generator.generate_vi_page(vi_data)
            generated_count += 1
            if i % 50 == 0:
                print(f"[TIMING]   Generated {i}/{total_loaded} pages ({time.time() - t0:.2f}s elapsed)")
        except Exception as e:
            failed_vis.append(f"{vi_name}: {str(e)}")
    print(f"[TIMING] HTML generation: {time.time() - t0:.2f}s - Generated {generated_count} pages")

    # Generate index page
    print(f"[TIMING] Generating index page...")
    t0 = time.time()
    generator.generate_index_page(all_vis, cross_refs)
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


# ========== Python Code Generation ==========


def generate_python(
    vi_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    include_code: bool = False,
) -> CodeGenResult:
    """Generate Python code from a LabVIEW VI using AST-based translation.

    Args:
        vi_path: Path to VI file (.vi) or block diagram XML (*_BDHb.xml)
        output_dir: Output directory for generated Python files
        search_paths: Optional list of search paths for dependencies
        include_code: If True, include generated code in response (default: False, read files instead)

    Returns:
        CodeGenResult with generated files, errors, and review needs.
        Files are written to output_dir - agent should read them for review.
    """
    import ast
    import re
    import shutil

    from ..agent.codegen import build_module, MissingDependencyError, CodeGenError
    from ..agent.codegen.ast_utils import to_function_name

    def to_module_name(vi_name: str) -> str:
        """Convert VI name to module name."""
        if ":" in vi_name:
            vi_name = vi_name.split(":")[-1]
        vi_name = vi_name.replace(".vi", "").replace(".VI", "")
        result = vi_name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or "module"

    def to_library_name(vi_name: str) -> str | None:
        """Extract library name from qualified VI name."""
        if ":" not in vi_name:
            return None
        library = vi_name.split(":")[0]
        library = library.replace(".lvlib", "").replace(".lvclass", "")
        result = library.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or None

    def get_output_path(out_dir: Path, vi_name: str, create_dirs: bool = True) -> tuple[Path, str | None]:
        """Get output path and library name for a VI."""
        module_name = to_module_name(vi_name)
        library_name = to_library_name(vi_name)

        if library_name:
            lib_dir = out_dir / library_name
            if create_dirs:
                lib_dir.mkdir(parents=True, exist_ok=True)
                init_path = lib_dir / "__init__.py"
                if not init_path.exists():
                    init_path.write_text(f'"""Package for {library_name} library."""\n')
            return (lib_dir / f"{module_name}.py", library_name)
        else:
            return (out_dir / f"{module_name}.py", None)

    def create_import_resolver(package_name: str, out_dir: Path, vi_paths: dict[str, Path]):
        """Create an import resolver for a VI."""
        def resolver(subvi_name: str) -> str:
            func_name = to_function_name(subvi_name)
            if subvi_name in vi_paths:
                dep_path = vi_paths[subvi_name]
            else:
                dep_path, _ = get_output_path(out_dir, subvi_name, create_dirs=False)

            dep_module = dep_path.stem
            dep_library = to_library_name(subvi_name)

            if dep_library:
                return f"from {package_name}.{dep_library}.{dep_module} import {func_name}"
            else:
                return f"from {package_name}.{dep_module} import {func_name}"
        return resolver

    # Setup
    input_path = Path(vi_path)
    if not input_path.exists():
        return CodeGenResult(
            success=False,
            output_dir=output_dir,
            package_name="",
            errors=[f"VI file not found: {vi_path}"],
        )

    # Create package folder name from VI
    vi_folder_name = input_path.stem
    vi_folder_name = re.sub(r"[^\w]", "_", vi_folder_name).lower()
    vi_folder_name = re.sub(r"_+", "_", vi_folder_name).strip("_")

    output_dir_path = Path(output_dir) / vi_folder_name

    # Clean and create output directory
    if output_dir_path.exists():
        shutil.rmtree(output_dir_path)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    # Load VI
    graph = InMemoryVIGraph()
    search_path_objs = [Path(p) for p in (search_paths or [])]

    try:
        graph.load_vi(input_path, search_paths=search_path_objs or None)
    except Exception as e:
        return CodeGenResult(
            success=False,
            output_dir=str(output_dir_path),
            package_name=vi_folder_name,
            errors=[f"Failed to load VI: {e}"],
        )

    order = graph.get_conversion_order()
    vilib_resolver = get_vilib_resolver()

    # Identify polymorphic groups
    poly_groups = graph.get_polymorphic_groups()
    poly_variants = set()
    for variants in poly_groups.values():
        poly_variants.update(variants)

    # Pre-compute output paths
    vi_paths_map: dict[str, Path] = {}
    for vi_name in order:
        path, _ = get_output_path(output_dir_path, vi_name, create_dirs=False)
        vi_paths_map[vi_name] = path

    # Generate code
    files: list[GeneratedFileSchema] = []
    errors: list[str] = []
    warnings: list[str] = []
    needs_review: list[str] = []
    missing_deps: dict[str, list[str]] = {}

    for vi_name in order:
        # Skip polymorphic variants
        if vi_name in poly_variants:
            continue

        is_stub = graph.is_stub_vi(vi_name)
        has_vilib = vilib_resolver.has_implementation(vi_name)
        has_inline = vilib_resolver.has_inline(vi_name)

        output_path, _ = get_output_path(output_dir_path, vi_name)
        module_name = to_module_name(vi_name)
        relative_path = str(output_path.relative_to(output_dir_path))

        if has_inline:
            # Inlined at call sites - skip
            continue

        if has_vilib:
            # Use vilib implementation
            code = vilib_resolver.get_implementation(vi_name)
            output_path.write_text(code)
            files.append(GeneratedFileSchema(
                path=relative_path,
                vi_name=vi_name,
                status="ok",
                code=code if include_code else None,
                source_type="vilib",
            ))

        elif is_stub:
            # Generate stub
            func_name = to_function_name(vi_name)
            code = f'''"""Stub: {vi_name} - NEEDS IMPLEMENTATION."""
from __future__ import annotations
from typing import Any


def {func_name}(*args, **kwargs) -> Any:
    """TODO: Implement {vi_name}.

    This VI was not found in the search paths. You need to either:
    1. Add the correct search path containing this VI
    2. Provide a vilib implementation in data/vilib/
    3. Implement this function manually
    """
    raise NotImplementedError("{vi_name}")
'''
            output_path.write_text(code)
            files.append(GeneratedFileSchema(
                path=relative_path,
                vi_name=vi_name,
                status="ok",
                code=code if include_code else None,
                source_type="stub",
                error="Missing VI - needs implementation or correct search path",
            ))
            needs_review.append(vi_name)
            if vi_name not in missing_deps:
                missing_deps[vi_name] = []

        elif vi_name in poly_groups:
            # Polymorphic VI - generate wrapper with all variants
            # For now, mark as needing review
            warnings.append(f"Polymorphic VI {vi_name} - complex generation needed")
            needs_review.append(vi_name)

        else:
            # Use AST builder
            vi_context = graph.get_vi_context(vi_name)

            try:
                import_resolver = create_import_resolver(vi_folder_name, output_dir_path, vi_paths_map)
                code = build_module(vi_context, vi_name, graph.get_vi_context, import_resolver)

                # Validate syntax
                ast.parse(code)
                output_path.write_text(code)
                files.append(GeneratedFileSchema(
                    path=relative_path,
                    vi_name=vi_name,
                    status="ok",
                    code=code if include_code else None,
                    source_type="ast",
                ))

            except SyntaxError as e:
                error_path = output_dir_path / f"{module_name}.error.py"
                error_path.write_text(f"# SYNTAX ERROR: {e}\n\n{code}")
                files.append(GeneratedFileSchema(
                    path=str(error_path.relative_to(output_dir_path)),
                    vi_name=vi_name,
                    status="syntax_error",
                    code=code if include_code else None,
                    error=str(e),
                    source_type="ast",
                ))
                errors.append(f"Syntax error in {vi_name}: {e}")
                needs_review.append(vi_name)

            except MissingDependencyError as e:
                files.append(GeneratedFileSchema(
                    path=relative_path,
                    vi_name=vi_name,
                    status="generation_error",
                    error=f"Missing dependency: {e}",
                    source_type="ast",
                ))
                errors.append(f"Missing dependency for {vi_name}: {e}")
                needs_review.append(vi_name)
                # Track which VIs are missing
                missing_deps.setdefault(vi_name, []).append(str(e))

            except CodeGenError as e:
                files.append(GeneratedFileSchema(
                    path=relative_path,
                    vi_name=vi_name,
                    status="generation_error",
                    error=str(e),
                    source_type="ast",
                ))
                errors.append(f"Code generation error for {vi_name}: {e}")
                needs_review.append(vi_name)

            except Exception as e:
                files.append(GeneratedFileSchema(
                    path=relative_path,
                    vi_name=vi_name,
                    status="generation_error",
                    error=str(e),
                    source_type="ast",
                ))
                errors.append(f"Unexpected error for {vi_name}: {e}")
                needs_review.append(vi_name)

    # Generate __init__.py
    init_path = output_dir_path / "__init__.py"
    if not init_path.exists():
        init_path.write_text('"""Generated package."""\n')

    # Count results
    ok_count = sum(1 for f in files if f.status == "ok")
    error_count = sum(1 for f in files if f.status != "ok")

    # Build summary with actionable info for agent
    summary_parts = [
        f"Generated Python package: {vi_folder_name}",
        f"Output: {output_dir_path}",
        f"Total VIs: {len(files)}",
        f"Successful: {ok_count}",
        f"Failed: {error_count}",
    ]

    if missing_deps:
        summary_parts.append("")
        summary_parts.append("MISSING DEPENDENCIES (agent should help resolve):")
        for vi, deps in missing_deps.items():
            if deps:
                summary_parts.append(f"  - {vi}: missing {', '.join(deps)}")
            else:
                summary_parts.append(f"  - {vi}: VI not found in search paths")

    if needs_review:
        summary_parts.append("")
        summary_parts.append("FILES NEEDING REVIEW:")
        for vi in needs_review[:10]:
            summary_parts.append(f"  - {vi}")
        if len(needs_review) > 10:
            summary_parts.append(f"  ... and {len(needs_review) - 10} more")

    return CodeGenResult(
        success=error_count == 0,
        output_dir=str(output_dir_path),
        package_name=vi_folder_name,
        files=files,
        summary="\n".join(summary_parts),
        errors=errors,
        warnings=warnings,
        total_vis=len(files),
        successful=ok_count,
        failed=error_count,
        needs_review=needs_review,
    )
