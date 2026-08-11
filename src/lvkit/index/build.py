"""Build ``list[VIFacts]`` for a repo — path-keyed, no silent collisions.

The in-memory graph (``InMemoryVIGraph``) is keyed by bare VI **name**, so a
whole-repo ``load_directory`` silently collapses same-named loose VIs
(measured: 487 files -> 422 in ``list_vis()`` on JKI VI Tester). This module
path-keys ALL of them:

1. Load the whole repo once at ``LoadMode.MINIMAL`` — the 422 (or however
   many) non-collided VIs come out with full facts + the ``_dep_graph``
   call/ownership edges.
2. Project each loaded VI to ``VIFacts``, keyed by its resolved source path.
3. Whatever ``.vi`` files under the repo did NOT come out of that load
   (shadowed by a same-named sibling) get loaded + projected INDIVIDUALLY,
   one fresh ``InMemoryVIGraph`` each — reported as ``collisions``, never
   silently dropped.
4. Merge: invert ``calls`` into a networkx call graph to compute
   ``impact_score`` (transitive dependent count) per VI.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from .. import cache_paths
from ..graph import InMemoryVIGraph, LoadMode
from ..graph.models import Constant, VINode
from ..models import FPTerminal
from ..structure import get_project_members, parse_lvproj
from .model import (
    WIRED_CONTROL,
    WIRED_INDICATOR,
    WIRED_NONE,
    WIRED_OTHER,
    ClassFact,
    ConstantFact,
    LVProjMemberFact,
    TerminalFact,
    VIFacts,
)
from .query import build_call_graph
from .store import delete as store_delete
from .store import load as store_load
from .store import save as store_save
from .store import save_lvproj_members as store_save_lvproj_members


@dataclass
class BuildResult:
    """Result of a full index build: the facts + how many VIs collided.

    ``lvproj_members`` is the repo's ``.lvproj`` membership relation (project-
    level, many-to-many) — see :func:`build_lvproj_membership`. Persisted
    separately from ``facts`` (``store.save_lvproj_members``), since it's a
    property of the ``.lvproj`` files, not of any one VI's bytes.
    """

    facts: list[VIFacts]
    collisions: int
    lvproj_members: list[LVProjMemberFact]


def build_index(project_root: Path, vi_paths: list[Path]) -> BuildResult:
    """Build path-keyed ``VIFacts`` for every ``.vi`` in ``vi_paths``.

    ``project_root`` is used as the single ``search_paths`` root for both the
    whole-repo load and each individual collision load, so dependency
    resolution behaves identically in both passes.

    .. important::
       On a directory of 50+ VIs this pre-parses across a ``spawn`` process
       pool (``graph.parallel_parse``). Python's ``spawn`` re-imports the
       calling program's ``__main__`` module in each worker, so a **script**
       that calls this at module level MUST guard the call::

           if __name__ == "__main__":
               build_index(root, vi_paths)

       Without the guard, workers re-execute the script, the pool aborts
       (``BrokenProcessPool``), and the build still completes via the serial
       fallback — but noisily and slowly. The CLI/MCP entry points already
       satisfy this; only ad-hoc API scripts need the guard.
    """
    all_paths = {p.resolve() for p in vi_paths}

    graph = InMemoryVIGraph()
    graph.load_directory(project_root, LoadMode.MINIMAL, search_paths=[project_root])
    _load_class_ownership(graph, project_root)
    _load_library_ownership(graph, project_root)

    facts: dict[str, VIFacts] = {}
    covered: set[Path] = set()
    for vi_name in graph.list_vis():
        src = graph.get_vi_source_path(vi_name)
        if src is None:
            continue
        resolved = src.resolve()
        # Only VIs that are actually repo files under project_root — a
        # dependency that resolved to an external vi.lib/userlib VI is not
        # part of THIS project's index.
        if resolved not in all_paths:
            continue
        covered.add(resolved)
        facts[str(resolved)] = project_vi_facts(graph, vi_name, resolved)

    collision_paths = sorted(all_paths - covered)
    for cp in collision_paths:
        facts[str(cp)] = build_one_vi(project_root, cp)

    _recompute_impact(facts)
    return BuildResult(
        facts=list(facts.values()),
        collisions=len(collision_paths),
        lvproj_members=build_lvproj_membership(project_root),
    )


def build_lvproj_membership(project_root: Path) -> list[LVProjMemberFact]:
    """Build the ``.lvproj`` membership relation for every project under
    ``project_root`` — one row per (``.lvproj``, member).

    A repo holds MANY ``.lvproj`` and a VI can belong to several (or none), so
    this is a many-to-many relation, not a per-VI column. Purely a parse of the
    ``.lvproj`` XML (``structure.get_project_members`` — no VI load), so it's
    cheap to rebuild in full on every index build/refresh. Each raw member is
    resolved against disk here: ``resolved_path`` is the file it points at (or
    None — many URLs encode a layout above the checkout, e.g. installed vi.lib
    copies), and ``is_in_repo`` is True only when that file sits UNDER the
    indexed root. Unresolved members are recorded, never dropped.
    """
    root = project_root.resolve()
    rows: list[LVProjMemberFact] = []
    for lvproj_path in sorted(project_root.rglob("*.lvproj")):
        project = parse_lvproj(lvproj_path)
        for m in get_project_members(project):
            exists = m.path.exists()
            resolved = m.path.resolve() if exists else None
            is_in_repo = exists and resolved is not None and resolved.is_relative_to(
                root
            )
            rows.append(
                LVProjMemberFact(
                    lvproj_path=str(lvproj_path.resolve()),
                    lvproj_name=lvproj_path.stem,
                    member_name=m.name,
                    member_url=m.url,
                    resolved_path=str(resolved) if resolved is not None else None,
                    member_type=m.member_type,
                    is_in_repo=is_in_repo,
                    target=m.target,
                    is_dependency=m.is_dependency,
                )
            )
    return rows


def build_one_vi(project_root: Path, vi_path: Path) -> VIFacts:
    """Project ONE ``.vi`` to ``VIFacts`` via a fresh single-VI graph.

    The collision-safe path, shared by ``build_index`` (for shadowed VIs) and
    ``refresh_index`` (for changed VIs): load just this VI at MINIMAL (which
    also leaf-loads its direct SubVIs), plus any ``.lvclass`` in its own
    directory so class-ownership facts resolve, then project. Every fact is
    intrinsic to the VI's own bytes (see ``model.py``), so a single-VI build
    equals what the whole-repo pass would have produced for it.
    """
    cp = vi_path.resolve()
    cgraph = InMemoryVIGraph()
    cgraph.load_vi(cp, LoadMode.MINIMAL, search_paths=[project_root])
    vi_name = _vi_name_for_path(cgraph, cp)
    if vi_name is None:
        raise RuntimeError(
            f"index build: {cp} did not load into its own fresh graph "
            "(unexpected — every repo .vi should be individually loadable)"
        )
    # A class method's own directory holds its .lvclass (LabVIEW's convention);
    # scoped to this VI's directory since only ONE VI is projected here. A
    # class nested inside one or more .lvlib embeds a FULLY qualified name in
    # its method VIs (e.g. "A.lvlib:B.lvlib:C.lvclass:m.vi") — load_lvclass
    # must register the class under that SAME qualified qname (via
    # owner_chain) or its "owns" edge lands on an under-qualified key that
    # doesn't match the VI's actual node, and get_owning_class silently
    # returns None (see _owner_chain_for_class).
    chain, class_seg = _owner_chain_for_class(vi_name)
    for cls_path in sorted(cp.parent.glob("*.lvclass")):
        oc = (
            chain
            if (chain and class_seg and cls_path.name.lower() == class_seg.lower())
            else None
        )
        cgraph.load_lvclass(
            cls_path, LoadMode.NONE, search_paths=[project_root], owner_chain=oc,
        )
    return project_vi_facts(cgraph, vi_name, cp)


def _owner_chain_for_class(vi_name: str) -> tuple[list[str] | None, str | None]:
    """From a fully-qualified VI key like
    ``"A.lvlib:B.lvlib:C.lvclass:m.vi"``, return the ``.lvlib`` segments
    BEFORE the class (``["A.lvlib", "B.lvlib"]``) and the class segment
    filename (``"C.lvclass"``), so ``build_one_vi`` registers the class
    under the SAME qualified qname its method VIs embed. ``(None, None)``
    when the VI isn't a class method."""
    parts = vi_name.split(":")
    for i, seg in enumerate(parts):
        if seg.endswith(".lvclass"):
            return (parts[:i] or None), seg
    return None, None


