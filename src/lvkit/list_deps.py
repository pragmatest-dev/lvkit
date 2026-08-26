"""Direct, on-disk dependency listing for one VI/typedef/class/library file.

Purely additive: the ONLY consumer is the web (Pyodide) VS Code extension
(``editors/vscode/web/extension.js``), which BFS-walks :func:`list_deps` to
compute the transitive dependency closure it must mirror into Pyodide's
virtual ``/proj`` filesystem before a render — instead of the old approach of
staging every ``.vi`` under the whole workspace. Nothing in the desktop
CLI/MCP/pipeline path calls this module; it changes no existing behavior.

The resolution itself is NOT reimplemented here — it reuses the exact same
primitives ``InMemoryVIGraph``'s loader (``graph/loading.py``) uses to resolve
a ``LinkSavePathRef`` (:meth:`InMemoryVIGraph._resolve_dependency_path`,
``_walk_up_find``, ``_find_file``, ``_find_subvi``,
``_find_private_data_ctl``, ``_resolve_class_vi_path``) against a throwaway,
never-loaded graph instance. This is deliberate: if the web staging closure
computed dependencies any other way, it could silently drift from what
``_load_dependency`` actually resolves and understage a render, breaking the
web/desktop byte-parity guarantee (see
``editors/vscode/build/check-web-parity.sh``).

``<vilib>``/``<userlib>`` refs are always skipped here (never resolved) —
this never sets library roots on the throwaway graph, matching the web
render path itself (``cached_render`` is likewise never given
``vilib_root``/``userlib_root``), so vi.lib/user.lib deps are consistently
left unresolved on both sides.
"""

from __future__ import annotations

from pathlib import Path

from .extractor import extract_vi_xml
from .graph import InMemoryVIGraph
from .parser import parse_vi
from .parser.type_mapping import parse_type_map_rich
from .structure import parse_lvclass, parse_lvlib


def list_deps(vi_path: Path | str, search_paths: list[Path] | None = None) -> list[str]:
    """Direct dependency file paths of one ``.vi``/``.ctl``/``.lvclass``/
    ``.lvlib`` file, resolved exactly as the graph loader would.

    Returns absolute path strings for dependencies that resolve to an
    existing on-disk file — one BFS "level" for the caller to keep expanding
    (recursing is the caller's job; this returns only the DIRECT deps of the
    one file passed in). Dispatches on suffix, since each container kind's
    dependency shape is different at the source (LinkSavePathRef entries for
    ``.vi``/``.ctl``; lvclass method/private-data/parent-class refs for
    ``.lvclass``; lvlib member refs for ``.lvlib``) — mirroring
    ``graph/loading.py``'s ``_load_dependency``/``load_typedef``/
    ``load_lvclass``/``load_lvlib`` respectively.
    """
    path = Path(vi_path)
    suffix = path.suffix.lower()
    if suffix == ".lvlib":
        return _list_deps_lvlib(path, search_paths)
    if suffix == ".lvclass":
        return _list_deps_lvclass(path, search_paths)
    if suffix == ".ctl":
        return _list_deps_ctl(path, search_paths)
    return _list_deps_vi(path, search_paths)


def _add_existing(results: list[str], seen: set[str], candidate: Path | None) -> None:
    """Append ``candidate`` (resolved, deduped) if it exists as a real file."""
    if candidate is None:
        return
    resolved = candidate.resolve()
    if not resolved.is_file():
        return
    key = str(resolved)
    if key in seen:
        return
    seen.add(key)
    results.append(key)


def _list_deps_vi(path: Path, search_paths: list[Path] | None) -> list[str]:
    """``.vi`` deps: SubVI calls + referenced-type classes/typedefs — the
    exact set ``graph/loading.py::_load_vi_recursive`` builds as
    ``all_dep_qnames`` before handing each to ``_load_dependency``."""
    if search_paths is None:
        search_paths = [path.parent]

    try:
        vi = parse_vi(path)
    except (RuntimeError, OSError, ValueError):
        return []

    metadata = vi.metadata
    own_qname = metadata.qualified_name or path.name

    all_dep_qnames: set[str] = set()
    for qname in metadata.subvi_qualified_names:
        if qname and qname != own_qname:
            all_dep_qnames.add(qname)
    for lv_type in metadata.type_map.values():
        if lv_type.classname and lv_type.classname != "LabVIEW Object":
            all_dep_qnames.add(lv_type.classname)
        if lv_type.typedef_name:
            all_dep_qnames.add(lv_type.typedef_name)

    dep_ref_map = {
        ref.qualified_name: ref
        for ref in metadata.dependency_refs
        if ref.qualified_name
    }

    graph = InMemoryVIGraph()
    results: list[str] = []
    seen: set[str] = set()
    for qname in sorted(all_dep_qnames):
        resolved = graph._resolve_dependency_path(
            qname, dep_ref_map.get(qname), path, search_paths
        )
        if resolved is None and qname.rsplit(":", 1)[-1].endswith(".lvclass"):
            resolved = graph._walk_up_find(path.parent, qname.rsplit(":", 1)[-1])
        _add_existing(results, seen, resolved)
    return results


