"""AST-based Python code generation pipeline for LabVIEW VIs.

This module exposes the core generation logic as a library API.
Scripts and CLI commands delegate to :func:`generate_python`.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import sys
import traceback
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lvkit.codegen import ClassBuilder, ClassConfig, build_module
from lvkit.codegen.ast_utils import (
    build_type_checking_block,
    to_function_name,
    to_module_name,
    unique_names,
)
from lvkit.codegen.error_path import is_error_wire_vi
from lvkit.codegen.errors import MissingVIError
from lvkit.codegen.errors_module import (
    ERRORS_MODULE_NAME,
    ERRORS_MODULE_SOURCE,
    LABVIEW_ERROR_NAME,
)
from lvkit.codegen.gaps import Gap, GapLog, GapStop
from lvkit.codegen.import_resolver import ImportResolver, ModuleImport, NameImport
from lvkit.codegen.package_modules import PackageModules
from lvkit.codegen.type_module import build_type_module
from lvkit.graph import AmbiguousVIReferenceError, InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.node_kinds import NodeType
from lvkit.long_paths import display_path, extended_length, too_long_to_import
from lvkit.module_layout import ModuleLayout
from lvkit.project_store import find_project_store, project_root
from lvkit.structure import parse_lvclass
from lvkit.type_names import derive_python_name
from lvkit.vi_files import strip_vi_extension
from lvkit.vilib_resolver import VILibResolver
from lvkit.vilib_resolver import get_resolver as get_vilib_resolver

# Increase recursion limit for deeply nested VIs
sys.setrecursionlimit(10000)


@dataclass
class GenerationResult:
    """Summary of a generation run."""

    vilib: int = 0
    ast: int = 0
    error: int = 0
    generated: list[tuple[str, Path | None, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utility helpers (also used by generate_docs and other callers)
# ---------------------------------------------------------------------------


def _write_syntax_error(
    module_path: Path,
    code: str,
    error: SyntaxError,
    vi_name: str,
    generated: list[tuple[str, Path | None, str]],
) -> None:
    """Write a .error.py file beside the module for a SyntaxError during
    generation."""
    error_path = module_path.with_suffix(".error.py")
    error_path.write_text(f"# SYNTAX ERROR: {error}\n\n{code}", encoding="utf-8")
    print(f"         -> SYNTAX ERROR: {error_path.name}")
    generated.append((vi_name, error_path, "error"))


# The dependency nodes that have a generated module.
_MODULE_NODE_TYPES = (NodeType.VI, NodeType.CLASS, NodeType.TYPEDEF)


def _as_given(path: Path, written_dir: Path, given_dir: Path) -> Path:
    """``path``, a file under ``written_dir`` (the output folder in the form the
    files are written through), in the form the caller gave the output folder:
    ``given_dir`` joined with the part below it."""
    return given_dir / display_path(path).relative_to(display_path(written_dir))


def _ensure_packages(path: Path, output_dir: Path) -> None:
    """Create the package folders above ``path`` (each with an ``__init__.py``)."""
    folder = path.parent
    while folder != output_dir and output_dir in folder.parents:
        folder.mkdir(parents=True, exist_ok=True)
        init_path = folder / "__init__.py"
        if not init_path.exists():
            init_path.write_text(
                f'"""Package for {folder.name}."""\n', encoding="utf-8"
            )
        folder = folder.parent


def create_import_resolver(
    package_name: str,
    output_dir: Path,
    vi_paths: dict[str, Path],
    caller_path: Path,
    caller_key: str | None = None,
    graph: InMemoryVIGraph | None = None,
    vilib_resolver: VILibResolver | None = None,
    defined_names: Collection[str] = (),
    package_modules: PackageModules | None = None,
    layout: ModuleLayout | None = None,
) -> ImportResolver:
    """Create an import resolver for a VI.

    Args:
        package_name: Name of the output package (e.g., "get_settings_path")
        output_dir: Root output directory
        vi_paths: Dict mapping fully qualified VI names to their output paths
        caller_path: Output file of the module being generated. Every import is
            relative to the package directory holding it.
        caller_key: Graph key (source path) of the VI, class or typedef being
            generated; the classes and typedefs it names are found among its own
            dependencies.
        graph: Memory graph for library metadata lookup
        vilib_resolver: VILib resolver for vilib membership check
        defined_names: Class names the module defines itself; an import of the
            same derived name gets an alias.
        package_modules: The package's shared state; with it, a VI that obtains a
            queue by a constant name, or uses a Shared Variable, imports it from
            the module that defines it at the output root.
        layout: Where every module lives; gives the package of an owner (a
            library or class) for generated code that imports a VI by a name it
            computes.

    Returns:
        An ``ImportResolver``: ``function`` gives the import of a SubVI's
        function; ``module`` the import of a Global VI's module; ``type_import``
        the import of a class or typedef.
    """

    caller_dir = caller_path.parent

    def locate_by_name(vi_name: str) -> Path:
        """The output file of a VI known only by name: a vi.lib VI, or a VI
        reference constant (which names its VI by name). A call of a project VI
        is never found this way -- it is found by its key, and a call that has
        none stops code generation first."""
        qualified = vi_name
        if graph is not None:
            try:
                qualified = graph.resolve_vi_name(vi_name) or vi_name
            except AmbiguousVIReferenceError:
                pass
        for key in (qualified, vi_name):
            if key in vi_paths:
                return found(key, vi_name)
        raise MissingVIError(
            vi_name,
            graph.recorded_path(qualified) if graph is not None else None,
            graph.stub_reason(qualified) if graph is not None else None,
        )

    def found(key: str, name: str) -> Path:
        """The output file of the VI at ``key``; a VI the graph holds only as a
        stub, with no implementation of its own, was not found."""
        implemented = vilib_resolver is not None and vilib_resolver.has_implementation(
            key
        )
        if graph is not None and graph.is_stub(key) and not implemented:
            raise MissingVIError(name, graph.recorded_path(key), graph.stub_reason(key))
        return vi_paths[key]

    def locate(subvi_name: str, callee_key: str | None) -> Path:
        """The output file of the VI a call names: the one generated for
        ``callee_key``, else the one for ``subvi_name`` (see ``locate_by_name``)."""
        if callee_key is not None and callee_key in vi_paths:
            return found(callee_key, subvi_name)
        return locate_by_name(subvi_name)

    def relative(dep_path: Path) -> tuple[str, str, str]:
        """``(dots, package, module)``: the relative import of ``dep_path`` from
        the package directory of the calling module, which sits ``depth``
        directories below the output root."""
        depth = len(caller_dir.relative_to(output_dir).parts)
        below_root = dep_path.relative_to(output_dir).with_suffix("").parts
        return "." * (depth + 1), ".".join(below_root[:-1]), below_root[-1]

    # The name each imported class, typedef or function is spelled as in this
    # module, by the identity of its node (its file path). Two with the same
    # derived name, or one with the name of what the module defines, are told
    # apart by an alias.
    spelled: dict[str, str] = {}
    taken = set(defined_names)
    if caller_key is not None:
        taken.add(to_function_name(Path(caller_key).name))

    def spell(identity: str, derived: str) -> str:
        if identity not in spelled:
            spelled[identity] = unique_names([*sorted(taken), derived])[-1]
            taken.add(spelled[identity])
        return spelled[identity]

    def function_import(subvi_name: str, callee_key: str | None) -> NameImport:
        located = locate(subvi_name, callee_key)
        derived = to_function_name(subvi_name)
        if located == caller_path:
            return NameImport("", derived)
        dots, package, module = relative(located)
        dotted = f"{package}.{module}" if package else module
        name = spell(f"function:{located}", derived)
        alias = "" if name == derived else f" as {name}"
        return NameImport(f"from {dots}{dotted} import {derived}{alias}", name)

    def module_import(global_vi_name: str) -> ModuleImport:
        dots, package, module = relative(locate_by_name(global_vi_name))
        alias = f"{module}_global"
        return ModuleImport(f"from {dots}{package} import {module} as {alias}", alias)

    def node_import(key: str) -> NameImport:
        dots, package, module = relative(vi_paths[key])
        dotted = f"{package}.{module}" if package else module
        derived = derive_python_name(Path(key).name)
        name = spell(key, derived)
        alias = "" if name == derived else f" as {name}"
        return NameImport(f"from {dots}{dotted} import {derived}{alias}", name)

    def type_import(type_ref: str) -> NameImport | None:
        if graph is None or caller_key is None:
            return None
        key = graph.type_key(type_ref, caller_key)
        if key is None or key not in vi_paths:
            return None
        return node_import(key)

    def package_spec(key: str) -> str:
        """The start of the relative import spec of a module in the package that
        holds the file ``key``: the dots, then each package and a dot."""
        folder = output_dir.joinpath(*(layout.packages_of(key) if layout else ()))
        depth = len(caller_dir.relative_to(output_dir).parts)
        below_root = folder.relative_to(output_dir).parts
        return "." * (depth + 1) + "".join(f"{part}." for part in below_root)

    def project_file(folder: Path, source: Path) -> str | None:
        """Where ``source``, a file at or below the project ``folder``, sits from
        the calling module's folder once copied into the package: below the
        package ``folder`` is generated as, under its own names."""
        placed = layout.placed_folder(folder) if layout else None
        if placed is None:
            return None
        target = output_dir.joinpath(*placed, *source.relative_to(folder).parts)
        return Path(os.path.relpath(target, caller_dir)).as_posix()

    def error_import() -> str:
        dots, package, module = relative(output_dir / f"{ERRORS_MODULE_NAME}.py")
        return f"from {dots}{module} import {LABVIEW_ERROR_NAME}"

    def root_module_import(stem: str, alias: str) -> ModuleImport:
        depth = len(caller_dir.relative_to(output_dir).parts)
        return ModuleImport(f"from {'.' * (depth + 1)} import {stem} as {alias}", alias)

    return ImportResolver(
        function_import,
        module_import,
        type_import,
        node_import,
        error_import,
        package_spec,
        package_modules,
        root_module_import,
        project_file,
    )


def _type_definition_scope(graph: InMemoryVIGraph, key: str) -> str:
    """The node whose dependencies the types named in a class's or typedef's
    definition are found among: a typedef's own, and a class's private data
    control's (the class itself depends on its parent and its methods)."""
    if graph.is_class(key):
        return graph.class_private_data_key(key) or key
    return key