def warm_index_for_vi(
    graph: InMemoryVIGraph, vi_name: str, vi_path: Path,
) -> None:
    """Persist ONE already-loaded VI's facts into its project index.

    This is what makes the index build **progressively**: every ordinary
    single-VI load (a CLI ``render``/``describe``/``generate``, an MCP deep tool)
    upserts that VI's row, so the index accumulates as the repo is used instead
    of only from a whole-repo ``build_index``. The row is keyed by path with a
    ``content_sha`` (``store.save`` deletes+inserts), so a later query reuses it
    when unchanged; ``impact_score`` stays 0 until a full/refresh pass recomputes
    it across the whole call graph (it needs the global inverse).

    Best-effort by contract: warming must NEVER break the command that triggered
    it (e.g. a read-only project dir), so any failure is swallowed.
    """
    try:
        p = vi_path.resolve()
        root = cache_paths._project_root_for(p) or p.parent
        store_save(root, [project_vi_facts(graph, vi_name, p)])
    except Exception:
        pass  # progressive warming is best-effort — never fail the caller


def warm_all_loaded(graph: InMemoryVIGraph) -> None:
    """Warm the index for EVERY VI this graph parsed (not just the entry VI).

    The single wiring point for "every command that parses a VI builds the
    index": a render/generate/docs/visualize load parses the entry VI *and* its
    SubVIs into one graph, so this upserts each one that has a real source path
    on disk. Multi-VI aware — a whole-directory or full-hierarchy load warms all
    of them from the parse it already paid for. Facts are grouped by project root
    and saved per root in one batch. Best-effort: never fails the caller.
    """
    try:
        by_root: dict[Path, list[VIFacts]] = defaultdict(list)
        for vi_name in graph.list_vis():
            src = graph.get_vi_source_path(vi_name)
            if src is None:
                continue  # a JSON-only stub / vilib VI has no file to key on
            try:
                p = src.resolve()
                root = cache_paths._project_root_for(p) or p.parent
                by_root[root].append(project_vi_facts(graph, vi_name, p))
            except Exception:
                continue  # one bad VI must not sink the rest
        for root, facts in by_root.items():
            try:
                store_save(root, facts)
            except Exception:
                pass
    except Exception:
        pass  # progressive warming is best-effort — never fail the caller


