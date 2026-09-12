"""Block-diagram logic generation: loads a VI's dependency closure and emits
one Python module per VI via ``lvkit.codegen.builder.build_module`` -- never
reimplementing any of its code generation.

Every module is named after its VI (``<vi>.py``), so the entry VI and each SubVI
it calls are peer modules in one flat directory, importing each other by that
bare VI name. Many converted VIs therefore co-locate in a single directory (the
way a LabVIEW ``.llb`` of VIs maps to a package of modules), and the pure entry
module ``<vi>.py`` is self-describing and runs headless on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lvkit.codegen.ast_utils import to_function_name, to_module_name
from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode


@dataclass
class LogicResult:
    """What logic generation produced, for state_gen/panel_gen to build on."""

    entry_vi_path: Path  # the concrete .vi file the panel/state reflect
    entry_module_stem: str  # the entry VI's module name, e.g. "build_path"
    entry_func_name: str
    written_files: list[Path] = field(default_factory=list)
    polymorphic_note: str | None = None  # set when the input VI was a
    # polymorphic wrapper and a variant was picked on its behalf


def _closure(graph: InMemoryVIGraph, entry_key: str) -> set[str]:
    """BFS the SubVI call graph from ``entry_key`` (via ``VIContext.subvi_calls``,
    the same edges ``build_module`` itself walks) to find exactly the VIs this
    one VI's logic needs -- not the whole graph's conversion order, which may
    include unrelated polymorphic siblings pulled in by the same load."""
    seen = {entry_key}
    queue = [entry_key]
    while queue:
        key = queue.pop()
        ctx = graph.get_vi_context(key)
        for call in ctx.subvi_calls:
            if call.vi_name is None:
                continue
            callee_key = graph.resolve_vi_name(call.vi_name)
            if callee_key not in seen:
                seen.add(callee_key)
                queue.append(callee_key)
    return seen


def generate_logic(
    vi_path: Path,
    output_dir: Path,
    search_paths: list[Path],
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
) -> LogicResult:
    """Load ``vi_path``'s dependency closure and write one Python module per
    VI in it into ``output_dir``, returning the entry module's name/func and
    the concrete .vi path the panel/state should reflect.

    A polymorphic wrapper VI has no logic of its own (`ctx.inputs` is
    empty -- confirmed by loading OpenG's polymorphic "Build Path__ogtk.vi"
    directly: `get_vi_context` returns `inputs=[]`, `subvi_calls` listing
    every variant) so this generically falls back to its alphabetically-first
    variant; ``LogicResult.polymorphic_note`` records that this happened.
    """
    graph = InMemoryVIGraph()
    if vilib_root or userlib_root:
        graph.set_library_roots(vilib_root=vilib_root, userlib_root=userlib_root)

    loaded_key = graph.load_vi(vi_path, LoadMode.FULL, search_paths=search_paths)
    if loaded_key is None:
        raise ValueError(f"Could not load VI: {vi_path}")

    polymorphic_note: str | None = None
    poly_groups = graph.get_polymorphic_groups()
    entry_key = loaded_key
    if loaded_key in poly_groups:
        variants = sorted(poly_groups[loaded_key])
        chosen = variants[0]
        entry_key = graph.resolve_vi_name(chosen)
        polymorphic_note = (
            f"'{vi_path.name}' is a polymorphic VI ({len(variants)} variants); "
            f"the panel/logic use variant '{chosen}' (alphabetically first) -- "
            "a real polymorphic panel would need a type selector, which this "
            "generator does not implement. Point the generator at a concrete "
            "variant .vi directly for a stable panel."
        )

    entry_source = graph.get_vi_source_path(entry_key)
    if entry_source is None:
        raise ValueError(f"No source path recorded for resolved VI: {entry_key}")

    closure = _closure(graph, entry_key)
    order = [key for key in graph.get_conversion_order() if key in closure]

    # Reserve every module/function name up front so import statements built
    # while generating an earlier (dependency) module can already name a
    # later (caller) module -- irrelevant in a DAG rooted at entry_key, but
    # keeps this a single deterministic pass regardless of visit order.
    name_map: dict[str, tuple[str, str]] = {}
    used_stems: dict[str, int] = {}
    for key in order:
        display = graph.vi_display_name(key)
        func_name = to_function_name(display)
        # Every VI -- entry and SubVI alike -- is named after itself, so the
        # whole closure is a set of peer modules that co-locate in one directory.
        base_stem = to_module_name(display)
        count = used_stems.get(base_stem, 0)
        used_stems[base_stem] = count + 1
        module_stem = base_stem if count == 0 else f"{base_stem}_{count + 1}"
        name_map[key] = (module_stem, func_name)

    def import_resolver(subvi_name: str) -> str:
        resolved = graph.resolve_vi_name(subvi_name)
        entry = name_map.get(resolved)
        if entry is None:
            raise KeyError(
                f"SubVI '{subvi_name}' resolved to '{resolved}', which is "
                "outside this generator's computed dependency closure."
            )
        stem, func = entry
        return f"from {stem} import {func}"

    written: list[Path] = []
    for key in order:
        ctx = graph.get_vi_context(key)
        display = graph.vi_display_name(key)
        code = build_module(ctx, display, import_resolver=import_resolver, graph=graph)
        stem, _func = name_map[key]
        out_path = output_dir / f"{stem}.py"
        out_path.write_text(code + "\n", encoding="utf-8")
        written.append(out_path)

    entry_stem, entry_func = name_map[entry_key]
    return LogicResult(
        entry_vi_path=entry_source,
        entry_module_stem=entry_stem,
        entry_func_name=entry_func,
        written_files=written,
        polymorphic_note=polymorphic_note,
    )