# ---------------------------------------------------------------------------
# Polymorphic module generation
# ---------------------------------------------------------------------------


def _write_errors_module(package_root: Path) -> None:
    """Write ``errors.py`` at the package root when a generated module imports
    ``LabVIEWError`` from it."""
    needle = f"import {LABVIEW_ERROR_NAME}"
    if any(
        needle in module.read_text(encoding="utf-8")
        for module in sorted(package_root.rglob("*.py"))
    ):
        (package_root / f"{ERRORS_MODULE_NAME}.py").write_text(
            ERRORS_MODULE_SOURCE, encoding="utf-8"
        )


def _warn_unimportable_paths(package_root: Path) -> None:
    """Warn once when a written module is at a path Python cannot import on this
    machine (a path over 260 characters while Windows long paths are off)."""
    longest = too_long_to_import(sorted(package_root.rglob("*.py")))
    if longest:
        path = display_path(longest[0])
        print(
            f"warning: {len(longest)} generated modules are at paths over "
            f"Windows' 260-character limit (the longest is {len(str(path))} "
            f"characters: {path}). Python cannot import them from a normal "
            "path: enable Windows long paths, or generate into a shorter "
            "output folder.",
            file=sys.stderr,
        )


def _write_data_files(folder: Path, data_files: dict[str, bytes]) -> None:
    """Write the files a generated module reads at run time, each at its path from
    the module's folder."""
    for name, content in data_files.items():
        target = Path(os.path.normpath(folder / name))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _report_gaps(vi_gaps: list[Gap], all_gaps: list[Gap], root: Path | None) -> None:
    """Print one warning line per gap of a VI that was written, and keep them."""
    for gap in vi_gaps:
        vi = gap.vi
        if root is not None:
            try:
                vi = Path(vi).relative_to(root).as_posix()
            except ValueError:
                pass
        where = f"{gap.node_name}: " if gap.node_name else ""
        print(f"warning: {vi}: {where}{gap.reason} [{gap.kind}]", file=sys.stderr)
        for line in gap.diagnostic():
            print(f"    {line}", file=sys.stderr)
    all_gaps.extend(vi_gaps)


