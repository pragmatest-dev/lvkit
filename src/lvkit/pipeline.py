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
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lvkit.cache_paths import project_root
from lvkit.codegen import build_module
from lvkit.codegen.ast_utils import (
    build_type_checking_block,
    to_function_name,
    unique_names,
)
from lvkit.codegen.error_path import is_error_wire_vi
from lvkit.codegen.errors import MissingVIError
from lvkit.codegen.errors_module import (
    ERRORS_MODULE_NAME,
    ERRORS_MODULE_SOURCE,
    LABVIEW_ERROR_NAME,
)
from lvkit.codegen.gaps import Gap, GapLog, gap_problem
from lvkit.codegen.import_resolver import ImportResolver, ModuleImport, NameImport
from lvkit.codegen.package_modules import PackageModules
from lvkit.codegen.type_module import build_type_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.node_kinds import NodeType
from lvkit.library_folders import Folders, SearchEntries
from lvkit.long_paths import display_path, extended_length, too_long_to_import
from lvkit.models import LVType
from lvkit.module_layout import ModuleLayout
from lvkit.polymorphic_variants import variants_to_generate
from lvkit.project_store import find_project_store
from lvkit.run_report import (
    Problem,
    ProblemKind,
    ProblemStage,
    RunProblems,
    RunSettings,
    RunStopped,
    problem_for_exception,
)
from lvkit.type_names import derive_python_name
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
    graph: InMemoryVIGraph,
) -> None:
    """Write a .error.py file beside the module for a SyntaxError during
    generation."""
    error_path = module_path.with_suffix(".error.py")
    error_path.write_text(f"# SYNTAX ERROR: {error}\n\n{code}", encoding="utf-8")
    print(f"         -> SYNTAX ERROR: {error_path.name}")
    _record_failure(graph, vi_name, error)
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

    def shown(key: str) -> str | None:
        """The path a message names the VI at ``key`` by: the one that places its
        module (project-relative, or the installation path its caller recorded);
        a missing VI with no recorded path (a class method no class lists) as
        ``key_label`` shows it."""
        if (
            graph is not None
            and graph.is_stub(key)
            and graph.recorded_path(key) is None
        ):
            return graph.key_label(key)
        if layout is not None:
            return layout.display_path(key)
        return graph.recorded_path(key) if graph is not None else None

    def missing(name: str, key: str) -> MissingVIError:
        """The gap for the VI at ``key`` that was not found. An instance saved
        inside its caller has no file of its own: the message names the VI as the
        caller labels it and says where it is kept, never the internal key."""
        holder = graph.embedding_vi(key) if graph is not None else None
        if holder is not None:
            where = shown(holder) or Path(holder).name
            return MissingVIError(
                name,
                None,
                f"it is saved inside {where} (an Express VI or other "
                "embedded instance) and has no file of its own",
            )
        return MissingVIError(
            name, shown(key), graph.stub_reason(key) if graph is not None else None
        )

    def locate_by_name(vi_name: str) -> Path:
        """The output file of a VI known only by name: a vi.lib VI, or a VI
        reference constant (which names its VI by name). A call of a project VI
        is never found this way -- it is found by its key, and a call that has
        none stops code generation first."""
        if vi_name in vi_paths:
            return found(vi_name, vi_name)
        raise missing(vi_name, vi_name)

    def found(key: str, name: str) -> Path:
        """The output file of the VI at ``key``; a VI the graph holds only as a
        stub, with no implementation of its own, was not found."""
        implemented = (
            vilib_resolver is not None
            and graph is not None
            and vilib_resolver.has_python(graph.recorded_path(key))
        )
        if graph is not None and graph.is_stub(key) and not implemented:
            raise missing(name, key)
        return vi_paths[key]

    def locate(subvi_name: str, callee_key: str | None) -> Path:
        """The output file of the VI a call names: the one generated for
        ``callee_key``, else the one for ``subvi_name`` (see ``locate_by_name``)."""
        if callee_key is not None:
            if callee_key in vi_paths:
                return found(callee_key, subvi_name)
            if graph is not None and graph.is_stub(callee_key):
                raise missing(subvi_name, callee_key)
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
        derived = derive_python_name(Path(key).name)
        if vi_paths[key] == caller_path:
            return NameImport("", derived)
        dots, package, module = relative(vi_paths[key])
        dotted = f"{package}.{module}" if package else module
        name = spell(key, derived)
        alias = "" if name == derived else f" as {name}"
        return NameImport(f"from {dots}{dotted} import {derived}{alias}", name)

    def names_import(key: str, names: Collection[str]) -> str:
        """The statement importing ``names`` from the module generated for the
        class or VI at ``key``; empty for the module being generated."""
        if vi_paths[key] == caller_path:
            return ""
        dots, package, module = relative(vi_paths[key])
        dotted = f"{package}.{module}" if package else module
        return f"from {dots}{dotted} import {', '.join(sorted(names))}"

    def type_import(lv_type: LVType) -> NameImport | None:
        if graph is None:
            return None
        key = graph.type_key(lv_type)
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
        shown,
        names_import,
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


