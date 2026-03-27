"""Loading mixin for InMemoryVIGraph.

Methods: load_vi, load_lvclass, load_lvlib, load_lvproj, load_directory,
_load_vi_recursive, _find_subvi, _resolve_class_vi_path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..extractor import extract_vi_xml
from ..graph_types import PolyInfo, VIMetadata
from ..parser import (
    parse_connector_pane_types,
    parse_vi,
    parse_vi_metadata,
)
from ..parser.type_mapping import parse_type_map_rich
from ..structure import (
    get_project_classes,
    get_project_libraries,
    get_project_vis,
    parse_lvclass,
    parse_lvlib,
    parse_lvproj,
)

if TYPE_CHECKING:
    import networkx as nx


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
        self._load_vi_recursive(
            bd_xml,
            fp_xml,
            main_xml,
            expand_subvis=expand_subvis,
            search_paths=search_paths,
            visited=set(),
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
                vi_path = lvlib_path.parent / member.url
                if vi_path.exists():
                    self.load_vi(vi_path, expand_subvis, search_paths)
                    # Ownership edge
                    vi_qname = lib_qname + ":" + Path(member.url).name
                    if vi_qname in self._dep_graph:
                        self._dep_graph.add_edge(lib_qname, vi_qname, rel="owns")
            elif member.member_type == "Class":
                class_path = lvlib_path.parent / member.url
                if class_path.exists():
                    self.load_lvclass(
                        class_path, expand_subvis, search_paths,
                        owner_chain=chain + [lib.name + ".lvlib"],
                    )
            elif member.member_type == "Library":
                nested_path = lvlib_path.parent / member.url
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
        from ..graph_types import ClusterField, LVType

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

        fields = [
            ClusterField(
                name=f.name,
                type=_field_to_lvtype(f.lv_type_name),
            )
            for f in cls.private_data_fields
        ]
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

    def _load_vi_recursive(
        self,
        bd_xml: Path,
        fp_xml: Path | None,
        main_xml: Path | None,
        expand_subvis: bool,
        search_paths: list[Path],
        visited: set[str],
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

        # Process SubVIs
        if main_xml and main_xml.exists():
            subvi_ref_map = {
                ref.qualified_name: ref
                for ref in metadata.subvi_path_refs
                if ref.qualified_name
            }

            caller_dir = bd_xml.parent

            for subvi_qname in metadata.subvi_qualified_names:
                if subvi_qname == vi_name:
                    continue

                if subvi_qname in visited:
                    continue

                ref = subvi_ref_map.get(subvi_qname)
                if ref and ref.path_tokens:
                    lookup_path = ref.get_relative_path()
                    is_vilib = ref.is_vilib
                    is_userlib = ref.is_userlib
                else:
                    if ":" in subvi_qname:
                        lookup_path = subvi_qname.split(":")[-1]
                    else:
                        lookup_path = subvi_qname
                    is_vilib = False
                    is_userlib = False

                if expand_subvis:
                    subvi_path = self._find_subvi(
                        lookup_path, search_paths, caller_dir, is_vilib, is_userlib
                    )
                    if subvi_path:
                        try:
                            subvi_bd_xml, subvi_fp_xml, subvi_main_xml = extract_vi_xml(
                                subvi_path
                            )
                            loaded_name = self._load_vi_recursive(
                                subvi_bd_xml,
                                subvi_fp_xml,
                                subvi_main_xml,
                                expand_subvis=True,
                                search_paths=search_paths,
                                visited=visited,
                            )
                            if loaded_name:
                                self._dep_graph.add_edge(vi_name, loaded_name)
                        except (RuntimeError, OSError):
                            self._stubs.add(subvi_qname)
                            self._dep_graph.add_node(subvi_qname)
                            self._dep_graph.add_edge(vi_name, subvi_qname)
                    else:
                        self._stubs.add(subvi_qname)
                        self._dep_graph.add_node(subvi_qname)
                        self._dep_graph.add_edge(vi_name, subvi_qname)
                else:
                    self._stubs.add(subvi_qname)
                    self._dep_graph.add_node(subvi_qname)
                    self._dep_graph.add_edge(vi_name, subvi_qname)

        # Load type dependencies: classes referenced in type_map
        # that aren't in the dep_graph yet.
        if type_map and expand_subvis:
            self._load_type_dependencies(
                type_map, search_paths, bd_xml.parent, visited,
            )

        # Build the unified graph for this VI AFTER all callees are loaded.
        # Callees are in the graph → cross-VI edges work → types propagate.
        self._add_vi_to_graph(
            bd, fp, conpane, wiring_rules, vi_name, type_map,
            iuse_to_qname=metadata.iuse_to_qualified_name,
        )

        return vi_name

    def _load_type_dependencies(
        self,
        type_map: dict,
        search_paths: list[Path],
        caller_dir: Path,
        visited: set[str],
    ) -> None:
        """Load named types referenced in a VI's type_map.

        Any class, typedef, or library referenced by a VI is a dependency
        that must be in the dep_graph. Same as SubVI loading — find it,
        load it, or stub it.
        """
        for lv_type in type_map.values():
            # Class dependencies
            if lv_type.classname and lv_type.classname != "LabVIEW Object":
                self._ensure_type_loaded(
                    lv_type.classname, search_paths, caller_dir,
                )
            # Typedef dependencies
            if lv_type.typedef_name:
                self._ensure_typedef_loaded(
                    lv_type.typedef_name, search_paths, caller_dir,
                )

    def _ensure_type_loaded(
        self,
        classname: str,
        search_paths: list[Path],
        caller_dir: Path,
    ) -> None:
        """Ensure a named type is in the dep_graph.

        If not already loaded, search for the .lvclass file and load it.
        """
        if self._dep_graph.has_node(classname):
            return

        # Extract the leaf filename from qualified name
        # e.g. "Lib.lvlib:TestSuite.lvclass" → "TestSuite.lvclass"
        leaf = classname.rsplit(":", 1)[-1]

        if leaf.endswith(".lvclass"):
            cls_path = self._find_file(leaf, search_paths, caller_dir)
            if cls_path:
                # Determine owner_chain from qualified name
                parts = classname.split(":")
                owner_chain = parts[:-1] if len(parts) > 1 else None
                self.load_lvclass(
                    cls_path,
                    expand_subvis=True,
                    search_paths=search_paths,
                    owner_chain=owner_chain,
                )
            else:
                # Stub it — we know the type exists but can't find it
                self._dep_graph.add_node(classname, node_type="class")
                self._stubs.add(classname)

    def _ensure_typedef_loaded(
        self,
        typedef_name: str,
        search_paths: list[Path],
        caller_dir: Path,
    ) -> None:
        """Ensure a typedef is in the dep_graph with its fields.

        Same as class/VI loading: find .ctl on disk → extract XML →
        parse type_map → add fields to dep_graph.
        """
        if self._dep_graph.has_node(typedef_name):
            return

        leaf = typedef_name.rsplit(":", 1)[-1]
        ctl_path = self._find_file(leaf, search_paths, caller_dir)

        if ctl_path:
            try:
                _, _, main_xml = extract_vi_xml(ctl_path)
                if main_xml and main_xml.exists():
                    type_map = parse_type_map_rich(main_xml)
                    # Find the primary type (first with fields)
                    fields = None
                    for lv_type in type_map.values():
                        if lv_type.fields:
                            fields = lv_type.fields
                            break
                    self._dep_graph.add_node(
                        typedef_name,
                        node_type="typedef",
                        fields=fields,
                    )
                    return
            except (RuntimeError, OSError):
                pass

        # Stub it
        self._dep_graph.add_node(typedef_name, node_type="typedef")
        self._stubs.add(typedef_name)

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
        is_vilib: bool = False,
        is_userlib: bool = False,
    ) -> Path | None:
        """Find a SubVI file in search paths."""
        vi_name = Path(vi_path).name
        path_parts = Path(vi_path).parts

        if caller_dir and not is_vilib and not is_userlib:
            candidate = caller_dir / vi_name
            if candidate.exists():
                return candidate

            if len(path_parts) > 1:
                for parent in [caller_dir] + list(caller_dir.parents)[:3]:
                    candidate = parent / vi_path
                    if candidate.exists():
                        return candidate

        for search_path in search_paths:
            if len(path_parts) > 1:
                candidate = search_path / vi_path
                if candidate.exists():
                    return candidate

            candidate = search_path / vi_name
            if candidate.exists():
                return candidate

            for found in search_path.rglob(vi_name):
                return found
        return None