def _generate_polymorphic_module(
    wrapper_name: str,
    variants: list[str],
    graph: InMemoryVIGraph,
    vilib_resolver: VILibResolver | None,
    package_name: str,
    output_dir: Path,
    vi_paths: dict[str, Path],
    layout: ModuleLayout,
    data_files: dict[str, bytes] | None = None,
    package_modules: PackageModules | None = None,
    gaps: list[Gap] | None = None,
) -> str:
    """Generate a module containing all variants and wrapper. Data files the
    variants read at run time are added to ``data_files``, and the gaps found in
    the variants to ``gaps``."""
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
    # Imports a variant body needs (e.g. the runtime helper for Index Array).
    # The variant bodies below drop their own import lines; collect them here so
    # the shared module header carries every one a variant references.
    _header = {ln for ln in lines if ln.startswith(("import ", "from "))}
    variant_imports: set[str] = set()
    variant_annotation_imports: set[str] = set()

    for variant_name in variants:
        vi_context = graph.get_vi_context(variant_name)
        # build_module names the variant function from its DISPLAY name
        # (basename), so the wrapper must reference that same short name — a
        # full-path-mangled name is undefined (NameError). Mirrors wrapper_func.
        func_name = to_function_name(Path(variant_name).name)
        variant_funcs.append(func_name)

        import_resolver = create_import_resolver(
            package_name,
            output_dir,
            vi_paths,
            vi_paths[wrapper_name],
            caller_key=graph.resolve_vi_name(variant_name),
            graph=graph,
            vilib_resolver=vilib_resolver,
            package_modules=package_modules,
            layout=layout,
        )
        code = build_module(
            vi_context,
            vi_name=variant_name,
            import_resolver=import_resolver,
            graph=graph,
            data_files=data_files,
            gaps=gaps,
        )
        # Take the function and result class; collect imports the body needs
        # into the shared header rather than dropping them.
        tree = ast.parse(code)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                lines.append("")
                lines.append(ast.unparse(node))
                variant_result_classes.append(node.name)
            elif isinstance(node, ast.FunctionDef):
                lines.append("")
                lines.append(ast.unparse(node))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                imp = ast.unparse(node)
                if imp not in _header:
                    variant_imports.add(imp)
            elif isinstance(node, ast.If):
                variant_annotation_imports.update(
                    ast.unparse(stmt) for stmt in node.body
                )

        # Collect inputs/outputs for union signature
        for inp in vi_context.inputs:
            idx = getattr(inp, "slot_index", getattr(inp, "index", len(all_inputs)))
            if idx not in all_inputs:
                all_inputs[idx] = inp
        for out in vi_context.outputs:
            idx = getattr(
                out,
                "slot_index",
                getattr(out, "index", len(all_outputs)),
            )
            if idx not in all_outputs:
                all_outputs[idx] = out

    # A variant that calls a SIBLING variant emits an import for it as a
    # separate module — but siblings are DEFINED inline in this same module, and
    # no standalone module for them exists (ModuleNotFoundError). Drop any import
    # whose bound name is one of this module's own variant functions.
    _inline = set(variant_funcs)

    def _imports_inline_sibling(imp: str) -> bool:
        try:
            node = ast.parse(imp).body[0]
        except (SyntaxError, IndexError):
            return False
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            return False
        return any((a.asname or a.name) in _inline for a in node.names)

    variant_imports = {i for i in variant_imports if not _imports_inline_sibling(i)}

    # Hoist any imports the variant bodies referenced into the shared header
    # (after the fixed imports, before the blank line separating header/body).
    if variant_imports:
        lines[4:4] = sorted(variant_imports)
    if variant_annotation_imports:
        lines[4:4] = [
            ast.unparse(stmt)
            for stmt in build_type_checking_block(variant_annotation_imports)
        ]

    # Generate wrapper function. Name it by the VI's DISPLAY name (basename),
    # not the full path — callers import it as to_function_name(node.name) (the
    # short name), so a path-mangled wrapper name is an ImportError for every
    # caller. (Path(...).name strips a path prefix; a bare qualified name is
    # left unchanged, so multi-VI generation is unaffected.)
    wrapper_func = to_function_name(Path(wrapper_name).name)

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
    lines.append("def _lv_dispatch(_fn, /, *_vals):")
    lines.append('    """Call a polymorphic variant, mapping the wrapper\'s values')
    lines.append("    to the variant's parameters BY POSITION (variants share the")
    lines.append("    input SLOT order but may name a slot differently — e.g. a")
    lines.append('    scalar `string` vs an array `strings`)."""')
    lines.append("    import inspect")
    lines.append("    _p = list(inspect.signature(_fn).parameters)")
    lines.append("    return _fn(**dict(zip(_p, _vals)))")
    lines.append("")
    lines.append("")
    # The wrapper takes *args/**kwargs: a caller names arguments by the POLY
    # VI's OWN connector pane, which need not match any variant's parameter
    # names, so collect the provided values in call order and let _lv_dispatch
    # map them to the chosen variant's parameters positionally.
    lines.append(f"def {wrapper_func}(*args, **kwargs) -> {returns}:")
    # repr() makes the VI path a valid string literal whatever it contains
    # (a Windows path holds backslashes).
    docstring = repr(f"Polymorphic wrapper for {wrapper_name}.")
    lines.append(f"    {docstring}")
    lines.append("    _vals = [*args, *kwargs.values()]")

    if variant_funcs:
        # Categorize variants by type (array vs traditional/scalar) and dispatch
        # on the first value's type.
        array_variants = [f for f in variant_funcs if "array" in f.lower()]
        traditional_variants = [f for f in variant_funcs if "traditional" in f.lower()]
        other_variants = [
            f
            for f in variant_funcs
            if f not in array_variants and f not in traditional_variants
        ]
        if array_variants and (traditional_variants or other_variants):
            arr0 = array_variants[0]
            fallback = (
                traditional_variants[0]
                if traditional_variants
                else (other_variants[0] if other_variants else variant_funcs[0])
            )
            lines.append("    if _vals and isinstance(_vals[0], (list, tuple)):")
            lines.append(f"        return _lv_dispatch({arr0}, *_vals)")
            lines.append("    else:")
            lines.append(f"        return _lv_dispatch({fallback}, *_vals)")
        else:
            lines.append(f"    return _lv_dispatch({variant_funcs[0]}, *_vals)")
    else:
        lines.append("    pass")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


