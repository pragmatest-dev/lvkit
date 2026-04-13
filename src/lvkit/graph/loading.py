"""Loading mixin for InMemoryVIGraph.

Methods: load_vi, load_lvclass, load_lvlib, load_lvproj, load_typedef,
load_directory, _load_vi_recursive, _load_dependency, _find_subvi,
_resolve_class_vi_path.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from ..extractor import extract_llb, extract_vi_xml
from ..models import ClusterField, LVType
from ..parser import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedDependencyRef,
    ParsedFrontPanel,
    parse_connector_pane_types,
    parse_vi,
    parse_vi_metadata,
)
from ..parser.type_mapping import parse_type_map_rich
from ..structure import (
    LVPrivateDataField,
    get_project_classes,
    get_project_libraries,
    get_project_vis,
    parse_lvclass,
    parse_lvlib,
    parse_lvproj,
)
from .models import PolyInfo, VIMetadata


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
    _loaded_vis: set[str]
    _source_paths: dict[str, Path]
    _vi_metadata: dict[str, VIMetadata]
    _vilib_root: Path | None
    _userlib_root: Path | None

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def clear(self) -> None: ...
        def _add_vi_to_graph(
            self,
            bd: ParsedBlockDiagram,
            fp: ParsedFrontPanel | None,
            conpane: ParsedConnectorPane | None,
            wiring_rules: dict[int, int],
            vi_name: str,
            type_map: dict[int, LVType] | None = None,
            iuse_to_qname: dict[str, str] | None = None,
            iuse_to_qpath: dict[str, str] | None = None,
        ) -> None: ...

    def load_vi(
        self,
        vi_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
        clear_first: bool = False,
    ) -> None:
        """Load a VI hierarchy into memory.

        Args:
            vi_path: Path to .vi file or *_BDHb.xml file
            expand_subvis: Recursively expand SubVIs
            search_paths: Directories to search for SubVIs
            clear_first: Clear existing data before loading
        """
        vi_path = Path(vi_path)

        if clear_first:
            self.clear()

        # Handle .llb containers by extracting members and loading each
        if vi_path.suffix.lower() == ".llb":
            self.load_llb(vi_path, expand_subvis, search_paths)
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

        # Early return if already loaded (prevents re-parsing)
        vi_name = bd_xml.name.replace("_BDHb.xml", ".vi")
        if vi_name in self._loaded_vis:
            return

        # Build search paths
        if search_paths is None:
            search_paths = [vi_path.parent]

        # Parse the VI hierarchy
        # source_dir is the directory of the actual .vi file, not the extracted
        # BD XML (which may be in a temp cache dir). SubVI and type-dependency
        # resolution uses this to find siblings of the original source file.
        source_dir = vi_path.parent if vi_path.suffix.lower() == ".vi" else None
        self._load_vi_recursive(
            bd_xml,
            fp_xml,
            main_xml,
            expand_subvis=expand_subvis,
            search_paths=search_paths,
            visited=set(),
            source_dir=source_dir,
        )

    def load_lvlib(
        self,
        lvlib_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
        owner_chain: list[str] | None = None,
    ) -> None:
        """Load all VIs from a .lvlib file."""
        lvlib_path = Path(lvlib_path)
        lib = parse_lvlib(lvlib_path)

        if search_paths is None:
            search_paths = [lvlib_path.parent]

        chain = list(owner_chain or [])
        lib_name = lib.name + ".lvlib"
        lib_qname = ":".join(chain + [lib_name]) if chain else lib_name

        # Add library node to dep_graph
        self._dep_graph.add_node(lib_qname, node_type="library")

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
                        self.load_typedef(
                            ctl_path, typedef_qname=typedef_qname,
                            search_paths=search_paths,
                        )
                    elif not self._dep_graph.has_node(typedef_qname):
                        self._dep_graph.add_node(typedef_qname, node_type="typedef")
                        self._stubs.add(typedef_qname)
                    if self._dep_graph.has_node(typedef_qname):
                        self._dep_graph.add_edge(lib_qname, typedef_qname, rel="owns")
                else:
                    vi_path = lvlib_path.parent / member.url
                    if not vi_path.exists():
                        # Relative path doesn't resolve — search by filename
                        vi_path = self._find_subvi(
                            member_name, search_paths, lvlib_path.parent,
                        )
                    if vi_path and vi_path.exists():
                        self.load_vi(vi_path, expand_subvis, search_paths)
                        # Ownership edge
                        vi_qname = lib_qname + ":" + member_name
                        if vi_qname in self._dep_graph:
                            self._dep_graph.add_edge(lib_qname, vi_qname, rel="owns")
            elif member.member_type == "Class":
                class_path = lvlib_path.parent / member.url
                if not class_path.exists():
                    class_name = Path(member.url).name
                    found = self._find_file(
                        class_name, search_paths, lvlib_path.parent,
                    )
                    if found:
                        class_path = found
                if class_path.exists():
                    self.load_lvclass(
                        class_path, expand_subvis, search_paths,
                        owner_chain=chain + [lib.name + ".lvlib"],
                    )
            elif member.member_type == "Library":
                nested_path = lvlib_path.parent / member.url
                if not nested_path.exists():
                    lib_name_file = Path(member.url).name
                    found = self._find_file(
                        lib_name_file, search_paths, lvlib_path.parent,
                    )
                    if found:
                        nested_path = found
                if nested_path.exists():
                    self.load_lvlib(
                        nested_path, expand_subvis, search_paths,
                        owner_chain=chain + [lib.name + ".lvlib"],
                    )

    def load_lvclass(
        self,
        lvclass_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
        owner_chain: list[str] | None = None,
    ) -> None:
        """Load all VIs from a .lvclass file."""
        lvclass_path = Path(lvclass_path)
        cls = parse_lvclass(lvclass_path)

        if search_paths is None:
            search_paths = [lvclass_path.parent]

        chain = list(owner_chain or [])
        cls_name = cls.name + ".lvclass"
        cls_qname = ":".join(chain + [cls_name]) if chain else cls_name

        # Add class node to dep_graph with field info
        def _field_to_lvtype(lv_type_name: str) -> LVType | None:
            if not lv_type_name:
                return None
            # Leaf component for classification
            leaf = lv_type_name.rsplit(":", 1)[-1]
            if leaf.endswith(".lvclass"):
                return LVType(
                    kind="class",
                    underlying_type=lv_type_name,
                    classname=lv_type_name,
                )
            if leaf.endswith(".ctl"):
                return LVType(
                    kind="typedef_ref",
                    underlying_type=lv_type_name,
                    typedef_name=lv_type_name,
                )
            if leaf == "Cluster":
                return LVType(kind="cluster", underlying_type=lv_type_name)
            if leaf == "Array":
                return LVType(kind="array", underlying_type=lv_type_name)
            return LVType(kind="primitive", underlying_type=lv_type_name)

        def _to_cluster_field(f: LVPrivateDataField) -> ClusterField:
            lv_type = _field_to_lvtype(f.lv_type_name)
            if f.sub_fields and lv_type is not None:
                lv_type = dc_replace(
                    lv_type,
                    fields=[_to_cluster_field(sf) for sf in f.sub_fields],
                )
            return ClusterField(name=f.name, type=lv_type)

        fields = [_to_cluster_field(f) for f in cls.private_data_fields]
        self._dep_graph.add_node(
            cls_qname,
            node_type="class",
            fields=fields,
            parent_class=cls.parent_class,
        )

        for method in cls.methods:
            vi_path = self._resolve_class_vi_path(lvclass_path.parent, method.vi_path)
            if vi_path and vi_path.exists():
                self.load_vi(vi_path, expand_subvis, search_paths)
                # Ownership edge
                vi_name = cls_qname + ":" + Path(method.vi_path).name
                if vi_name in self._dep_graph:
                    self._dep_graph.add_edge(cls_qname, vi_name, rel="owns")

    def _resolve_class_vi_path(self, cls_dir: Path, relative_path: str) -> Path | None:
        """Resolve VI path from lvclass relative URL."""
        direct = cls_dir / relative_path
        if direct.exists():
            return direct.resolve()

        stripped = relative_path
        while stripped.startswith("../"):
            stripped = stripped[3:]
        if stripped != relative_path:
            from_cls = cls_dir / stripped
            if from_cls.exists():
                return from_cls.resolve()

        return None

    def load_lvproj(
        self,
        lvproj_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs referenced by a .lvproj file."""
        lvproj_path = Path(lvproj_path)
        proj = parse_lvproj(lvproj_path)
        proj_dir = lvproj_path.parent

        if search_paths is None:
            search_paths = [proj_dir]

        for lib_name, lib_path in get_project_libraries(proj):
            if lib_path.exists():
                self.load_lvlib(lib_path, expand_subvis, search_paths)

        for class_name, class_path in get_project_classes(proj):
            if class_path.exists():
                self.load_lvclass(class_path, expand_subvis, search_paths)

        for vi_name, vi_path in get_project_vis(proj):
            if vi_path.exists():
                self.load_vi(vi_path, expand_subvis, search_paths)

    def load_directory(
        self,
        dir_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from a directory recursively."""
        dir_path = Path(dir_path)

        if search_paths is None:
            search_paths = [dir_path]

        for vi_path in dir_path.rglob("*.vi"):
            self.load_vi(vi_path, expand_subvis, search_paths)

        for llb_path in dir_path.rglob("*.llb"):
            if llb_path.is_file():
                self.load_llb(llb_path, expand_subvis, search_paths)

    def load_llb(
        self,
        llb_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from an LLB container archive.

        If ``llb_path`` is already a directory (pre-extracted, as in the
        OpenG samples), delegates to ``load_directory()``.  Otherwise extracts
        the binary LLB to a cache directory and loads each extracted ``.vi``.
        """
        llb_path = Path(llb_path)
        if llb_path.is_dir():
            self.load_directory(llb_path, expand_subvis, search_paths)
            return

        try:
            cache_dir = extract_llb(llb_path)
        except RuntimeError:
            return  # Unreadable LLB — silently skip

        if search_paths is None:
            search_paths = [cache_dir]

        for vi_path in cache_dir.glob("*.vi"):
            try:
                self.load_vi(vi_path, expand_subvis, search_paths)
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
                member_name = str(Path(*parts[i + 1 :])) if i + 1 < len(parts) else ""
                if not member_name:
                    continue
                try:
                    cache_dir = extract_llb(llb_path)
                except RuntimeError:
                    return None
                member_path = cache_dir / member_name
                return member_path if member_path.exists() else None
        return None

    def _load_vi_recursive(
        self,
        bd_xml: Path,
        fp_xml: Path | None,
        main_xml: Path | None,
        expand_subvis: bool,
        search_paths: list[Path],
        visited: set[str],
        source_dir: Path | None = None,
    ) -> str | None:
        """Recursively load a VI and its SubVIs.

        Returns the VI name (qualified if available) or None if already visited.
        """
        # Parse VI using unified parse_vi()
        vi = parse_vi(
            bd_xml=bd_xml,
            fp_xml=fp_xml if fp_xml and fp_xml.exists() else None,
            main_xml=main_xml if main_xml and main_xml.exists() else None,
        )

        metadata = vi.metadata
        bd = vi.block_diagram
        fp = vi.front_panel
        conpane = vi.connector_pane

        unqualified_name = bd_xml.name.replace("_BDHb.xml", ".vi")
        vi_name = metadata.qualified_name or unqualified_name

        if vi_name in visited:
            return None

        if vi_name in self._loaded_vis:
            return vi_name

        if metadata.qualified_name and metadata.qualified_name != unqualified_name:
            self._qualified_aliases[unqualified_name] = metadata.qualified_name

        visited.add(vi_name)

        if metadata.source_path:
            self._source_paths[vi_name] = Path(metadata.source_path)

        # Parse wiring rules from main XML
        wiring_rules: dict[int, int] = {}
        if main_xml and main_xml.exists() and conpane:
            wiring_rules = parse_connector_pane_types(main_xml, conpane)

        type_map = metadata.type_map

        # Parse VI metadata for polymorphic info and library membership
        if main_xml and main_xml.exists():
            poly_metadata = parse_vi_metadata(main_xml)
            if poly_metadata.get("is_polymorphic"):
                self._poly_info[vi_name] = PolyInfo(
                    variants=poly_metadata.get("poly_variants", []),
                    selectors=poly_metadata.get("poly_selectors", []),
                )
            self._vi_metadata[vi_name] = VIMetadata(
                library=poly_metadata.get("library"),
                qualified_name=poly_metadata.get("qualified_name"),
            )

        # Add to dependency graph
        self._dep_graph.add_node(vi_name)

        # Mark as loaded
        self._loaded_vis.add(vi_name)

        # Build dep_ref_map from recorded LinkSavePathRef data.
        # Used for both dependency loading and iUse path diagnostics.
        dep_ref_map: dict[str, ParsedDependencyRef] = (
            {
                ref.qualified_name: ref
                for ref in metadata.dependency_refs
                if ref.qualified_name
            }
            if main_xml and main_xml.exists()
            else {}
        )

        # caller_file is the VI file itself (not its directory): each
        # leading empty in a LinkSavePathRef pops one level from it.
        caller_file = (
            source_dir / unqualified_name
            if source_dir is not None
            else bd_xml.parent / unqualified_name
        )

        # Load all dependencies through the single generic walker.
        if expand_subvis and main_xml and main_xml.exists():
            # Collect all dependency qnames: SubVI/class refs + type_map deps.
            all_dep_qnames: set[str] = set()
            for qname in metadata.subvi_qualified_names:
                if qname and qname != vi_name:
                    all_dep_qnames.add(qname)
            for lv_type in type_map.values():
                if lv_type.classname and lv_type.classname != "LabVIEW Object":
                    all_dep_qnames.add(lv_type.classname)
                if lv_type.typedef_name:
                    all_dep_qnames.add(lv_type.typedef_name)

            for qname in all_dep_qnames:
                self._load_dependency(
                    qname,
                    dep_ref_map.get(qname),
                    caller_file,
                    search_paths,
                    caller_qname=vi_name,
                )

        # Build map of iUse uid → fully qualified on-disk path for diagnostics.
        iuse_to_qpath: dict[str, str] = {}
        for uid, qname in metadata.iuse_to_qualified_name.items():
            ref = dep_ref_map.get(qname)
            if ref and ref.path_tokens:
                iuse_to_qpath[uid] = "/".join(ref.path_tokens)

        # Build the unified graph for this VI AFTER all callees are loaded.
        # Callees are in the graph → cross-VI edges work → types propagate.
        self._add_vi_to_graph(
            bd, fp, conpane, wiring_rules, vi_name, type_map,
            iuse_to_qname=metadata.iuse_to_qualified_name,
            iuse_to_qpath=iuse_to_qpath,
        )

        return vi_name

    def load_typedef(
        self,
        ctl_path: Path | str,
        typedef_qname: str | None = None,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load a .ctl typedef and add it to the dep_graph with its fields.

        Mirrors load_vi / load_lvclass / load_lvlib for consistency.
        """
        ctl_path = Path(ctl_path)
        if search_paths is None:
            search_paths = [ctl_path.parent]

        qname = typedef_qname or ctl_path.name
        if self._dep_graph.has_node(qname):
            return

        if not ctl_path.exists():
            self._dep_graph.add_node(qname, node_type="typedef")
            self._stubs.add(qname)
            return

        try:
            _, fp_xml, main_xml = extract_vi_xml(ctl_path)
        except (RuntimeError, OSError):
            self._dep_graph.add_node(qname, node_type="typedef")
            self._stubs.add(qname)
            return

        if main_xml and main_xml.exists():
            type_map = parse_type_map_rich(main_xml)
            root_type_id = _get_fp_root_type_id(fp_xml)
            if root_type_id is None:
                root_type_id = 1  # cluster control default
            fields = None
            if root_type_id in type_map:
                root_type = type_map[root_type_id]
                fields = root_type.fields
            self._dep_graph.add_node(qname, node_type="typedef", fields=fields)

            # Recurse: load any class/typedef deps referenced in this ctl's
            # type_map (e.g. a cluster field whose type is an lvclass or ctl).
            for lv_type in type_map.values():
                if lv_type.classname and lv_type.classname != "LabVIEW Object":
                    self._load_dependency(
                        lv_type.classname, None, ctl_path, search_paths,
                        caller_qname=qname,
                    )
                if lv_type.typedef_name:
                    self._load_dependency(
                        lv_type.typedef_name, None, ctl_path, search_paths,
                        caller_qname=qname,
                    )
        else:
            # XML not produced — stub with what we know
            self._dep_graph.add_node(qname, node_type="typedef")
            self._stubs.add(qname)

    def _load_dependency(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
        search_paths: list[Path],
        caller_qname: str | None = None,
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
        if self._dep_graph.has_node(qualified_name):
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, qualified_name)
            return

        leaf = qualified_name.rsplit(":", 1)[-1]

        # Resolve path: prefer the recorded ref, fall back to name-based search.
        resolved: Path | None = None
        if dep_ref is not None:
            candidate = dep_ref.resolve_against(
                caller_file,
                vilib_root=self._vilib_root,
                userlib_root=self._userlib_root,
            )
            if candidate is not None:
                if candidate.exists():
                    resolved = candidate
                else:
                    llb_resolved = self._resolve_through_llb(candidate)
                    if llb_resolved is not None:
                        resolved = llb_resolved
        if resolved is None:
            if leaf.endswith(".vi"):
                resolved = self._find_subvi(leaf, search_paths, caller_file.parent)
            else:
                resolved = self._find_file(leaf, search_paths, caller_file.parent)

        if resolved is None:
            node_type = (
                "class" if leaf.endswith(".lvclass") else
                "typedef" if leaf.endswith(".ctl") else
                "library" if leaf.endswith(".lvlib") else
                "vi"
            )
            self._dep_graph.add_node(qualified_name, node_type=node_type)
            self._stubs.add(qualified_name)
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, qualified_name)
            return

        # Dispatch by extension to the matching public loader.
        if leaf.endswith(".vi"):
            try:
                bd_xml, fp_xml, main_xml = extract_vi_xml(resolved)
                loaded_name = self._load_vi_recursive(
                    bd_xml, fp_xml, main_xml,
                    expand_subvis=True,
                    search_paths=search_paths,
                    visited=set(),
                    source_dir=resolved.parent,
                )
                if loaded_name:
                    if caller_qname:
                        self._dep_graph.add_edge(caller_qname, loaded_name)
                    if qualified_name != loaded_name:
                        self._qualified_aliases[qualified_name] = loaded_name
                    # Auto-cache terminal layout for vilib VIs loaded from disk
                    if (dep_ref is not None
                            and dep_ref.path_tokens
                            and dep_ref.path_tokens[0] == "<vilib>"):
                        self._cache_vilib_terminal_layout(loaded_name, dep_ref)
            except (RuntimeError, OSError):
                self._stub_subvi(qualified_name, caller_qname or "")
        elif leaf.endswith(".lvclass"):
            parts = qualified_name.split(":")
            owner_chain = parts[:-1] if len(parts) > 1 else None
            self.load_lvclass(
                resolved, expand_subvis=True,
                search_paths=search_paths, owner_chain=owner_chain,
            )
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, qualified_name)
        elif leaf.endswith(".lvlib"):
            self.load_lvlib(resolved, expand_subvis=True, search_paths=search_paths)
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, qualified_name)
        elif leaf.endswith(".ctl"):
            self.load_typedef(
                resolved, typedef_qname=qualified_name, search_paths=search_paths,
            )
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, qualified_name)
        else:
            # Unknown extension — stub rather than guess.
            self._dep_graph.add_node(qualified_name, node_type="unknown")
            self._stubs.add(qualified_name)
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, qualified_name)

    def _stub_subvi(self, name: str, _parent_vi: str) -> None:
        """Record a SubVI reference that could not be resolved as a stub."""
        self._stubs.add(name)
        self._dep_graph.add_node(name)

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
        import json as _json

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
                t["type"] = term.lv_type.name or ""
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
                existing_data = _json.loads(category_file.read_text())
            except (_json.JSONDecodeError, OSError):
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
            _json.dumps({"entries": entries}, indent=2)
        )

        # Update _index.json
        index_file = vilib_dir / "_index.json"
        if index_file.exists():
            try:
                index_data = _json.loads(index_file.read_text())
            except (_json.JSONDecodeError, OSError):
                index_data = {"categories": {}}
        else:
            index_data = {"categories": {}}

        categories: dict[str, str] = index_data.get("categories", {})
        categories[category] = f"{category}.json"
        index_data["categories"] = categories
        index_file.write_text(_json.dumps(index_data, indent=2))

    def _find_file(
        self,
        filename: str,
        search_paths: list[Path],
        caller_dir: Path,
    ) -> Path | None:
        """Find a file by name in search paths."""
        # Check caller's directory first
        candidate = caller_dir / filename
        if candidate.exists():
            return candidate

        for search_path in search_paths:
            candidate = search_path / filename
            if candidate.exists():
                return candidate
            for found in search_path.rglob(filename):
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

            for found in search_path.rglob(vi_name):
                return found

            if alt_name:
                for found in search_path.rglob(alt_name):
                    return found
        return None