def _list_deps_ctl(path: Path, search_paths: list[Path] | None) -> list[str]:
    """``.ctl`` typedef deps: nested class/typedef refs from its own type
    map — mirrors ``graph/loading.py::load_typedef`` (which always resolves
    these with ``dep_ref=None``, i.e. name-based search / class walk-up
    only — a ``.ctl``'s nested type refs carry no LinkSavePathRef)."""
    if search_paths is None:
        search_paths = [path.parent]

    try:
        _bd_xml, _fp_xml, main_xml = extract_vi_xml(path)
    except (RuntimeError, OSError):
        return []
    if main_xml is None or not main_xml.exists():
        return []
    type_map = parse_type_map_rich(main_xml)

    names: set[str] = set()
    for lv_type in type_map.values():
        if lv_type.classname and lv_type.classname != "LabVIEW Object":
            names.add(lv_type.classname)
        if lv_type.typedef_name:
            names.add(lv_type.typedef_name)

    graph = InMemoryVIGraph()
    results: list[str] = []
    seen: set[str] = set()
    for name in sorted(names):
        resolved = graph._resolve_dependency_path(name, None, path, search_paths)
        if resolved is None and name.endswith(".lvclass"):
            resolved = graph._walk_up_find(path.parent, name)
        _add_existing(results, seen, resolved)
    return results


def _list_deps_lvclass(path: Path, search_paths: list[Path] | None) -> list[str]:
    """``.lvclass`` deps: its private-data control, its parent class (walked
    up from its own directory), and its method VIs — mirrors
    ``graph/loading.py::load_lvclass``. Method VIs are included even though a
    class reached only as a referenced TYPE loads ``fields_only`` (no
    methods) under the render path's MINIMAL mode: over-staging a method VI
    that a render happens not to need is harmless, while a closure that is
    UNDER what desktop can load would break byte-parity — see this module's
    docstring."""
    if search_paths is None:
        search_paths = [path.parent]

    try:
        cls = parse_lvclass(path)
    except (RuntimeError, OSError, ValueError):
        return []

    graph = InMemoryVIGraph()
    results: list[str] = []
    seen: set[str] = set()

    ctl = graph._find_private_data_ctl(path.parent, cls.private_data_ctl)
    _add_existing(results, seen, ctl)

    if cls.parent_class and cls.parent_class != "LabVIEW Object":
        parent_file = graph._walk_up_find(path.parent, cls.parent_class + ".lvclass")
        _add_existing(results, seen, parent_file)

    for method in cls.methods:
        vi_path = graph._resolve_class_vi_path(path.parent, method.vi_path)
        _add_existing(results, seen, vi_path)

    return results


def _list_deps_lvlib(path: Path, search_paths: list[Path] | None) -> list[str]:
    """``.lvlib`` deps: its member VIs/typedefs/classes/nested libraries —
    mirrors ``graph/loading.py::load_lvlib``."""
    if search_paths is None:
        search_paths = [path.parent]

    try:
        lib = parse_lvlib(path)
    except (RuntimeError, OSError, ValueError):
        return []

    graph = InMemoryVIGraph()
    results: list[str] = []
    seen: set[str] = set()

    for member in lib.members:
        member_name = Path(member.url).name
        if member.member_type == "VI":
            if member_name.lower().endswith(".ctl"):
                ctl_path = path.parent / member.url
                if not ctl_path.exists():
                    found = graph._find_file(member_name, search_paths, path.parent)
                    if found:
                        ctl_path = found
                _add_existing(results, seen, ctl_path if ctl_path.exists() else None)
            else:
                vi_path = path.parent / member.url
                if not vi_path.exists():
                    vi_path = graph._find_subvi(member_name, search_paths, path.parent)
                _add_existing(
                    results, seen, vi_path if vi_path and vi_path.exists() else None
                )
        elif member.member_type == "LVClass":
            class_path = path.parent / member.url
            if not class_path.exists():
                found = graph._find_file(member_name, search_paths, path.parent)
                if found:
                    class_path = found
            _add_existing(results, seen, class_path if class_path.exists() else None)
        elif member.member_type == "Library":
            nested_path = path.parent / member.url
            if not nested_path.exists():
                found = graph._find_file(member_name, search_paths, path.parent)
                if found:
                    nested_path = found
            _add_existing(results, seen, nested_path if nested_path.exists() else None)

    return results