def generate_python(
    input_path: Path | str,
    output_dir: Path | str,
    mode: LoadMode = LoadMode.FULL,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    instrlib_root: Path | None = None,
    fail_fast: bool = False,
) -> dict:
    """Generate Python from VI files.

    Args:
        input_path: Path to .vi, .lvlib, .lvclass, or directory
        output_dir: Output directory for generated Python
        mode: dependency depth (``LoadMode``). Codegen defaults to ``FULL`` (the
            whole SubVI/class-method tree); ``NONE`` generates this VI only.
        vilib_root: Path to LabVIEW vi.lib on disk for <vilib> resolution
        userlib_root: Path to LabVIEW user.lib on disk for <userlib> resolution
        instrlib_root: Path to LabVIEW instr.lib on disk for <instrlib> resolution
        fail_fast: End the run at the first gap: its warning is printed, nothing
            more is generated, and the run counts as a stopped VI.

    Returns:
        Summary dict with keys: vilib, ast, error, failed counts and
        ``gaps``, the list of ``Gap`` records (printed as warnings).
        ``output_dir``, ``vi_paths`` and ``generated`` are in the form the
        caller gave ``output_dir``. On Windows with long paths off, open a file
        deeper than 260 characters through ``lvkit.long_paths.extended_length``.
    """
    input_path = Path(input_path)
    output_dir_root = Path(output_dir)

    # Create VI-named subfolder within output directory
    vi_folder_name = input_path.stem
    vi_folder_name = re.sub(r"[^\w]", "_", vi_folder_name).lower()
    vi_folder_name = re.sub(r"_+", "_", vi_folder_name).strip("_")

    output_dir_given = output_dir_root / vi_folder_name
    output_dir_resolved = extended_length(output_dir_given)

    # Clean output directory
    if output_dir_resolved.exists():
        shutil.rmtree(output_dir_resolved)
    output_dir_resolved.mkdir(parents=True, exist_ok=True)

    print(f"Loading: {input_path}")

    graph = InMemoryVIGraph()
    if vilib_root or userlib_root or instrlib_root:
        graph.set_library_roots(
            vilib_root=vilib_root,
            userlib_root=userlib_root,
            instrlib_root=instrlib_root,
        )

    # Detect input type and load appropriately
    if input_path.suffix.lower() == ".lvclass":
        graph.load_lvclass(
            str(input_path),
            mode,
        )
    elif input_path.suffix.lower() == ".lvlib":
        graph.load_lvlib(
            str(input_path),
            mode,
        )
    elif input_path.suffix.lower() == ".ctl":
        graph.load_typedef(input_path)
    elif input_path.suffix.lower() == ".llb":
        graph.load_llb(
            str(input_path),
            mode,
        )
    elif input_path.is_dir():
        graph.load_directory(
            str(input_path),
            mode,
        )
    else:
        graph.load_vi(
            str(input_path),
            mode,
        )

    # Every command that parses a VI warms the index — generate parses the whole
    # hierarchy into `graph`, so upsert each loaded VI's facts (best-effort).
    from lvkit.index.build import warm_all_loaded

    warm_all_loaded(graph)

    # A library only scopes its members: it has no module, and nothing imports it.
    order = [
        key
        for key in graph.get_conversion_order()
        if graph.node_type(key) in _MODULE_NODE_TYPES
    ]
    print(f"\nConversion order ({len(order)} VIs):")

    vilib_resolver = get_vilib_resolver()

    # Identify polymorphic groups (from VI metadata, not heuristics)
    poly_groups = graph.get_polymorphic_groups()

    # Structural fallback: detect polymorphic groups by finding VIs whose
    # names follow the pattern "Base (Variant)". Group them under the base
    # name even if the wrapper VI isn't in the conversion order.
    _variant_candidates: dict[str, list[str]] = defaultdict(list)
    for vi_name in order:
        if vi_name in poly_groups:
            continue
        name_no_ext = strip_vi_extension(vi_name)
        if "(" in name_no_ext and ")" in name_no_ext:
            # Extract base: "Filter (Array)__sfx" -> "Filter__sfx"
            paren_start = name_no_ext.index("(")
            paren_end = name_no_ext.rindex(")")
            base_part = name_no_ext[:paren_start].rstrip()
            suffix_part = name_no_ext[paren_end + 1 :]
            wrapper_name = base_part + suffix_part + ".vi"
            _variant_candidates[wrapper_name].append(vi_name)
    for wrapper_name, variants in _variant_candidates.items():
        if len(variants) >= 2 and wrapper_name not in poly_groups:
            poly_groups[wrapper_name] = variants

    poly_variants: set[str] = set()
    for variants in poly_groups.values():
        poly_variants.update(variants)

    # Every module lives at the project-relative path of its file.
    layout = ModuleLayout(
        graph,
        order,
        project_root(input_path),
        shared=frozenset(poly_variants),
        reserved=[ERRORS_MODULE_NAME],
    )
    package_modules = PackageModules.from_graph(
        graph, order, reserved={*layout.root_names, ERRORS_MODULE_NAME}
    )
    vi_paths: dict[str, Path] = {
        key: output_dir_resolved.joinpath(*place.packages) / f"{place.module}.py"
        for key, place in layout.places.items()
    }

    # Redirect polymorphic variant paths to their wrapper's path
    for wrapper_name, variants in poly_groups.items():
        wrapper_path = vi_paths.get(wrapper_name)
        if wrapper_path:
            for variant_name in variants:
                vi_paths[variant_name] = wrapper_path

    generated: list[tuple[str, Path | None, str]] = []
    all_gaps: list[Gap] = []
    gap_root = project_root(input_path)

    try:
        for i, vi_name in enumerate(order, 1):
            # Skip variants - they'll be generated with their wrapper
            if vi_name in poly_variants:
                print(f"  [{i}/{len(order)}] {vi_name}")
                print("         -> (included in polymorphic wrapper)")
                continue

            is_stub = graph.is_stub(vi_name)
            has_vilib = vilib_resolver.has_implementation(vi_name)
            has_inline = vilib_resolver.has_inline(vi_name)

            print(f"  [{i}/{len(order)}] {vi_name}")

            if has_inline or is_error_wire_vi(vi_name, vilib_resolver):
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

            if vi_name in layout.unplaced:
                print(f"         -> FAILED: {layout.unplaced[vi_name]}")
                generated.append((vi_name, None, "failed"))
                continue

            # Only create directory structure when we're actually going to write a file
            output_path = vi_paths[vi_name]
            vi_gaps = GapLog(fail_fast)
            _ensure_packages(output_path, output_dir_resolved)

            if has_vilib:
                # Generate vilib/openg implementation
                code = vilib_resolver.get_implementation(vi_name) or ""
                output_path.write_text(code, encoding="utf-8")
                print(f"         -> vilib: {output_path.name}")
                generated.append((vi_name, output_path, "vilib"))

            elif is_stub:
                # Not found: nothing to write. Each call of it is a gap.
                print("         -> (not found: no module)")
                continue

            elif graph.is_class(vi_name) or graph.is_typedef(vi_name):
                try:
                    code = build_type_module(
                        graph,
                        vi_name,
                        create_import_resolver(
                            vi_folder_name,
                            output_dir_resolved,
                            vi_paths,
                            output_path,
                            caller_key=_type_definition_scope(graph, vi_name),
                            graph=graph,
                            vilib_resolver=vilib_resolver,
                            defined_names=[derive_python_name(Path(vi_name).name)],
                            layout=layout,
                        ),
                        gaps=vi_gaps,
                    )
                    ast.parse(code)
                    output_path.write_text(code, encoding="utf-8")
                    _report_gaps(vi_gaps, all_gaps, gap_root)
                    print(f"         -> type: {output_path.name}")
                    generated.append((vi_name, output_path, "ast"))
                except Exception as e:  # noqa: BLE001 — per-type; keep generating remaining modules
                    error_path = output_path.with_suffix(".error.py")
                    error_path.write_text(f"# ERROR: {e}", encoding="utf-8")
                    print(f"         -> FAILED: {e}")
                    generated.append((vi_name, error_path, "error"))

            elif vi_name in poly_groups:
                # Generate polymorphic module with all variants
                # (all_inlined was checked above; only reach here if not all inlined)
                variants = poly_groups[vi_name]
                code = ""
                try:
                    data_files: dict[str, bytes] = {}
                    code = _generate_polymorphic_module(
                        vi_name,
                        variants,
                        graph,
                        vilib_resolver,
                        vi_folder_name,
                        output_dir_resolved,
                        vi_paths,
                        layout,
                        data_files=data_files,
                        package_modules=package_modules,
                        gaps=vi_gaps,
                    )
                    ast.parse(code)  # Validate syntax
                    output_path.write_text(code, encoding="utf-8")
                    _write_data_files(output_path.parent, data_files)
                    _report_gaps(vi_gaps, all_gaps, gap_root)
                    print(
                        f"         -> polymorphic: {output_path.name}"
                        f" ({len(variants)} variants)"
                    )
                    generated.append((vi_name, output_path, "ast"))
                except SyntaxError as e:
                    _write_syntax_error(output_path, code, e, vi_name, generated)
                except Exception as e:  # noqa: BLE001 — per-VI; keep generating remaining VIs
                    print(f"         -> FAILED: {e}")
                    traceback.print_exc()
                    generated.append((vi_name, None, "failed"))

            else:
                # Use AST builder for regular VIs
                vi_context = graph.get_vi_context(vi_name)
                code = ""

                try:
                    import_resolver = create_import_resolver(
                        vi_folder_name,
                        output_dir_resolved,
                        vi_paths,
                        output_path,
                        caller_key=vi_name,
                        graph=graph,
                        vilib_resolver=vilib_resolver,
                        package_modules=package_modules,
                        layout=layout,
                    )
                    data_files: dict[str, bytes] = {}
                    code = build_module(
                        vi_context,
                        vi_name,
                        import_resolver=import_resolver,
                        graph=graph,
                        data_files=data_files,
                        gaps=vi_gaps,
                    )

                    # Validate syntax
                    ast.parse(code)
                    output_path.write_text(code, encoding="utf-8")
                    _write_data_files(output_path.parent, data_files)
                    _report_gaps(vi_gaps, all_gaps, gap_root)
                    print(f"         -> AST: {output_path.name}")
                    generated.append((vi_name, output_path, "ast"))

                except SyntaxError as e:
                    _write_syntax_error(output_path, code, e, vi_name, generated)

                except Exception as e:  # noqa: BLE001 — per-VI; keep generating remaining VIs
                    error_path = output_path.with_suffix(".error.py")
                    error_path.write_text(f"# ERROR: {e}", encoding="utf-8")
                    print(f"         -> FAILED: {e}")
                    generated.append((vi_name, error_path, "error"))

        # Generate polymorphic wrappers for structurally-detected groups
        # whose wrapper VI wasn't in the conversion order
        for wrapper_name, variants in poly_groups.items():
            wrapper_path = vi_paths.get(wrapper_name)
            if not wrapper_path:
                place = layout.place_beside(wrapper_name, variants)
                wrapper_path = output_dir_resolved.joinpath(*place.packages) / (
                    f"{place.module}.py"
                )
                vi_paths[wrapper_name] = wrapper_path
            if not wrapper_path.exists():
                try:
                    _ensure_packages(wrapper_path, output_dir_resolved)
                    data_files: dict[str, bytes] = {}
                    wrapper_gaps = GapLog(fail_fast)
                    code = _generate_polymorphic_module(
                        wrapper_name,
                        variants,
                        graph,
                        vilib_resolver,
                        vi_folder_name,
                        output_dir_resolved,
                        vi_paths,
                        layout,
                        data_files=data_files,
                        package_modules=package_modules,
                        gaps=wrapper_gaps,
                    )
                    ast.parse(code)
                    wrapper_path.write_text(code, encoding="utf-8")
                    _write_data_files(wrapper_path.parent, data_files)
                    _report_gaps(wrapper_gaps, all_gaps, gap_root)
                    generated.append((wrapper_name, wrapper_path, "ast"))
                except Exception as e:  # noqa: BLE001 — per-wrapper; continue to next
                    print(f"  [poly wrapper] FAILED for {wrapper_name}: {e}")
                    traceback.print_exc()

        # Generate minimal __init__.py (just makes it a package)
        init_path = output_dir_resolved / "__init__.py"
        if not init_path.exists():
            init_path.write_text('"""Generated package."""\n', encoding="utf-8")

        # Generate class wrapper if input was an lvclass
        if input_path.suffix.lower() == ".lvclass":
            lvclass = parse_lvclass(input_path)

            # Get method contexts from graph
            method_contexts = {}
            for method in lvclass.methods:
                qualified_name = f"{lvclass.name}.lvclass:{method.name}.vi"
                ctx = graph.get_vi_context(qualified_name)
                if ctx.inputs or ctx.outputs or graph.top_level_nodes(qualified_name):
                    method_contexts[method.name] = ctx

            # Build class wrapper with context lookup for SubVI resolution
            class_path = output_dir_resolved / (to_module_name(lvclass.name) + ".py")
            import_resolver = create_import_resolver(
                vi_folder_name,
                output_dir_resolved,
                vi_paths,
                class_path,
                graph=graph,
                vilib_resolver=vilib_resolver,
                package_modules=package_modules,
                layout=layout,
            )
            builder = ClassBuilder(config=ClassConfig())
            class_gaps = GapLog(fail_fast)
            try:
                module = builder.build_class_module(
                    lvclass,
                    method_contexts,
                    import_resolver=import_resolver,
                    graph=graph,
                    gaps=class_gaps,
                )
                ast.fix_missing_locations(module)
                class_code = ast.unparse(module)

                # Write class file
                class_path.write_text(class_code, encoding="utf-8")
                _report_gaps(class_gaps, all_gaps, gap_root)
                print(f"\nGenerated class wrapper: {class_path.name}")
            except Exception as e:  # noqa: BLE001 — a class wrapper that cannot be built stops only itself
                print(f"\n  [class wrapper] FAILED for {lvclass.name}: {e}")
                traceback.print_exc()
                generated.append((lvclass.name, None, "failed"))

    except GapStop as stop:
        _report_gaps([stop.gap], all_gaps, gap_root)
        print("Stopped at the first gap (--fail-fast): the output is incomplete.")
        generated.append((stop.gap.vi, None, "failed"))

    _write_errors_module(output_dir_resolved)
    for written in package_modules.write(output_dir_resolved):
        print(f"\nGenerated shared module: {written.name}")

    _warn_unimportable_paths(output_dir_resolved)
    print(f"\nOutput: {display_path(output_dir_resolved)}")

    vilib_count = sum(1 for _, _, s in generated if s == "vilib")
    ast_count = sum(1 for _, _, s in generated if s == "ast")
    error_count = sum(1 for _, _, s in generated if s == "error")
    failed_count = sum(1 for _, _, s in generated if s == "failed")

    print(f"  vilib: {vilib_count}")
    print(f"  ast:   {ast_count}")
    print(f"  error: {error_count}")
    print(f"  failed: {failed_count}")
    print(f"  gaps:  {len(all_gaps)}")
    gap_vis = len({gap.vi for gap in all_gaps})
    if error_count or failed_count:
        print(
            f"Stopped: {error_count + failed_count} VI(s) could not be written"
            f" ({ast_count} generated, {gap_vis} with gaps)"
        )
    elif all_gaps:
        print(
            f"Partial: {ast_count} VIs generated, {gap_vis} with gaps"
            f" ({len(all_gaps)} gaps) — see warnings"
        )
    else:
        print(f"Complete: {ast_count} VIs generated, no gaps")

    if error_count > 0:
        store = find_project_store()
        if store:
            print(f"\n  To resolve errors, add mappings to {store}/")
            print(f"  See {store / 'README.md'} for file formats and instructions.")
            print(
                "  AI agent skills can add these automatically"
                " — run `lvkit setup` if not installed."
            )
        else:
            print("\n  To resolve errors:")
            print(
                "  - Run `lvkit setup` to install AI agent skills"
                " that resolve unknowns automatically."
            )
            print(
                "  - Or run `lvkit setup --no-skills` to create a"
                " .lvkit/ store for manual mappings."
            )

    return {
        "vilib": vilib_count,
        "ast": ast_count,
        "error": error_count,
        "failed": failed_count,
        "gaps": all_gaps,
        "generated": [
            (
                name,
                None
                if path is None
                else _as_given(path, output_dir_resolved, output_dir_given),
                status,
            )
            for name, path, status in generated
        ],
        "output_dir": output_dir_given,
        "graph": graph,
        "vilib_resolver": vilib_resolver,
        "vi_paths": {
            key: _as_given(path, output_dir_resolved, output_dir_given)
            for key, path in vi_paths.items()
        },
        "vi_folder_name": vi_folder_name,
    }
