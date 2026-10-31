"""HTML documentation generation for LabVIEW VIs.

Re-exports ``generate_documents`` so that ``lvkit docs`` can import it
without reaching into ``scripts/``.
"""

from __future__ import annotations

import logging
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.docs.html_generator.naming import folders_below_root, library_group
from lvkit.docs.utils import generate_dependency_description
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import VINode
from lvkit.graph.node_kinds import NodeType
from lvkit.graph.typedef import TypedefInfo
from lvkit.module_layout import ModuleLayout, UnplacedVIError, project_root
from lvkit.render import render_vi_with_subvis
from lvkit.render.front_panel import render_ctl_front_panel
from lvkit.render.icons import resolve_icon_png
from lvkit.structure import parse_lvclass, parse_lvlib
from lvkit.vi_files import is_vi_file, rglob_vi_files

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------


def _collect_library_vis(library_path: Path) -> list[Path]:
    """Collect the file paths of a .lvlib's VI members -- its controls (.ctl) are
    listed as VI members too; ``_split_controls`` separates them."""
    library = parse_lvlib(library_path)
    base_path = library_path.parent

    vi_paths: list[Path] = []
    for member in library.members:
        if member.member_type != "VI":
            continue
        vi_path = base_path / member.url
        if vi_path.exists():
            vi_paths.append(vi_path.resolve())
            continue
        # The lvlib's stored relative path may not match the on-disk layout
        # (extracted samples get flattened/reorganized, so a member listed as
        # "../foo.vi" actually sits beside the lvlib). Resolve by the bare file
        # name — first in the library's own directory, then anywhere beneath it.
        name = Path(member.url).name
        candidate: Path | None = base_path / name
        if not candidate.exists():
            candidate = next(iter(base_path.rglob(name)), None)
        if candidate is not None and candidate.exists():
            vi_paths.append(candidate.resolve())
        else:
            logger.warning(
                "lvlib member not found: %r (listed as %r)",
                name,
                member.url,
            )

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
    """Collect all VI files recursively from a directory."""
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    return [p.resolve() for p in rglob_vi_files(dir_path) if p.is_file()]


def _generate_typedef_pages(
    graph: InMemoryVIGraph,
    generator: HTMLDocGenerator,
    typedef_infos: list[TypedefInfo],
) -> None:
    """Write one page per typedef, its front panel drawn inline. A panel that
    can't be rendered leaves a note on the page (and is logged) rather than a
    silently missing section."""
    for info in typedef_infos:
        note = None
        try:
            svg = render_ctl_front_panel(graph, info.key)
        except Exception:
            logger.exception("front panel render failed for %s", info.name)
            svg, note = None, "Front panel unavailable: it could not be rendered."
        generator.generate_typedef_page(info, svg, note)


def _collect_directory_ctls(dir_path: Path) -> list[Path]:
    """Collect all ``.ctl`` controls recursively from a directory."""
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")
    return sorted(p.resolve() for p in dir_path.rglob("*.ctl") if p.is_file())


