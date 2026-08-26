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
``_find_private_data_ctl``, ``_resolve_class_vi_path``, ``_ctl_root_fields``)
against a throwaway, never-loaded graph instance, plus the shared
``collect_direct_dep_qnames`` helper. This is deliberate: if the web staging
closure computed dependencies any other way, it could silently drift from what
``_load_dependency`` actually resolves and understage a render, breaking the
web/desktop byte-parity guarantee (see
``editors/vscode/build/check-web-parity.sh``). Those loader primitives are thus
a de-facto shared contract: renaming, relocating, or changing the resolution of
any one MUST update this module in lockstep.

``<vilib>``/``<userlib>`` refs are never resolved via a LIBRARY ROOT here —
this never sets library roots on the throwaway graph, matching the web
render path itself (``cached_render`` is likewise never given
``vilib_root``/``userlib_root``). They may still resolve via the same
name-based fallback the loader uses (a same-named file on a search path), so
vi.lib/user.lib deps are handled identically on both sides.
"""

from __future__ import annotations

from pathlib import Path

from .graph import InMemoryVIGraph
from .graph.loading import build_dep_ref_map, collect_direct_dep_qnames
from .parser import parse_vi
from .parser.models import ParsedDependencyRef
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


def _resolve_one(
    graph: InMemoryVIGraph,
    qname: str,
    dep_ref: ParsedDependencyRef | None,
    caller: Path,
    search_paths: list[Path],
) -> Path | None:
    """Resolve one dependency qname to an on-disk file exactly as
    ``_load_dependency`` does: the loader's ``_resolve_dependency_path``, then
    the caller-side ``.lvclass`` walk-up fallback that ``_resolve_dependency_path``
    deliberately leaves to its caller. Shared by ``_list_deps_vi`` and
    ``_list_deps_ctl`` so both mirror the loader identically."""
    resolved = graph._resolve_dependency_path(qname, dep_ref, caller, search_paths)
    leaf = qname.rsplit(":", 1)[-1]
    if resolved is None and leaf.endswith(".lvclass"):
        resolved = graph._walk_up_find(caller.parent, leaf)
    return resolved


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

    all_dep_qnames = collect_direct_dep_qnames(
        metadata.subvi_qualified_names, metadata.type_map, own_qname
    )

    dep_ref_map = build_dep_ref_map(metadata.dependency_refs)

    # Every recorded LinkSavePathRef dependency, too. The SubVI/type call table
    # (subvi_qualified_names / type_map) is empty on some VIs — e.g. older files
    # whose VITS block doesn't decode — yet the RENDER still resolves and reads
    # each LinkSavePathRef, so a closure keyed only off the call table understages
    # and the web render degrades vs desktop (a wired SubVI drawn as a bare box).
    # dependency_refs is the complete record of what the file references, so the
    # closure must follow it. (tests/test_issue_corpus.py::…issue29… guards this.)
    all_dep_qnames.update(dep_ref_map)

    graph = InMemoryVIGraph()
    results: list[str] = []
    seen: set[str] = set()
    for qname in sorted(all_dep_qnames):
        _add_existing(
            results,
            seen,
            _resolve_one(graph, qname, dep_ref_map.get(qname), path, search_paths),
        )
    return results


def _list_deps_ctl(path: Path, search_paths: list[Path] | None) -> list[str]:
    """``.ctl`` typedef deps: nested class/typedef refs from its own type map —
    mirrors ``graph/loading.py::load_typedef`` (which always resolves these with
    ``dep_ref=None``: name-based search / class walk-up only — a ``.ctl``'s
    nested type refs carry no LinkSavePathRef). The type map is read via the
    loader's own ``_ctl_root_fields`` primitive (it guards its own extraction,
    returning ``{}`` on failure), so this never reimplements the ``.ctl`` parse."""
    if search_paths is None:
        search_paths = [path.parent]

    graph = InMemoryVIGraph()
    try:
        _, type_map = graph._ctl_root_fields(path)
    except (RuntimeError, OSError, ValueError):
        return []

    # A .ctl's nested type refs are classes/typedefs only (no SubVI call table),
    # so the shared collector with an empty subvi list yields exactly them.
    names = collect_direct_dep_qnames([], type_map, None)

    results: list[str] = []
    seen: set[str] = set()
    for name in sorted(names):
        _add_existing(
            results, seen, _resolve_one(graph, name, None, path, search_paths)
        )
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

    def _member_file(member_name: str, url: str, *, subvi: bool) -> Path:
        """A member's on-disk file: the recorded relative url, else a name-based
        search (a VI via ``_find_subvi``, everything else via ``_find_file``) —
        the same per-member resolution ``load_lvlib`` does. ``_add_existing``
        filters a still-missing path, so no existence guard is needed here."""
        cand = path.parent / url
        if not cand.exists():
            finder = graph._find_subvi if subvi else graph._find_file
            cand = finder(member_name, search_paths, path.parent) or cand
        return cand

    for member in lib.members:
        member_name = Path(member.url).name
        if member.member_type == "VI":
            resolved = _member_file(
                member_name, member.url, subvi=not member_name.lower().endswith(".ctl")
            )
        elif member.member_type in ("LVClass", "Library"):
            resolved = _member_file(member_name, member.url, subvi=False)
        else:
            continue
        _add_existing(results, seen, resolved)

    return results