def _recompute_impact(facts: dict[str, VIFacts]) -> None:
    """Fill each VI's ``impact_score`` (transitive dependent count) AND
    ``callers_count`` (direct in-repo callers) from the inverted call graph —
    cheap (a graph walk, no re-parse).

    Both are computed on the SAME path-keyed call graph
    (:func:`build_call_graph`, which resolves each ``calls`` callee-key to a VI
    path). ``callers_count`` is the in-degree — the honest dead-code signal
    (``callers_count == 0`` == nothing in the repo calls this VI), correct even
    when ``qualified_name`` is None and ``calls`` holds bare filenames, so a
    user never has to write a fragile name-matching anti-join.
    """
    call_graph = build_call_graph(facts.values())
    for path, f in facts.items():
        if call_graph.has_node(path):
            f.impact_score = len(nx.ancestors(call_graph, path))
            f.callers_count = call_graph.in_degree(path)
        else:
            f.impact_score = 0
            f.callers_count = 0


@dataclass
class RefreshResult:
    """Result of an incremental refresh: which VIs changed, and the new total."""

    rebuilt: list[str]  # paths rebuilt (content changed or newly added)
    deleted: list[str]  # paths dropped (the .vi is gone)
    total: int          # VIs in the index after the refresh


def refresh_index(
    project_root: Path, vi_paths: list[Path], stored: list[VIFacts],
) -> tuple[RefreshResult, list[VIFacts]]:
    """Incrementally refresh ``stored`` against the on-disk repo by content hash.

    Content-hash — not git — so it works in any checkout (and matches how the
    model already keys incrementality, ``VIFacts.content_sha``): a VI whose
    ``sha256_file`` still equals its stored ``content_sha`` is reused untouched;
    changed/new VIs are rebuilt via :func:`build_one_vi`; VIs whose file is gone
    are dropped. ``impact_score`` is recomputed across the whole set. Returns the
    result plus the merged facts for the caller to persist (``store.save`` the
    facts, ``store.delete`` the ``deleted`` paths).
    """
    facts = {f.path: f for f in stored}
    current = {str(p.resolve()): p for p in vi_paths}

    deleted = [path for path in facts if path not in current]
    for path in deleted:
        del facts[path]

    rebuilt: list[str] = []
    for path, p in current.items():
        existing = facts.get(path)
        if existing is not None and existing.content_sha == cache_paths.sha256_file(p):
            continue
        facts[path] = build_one_vi(project_root, p)
        rebuilt.append(path)

    _recompute_impact(facts)
    merged = list(facts.values())
    return RefreshResult(rebuilt=rebuilt, deleted=deleted, total=len(merged)), merged