def _report_gaps(
    vi_gaps: list[Gap], all_gaps: list[Gap], graph: InMemoryVIGraph
) -> None:
    """Put every gap of a VI that was written on the VI's node (which announces
    its warning), and keep them."""
    if graph.run_settings.gaps_are_problems:
        for gap in vi_gaps:
            graph.record_problem(gap.vi, gap_problem(gap, graph))
    all_gaps.extend(vi_gaps)


def _record_failure(graph: InMemoryVIGraph, vi_name: str, exc: Exception) -> None:
    """Record that generating ``vi_name`` failed with ``exc``; an lvkit error also
    prints its traceback, the same in every command."""
    problem = problem_for_exception(
        graph.key_label(vi_name), ProblemStage.GENERATE, exc
    )
    graph.record_problem(vi_name, problem)
    if problem.kind is ProblemKind.LVKIT_ERROR:
        traceback.print_exception(exc)


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
            caller_key=variant_name,
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
    vilib_root: Folders = None,
    userlib_root: Folders = None,
    instrlib_root: Folders = None,
    search_path: SearchEntries = None,
    run: RunProblems | None = None,
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
        run: The run's problems (``run_report.py``). Each gap and each VI
            that cannot be written is recorded on its VI's node and collected in
            it; with ``fail_fast`` on, the run ends at the first problem.

    Returns:
        Summary dict with keys: vilib, ast, error, failed counts and
        ``gaps``, the list of ``Gap`` records.
        ``output_dir``, ``vi_paths`` and ``generated`` are in the form the
        caller gave ``output_dir``. On Windows with long paths off, open a file
        deeper than 260 characters through ``lvkit.long_paths.extended_length``.
    """
    input_path = Path(input_path)
    output_dir_root = Path(output_dir)
    run = run or RunProblems(RunSettings(echo=True, root=project_root(input_path)))

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

    graph = run.new_graph()
    if vilib_root or userlib_root or instrlib_root or search_path:
        graph.set_library_roots(
            vilib_root=vilib_root,
            userlib_root=userlib_root,
            instrlib_root=instrlib_root,
            search_path=search_path,
        )
    graph.set_catalog_stop(True)

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

    vilib_resolver = get_vilib_resolver()

    def catalog_gives_python(key: str) -> bool:
        return vilib_resolver.has_python(graph.recorded_path(key))

    # A library only scopes its members: it has no module, and nothing imports it.
    # Where the catalog gives Python for a VI, what only it calls is no part of
    # the output, except a VI the user asked for by naming its folder or file.
    reached = graph.keys_reached_without_entering(
        catalog_gives_python, graph.keys_loaded_from(input_path)
    )
    # A class's private-data control is written as the class itself (its
    # fields are the class's): it has no module of its own.
    conversion_order = graph.get_conversion_order()
    private_data = {
        control
        for key in conversion_order
        if graph.is_class(key) and (control := graph.class_private_data_key(key))
    }
    order = [
        key
        for key in conversion_order
        if graph.node_type(key) in _MODULE_NODE_TYPES
        and key in reached
        and key not in private_data
    ]
    print(f"\nConversion order ({len(order)} VIs):")

    # The VIs a class lists are methods of its one Python class, in the module of
    # the class file: they have no module of their own.
    class_of_member: dict[str, str] = {
        member: class_key
        for class_key in order
        if graph.node_type(class_key) is NodeType.CLASS and not graph.is_stub(class_key)
        for member in graph.class_member_keys(class_key)
    }

    # Identify polymorphic groups (from VI metadata, not heuristics). A group
    # whose polymorphic VI a class lists has no wrapper module: its variants are
    # members, or modules of their own.
    poly_groups = {
        wrapper: variants
        for wrapper, variants in graph.get_polymorphic_groups().items()
        if wrapper not in class_of_member
    }

    # A variant the catalog gives a module for (a full implementation) is that
    # module; every other variant (its own diagram, or a catalog inline template)
    # has no module of its own: a diagram is written in its wrapper's module, if
    # at all, and a template at its call sites.
    poly_variants: set[str] = {
        v
        for variants in poly_groups.values()
        for v in variants
        if not vilib_resolver.has_implementation(graph.recorded_path(v))
    }
    # The variants that get code are the ones the VIs written from their diagrams
    # use, which includes the variants those variants use.
    poly_groups = variants_to_generate(
        graph,
        vilib_resolver,
        poly_groups,
        written={
            key
            for key in order
            if graph.node_type(key) is NodeType.VI
            and not graph.is_stub(key)
            and not catalog_gives_python(key)
            and key not in poly_variants
            and key not in poly_groups
        },
    )

    # Every module lives at the project-relative path of its file.
    layout = ModuleLayout(
        graph,
        order,
        project_root(input_path),
        shared=frozenset(poly_variants) | frozenset(class_of_member),
        reserved=[ERRORS_MODULE_NAME],
    )
    package_modules = PackageModules.from_graph(
        graph, order, reserved={*layout.root_names, ERRORS_MODULE_NAME}
    )
    vi_paths: dict[str, Path] = {
        key: output_dir_resolved.joinpath(*place.packages) / f"{place.module}.py"
        for key, place in layout.places.items()
    }

    # A method's module is its class's
    for member, class_key in class_of_member.items():
        if class_key in vi_paths:
            vi_paths[member] = vi_paths[class_key]

    # Redirect polymorphic variant paths to their wrapper's path
    for wrapper_name, variants in poly_groups.items():
        wrapper_path = vi_paths.get(wrapper_name)
        if wrapper_path:
            for variant_name in variants:
                vi_paths[variant_name] = wrapper_path

    generated: list[tuple[str, Path | None, str]] = []
    all_gaps: list[Gap] = []

    try:
        for i, vi_name in enumerate(order, 1):
            if vi_name in class_of_member:
                print(f"  [{i}/{len(order)}] {graph.key_label(vi_name)}")
                print("         -> (method of its class)")
                continue

            # Skip variants - they'll be generated with their wrapper
            if vi_name in poly_variants:
                print(f"  [{i}/{len(order)}] {graph.key_label(vi_name)}")
                print("         -> (included in polymorphic wrapper)")
                continue

            is_stub = graph.is_stub(vi_name)
            # The catalog decides from the path the callers record, whether or
            # not the VI's file is loaded: a VI it gives Python for is generated
            # from that, never from its diagram.
            recorded = graph.recorded_path(vi_name)
            has_vilib = vilib_resolver.has_python(recorded)
            has_inline = has_vilib and vilib_resolver.has_inline(recorded)

            print(f"  [{i}/{len(order)}] {graph.key_label(vi_name)}")

            if has_inline or is_error_wire_vi(recorded, vilib_resolver):
                # Skip - inlined at call sites by AST builder
                print("         -> (inlined at call sites)")
                continue

            # A polymorphic wrapper with no variant to generate has no module
            if vi_name in poly_groups and not poly_groups[vi_name]:
                print("         -> (polymorphic, every called variant is catalog)")
                continue

            if vi_name in layout.unplaced:
                graph.record_problem(
                    vi_name,
                    Problem(
                        vi=graph.key_label(vi_name),
                        stage=ProblemStage.GENERATE,
                        kind=ProblemKind.UNWRITABLE,
                        reason=str(layout.unplaced[vi_name]),
                    ),
                )
                print(f"         -> FAILED: {layout.unplaced[vi_name]}")
                generated.append((vi_name, None, "failed"))
                continue

            # Only create directory structure when we're actually going to write a file
            output_path = vi_paths[vi_name]
            vi_gaps = GapLog(graph)
            _ensure_packages(output_path, output_dir_resolved)

            if has_vilib:
                # Generate vilib/openg implementation
                code = vilib_resolver.get_implementation(recorded) or ""
                output_path.write_text(code, encoding="utf-8")
                print(f"         -> vilib: {output_path.name}")
                generated.append((vi_name, output_path, "vilib"))

            elif is_stub:
                # Not found: nothing to write. Each call of it is a gap.
                print("         -> (not found: no module)")
                continue

            elif graph.is_class(vi_name) or graph.is_typedef(vi_name):
                try:
                    data_files: dict[str, bytes] = {}
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
                            package_modules=package_modules,
                            layout=layout,
                        ),
                        gaps=vi_gaps,
                        data_files=data_files,
                    )
                    ast.parse(code)
                    output_path.write_text(code, encoding="utf-8")
                    _write_data_files(output_path.parent, data_files)
                    _report_gaps(vi_gaps, all_gaps, graph)
                    print(f"         -> type: {output_path.name}")
                    generated.append((vi_name, output_path, "ast"))
                except Exception as e:  # noqa: BLE001 — per-type; keep generating remaining modules
                    _record_failure(graph, vi_name, e)
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
                    _report_gaps(vi_gaps, all_gaps, graph)
                    print(
                        f"         -> polymorphic: {output_path.name}"
                        f" ({len(variants)} variants)"
                    )
                    generated.append((vi_name, output_path, "ast"))
                except SyntaxError as e:
                    _write_syntax_error(output_path, code, e, vi_name, generated, graph)
                except Exception as e:  # noqa: BLE001 — per-VI; keep generating remaining VIs
                    _record_failure(graph, vi_name, e)
                    print(f"         -> FAILED: {e}")
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
                    _report_gaps(vi_gaps, all_gaps, graph)
                    print(f"         -> AST: {output_path.name}")
                    generated.append((vi_name, output_path, "ast"))

                except SyntaxError as e:
                    _write_syntax_error(output_path, code, e, vi_name, generated, graph)

                except Exception as e:  # noqa: BLE001 — per-VI; keep generating remaining VIs
                    _record_failure(graph, vi_name, e)
                    error_path = output_path.with_suffix(".error.py")
                    error_path.write_text(f"# ERROR: {e}", encoding="utf-8")
                    print(f"         -> FAILED: {e}")
                    generated.append((vi_name, error_path, "error"))

        # Generate polymorphic wrappers for structurally-detected groups
        # whose wrapper VI wasn't in the conversion order
        for wrapper_name, variants in poly_groups.items():
            if not variants:
                continue
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
                    wrapper_gaps = GapLog(graph)
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
                    _report_gaps(wrapper_gaps, all_gaps, graph)
                    generated.append((wrapper_name, wrapper_path, "ast"))
                except Exception as e:  # noqa: BLE001 — per-wrapper; continue to next
                    _record_failure(graph, wrapper_name, e)
                    print(f"  [poly wrapper] FAILED for {wrapper_name}: {e}")

        # Generate minimal __init__.py (just makes it a package)
        init_path = output_dir_resolved / "__init__.py"
        if not init_path.exists():
            init_path.write_text('"""Generated package."""\n', encoding="utf-8")

    except RunStopped as stop:
        print("Stopped at the first problem (--fail-fast): the output is incomplete.")
        generated.append((stop.problem.vi, None, "failed"))

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
        "layout": layout,
        "vi_folder_name": vi_folder_name,
    }
