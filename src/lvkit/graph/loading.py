"""Loading mixin for InMemoryVIGraph.

Methods: load_vi, load_llb, load_lvclass, load_lvlib, load_lvproj, load_typedef,
load_directory, _load_vi_recursive, _load_dependency, _find_subvi,
_resolve_class_vi_path, _resolve_through_llb.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import networkx as nx

from ..extractor import extract_llb, extract_vi_xml
from ..flattened_typedesc import private_data_from_lvclass_xml
from ..load_mode import LoadMode as LoadMode  # re-export for old call sites
from ..models import ClusterField, LVType
from ..parser import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedDependencyRef,
    ParsedFrontPanel,
    ParsedVI,
    ParsedVIMetadata,
    parse_connector_pane_types,
    parse_vi,
    parse_vi_metadata,
)
from ..parser.type_mapping import parse_type_map_rich
from ..structure import (
    LVClass,
    get_project_classes,
    get_project_libraries,
    get_project_vis,
    parse_lvclass,
    parse_lvlib,
    parse_lvproj,
    private_data_field_to_cluster_field,
)
from .models import (
    ExecSystem,
    ExecutionProps,
    InstanceProps,
    KindProps,
    LockState,
    PolyInfo,
    Priority,
    Reentrancy,
    ToolbarProps,
    TypedefStatus,
    VIHealth,
    VIMetadata,
    VIProperties,
    WindowProps,
)
from .op_walk import stamp_nmux_lane_names, stamp_property_value_names
from .parallel_parse import PARALLEL_THRESHOLD, parallel_parse_directory

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..parser.layout import Layout
    from .core import InMemoryVIGraph


# LoadMode is defined in the light ``lvkit.load_mode`` leaf module (imported
# above) so the CLI can name the modes without pulling the graph stack; it is
# re-exported here for the long-standing ``from lvkit.graph.loading import
# LoadMode`` call sites.


def _case_insensitive_match(directory: Path, filename: str) -> Path | None:
    """Single-directory case-insensitive filename match.

    LabVIEW class/typedef names are effectively case-insensitive — a type
    reference's recorded casing (e.g. ``DAQmx Module Runtime.lvclass``, from
    a TypeDesc ``Item``) can differ from the casing the file was actually
    SAVED under on disk (e.g. ``Daqmx Module runtime.lvclass``). An exact
    (case-sensitive) lookup is always tried first by the caller — this is
    only the fallback, so it never changes behavior on a case-sensitive
    filesystem where the exact name already matches.
    """
    target = filename.lower()
    try:
        for entry in directory.iterdir():
            if entry.name.lower() == target:
                return entry
    except OSError:
        return None
    return None


def _redirect_member_beside_container(cand: Path, leaf: str) -> Path:
    """Redirect a resolved dependency path from its OWNING container to the
    member's actual on-disk sibling.

    A class/library MEMBER's LinkSavePathRef tokens describe the owning
    container (e.g. ``['', 'TestCase.lvclass']``); the member's own filename
    is carried separately as ``leaf``, NOT in the tokens. So resolving those
    tokens yields the ``.lvclass``/``.lvlib`` file itself — dispatching that
    as the member's own file fails (e.g. a ``.vi`` member fails
    ``extract_vi_xml``, a ``NotADirectoryError`` joining a ``.ctl`` member
    INTO the class file). Member files are stored BESIDE their container, so
    when ``cand`` resolved to a ``.lvclass``/``.lvlib`` that isn't ``leaf``
    itself, redirect to the sibling ``container_dir/leaf``. Returns ``cand``
    unchanged otherwise. Pure path math — does not check existence; callers
    that need an existence gate (only a resolved sibling counts) apply it
    themselves.
    """
    if cand.suffix.lower() in (".lvclass", ".lvlib") and leaf != cand.name:
        return cand.with_name(leaf)
    return cand


def _build_vi_properties(poly_metadata: dict[str, Any]) -> VIProperties:
    """Build the typed ``VIProperties`` facet from ``parse_vi_metadata``'s
    plain nested dict-of-dicts (``lvsr._parse_lvsr_properties`` output,
    merged into the same dict returned by ``parse_vi_metadata``).

    The parser layer never imports graph models (see graph/models.py's
    module docstring), so it returns plain str/bool/int -- this is the one
    place that wraps them into the typed dataclasses/``LockState`` enum.
    ``kind`` (what ROLE the VI plays) is built here too, as a sub-struct --
    see ``KindProps``.
    """
    execution = dict(cast("dict[str, Any]", poly_metadata.get("execution", {})))
    execution["priority"] = Priority(execution.get("priority", Priority.NORMAL.value))
    execution["reentrancy"] = Reentrancy(
        execution.get("reentrancy", Reentrancy.NON_REENTRANT.value)
    )
    execution["exec_system"] = ExecSystem(
        execution.get("exec_system", ExecSystem.SAME_AS_CALLER.value)
    )
    window = cast("dict[str, Any]", poly_metadata.get("window", {}))
    toolbar = cast("dict[str, Any]", poly_metadata.get("toolbar", {}))
    instance = cast("dict[str, Any]", poly_metadata.get("instance", {}))
    kind = dict(cast("dict[str, Any]", poly_metadata.get("kind", {})))
    kind["typedef_status"] = TypedefStatus(
        kind.get("typedef_status", TypedefStatus.NOT_A_TYPEDEF.value)
    )
    return VIProperties(
        lv_version=cast("str | None", poly_metadata.get("lv_version")),
        vi_type=cast("str | None", poly_metadata.get("vi_type")),
        lock_state=LockState(poly_metadata.get("lock_state", LockState.UNLOCKED.value)),
        execution=ExecutionProps(**execution),
        window=WindowProps(**window),
        toolbar=ToolbarProps(**toolbar),
        instance=InstanceProps(**instance),
        kind=KindProps(**kind),
    )


def _build_vi_health(poly_metadata: dict[str, Any]) -> VIHealth:
    """Build the typed ``VIHealth`` facet (compile-health) -- a SIBLING
    facet to ``VIProperties``, never nested inside it (emergent state, not
    a user-settable property).

    Sourced from the same ``parse_vi_metadata``/``_parse_lvsr_properties``
    nested dict as ``_build_vi_properties``, under its ``"health"`` key.
    """
    health = cast("dict[str, Any]", poly_metadata.get("health", {}))
    return VIHealth(**health)


def _get_fp_root_type_id(fp_xml: Path | None) -> int | None:
    """Read the root TypeID from a .ctl's FPHb (Front Panel Heap).

    The first fPDCO element's typeDesc identifies the control's
    top-level type.  This is TypeID(1) for cluster controls and
    TypeID(2) for enum controls — verified across 83 .ctl files.
    """
    if fp_xml is None or not fp_xml.exists():
        return None
    tree = ET.parse(fp_xml)
    fpdco = tree.find(".//*[@class='fPDCO']")
    if fpdco is None:
        return None
    td = fpdco.find("typeDesc")
    if td is None or not td.text:
        return None
    m = re.search(r"TypeID\((\d+)\)", td.text)
    if m:
        return int(m.group(1))
    return None


# Dependency-load depth ordering. A VI already loaded at a >= depth need not be
# reloaded; a request at a deeper level UPGRADES it (progressive partial->full).
_MODE_RANK = {LoadMode.NONE: 0, LoadMode.MINIMAL: 1, LoadMode.FULL: 2}


def _mode_covers(have: LoadMode | None, want: LoadMode) -> bool:
    """Whether a VI whose deps were loaded at ``have`` already covers ``want``."""
    return have is not None and _MODE_RANK[have] >= _MODE_RANK[want]


def collect_direct_dep_qnames(
    subvi_qualified_names: list[str],
    type_map: dict[int, LVType],
    own_qname: str | None,
) -> set[str]:
    """The base set of dependency qnames a VI records: its SubVI/class call
    table (``subvi_qualified_names``) plus its referenced-type classes/typedefs
    (from ``type_map``), excluding the VI's own qname. Shared by the loader's
    ``_load_vi_recursive`` and ``lvkit.list_deps`` so both provably enumerate
    the SAME base set (``list_deps`` then unions the recorded ``dependency_refs``
    on top, since the render reads those even when the call table is empty)."""
    qnames: set[str] = set()
    for qname in subvi_qualified_names:
        if qname and qname != own_qname:
            qnames.add(qname)
    for lv_type in type_map.values():
        if lv_type.classname and lv_type.classname != "LabVIEW Object":
            qnames.add(lv_type.classname)
        if lv_type.typedef_name:
            qnames.add(lv_type.typedef_name)
    return qnames


def build_dep_ref_map(
    dependency_refs: list[ParsedDependencyRef],
) -> dict[str, ParsedDependencyRef]:
    """The recorded ``LinkSavePathRef`` deps keyed by qualified name (skipping
    refs that carry none). Shared by the loader's ``_load_vi_recursive`` and
    ``lvkit.list_deps`` so both key the same map from the same source."""
    return {ref.qualified_name: ref for ref in dependency_refs if ref.qualified_name}


class LoadingMixin:
    """Mixin providing VI loading methods."""

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
    _dep_load_mode: dict[str, LoadMode]
    _source_paths: dict[str, Path]
    _vi_display_names: dict[str, str]
    _vi_metadata: dict[str, VIMetadata]
    _vi_properties: dict[str, VIProperties]
    _vi_health: dict[str, VIHealth]
    _vilib_root: Path | None
    _userlib_root: Path | None
    _instrlib_root: Path | None
    _layouts: dict[str, Layout]
    _want_layout: bool
    _parse_cache: dict[str, ParsedVI] | None

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def clear(self) -> None: ...
        def _add_vi_to_graph(
            self,
            bd: ParsedBlockDiagram,
            fp: ParsedFrontPanel | None,
            conpane: ParsedConnectorPane | None,
            wiring_rules: dict[int, int],
            vi_key: str,
            display_name: str,
            type_map: dict[int, LVType] | None = None,
            iuse_to_qname: dict[str, str] | None = None,
            iuse_to_qpath: dict[str, str] | None = None,
        ) -> None: ...
        def resolve_dispatch_qnames(self) -> None: ...

    def load_vi(
        self,
        vi_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        search_paths: list[Path] | None = None,
        clear_first: bool = False,
        layout: bool = False,
    ) -> str | None:
        """Load a VI hierarchy into memory.

        Args:
            vi_path: Path to .vi file or *_BDHb.xml file
            mode: dependency depth (see ``LoadMode``). ``FULL`` (default) loads
                the whole SubVI/class-method tree (codegen); ``MINIMAL`` loads
                this VI + its direct SubVIs' connector panes + referenced-type
                fields (render/diff/describe — byte-identical to FULL, far
                cheaper); ``NONE`` loads this VI only.
            search_paths: Directories to search for SubVIs
            clear_first: Clear existing data before loading
            layout: decode + retain block-diagram GEOMETRY for every loaded VI
                (see ``get_layout``). Off by default — only rendering needs it;
                codegen/analysis loads pay nothing. Geometry comes from the same
                parse, never a second heap read.
        """
        vi_path = Path(vi_path)
        self._want_layout = layout

        if clear_first:
            self.clear()

        # Handle .llb containers by extracting members and loading each
        if vi_path.suffix.lower() == ".llb":
            self.load_llb(vi_path, mode, search_paths)
            return

        # Handle .vi files by extracting first
        if vi_path.suffix.lower() == ".vi":
            bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)
        elif vi_path.name.endswith("_BDHb.xml"):
            bd_xml = vi_path
            fp_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", "_FPHb.xml"))
            if not fp_xml.exists():
                fp_xml = None
            main_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", ".xml"))
            if not main_xml.exists():
                main_xml = None
        else:
            raise ValueError(f"Expected .vi or *_BDHb.xml file: {vi_path}")

        # Early return only if already loaded AT >= the requested depth. A VI
        # first seen as a leaf (child NONE) must still UPGRADE when loaded in its
        # own right, or it never gets its own SubVIs/call edges (see
        # _dep_load_mode). Identity is the file path: a ``.vi``'s key is its
        # resolved path, so probe _loaded_vis with that. A raw ``*_BDHb.xml`` has
        # no source path here, so it falls through to _load_vi_recursive, which
        # computes and dedups on the real vi_key.
        if vi_path.suffix.lower() == ".vi":
            probe_key = str(vi_path.resolve())
            if probe_key in self._loaded_vis and _mode_covers(
                self._dep_load_mode.get(probe_key), mode
            ):
                return probe_key

        # Build search paths
        if search_paths is None:
            search_paths = [vi_path.parent]
        # Retain for decoration-only lookups (SubVI icons under a MINIMAL load
        # that never loads the SubVIs themselves — see locate_vi_file).
        self._search_paths = list(search_paths)
        self._vi_file_index = None

        # Parse the VI hierarchy
        # source_dir is the directory of the actual .vi file, not the extracted
        # BD XML (which may be in a temp cache dir). SubVI and type-dependency
        # resolution uses this to find siblings of the original source file.
        source_dir = vi_path.parent if vi_path.suffix.lower() == ".vi" else None
        loaded_key = self._load_vi_recursive(
            bd_xml,
            fp_xml,
            main_xml,
            search_paths=search_paths,
            visited=set(),
            source_dir=source_dir,
            source_override=(vi_path if vi_path.suffix.lower() == ".vi" else None),
            mode=mode,
        )

        # Class-qualify dynamic-dispatch callees now that this VI and all its
        # callees are loaded and the dispatch object's class type has propagated.
        # Graph-level so every consumer (docs, render, MCP) sees the qualified
        # name; idempotent, so the redundant calls from batch loaders are cheap.
        self.resolve_dispatch_qnames()

        # Resolve + stamp nMux/decompose lane (field) NAMES onto
        # Terminal.display_name now that this VI and its referenced-type
        # dependencies are loaded -- the ONE seam netlist/diff/describe read
        # from instead of each re-resolving field names themselves (see
        # op_walk.stamp_nmux_lane_names). Idempotent, so the redundant calls
        # from batch loaders (load_lvclass/load_lvlib/load_directory, which
        # all funnel through load_vi per member) are cheap.
        stamp_nmux_lane_names(cast("InMemoryVIGraph", self))

        # Resolve + stamp Property Node accessed-property NAMES onto their
        # correlated VALUE terminal's Terminal.display_name -- same seam,
        # same idempotency/cheapness rationale as the nMux stamp just above
        # (see op_walk.stamp_property_value_names).
        stamp_property_value_names(cast("InMemoryVIGraph", self))

        return loaded_key

    def _register_dep_node(
        self, key: str, source_file: Path, name: str, qname: str
    ) -> None:
        """Register a dependency node's IDENTITY: its resolved source-path
        ``key`` plus the ``_source_paths`` entry and the bare-name/qname reverse
        indexes ``resolve_vi_name`` walks. Shared by the ``.vi`` loader and the
        ``.lvclass``/``.ctl``/``.lvlib`` loaders so EVERY dep-graph node is keyed
        by path, not qname (finishes #26): path is unique on disk where a qname
        is not (a built copy beside its source twin share a qname), so a class /
        typedef / library resolves to a file exactly as a VI does. The qname is
        recorded as the human-facing display name (never a lookup key)."""
        self._source_paths[key] = source_file
        self._vi_display_names[key] = qname
        # Reverse indexes: bare-name / qname -> [key]. List-valued: genuine
        # on-disk duplicates share a name/qname across distinct keys.
        self._register_dep_name(key, name, qname)

    def _register_dep_name(self, key: str, name: str, qname: str) -> None:
        """Index a dep node's bare-name and qname to its ``key`` for
        ``resolve_vi_name`` — WITHOUT a ``_source_paths`` entry. Used for STUBS
        (absent files): the intended path is known and used as the key so the
        node upgrades by identity when the file appears, but ``_source_paths``
        stays the set of LOADED files (its single job).

        Also records ``name``/``qname`` AS NODE ATTRIBUTES (the key is the path;
        the name/qname ride on the node for display and for CALLER-SCOPED
        confirmation — a caller-scoped edge query reads ``qname`` to confirm
        which of a caller's dep edges a reference names, never a global lookup).
        """
        if key not in self._name_to_keys.setdefault(name, []):
            self._name_to_keys[name].append(key)
        if key not in self._qname_to_keys.setdefault(qname, []):
            self._qname_to_keys[qname].append(key)
        if self._dep_graph.has_node(key):
            node = self._dep_graph.nodes[key]
            node["qname"] = qname
            node["name"] = name

    def _add_stub(
        self,
        key: str,
        node_type: str,
        name: str,
        qname: str | None,
        path_tokens: list[str] | None,
        caller: str | None,
        rel: str | None = None,
    ) -> None:
        """Add one path-keyed STUB node for a dependency whose file is absent
        (or unresolved) -- the single shape shared by every stub-creation site
        in this module: the dep is still NAMED by its recorded/intended path
        (``key``) so a progressive (web) load can see + fetch it, and a later
        real load upgrades it in place (paired with a ``self._stubs.discard``
        at the matching real-load site -- see e.g. ``load_lvclass``,
        ``load_typedef``, ``_load_vi_recursive``).

        Never overwrites an already-loaded (non-stub) node: guarded by
        ``has_node``, so a stub call that races a real load is a no-op on the
        node/``_stubs`` side. ``caller`` is optional -- when given, edges
        ``caller -> key`` (with ``rel`` when one is passed, e.g. ``"owns"``
        for a class/library member; no ``rel`` attribute for a plain SubVI/
        type reference); when None, no edge is added (a caller with no
        dep_graph node of its own, e.g. a ``.lvproj`` member, cannot be
        edged from).
        """
        if not self._dep_graph.has_node(key):
            self._dep_graph.add_node(key, node_type=node_type, path_tokens=path_tokens)
            self._stubs.add(key)
        self._register_dep_name(key, name, qname if qname is not None else name)
        if caller:
            if rel:
                self._dep_graph.add_edge(caller, key, rel=rel)
            else:
                self._dep_graph.add_edge(caller, key)

    def load_lvlib(
        self,
        lvlib_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        search_paths: list[Path] | None = None,
        owner_chain: list[str] | None = None,
    ) -> str:
        """Load all VIs from a .lvlib file. Returns the dep-graph KEY (the
        resolved ``.lvlib`` path) it was registered under — callers add
        ownership/reference edges to THIS key, never to the qname."""
        lvlib_path = Path(lvlib_path)
        lib = parse_lvlib(lvlib_path)

        if search_paths is None:
            search_paths = [lvlib_path.parent]

        chain = list(owner_chain or [])
        lib_name = lib.name + ".lvlib"
        lib_qname = ":".join(chain + [lib_name]) if chain else lib_name

        # PATH is the library's identity (finishes #26): key the node by the
        # resolved .lvlib path, and index its qname/bare-name so a reference
        # resolves to this key. Ownership edges use loader RETURN keys, never a
        # rebuilt member qname (the member VI's own qname may not equal
        # lib_qname + ":" + name — dynamic dispatch, owner-chain nesting).
        lib_path_r = lvlib_path.resolve()
        lib_key = str(lib_path_r)
        self._dep_graph.add_node(lib_key, node_type="library")
        self._register_dep_node(lib_key, lib_path_r, lib_name, lib_qname)
        self._stubs.discard(lib_key)

        for member in lib.members:
            if member.member_type == "VI":
                member_name = Path(member.url).name
                if member_name.lower().endswith(".ctl"):
                    # .ctl members are type definitions, not loadable VIs
                    typedef_qname = lib_qname + ":" + member_name
                    ctl_path = lvlib_path.parent / member.url
                    if not ctl_path.exists():
                        found = self._find_file(
                            member_name, search_paths, lvlib_path.parent
                        )
                        if found:
                            ctl_path = found
                    if ctl_path.exists():
                        ctl_key = self.load_typedef(
                            ctl_path,
                            typedef_qname=typedef_qname,
                            search_paths=search_paths,
                        )
                        self._dep_graph.add_edge(lib_key, ctl_key, rel="owns")
                    else:
                        # Absent .ctl: key the stub by its INTENDED resolved path
                        # (computable without the file), so it stages/upgrades by
                        # identity. Index the qname so refs resolve here.
                        ctl_key = str(ctl_path.resolve())
                        self._add_stub(
                            ctl_key,
                            "typedef",
                            member_name,
                            typedef_qname,
                            path_tokens=None,
                            caller=lib_key,
                            rel="owns",
                        )
                else:
                    member_qname = lib_qname + ":" + member_name
                    vi_path = lvlib_path.parent / member.url
                    if not vi_path.exists():
                        # Relative path doesn't resolve — search by filename
                        vi_path = self._find_subvi(
                            member_name,
                            search_paths,
                            lvlib_path.parent,
                        )
                    if vi_path and vi_path.exists():
                        member_key = self.load_vi(vi_path, mode, search_paths)
                        # Ownership edge — to the member's RETURN key (its path).
                        if member_key and member_key in self._dep_graph:
                            self._dep_graph.add_edge(lib_key, member_key, rel="owns")
                    else:
                        # Absent .vi member: key the stub by its INTENDED
                        # resolved path (computable without the file), so it
                        # stages/upgrades by identity. Mirrors the absent-.ctl
                        # branch above.
                        vi_key = str((lvlib_path.parent / member.url).resolve())
                        self._add_stub(
                            vi_key,
                            "vi",
                            member_name,
                            member_qname,
                            path_tokens=None,
                            caller=lib_key,
                            rel="owns",
                        )
            elif member.member_type == "LVClass":
                class_name = Path(member.url).name
                class_path = lvlib_path.parent / member.url
                if not class_path.exists():
                    found = self._find_file(
                        class_name,
                        search_paths,
                        lvlib_path.parent,
                    )
                    if found:
                        class_path = found
                if class_path.exists():
                    # owner_chain matters here: a library-owned class's own
                    # method VIs embed a FULLY qualified name that DOES carry
                    # the containing library chain (verified against the real
                    # corpus — JKI VI Tester's "ABC - Parentheses (Valid)."
                    # "lvclass", nested under MyParentLibrary.lvlib:MyLibrary.
                    # lvlib, has methods whose OWN metadata.qualified_name is
                    # "MyParentLibrary.lvlib:MyLibrary.lvlib:ABC - Parentheses"
                    # " (Valid).lvclass:setUp.vi" — so cls_qname must match
                    # that chain for the "owns" edge (added inside
                    # load_lvclass) to land on the real VI node. Dropping
                    # owner_chain here silently loses that ownership
                    # attribution instead of fixing anything.
                    member_key = self.load_lvclass(
                        class_path,
                        mode,
                        search_paths,
                        owner_chain=chain + [lib.name + ".lvlib"],
                    )
                    if member_key:
                        self._dep_graph.add_edge(lib_key, member_key, rel="owns")
                else:
                    # Absent .lvclass member: same path-keyed stub treatment.
                    class_key = str((lvlib_path.parent / member.url).resolve())
                    class_qname = lib_qname + ":" + class_name
                    self._add_stub(
                        class_key,
                        "class",
                        class_name,
                        class_qname,
                        path_tokens=None,
                        caller=lib_key,
                        rel="owns",
                    )
            elif member.member_type == "Library":
                lib_name_file = Path(member.url).name
                nested_path = lvlib_path.parent / member.url
                if not nested_path.exists():
                    found = self._find_file(
                        lib_name_file,
                        search_paths,
                        lvlib_path.parent,
                    )
                    if found:
                        nested_path = found
                if nested_path.exists():
                    nested_key = self.load_lvlib(
                        nested_path,
                        mode,
                        search_paths,
                        owner_chain=chain + [lib.name + ".lvlib"],
                    )
                    if nested_key:
                        self._dep_graph.add_edge(lib_key, nested_key, rel="owns")
                else:
                    # Absent nested .lvlib member: same path-keyed stub
                    # treatment.
                    nested_key = str((lvlib_path.parent / member.url).resolve())
                    nested_qname = lib_qname + ":" + lib_name_file
                    self._add_stub(
                        nested_key,
                        "library",
                        lib_name_file,
                        nested_qname,
                        path_tokens=None,
                        caller=lib_key,
                        rel="owns",
                    )

        return lib_key

    def load_lvclass(
        self,
        lvclass_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        search_paths: list[Path] | None = None,
        owner_chain: list[str] | None = None,
    ) -> str:
        """Load a .lvclass file. Returns the dep_graph qname it was
        registered under (``cls_qname``, built from the ON-DISK file's own
        stem casing) — the CALLER may have referenced this class under a
        different casing (a type reference's recorded casing can differ
        from the file's actual casing), so callers that add dep_graph edges
        or aliases must use this return value, never re-derive/assume the
        qname themselves (see ``_load_dependency``'s ``.lvclass`` branch).

        ``MINIMAL`` is an INTERFACE load: add the class's private-data fields
        (and its parent chain's, via walk-up — nMux field indices run into the
        parent+own combined list) but load NONE of its method VIs. Used to
        resolve unbundle-by-name field names for a class referenced only by type
        (e.g. a member VI in a subfolder whose ``.lvclass`` sits one dir up),
        without the expensive method tree. A later reference that resolves the
        class on disk upgrades this placeholder to a full load (see
        ``_load_dependency``). ``FULL``/``NONE`` load every method VI (each at
        that same mode)."""
        lvclass_path = Path(lvclass_path)
        fields_only = mode is LoadMode.MINIMAL
        cls = parse_lvclass(lvclass_path)

        if search_paths is None:
            search_paths = [lvclass_path.parent]

        chain = list(owner_chain or [])
        cls_name = cls.name + ".lvclass"
        cls_qname = ":".join(chain + [cls_name]) if chain else cls_name

        # Add class node to dep_graph with field info.
        fields = self._class_private_data_fields(cls, lvclass_path)
        # PATH is the class's identity (finishes #26): key the node by the
        # resolved .lvclass path and index its qname/bare-name so a type
        # reference resolves to this key. The qname stays the DISPLAY name.
        cls_path_r = lvclass_path.resolve()
        cls_key = str(cls_path_r)
        # The parent class's PATH (its key). Kept on the node so inheritance
        # walks follow it by IDENTITY (a name can hit duplicates; a path
        # can't). None when the parent isn't on disk.
        parent = cls.parent_class
        parent_file, parent_intended = self._resolve_parent(cls, lvclass_path)
        # The parent's KEY is its recorded path whether or not the file is staged
        # yet — so progressive (web) staging can NAME + fetch a not-yet-present
        # parent (an ``.exists()`` gate would drop the whole inherited surface).
        parent_ref = parent_file or parent_intended
        parent_key = str(parent_ref.resolve()) if parent_ref is not None else None
        # The class's member VIs as a ``method-leaf -> resolved .vi path`` map,
        # keyed by the ``.vi`` filename (e.g. ``"GET_LayerData.vi"``) so a caller's
        # dynamic-dispatch call resolves to its file by member-list membership —
        # the authoritative owner (the VIPI/VCTP name only the CALL-SITE class).
        # Recorded even under a MINIMAL field-load (it's just names + paths, no
        # bodies), so the class can supply a called method's path without loading
        # all 27 members. See ``_load_subvi_method_deps``.
        # Record the member's path even when the file is NOT present yet: the
        # ``.lvclass`` URL (``../X.vi``, the class-as-directory quirk) gives its
        # INTENDED path, which is what progressive (web) staging needs to NAME +
        # fetch a not-yet-staged member. An ``.exists()``-gated resolve would name
        # nothing until the file is already staged — chicken-and-egg — so the web
        # closure would converge WITHOUT the SubVIs (they'd never be fetched).
        method_paths: dict[str, str] = {}
        for method in cls.methods:
            vp = self._resolve_class_vi_path(lvclass_path.parent, method.vi_path)
            if vp is None:
                vp = self._intended_class_vi_path(lvclass_path.parent, method.vi_path)
            method_paths[Path(method.vi_path).name] = str(vp)
        self._dep_graph.add_node(
            cls_key,
            node_type="class",
            fields=fields,
            parent_class=cls.parent_class,
            parent_key=parent_key,
            fields_only=fields_only,
            version=cls.version,
            ancestors=cls.ancestors,
            method_paths=method_paths,
        )
        self._register_dep_node(cls_key, cls_path_r, cls_name, cls_qname)
        self._stubs.discard(cls_key)

        # Field-load the parent chain (no methods) so inherited nMux field
        # indices AND inherited-method calls resolve; dedup on the parent's
        # PATH key. Edge class -> parent so the parent (and thus the render's
        # inherited surface) is reachable in the dep graph and gets staged.
        # Runs for BOTH a fields_only (MINIMAL) and a FULL class load — a FULL
        # load's method-loading loop below still needs the inheritance edge
        # (and the parent's method_paths) for dispatch resolution, so the
        # parent is never skipped just because this load went deeper.
        if parent_file is not None:
            assert parent_key is not None  # parent_file present => key set
            # Load when absent OR when a prior round left it a STUB (present
            # now — upgrade it so its method_paths populate; the has_node
            # guard alone would leave the stub un-upgraded and its inherited
            # methods forever unresolvable).
            if not self._dep_graph.has_node(parent_key) or parent_key in self._stubs:
                self.load_lvclass(
                    parent_file,
                    LoadMode.MINIMAL,
                    search_paths=search_paths,
                )
                self._stubs.discard(parent_key)
            self._dep_graph.add_edge(cls_key, parent_key)
        elif parent_key is not None:
            # Parent recorded but not staged yet (web round 1): stub it by its
            # intended path so the closure NAMES it and staging fetches it; a
            # later round loads it for real and its methods become resolvable.
            # Not routed through _add_stub: this stub carries a ``fields_only``
            # node attribute the shared helper doesn't set, so it stays manual
            # (kept shape-consistent with the other stub sites: an explicit
            # ``path_tokens=None`` and the same register/edge order).
            assert parent is not None  # parent_key set => parent name is known
            if not self._dep_graph.has_node(parent_key):
                self._dep_graph.add_node(
                    parent_key,
                    node_type="class",
                    fields_only=True,
                    path_tokens=None,
                )
                self._stubs.add(parent_key)
                self._register_dep_name(parent_key, parent + ".lvclass", parent)
            self._dep_graph.add_edge(cls_key, parent_key)

        if fields_only:
            return cls_key

        for method in cls.methods:
            vi_path = self._resolve_class_vi_path(lvclass_path.parent, method.vi_path)
            if vi_path and vi_path.exists():
                method_key = self.load_vi(vi_path, mode, search_paths)
                # Ownership edge — carries scope/accessor info for docs (class
                # landing pages, access-level badges). A method VI's identity is
                # its file path (the vi_key it loaded under), which is unique, so
                # attach the edge straight to it — no bare-name collision to lose
                # (path identity subsumes the old #18 source-path fallback).
                if method_key and method_key in self._dep_graph:
                    self._dep_graph.add_edge(
                        cls_key,
                        method_key,
                        rel="owns",
                        scope=method.scope,
                        is_accessor=method.is_accessor,
                        accessor_type=method.accessor_type,
                        accessor_field=method.accessor_field,
                        is_static=method.is_static,
                        must_override=method.must_override,
                        must_call_parent=method.must_call_parent,
                    )

        return cls_key

    def _class_private_data_fields(
        self, cls: LVClass, lvclass_path: Path
    ) -> list[ClusterField]:
        """The class's private-data fields, tried in fallback order.

        The LVPrivateDataField -> ClusterField conversion (incl. nested
        sub-field typing) is shared with the render resolver's
        VI-own-inline-copy fallback — see
        structure.py::private_data_field_to_cluster_field. Three sources, in
        order:

        1. An inline "class private data" cluster found in a method VI's VCTP
           (structure.py::_parse_private_data_fields).
        2. A separate control (.ctl) typedef this class stores its private
           data as instead (same .ctl extraction as load_typedef); its file
           may differ from the class's recorded logical name.
        3. A source-only .lvclass: neither of the above carries the cluster
           (e.g. the pre-refactor layout where the private data is embedded
           ONLY as the flattened NI.LVClass.FlattenedPrivateDataCTL property
           in the class XML) — recovered straight from that property.
           Best-effort: a malformed/older embedded control must never break
           class loading.
        """
        fields = [
            private_data_field_to_cluster_field(f) for f in cls.private_data_fields
        ]
        if not fields:
            ctl = self._find_private_data_ctl(lvclass_path.parent, cls.private_data_ctl)
            if ctl is not None:
                ctl_fields, _ = self._ctl_root_fields(ctl)
                if ctl_fields:
                    fields = ctl_fields
        if not fields:
            try:
                pd_fields = private_data_from_lvclass_xml(lvclass_path)
            except Exception:
                logger.debug(
                    "flattened private-data recovery failed for %s",
                    lvclass_path,
                    exc_info=True,
                )
                pd_fields = []
            fields = [private_data_field_to_cluster_field(f) for f in pd_fields]
        return fields

    def _resolve_parent(
        self, cls: LVClass, lvclass_path: Path
    ) -> tuple[Path | None, Path | None]:
        """The parent class's file (when present) and its INTENDED path
        (present or not) -> ``(parent_file, parent_intended)``.

        PREFERS the parent's own recorded URL (a DIRECT relative path — the
        parent commonly lives in a sibling subtree an up-tree walk can't
        reach, e.g. ``../../Layer/Layer.lvclass``); falls back to a walk-up
        by name only when no URL was recorded. Both None when the class has
        no parent (or its parent is the root ``LabVIEW Object``).
        """
        parent = cls.parent_class
        parent_file: Path | None = None
        parent_intended: Path | None = None
        if parent and parent != "LabVIEW Object":
            if cls.parent_url:
                # LabVIEW ``.lvclass`` URLs are relative to the class FILE
                # treated as a directory (members read ``../X.vi``), so
                # resolve against ``lvclass_path`` itself — that absorbs the
                # leading ``..``.
                parent_intended = (lvclass_path / cls.parent_url).resolve()
                if parent_intended.exists():
                    parent_file = parent_intended
            if parent_file is None and parent_intended is None:
                parent_file = self._walk_up_find(
                    lvclass_path.parent, parent + ".lvclass"
                )
        return parent_file, parent_intended

    def _walk_up_find(
        self,
        start_dir: Path,
        filename: str,
        max_levels: int = 16,
    ) -> Path | None:
        """Walk up from ``start_dir`` looking for ``filename`` — e.g. an owning
        ``<Class>.lvclass`` that sits above a member VI's ``private/``/``protected/``
        subfolder. Filesystem-only (no parsing), bounded by ``max_levels`` so a
        deep tree can't run away. Returns the first match, else None. Falls
        back to a case-insensitive match at each level (see
        ``_case_insensitive_match``) when the exact name isn't there."""
        d = start_dir.resolve()
        for _ in range(max_levels):
            candidate = d / filename
            if candidate.exists():
                return candidate
            ci_match = _case_insensitive_match(d, filename)
            if ci_match is not None:
                return ci_match
            if d.parent == d:
                break
            d = d.parent
        return None

    def _intended_class_vi_path(self, cls_dir: Path, relative_path: str) -> Path:
        """The resolved INTENDED path of a class-method VI from its recorded
        ``.lvclass`` relative URL — WITHOUT requiring the file to exist, so an
        ABSENT member's stub key and a later PRESENT load's key are the SAME
        resolved string. A ``.lvclass`` treats its OWN file as a directory, so a
        member URL ``../Member.vi`` actually lives IN the class dir (the leading
        ``..`` is that quirk, not a real level-up): strip it and resolve against
        ``cls_dir``. This is the form ``_resolve_class_vi_path`` returns for a
        PRESENT member, so absent stubs upgrade in place. Shared by the
        absent-member branch of ``load_lvclass``'s ``method_paths`` loop."""
        rel = relative_path
        while rel.startswith("../"):
            rel = rel[3:]
        return (cls_dir / rel).resolve()

    def _resolve_class_vi_path(self, cls_dir: Path, relative_path: str) -> Path | None:
        """Resolve a class member VI from its ``.lvclass`` relative URL. Prefers
        the INTENDED (class-as-directory) path — the same key the absent-member
        stub uses, so they never diverge — then the literal ``..``-preserving
        path as a fallback for the rare member that truly lives one level up."""
        intended = self._intended_class_vi_path(cls_dir, relative_path)
        if intended.exists():
            return intended
        direct = (cls_dir / relative_path).resolve()
        if direct != intended and direct.exists():
            return direct
        return None

    def load_lvproj(
        self,
        lvproj_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs referenced by a .lvproj file."""
        lvproj_path = Path(lvproj_path)
        proj = parse_lvproj(lvproj_path)
        proj_dir = lvproj_path.parent

        if search_paths is None:
            search_paths = [proj_dir]

        # Absent project members are stubbed by their INTENDED resolved path
        # (mirroring load_lvlib's absent-member branches) so they're still
        # NAMED — get_dependency_paths / is_stub_vi / progressive staging can
        # see them and upgrade by identity when the file appears. UNLIKE
        # load_lvlib's members, there is no ownership edge: a ``.lvproj`` is a
        # build/reference manifest, not itself a dep_graph node — load_lvproj
        # creates no project node to edge these members FROM.
        for lib_name, lib_path in get_project_libraries(proj):
            if lib_path.exists():
                self.load_lvlib(lib_path, mode, search_paths)
            else:
                key = str(lib_path.resolve())
                self._add_stub(
                    key, "library", lib_name, lib_name, path_tokens=None, caller=None
                )

        for class_name, class_path in get_project_classes(proj):
            if class_path.exists():
                self.load_lvclass(class_path, mode, search_paths)
            else:
                key = str(class_path.resolve())
                self._add_stub(
                    key,
                    "class",
                    class_name,
                    class_name,
                    path_tokens=None,
                    caller=None,
                )

        for vi_name, vi_path in get_project_vis(proj):
            if vi_path.exists():
                self.load_vi(vi_path, mode, search_paths)
            else:
                key = str(vi_path.resolve())
                self._add_stub(
                    key, "vi", vi_name, vi_name, path_tokens=None, caller=None
                )

    def load_directory(
        self,
        dir_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from a directory recursively."""
        dir_path = Path(dir_path)

        if search_paths is None:
            search_paths = [dir_path]

        # Sorted: load order decides which same-named file claims a name first
        # when SubVI deps resolve, so filesystem order made loads irreproducible.
        vi_paths = sorted(dir_path.rglob("*.vi"))

        # Above a size threshold, pre-parse (XML -> ParsedVI) across a process
        # pool -- profiling showed parse_vi dominates a whole-directory load
        # (~62% of wall time on JKI VI Tester). This ONLY warms
        # self._parse_cache; the serial loop below, and every dependency-load
        # code path it triggers, is completely unchanged -- graph assembly
        # order stays exactly the sorted order iterated here, so codegen
        # determinism is unaffected by which worker finishes when. Layout
        # loads (rendering) skip this: parallel_parse_directory always parses
        # layout=False.
        if len(vi_paths) >= PARALLEL_THRESHOLD and not self._want_layout:
            self._parse_cache = parallel_parse_directory(vi_paths)

        try:
            for vi_path in vi_paths:
                self.load_vi(vi_path, mode, search_paths)

            for llb_path in sorted(dir_path.rglob("*.llb")):
                if llb_path.is_file():
                    self.load_llb(llb_path, mode, search_paths)
        finally:
            self._parse_cache = None

    def load_llb(
        self,
        llb_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from an LLB container archive.

        If ``llb_path`` is already a directory (pre-extracted, as in the
        OpenG samples), delegates to ``load_directory()``.  Otherwise extracts
        the binary LLB to a cache directory and loads each extracted ``.vi``.
        """
        llb_path = Path(llb_path)
        if llb_path.is_dir():
            self.load_directory(llb_path, mode, search_paths)
            return

        try:
            cache_dir = extract_llb(llb_path)
        except RuntimeError:
            return  # Unreadable LLB — silently skip

        if search_paths is None:
            search_paths = [cache_dir]

        for vi_path in cache_dir.glob("*.vi"):
            try:
                self.load_vi(vi_path, mode, search_paths)
            except (RuntimeError, ValueError):
                pass  # Skip VIs that have no block diagram (compiled-only)

    def _resolve_through_llb(self, candidate: Path) -> Path | None:
        """Walk ``candidate``'s path components for a ``.llb`` file.

        If any component resolves to an ``.llb`` binary archive, extract it to
        the cache and return the path to the requested member VI inside the
        cache directory.  Returns ``None`` if no ``.llb`` component exists or
        the member is not found.
        """
        parts = candidate.parts
        for i, part in enumerate(parts):
            if part.lower().endswith(".llb"):
                llb_path = Path(*parts[: i + 1])
                if not llb_path.is_file():
                    continue
                # Remaining components give the member name
                if i + 1 >= len(parts):
                    continue
                try:
                    cache_dir = extract_llb(llb_path)
                except RuntimeError:
                    return None
                member_path = cache_dir / Path(*parts[i + 1 :])
                return member_path if member_path.exists() else None
        return None

    def _pick_link_ref(
        self,
        candidates: list[ParsedDependencyRef] | None,
        caller_file: Path,
    ) -> ParsedDependencyRef | None:
        """Pick the best link-table ref for a leaf-filename lookup out of
        several candidates that share the leaf.

        ``parse_link_path_refs`` dedups on ``(leaf, *path_tokens)`` and
        deliberately keeps distinct same-leaf refs — different link records
        can name the same on-disk leaf via DIFFERENT recorded paths. Prefer
        the one whose recorded path actually resolves to an EXISTING file
        against ``caller_file``; otherwise fall back to the first candidate
        (list order — deterministic, unlike a set)."""
        if not candidates:
            return None
        for ref in candidates:
            resolved = ref.resolve_against(
                caller_file,
                vilib_root=self._vilib_root,
                userlib_root=self._userlib_root,
                instrlib_root=self._instrlib_root,
            )
            if resolved is not None and resolved.exists():
                return ref
        return candidates[0]

    def _load_vi_recursive(
        self,
        bd_xml: Path,
        fp_xml: Path | None,
        main_xml: Path | None,
        search_paths: list[Path],
        visited: set[str],
        source_dir: Path | None = None,
        source_override: Path | None = None,
        mode: LoadMode = LoadMode.FULL,
    ) -> str | None:
        """Recursively load a VI and its SubVIs.

        ``source_override`` pins the VI's identity path (its ``vi_key``) to the
        exact file the caller loaded from. The TOP-LEVEL ``load_vi`` passes its
        ``vi_path`` here so the computed ``vi_key`` matches its early-return
        probe key exactly, instead of being re-derived from the (possibly
        different) BD-heap filename. Returns the ``vi_key`` (path), or None if
        already visited.
        """
        # Parse VI using unified parse_vi(). When rendering (layout=True), the
        # geometry is decoded from this SAME parse and retained — no second read.
        # A directory load's optional parallel pre-parse pass (see
        # parallel_parse.py) may have already computed this VI's ParsedVI --
        # reuse it (it's a pure function of the same XML files, so identical
        # to a fresh call) instead of re-parsing. Also serves a repeat visit
        # of the same VI within one load (as another VI's SubVI dependency)
        # for free, since the cache isn't popped. Any miss (VI outside the
        # pre-parsed set, layout wanted, or the pre-parse pass never ran)
        # falls straight through to the normal serial parse.
        cached_vi = self._parse_cache.get(str(bd_xml)) if self._parse_cache else None
        vi = (
            cached_vi
            if cached_vi is not None
            else parse_vi(
                bd_xml=bd_xml,
                fp_xml=fp_xml if fp_xml and fp_xml.exists() else None,
                main_xml=main_xml if main_xml and main_xml.exists() else None,
                layout=self._want_layout,
            )
        )

        metadata = vi.metadata
        bd = vi.block_diagram
        fp = vi.front_panel
        conpane = vi.connector_pane

        unqualified_name = bd_xml.name.replace("_BDHb.xml", ".vi")
        # own_qname is this VI's (possibly NON-unique) qualified name — kept only
        # for the self-dependency compare below and the qname reverse index. It
        # is NOT the identity: two on-disk copies (a source VI + its stripped
        # built copy) can share a qname while being distinct files.
        own_qname = metadata.qualified_name or unqualified_name

        # caller_file is the VI file itself (not its directory): each
        # leading empty in a LinkSavePathRef pops one level from it. It also
        # doubles as the source-path fallback below: metadata.source_path is
        # only set when a real ``.vi`` sibling sits next to the extracted
        # heap XML, which is true for dependencies resolved from an on-disk
        # search path but NOT for the top-level VI passed to load_vi() (its
        # heap XML lives in a temp extraction dir with no ``.vi`` sibling).
        caller_file = (
            source_dir / unqualified_name
            if source_dir is not None
            else bd_xml.parent / unqualified_name
        )

        # vi_key is the VI's IDENTITY: its canonical source-path string. Path is
        # unique on disk where a qname may not be, so keying every per-VI store
        # by vi_key makes loading CONFLUENT — two copies get DISTINCT keys and
        # both load fully, instead of the first-seen one clobbering the other
        # (the bug where FS enumeration order changed the loaded VI set).
        if source_override is not None:
            source_file = source_override
        elif metadata.source_path:
            source_file = Path(metadata.source_path)
        elif caller_file.exists():
            source_file = caller_file
        else:
            source_file = bd_xml
        vi_key = str(source_file.resolve())

        if vi_key in visited:
            return None

        # Already loaded at a >= depth → done. Otherwise fall through to UPGRADE:
        # re-walk this VI's dependencies at the deeper mode (adds the missing
        # SubVI loads + call edges). The graph-node build below is guarded so it
        # runs once; the node/metadata adds are idempotent.
        if vi_key in self._loaded_vis and _mode_covers(
            self._dep_load_mode.get(vi_key), mode
        ):
            return vi_key

        visited.add(vi_key)

        self._register_dep_node(vi_key, source_file, unqualified_name, own_qname)

        # Retain geometry decoded during this parse (layout=True loads only).
        if vi.layout is not None:
            self._layouts[vi_key] = vi.layout

        # Parse wiring rules from main XML
        wiring_rules: dict[int, int] = {}
        if main_xml and main_xml.exists() and conpane:
            wiring_rules = parse_connector_pane_types(main_xml, conpane)

        type_map = metadata.type_map

        # Parse VI metadata for polymorphic info and library membership
        if main_xml and main_xml.exists():
            poly_metadata = parse_vi_metadata(main_xml)
            if poly_metadata.get("is_polymorphic"):
                self._poly_info[vi_key] = PolyInfo(
                    variants=poly_metadata.get("poly_variants", []),
                    selectors=poly_metadata.get("poly_selectors", []),
                )
            self._vi_metadata[vi_key] = VIMetadata(
                library=poly_metadata.get("library"),
                qualified_name=poly_metadata.get("qualified_name"),
                owning_libraries=poly_metadata.get("owning_libraries", []),
                description=poly_metadata.get("description"),
            )
            self._vi_properties[vi_key] = _build_vi_properties(poly_metadata)
            self._vi_health[vi_key] = _build_vi_health(poly_metadata)

        # Add to dependency graph
        self._dep_graph.add_node(vi_key)

        # Mark as loaded, and record the depth we're loading its deps at — BEFORE
        # the dependency walk below, so a cyclic callee that re-enters this VI at
        # the same (or lower) depth is covered and short-circuits instead of
        # recursing forever. Recording it after the walk would reintroduce the
        # infinite recursion the old unconditional _loaded_vis check prevented.
        self._loaded_vis.add(vi_key)
        self._stubs.discard(vi_key)
        self._dep_load_mode[vi_key] = mode

        # Build dep_ref_map from recorded LinkSavePathRef data.
        # Used for both dependency loading and iUse path diagnostics.
        dep_ref_map: dict[str, ParsedDependencyRef] = (
            build_dep_ref_map(metadata.dependency_refs)
            if main_xml and main_xml.exists()
            else {}
        )

        # Load all dependencies through the single generic walker. NONE stops
        # here — the target VI only, no dependencies at all.
        if mode is not LoadMode.NONE and main_xml and main_xml.exists():
            # Collect all dependency qnames: SubVI/class refs + type_map deps.
            # MINIMAL and FULL both collect the same set — what differs is DEPTH
            # (see _load_dependency): MINIMAL leaf-loads each SubVI (its connector
            # pane, for param names; no recursion into ITS SubVIs) and field-loads
            # each class (no methods); FULL loads the whole transitive tree.
            all_dep_qnames = collect_direct_dep_qnames(
                metadata.subvi_qualified_names, type_map, own_qname
            )
            # Union the recorded LinkSavePathRef deps too. The call table
            # (subvi_qualified_names) / type_map can be EMPTY on some VIs — e.g.
            # a file whose VITS block doesn't decode (issue #29's Test.vi) — yet
            # the diagram still references those SubVIs and the render resolves +
            # reads each one. dependency_refs is LabVIEW's own complete record of
            # what the file references, so enumerating only the call table lets a
            # referenced SubVI vanish from _dep_graph entirely (never even a
            # stub) and the render degrades to a bare box. Progressive load then
            # resolves each as usual: present -> path-keyed node, absent -> qname
            # stub carrying its path_tokens (as good as known until we know
            # better). Guards test_issue_corpus.py::…issue29….
            all_dep_qnames.update(dep_ref_map)

            # A leaf-filename -> recorded-path index from LabVIEW's link tables:
            # supplies a recorded PATH for a type-derived class/typedef dep that
            # carries no LinkSavePathRef of its own, so it resolves by path
            # instead of a name-search (path-driven closure, web == desktop). It
            # only ENRICHES deps already in all_dep_qnames — never adds one — so a
            # library-prefixed dep's bare leaf can't shadow it into a duplicate.
            # List-valued: parse_link_path_refs dedups on (leaf, *path_tokens),
            # so several refs can legitimately share a leaf via DISTINCT recorded
            # paths — grouping (not last-wins) keeps every one available to
            # _pick_link_ref below.
            link_index: dict[str, list[ParsedDependencyRef]] = {}
            for r in metadata.link_path_refs:
                link_index.setdefault(r.name, []).append(r)

            # Sorted: dependency load ORDER decides which same-named candidate
            # file claims a name first, so a hash-ordered set here made the
            # loaded VI SET non-deterministic across runs (146 vs 109 on the
            # JKI Programmatic API tree). Deterministic order → reproducible docs.
            for qname in sorted(all_dep_qnames):
                dep_ref = dep_ref_map.get(qname) or self._pick_link_ref(
                    link_index.get(qname.rsplit(":", 1)[-1]), caller_file
                )
                self._load_dependency(
                    qname,
                    dep_ref,
                    caller_file,
                    search_paths,
                    caller_qname=vi_key,
                    mode=mode,
                )

            # Resolve SubVI CALLEES (incl. dynamic-dispatch methods absent from
            # _LIbd.bin) to their files via the now-loaded class member lists —
            # AFTER the class deps above are in the graph. See the method.
            self._load_subvi_method_deps(vi_key, metadata, search_paths, mode)

        # Build map of iUse uid → fully qualified on-disk path for diagnostics.
        iuse_to_qpath: dict[str, str] = {}
        for uid, qname in metadata.iuse_to_qualified_name.items():
            ref = dep_ref_map.get(qname)
            if ref and ref.path_tokens:
                iuse_to_qpath[uid] = "/".join(ref.path_tokens)

        # Build the unified graph for this VI AFTER all callees are loaded.
        # Callees are in the graph → cross-VI edges work → types propagate.
        # Guarded: on an UPGRADE re-run the VI's nodes already exist (they're
        # built for every depth, even a leaf/NONE load), so only the dependency
        # walk above needed to re-run — re-adding would duplicate nodes.
        if vi_key not in self._vi_nodes:
            self._add_vi_to_graph(
                bd,
                fp,
                conpane,
                wiring_rules,
                vi_key,
                unqualified_name,
                type_map,
                iuse_to_qname=metadata.iuse_to_qualified_name,
                iuse_to_qpath=iuse_to_qpath,
            )

        return vi_key

    def _ctl_root_fields(
        self,
        ctl_path: Path,
    ) -> tuple[list[ClusterField] | None, dict[int, LVType]]:
        """The root cluster fields + full type_map of a control (.ctl). The
        single ``.ctl`` field-extraction, shared by ``load_typedef`` and the
        class private-data fallback in ``load_lvclass`` (a class whose private
        data is a ``.ctl`` control, not an inline cluster). Returns
        ``(None, {})`` when the control's XML can't be produced."""
        # Guard the whole extract+parse. A control can extract to XML that is
        # then malformed, so parse_type_map_rich / _get_fp_root_type_id can raise
        # ET.ParseError (a SyntaxError subclass — NOT an OSError/ValueError) or
        # ValueError. Honor this method's documented "(None, {}) on failure"
        # contract for those too (load_typedef and lvkit.list_deps rely on it).
        try:
            _, fp_xml, main_xml = extract_vi_xml(ctl_path)
            if not (main_xml and main_xml.exists()):
                return None, {}
            type_map = parse_type_map_rich(main_xml)
            root_type_id = _get_fp_root_type_id(fp_xml)
        except (RuntimeError, OSError, ValueError, ET.ParseError):
            return None, {}
        if root_type_id is None:
            root_type_id = 1  # cluster control default
        root = type_map.get(root_type_id)
        return (root.fields if root is not None else None), type_map

    def _find_private_data_ctl(
        self,
        class_dir: Path,
        recorded_name: str | None,
    ) -> Path | None:
        """The class's private-data control FILE. LabVIEW records a LOGICAL name
        for it (e.g. ``<Class>.ctl``) that can differ from the on-disk file
        (conventionally ``Data.ctl``): try the recorded name, then the
        ``Data.ctl`` convention, then the sole ``.ctl`` in the class dir. None
        when it can't be pinned down (multiple ambiguous controls / none)."""
        for cand in (recorded_name, "Data.ctl"):
            if cand and (class_dir / cand).exists():
                return class_dir / cand
        ctls = sorted(class_dir.glob("*.ctl"))
        return ctls[0] if len(ctls) == 1 else None

    def load_typedef(
        self,
        ctl_path: Path | str,
        typedef_qname: str | None = None,
        search_paths: list[Path] | None = None,
    ) -> str:
        """Load a .ctl typedef and add it to the dep_graph with its fields.
        Returns the dep-graph KEY (the resolved .ctl path) it registered under.

        Mirrors load_vi / load_lvclass / load_lvlib for consistency: PATH is the
        typedef's identity (finishes #26), and the qname/bare-name index to that
        key so a type reference resolves here.
        """
        ctl_path = Path(ctl_path)
        if search_paths is None:
            search_paths = [ctl_path.parent]

        qname = typedef_qname or ctl_path.name
        # PATH-keyed even when absent: the intended resolved path is computable
        # and lets the stub upgrade by identity when the file appears.
        ctl_path_r = ctl_path.resolve()
        ctl_key = str(ctl_path_r)
        if self._dep_graph.has_node(ctl_key) and ctl_key not in self._stubs:
            return ctl_key

        if not ctl_path.exists():
            # Not routed through _add_stub: load_typedef returns the key and
            # is edged by its CALLER, not internally (unlike a caller-edging
            # stub site) — kept shape-consistent (explicit path_tokens) with
            # the other stub sites regardless.
            self._dep_graph.add_node(ctl_key, node_type="typedef", path_tokens=None)
            self._stubs.add(ctl_key)
            self._register_dep_name(ctl_key, ctl_path.name, qname)
            return ctl_key

        fields, type_map = self._ctl_root_fields(ctl_path)
        if type_map:
            self._dep_graph.add_node(ctl_key, node_type="typedef", fields=fields)
            self._register_dep_node(ctl_key, ctl_path_r, ctl_path.name, qname)
            self._stubs.discard(ctl_key)

            # Recurse: load any class/typedef deps referenced in this ctl's
            # type_map (e.g. a cluster field whose type is an lvclass or ctl).
            for lv_type in type_map.values():
                if lv_type.classname and lv_type.classname != "LabVIEW Object":
                    self._load_dependency(
                        lv_type.classname,
                        None,
                        ctl_path,
                        search_paths,
                        caller_qname=ctl_key,
                    )
                if lv_type.typedef_name:
                    self._load_dependency(
                        lv_type.typedef_name,
                        None,
                        ctl_path,
                        search_paths,
                        caller_qname=ctl_key,
                    )
        else:
            # XML not produced — stub with what we know
            self._dep_graph.add_node(ctl_key, node_type="typedef", path_tokens=None)
            self._stubs.add(ctl_key)
            self._register_dep_name(ctl_key, ctl_path.name, qname)
        return ctl_key

    def _resolve_dependency_path(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
        search_paths: list[Path],
    ) -> Path | None:
        """Resolve one dependency ref to an on-disk file — the pure
        PATH-RESOLUTION half of ``_load_dependency`` (no graph mutation, no
        ``.lvclass`` walk-up): prefer the recorded LinkSavePathRef (+ LLB
        archive fallback + class/library MEMBER-VI redirect), else fall back
        to a name-based search. Shared with ``lvkit.list_deps`` (the web
        staging closure) so that reader can never drift from what this loader
        actually resolves — callers that also need the ``.lvclass`` walk-up
        fallback (``_load_dependency`` itself; ``list_deps``) call
        ``_walk_up_find`` themselves afterward, since its DISPOSITION differs
        per caller (``_load_dependency`` field-loads it at a fixed MINIMAL
        mode; ``list_deps`` just wants the file path).
        """
        leaf = qualified_name.rsplit(":", 1)[-1]

        # Resolve path: prefer the recorded ref, fall back to name-based search.
        resolved: Path | None = None
        if dep_ref is not None:
            candidate = dep_ref.resolve_against(
                caller_file,
                vilib_root=self._vilib_root,
                userlib_root=self._userlib_root,
                instrlib_root=self._instrlib_root,
            )
            if candidate is not None:
                if candidate.exists():
                    resolved = candidate
                else:
                    llb_resolved = self._resolve_through_llb(candidate)
                    if llb_resolved is not None:
                        resolved = llb_resolved

        # Member VIs (and .ctl typedefs) are stored beside their owning class/
        # library container, not inside it (see
        # _redirect_member_beside_container) — redirect so a resolved
        # container path becomes the actual sibling file. If the sibling
        # isn't there, drop to None and let the name-based search below find
        # it.
        if resolved is not None:
            redirected = _redirect_member_beside_container(resolved, leaf)
            if redirected != resolved:
                resolved = redirected if redirected.exists() else None

        if resolved is None:
            if leaf.endswith(".vi"):
                resolved = self._find_subvi(leaf, search_paths, caller_file.parent)
            else:
                resolved = self._find_file(leaf, search_paths, caller_file.parent)

        return resolved

    def _intended_dep_path(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
    ) -> Path | None:
        """The resolved-or-INTENDED absolute path of a dependency, even when the
        file is ABSENT — used to key a STUB by identity so it upgrades
        progressively (inv. 3) and the web loop learns the path to fetch. Pure
        path math (``resolve_against``), no file need exist. Returns None for a
        pseudo-root ref (``<vilib>``/``<userlib>``/``<instrlib>`` with no
        configured root): those have no local path and must never be staged into
        ``/proj``. Applies the same member-beside-container redirect (G1) as
        ``_resolve_dependency_path`` so a member ``.ctl``/``.vi`` sits beside its
        container, not joined into it."""
        if dep_ref is None:
            return None
        cand = dep_ref.resolve_against(
            caller_file,
            vilib_root=self._vilib_root,
            userlib_root=self._userlib_root,
            instrlib_root=self._instrlib_root,
        )
        if cand is None:
            return None
        leaf = qualified_name.rsplit(":", 1)[-1]
        cand = _redirect_member_beside_container(cand, leaf)
        return cand.resolve()

    def _load_dependency(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
        search_paths: list[Path],
        caller_qname: str | None = None,
        mode: LoadMode = LoadMode.FULL,
    ) -> None:
        """Load one dependency by its LabVIEW qualified name and optional path ref.

        Single entry point for all dependency loading: SubVI calls, class refs,
        typedef refs, and library refs all funnel through here.

        Uses the recorded LinkSavePathRef for resolution (exact path, no scanning).
        Falls back to name-based search only when no path ref is available or the
        recorded path doesn't exist on disk (e.g. <userlib> refs without a root).

        LabVIEW's one-qname-per-memory invariant means the dep_graph node check
        at the top is the definitive dedup — resolution only runs on first visit.
        """
        leaf = qualified_name.rsplit(":", 1)[-1]
        resolved = self._resolve_dependency_path(
            qualified_name, dep_ref, caller_file, search_paths
        )

        # PATH is the identity (finishes #26). The dep-graph key is ALWAYS the
        # resolved file path — never the qname or the bare name. A built copy
        # beside its source twin shares a qname; two genuinely different VIs
        # share a bare name (Lib1/Do.vi vs Lib2/Do.vi); ONLY the path is unique,
        # so ONLY the path dedups. Dedup on has_node(<path>).
        if resolved is not None:
            dep_key = str(resolved.resolve())
            if self._dep_graph.has_node(dep_key):
                if caller_qname:
                    self._dep_graph.add_edge(caller_qname, dep_key)
                # A fields-only class placeholder can be UPGRADED by a resolvable
                # ref — fall through to a full load; otherwise it's done.
                if not self._dep_graph.nodes[dep_key].get("fields_only"):
                    return
            # else fall through to the dispatch below, which loads and keys by
            # this same path (idempotently).
        else:
            # UNRESOLVED on disk. A .lvclass referenced only by type (its file
            # sits one dir up from a member VI) is field-loaded via walk-up so
            # by-name field names resolve; anything else stubs by its INTENDED
            # path so it upgrades by identity when the file appears (progressive
            # loading). A pseudo-root ref (<vilib>/... with no configured root)
            # has no local path -> keyed by qname, carrying its path_tokens.
            if leaf.endswith(".lvclass"):
                found = self._walk_up_find(caller_file.parent, leaf)
                if found is not None:
                    found_key = str(found.resolve())
                    if not self._dep_graph.has_node(found_key):
                        parts = qualified_name.split(":")
                        owner_chain = parts[:-1] if len(parts) > 1 else None
                        self.load_lvclass(
                            found,
                            LoadMode.MINIMAL,
                            search_paths=search_paths,
                            owner_chain=owner_chain,
                        )
                    if caller_qname:
                        self._dep_graph.add_edge(caller_qname, found_key)
                    return
            node_type = (
                "class"
                if leaf.endswith(".lvclass")
                else "typedef"
                if leaf.endswith(".ctl")
                else "library"
                if leaf.endswith(".lvlib")
                else "vi"
            )
            computed = self._intended_dep_path(qualified_name, dep_ref, caller_file)
            stub_key = str(computed) if computed is not None else qualified_name
            path_tokens = (
                list(dep_ref.path_tokens) if dep_ref and dep_ref.path_tokens else None
            )
            self._add_stub(
                stub_key,
                node_type,
                leaf,
                qualified_name,
                path_tokens=path_tokens,
                caller=caller_qname,
            )
            return

        # Dispatch by extension to the matching public loader.
        if leaf.endswith(".vi"):
            self._leaf_load_vi(
                resolved, qualified_name, caller_qname, dep_ref, search_paths, mode
            )
        elif leaf.endswith(".lvclass"):
            parts = qualified_name.split(":")
            owner_chain = parts[:-1] if len(parts) > 1 else None
            # MINIMAL: field-load the class (no method bodies) — enough for
            # by-name field names. FULL: load its whole method tree for codegen.
            loaded_qname = self.load_lvclass(
                resolved,
                mode,
                search_paths=search_paths,
                owner_chain=owner_chain,
            )
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, loaded_qname)
            # See the walk-up branch above: alias rather than let a
            # differently-cased reference create a second, empty dep_graph
            # node that shadows the real (populated) one.
            if qualified_name != loaded_qname:
                self._qualified_aliases[qualified_name] = loaded_qname
        elif leaf.endswith(".lvlib"):
            lib_key = self.load_lvlib(resolved, mode, search_paths=search_paths)
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, lib_key)
            if qualified_name != lib_key:
                self._qualified_aliases[qualified_name] = lib_key
        elif leaf.endswith(".ctl"):
            ctl_key = self.load_typedef(
                resolved,
                typedef_qname=qualified_name,
                search_paths=search_paths,
            )
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, ctl_key)
            if qualified_name != ctl_key:
                self._qualified_aliases[qualified_name] = ctl_key
        else:
            # Unknown extension — stub rather than guess, but key by the resolved
            # file path (it exists here) so it is identified consistently.
            unknown_key = str(resolved.resolve())
            path_tokens = (
                list(dep_ref.path_tokens) if dep_ref and dep_ref.path_tokens else None
            )
            self._add_stub(
                unknown_key,
                "unknown",
                leaf,
                qualified_name,
                path_tokens=path_tokens,
                caller=caller_qname,
            )

    def _leaf_load_vi(
        self,
        resolved: Path,
        qualified_name: str,
        caller_qname: str | None,
        dep_ref: ParsedDependencyRef | None,
        search_paths: list[Path],
        mode: LoadMode,
    ) -> str | None:
        """Leaf-load one SubVI file and edge it to its caller — the shared body of
        ``_load_dependency``'s ``.vi`` branch and the class-method resolver.

        MINIMAL leaf-loads: the SubVI's own connector pane (for the caller's
        param-name hovers + icon) but NOT its own SubVIs (child NONE), so the
        transitive tree is never walked; FULL recurses fully. On a parse failure
        the SubVI is stubbed PATH-keyed so it upgrades by identity later.
        """
        try:
            bd_xml, fp_xml, main_xml = extract_vi_xml(resolved)
            child_mode = LoadMode.FULL if mode is LoadMode.FULL else LoadMode.NONE
            loaded_name = self._load_vi_recursive(
                bd_xml,
                fp_xml,
                main_xml,
                search_paths=search_paths,
                visited=set(),
                source_dir=resolved.parent,
                mode=child_mode,
            )
            if loaded_name:
                if caller_qname:
                    self._dep_graph.add_edge(caller_qname, loaded_name)
                if qualified_name != loaded_name:
                    self._qualified_aliases[qualified_name] = loaded_name
                if (
                    dep_ref is not None
                    and dep_ref.path_tokens
                    and dep_ref.path_tokens[0] == "<vilib>"
                ):
                    self._cache_vilib_terminal_layout(loaded_name, dep_ref)
            return loaded_name
        except (RuntimeError, OSError):
            # The file isn't present (e.g. web round 1: it hasn't been staged
            # yet). Stub it BY PATH and EDGE it to the caller, so the recorded
            # path surfaces in get_dependency_paths — otherwise the extension has
            # nothing to stage and the SubVI never appears. A later round finds
            # the file and this upgrades to a real leaf-load.
            stub_key = str(resolved.resolve())
            self._stub_subvi(qualified_name, dep_ref=dep_ref, key=stub_key)
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, stub_key)
            return None

    def _load_subvi_method_deps(
        self,
        vi_key: str,
        metadata: ParsedVIMetadata,
        search_paths: list[Path],
        mode: LoadMode,
    ) -> None:
        """Resolve the VI's class-method SubVI CALLEES — including the dispatch
        methods the ``_LIbd.bin`` IUVI table omits — to their files by MEMBER-LIST
        membership over the classes this VI already depends on, and edge each to
        the caller so the render draws its icon + interface.

        A callee name (from the ``_LIvi.bin`` VIPI table + the IUVI map) resolves
        against the ``method_paths`` of the VI's class deps: the owner is the class
        whose member list HAS the method. A CROSS-CLASS dynamic-dispatch call —
        a method whose owner is a class this VI does NOT path-record (its object
        was derived mid-diagram, so nothing stores that class's path) — is left
        UNRESOLVED on purpose: MINIMAL does not follow into it (its ``dynLink``
        carries no static path, only the wire's class resolves it), so the render
        draws a named box and a later click loads it as its own root.
        """
        called: set[str] = set(metadata.subvi_method_names)
        for qname in metadata.iuse_to_qualified_name.values():
            called.add(qname.rsplit(":", 1)[-1])
        if not called:
            return

        # Most-derived class first, then by path: when a method is OVERRIDDEN
        # (declared in a child and its parent, both loaded), the child's copy is
        # the icon that would actually run, and the tie-break is deterministic.
        class_keys = sorted(
            (
                s
                for s in self._dep_graph.successors(vi_key)
                if self._dep_graph.nodes[s].get("node_type") == "class"
            ),
            key=lambda s: (-len(self._dep_graph.nodes[s].get("ancestors", [])), s),
        )
        for name in sorted(called):
            if self._resolve_method_in_hierarchy(
                name, class_keys, vi_key, search_paths, mode
            ):
                continue

    def _resolve_method_in_hierarchy(
        self,
        name: str,
        class_keys: list[str],
        vi_key: str,
        search_paths: list[Path],
        mode: LoadMode,
    ) -> bool:
        """Resolve one callee against each class dep AND its ANCESTOR chain (a
        dispatch method not declared on the wire's class is INHERITED from a
        parent — that owner's file is the icon to show), then leaf-load + edge it.
        The ancestor hop follows the parent's recorded ``parent_key`` — a direct
        path link, never a name-search. Returns whether it resolved."""
        for cls_key in class_keys:
            k: str | None = cls_key
            seen: set[str] = set()
            while k is not None and k not in seen and self._dep_graph.has_node(k):
                seen.add(k)
                path_str = self._dep_graph.nodes[k].get("method_paths", {}).get(name)
                if path_str is not None:
                    self._leaf_load_vi(
                        Path(path_str),
                        f"{Path(k).name}:{name}",
                        vi_key,
                        None,
                        search_paths,
                        mode,
                    )
                    return True
                k = self._dep_graph.nodes[k].get("parent_key")
        return False

    def _stub_subvi(
        self,
        name: str,
        key: str,
        dep_ref: ParsedDependencyRef | None = None,
    ) -> None:
        """Record a SubVI reference that could not be resolved as a stub.

        Carries path_tokens from the parser's LinkSavePathRef when
        available — diagnostics-only, never gates code generation. ``key`` is
        the SubVI's resolved (intended) path, so the stub is PATH-keyed
        (upgrades by identity) and the qname/name index to it. Edging to the
        caller is the caller's job (``_leaf_load_vi``'s except branch), not
        this method's — so ``caller`` is None here.
        """
        path_tokens = (
            list(dep_ref.path_tokens) if dep_ref and dep_ref.path_tokens else None
        )
        self._add_stub(
            key,
            "vi",
            name.rsplit(":", 1)[-1],
            name,
            path_tokens=path_tokens,
            caller=None,
        )

    def _cache_vilib_terminal_layout(
        self,
        vi_name: str,
        dep_ref: ParsedDependencyRef,
    ) -> None:
        """Cache terminal layout for a vi.lib VI loaded from disk.

        Writes to .lvkit/vilib/<Category>.json and updates _index.json.
        Only runs if .lvkit/ exists (created by `lvkit setup`).
        Skips VIs already in the bundled JSON with complete terminals.
        Safe to call repeatedly — overwrites stale entries.
        """
        from ..project_store import find_project_store
        from ..vilib_resolver import get_resolver as get_vilib_resolver

        store = find_project_store()
        if store is None:
            return

        vilib_dir = store / "vilib"

        # Derive category from first path component after <vilib>
        tokens = dep_ref.path_tokens
        category = tokens[1] if len(tokens) > 1 else "other"

        # Build relative vi_path from tokens (skip <vilib>)
        vi_path_rel = "/".join(tokens[1:]) if len(tokens) > 1 else vi_name

        # Get the VI's front panel terminals from the graph
        vi_node = self._graph.nodes.get(vi_name, {}).get("node")
        if vi_node is None:
            return

        terminals_data = []
        for term in getattr(vi_node, "terminals", []):
            if term.index is None or term.index < 0:
                continue
            t: dict[str, object] = {
                "name": term.name or "",
                "index": term.index,
                "direction": term.direction,
            }
            if term.lv_type:
                # LVType has no ``.name``; its type-name string is
                # ``underlying_type`` (e.g. "NumFloat64", a class/typedef name).
                t["type"] = term.lv_type.underlying_type or ""
            terminals_data.append(t)

        if not terminals_data:
            return

        # Skip if already in bundled JSON with complete terminals
        resolver = get_vilib_resolver()
        existing = resolver.resolve_by_name(vi_name)
        if existing and existing.terminals:
            bundled_indices = {
                t.index for t in existing.terminals if t.index is not None
            }
            new_indices = {t["index"] for t in terminals_data}
            if new_indices.issubset(bundled_indices):
                return  # Bundled JSON already covers all terminals we found

        # Prepare the entry
        vi_leaf = vi_name if vi_name.endswith(".vi") else f"{vi_name}.vi"
        entry: dict[str, object] = {
            "name": vi_leaf,
            "vi_path": vi_path_rel,
            "category": category,
            "terminals": terminals_data,
            "status": "auto_cached",
        }

        # Read or create category file
        vilib_dir.mkdir(parents=True, exist_ok=True)
        category_file = vilib_dir / f"{category}.json"
        if category_file.exists():
            try:
                existing_data = json.loads(category_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing_data = {"entries": []}
        else:
            existing_data = {"entries": []}

        entries: list[dict[str, object]] = existing_data.get("entries", [])

        # Replace or append
        replaced = False
        for i, e in enumerate(entries):
            if e.get("name") == vi_leaf:
                entries[i] = entry
                replaced = True
                break
        if not replaced:
            entries.append(entry)

        category_file.write_text(
            json.dumps({"entries": entries}, indent=2), encoding="utf-8"
        )

        # Update _index.json
        index_file = vilib_dir / "_index.json"
        if index_file.exists():
            try:
                index_data = json.loads(index_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                index_data = {"categories": {}}
        else:
            index_data = {"categories": {}}

        categories: dict[str, str] = index_data.get("categories", {})
        categories[category] = f"{category}.json"
        index_data["categories"] = categories
        index_file.write_text(json.dumps(index_data, indent=2), encoding="utf-8")

    def _find_file(
        self,
        filename: str,
        search_paths: list[Path],
        caller_dir: Path,
    ) -> Path | None:
        """Find a file by name in search paths. Falls back to a
        case-insensitive match in each directory (see
        ``_case_insensitive_match``) when the exact name isn't there — a
        type reference's recorded casing can differ from the on-disk
        file's actual casing."""
        # Check caller's directory first
        candidate = caller_dir / filename
        if candidate.exists():
            return candidate
        ci_match = _case_insensitive_match(caller_dir, filename)
        if ci_match is not None:
            return ci_match

        for search_path in search_paths:
            candidate = search_path / filename
            if candidate.exists():
                return candidate
            ci_match = _case_insensitive_match(search_path, filename)
            if ci_match is not None:
                return ci_match
            # Sorted: rglob yields filesystem order, so an unsorted first-match
            # picked a different duplicate per run/machine — non-deterministic.
            found = next(iter(sorted(search_path.rglob(filename))), None)
            if found is not None:
                return found

        return None

    def _find_subvi(
        self,
        vi_path: str,
        search_paths: list[Path],
        caller_dir: Path | None = None,
    ) -> Path | None:
        """Find a SubVI file in search paths."""
        vi_name = Path(vi_path).name
        path_parts = Path(vi_path).parts

        if caller_dir:
            candidate = caller_dir / vi_name
            if candidate.exists():
                return candidate

        # Also try without __LibName suffix (JKI convention:
        # "VIName__LibraryName.vi" → actual file is "VIName.vi")
        alt_name = None
        if "__" in vi_name:
            base, _lib = vi_name.rsplit("__", 1)
            if _lib.endswith(".vi"):
                alt_name = base + ".vi"

        for search_path in search_paths:
            if len(path_parts) > 1:
                candidate = search_path / vi_path
                if candidate.exists():
                    return candidate

            candidate = search_path / vi_name
            if candidate.exists():
                return candidate

            if alt_name:
                candidate = search_path / alt_name
                if candidate.exists():
                    return candidate

            # Sorted: deterministic pick among duplicate matches (rglob order
            # is filesystem-dependent — see _find_by_name).
            found = next(iter(sorted(search_path.rglob(vi_name))), None)
            if found is not None:
                return found

            if alt_name:
                found = next(iter(sorted(search_path.rglob(alt_name))), None)
                if found is not None:
                    return found
        return None


def load_vi_by_path(
    path: Path | str,
    mode: LoadMode = LoadMode.FULL,
    *,
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    layout: bool = False,
) -> tuple[InMemoryVIGraph, str]:
    """Load ONE VI, by its on-disk PATH, into a fresh graph and return
    ``(graph, vi_key)``.

    ``vi_key`` is ``load_vi``'s OWN return value -- the exact identity of the
    file just requested. Path IS a VI's identity (see ``resolve_vi_name``):
    two on-disk VIs routinely share a bare filename under LabVIEW dynamic
    dispatch (every class's override of a method is literally ``run.vi``), so
    re-resolving "the VI I just loaded" by ``path.name`` afterward can
    silently pick the WRONG same-named VI out of the graph. This is the ONE
    place "load a user-given path, get its key" is implemented -- the three
    user-facing single-VI load sites (``cli.cmd_describe``, the MCP
    ``read_vi`` tool, ``vi_diff.diff_vi_files``) share it instead of each
    re-deriving the key by bare name and getting it wrong the same way.

    ``vilib_root``/``userlib_root`` are forwarded to ``set_library_roots``
    when given. Raises if ``load_vi`` returns no key (only possible for a
    ``.llb`` container, which none of these single-VI callers pass).
    """
    from .core import InMemoryVIGraph  # local: avoid a core<->loading cycle

    path = Path(path)
    graph = InMemoryVIGraph()
    if vilib_root or userlib_root:
        graph.set_library_roots(vilib_root=vilib_root, userlib_root=userlib_root)
    key = graph.load_vi(str(path), mode, search_paths=search_paths, layout=layout)
    if key is None:
        raise ValueError(
            f"load_vi_by_path: load_vi returned no key for {path!r} "
            "(expected a single .vi file)"
        )
    return graph, key