def ensure_fresh_index(project_root: Path, vi_paths: list[Path]) -> None:
    """Make the persisted index reflect the current files, then return.

    The gap-fill a read (a `query`) needs so it never serves stale rows: a cold
    store gets one full build; a warm store gets an incremental refresh
    (rebuild only content-changed/added VIs, drop deleted ones) keyed by
    ``VIFacts.content_sha``. Persists to the store. This is the SAME cold-or-
    refresh policy the MCP server applies before a query — shared here so the
    CLI `lvkit query` stays fresh too.
    """
    stored = store_load(project_root)
    if not stored:
        result = build_index(project_root, vi_paths)
        store_save(project_root, result.facts)
        store_save_lvproj_members(project_root, result.lvproj_members)
        return
    rr, merged = refresh_index(project_root, vi_paths, stored)
    store_delete(project_root, rr.deleted)
    store_save(project_root, merged)
    # Membership is a cheap .lvproj-only reparse (no VI content), so a refresh
    # recomputes it wholesale — catches added/removed projects and moved members.
    store_save_lvproj_members(project_root, build_lvproj_membership(project_root))


def _load_class_ownership(graph: InMemoryVIGraph, project_root: Path) -> None:
    """Establish class-ownership ("owns") edges for every ``.lvclass`` under
    ``project_root``.

    ``load_directory`` only walks ``.vi``/``.llb`` — it never touches a
    ``.lvclass`` file directly, so a class's method VIs (loaded as plain loose
    VIs by the directory walk) never get the "owns" edge that carries the
    scope/accessor info ``ClassFact`` needs (``get_owning_class`` returns
    ``None`` for all of them). Explicitly loading each class at
    ``LoadMode.NONE`` fixes this cheaply: ``load_lvclass`` re-visits each
    method (a no-op — LabVIEW's one-qname-per-memory invariant means an
    already-loaded VI is never re-parsed) purely to add the ownership edge;
    ``NONE`` (not ``MINIMAL``, which explicitly skips every method — see
    ``load_lvclass``'s docstring) is what makes it load the method list at
    all, and costs nothing extra since the methods themselves are already
    loaded, richer, from the MINIMAL directory pass.
    """
    for cls_path in sorted(project_root.rglob("*.lvclass")):
        graph.load_lvclass(cls_path, LoadMode.NONE, search_paths=[project_root])