def _split_controls(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """``paths`` split into ``(VIs, .ctl controls)``. A library lists its
    controls beside its VIs, but a control loads as a typedef, not a VI."""
    controls = [p for p in paths if p.suffix.lower() == ".ctl"]
    return [p for p in paths if p.suffix.lower() != ".ctl"], controls


def _collect_library_groups(
    graph: InMemoryVIGraph,
    vi_names: list[str],
    project: Path,
    search_roots: Sequence[Path] = (),
) -> tuple[dict[str, str], dict[str, str]]:
    """The group and the shown name of every VI that has a page or is linked from
    one. The group is its owning class or library; else the installation folder
    (``vi.lib``, ``user.lib``) its caller recorded; else its project-relative
    folder (the recorded path's folder for a VI outside the project)."""
    layout = ModuleLayout(graph, [], project, search_roots)
    poly_groups = graph.get_polymorphic_groups()
    names: set[str] = set(vi_names)
    for vi_name in vi_names:
        names.update(graph.get_vi_dependencies(vi_name))
        names.update(graph.get_vi_dependents(vi_name))
        names.update(poly_groups.get(vi_name, []))
    groups: dict[str, str] = {}
    labels: dict[str, str] = {}
    folders_of: dict[str, tuple[str, ...]] = {}
    for name in sorted(names):
        try:
            folders_of[name] = layout.folders_of(name)
        except UnplacedVIError:
            folders_of[name] = ()
        labels[name] = graph.vi_display_name(name)
        owner = graph.get_owning_class(name) or graph.get_owning_library(name)
        groups[name] = library_group(owner, folders_of[name])
    # Two VIs filed under one group with one name (vi.lib holds many folders):
    # the folders they sit in tell them apart.
    taken = Counter((groups[n], labels[n]) for n in names)
    for name in names:
        if taken[(groups[name], labels[name])] > 1:
            labels[name] = "/".join(
                (*folders_below_root(folders_of[name]), labels[name])
            )
    return groups, labels


def _collect_icons(
    graph: InMemoryVIGraph,
    output_dir: Path,
    page_filename: Callable[[str], str],
) -> dict[str, str]:
    """Collect and copy VI icons to the output directory, each beside the name of
    its VI's page (``icons/<group>/<page>.png``)."""
    icons_dir = output_dir / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    icon_map: dict[str, str] = {}

    for vi_name in graph.list_vis():
        vi_path = graph.get_vi_source_path(vi_name)
        if not vi_path:
            continue

        vi_path = Path(vi_path)

        icon_source = resolve_icon_png(vi_path.parent, vi_path.stem)

        if icon_source is not None:
            icon_name = page_filename(vi_name).removesuffix(".html") + ".png"
            icon_dest = icons_dir / icon_name
            icon_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon_source, icon_dest)
            icon_map[vi_name] = f"../icons/{icon_name}"

    return icon_map


class DocsStage(str, Enum):
    """The stage of documentation generation a problem happened in."""

    LOAD = "load"
    DIAGRAM = "diagram"
    PAGE = "page"


@dataclass(frozen=True)
class DocsProblem:
    """One problem of one VI: its path as generation shows it
    (``ModuleLayout.display_path``), the stage that failed (a ``DocsStage``) and why."""

    vi: str
    stage: DocsStage
    reason: str

    def __str__(self) -> str:
        return f"{self.vi}: {self.reason}"


@dataclass
class DocsResult:
    """What ``generate_documents`` did: the summary to print, every problem found,
    and whether nothing could be loaded."""

    summary: str
    problems: list[DocsProblem] = field(default_factory=list)
    stopped: bool = False

    def __str__(self) -> str:
        return self.summary


def _failed_path(layout: ModuleLayout, path: Path) -> str:
    """The path a file that could not be loaded is named by."""
    return layout.display_path(str(path.resolve()))


