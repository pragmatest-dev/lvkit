"""Query mixin for InMemoryVIGraph.

Methods: get_inputs, get_outputs, get_constants, get_wires,
get_operation_order, get_dataflow_graph (test-only), get_vi_context,
get_subvi_calls, resolve_vi_name, list_vis, get_vi_source_path, is_stub_vi,
get_stub_vi_info, dependency graph queries, polymorphic VI methods.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

from ..models import ClusterField, FPTerminal, Terminal
from ..parser.models import ParsedDependencyRef
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .core import _OPERATION_KINDS, _graph_node_to_op_kind, _node_order_key
from .interface_order import ordered_interface
from .loading import _redirect_member_beside_container
from .models import (
    AnyGraphNode,
    ClassFieldEntry,
    ClassHierarchyInfo,
    Constant,
    ConstantNode,
    Label,
    LabelNode,
    MethodAccessInfo,
    MethodOverrideInfo,
    PolyInfo,
    StructureNode,
    StubTerminalInfo,
    StubVIInfo,
    SubVICall,
    TerminalRef,
    VIContext,
    VIHealth,
    VIMetadata,
    VINode,
    VIProperties,
    Wire,
    WireEnd,
)
from .models import (
    PrimitiveNode as GraphPrimitiveNode,
)

if TYPE_CHECKING:
    from ..parser.layout import Layout
    from .core import InMemoryVIGraph


def _real_member_path(path: Path) -> Path | None:
    """The on-disk FILE path for a dependency's intended path, or None when it
    denotes no standalone file. A ``.lvclass``/``.lvlib`` is a container FILE,
    never a directory, so an INTERIOR container component means the ref names a
    MEMBER embedded in that container (e.g. a class's virtual private-data
    ``.ctl`` — a real graph node, but its bytes live inside the ``.lvclass``,
    not in a file of its own). Members are stored BESIDE their container, so
    flatten interior container components to the sibling and return it only if
    it is a real file; an embedded member (no sibling file) returns None. A
    CLEAN path (no interior container) is returned untouched WITHOUT an
    existence check — a genuinely absent-but-real dep (the web stages it into
    ``/proj`` on a later pass) must still be named."""
    parts = path.parts
    flat = [
        seg
        for i, seg in enumerate(parts)
        if i == len(parts) - 1 or not seg.lower().endswith((".lvclass", ".lvlib"))
    ]
    if len(flat) == len(parts):
        return path  # no interior container — a normal file path
    sibling = Path(*flat)
    return sibling if sibling.is_file() else None


class AmbiguousVIReferenceError(Exception):
    """A bare name/qname lookup (``resolve_vi_name``) matched two or more
    on-disk VIs that are genuinely DIFFERENT (distinct qualified identity),
    not duplicate copies of the same VI. Raised by ``_pick_vi_key`` instead of
    silently guessing one -- resolve by full source path instead."""


class QueryMixin:
    """Mixin providing graph query methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _term_to_node: dict[str, str]
    _dep_graph: nx.DiGraph
    _stubs: set[str]
    _poly_info: dict[str, PolyInfo]
    _qualified_aliases: dict[str, str]
    _name_to_keys: dict[str, list[str]]
    _qname_to_keys: dict[str, list[str]]
    _loaded_vis: set[str]
    _source_paths: dict[str, Path]
    _vi_display_names: dict[str, str]
    _vi_metadata: dict[str, VIMetadata]
    _vi_properties: dict[str, VIProperties]
    _vi_health: dict[str, VIHealth]
    _layouts: dict[str, Layout]
    _search_paths: list[Path]
    _vilib_root: Path | None
    _userlib_root: Path | None
    _instrlib_root: Path | None
    _vi_file_index: dict[str, Path] | None

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def has_parallel_branches(self, vi_name: str) -> bool: ...
        def get_class_fields(
            self,
            classname: str,
            caller_vi_key: str | None = None,
        ) -> list[ClusterField] | None: ...
        def _dep_key_for_ref(
            self, ref: str, caller_vi_key: str | None = None
        ) -> str | None: ...
        def _class_fields_by_key(self, node_key: str) -> list[ClusterField] | None: ...
        def _parent_class_key(self, node_key: str) -> str | None: ...

    # === Dependency Graph Queries ===

    def resolve_vi_name(self, vi_name: str) -> str:
        """Resolve any VI reference to its canonical ``vi_key`` (source path).

        A ``vi_key`` is a VI's identity: the canonical source-path string it was
        loaded under (see ``_load_vi_recursive``). This accepts a ``vi_key``
        (returned as-is), a qualified name (``Lib.lvclass:VI.vi``), a
        differently-cased alias, or a bare filename, and maps it through the
        name/qname reverse indexes. When a name/qname maps to MORE THAN ONE key
        -- genuine on-disk duplicates (a VI copied into a build-output tree, or
        parallel plugin trees) -- the pick is DETERMINISTIC (see
        ``_pick_vi_key``), never first-filesystem-order (that was the confluence
        bug). Returns the input unchanged when nothing matches.
        """
        # Already a canonical key.
        if vi_name in self._vi_nodes:
            return vi_name
        # A differently-cased / library reference aliased to a real key.
        if vi_name in self._qualified_aliases:
            return self._qualified_aliases[vi_name]
        # Qualified name -> key(s).
        keys = self._qname_to_keys.get(vi_name)
        if keys:
            return self._pick_vi_key(keys)
        # Bare filename -> key(s).
        keys = self._name_to_keys.get(vi_name)
        if keys:
            return self._pick_vi_key(keys)
        # A qualified ref we didn't index under that exact qname: try its leaf.
        if ":" in vi_name:
            keys = self._name_to_keys.get(vi_name.rsplit(":", 1)[-1])
            if keys:
                return self._pick_vi_key(keys)
        return vi_name

    def vi_display_name(self, vi_name: str) -> str:
        """The VI's human-facing qualified name (``Class.lvclass:vi.vi``, else the
        bare filename) for titles/headers. DISPLAY ONLY — never a lookup key. The
        identity remains the ``vi_key`` (path) from :meth:`resolve_vi_name`; this
        maps that key to its qname so the viewer shows the qualified name instead
        of the absolute source path. Falls back to the resolved key's basename,
        then the input, when no display name was recorded."""
        key = self.resolve_vi_name(vi_name)
        display = self._vi_display_names.get(key)
        if display:
            return display
        return Path(key).name or vi_name

    def _pick_vi_key(self, keys: list[str]) -> str:
        """Deterministically choose among duplicate ``vi_key``s that share a
        name/qname. Prefer the richest copy -- a stripped built copy loses
        control labels, so it carries fewer labelled terminals than its source
        twin, and fewer graph nodes -- then the lexically-smallest key. Path is
        the identity; this only fires for genuine on-disk duplicates, and the
        final key tie-break makes it filesystem-order-independent.

        GUARD: before collapsing, group the candidates by the loader-tracked
        qualified identity (``_vi_display_names`` -- each VI's own LIBN/LIBH
        owner chain, stamped once at load time; see ``_load_vi_recursive``).
        Two keys sharing that qname are genuinely the SAME VI saved to two
        paths (a stripped build copy alongside its source twin, or a parallel
        plugin tree) -- the collapse above is correct for those. Two keys with
        DIFFERENT qnames are provably DIFFERENT VIs that merely share a bare
        filename -- routine under LabVIEW dynamic dispatch, where every
        class's override of a method is literally ``run.vi`` (e.g.
        ``TestCase.lvclass:run.vi`` vs ``TestSuite.lvclass:run.vi``).
        Silently collapsing those via "richest wins" previously picked one
        arbitrarily and rendered/diffed/described the WRONG VI, so this
        raises a named, actionable error instead of guessing.
        """
        if len(keys) == 1:
            return keys[0]

        by_qname: dict[str, list[str]] = {}
        for key in keys:
            by_qname.setdefault(self._vi_display_names.get(key, key), []).append(key)
        if len(by_qname) > 1:
            groups = "; ".join(
                f"{qname!r}: {sorted(group_keys)}"
                for qname, group_keys in sorted(by_qname.items())
            )
            raise AmbiguousVIReferenceError(
                "Ambiguous VI reference -- this name matches multiple "
                f"DISTINCT VIs (different qualified identity): {groups}. "
                "Load/reference the VI by its full source path instead of "
                "its bare filename or an unqualified name."
            )

        def rank(key: str) -> tuple[int, int, str]:
            uids = self._vi_nodes.get(key) or set()
            labelled = 0
            if key in self._graph:
                node = self._graph.nodes[key].get("node")
                if node is not None:
                    labelled = sum(
                        1
                        for t in getattr(node, "terminals", ())
                        if getattr(t, "name", None)
                    )
            # Richest first (more labelled terminals, then more nodes); the key
            # itself is the final, deterministic tie-break.
            return (-labelled, -len(uids), key)

        return min(keys, key=rank)

    def list_vis(self) -> list[str]:
        """List all VIs in the graph (excluding stubs)."""
        return list(self._vi_nodes.keys())

    def get_vi_source_path(self, vi_name: str) -> Path | None:
        """Get the source file path for a VI (accepts a vi_key, qname, or name)."""
        return self._source_paths.get(self.resolve_vi_name(vi_name))

    def locate_vi_file(
        self, vi_name: str, qualified_path: str | None = None
    ) -> Path | None:
        """Best-effort on-disk ``.vi`` for a SubVI. Decoration-only (SubVI icons /
        click-nav) — never forces a load.

        ``qualified_path`` (the caller's project-relative token, e.g.
        ``/Lib1/Do.vi``) resolves to the EXACT file by PATH: joined to each search
        root, the file AT that path IS the SubVI. This is what disambiguates two
        SubVIs that share a bare filename across libraries (``Lib1/Do.vi`` vs
        ``Lib2/Do.vi``) — a bare-name lookup collides, the path does not. A
        ``<vilib>``/``<userlib>`` token is skipped here (resolved separately).

        Falls back to an already-loaded source, then a bare-filename search of the
        retained search paths (the last-resort path, which CAN collide on
        duplicate names — callers with a ``qualified_path`` never reach it). Under
        a MINIMAL load the SubVIs aren't in ``_source_paths``, so the filename
        search is what lets a project-local SubVI's own ``_ICON.png`` resolve; its
        index is built once, lazily, and cached."""
        if qualified_path:
            rel = qualified_path.replace("\\", "/").lstrip("/")
            if rel and not rel.startswith("<"):
                for root in self._search_paths:
                    candidate = root / rel
                    if candidate.is_file():
                        return candidate
        loaded = self._source_paths.get(self.resolve_vi_name(vi_name))
        if loaded is not None:
            return loaded
        if self._vi_file_index is None:
            index: dict[str, Path] = {}
            for root in self._search_paths:
                try:
                    for found in sorted(root.rglob("*.vi")):
                        index.setdefault(found.name, found)
                except OSError:
                    continue
            self._vi_file_index = index
        return self._vi_file_index.get(vi_name)

    def resolve_library_vi_path(self, qualified_path: str | None) -> Path | None:
        """Resolve a ``<vilib>``/``<userlib>`` token path to a real ``.vi``
        under the user's LOCAL library roots (``set_library_roots`` from
        ``--vilib``/``--userlib`` or auto-detect). Decoration-only (icons).

        Rendering a user's own licensed vi.lib icon on their own machine is
        NOT distribution — lvkit never ships this art, and a hosted service
        simply has no roots set (falls through to None). Loose ``.vi`` only:
        a member packed inside a ``.llb`` isn't addressable as a file and
        returns None (deferred). Token order mirrors the loader's
        ``path_tokens`` (``["<vilib>", "Utility", ...]``)."""
        if not qualified_path:
            return None
        for token, root in (
            ("<vilib>", self._vilib_root),
            ("<userlib>", self._userlib_root),
            ("<instrlib>", self._instrlib_root),
        ):
            if qualified_path.startswith(token) and root is not None:
                rel = qualified_path[len(token) :].lstrip("/\\")
                if ".llb/" in rel.replace("\\", "/").lower():
                    return None  # packed member — needs archive extraction
                candidate = root / rel
                return candidate if candidate.is_file() else None
        return None

    def get_layout(self, vi_name: str) -> Layout | None:
        """Get a VI's block-diagram geometry, or None if the graph was not
        loaded with ``layout=True``. Populated from the same parse (no second
        heap read); the renderer consumes this instead of re-reading the XML."""
        return self._layouts.get(self.resolve_vi_name(vi_name))

    def is_stub_vi(self, vi_name: str) -> bool:
        """Check if a VI is a stub (missing dependency)."""
        return vi_name in self._stubs

    def get_stub_vi_info(self, vi_name: str) -> StubVIInfo | None:
        """Get stub VI info from vilib reference or call site inference."""
        if vi_name not in self._stubs:
            return None

        # First, check vilib resolver for known VIs
        resolver = get_vilib_resolver()
        vilib_info = resolver.get_context(vi_name)
        if vilib_info:
            inputs: list[StubTerminalInfo] = []
            outputs: list[StubTerminalInfo] = []
            for t in vilib_info.get("terminals", []):
                term_type = t.get("type") or "Any"
                if t.get("direction") == "in":
                    inputs.append(StubTerminalInfo(name=t["name"], type=term_type))
                else:
                    outputs.append(StubTerminalInfo(name=t["name"], type=term_type))
            return StubVIInfo(
                name=vi_name,
                vilib_path=vilib_info.get("vilib_path"),
                python_hint=vilib_info.get("python"),
                inputs=inputs,
                outputs=outputs,
                input_types=[i.type for i in inputs],
                output_types=[o.type for o in outputs],
            )

        # Fall back to inferring from call site
        input_types: list[str] = []
        output_types: list[str] = []

        for caller_vi, node_uids in self._vi_nodes.items():
            for uid in node_uids:
                if uid not in self._graph:
                    continue
                gnode = self._graph.nodes[uid].get("node")
                if (
                    isinstance(gnode, VINode)
                    and gnode.name == vi_name
                    and gnode.id != vi_name  # Not the VI definition itself
                ):
                    for term in gnode.terminals:
                        term_type = (
                            term.lv_type.type_descriptor() if term.lv_type else "Any"
                        )
                        if term_type == "unknown":
                            term_type = "Any"
                        if term.direction == "input":
                            input_types.append(term_type)
                        else:
                            output_types.append(term_type)
                    break
            if input_types or output_types:
                break

        return StubVIInfo(
            name=vi_name,
            input_types=input_types,
            output_types=output_types,
        )

    def get_vi_dependencies(self, vi_name: str) -> list[str]:
        """Get VIs that this VI depends on (SubVIs it calls)."""
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._dep_graph:
            return []
        return list(self._dep_graph.successors(vi_name))

    def _dependency_file_path(self, node_key: str) -> Path | None:
        """On-disk path of one dependency — whether it was found and loaded or
        is ABSENT and stubbed. Every dep node is PATH-keyed (finishes #26): its
        key IS the resolved (or intended-local) path, so a loaded dep returns
        its ``_source_paths`` file and a local absent stub returns ``Path(key)``
        directly. Only a pseudo-root ref (``<vilib>``/``<userlib>``/
        ``<instrlib>`` with no configured root) stays qname-keyed — for those
        the recorded ``LinkSavePathRef`` tokens are resolved against a caller by
        pure path math (no root -> None, never in the workspace)."""
        loaded = self._source_paths.get(node_key)
        if loaded is not None:
            return loaded
        if node_key not in self._dep_graph:
            return None
        # Path-keyed stub: the key already IS the intended local path.
        if Path(node_key).is_absolute():
            return _real_member_path(Path(node_key))
        # Qname-keyed pseudo-root stub: resolve its path_tokens against a caller.
        tokens = self._dep_graph.nodes[node_key].get("path_tokens")
        if not tokens:
            return None
        leaf = node_key.rsplit(":", 1)[-1]
        ref = ParsedDependencyRef(name=leaf, path_tokens=list(tokens))
        for caller in self._dep_graph.predecessors(node_key):
            caller_file = self._source_paths.get(caller)
            if caller_file is None:
                continue
            cand = ref.resolve_against(
                caller_file,
                vilib_root=self._vilib_root,
                userlib_root=self._userlib_root,
                instrlib_root=self._instrlib_root,
            )
            if cand is None:
                continue
            # A class/library MEMBER (.vi OR .ctl) names its OWNING container;
            # the member's own file sits beside it (see
            # _redirect_member_beside_container).
            cand = _redirect_member_beside_container(cand, leaf)
            return _real_member_path(cand)
        return None

    def get_dependency_paths(self, vi_name: str) -> set[Path]:
        """On-disk file path of every dependency of ``vi_name`` the current
        load reached — loaded or absent-and-stubbed alike (see
        ``_dependency_file_path``). The set is bounded by the load's
        ``LoadMode``: a MINIMAL load's dep graph holds only the MINIMAL
        closure, so this returns exactly that. Powers the web extension's
        progressive staging loop — load MINIMAL against the files present in
        ``/proj``, fetch these paths (the absent ones) from the workspace,
        reload, repeat until the set stops growing — with no reinvented
        dependency walk: the paths are the ones the loader itself recorded."""
        root = self.resolve_vi_name(vi_name)
        if root not in self._dep_graph:
            return set()
        paths: set[Path] = set()
        for qname in nx.descendants(self._dep_graph, root):
            p = self._dependency_file_path(qname)
            if p is not None:
                paths.add(p)
        return paths

    def get_vi_dependents(self, vi_name: str) -> list[str]:
        """Get VIs that depend on this VI (VIs that call it)."""
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._dep_graph:
            return []
        return list(self._dep_graph.predecessors(vi_name))

    def get_leaf_vis(self) -> list[str]:
        """Get VIs that don't call any SubVIs (leaves of dependency tree)."""
        return [
            n for n in self._dep_graph.nodes() if self._dep_graph.out_degree(n) == 0
        ]

    def has_cycles(self) -> bool:
        """Check if the dependency graph contains any cycles (recursive VIs)."""
        return not nx.is_directed_acyclic_graph(self._dep_graph)

    def get_cycles(self) -> list[list[str]]:
        """Detect and return all cycles in the VI dependency graph."""
        return list(nx.simple_cycles(self._dep_graph))

    def get_generation_order(self) -> Iterator[set[str]]:
        """Yield VI groups in dependency order.

        Returns sets of VI names. Each set can be generated together.
        Mutually recursive VIs are grouped in the same set.
        Dependencies come before dependents.
        """
        if not self._dep_graph.nodes():
            return

        condensation = nx.condensation(self._dep_graph)

        def scc_key(scc_id: int) -> str:
            return min(condensation.nodes[scc_id]["members"])

        scc_order = list(
            reversed(
                list(nx.lexicographical_topological_sort(condensation, key=scc_key))
            )
        )

        vilib_resolver = get_vilib_resolver()

        for scc_id in scc_order:
            members = condensation.nodes[scc_id]["members"]
            convertible_vis = {
                m
                for m in members
                if m not in self._stubs or vilib_resolver.has_implementation(m)
            }
            if convertible_vis:
                yield convertible_vis

    def get_conversion_order(self) -> list[str]:
        """Get VIs in topological order for bottom-up conversion."""
        result = []
        for group in self.get_generation_order():
            result.extend(sorted(group))
        return result

    # === Unified Graph Queries ===

    def _get_vi_nodes(self, vi_name: str) -> set[str]:
        """Get the set of node UIDs belonging to a VI."""
        return self._vi_nodes.get(self.resolve_vi_name(vi_name), set())

    def _get_typed_node(self, uid: str) -> AnyGraphNode | None:
        """Get the typed Pydantic node model for a graph node."""
        if uid not in self._graph:
            return None
        return self._graph.nodes[uid].get("node")

    def get_dataflow_graph(self, vi_name: str) -> nx.DiGraph | None:
        """Get a subgraph view for a VI (TEST-ONLY — no production callers;
        retained for test_parser_regression's dataflow assertion).

        Returns a new DiGraph containing only nodes belonging to this VI,
        with edges between them. Used for backward compatibility.
        """
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return None

        # Build a DiGraph view from the unified MultiDiGraph
        sub = nx.DiGraph()

        for uid in node_uids:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            # Convert typed node to legacy dict format for backward compat
            sub.add_node(uid, **self._typed_node_to_legacy_dict(gnode))

        # Add edges between VI nodes
        for uid in node_uids:
            if uid not in self._graph:
                continue
            for _, dest, key, edata in self._graph.out_edges(uid, data=True, keys=True):
                if dest in node_uids and edata.get("vi") == vi_name:
                    sub.add_edge(uid, dest, **edata)

        return sub

    def _typed_node_to_legacy_dict(self, gnode: AnyGraphNode) -> dict[str, Any]:
        """Convert a typed graph node to the old dict format."""
        result: dict[str, Any] = {
            "name": gnode.name,
            "node_type": gnode.node_type,
        }

        if isinstance(gnode, VINode):
            # Could be a VI definition or SubVI call
            if gnode.id == gnode.vi_path:
                # VI definition — doesn't have a "kind" in the old sense
                result["kind"] = "vi_definition"
            else:
                result["kind"] = "vi"
            result["poly_variant_name"] = gnode.poly_variant_name
            result["terminals"] = [
                self._terminal_to_legacy_dict(t) for t in gnode.terminals
            ]
        elif isinstance(gnode, GraphPrimitiveNode):
            result["kind"] = "primitive"
            result["prim_id"] = gnode.prim_id
            result["prim_index"] = gnode.prim_index
            result["operation"] = gnode.operation
            result["object_name"] = gnode.object_name
            result["object_method_id"] = gnode.object_method_id
            result["properties"] = [{"name": p.name} for p in gnode.properties]
            result["method_name"] = gnode.method_name
            result["method_code"] = gnode.method_code
            result["terminals"] = [
                self._terminal_to_legacy_dict(t) for t in gnode.terminals
            ]
        elif isinstance(gnode, StructureNode):
            result["kind"] = _graph_node_to_op_kind(gnode)
            result["terminals"] = [
                self._terminal_to_legacy_dict(t) for t in gnode.terminals
            ]
        elif isinstance(gnode, ConstantNode):
            result["kind"] = "constant"
            result["value"] = gnode.value
            result["raw_value"] = gnode.raw_value
            result["label"] = gnode.label
            result["lv_type"] = gnode.lv_type

        if gnode.description:
            result["description"] = gnode.description

        return result

    @staticmethod
    def _terminal_to_legacy_dict(t: Terminal) -> dict[str, Any]:
        """Convert Terminal dataclass to legacy dict format."""
        d: dict[str, Any] = {
            "id": t.id,
            "index": t.index,
            "direction": t.direction,
            "type": t.lv_type.type_descriptor() if t.lv_type else "Any",
            "name": t.name,
        }
        if t.lv_type:
            d["lv_type"] = t.lv_type
            if t.lv_type.typedef_path:
                d["typedef_path"] = t.lv_type.typedef_path
            if t.lv_type.typedef_name:
                d["typedef_name"] = t.lv_type.typedef_name
        return d

    def get_inputs(self, vi_name: str, *, public_only: bool = True) -> list[Terminal]:
        """Get VI input terminals.

        Reads from the VINode's terminal list (FPTerminal controls).
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._graph:
            return []

        gnode = self._graph.nodes[vi_name].get("node")
        if not isinstance(gnode, VINode):
            return []

        results = []
        for t in gnode.terminals:
            if t.direction != "input":
                continue
            if public_only and isinstance(t, FPTerminal) and not t.is_public:
                continue
            results.append(t)
        return ordered_interface(results, "input", gnode.connector_pattern_id)

    def get_outputs(self, vi_name: str, *, public_only: bool = True) -> list[Terminal]:
        """Get VI output terminals, in canonical connector-pane order.

        Reads from the VINode's terminal list (FPTerminal indicators).
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._graph:
            return []

        gnode = self._graph.nodes[vi_name].get("node")
        if not isinstance(gnode, VINode):
            return []

        results = []
        for t in gnode.terminals:
            if t.direction != "output":
                continue
            if public_only and isinstance(t, FPTerminal) and not t.is_public:
                continue
            results.append(t)
        return ordered_interface(results, "output", gnode.connector_pattern_id)

    def get_constants(self, vi_name: str) -> list[Constant]:
        """Get all constants in a VI."""
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        results = []
        for uid in sorted(node_uids, key=_node_order_key):
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if not isinstance(gnode, ConstantNode):
                continue
            results.append(
                Constant(
                    id=gnode.id,
                    value=gnode.value,
                    lv_type=gnode.lv_type,
                    display_format=gnode.display_format,
                    raw_value=gnode.raw_value,
                    label=gnode.label,
                    parent=gnode.parent,
                    frame=gnode.frame,
                )
            )
        return results

    def get_labels(self, vi_name: str) -> list[Label]:
        """Get all free labels (block-diagram comments) in a VI."""
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        results = []
        for uid in sorted(node_uids, key=_node_order_key):
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if not isinstance(gnode, LabelNode):
                continue
            results.append(
                Label(
                    id=gnode.id,
                    text=gnode.text,
                    bg_color=gnode.bg_color,
                    attached_to=gnode.attached_to,
                    parent=gnode.parent,
                    frame=gnode.frame,
                )
            )
        return results

    def get_operation_order(
        self, vi_name: str, extra_kinds: tuple[str, ...] = ()
    ) -> list[str]:
        """Get top-level operations in dataflow execution order.

        Returns operation node IDs in the order they should execute
        (topological sort based on wire connections).

        Only includes top-level operations (parent=None). Nested operations
        (inside structures like flat/stacked sequences, loops, cases) are
        handled by their parent structure's codegen — including them here
        creates cycles (structure ↔ child edges) that break topological sort.

        ``extra_kinds`` widens the "real operation" gate (``_OPERATION_KINDS``)
        for ONE call, without touching the shared constant every other caller
        (``top_level_nodes``, this method's own default) relies on.
        The ONLY current use is ``netlist_build._top_level_nodes_gn`` passing
        ``("local_variable",)`` so a top-level local-variable read/write
        (lvnet-only; codegen never sees it) participates in the SAME
        dataflow-topological sort as every other node instead of being
        dropped before ordering even starts.
        """
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        allowed_kinds = (
            _OPERATION_KINDS
            if not extra_kinds
            else (
                *_OPERATION_KINDS,
                *extra_kinds,
            )
        )

        # Get top-level operation node IDs only
        op_ids: set[str] = set()
        for uid in node_uids:
            if uid == vi_name:
                continue  # Skip VINode itself
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            op_kind = _graph_node_to_op_kind(gnode)
            if op_kind in allowed_kinds and gnode.parent is None:
                op_ids.add(uid)

        if not op_ids:
            return []

        # Build operation-level dependency graph from edges. Insert nodes in
        # a deterministic order (op_ids is a hash-randomized set) and break
        # topological-sort ties with the same key, so independent operations
        # land in a stable order — codegen output must be reproducible.
        ordered_ids = sorted(op_ids, key=_node_order_key)
        op_deps = nx.DiGraph()
        op_deps.add_nodes_from(ordered_ids)

        for uid in ordered_ids:
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if dest in op_ids and dest != uid:
                    op_deps.add_edge(uid, dest)

        try:
            return list(
                nx.lexicographical_topological_sort(op_deps, key=_node_order_key)
            )
        except nx.NetworkXUnfeasible:
            return ordered_ids

    def get_wires(
        self,
        vi_name: str,
        include_internal: bool = False,
    ) -> list[Wire]:
        """Get all wires (edges) in a VI's dataflow graph.

        Returns Wire objects with typed WireEnd source/dest.

        An edge is *internal* when it's a structure self-loop (tunnel
        outer<->inner or sRN input->output pairing) — detected as
        ``source.node_id == dest.node_id`` and/or a truthy ``tunnel_type``
        edge attribute. By default internal edges are excluded (renderers
        want external wires only); pass ``include_internal=True`` to get
        the full set, with internal edges first (legacy behavior).
        """
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        # Collect edges: tunnel/internal edges first, then normal edges.
        # node_uids is a hash-randomized set — sort for byte-reproducible
        # output (codegen and the renderer both depend on stable ordering).
        tunnel_edges: list[Wire] = []
        normal_edges: list[Wire] = []

        for uid in sorted(node_uids, key=_node_order_key):
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if edata.get("vi") != vi_name:
                    continue

                src_end = edata.get("source")
                dst_end = edata.get("dest")

                if src_end is None or dst_end is None:
                    continue

                is_internal = (
                    bool(edata.get("tunnel_type")) or src_end.node_id == dst_end.node_id
                )
                if is_internal and not include_internal:
                    continue

                # Look up parent node data for classification kind and names
                from_node = self._get_typed_node(src_end.node_id)
                to_node = self._get_typed_node(dst_end.node_id)

                from_kind = _graph_node_to_op_kind(from_node) if from_node else ""
                to_kind = _graph_node_to_op_kind(to_node) if to_node else ""

                wire = Wire(
                    source=WireEnd(
                        terminal_id=src_end.terminal_id,
                        node_id=src_end.node_id,
                        index=src_end.index,
                        name=from_node.name if from_node else None,
                        parent_kind=from_kind or None,
                    ),
                    dest=WireEnd(
                        terminal_id=dst_end.terminal_id,
                        node_id=dst_end.node_id,
                        index=dst_end.index,
                        name=to_node.name if to_node else None,
                        parent_kind=to_kind or None,
                    ),
                )

                if is_internal:
                    tunnel_edges.append(wire)
                else:
                    normal_edges.append(wire)

        return tunnel_edges + normal_edges

    def iter_nodes(self, vi_name: str) -> list[AnyGraphNode]:
        """List a VI's graph nodes, excluding the VI-definition node itself.

        The VI-definition node's id equals ``vi_name`` and has no diagram
        geometry — callers that need per-node rendering/geometry joins want
        everything else. Sorted by ``_node_order_key`` for deterministic,
        byte-reproducible output (``_vi_nodes`` is a hash-randomized set).
        """
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if not node_uids:
            return []
        result: list[AnyGraphNode] = []
        for uid in sorted(node_uids, key=_node_order_key):
            if uid == vi_name:
                continue
            gnode = self._get_typed_node(uid)
            if gnode is not None:
                result.append(gnode)
        return result

    def get_terminal(self, terminal_id: str) -> Terminal | None:
        """Look up a single Terminal by its fully qualified id.

        Uses ``_term_to_node`` to find the owning node, then scans its
        terminal list for the matching id. Returns None if the terminal
        (or its node) isn't in the graph.
        """
        node_id = self._term_to_node.get(terminal_id)
        if node_id is None:
            return None
        gnode = self._get_typed_node(node_id)
        if gnode is None:
            return None
        for t in gnode.terminals:
            if t.id == terminal_id:
                return t
        return None

    def get_feedback_info(
        self, node_id: str
    ) -> tuple[bool, str | None, int | None] | None:
        """Feedback Node (z^-N) master/slave link for ``node_id`` --
        ``(is_master, partner_uid, delay)``, or ``None`` when ``node_id`` isn't
        a Feedback Node.

        These three facts are stashed as extra networkx node attributes
        (``feedback_is_master``/``feedback_partner``/``feedback_delay`` --
        see ``construction.py``) rather than on the Pydantic ``PrimitiveNode``
        model itself (the graph node stays a plain ``PrimitiveNode`` so
        render/codegen treat it exactly as before). This is the one
        graph-level accessor for them, letting a graph-only consumer (e.g.
        ``netlist.build_netlist_from_graph``) resolve a Feedback Node's
        linked write side directly.
        """
        if node_id not in self._graph:
            return None
        data = self._graph.nodes[node_id]
        is_master = data.get("feedback_is_master")
        if is_master is None:
            return None
        return is_master, data.get("feedback_partner"), data.get("feedback_delay")

    def is_feedback_master(self, node_id: str) -> bool:
        """True if ``node_id`` is a Feedback Node graph node -- EITHER side
        (master or slave) of the pair, i.e. it carries a
        ``feedback_is_master`` graph attribute at all.

        Despite the name (kept for symmetry with the ``feedback_is_master``
        attribute it reads), this does NOT distinguish master from slave --
        every current caller (``describe``'s generic one-liner styling,
        codegen's not-yet-supported gate) only ever needs "is this a Feedback
        Node", never the master/slave value itself. Reuses
        :meth:`get_feedback_info` (presence-only) rather than duplicating its
        node/attribute lookup; use that method directly when the actual
        master/slave/partner/delay facts are needed.
        """
        return self.get_feedback_info(node_id) is not None

    def get_poser_uid(self, node_id: str) -> str | None:
        """The In-Place-Element-Structure decompose/recompose pairing id for
        ``node_id``, or ``None`` when ``node_id`` isn't an IPES border node.

        Stashed as an extra networkx node attribute (``poser_uid`` -- see
        ``construction.py``) rather than a ``PrimitiveNode`` model field, for
        the same reason as ``get_feedback_info`` above.
        """
        if node_id not in self._graph:
            return None
        return self._graph.nodes[node_id].get("poser_uid")

    # === Legacy API ===

    def get_vi_properties(self, vi_name: str) -> VIProperties:
        """The VI's Properties facet (Protection/Execution/Window/…) alone --
        no full ``VIContext`` build. Same source ``get_vi_context`` reads
        (``_vi_properties``); for callers that only need this one facet
        (e.g. ``index/build.py``)."""
        vi_name = self.resolve_vi_name(vi_name)
        return self._vi_properties.get(vi_name, VIProperties())

    def get_vi_health(self, vi_name: str) -> VIHealth:
        """The VI's Health facet (compile-health) alone -- see
        ``get_vi_properties``."""
        vi_name = self.resolve_vi_name(vi_name)
        return self._vi_health.get(vi_name, VIHealth())

    def get_vi_context(self, vi_name: str) -> VIContext:
        """Get complete VI context for code generation.

        Returns a VIContext with inputs, outputs, constants, etc.
        Builds from typed graph nodes.
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._vi_nodes:
            return VIContext(name=vi_name)

        # Build subvi_calls list
        subvi_calls: list[SubVICall] = []
        for uid in self._vi_nodes[vi_name]:
            if uid == vi_name:
                continue
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if isinstance(gnode, VINode) and gnode.id != gnode.vi_path:
                subvi_calls.append(
                    SubVICall(
                        call_name=gnode.name,
                        vi_name=gnode.name,
                    )
                )

        # Build terminals list for skeleton generator
        terminals: list[TerminalRef] = []
        for uid in self._vi_nodes[vi_name]:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            for t in gnode.terminals:
                terminals.append(
                    TerminalRef(
                        id=t.id,
                        parent_id=gnode.id,
                        index=t.index,
                        type=t.lv_type.type_descriptor() if t.lv_type else "Any",
                        name=t.name,
                        direction=t.direction,
                    )
                )

        inputs = list(self.get_inputs(vi_name))
        outputs = list(self.get_outputs(vi_name))
        constants = list(self.get_constants(vi_name))
        labels = list(self.get_labels(vi_name))
        data_flow = list(self.get_wires(vi_name))

        vi_meta = self._vi_metadata.get(vi_name, VIMetadata())

        # Unguarded [vi_name] is safe: the `vi_name not in self._vi_nodes`
        # early-return above guarantees membership in self._graph too (a VI is
        # registered in both at load time).
        vi_gnode = self._graph.nodes[vi_name].get("node")
        is_vinode = isinstance(vi_gnode, VINode)
        pattern_id = vi_gnode.connector_pattern_id if is_vinode else None
        description = vi_gnode.description if is_vinode else None

        return VIContext(
            # Display name (bare filename), not the vi_key path; vi_key is the
            # identity, qualified_name carries the library-qualified form.
            name=vi_gnode.name if is_vinode and vi_gnode.name else vi_name,
            library=vi_meta.library,
            qualified_name=vi_meta.qualified_name,
            inputs=inputs,
            outputs=outputs,
            constants=constants,
            labels=labels,
            terminals=terminals,
            data_flow=data_flow,
            subvi_calls=subvi_calls,
            poly_variants=self.get_poly_variants(vi_name),
            has_parallel_branches=self.has_parallel_branches(vi_name),
            properties=self._vi_properties.get(vi_name, VIProperties()),
            health=self._vi_health.get(vi_name, VIHealth()),
            connector_pattern_id=pattern_id,
            description=description,
        )

    def get_subvi_calls(self, vi_name: str) -> list[SubVICall]:
        """Get SubVIs called by a VI."""
        ctx = self.get_vi_context(vi_name)
        return ctx.subvi_calls

    # === Polymorphic VI Methods ===

    def is_polymorphic(self, vi_name: str) -> bool:
        """Check if a VI is a polymorphic wrapper."""
        return vi_name in self._poly_info

    def get_poly_variants(self, vi_name: str) -> list[str]:
        """Get variants for a polymorphic VI."""
        info = self._poly_info.get(vi_name)
        return info.variants if info else []

    def get_polymorphic_groups(self) -> dict[str, list[str]]:
        """Get all polymorphic VIs and their variants."""
        return {
            vi_name: info.variants
            for vi_name, info in self._poly_info.items()
            if info.variants
        }

    def get_poly_variant_wrappers(self) -> dict[str, str]:
        """Get mapping of variant VI names to their wrapper VI."""
        result: dict[str, str] = {}
        for wrapper, info in self._poly_info.items():
            for variant in info.variants:
                result[variant] = wrapper
        return result

    # === Class Hierarchy Queries ===
    #
    # These read the class/method structure that ``load_lvclass()`` records
    # on ``_dep_graph``: class nodes carry ``parent_class`` (bare ancestor
    # name) + ``fields`` (own private-data fields), and "owns" edges from a
    # class to its method VIs carry the per-method scope/accessor info
    # parsed by ``structure.parse_lvclass()``.

    def list_classes(self) -> list[str]:
        """List all loaded (non-stub) class QNAMES in the dependency graph.

        The node KEY is the path (identity); the qname is the node's display
        identity, read straight off the node (``qname`` attribute) — no side
        table."""
        return sorted(
            d.get("qname", n)
            for n, d in self._dep_graph.nodes(data=True)
            if d.get("node_type") == "class" and n not in self._stubs
        )

    def get_owning_class(self, vi_name: str) -> str | None:
        """Get the class that owns this method VI, via its "owns" edge.

        Returns None if ``vi_name`` isn't a class method VI.
        """
        key = self._owning_class_key(self.resolve_vi_name(vi_name))
        if key is None:
            return None
        return self._dep_graph.nodes[key].get("qname", key)  # display qname

    def _owning_class_key(self, vi_key: str) -> str | None:
        """PATH key of the class that owns this method VI, via its "owns" edge,
        or None. Returns the KEY (path) for edge/attr lookups — distinct from
        :meth:`get_owning_class`, which returns the display qname."""
        if vi_key not in self._dep_graph:
            return None
        for pred in self._dep_graph.predecessors(vi_key):
            if self._dep_graph.nodes[pred].get("node_type") != "class":
                continue
            if (self._dep_graph.get_edge_data(pred, vi_key) or {}).get("rel") == "owns":
                return pred
        return None

    def get_owning_library(self, vi_name: str) -> str | None:
        """Get the ``.lvlib`` that owns this VI directly, via its "owns" edge.

        Exact mirror of ``get_owning_class`` but for ``node_type == "library"``
        predecessors — ``load_lvlib`` records a library node + an "owns" edge
        to each ``Type="VI"`` member the same way ``load_lvclass`` does for
        methods (see ``loading.py``). Returns None if ``vi_name`` isn't a
        library member VI (or the library was never loaded).
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._dep_graph:
            return None
        for pred in self._dep_graph.predecessors(vi_name):
            pdata = self._dep_graph.nodes[pred]
            if pdata.get("node_type") != "library":
                continue
            edata = self._dep_graph.get_edge_data(pred, vi_name) or {}
            if edata.get("rel") == "owns":
                return pdata.get("qname", pred)  # display qname, keyed by path
        return None

    def get_class_hierarchy(self, classname: str) -> ClassHierarchyInfo | None:
        """Get hierarchy info for one loaded class: parent, children,
        documented methods, and fields (own + inherited).

        Returns None if ``classname`` isn't a loaded (non-stub) class node.
        """
        # Resolve the class reference to its PATH-key node (the key is the path;
        # the caller passes a qname/name).
        node_key = self._dep_key_for_ref(classname)
        if (
            node_key is None
            or node_key in self._stubs
            or self._dep_graph.nodes[node_key].get("node_type") != "class"
        ):
            return None
        data = self._dep_graph.nodes[node_key]
        own_qname = data.get("qname", classname)

        # parent_class is recorded as the bare ancestor name (no ".lvclass",
        # no library qualification — see structure._parent_from_link_info). Only
        # surface it if the parent is itself loaded (DOCS contract: a not-loaded
        # parent must not dangle) — follow the recorded parent_key (PATH), and
        # report the parent's display QNAME.
        parent_key = self._parent_class_key(node_key)
        parent_class: str | None = (
            self._dep_graph.nodes[parent_key].get("qname", parent_key)
            if parent_key is not None
            else None
        )

        # Children: invert parent_class across every loaded class. Compare
        # against the bare (unqualified) leaf of our own name, matching how
        # parent_class is recorded. Report each child's display QNAME.
        leaf = own_qname.rsplit(":", 1)[-1]
        own_bare = leaf[: -len(".lvclass")] if leaf.endswith(".lvclass") else leaf
        child_classes = sorted(
            ndata.get("qname", node)
            for node, ndata in self._dep_graph.nodes(data=True)
            if ndata.get("node_type") == "class"
            and node not in self._stubs
            and ndata.get("parent_class") == own_bare
        )

        # Methods: VI nodes owned by this class that are actually documented
        # (present in list_vis() — excludes stub/unresolved method VIs). Report
        # each method's display qname.
        vis = set(self.list_vis())
        methods = sorted(
            self.vi_display_name(succ)
            for succ in self._dep_graph.successors(node_key)
            if self.vi_display_name(succ) in vis
            and (self._dep_graph.get_edge_data(node_key, succ) or {}).get("rel")
            == "owns"
        )

        own_fields: list[ClusterField] = data.get("fields") or []
        inherited_fields: list[ClusterField] = (
            self._class_fields_by_key(parent_key) or []
            if parent_class and parent_key is not None
            else []
        )
        fields = [
            ClassFieldEntry(field=f, inherited=True) for f in inherited_fields
        ] + [ClassFieldEntry(field=f, inherited=False) for f in own_fields]

        return ClassHierarchyInfo(
            classname=own_qname,
            parent_class=parent_class,
            child_classes=child_classes,
            methods=methods,
            fields=fields,
        )

    def _class_node_key(self, classname: str) -> str | None:
        """Resolve a class reference to its LOADED class-node PATH key, or None
        (stub, unresolved, or not a class). The KEY is the path; ``classname``
        is a name, resolved via :meth:`_dep_key_for_ref`. Shared by every
        class-attribute query so they all look up by identity, not by a name
        that no longer keys the graph."""
        key = self._dep_key_for_ref(classname)
        if key is None or key in self._stubs:
            return None
        if self._dep_graph.nodes[key].get("node_type") != "class":
            return None
        return key

    def get_class_parent(self, classname: str) -> str | None:
        """The AUTHORITATIVE parent class of ``classname`` (bare name +
        ``.lvclass``), or None if it's a root / not a loaded class node.

        Unlike ``get_class_hierarchy``, this does NOT gate on the parent being
        loaded: ``parent_class`` is decoded straight from the class file's
        ``NI.LVClass.ParentClassLinkInfo`` (``structure._parent_from_link_info``),
        so it is correct even in a single-VI collision-load graph
        (``index/build.build_one_vi``) that never loads the parent. The index
        uses this so a method's ``class_fact.parent`` is stable across load
        paths — no spurious NULL beside the real value (the WaitOnTestComplete
        duplicate). Docs keep the link-safe, load-gated ``get_class_hierarchy``.
        """
        key = self._class_node_key(classname)
        if key is None:
            return None
        parent_raw = self._dep_graph.nodes[key].get("parent_class")
        if parent_raw and parent_raw != "LabVIEW Object":
            return parent_raw + ".lvclass"
        return None

    def get_class_version(self, classname: str) -> str | None:
        """The class's ``NI.Lib.Version`` (dotted-quad string), or None if
        it's a not-loaded/stub class node or the property was absent.

        Straight passthrough of ``structure.LVClass.version``, recorded on
        the class node at load time (``loading.load_lvclass``).
        """
        key = self._class_node_key(classname)
        if key is None:
            return None
        version = self._dep_graph.nodes[key].get("version")
        return version if isinstance(version, str) else None

    def get_class_ancestors(self, classname: str) -> list[str]:
        """The class's FULL ancestor chain, nearest-first, or ``[]`` if it's a
        not-loaded/stub class node or no ancestor's file resolved.

        Straight passthrough of ``structure.LVClass.ancestors`` — a best-effort
        on-disk resolution done at parse time (see
        ``structure._build_ancestor_chain``); may be a PREFIX of the true
        chain when an ancestor's ``.lvclass`` isn't present in this checkout.
        """
        key = self._class_node_key(classname)
        if key is None:
            return []
        ancestors = self._dep_graph.nodes[key].get("ancestors")
        return list(ancestors) if ancestors else []

    def get_method_access(self, vi_name: str) -> MethodAccessInfo | None:
        """Get access-scope info for a class method VI.

        Returns None if ``vi_name`` isn't a class method VI, or its "owns"
        edge predates the scope/accessor attrs (should not happen for
        anything loaded via ``load_lvclass()``).
        """
        method_key = self.resolve_vi_name(vi_name)
        owner_key = self._owning_class_key(method_key)
        if owner_key is None:
            return None
        edata = self._dep_graph.get_edge_data(owner_key, method_key) or {}
        scope = edata.get("scope")
        if scope is None:
            return None
        return MethodAccessInfo(
            vi_name=vi_name,
            scope=scope,
            is_accessor=bool(edata.get("is_accessor")),
            accessor_type=edata.get("accessor_type"),
            accessor_field=edata.get("accessor_field"),
            is_static=bool(edata.get("is_static")),
            must_override=bool(edata.get("must_override")),
            must_call_parent=bool(edata.get("must_call_parent")),
        )

    def get_method_overrides(self, vi_name: str) -> MethodOverrideInfo | None:
        """Get bidirectional override links for a class method VI.

        Matches by bare method name (e.g. "run.vi") against the immediate
        parent class and each immediate child class. Only links to VIs that
        are themselves documented (in ``list_vis()``). Returns None if
        ``vi_name`` isn't a class method VI, or no override link exists in
        either direction.
        """
        owner = self.get_owning_class(vi_name)
        if owner is None:
            return None
        hierarchy = self.get_class_hierarchy(owner)
        if hierarchy is None:
            return None

        # Bare method name from the VI's own display name (path-key-safe;
        # rsplit(":") would mangle a filesystem path key).
        key = self.resolve_vi_name(vi_name)
        node = self._graph.nodes[key].get("node") if key in self._graph else None
        bare_method = (
            node.name
            if isinstance(node, VINode) and node.name
            else vi_name.rsplit(":", 1)[-1]
        )

        # A parent/child class's version of this method is "documented" when its
        # class-qualified name resolves to a loaded VI. VIs are path-keyed now,
        # so resolve the qname to its vi_key and test membership (works whether
        # the VI was loaded from disk or registered by qname in a test).
        def _loaded(qname: str) -> bool:
            return self.resolve_vi_name(qname) in self._vi_nodes

        overrides: str | None = None
        if hierarchy.parent_class:
            candidate = f"{hierarchy.parent_class}:{bare_method}"
            if _loaded(candidate):
                overrides = candidate

        overridden_by: list[str] = []
        for child in hierarchy.child_classes:
            candidate = f"{child}:{bare_method}"
            if _loaded(candidate):
                overridden_by.append(candidate)
        overridden_by.sort()

        if overrides is None and not overridden_by:
            return None

        return MethodOverrideInfo(
            vi_name=vi_name,
            overrides=overrides,
            overridden_by=overridden_by,
        )