def _load_library_ownership(graph: InMemoryVIGraph, project_root: Path) -> None:
    """Establish library-ownership ("owns") edges for every ``.lvlib`` under
    ``project_root`` — the ``.lvlib`` mirror of ``_load_class_ownership``.

    ``load_directory`` only walks ``.vi``/``.llb`` — it never touches a
    ``.lvlib`` file directly, so an unqualified directory walk never gets the
    library node + "owns" edge ``get_owning_library`` needs. Explicitly
    loading each ``.lvlib`` at ``LoadMode.NONE`` fixes this cheaply:
    ``load_lvlib`` re-visits each member VI (a no-op re-parse — LabVIEW's
    one-qname-per-memory invariant, same as ``load_lvclass``) purely to add
    the library node + ownership edge. This is safe to run because a
    library-member VI's OWN embedded metadata (``ParsedVI.metadata.
    qualified_name``, read straight from the VI's ``LVIN``/``LVSR`` binary
    blocks) already carries the SAME ``Lib.lvlib:VI.vi`` qualified key
    ``load_lvlib`` independently computes from the ``.lvlib`` XML — verified
    empirically against the real JKI-VI-Tester corpus's ``VITesterUtilities.
    lvlib``: the directory-walk load and a subsequent ``load_lvlib`` call
    register the SAME ``_dep_graph`` node for every member VI, so this pass
    only ever ADDS the missing "owns" edge, never creates a duplicate/
    shadow VI node.
    """
    for lib_path in sorted(project_root.rglob("*.lvlib")):
        graph.load_lvlib(lib_path, LoadMode.NONE, search_paths=[project_root])


def _vi_name_for_path(graph: InMemoryVIGraph, vi_path: Path) -> str | None:
    """Find the ``vi_name`` in a freshly-loaded graph whose source path IS
    ``vi_path`` — a MINIMAL single-file load also leaf-loads direct SubVIs, so
    ``list_vis()`` may hold more than one entry."""
    for vi_name in graph.list_vis():
        src = graph.get_vi_source_path(vi_name)
        if src is not None and src.resolve() == vi_path:
            return vi_name
    return None


