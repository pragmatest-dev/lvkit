"""Query mixin for InMemoryVIGraph.

Methods: get_inputs, get_outputs, get_constants, get_wires,
get_operation_order, get_dataflow_graph (test-only), get_vi_context,
get_subvi_calls, list_vis, get_vi_source_path, is_stub,
get_stub_vi_info, dependency graph queries, polymorphic VI methods.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

from ..load_mode import LoadMode
from ..models import ClusterField, FPTerminal, LVType, Terminal, TerminalDirection
from ..parser.models import ParsedDependencyRef, ParsedFrontPanel
from ..type_names import ROOT_CLASS
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .core import _OPERATION_KINDS, _graph_node_to_op_kind, _node_order_key
from .interface_order import ordered_interface
from .models import (
    AnyGraphNode,
    ClassFieldEntry,
    ClassHierarchyInfo,
    Constant,
    ConstantNode,
    GlobalVariableNode,
    Label,
    LabelNode,
    MethodAccessInfo,
    MethodOverrideInfo,
    PolyInfo,
    SharedVariableNode,
    StructureNode,
    StubTerminalInfo,
    StubVIInfo,
    SubVICall,
    TerminalRef,
    VIContext,
    VIDotNetAssemblies,
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
from .node_kinds import OWNERSHIP_RELS, EdgeRel, NodeType, node_type_of
from .recorded_paths import RecordedPaths, resolve_against
from .type_records import TypeRecords, resolve_name, resolve_type

if TYPE_CHECKING:
    from ..parser.layout import Layout
    from .core import InMemoryVIGraph


def _off_pane(terminal: Terminal) -> bool:
    """A front-panel control or indicator not wired to the connector pane."""
    return isinstance(terminal, FPTerminal) and not terminal.is_public


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


class QueryMixin:
    """Mixin providing graph query methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _term_to_node: dict[str, str]
    _dep_graph: nx.DiGraph
    _stubs: set[str]
    _recorded: RecordedPaths
    _poly_info: dict[str, PolyInfo]
    _loaded_vis: set[str]
    _dep_load_mode: dict[str, LoadMode]
    _source_paths: dict[str, Path]
    _vi_display_names: dict[str, str]
    _vi_metadata: dict[str, VIMetadata]
    _vi_properties: dict[str, VIProperties]
    _vi_health: dict[str, VIHealth]
    _vi_dotnet_assemblies: dict[str, VIDotNetAssemblies]
    _layouts: dict[str, Layout]
    _vilib_roots: tuple[Path, ...]
    _userlib_roots: tuple[Path, ...]
    _instrlib_roots: tuple[Path, ...]

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def has_parallel_branches(self, vi_name: str) -> bool: ...
        def class_fields_with_inherited(self, node_key: str) -> list[ClusterField]: ...
        def class_type_key(self, lv_type: LVType) -> str | None: ...
        def _parent_class_key(self, node_key: str) -> str | None: ...
        def key_label(self, key: str) -> str: ...

    # === Dependency Graph Queries ===

    def type_records_of(self, key: str) -> TypeRecords | None:
        """The class, typedef and library records the VI or control at ``key``
        stored when it loaded (``LoadingMixin._store_type_records``); None when
        it stored none (a stub, a file never loaded)."""
        if key not in self._dep_graph:
            return None
        records: TypeRecords | None = self._dep_graph.nodes[key].get("type_records")
        return records

    def recorded_type_key(self, file_key: str, lv_type: LVType) -> str | None:
        """The dependency key of the file the class or typedef ``lv_type`` denotes
        in the VI or control at ``file_key`` (``resolve_type``): tied once, when
        the file loaded, through its link records; None when none gives it."""
        return resolve_type(file_key, lv_type, self.type_records_of)

    def recorded_name_key(self, file_key: str, name: str) -> str | None:
        """The dependency key of the file the qualified name ``name`` (a project
        library's) denotes in the VI at ``file_key`` (``resolve_name``)."""
        return resolve_name(file_key, name, self.type_records_of)

    def loaded_callee_key(self, node: VINode) -> str | None:
        """The ``vi_key`` of the VI whose connector pane a SubVI call node shows
        (``VINode.interface_key``), when that VI is loaded in the graph; None
        when it is absent (a stub, or a load that did not reach it) or the call
        records no path.

        The one way a call node finds its callee: the identity the caller
        recorded for the call, never a lookup by the callee's name."""
        key = node.interface_key
        return key if key is not None and key in self._vi_nodes else None

    def call_qualified_name(self, node: VINode) -> str | None:
        """The qualified name a SubVI call node shows: the name the graph gives
        the VI it calls (``VINode.interface_key``: the instance a polymorphic
        call runs, else the callee; a loaded file's own name, a stub's recorded
        one), so a call and the ``uses :`` entry of its callee spell one
        identity one way; the caller's record when the call has no callee in
        the graph."""
        key = node.interface_key
        if key is not None and key in self._dep_graph:
            qname = self._dep_graph.nodes[key].get("qname")
            if qname:
                return str(qname)
        return node.qualified_name

    def catalog_path(self, node: VINode) -> str | None:
        """The path the vi.lib catalog looks a call up by (see
        ``catalog_path_of``)."""
        return self.catalog_path_of(
            node.interface_key, node.qualified_path, node.vi_path
        )

    def catalog_path_of(
        self, interface_key: str | None, qualified_path: str | None, caller: str
    ) -> str | None:
        """The path the vi.lib catalog looks a call up by: the path recorded for
        the VI whose connector pane the call shows (``interface_key``: the variant
        a polymorphic call resolves to, else the VI it names), the one placement
        gives that VI's module, so a call and its callee's module agree on who
        writes the Python. A call that names no VI in the graph (a ``<vilib>`` call
        with no configured root) has the path ``qualified_path`` its caller
        ``caller`` recorded, resolved against the caller's own."""
        if interface_key is not None:
            recorded = self.recorded_path(interface_key)
            if recorded is not None:
                return recorded
        if not qualified_path:
            return None
        return "/".join(
            resolve_against(qualified_path.split("/"), self._recorded.resolved(caller))
        )

    def vi_display_name(self, vi_name: str) -> str:
        """The VI's human-facing qualified name (``Class.lvclass:vi.vi``, else the
        bare filename) for titles/headers. DISPLAY ONLY — never a lookup key. The
        identity remains the ``vi_key`` (path); this maps that key to its qname so
        the viewer shows the qualified name instead of the absolute source path.
        Falls back to the key's basename when no display name was recorded."""
        display = self._vi_display_names.get(vi_name)
        if display:
            return display
        return Path(vi_name).name or vi_name

    def list_vis(self) -> list[str]:
        """List all VIs in the graph (excluding stubs)."""
        return list(self._vi_nodes.keys())

    def get_vi_source_path(self, vi_name: str) -> Path | None:
        """Get the source file path for the VI at ``vi_name`` (a vi_key)."""
        return self._source_paths.get(vi_name)

    def library_folders(self) -> dict[str, tuple[Path, ...]]:
        """The folders each pseudo path names, in the order they are tried."""
        return {
            "<vilib>": self._vilib_roots,
            "<userlib>": self._userlib_roots,
            "<instrlib>": self._instrlib_roots,
        }

    def library_roots(self) -> list[tuple[str, Path]]:
        """The configured library folders, each with the symbolic root token a
        recorded path names it by (``("<userlib>", Path(".../user.lib"))``), in
        the order they are tried."""
        return [
            (token, root)
            for token, roots in self.library_folders().items()
            for root in roots
        ]

    def recorded_candidates(
        self, ref: ParsedDependencyRef, caller_file: Path
    ) -> list[Path]:
        """Where a record names its file on this machine, in the order they are
        tried: an absolute path as it is, a relative path joined onto the
        caller's folder, a pseudo path joined onto each folder it names. Empty
        for a pseudo path with no folder, or an absolute path with no file here
        (another machine's folder, never shown or staged)."""
        absolute = ref.absolute_path()
        if absolute is not None:
            return [absolute] if absolute.exists() else []
        head = ref.path_tokens[0] if ref.path_tokens else ""
        folders = self.library_folders().get(head)
        if folders is None:
            only = ref.resolve_against(caller_file)
            return [only] if only is not None else []
        keyword = {
            "<vilib>": "vilib_root",
            "<userlib>": "userlib_root",
            "<instrlib>": "instrlib_root",
        }[head]
        found: list[Path] = []
        for folder in folders:
            candidate = ref.resolve_against(caller_file, **{keyword: folder})
            if candidate is not None:
                found.append(candidate)
        return found

    def library_root_of(self, path: Path) -> tuple[str, Path] | None:
        """The LabVIEW library folder (``<vilib>``, ``<userlib>``, ``<instrlib>``)
        the file at ``path`` is in, with that folder, else None."""
        resolved = path.resolve()
        for token, root in self.library_roots():
            if resolved.is_relative_to(root.resolve()):
                return token, root
        return None

    def is_library_file(self, path: Path) -> bool:
        """Whether ``path`` sits under a configured vi.lib / user.lib / instr.lib
        root (the user's own library install, not their project)."""
        return any(path.is_relative_to(root) for _, root in self.library_roots())

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
        for token, root in self.library_roots():
            if qualified_path.startswith(token):
                rel = qualified_path[len(token) :].lstrip("/\\")
                if ".llb/" in rel.replace("\\", "/").lower():
                    return None  # packed member — needs archive extraction
                candidate = root / rel
                if candidate.is_file():
                    return candidate
        return None

    def get_layout(self, vi_name: str) -> Layout | None:
        """Get a VI's block-diagram geometry, or None if the graph was not
        loaded with ``layout=True``. Populated from the same parse (no second
        heap read); the renderer consumes this instead of re-reading the XML."""
        return self._layouts.get(vi_name)

    def is_stub(self, key: str) -> bool:
        """Check if a dep-graph node (a VI, class, typedef, ...) is a stub
        (a missing dependency)."""
        return key in self._stubs

    def recorded_path(self, key: str) -> str | None:
        """The installation-folder path the callers recorded for the VI, class or
        control ``key`` (``<vilib>/addons/TestCase.llb/skip.vi``), as ``/``-joined
        tokens: a path recorded relative to a caller is resolved against the
        caller's own (``RecordedPaths``). None when no caller recorded one that
        starts at a LabVIEW installation folder (``<vilib>``, ``<userlib>``, ...),
        which names the file the same for every caller; a path relative to a caller
        names a place only beside that caller."""
        if key not in self._dep_graph:
            return None
        return self._recorded.path(key)

    def embedding_vi(self, key: str) -> str | None:
        """The key of the loaded VI that holds ``key`` inside its own file: a
        dependency whose recorded path is a place in a VI file (an instance
        saved in its caller, such as an Express VI) has no file of its own.
        None for a dependency that is a file."""
        parent = str(Path(key).parent)
        if parent != key and parent in self._dep_graph and parent not in self._stubs:
            return parent
        return None

    def stub_reason(self, key: str) -> str | None:
        """Why the absent dependency ``key`` is missing, when more than its
        absence (several files of its name, a candidate that cannot be read);
        None otherwise."""
        if key not in self._stubs or key not in self._dep_graph:
            return None
        return self._dep_graph.nodes[key].get("stub_reason")

    def get_stub_vi_info(self, vi_name: str) -> StubVIInfo | None:
        """Get stub VI info from vilib reference or call site inference."""
        if vi_name not in self._stubs:
            return None

        # First, check vilib resolver for known VIs
        resolver = get_vilib_resolver()
        vilib_info = resolver.get_context(self.recorded_path(vi_name))
        if vilib_info:
            inputs: list[StubTerminalInfo] = []
            outputs: list[StubTerminalInfo] = []
            for t in vilib_info.get("terminals", []):
                term_type = t.get("type") or "Any"
                if t.get("direction") == TerminalDirection.INPUT:
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
                # A call to this stub: the call's own key for the VI it calls.
                if isinstance(gnode, VINode) and gnode.interface_key == vi_name:
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
        if vi_name not in self._dep_graph:
            return []
        return list(self._dep_graph.successors(vi_name))

    def dependencies_walked(self, vi_name: str) -> bool:
        """Whether this VI's own dependencies (SubVIs, classes, typedefs) were
        loaded, so ``get_vi_dependencies`` is its full dependency list. False
        for a VI loaded at ``LoadMode.NONE`` (connector pane only) and for one
        this graph never loaded."""
        mode = self._dep_load_mode.get(vi_name)
        return mode is not None and mode is not LoadMode.NONE

    def type_key(self, lv_type: LVType) -> str | None:
        """The dependency key (file path) of the loaded class or typedef that
        ``lv_type`` is (``LVType.file_key``).
        ``None`` when it denotes no loaded one."""
        key = lv_type.file_key
        if key is None or key not in self._dep_graph or key in self._stubs:
            return None
        if self._dep_graph.nodes[key].get("node_type") not in (
            NodeType.CLASS,
            NodeType.TYPEDEF,
        ):
            return None
        return key

    def class_method(self, class_key: str, method: str) -> tuple[str, str] | None:
        """``(owner_key, path)`` of the file that declares ``method`` for the
        class at ``class_key``: the class's own, else the nearest ancestor's,
        following each class's recorded parent key. ``None`` when no class of
        the chain lists it."""
        key: str | None = class_key
        seen: set[str] = set()
        while key is not None and key not in seen and self._dep_graph.has_node(key):
            seen.add(key)
            path = self._dep_graph.nodes[key].get("method_paths", {}).get(method)
            if path is not None:
                return key, path
            key = self._dep_graph.nodes[key].get("parent_key")
        return None

    def node_type(self, key: str) -> NodeType:
        """What the dependency node at ``key`` is: a VI, class, library or
        typedef."""
        return node_type_of(self._dep_graph.nodes[key])

    def is_class(self, key: str) -> bool:
        """True when ``key`` is a class node, loaded or a stub."""
        return (
            key in self._dep_graph
            and self._dep_graph.nodes[key].get("node_type") == NodeType.CLASS
        )

    def is_class_wire(self, lv_type: LVType | None, class_key: str) -> bool:
        """Whether a wire of type ``lv_type`` carries an object of the class at
        ``class_key``: the class file the type's ``file_key`` names is that class
        (never compared by name)."""
        if lv_type is None or lv_type.ref_type != "UDClassInst":
            return False
        return self.class_type_key(lv_type) == class_key

    def class_child_keys(self, key: str) -> list[str]:
        """The keys of the loaded classes whose parent is the class at ``key``,
        sorted."""
        return sorted(
            node
            for node, data in self._dep_graph.nodes(data=True)
            if data.get("node_type") == NodeType.CLASS
            and node not in self._stubs
            and self._parent_class_key(node) == key
        )

    def class_declares(self, class_key: str, file_name: str) -> bool:
        """Whether the class at ``class_key`` lists a VI file named ``file_name``
        itself (not an ancestor)."""
        return file_name in (self._dep_graph.nodes[class_key].get("method_paths") or {})

    def call_edges(self) -> list[tuple[str, str]]:
        """``(importer, imported)`` for every plain reference of a VI to another VI
        or to a class (a call, or a class constant): the edges along which one
        generated module imports another, not the ownership of members or private
        data."""
        return sorted(
            (source, target)
            for source, target, data in self._dep_graph.edges(data=True)
            if data.get("rel") not in OWNERSHIP_RELS
            and self._dep_graph.nodes[target].get("node_type", NodeType.VI)
            in (NodeType.VI, NodeType.CLASS)
        )

    def list_class_keys(self) -> list[str]:
        """The keys of the loaded (non-stub) classes, sorted."""
        return sorted(
            key
            for key, data in self._dep_graph.nodes(data=True)
            if data.get("node_type") == NodeType.CLASS and key not in self._stubs
        )

    def class_own_fields(self, key: str) -> list[ClusterField]:
        """The private-data fields the class at ``key`` declares itself, in
        declaration order: not those it inherits."""
        return list(self._dep_graph.nodes[key].get("fields") or [])

    def class_member_keys(self, key: str) -> list[str]:
        """The keys of the VIs the class at ``key`` owns, in the order its file
        lists them."""
        members = []
        for member in self._dep_graph.successors(key):
            edge = self._dep_graph.get_edge_data(key, member) or {}
            if (
                edge.get("rel") == EdgeRel.OWNS
                and self.node_type(member) is NodeType.VI
            ):
                members.append(member)
        return members

    def class_private_data_key(self, key: str) -> str | None:
        """The dependency key of the control that is the private data of the
        class at ``key``; ``None`` when the class has none loaded."""
        for succ in self._dep_graph.successors(key):
            edge = self._dep_graph.get_edge_data(key, succ) or {}
            if edge.get("rel") == EdgeRel.PRIVATE_DATA:
                return succ
        return None

    def class_parent_key(self, key: str) -> str | None:
        """The dependency key of the loaded parent of the class at ``key``;
        ``None`` for a root class or a parent that is not loaded."""
        return self._parent_class_key(key)

    def class_parent_name(self, key: str) -> str | None:
        """The name the class at ``key`` records for its parent, loaded or not;
        ``None`` for a class that descends directly from LabVIEW Object."""
        parent = self._dep_graph.nodes[key].get("parent_class")
        return parent if parent and parent != ROOT_CLASS else None

    def global_vi_key(self, node: GlobalVariableNode) -> str | None:
        """The dependency key (the Global VI's file path) a global variable node
        refers to: the file the calling VI's link record for the node names.
        ``None`` when the dependencies were not loaded."""
        key = node.global_vi.key
        return key if key is not None and key in self._dep_graph else None

    def shared_variable_library_key(self, node: SharedVariableNode) -> str | None:
        """The dependency key (the library's file path) of the project library of
        a Shared Variable node: the file the calling VI's own records give the
        library's name (``recorded_type_key``). ``None`` when the node records no
        library or no single file is found."""
        if node.library is None:
            return None
        return self.recorded_name_key(node.vi_path, node.library.qualified_name)

    def global_vi_control(
        self, node: GlobalVariableNode
    ) -> tuple[str, FPTerminal] | None:
        """The loaded Global VI a global variable node refers to (its dependency
        key) and that VI's control at the node's control index -- its own
        front-panel control order, the position the node records. ``None`` when
        the Global VI is not loaded or has no control at that position."""
        key = self.global_vi_key(node)
        if key is None or key not in self._graph:
            return None
        vi_node = self._graph.nodes[key].get("node")
        if not isinstance(vi_node, VINode) or vi_node.front_panel is None:
            return None
        uid = vi_node.front_panel.control_uid_at_param_idx(node.param_idx)
        if uid is None:
            return None
        for term in vi_node.terminals:
            if isinstance(term, FPTerminal) and term.fp_dco_uid == uid:
                return key, term
        return None

    def _dependency_file_path(self, node_key: str) -> Path | None:
        """On-disk path of one dependency — whether it was found and loaded or
        is ABSENT and stubbed. Every dep node is PATH-keyed (finishes #26): its
        key IS the resolved (or intended-local) path, so a loaded dep returns
        its ``_source_paths`` file and a local absent stub returns ``Path(key)``
        directly. Only a symbolic-root ref (``<vilib>``, ``<resource>``, … with no
        configured root) stays qname-keyed — for those
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
        # Qname-keyed pseudo-root stub: an installation path resolves by the
        # configured roots; a path relative to a caller, against that caller.
        installed = self._recorded.resolved(node_key)
        leaf = node_key.rsplit(":", 1)[-1]
        for caller in sorted(self._dep_graph.predecessors(node_key)):
            caller_file = self._source_paths.get(caller)
            ref = (
                ParsedDependencyRef(name=leaf, path_tokens=list(installed))
                if installed
                else self._recorded.recorded_by(node_key, caller)
            )
            if caller_file is None or ref is None:
                continue
            # The recorded tokens already end at a class or library member's own
            # file (``recorded_paths.member_tokens``).
            candidates = self.recorded_candidates(ref, caller_file)
            if not candidates:
                continue
            cand = next((c for c in candidates if c.exists()), candidates[0])
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
        root = vi_name
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
        if vi_name not in self._dep_graph:
            return []
        return list(self._dep_graph.predecessors(vi_name))

    def keys_loaded_from(self, root: Path) -> set[str]:
        """The keys of the loaded VIs whose file is ``root`` or lies inside it."""
        root = root.resolve()
        return {
            key
            for key, path in self._source_paths.items()
            if path.resolve() == root or root in path.resolve().parents
        }

    def keys_reached_without_entering(
        self, stop: Callable[[str], bool], requested: Iterable[str] = ()
    ) -> set[str]:
        """The keys reachable from the ones nothing references (every member of a
        cycle that nothing outside it references) and from the ``requested`` ones,
        without going through a key for which ``stop`` is true: such a key is
        reached, but what only it references is not."""
        components = nx.condensation(self._dep_graph)
        reached: set[str] = set()
        pending = [
            key
            for node, data in components.nodes(data=True)
            if components.in_degree(node) == 0
            for key in data["members"]
        ]
        pending.extend(key for key in requested if key in self._dep_graph)
        while pending:
            key = pending.pop()
            if key in reached:
                continue
            reached.add(key)
            if not stop(key):
                pending.extend(self._dep_graph.successors(key))
        return reached

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
                if m not in self._stubs
                or vilib_resolver.has_implementation(self.recorded_path(m))
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
        return self._vi_nodes.get(vi_name, set())

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

    def pane_terminals(
        self, vi_key: str, direction: TerminalDirection, *, public_only: bool = True
    ) -> list[Terminal]:
        """The terminals of the VI loaded under exactly ``vi_key`` in
        ``direction`` (``"input"`` / ``"output"``), in connector-pane order.

        ``vi_key`` is an identity, never a name: nothing is resolved, so a key
        that is not a loaded VI (a stub, a class method no class lists) has no
        terminals instead of matching another VI of the same file name."""
        if vi_key not in self._graph:
            return []
        gnode = self._graph.nodes[vi_key].get("node")
        if not isinstance(gnode, VINode):
            return []
        results = [
            t
            for t in gnode.terminals
            if t.direction == direction
            and not (public_only and isinstance(t, FPTerminal) and not t.is_public)
        ]
        return ordered_interface(results, direction, gnode.connector_pattern_id)

    def get_inputs(self, vi_name: str, *, public_only: bool = True) -> list[Terminal]:
        """The input terminals of the VI at key ``vi_name``."""
        return self.pane_terminals(
            vi_name,
            TerminalDirection.INPUT,
            public_only=public_only,
        )

    def get_outputs(self, vi_name: str, *, public_only: bool = True) -> list[Terminal]:
        """The output terminals of the VI at key ``vi_name``, in canonical
        connector-pane order."""
        return self.pane_terminals(
            vi_name,
            TerminalDirection.OUTPUT,
            public_only=public_only,
        )

    def vi_interface(
        self, vi_key: str
    ) -> tuple[list[Terminal], list[Terminal], list[Terminal], list[Terminal]]:
        """The inputs, off-pane inputs, outputs and off-pane outputs of the VI
        loaded under exactly ``vi_key``, each in interface order: what its
        function's parameters and result fields are named from. Nothing is
        resolved by name (``pane_terminals``)."""
        inputs = self.pane_terminals(vi_key, TerminalDirection.INPUT, public_only=False)
        outputs = self.pane_terminals(
            vi_key, TerminalDirection.OUTPUT, public_only=False
        )
        return (
            [t for t in inputs if not _off_pane(t)],
            [t for t in inputs if _off_pane(t)],
            [t for t in outputs if not _off_pane(t)],
            [t for t in outputs if _off_pane(t)],
        )

    def get_off_pane_inputs(self, vi_name: str) -> list[Terminal]:
        """Front-panel controls not wired to the connector pane."""
        return [t for t in self.get_inputs(vi_name, public_only=False) if _off_pane(t)]

    def get_off_pane_outputs(self, vi_name: str) -> list[Terminal]:
        """Front-panel indicators not wired to the connector pane."""
        return [t for t in self.get_outputs(vi_name, public_only=False) if _off_pane(t)]

    def get_constants(self, vi_name: str) -> list[Constant]:
        """Get all constants in a VI."""
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
                    referenced_vi_key=gnode.referenced_vi_key,
                    unwritable=gnode.unwritable,
                    label=gnode.label,
                    parent=gnode.parent,
                    frame=gnode.frame,
                )
            )
        return results

    def get_labels(self, vi_name: str) -> list[Label]:
        """Get all free labels (block-diagram comments) in a VI."""
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

    def get_feedback_output_bmps(self, node_id: str) -> int | None:
        """The Feedback Node MASTER's own output (``leftFeedback`` dco)
        ``<termBMPs>`` code -- the raw bitmap-selector id LabVIEW uses to pick
        which arrow variant to draw. ``None`` for a non-Feedback-Node, a
        slave, or a master whose heap didn't carry one. See
        ``parser.node_types.FeedbackNode.output_bmps`` for what values mean
        (only 209/211 are verified)."""
        if node_id not in self._graph:
            return None
        bmps = self._graph.nodes[node_id].get("feedback_output_bmps")
        return bmps if isinstance(bmps, int) else None

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
        return self._vi_properties.get(vi_name, VIProperties())

    def get_vi_health(self, vi_name: str) -> VIHealth:
        """The VI's Health facet (compile-health) alone -- see
        ``get_vi_properties``."""
        return self._vi_health.get(vi_name, VIHealth())

    def get_dotnet_assemblies(self, vi_name: str) -> VIDotNetAssemblies:
        """The .NET assemblies the VI calls, and the link records that could not
        be read."""
        return self._vi_dotnet_assemblies.get(vi_name, VIDotNetAssemblies())

    def get_vi_front_panel(self, vi_name: str) -> ParsedFrontPanel | None:
        """The VI's OWN front-panel layout (every control, not just the
        connector-pane subset), or None when the VI is unknown or its node
        carries none."""
        if vi_name not in self._graph:
            return None
        node = self._graph.nodes[vi_name].get("node")
        return node.front_panel if isinstance(node, VINode) else None

    def vi_is_broken(self, vi_name: str) -> bool:
        """Whether the VI was saved broken (its compile-health flags)."""
        health = self._vi_health.get(vi_name)
        return health is not None and health.is_broken

    def get_vi_context(self, vi_name: str) -> VIContext:
        """Get complete VI context for code generation.

        Returns a VIContext with inputs, outputs, constants, etc.
        Builds from typed graph nodes.
        """
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

        inputs, off_pane_inputs, outputs, off_pane_outputs = self.vi_interface(vi_name)
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
            off_pane_inputs=off_pane_inputs,
            off_pane_outputs=off_pane_outputs,
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
        """The keys of the variants of a polymorphic VI that are its loaded or
        absent dependencies, in the order the wrapper records them."""
        info = self._poly_info.get(vi_name)
        return list(info.variant_keys) if info else []

    def get_polymorphic_groups(self) -> dict[str, list[str]]:
        """Every polymorphic VI's key with the keys of its variants."""
        return {
            vi_name: list(info.variant_keys)
            for vi_name, info in self._poly_info.items()
            if info.variant_keys
        }

    def poly_call_choices(self) -> dict[str, dict[str, set[str | None]]]:
        """For each polymorphic VI some node calls: the VIs that call it, each with
        the variants its calls resolve to (``None`` for a call that leaves the
        choice to the wrapper). One pass over every node of every VI."""
        calls: dict[str, dict[str, set[str | None]]] = {}
        for caller, uids in self._vi_nodes.items():
            for uid in uids:
                node = self._get_typed_node(uid)
                if isinstance(node, VINode) and node.callee_key in self._poly_info:
                    calls.setdefault(node.callee_key, {}).setdefault(caller, set()).add(
                        node.variant_key
                    )
        return calls

    def get_poly_variant_wrappers(self) -> dict[str, str]:
        """The key of each variant mapped to the key of its wrapper VI."""
        result: dict[str, str] = {}
        for wrapper, info in self._poly_info.items():
            for variant in info.variant_keys:
                result[variant] = wrapper
        return result

    # === Class Hierarchy Queries ===
    #
    # These read the class/method structure that ``load_lvclass()`` records
    # on ``_dep_graph``: class nodes carry ``parent_class`` (bare ancestor
    # name) + ``fields`` (own private-data fields), and "owns" edges from a
    # class to its method VIs carry the per-method scope/accessor info
    # parsed by ``structure.parse_lvclass()``.

    def class_keys(self) -> list[str]:
        """The keys of all loaded (non-stub) classes in the dependency graph,
        ordered by qualified name, then key."""
        return sorted(
            (
                n
                for n, d in self._dep_graph.nodes(data=True)
                if d.get("node_type") == NodeType.CLASS and n not in self._stubs
            ),
            key=lambda n: (self._dep_graph.nodes[n].get("qname", n), n),
        )

    def list_classes(self) -> list[str]:
        """List all loaded (non-stub) class QNAMES in the dependency graph.

        The node KEY is the path (identity); the qname is the node's display
        identity, read straight off the node (``qname`` attribute) — no side
        table."""
        return sorted(
            d.get("qname", n)
            for n, d in self._dep_graph.nodes(data=True)
            if d.get("node_type") == NodeType.CLASS and n not in self._stubs
        )

    def get_owning_class(self, vi_name: str) -> str | None:
        """Get the class that owns this method VI, via its "owns" edge.

        Returns None if ``vi_name`` isn't a class method VI.
        """
        key = self._owning_class_key(vi_name)
        if key is None:
            return None
        return self._dep_graph.nodes[key].get("qname", key)  # display qname

    def owning_class_key(self, vi_name: str) -> str | None:
        """PATH key of the class that owns this method VI (its "owns" edge), or
        None — the class's identity, for class queries that must not be
        resolved by the class's name (another file of that name may be in the
        graph)."""
        return self._owning_class_key(vi_name)

    def _owner_key(self, key: str, owner_type: NodeType) -> str | None:
        """PATH key of the ``owner_type`` node that owns ``key`` via an "owns"
        edge, or None. The one owner walk behind ``get_owning_class`` /
        ``get_owning_library`` / a typedef's display name."""
        if key not in self._dep_graph:
            return None
        for pred in self._dep_graph.predecessors(key):
            if self._dep_graph.nodes[pred].get("node_type") != owner_type:
                continue
            edge = self._dep_graph.get_edge_data(pred, key) or {}
            if edge.get("rel") in OWNERSHIP_RELS:
                return pred
        return None

    def _owning_class_key(self, vi_key: str) -> str | None:
        """PATH key of the class that owns this method VI, or None. Returns the
        KEY (path) for edge/attr lookups — distinct from :meth:`get_owning_class`,
        which returns the display qname."""
        return self._owner_key(vi_key, NodeType.CLASS)

    def get_owning_library(self, vi_name: str) -> str | None:
        """Get the ``.lvlib`` that owns this VI directly, via its "owns" edge.

        Exact mirror of ``get_owning_class`` but for library owners —
        ``load_lvlib`` records a library node + an "owns" edge to each
        ``Type="VI"`` member the same way ``load_lvclass`` does for methods (see
        ``loading.py``). Returns None if ``vi_name`` isn't a library member VI
        (or the library was never loaded).
        """
        key = self._owner_key(vi_name, NodeType.LIBRARY)
        if key is None:
            return None
        return self._dep_graph.nodes[key].get("qname", key)  # display qname

    def get_class_hierarchy(self, class_key: str) -> ClassHierarchyInfo | None:
        """Get hierarchy info for one loaded class: parent, children,
        documented methods, and fields (own + inherited).

        Returns None if ``class_key`` isn't a loaded (non-stub) class node.
        """
        node_key = self._class_node_key(class_key)
        if node_key is None:
            return None
        data = self._dep_graph.nodes[node_key]
        own_qname = data.get("qname", node_key)

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

        # Children: the loaded classes whose recorded parent is this class.
        # Each is reported by its display QNAME.
        child_classes = sorted(
            self._dep_graph.nodes[child].get("qname", child)
            for child in self._child_class_keys(node_key)
        )

        # Methods: the loaded VIs this class owns (a stub or unresolved method
        # VI is not documented), by key, in display-name order.
        methods = sorted(
            self._class_member_keys(node_key),
            key=lambda key: (self.vi_display_name(key), key),
        )

        own_fields: list[ClusterField] = data.get("fields") or []
        inherited_fields: list[ClusterField] = (
            self.class_fields_with_inherited(parent_key)
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

    def _class_member_keys(self, class_key: str) -> list[str]:
        """The keys of the loaded VIs the class node ``class_key`` owns."""
        return [
            succ
            for succ in self._dep_graph.successors(class_key)
            if succ in self._vi_nodes
            and (self._dep_graph.get_edge_data(class_key, succ) or {}).get("rel")
            == EdgeRel.OWNS
        ]

    def _child_class_keys(self, class_key: str) -> list[str]:
        """The keys of the loaded classes whose recorded parent (``parent_key``)
        is the class at ``class_key``."""
        return [
            node
            for node, ndata in self._dep_graph.nodes(data=True)
            if ndata.get("node_type") == NodeType.CLASS
            and node not in self._stubs
            and ndata.get("parent_key") == class_key
        ]

    def _class_node_key(self, class_key: str) -> str | None:
        """``class_key`` when it is the PATH key of a LOADED class node, else
        None (stub, unknown, or not a class). Shared by every class-attribute
        query."""
        key = class_key if class_key in self._dep_graph else None
        if key is None or key in self._stubs:
            return None
        if self._dep_graph.nodes[key].get("node_type") != NodeType.CLASS:
            return None
        return key

    def get_class_parent(self, class_key: str) -> str | None:
        """The AUTHORITATIVE parent class of the class at ``class_key`` (bare name +
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
        key = self._class_node_key(class_key)
        if key is None:
            return None
        parent_raw = self._dep_graph.nodes[key].get("parent_class")
        if parent_raw and parent_raw != ROOT_CLASS:
            return parent_raw + ".lvclass"
        return None

    def get_class_version(self, class_key: str) -> str | None:
        """The class's ``NI.Lib.Version`` (dotted-quad string), or None if
        it's a not-loaded/stub class node or the property was absent.

        Straight passthrough of ``structure.LVClass.version``, recorded on
        the class node at load time (``loading.load_lvclass``).
        """
        key = self._class_node_key(class_key)
        if key is None:
            return None
        version = self._dep_graph.nodes[key].get("version")
        return version if isinstance(version, str) else None

    def get_class_ancestors(self, class_key: str) -> list[str]:
        """The class's FULL ancestor chain, nearest-first, or ``[]`` if it's a
        not-loaded/stub class node or no ancestor's file resolved.

        Straight passthrough of ``structure.LVClass.ancestors`` — a best-effort
        on-disk resolution done at parse time (see
        ``structure._build_ancestor_chain``); may be a PREFIX of the true
        chain when an ancestor's ``.lvclass`` isn't present in this checkout.
        """
        key = self._class_node_key(class_key)
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
        method_key = vi_name
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
        owner_key = self._owning_class_key(vi_name)
        if owner_key is None or self._class_node_key(owner_key) is None:
            return None

        # An override is the same-named method owned by the parent class or by
        # a child class; each class's methods are the VIs it owns.
        def _bare_name(key: str) -> str:
            node = self._graph.nodes[key].get("node") if key in self._graph else None
            if isinstance(node, VINode) and node.name:
                return node.name
            return self.vi_display_name(key).rsplit(":", 1)[-1]

        bare_method = _bare_name(vi_name)

        def _method_of(class_key: str) -> str | None:
            for member in self._class_member_keys(class_key):
                if _bare_name(member) == bare_method:
                    return member
            return None

        parent_key = self._parent_class_key(owner_key)
        overrides = _method_of(parent_key) if parent_key is not None else None
        overridden_by = sorted(
            (
                found
                for found in (
                    _method_of(child) for child in self._child_class_keys(owner_key)
                )
                if found is not None
            ),
            key=lambda key: (self.vi_display_name(key), key),
        )

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
    vi_key: str,
) -> ClassContext | None:
    """The owning class's context when the VI at ``vi_key`` is a ``.lvclass``
    method (the class its "owns" edge comes from), else None -- the single source
    both ``describe._describe_class_context`` and
    ``netlist._build_class_context`` build on."""
    class_key = graph.owning_class_key(vi_key)
    if class_key is None or graph._class_node_key(class_key) is None:
        return None

    parent = graph._dep_graph.nodes[class_key].get("parent_class")
    access = graph.get_method_access(vi_key)
    return ClassContext(
        owning_class=graph._dep_graph.nodes[class_key].get("qname", class_key),
        parent=parent,
        version=graph.get_class_version(class_key),
        ancestors=graph.get_class_ancestors(class_key),
        scope=access.scope if access else None,
        is_static=bool(access and access.is_static),
        must_override=bool(access and access.must_override),
        must_call_parent=bool(access and access.must_call_parent),
    )