@dataclass(frozen=True)
class ClassContext:
    """The owning ``.lvclass``'s context for a class-method VI -- the ONE
    shared collection of facts both ``describe.py``'s ``## Class`` section
    and ``netlist.py``'s ``NetlistModule.class_context`` (the ``get_context``/
    MCP JSON IR, which carries this dataclass DIRECTLY -- no netlist-local
    wrapper) render, via ``collect_class_context`` -- so the two surfaces
    can't drift on what "the class context" means."""

    owning_class: str
    parent: str | None
    version: str | None
    ancestors: list[str]
    scope: str | None
    is_static: bool
    must_override: bool
    must_call_parent: bool


def collect_class_context(
    graph: InMemoryVIGraph,
    ctx: VIContext,
) -> ClassContext | None:
    """The owning class's context when ``ctx`` is a ``.lvclass`` method VI,
    else None -- the single source both ``describe._describe_class_context``
    and ``netlist._build_class_context`` build on."""
    cls = ctx.library
    if not cls or not cls.endswith(".lvclass"):
        return None
    if not graph._dep_graph.has_node(cls):
        return None

    parent = graph._dep_graph.nodes[cls].get("parent_class")
    # ctx.name is the DISPLAY bare filename (e.g. "run.vi" -- see
    # get_vi_context's docstring comment), which collides across every
    # same-named override in a dynamic-dispatch class hierarchy. Resolve by
    # ctx.qualified_name (the class-qualified identity, e.g.
    # "TestSuite.lvclass:run.vi") instead -- exact and unambiguous.
    access = graph.get_method_access(ctx.qualified_name or ctx.name)
    return ClassContext(
        owning_class=cls,
        parent=parent,
        version=graph.get_class_version(cls),
        ancestors=graph.get_class_ancestors(cls),
        scope=access.scope if access else None,
        is_static=bool(access and access.is_static),
        must_override=bool(access and access.must_override),
        must_call_parent=bool(access and access.must_call_parent),
    )