def _prepare_vi_documentation_data(
    vi_name: str,
    graph: InMemoryVIGraph,
    poly_groups: dict[str, list[str]],
    problems: list[DocsProblem],
    shown: Callable[[str], str],
    icon_map: dict[str, str] | None = None,
) -> dict:
    """Prepare all data needed for one VI documentation page. A part of the page
    that cannot be built is left out and named in ``problems``."""
    inputs_dc = graph.get_inputs(vi_name)
    outputs_dc = graph.get_outputs(vi_name)
    operations_dc = graph.top_level_nodes(vi_name)
    constants_dc = graph.get_constants(vi_name)
    dataflow_dc = graph.get_wires(vi_name)

    def control_rows(terminals: list) -> list[dict]:
        rows = []
        for inp in terminals:
            default_val = inp.default_value
            if default_val is not None and inp.lv_type and inp.lv_type.values:
                default_val = inp.lv_type.item_for(default_val) or default_val
            rows.append(
                {
                    "name": inp.name or f"input_{inp.index}",
                    "type": inp.lv_type.type_descriptor() if inp.lv_type else "Any",
                    "default_value": default_val,
                }
            )
        return rows

    def indicator_rows(terminals: list) -> list[dict]:
        return [
            {
                "name": out.name or f"output_{out.index}",
                "type": out.lv_type.type_descriptor() if out.lv_type else "Any",
            }
            for out in terminals
        ]

    controls = control_rows(inputs_dc)
    indicators = indicator_rows(outputs_dc)
    off_pane_controls = control_rows(graph.get_off_pane_inputs(vi_name))
    off_pane_indicators = indicator_rows(graph.get_off_pane_outputs(vi_name))

    graph_data = {
        "inputs": inputs_dc,
        "outputs": outputs_dc,
        "operations": operations_dc,
        "constants": constants_dc,
        "data_flow": dataflow_dc,
    }

    qualified_deps = set(graph.get_vi_dependencies(vi_name))
    # Type definitions (.ctl) are not SubVI calls: they are listed apart, linked
    # to their own page.
    type_definitions = {
        dep: graph.typedef_name(dep) for dep in qualified_deps if graph.is_typedef(dep)
    }
    qualified_deps -= type_definitions.keys()

    def _callee_keys(nodes: list) -> list[str]:
        keys: list[str] = []
        for node in nodes:
            if isinstance(node, VINode):
                key = graph.loaded_callee_key(node)
                if key is not None:
                    keys.append(key)
            keys.extend(_callee_keys(graph.child_nodes(node.id, vi_name)))
        return keys

    qualified_deps.update(_callee_keys(operations_dc))
    qualified_deps.discard(vi_name)

    dependencies = {
        dep_name: generate_dependency_description(dep_name, graph)
        for dep_name in sorted(qualified_deps)
    }

    callers = graph.get_vi_dependents(vi_name)

    is_poly = vi_name in poly_groups
    poly_variants = poly_groups.get(vi_name, [])

    variant_params = [
        {
            "name": variant_key,
            "inputs": [
                {
                    "name": inp.name,
                    "type": inp.lv_type.type_descriptor() if inp.lv_type else "Any",
                }
                for inp in graph.get_inputs(variant_key)
            ],
            "outputs": [
                {
                    "name": out.name,
                    "type": out.lv_type.type_descriptor() if out.lv_type else "Any",
                }
                for out in graph.get_outputs(variant_key)
            ],
        }
        for variant_key in poly_variants
    ]

    icon_path = None
    if icon_map and vi_name in icon_map:
        icon_path = str(icon_map[vi_name])

    try:
        diagram_svg, subvi_nodes = render_vi_with_subvis(graph, vi_name)
    except Exception as e:  # noqa: BLE001 — per-VI; the page is written without it
        logger.exception("SVG diagram render failed for %s", shown(vi_name))
        problems.append(
            DocsProblem(
                shown(vi_name),
                DocsStage.DIAGRAM,
                f"the diagram could not be drawn ({e})",
            )
        )
        diagram_svg, subvi_nodes = None, {}

    # Class hierarchy navigation: only set for class method VIs.
    owning_class = graph.get_owning_class(vi_name)
    method_access = graph.get_method_access(vi_name) if owning_class else None
    method_overrides = graph.get_method_overrides(vi_name) if owning_class else None

    return {
        "vi_name": vi_name,
        "controls": controls,
        "indicators": indicators,
        "off_pane_controls": off_pane_controls,
        "off_pane_indicators": off_pane_indicators,
        "graph": graph_data,
        "dependencies": dependencies,
        "type_definitions": type_definitions,
        "callers": callers,
        "is_polymorphic": is_poly,
        "poly_variants": poly_variants,
        "variant_params": variant_params,
        "icon_path": icon_path,
        "diagram_svg": diagram_svg,
        # {diagram data-node id -> target subVI key}; the page turns this into
        # click-to-navigate links (navigation lives in the doc layer).
        "subvi_nodes": subvi_nodes,
        "owning_class": owning_class,
        "method_access": method_access,
        "method_overrides": method_overrides,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_documents(
    library_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    mode: LoadMode = LoadMode.FULL,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
) -> DocsResult:
    """Generate HTML docs for a LabVIEW library, class, directory, or VI."""
    start_time = time.time()
    library_path_obj = Path(library_path)
    output_dir_obj = Path(output_dir)

    if not library_path_obj.exists():
        raise FileNotFoundError(f"Path not found: {library_path}")

    # Determine input type and collect VI paths
    print("[TIMING] Starting VI discovery...")
    t0 = time.time()
    ctl_paths: list[Path] = []
    if is_vi_file(library_path_obj):
        doc_type = "vi"
        doc_title = library_path_obj.stem
        vi_paths = [library_path_obj]
    elif library_path_obj.suffix == ".ctl":
        doc_type = "typedef"
        doc_title = library_path_obj.stem
        vi_paths = []
        ctl_paths = [library_path_obj.resolve()]
    elif library_path_obj.suffix == ".lvlib":
        doc_type = "library"
        doc_title = library_path_obj.stem
        vi_paths, ctl_paths = _split_controls(_collect_library_vis(library_path_obj))
    elif library_path_obj.suffix == ".lvclass":
        doc_type = "class"
        doc_title = library_path_obj.stem
        vi_paths = _collect_class_vis(library_path_obj)
    elif library_path_obj.is_dir():
        doc_type = "directory"
        doc_title = library_path_obj.name
        vi_paths = _collect_directory_vis(library_path_obj)
        ctl_paths = _collect_directory_ctls(library_path_obj)
    else:
        raise ValueError(
            f"Unsupported input type: {library_path}. "
            "Expected .lvlib, .lvclass, .vi, .ctl, or directory"
        )
    print(
        f"[TIMING] VI discovery: {time.time() - t0:.2f}s - Found {len(vi_paths)} VIs"
        f" and {len(ctl_paths)} controls"
    )

    if not vi_paths and not ctl_paths:
        return DocsResult(f"No VIs or controls found in {library_path}")

    # Load all VIs into graph
    print(f"[TIMING] Starting VI loading (mode={mode.value})...")
    t0 = time.time()
    graph = InMemoryVIGraph()
    if vilib_root or userlib_root:
        graph.set_library_roots(vilib_root=vilib_root, userlib_root=userlib_root)
    search_path_objs = [Path(p) for p in (search_paths or [])]

    loaded_vis: list[str] = []
    problems: list[DocsProblem] = []
    layout = ModuleLayout(graph, [], project_root(library_path_obj), search_path_objs)

    for i, vi_path in enumerate(vi_paths, 1):
        print(
            f"[TIMING]   Starting VI {i}/{len(vi_paths)}: {vi_path.name}...",
            flush=True,
        )
        vi_start = time.time()
        before_count = len(graph.list_vis())
        try:
            graph.load_vi(
                vi_path,
                mode=mode,
                search_paths=search_path_objs or None,
                layout=True,  # docs renders every VI — carry geometry from the
                # same parse instead of a second heap read per VI.
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
            problems.append(
                DocsProblem(_failed_path(layout, vi_path), DocsStage.LOAD, str(e))
            )
            print(
                f"[TIMING]   Failed VI {i}/{len(vi_paths)}: {vi_path.name} - {str(e)}",
                flush=True,
            )

    total_loaded = len(graph.list_vis())
    print(
        f"[TIMING] VI loading complete: {time.time() - t0:.2f}s - "
        f"Loaded {len(loaded_vis)} VIs, expanded to {total_loaded} total"
    )

    # Type-definition controls load into the SAME graph as typedef nodes (a
    # class's private-data control and every control a VI uses are already there).
    loaded_ctls: list[str] = []
    for ctl_path in ctl_paths:
        try:
            key = graph.load_typedef(ctl_path, search_paths=search_path_objs or None)
        except Exception as e:
            problems.append(
                DocsProblem(_failed_path(layout, ctl_path), DocsStage.LOAD, str(e))
            )
            continue
        if graph.is_stub(key):
            problems.append(
                DocsProblem(
                    _failed_path(layout, ctl_path),
                    DocsStage.LOAD,
                    "could not be read as a control",
                )
            )
        else:
            loaded_ctls.append(ctl_path.name)

    if not loaded_vis and not loaded_ctls:
        return DocsResult(
            "Failed to load any VIs or controls. Errors:\n"
            + "\n".join(str(p) for p in problems),
            problems,
            stopped=True,
        )
    typedef_keys = graph.list_typedefs()

    # Every command that parses a VI warms the index — docs parses the whole
    # set into `graph`, so upsert each loaded VI's facts (best-effort).
    from lvkit.index.build import warm_all_loaded

    warm_all_loaded(graph)

    # Get polymorphic VI info
    poly_groups = graph.get_polymorphic_groups()
    poly_variant_to_wrapper = graph.get_poly_variant_wrappers()

    # Create HTML generator
    generator = HTMLDocGenerator(output_dir_obj, doc_title, doc_type)
    typedef_infos = [graph.get_typedef(k) for k in typedef_keys]
    generator.register_typedefs([(i.key, i.name) for i in typedef_infos])

    all_vis = graph.list_vis()
    generator.all_vis = set(all_vis)
    generator.register_vi_names(
        *_collect_library_groups(
            graph, all_vis, project_root(library_path_obj), search_path_objs
        )
    )

    # Collect and copy icons
    print("[TIMING] Collecting VI icons...")
    t0 = time.time()
    icon_map = _collect_icons(graph, output_dir_obj, generator._vi_name_to_filename)
    generator.icon_map = icon_map
    print(
        f"[TIMING] Icon collection: {time.time() - t0:.2f}s"
        f" - Found {len(icon_map)} icons"
    )

    # Generate documentation for each VI
    print(f"[TIMING] Generating HTML pages for {total_loaded} VIs...")
    t0 = time.time()

    generated_count = 0

    for i, vi_name in enumerate(all_vis, 1):
        try:
            vi_data = _prepare_vi_documentation_data(
                vi_name,
                graph,
                poly_groups,
                problems,
                layout.display_path,
                icon_map,
            )
            generator.generate_vi_page(vi_data)
            generated_count += 1
            if i % 50 == 0:
                print(
                    f"[TIMING]   Generated {i}/{total_loaded} pages "
                    f"({time.time() - t0:.2f}s elapsed)"
                )
        except Exception as e:
            problems.append(
                DocsProblem(layout.display_path(vi_name), DocsStage.PAGE, str(e))
            )
    print(
        f"[TIMING] HTML generation: {time.time() - t0:.2f}s"
        f" - Generated {generated_count} pages"
    )

    # Generate class landing pages (must run before the index page, so the
    # index can link class group headers to their landing page).
    print("[TIMING] Generating class landing pages...")
    t0 = time.time()
    class_names = graph.list_classes()
    # A class owns its private-data control: class qualified name -> its path key.
    private_data = {
        ref.qualified: info.key
        for info in typedef_infos
        for ref in info.owned_by
        if ref.node_type == NodeType.CLASS
    }
    for classname in class_names:
        hierarchy = graph.get_class_hierarchy(classname)
        if hierarchy is None:
            continue
        method_access = {}
        for method_vi in hierarchy.methods:
            access = graph.get_method_access(method_vi)
            if access is not None:
                method_access[method_vi] = access
        generator.generate_class_page(
            hierarchy, method_access, private_data.get(classname)
        )
    print(
        f"[TIMING] Class page generation: {time.time() - t0:.2f}s"
        f" - Generated {len(class_names)} class pages"
    )

    # Type-definition pages (their Used By links to the VI / class pages above).
    _generate_typedef_pages(graph, generator, typedef_infos)
    print(f"[TIMING] Generated {len(typedef_infos)} type definition pages")

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
    ]
    if typedef_infos:
        summary_parts.append(f"Type definitions documented: {len(typedef_infos)}")
    summary_parts.append(f"Index page: {output_dir_obj / 'index.html'}")

    if problems:
        summary_parts.append(f"\nPartial: {len(problems)} problems:")
        summary_parts.extend(f"  - {err}" for err in problems)
        for err in problems:
            print(f"warning: {err}", file=sys.stderr)

    return DocsResult("\n".join(summary_parts), problems)