def project_vi_facts(
    graph: InMemoryVIGraph, vi_name: str, vi_path: Path,
) -> VIFacts:
    """Project one loaded VI's graph facts into a ``VIFacts`` row.

    Every field here is intrinsic to the VI's own bytes (own connector pane,
    own constants, own dep_graph edges, own terminal types) — see
    ``model.py``'s module docstring for why that makes this a pure function
    of the VI's content hash.
    """
    vnode = graph.get_graph_node(vi_name)
    # A directory build loads class methods (and library members) as loose
    # VIs, so VINode.library is never set for them. get_owning_class/
    # get_owning_library resolve via the ownership edge (_load_class_ownership
    # / _load_library_ownership) instead: for a class member, the owning
    # .lvclass IS the library; for a plain .lvlib member (not also a class
    # method), the owning .lvlib is.
    owning_class = graph.get_owning_class(vi_name)
    library = (
        (vnode.library if isinstance(vnode, VINode) else None)
        or owning_class
        or graph.get_owning_library(vi_name)
    )
    qualified_name = vnode.qualified_name if isinstance(vnode, VINode) else None

    terminals: list[TerminalFact] = []
    type_use_keys: set[str] = set()
    all_terminals = [
        *graph.get_inputs(vi_name, public_only=False),
        *graph.get_outputs(vi_name, public_only=False),
    ]
    for t in all_terminals:
        field_names: list[str] = []
        enum_values: list[str] = []
        # Faithful label — resolved LVType, else the control_type family word
        # (cluster/class/array/ring/refnum/…), never the Python token "Any".
        lv_type_label = t.faithful_type_label()
        if t.lv_type is not None:
            fields = graph.get_type_fields(t.lv_type)
            if fields:
                field_names = [f.name for f in fields]
            if t.lv_type.classname:
                type_use_keys.add(t.lv_type.classname)
            if t.lv_type.typedef_name:
                type_use_keys.add(t.lv_type.typedef_name)
            if t.lv_type.values:
                enum_values = [
                    name for name, _ev in
                    sorted(t.lv_type.values.items(), key=lambda kv: kv[1].value)
                ]
        is_fp = isinstance(t, FPTerminal)
        terminals.append(
            TerminalFact(
                name=t.name,
                direction=t.direction,
                is_indicator=bool(is_fp and t.is_indicator),
                is_public=bool(is_fp and t.is_public),
                control_type=t.control_type if is_fp else None,
                py_type=t.python_type(),
                is_error_cluster=t.is_error_cluster,
                field_names=field_names,
                fp_dco_uid=t.fp_dco_uid if is_fp else None,
                lv_type=lv_type_label,
                enum_values=enum_values,
            )
        )

    constants: list[ConstantFact] = [
        ConstantFact(
            value=(
                c.raw_value
                if c.raw_value is not None
                else (str(c.value) if c.value is not None else "")
            ),
            label=c.label,
            py_type=c.lv_type.to_python() if c.lv_type else "Any",
            lv_type=c.lv_type.lv_label() if c.lv_type else "?",
            wired_to=_constant_wired_to(graph, vi_name, c),
        )
        for c in graph.get_constants(vi_name)
    ]

    calls: list[str] = []
    if vi_name in graph._dep_graph:
        for succ in graph._dep_graph.successors(vi_name):
            edata = graph._dep_graph.get_edge_data(vi_name, succ) or {}
            if edata.get("rel") == "owns":
                continue
            # A call is VI -> VI. A successor that is a class/typedef/library
            # node is a TYPE or containment reference (already captured in
            # ``type_uses``), NOT a call — e.g. a method referencing its own
            # class type for a "self" param yields a method -> class edge that
            # is not an ``owns`` edge. Keep only VI successors (``node_type``
            # is None for a loaded VI, "vi" for a stub) so the call graph stays
            # pure and ``get_callers``/``blast_radius`` never see a class.
            if graph._dep_graph.nodes[succ].get("node_type") in (
                "class", "typedef", "library", "unknown",
            ):
                continue
            calls.append(succ)

    class_fact = _build_class_fact(graph, vi_name, owning_class)

    return VIFacts(
        path=str(vi_path),
        name=vi_path.name,
        qualified_name=qualified_name,
        library=library,
        is_stub=graph.is_stub_vi(vi_name),
        content_sha=cache_paths.sha256_file(vi_path),
        terminals=terminals,
        constants=constants,
        calls=sorted(calls),
        type_uses=sorted(type_use_keys),
        class_fact=class_fact,
        impact_score=0,  # filled at merge time
    )


def _constant_wired_to(
    graph: InMemoryVIGraph, vi_name: str, c: Constant,
) -> str:
    """Classify what a constant's single output wire feeds: an indicator on
    ``vi_name``'s own connector pane, a control input, something else on the
    diagram, or nothing at all."""
    dests = graph.outgoing_edges(c.id)
    if not dests:
        return WIRED_NONE
    for dest in dests:
        if dest.node_id != vi_name:
            continue
        term = graph.get_terminal(dest.terminal_id)
        if isinstance(term, FPTerminal):
            return WIRED_INDICATOR if term.is_indicator else WIRED_CONTROL
    return WIRED_OTHER


def _build_class_fact(
    graph: InMemoryVIGraph, vi_name: str, owning_class: str | None,
) -> ClassFact | None:
    if owning_class is None:
        return None
    access = graph.get_method_access(vi_name)
    # The owning class's private data, rendered FAITHFULLY (never to_python) —
    # None when the class/its data isn't resolvable (e.g. an external class).
    fields = graph.get_class_fields(owning_class)
    private_data = [
        f"{f.name}: {f.type.lv_label()}" if f.type else f.name
        for f in (fields or [])
    ]
    # Authoritative parent (get_class_parent, NOT the load-gated
    # get_class_hierarchy): a collision-load graph never loads the parent, and
    # gating on that produced a spurious NULL parent beside the real value for
    # the same class (the WaitOnTestComplete duplicate). See get_class_parent.
    return ClassFact(
        owning_class=owning_class,
        parent=graph.get_class_parent(owning_class),
        scope=access.scope if access else None,
        is_accessor=bool(access and access.is_accessor),
        accessor_field=access.accessor_field if access else None,
        private_data=private_data,
    )
