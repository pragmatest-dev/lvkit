"""Loading mixin for InMemoryVIGraph.

Methods: load_vi, load_llb, load_lvclass, load_lvlib, load_lvproj, load_typedef,
load_directory, _load_vi_recursive, _load_dependency, _locate_recorded,
_member_file, _resolve_through_llb.

A dependency is found only at the path its caller records for it: a relative
path joined onto the caller's own folder, a ``<vilib>``/``<userlib>``/
``<instrlib>`` path joined onto the folder configured for that root, an
absolute path as it is. A file not there (after a ``.llb`` member, a member
stored beside its class or library, and one case-insensitive match of the file
name) is a missing dependency that keeps its recorded path. Nothing is looked
up by file name.
"""

from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import networkx as nx

from lvkit.library_folders import Folders, SearchEntries

from ..cache_paths import project_root
from ..class_property import property_role
from ..embedded_instances import read_instance_sources
from ..extract_pool import ExtractionPool
from ..extractor import extract_llb_members, extract_vi_xml
from ..flattened_typedesc import embedded_private_data_copy
from ..load_mode import LoadMode as LoadMode  # re-export for old call sites
from ..models import ClusterField, LVType, Terminal
from ..parser import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedDependencyRef,
    ParsedFrontPanel,
    ParsedVI,
    ParsedVIMetadata,
    parse_vi,
)
from ..parser.link_table import SharedVariableLink, SharedVariableRecordFailure
from ..parser.metadata import parse_vi_metadata, recorded_path_key
from ..parser.models import ABSOLUTE_PATH_TYPE, is_symbolic_root
from ..parser.vi import own_container_record
from ..run_report import (
    CacheWriteError,
    Problem,
    ProblemKind,
    ProblemStage,
    RunSettings,
    UnreadableVIError,
)
from ..structure import (
    LVClass,
    get_project_classes,
    get_project_libraries,
    get_project_vis,
    member_url_paths,
    parse_lvclass,
    parse_lvlib,
    parse_lvproj,
    private_data_field_to_cluster_field,
)
from ..vi_files import VI_EXTENSIONS, glob_vi_files, is_vi_file, rglob_vi_files
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .builders.operations import callee_keys
from .errors import AmbiguousVIReferenceError, DependencyFileError
from .llb_members import file_is_present, llb_member_copy, vi_bytes_file
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
    VIDotNetAssemblies,
    VIHealth,
    VIMetadata,
    VINode,
    VIProperties,
    WindowProps,
)
from .node_kinds import EdgeRel, NodeType
from .op_walk import stamp_nmux_lane_names, stamp_property_value_names
from .parallel_parse import PARALLEL_THRESHOLD, parallel_parse_directory
from .recorded_paths import (
    RecordedPaths,
    member_tokens,
    redirect_member_beside_container,
)
from .type_records import NamedRecord, TypeRecords, type_names

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..parser.layout import Layout
    from .core import InMemoryVIGraph
    from .search_path import SearchPath


_F = TypeVar("_F", bound=Callable[..., Any])


def _with_extraction_pool(method: _F) -> _F:
    """Run a public load entry point with the graph's extraction pool open.

    The outermost entry point opens the pool and closes it on exit; entry points
    nested inside it (a class load calling ``load_vi`` per member) reuse it.
    """

    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self._extract_pool is not None:
            return method(self, *args, **kwargs)
        with ExtractionPool() as pool:
            self._extract_pool = pool
            try:
                return method(self, *args, **kwargs)
            finally:
                self._extract_pool = None

    return cast("_F", wrapper)


# LoadMode is defined in the light ``lvkit.load_mode`` leaf module (imported
# above) so the CLI can name the modes without pulling the graph stack; it is
# re-exported here for the long-standing ``from lvkit.graph.loading import
# LoadMode`` call sites.


def _has_qualified_name(path: Path, qualified_name: str) -> bool:
    """Whether the VI at ``path`` is the one a caller records as
    ``qualified_name``: the libraries and classes it records it is in, then its
    file name (case-insensitive, as LabVIEW names compare). A class, library or
    other non-VI file is named by its file alone."""
    if not is_vi_file(path):
        return True
    try:
        _, _, main_xml = extract_vi_xml(path)
    except (UnreadableVIError, CacheWriteError):
        return False
    if main_xml is None:
        return False
    owners = parse_vi_metadata(main_xml).get("owning_libraries") or []
    return file_qualified_name(owners, path.name).lower() == qualified_name.lower()


def _case_insensitive_match(directory: Path, filename: str) -> Path | None:
    """Single-directory case-insensitive filename match.

    LabVIEW class/typedef names are effectively case-insensitive — a type
    reference's recorded casing (e.g. ``DAQmx Module Runtime.lvclass``, from
    a TypeDesc ``Item``) can differ from the casing the file was actually
    SAVED under on disk (e.g. ``Daqmx Module runtime.lvclass``). An exact
    (case-sensitive) lookup is always tried first by the caller — this is
    only the fallback, so it never changes behavior on a case-sensitive
    filesystem where the exact name already matches. Several files that differ
    only in case raise ``AmbiguousVIReferenceError``; none gives ``None``.
    """
    target = filename.lower()
    try:
        matches = sorted(e for e in directory.iterdir() if e.name.lower() == target)
    except OSError:
        return None
    if len(matches) > 1:
        raise AmbiguousVIReferenceError(
            f"Ambiguous reference -- {filename!r} matches several files that "
            "differ only in case. Keep the one it names.",
            matches,
        )
    return matches[0] if matches else None


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


def file_qualified_name(owning_libraries: Sequence[str], file_name: str) -> str:
    """A file's qualified name: the owner chain its own ``LIBN`` block records
    (the libraries and classes it is in, outermost first) with its file name as
    the last component. LabVIEW names a VI by its file; a VI copied and renamed
    outside LabVIEW still stores its old name."""
    return ":".join([*owning_libraries, file_name])


def collect_direct_dep_qnames(
    subvi_qualified_names: list[str], own_qname: str | None
) -> set[str]:
    """The SubVI/class call table's qnames (``subvi_qualified_names``), less the
    VI's own qname. ``_load_vi_recursive`` unions the recorded
    ``dependency_refs`` and the class and typedef records
    (``type_file_records``) on top."""
    return {q for q in subvi_qualified_names if q and q != own_qname}


_TYPE_FILE_SUFFIXES = (".ctl", ".lvclass")
# The files a type or a Shared Variable names by name only
# (``LoadingMixin._store_type_records``).
_NAMED_TYPE_SUFFIXES = (*_TYPE_FILE_SUFFIXES, ".lvlib")
# Link records that name a class without being a use of its type
# (``type_file_records``).
_NOT_TYPE_RECORDS = frozenset({"VILB", "DyOM"})


def type_file_records(
    link_path_refs: list[ParsedDependencyRef],
) -> dict[str, ParsedDependencyRef]:
    """The class and typedef files a VI's (or control's) link-table records
    name, each once by its recorded path, keyed by the name its record gives
    it (its file name when the record gives none): its class and typedef
    dependencies. Two record kinds name a class without using its type and
    are not: ``VILB``, the VI's link to its own class or library, and
    ``DyOM``, a dynamic-dispatch call (recorded by the class whose method it
    calls; the method is the dependency). A record of the VI's own class's type
    (on the panel, in the diagram) is one. A type no record names is nested in
    a dependency that does."""
    by_path: dict[tuple[str, ...], ParsedDependencyRef] = {}
    for ref in link_path_refs:
        path = recorded_path_key(ref)
        if ref.record in _NOT_TYPE_RECORDS or not ref.name.lower().endswith(
            _TYPE_FILE_SUFFIXES
        ):
            continue
        held = by_path.get(path)
        if held is None or (held.qualified_name is None and ref.qualified_name):
            by_path[path] = ref
    return {ref.qualified_name or ref.name: ref for _, ref in sorted(by_path.items())}


def class_owner_ref(
    qualified_name: str, dep_ref: ParsedDependencyRef | None
) -> ParsedDependencyRef | None:
    """The class a call's record names as the owner of the called method: set
    when the recorded path ends in a ``.lvclass`` file and the called name is a
    VI. The method file is that class's member, not the path itself (see
    ``LoadingMixin._class_member_file``). The owner's qualified name is the
    record's, less the method; a record naming only the method has the class. A
    record whose owner chain does not end in the path's class is not a class
    call."""
    if dep_ref is None or not dep_ref.path_tokens:
        return None
    class_leaf = dep_ref.path_tokens[-1]
    if not class_leaf.lower().endswith(".lvclass") or not is_vi_file(
        qualified_name.rsplit(":", 1)[-1]
    ):
        return None
    owner = qualified_name.rsplit(":", 1)[0] if ":" in qualified_name else class_leaf
    if owner.rsplit(":", 1)[-1].lower() != class_leaf.lower():
        return None
    return ParsedDependencyRef(
        name=class_leaf,
        path_tokens=list(dep_ref.path_tokens),
        qualified_name=owner,
        path_type=dep_ref.path_type,
    )


def build_dep_ref_map(
    dependency_refs: list[ParsedDependencyRef],
) -> dict[str, ParsedDependencyRef]:
    """The recorded ``LinkSavePathRef`` deps keyed by qualified name (skipping
    refs that carry none), for a VI's and a control's dependency walk."""
    return {ref.qualified_name: ref for ref in dependency_refs if ref.qualified_name}


@dataclass(frozen=True)
class LinkIndex:
    """A VI's link-table records, found by the name a type gives its file:
    ``by_name`` holds each record that names its file, by that qualified name;
    ``by_file`` each record that names none, by its path's file name (LabVIEW
    names a class or a control by its file). Keys are case-folded: LabVIEW
    compares names without regard to case."""

    by_name: dict[str, list[ParsedDependencyRef]]
    by_file: dict[str, list[ParsedDependencyRef]]

    def records_of(self, qualified_name: str) -> list[ParsedDependencyRef] | None:
        """The records of the file a type names ``qualified_name``: those that
        give it that name, else the unnamed ones whose path ends in its file."""
        named = self.by_name.get(qualified_name.casefold())
        if named:
            return named
        return self.by_file.get(qualified_name.rsplit(":", 1)[-1].casefold())


def build_link_index(
    link_path_refs: list[ParsedDependencyRef], owners: Sequence[str]
) -> LinkIndex:
    """Every link-table record, in record order (several records can name one
    file). A named record is keyed by the qualified name it gives the file its
    path ends in, and, since a record in the caller's own library omits that
    library (as ``joined_link_name`` qualifies a same-library link), also under
    the first entry of the caller's own ``LIBN`` chain (``owners``). A record
    that names no file is keyed by its path's file name."""
    by_name: dict[str, list[ParsedDependencyRef]] = {}
    by_file: dict[str, list[ParsedDependencyRef]] = {}
    for ref in link_path_refs:
        if ref.qualified_name is None:
            by_file.setdefault(ref.name.casefold(), []).append(ref)
            continue
        names = [ref.qualified_name]
        if owners:
            names.append(f"{owners[0]}:{ref.qualified_name}")
        for name in names:
            by_name.setdefault(name.casefold(), []).append(ref)
    return LinkIndex(by_name, by_file)


class LoadingMixin:
    """Mixin providing VI loading methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _term_to_node: dict[str, str]
    _dep_graph: nx.DiGraph
    _stubs: set[str]
    _recorded: RecordedPaths
    _catalog_stop: bool
    _poly_info: dict[str, PolyInfo]
    _loaded_vis: set[str]
    _dep_load_mode: dict[str, LoadMode]
    _source_paths: dict[str, Path]
    _vi_display_names: dict[str, str]
    _vi_metadata: dict[str, VIMetadata]
    _vi_properties: dict[str, VIProperties]
    _vi_health: dict[str, VIHealth]
    _vi_dotnet_assemblies: dict[str, VIDotNetAssemblies]
    _vilib_roots: tuple[Path, ...]
    _userlib_roots: tuple[Path, ...]
    _instrlib_roots: tuple[Path, ...]
    _search_path: SearchPath
    _layouts: dict[str, Layout]
    _want_layout: bool
    _parse_cache: dict[str, ParsedVI] | None
    _extract_pool: ExtractionPool | None

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def load_vi_recording(
            self,
            vi_path: Path | str,
            mode: LoadMode,
        ) -> str | None: ...
        def record_load_failure(
            self,
            vi_path: Path | str,
            exc: Exception,
            stage: ProblemStage | None = None,
            node_type: NodeType = ...,
        ) -> None: ...
        def record_file_problem(
            self,
            path: Path | str,
            problem: Problem,
            node_type: NodeType = ...,
            may_stop: bool = ...,
        ) -> None: ...
        def recorded_candidates(
            self, ref: ParsedDependencyRef, caller_file: Path
        ) -> list[Path]: ...
        def problems(self, vi_keys: Iterable[str] | None = None) -> list[Problem]: ...
        def record_problem(
            self,
            vi_key: str,
            problem: Problem,
            node_type: NodeType = ...,
            may_stop: bool = ...,
        ) -> None: ...
        def clear_problems(self, vi_key: str) -> None: ...
        def vi_label(self, path: Path | str) -> str: ...
        def clear(self) -> None: ...
        def vi_interface(
            self, vi_name: str
        ) -> tuple[list[Terminal], list[Terminal], list[Terminal], list[Terminal]]: ...
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
            iuse_to_callee_key: dict[str, str] | None = None,
            poly_to_callee_key: dict[str, str] | None = None,
            iuse_refs: dict[str, ParsedDependencyRef] | None = None,
            shared_variable_links: dict[str, SharedVariableLink] | None = None,
            shared_variable_failures: list[SharedVariableRecordFailure] | None = None,
        ) -> None: ...
        def class_method(
            self, class_key: str, method: str
        ) -> tuple[str, str] | None: ...
        def is_class(self, key: str) -> bool: ...
        def is_class_wire(self, lv_type: LVType | None, class_key: str) -> bool: ...
        def class_private_data_key(self, key: str) -> str | None: ...
        def key_label(self, key: str) -> str: ...
        def recorded_type_key(self, file_key: str, lv_type: LVType) -> str | None: ...

    @_with_extraction_pool
    def load_vi(
        self,
        vi_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        clear_first: bool = False,
        layout: bool = False,
        original_path: Path | str | None = None,
    ) -> str | None:
        """Load a VI hierarchy into memory.

        Args:
            vi_path: Path to .vi file or *_BDHb.xml file
            mode: dependency depth (see ``LoadMode``). ``FULL`` (default) loads
                the whole SubVI/class-method tree (codegen); ``MINIMAL`` loads
                this VI + its direct SubVIs' connector panes + referenced-type
                fields (render/diff/describe — byte-identical to FULL, far
                cheaper); ``NONE`` loads this VI only.
            clear_first: Clear existing data before loading
            layout: decode + retain block-diagram GEOMETRY for every loaded VI
                (see ``get_layout``). Off by default — only rendering needs it;
                codegen/analysis loads pay nothing. Geometry comes from the same
                parse, never a second heap read.
            original_path: the file ``vi_path`` (a ``.vi``) is a copy of, e.g.
                a git version written to a temporary folder. The VI is read from
                ``vi_path`` but is the VI at ``original_path``: that is its
                identity, the folder its relative recorded paths start from and
                the project its paths are shown in. Its source file stays
                ``vi_path``.
        """
        vi_path = Path(vi_path)
        if original_path is not None and not is_vi_file(vi_path):
            raise ValueError(f"original_path applies to a VI file, not {vi_path}")
        # The file this VI is: the original a copy stands for, else itself.
        identity = Path(original_path) if original_path is not None else vi_path
        if self._search_path.top_vi_folder is None:
            # The search path's <topvi>: the folder of the VI the load is for.
            self._search_path.top_vi_folder = identity.resolve().parent
        self._want_layout = layout

        if clear_first:
            self.clear()

        # Handle .llb containers by extracting members and loading each
        if vi_path.suffix.lower() == ".llb":
            self.load_llb(vi_path, mode)
            return

        # Handle VI files (.vi/.vim/.vit) by extracting first
        if is_vi_file(vi_path):
            bd_xml, fp_xml, main_xml = self._extract(vi_path)
        elif vi_path.name.endswith("_BDHb.xml"):
            bd_xml = vi_path
            fp_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", "_FPHb.xml"))
            if not fp_xml.exists():
                fp_xml = None
            main_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", ".xml"))
            if not main_xml.exists():
                main_xml = None
        else:
            raise ValueError(
                f"Expected a VI file ({', '.join(VI_EXTENSIONS)}) or *_BDHb.xml file: "
                f"{vi_path}"
            )

        # Early return only if already loaded AT >= the requested depth. A VI
        # first seen as a leaf (child NONE) must still UPGRADE when loaded in its
        # own right, or it never gets its own SubVIs/call edges (see
        # _dep_load_mode). Identity is the file path: a ``.vi``'s key is its
        # resolved path, so probe _loaded_vis with that. A raw ``*_BDHb.xml`` has
        # no source path here, so it falls through to _load_vi_recursive, which
        # computes and dedups on the real vi_key.
        if is_vi_file(vi_path):
            probe_key = str(identity.resolve())
            if probe_key in self._loaded_vis and _mode_covers(
                self._dep_load_mode.get(probe_key), mode
            ):
                return probe_key

        # Parse the VI hierarchy
        # source_dir is the directory of the actual .vi file, not the extracted
        # BD XML (which may be in a temp cache dir). SubVI and type-dependency
        # resolution uses this to find siblings of the original source file.
        source_dir = identity.parent if is_vi_file(vi_path) else None
        loaded_key = self._load_vi_recursive(
            bd_xml,
            fp_xml,
            main_xml,
            visited=set(),
            source_dir=source_dir,
            source_override=(identity if is_vi_file(vi_path) else None),
            mode=mode,
        )
        if original_path is not None and loaded_key is not None:
            self._source_paths[loaded_key] = vi_path  # the bytes it was read from

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
        ``key`` plus the ``_source_paths`` entry and its display names. Shared by
        the ``.vi`` loader and the ``.lvclass``/``.ctl``/``.lvlib`` loaders so
        EVERY dep-graph node is keyed by path, not qname: path is unique on disk
        where a qname is not (a built copy beside its source twin share a
        qname). The qname is the human-facing display name (never a lookup
        key)."""
        self._source_paths[key] = vi_bytes_file(source_file)
        # The search path's <foundvi>: the folders this load found files in.
        self._search_path.found_folders.setdefault(Path(source_file).parent, None)
        self._vi_display_names[key] = qname
        self._register_dep_name(key, name, qname)
        if self._dep_graph.has_node(key):
            self._name_by_owner(key)

    def _register_dep_name(self, key: str, name: str, qname: str) -> None:
        """Record a dep node's bare ``name`` and ``qname`` AS NODE ATTRIBUTES,
        for display (the key is the path) -- WITHOUT a ``_source_paths`` entry.
        Used for STUBS (absent files) too: the intended path is the key, so the
        node upgrades by identity when the file appears, and ``_source_paths``
        stays the set of LOADED files (its single job)."""
        if self._dep_graph.has_node(key):
            node = self._dep_graph.nodes[key]
            node["qname"] = qname
            node["name"] = name

    def _add_stub(
        self,
        key: str,
        node_type: NodeType,
        name: str,
        qname: str | None,
        caller: str | None,
        rel: EdgeRel | None = None,
        reason: str | None = None,
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
        edged from). ``reason`` is why the dependency is missing when more than
        its absence (``stub_reason``).
        """
        if not self._dep_graph.has_node(key):
            self._dep_graph.add_node(key, node_type=node_type)
            self._stubs.add(key)
        if reason is not None and key in self._stubs:
            self._dep_graph.nodes[key]["stub_reason"] = reason
        self._register_dep_name(key, name, qname if qname is not None else name)
        if caller:
            if rel == EdgeRel.OWNS:
                self._add_ownership_edge(caller, key)
            elif rel:
                self._dep_graph.add_edge(caller, key, rel=rel)
            else:
                self._dep_graph.add_edge(caller, key)

    def _add_ownership_edge(self, owner: str, member: str, **attrs: object) -> None:
        """Edge ``owner`` (a class or library) to the ``member`` it owns; the
        paths of the files below it follow from the owner's, so the recorded paths
        are told the owners changed."""
        self._dep_graph.add_edge(owner, member, rel=EdgeRel.OWNS, **attrs)
        self._recorded.owners_changed()
        self._name_by_owner(member)

    def _owner_qname(self, key: str) -> str | None:
        """The qualified name of the class or library that lists ``key`` as its
        member (its ``owns`` edge), None when nothing loaded lists it."""
        for owner in sorted(self._dep_graph.predecessors(key)):
            if self._dep_graph.edges[owner, key].get("rel") != EdgeRel.OWNS:
                continue
            qname = self._dep_graph.nodes[owner].get("qname")
            if qname:
                return str(qname)
        return None

    def _name_by_owner(self, key: str) -> None:
        """Name ``key`` by the class or library that lists it: that owner's
        qualified name (its own file's name) and the member's file name, so one
        file has one name in every view whatever spelling its callers or its own
        ``LIBN`` record use. The members it lists in turn follow. A file nothing
        loaded lists keeps the name it was registered with."""
        owner = self._owner_qname(key)
        node = self._dep_graph.nodes[key]
        name = node.get("name")
        if owner is None or not name:
            return
        qname = f"{owner}:{name}"
        if node.get("qname") == qname:
            return
        self._register_dep_name(key, name, qname)
        if key in self._vi_display_names:
            self._vi_display_names[key] = qname
        for member in sorted(self._dep_graph.successors(key)):
            if self._dep_graph.edges[key, member].get("rel") == EdgeRel.OWNS:
                self._name_by_owner(member)

    def _note_recorded_path(
        self,
        key: str,
        dep_ref: ParsedDependencyRef | None,
        qualified_name: str,
        caller_key: str | None,
    ) -> None:
        """Remember that ``caller_key`` recorded ``dep_ref``'s path for the file
        ``key`` (``RecordedPaths``). Recording twice is the same as once: a
        dependency that resolves to a file records before its own children load,
        and every dependency, resolved or not, records when its load returns. A
        class method's record names its class, not the method (``class_owner_ref``):
        the method's path follows from the class that lists it, so it records
        none."""
        if dep_ref is None or not dep_ref.path_tokens:
            return
        if class_owner_ref(qualified_name, dep_ref) is not None:
            return
        leaf = qualified_name.rsplit(":", 1)[-1]
        self._recorded.record(
            key,
            caller_key,
            member_tokens(dep_ref.path_tokens, leaf),
            dep_ref.path_type,
        )

    def _store_type_records(
        self,
        file_key: str,
        metadata: ParsedVIMetadata | None,
        loaded: dict[str, str],
        caller_file: Path,
    ) -> None:
        """Store on the VI or control ``file_key`` its ``TypeRecords``: each of
        its link records that names a class, typedef or library, keyed by the
        file the dependency walk loaded for that name (``loaded``) or, for a
        record the walk did not load, by the file at its recorded path; and the
        loaded dependencies a name it records no file for is nested in. Then
        resolves every class and typedef name its types record, once."""
        refs = metadata.link_path_refs if metadata is not None else []
        records: list[NamedRecord] = []
        for ref in refs:
            name = ref.qualified_name or ref.name
            if not name.lower().endswith(_NAMED_TYPE_SUFFIXES):
                continue
            key, exists = self._record_key(ref, caller_file)
            if not exists and name in loaded:
                key = loaded[name]
                exists = key not in self._stubs
            if key is not None:
                records.append(
                    NamedRecord(name, ref.link_flag, key, exists, ref.type_ids)
                )
        # A dependency of another kind of record (a Shared Variable's library,
        # which records a name and no path) is on the file's edges under the name
        # it was registered with.
        recorded_keys = {r.key for r in records}
        for dep in sorted(self._dep_graph.successors(file_key)):
            name = self._dep_graph.nodes[dep].get("qname") or ""
            if name.lower().endswith(_NAMED_TYPE_SUFFIXES) and dep not in recorded_keys:
                records.append(NamedRecord(name, None, dep, dep not in self._stubs))
        self._dep_graph.nodes[file_key]["type_records"] = TypeRecords(
            owners=tuple(metadata.owning_libraries) if metadata is not None else (),
            records=tuple(records),
            nested=tuple(sorted(set(loaded.values()) - {file_key})),
        )
        types = metadata.type_map.values() if metadata is not None else ()
        for lv_type in type_names(types):
            lv_type.file_key = self.recorded_type_key(file_key, lv_type)

    def _record_key(
        self, ref: ParsedDependencyRef, caller_file: Path
    ) -> tuple[str | None, bool]:
        """The key of the file a link record names, without loading it, and
        whether that file is on this machine: the file at its recorded path
        (``_locate_recorded``), else the intended path, as a load would key it.
        No key when the record names no path here."""
        try:
            for candidate in self.recorded_candidates(ref, caller_file):
                found = self._locate_recorded(candidate, ref.name)
                if found is not None:
                    return str(found.resolve()), True
        except AmbiguousVIReferenceError:
            return None, False
        intended = self._intended_dep_path(ref.name, ref, caller_file)
        return (str(intended) if intended is not None else None), False

    @_with_extraction_pool
    def load_lvlib(
        self,
        lvlib_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        owner_chain: list[str] | None = None,
    ) -> str:
        """Load all VIs from a .lvlib file. Returns the dep-graph KEY (the
        resolved ``.lvlib`` path) it was registered under — callers add
        ownership/reference edges to THIS key, never to the qname.

        Each member is the file at the URL the library records for it
        (``_member_file``); a member not there is a stub keyed by that
        path."""
        lvlib_path = Path(lvlib_path)
        lib = parse_lvlib(lvlib_path)

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
        self._dep_graph.add_node(lib_key, node_type=NodeType.LIBRARY)
        self._register_dep_node(lib_key, lib_path_r, lib_name, lib_qname)
        self._stubs.discard(lib_key)
        member_chain = chain + [lib_name]

        for member in lib.members:
            if not member.url:
                continue  # records no path: nothing to name or load
            member_name = Path(member.url).name
            recorded, found = self._member_file(lvlib_path, member.url)
            member_qname = lib_qname + ":" + member_name
            if member.member_type == "VI":
                if member_name.lower().endswith(".ctl"):
                    # .ctl members are type definitions, not loadable VIs
                    if found is not None:
                        ctl_key = self.load_typedef(found, typedef_qname=member_qname)
                        self._add_ownership_edge(lib_key, ctl_key)
                        continue
                    node_type = NodeType.TYPEDEF
                elif is_vi_file(member_name):
                    if found is not None:
                        member_key = self.load_vi_recording(found, mode)
                        # Ownership edge — to the member's RETURN key (its path).
                        if member_key and member_key in self._dep_graph:
                            self._add_ownership_edge(lib_key, member_key)
                        continue
                    node_type = NodeType.VI
                else:
                    # A member the library XML declares Type="VI" but whose URL
                    # is neither a VI file nor .ctl -- a source-metadata anomaly,
                    # not a code entity. No node, no stub (a stub means "a VI we
                    # expect to resolve later", which is wrong here): warn and
                    # move on rather than sending a non-VI file to load_vi
                    # (which raises ValueError and would abort the whole load).
                    self.record_file_problem(
                        lvlib_path,
                        Problem(
                            vi=self.vi_label(lvlib_path),
                            stage=ProblemStage.LOAD,
                            kind=ProblemKind.UNREADABLE,
                            reason=(
                                f'declares member {member.name!r} as Type="VI" but '
                                f"its URL {member.url!r} is not a loadable VI "
                                "(.vi/.vim/.vit) or .ctl file; it is skipped"
                            ),
                        ),
                    )
                    continue
            elif member.member_type == "LVClass":
                if found is not None:
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
                        found, mode, owner_chain=member_chain
                    )
                    self._add_ownership_edge(lib_key, member_key)
                    continue
                node_type = NodeType.CLASS
            else:  # "Library"
                if found is not None:
                    nested_key = self.load_lvlib(found, mode, owner_chain=member_chain)
                    self._add_ownership_edge(lib_key, nested_key)
                    continue
                node_type = NodeType.LIBRARY
            # Absent member: keyed by the path the library records for it
            # (computable without the file), so it stages/upgrades by
            # identity; the qname is indexed so references resolve here.
            self._add_stub(
                str(recorded),
                node_type,
                member_name,
                member_qname,
                caller=lib_key,
                rel=EdgeRel.OWNS,
            )

        return lib_key

    def member_file(self, container: Path, url: str) -> Path | None:
        """The file of a library or class member at its recorded URL, None
        when absent (``_member_file``)."""
        return self._member_file(container, url)[1]

    def _member_file(self, container: Path, url: str) -> tuple[Path, Path | None]:
        """The path a library or class records for a member and the member's
        file (None when absent) at the first path its URL can name
        (``member_url_paths``, each through ``_locate_recorded``)."""
        recorded, alternate = member_url_paths(container, url)
        for candidate in dict.fromkeys((recorded, alternate)):
            found = self._locate_recorded(candidate, candidate.name)
            if found is not None:
                return recorded, found
        return recorded, None

    @_with_extraction_pool
    def load_lvclass(
        self,
        lvclass_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
        owner_chain: list[str] | None = None,
    ) -> str:
        """Load a .lvclass file. Returns the dep-graph KEY (the resolved
        ``.lvclass`` path) it was registered under — mirrors ``load_lvlib``/
        ``load_typedef``: callers add ownership/reference edges to THIS key,
        never to the qname (``cls_qname``, built from the ON-DISK file's own
        stem casing — the CALLER may have referenced this class under a
        different casing, e.g. a type reference's recorded casing can differ
        from the file's actual casing, so the qname alone isn't a safe edge
        target; see ``_load_dependency``'s ``.lvclass`` branch).

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

        chain = list(owner_chain or [])
        cls_name = cls.name + ".lvclass"
        cls_qname = ":".join(chain + [cls_name]) if chain else cls_name

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
        # all 27 members. See ``_class_member_file``.
        # Record the member's path even when the file is NOT present yet: the
        # ``.lvclass`` URL (``../X.vi``, the class-as-directory quirk) gives its
        # INTENDED path, which is what progressive (web) staging needs to NAME +
        # fetch a not-yet-staged member. An ``.exists()``-gated resolve would name
        # nothing until the file is already staged — chicken-and-egg — so the web
        # closure would converge WITHOUT the SubVIs (they'd never be fetched).
        method_paths: dict[str, str] = {}
        for method in cls.methods:
            if not method.vi_path:
                continue  # records no path: nothing to name or load
            recorded, found = self._member_file(lvclass_path, method.vi_path)
            method_paths[Path(method.vi_path).name] = str(found or recorded)
        self._dep_graph.add_node(
            cls_key,
            node_type=NodeType.CLASS,
            fields=[],
            parent_class=cls.parent_class,
            parent_key=parent_key,
            fields_only=fields_only,
            version=cls.version,
            ancestors=cls.ancestors,
            method_paths=method_paths,
            wire_style=cls.wire_style,
        )
        self._register_dep_node(cls_key, cls_path_r, cls_name, cls_qname)
        self._stubs.discard(cls_key)
        # The class node must exist before its private-data control loads, so a
        # private-data type that references this class dedups on it.
        # The class's fields: its private-data control's, read once the node
        # exists; else the copy of its private data a method VI carries.
        self._dep_graph.nodes[cls_key]["fields"] = self._load_private_data_ctl(
            cls_key, cls, lvclass_path
        ) or self._inline_private_data_fields(cls)
        # The names its private data records are the private-data control's.
        private_data = self.class_private_data_key(cls_key)
        self._dep_graph.nodes[cls_key]["type_records"] = TypeRecords(
            owners=tuple(chain),
            records=(),
            nested=(private_data,) if private_data is not None else (),
        )

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
                )
                self._stubs.discard(parent_key)
            self._dep_graph.add_edge(cls_key, parent_key)
        elif parent_key is not None:
            # Parent recorded but not staged yet (web round 1): stub it by its
            # intended path so the closure NAMES it and staging fetches it; a
            # later round loads it for real and its methods become resolvable.
            # Not routed through _add_stub: this stub carries a ``fields_only``
            # node attribute the shared helper doesn't set, so it stays manual.
            assert parent is not None  # parent_key set => parent name is known
            if not self._dep_graph.has_node(parent_key):
                self._dep_graph.add_node(
                    parent_key,
                    node_type=NodeType.CLASS,
                    fields_only=True,
                )
                self._stubs.add(parent_key)
                self._register_dep_name(parent_key, parent + ".lvclass", parent)
            self._dep_graph.add_edge(cls_key, parent_key)

        if fields_only:
            return cls_key

        for method in cls.methods:
            if not method.vi_path:
                continue
            _, vi_path = self._member_file(lvclass_path, method.vi_path)
            if vi_path is not None:
                method_key = self.load_vi_recording(vi_path, mode)
                # Ownership edge — carries scope/property info for docs (class
                # landing pages, access-level badges). A method VI's identity is
                # its file path (the vi_key it loaded under), which is unique, so
                # attach the edge straight to it — no bare-name collision to lose
                # (path identity subsumes the old #18 source-path fallback).
                if method_key and method_key in self._dep_graph:
                    role = None
                    if method.property_name is not None:
                        inputs, _, outputs, _ = self.vi_interface(method_key)
                        role = property_role(
                            inputs,
                            outputs,
                            lambda t, ck=cls_key: self.is_class_wire(t.lv_type, ck),
                        )
                    self._add_ownership_edge(
                        cls_key,
                        method_key,
                        scope=method.scope,
                        is_accessor=method.property_name is not None,
                        accessor_type=role.value if role else None,
                        accessor_field=method.property_name,
                        is_static=method.is_static,
                        must_override=method.must_override,
                        must_call_parent=method.must_call_parent,
                    )

        return cls_key

    def _inline_private_data_fields(self, cls: LVClass) -> list[ClusterField]:
        """The class's private data as an inline "class private data" cluster
        found in a method VI's VCTP (structure.py::_parse_private_data_fields),
        converted by the same helper the render resolver's VI-own-inline-copy
        fallback uses — see structure.py::private_data_field_to_cluster_field.
        Empty when the class carries none."""
        return [private_data_field_to_cluster_field(f) for f in cls.private_data_fields]

    def _load_private_data_ctl(
        self,
        cls_key: str,
        cls: LVClass,
        lvclass_path: Path,
    ) -> list[ClusterField]:
        """The class's private-data control: load it as a typedef node, edge
        the class to it (``EdgeRel.PRIVATE_DATA``), and return its cluster
        fields. The control is its file at the URL the class records, else the
        copy a source-only class keeps inside itself
        (``embedded_private_data_copy``), at its path INSIDE the ``.lvclass``
        treated as a folder (``<X.lvclass>/<url>``; the paths it records for
        the files it references are relative to that path;
        ``docs/_internal/design/labview-binary-format.md``, member URLs). Runs
        AFTER the class node is registered, so a private data type that
        references its own class dedups on that node instead of recursing.
        Empty when the class records no control or neither file nor copy is
        there, or when the control is readable but has no cluster root: a
        control that isn't a cluster is not a class's private data, so it is
        not edged (it stays a typedef node). An unreadable control stays edged
        as a stub, so staging can still name it; the problem is on the
        control."""
        url = cls.private_data_url
        if not url:
            return []
        # The classes its fields name are needed for their types only.
        _, found = self._member_file(lvclass_path, url)
        if found is not None:
            ctl_key = self.load_typedef(found, mode=LoadMode.MINIMAL)
        else:
            inside = (lvclass_path / url).resolve()
            copy = embedded_private_data_copy(lvclass_path, inside.name)
            if copy is None:
                return []
            ctl_key = self.load_typedef(
                inside, source=copy, mode=LoadMode.MINIMAL
            )
        if ctl_key in self._stubs:
            pass  # the control's own node says why it could not be read
        elif not self._dep_graph.nodes[ctl_key].get("fields"):
            return []
        self._dep_graph.add_edge(cls_key, ctl_key, rel=EdgeRel.PRIVATE_DATA)
        return list(self._dep_graph.nodes[ctl_key].get("fields") or [])

    def _resolve_recorded_class_ref(
        self,
        parent_name: str,
        path_tokens: list[str],
        lvclass_path: Path,
        path_type: int | None = None,
    ) -> tuple[Path | None, Path | None]:
        """A parent class's recorded path TOKENS -> ``(parent_file,
        parent_intended)``, through ``ParsedDependencyRef.resolve_against`` —
        the same resolution every SubVI/type dependency goes through
        (``_resolve_dependency_path``). Shared by both recorded sources
        ``_resolve_parent`` reads: a ``<vilib>``-rooted plain-XML ``Parent``
        Item URL and the binary ``ParentClassLinkInfo`` record.
        ``parent_intended`` is the recorded path whether or not the file is
        there (so progressive staging can name and fetch it); ``None`` for a
        symbolic root with no configured folder."""
        dep_ref = ParsedDependencyRef(
            name=parent_name + ".lvclass", path_tokens=path_tokens, path_type=path_type
        )
        candidate = self._recorded_path(dep_ref, lvclass_path)
        if candidate is None:
            return None, None
        return self._locate_recorded(candidate, candidate.name), candidate

    def _resolve_parent(
        self, cls: LVClass, lvclass_path: Path
    ) -> tuple[Path | None, Path | None]:
        """The parent class's file (when present) and its INTENDED path
        (present or not) -> ``(parent_file, parent_intended)``, from the path
        the class records for its parent.

        PREFERS the parent's own recorded URL (a DIRECT relative path — the
        parent commonly lives in a sibling subtree, e.g.
        ``../../Layer/Layer.lvclass``) — UNLESS that URL is itself
        ``<vilib>``-rooted (``cls.is_vilib_parent``, e.g.
        ``"/<vilib>/ActorFramework/Actor/Actor.lvclass"``, verified against
        Event Receiver Actor.lvclass's real ``Parent`` Item — a LEADING
        SLASH, the marker, then forward-slash path components, NOT a normal
        relative path), in which case it's tokenized and resolved via
        ``_resolve_recorded_class_ref`` like any other vi.lib reference.

        When there's no URL but the parent's binary link record carries a
        path (``cls.parent_link_path``), resolves it the SAME way via
        ``_resolve_recorded_class_ref``: a ``<vilib>``-rooted record (e.g.
        ``["<vilib>", "ActorFramework", "Actor", "Actor.lvclass"]``) against
        the vi.lib root, an in-repo record (leading EMPTY tokens, each one
        ``..`` hop from the child ``.lvclass`` file treated as a directory,
        e.g. ``["", "", "", "Classes", "TestCase", "TestCase.lvclass"]`` from
        ``Tests/MyFirstTest/MyFirstTest.lvclass``) against the child's own
        location.

        Both None when the class has no parent (or its parent is the root
        ``LabVIEW Object``) or records no path for it.
        """
        parent = cls.parent_class
        if not parent or parent == "LabVIEW Object":
            return None, None
        if cls.parent_url and cls.is_vilib_parent:
            tokens = [t for t in cls.parent_url.split("/") if t]
            return self._resolve_recorded_class_ref(parent, tokens, lvclass_path)
        if cls.parent_url:
            # LabVIEW ``.lvclass`` URLs are relative to the class FILE treated
            # as a directory (members read ``../X.vi``), so resolve against
            # ``lvclass_path`` itself — that absorbs the leading ``..``.
            intended = (lvclass_path / cls.parent_url).resolve()
            return self._locate_recorded(intended, intended.name), intended
        if cls.parent_link_path:
            return self._resolve_recorded_class_ref(
                parent, cls.parent_link_path, lvclass_path, cls.parent_link_path_type
            )
        return None, None

    def load_lvproj(
        self,
        lvproj_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
    ) -> None:
        """Load all VIs referenced by a .lvproj file."""
        lvproj_path = Path(lvproj_path)
        proj = parse_lvproj(lvproj_path)

        # Absent project members are stubbed by their INTENDED resolved path
        # (mirroring load_lvlib's absent-member branches) so they're still
        # NAMED — get_dependency_paths / is_stub / progressive staging can
        # see them and upgrade by identity when the file appears. UNLIKE
        # load_lvlib's members, there is no ownership edge: a ``.lvproj`` is a
        # build/reference manifest, not itself a dep_graph node — load_lvproj
        # creates no project node to edge these members FROM.
        for lib_name, lib_path in get_project_libraries(proj):
            if lib_path.exists():
                self.load_lvlib(lib_path, mode)
            else:
                key = str(lib_path.resolve())
                self._add_stub(
                    key,
                    NodeType.LIBRARY,
                    lib_name,
                    lib_name,
                    caller=None,
                )

        for class_name, class_path in get_project_classes(proj):
            if class_path.exists():
                self.load_lvclass(class_path, mode)
            else:
                key = str(class_path.resolve())
                self._add_stub(
                    key,
                    NodeType.CLASS,
                    class_name,
                    class_name,
                    caller=None,
                )

        for vi_name, vi_path in get_project_vis(proj):
            if vi_path.exists():
                self.load_vi_recording(vi_path, mode)
            else:
                key = str(vi_path.resolve())
                self._add_stub(key, NodeType.VI, vi_name, vi_name, caller=None)

    @_with_extraction_pool
    def load_directory(
        self,
        dir_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
    ) -> None:
        """Load all VIs from a directory recursively."""
        dir_path = Path(dir_path)

        # Sorted: load order decides which same-named file claims a name first
        # when SubVI deps resolve, so filesystem order made loads irreproducible.
        vi_paths = rglob_vi_files(dir_path)

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
            self._parse_cache = parallel_parse_directory(vi_paths, self._extract_pool)

        try:
            for vi_path in vi_paths:
                self.load_vi_recording(vi_path, mode)

            for llb_path in sorted(dir_path.rglob("*.llb")):
                if llb_path.is_file():
                    self.load_llb(llb_path, mode)
        finally:
            self._parse_cache = None

    @_with_extraction_pool
    def load_llb(
        self,
        llb_path: Path | str,
        mode: LoadMode = LoadMode.FULL,
    ) -> None:
        """Load all VIs from an LLB container archive.

        If ``llb_path`` is already a directory (pre-extracted, as in the
        OpenG samples), delegates to ``load_directory()``.  Otherwise extracts
        the binary LLB to a cache directory and loads each extracted ``.vi``.
        """
        llb_path = Path(llb_path)
        if llb_path.is_dir():
            self.load_directory(llb_path, mode)
            return

        try:
            cache_dir, failures = extract_llb_members(llb_path)
        except RuntimeError as exc:
            self.record_load_failure(
                llb_path, UnreadableVIError(str(exc)), node_type=NodeType.LIBRARY
            )
            return
        for failure in failures:
            # A member that could not be extracted is a problem on its own VI,
            # at its path inside the archive; a failure of the whole archive's
            # content block belongs to the archive.
            member = llb_path / failure.member if failure.member else llb_path
            self.record_load_failure(
                member,
                UnreadableVIError(failure.reason),
                node_type=NodeType.VI if failure.member else NodeType.LIBRARY,
            )

        # Each member is the VI at its path inside the archive (its identity);
        # its bytes are the extracted copy (``llb_members``).
        for vi_path in glob_vi_files(cache_dir):
            self.load_vi_recording(llb_path / vi_path.relative_to(cache_dir), mode)

    def _resolve_through_llb(self, candidate: Path) -> Path | None:
        """``candidate`` when it names a member of a ``.llb`` archive on its
        path (``llb_member_copy``): the member's identity is that path, as
        LabVIEW records it; its bytes are read from the extracted copy
        (``vi_bytes_file``). None when no ``.llb`` on the path holds it."""
        return candidate if llb_member_copy(candidate) is not None else None

    def _pick_link_ref(
        self,
        candidates: list[ParsedDependencyRef] | None,
        caller_file: Path,
    ) -> ParsedDependencyRef | None:
        """Pick the best link-table ref out of several records that give one
        file one qualified name (``build_link_index``).

        Different link records can name the same file via DIFFERENT recorded
        paths, and the parse keeps each one. Prefer
        the one whose recorded path actually resolves to an EXISTING file
        against ``caller_file``; otherwise fall back to the first candidate
        (list order — deterministic, unlike a set)."""
        if not candidates:
            return None
        for ref in candidates:
            resolved = self._recorded_path(ref, caller_file)
            if resolved is not None and resolved.exists():
                return ref
        return candidates[0]

    def _recorded_dep_ref(
        self,
        qname: str,
        dep_ref_map: dict[str, ParsedDependencyRef],
        link_index: LinkIndex,
        caller_file: Path,
    ) -> ParsedDependencyRef | None:
        """The recorded path ref for a dependency: its own LinkSavePathRef, else
        the best link-table record of its file (``LinkIndex.records_of``: one
        that gives the file exactly this qualified name, else one that names no
        file and whose path ends in its file name). None when this VI records
        no such file: the type is nested in a dependency that records it."""
        return dep_ref_map.get(qname) or self._pick_link_ref(
            link_index.records_of(qname), caller_file
        )

    def _prefetch_dependencies(
        self,
        qnames: list[str],
        instance_sources: dict[str, ParsedDependencyRef],
        dep_ref_map: dict[str, ParsedDependencyRef],
        link_index: LinkIndex,
        caller_file: Path,
    ) -> dict[str, Path | None]:
        """Start extracting, in the pool, the SubVI and ``.ctl`` files this VI's
        dependency walk is about to load that are not in the graph yet; the walk
        then waits for its file in ``_extract`` and builds the graph in its own
        sorted order. Returns each resolved file by the qualified name
        ``_load_dependency`` is called with, so the walk does not resolve it
        again."""
        pool = self._extract_pool
        if pool is None or not pool.enabled:
            return {}
        resolved_paths: dict[str, Path | None] = {}
        files: list[Path] = []
        for qname in qnames:
            source = instance_sources.get(qname.rsplit(":", 1)[-1])
            if source is not None:
                name, ref = source.qualified_name or source.name, source
            else:
                name = qname
                ref = self._recorded_dep_ref(
                    qname, dep_ref_map, link_index, caller_file
                )
            leaf = name.rsplit(":", 1)[-1]
            if not (is_vi_file(leaf) or leaf.endswith(".ctl")):
                continue
            if class_owner_ref(name, ref) is not None:
                continue
            try:
                resolved = self._resolve_dependency_path(name, ref, caller_file)
            except DependencyFileError:
                continue  # ``_load_dependency`` resolves it again and records why
            resolved_paths[name] = resolved
            if resolved is not None and not self._dep_graph.has_node(
                str(resolved.resolve())
            ):
                files.append(vi_bytes_file(resolved))
        pool.prefetch(files)
        return resolved_paths

    def _extract(self, path: Path) -> tuple[Path, Path | None, Path | None]:
        """``extract_vi_xml`` for a file the pool may be extracting: wait for
        that extraction first, so this call finds it cached."""
        source = vi_bytes_file(path)
        if self._extract_pool is not None:
            self._extract_pool.wait(source)
        return extract_vi_xml(source)

    def _own_owner_chain(
        self, metadata: ParsedVIMetadata, caller_file: Path
    ) -> list[str]:
        """The VI's own owner chain (its ``LIBN`` block) with its own class or
        library named by its file: the file its ``VILB`` record's path names,
        as it is spelled on disk (a class is named by its file; the VI may
        spell it in another case). Found on disk only, never loaded. The
        ``LIBN`` spelling when that file is not here."""
        owners = list(metadata.owning_libraries)
        if not owners:
            return owners
        chain = ":".join(owners).casefold()
        for ref in metadata.link_path_refs:
            if ref.qualified_name is None or ref.qualified_name.casefold() != chain:
                continue
            for candidate in self.recorded_candidates(ref, caller_file):
                try:
                    found = _case_insensitive_match(candidate.parent, candidate.name)
                except AmbiguousVIReferenceError:
                    found = None
                if found is not None:
                    return [*owners[:-1], found.name]
        return owners

    def _already_handled(
        self, vi_key: str, visited: set[str], mode: LoadMode
    ) -> tuple[bool, str | None]:
        """Whether ``vi_key`` needs no load, and what the load returns then:
        ``None`` for a VI already visited in this load, else ``vi_key`` for one
        already loaded at a depth covering ``mode``. A VI loaded shallower is
        not handled: it falls through to re-walk its dependencies at the deeper
        mode (adds the missing SubVI loads + call edges); the graph-node build
        is guarded so it runs once and the node/metadata adds are idempotent."""
        if vi_key in visited:
            return True, None
        if vi_key in self._loaded_vis and _mode_covers(
            self._dep_load_mode.get(vi_key), mode
        ):
            return True, vi_key
        return False, None

    def _load_vi_recursive(
        self,
        bd_xml: Path,
        fp_xml: Path | None,
        main_xml: Path | None,
        visited: set[str],
        *,
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
        # A pinned ``source_override`` fixes the identity before parsing, so a
        # VI that is already visited or loaded is skipped without being parsed.
        vi_key = str(source_override.resolve()) if source_override is not None else None
        if vi_key is not None:
            handled, result = self._already_handled(vi_key, visited, mode)
            if handled:
                return result
        cached_vi = self._parse_cache.get(str(bd_xml)) if self._parse_cache else None
        vi = (
            cached_vi
            if cached_vi is not None
            else parse_vi(
                bd_xml=bd_xml,
                fp_xml=fp_xml if fp_xml and fp_xml.exists() else None,
                main_xml=main_xml if main_xml and main_xml.exists() else None,
                # Pin the parser's identity (name, source_path, warnings) to the
                # real source path, not the fixed ``vi_BDHb.xml`` cache name.
                source_override=source_override,
                layout=self._want_layout,
            )
        )

        metadata = vi.metadata
        bd = vi.block_diagram
        fp = vi.front_panel
        conpane = vi.connector_pane

        # The VI's real unqualified name comes from its SOURCE path when we have
        # one (``source_override``), NOT from the extracted BD XML filename: the
        # managed extraction cache names every artifact ``vi_BDHb.xml`` (a bounded
        # fixed name — see ``extractor.extract_vi_xml``), so deriving the name from
        # the XML would make every VI ``vi.vi``. The source path is authoritative
        # and is threaded here for the top-level VI (``load_vi``) and every SubVI
        # dependency (``_leaf_load_vi``). Only a raw ``*_BDHb.xml`` handed in
        # directly (no source path) falls back to the XML-derived name.
        unqualified_name = (
            source_override.name
            if source_override is not None
            else bd_xml.name.replace("_BDHb.xml", ".vi")
        )
        # own_qname is this VI's (possibly NON-unique) qualified name — kept only
        # for the self-dependency compare below and the qname reverse index. It
        # is NOT the identity: two on-disk copies (a source VI + its stripped
        # built copy) can share a qname while being distinct files.
        # caller_file is the VI file itself (not its directory): each
        # leading empty in a LinkSavePathRef pops one level from it. It also
        # doubles as the source-path fallback below: metadata.source_path is
        # only set when a real ``.vi`` sibling sits next to the extracted
        # heap XML, which is true for dependencies resolved to an on-disk
        # file but NOT for the top-level VI passed to load_vi() (its
        # heap XML lives in a temp extraction dir with no ``.vi`` sibling).
        caller_file = (
            source_dir / unqualified_name
            if source_dir is not None
            else bd_xml.parent / unqualified_name
        )
        own_qname = file_qualified_name(
            self._own_owner_chain(metadata, caller_file), unqualified_name
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
        if vi_key is None:
            vi_key = str(source_file.resolve())
            handled, result = self._already_handled(vi_key, visited, mode)
            if handled:
                return result

        visited.add(vi_key)

        self._register_dep_node(vi_key, source_file, unqualified_name, own_qname)

        # Retain geometry decoded during this parse (layout=True loads only).
        if vi.layout is not None:
            self._layouts[vi_key] = vi.layout

        wiring_rules = vi.wiring_rules

        type_map = metadata.type_map

        # Polymorphic info, library membership and VI Properties, from parse_vi
        poly_metadata = vi.file_metadata
        if poly_metadata is not None:
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
            self._vi_dotnet_assemblies[vi_key] = VIDotNetAssemblies(
                assemblies=metadata.dotnet_assemblies,
                unreadable=[
                    *(f.reason for f in metadata.dotnet_assembly_failures),
                    *sorted(
                        {
                            t.dotnet_type_unreadable
                            for t in vi.block_diagram.terminal_info.values()
                            if t.dotnet_type_unreadable
                        }
                    ),
                ],
            )

        # Add to dependency graph, carrying the VI's own display identity on its
        # node (a node created here is not yet there when the identity is
        # registered above).
        self._dep_graph.add_node(vi_key, name=unqualified_name, qname=own_qname)
        self._name_by_owner(vi_key)

        # Mark as loaded, and record the depth we're loading its deps at — BEFORE
        # the dependency walk below, so a cyclic callee that re-enters this VI at
        # the same (or lower) depth is covered and short-circuits instead of
        # recursing forever. Recording it after the walk would reintroduce the
        # infinite recursion the old unconditional _loaded_vis check prevented.
        self._loaded_vis.add(vi_key)
        self._stubs.discard(vi_key)
        self.clear_problems(vi_key)
        for uid in vi.front_panel.unlabeled_controls if vi.front_panel else ():
            self.record_problem(
                vi_key,
                Problem(
                    vi=self.vi_label(source_file),
                    stage=ProblemStage.LOAD,
                    kind=ProblemKind.UNLABELED_CONTROL,
                    reason=(
                        f"control uid={uid} records no label (its own label part, "
                        "the flat type's Label and the connector pane name none); "
                        f"it is named control_{uid}"
                    ),
                ),
                may_stop=False,
            )
        self._dep_load_mode[vi_key] = mode

        # Build dep_ref_map from recorded LinkSavePathRef data.
        # Used for both dependency loading and iUse path diagnostics.
        dep_ref_map: dict[str, ParsedDependencyRef] = (
            build_dep_ref_map(metadata.dependency_refs)
            if main_xml and main_xml.exists()
            else {}
        )

        # Each malleable-VI call's recorded instance name -> the qualified name
        # of the file it is an instance of (filled while loading dependencies).
        instance_qnames: dict[str, str] = {}
        # Each dependency's identity (see ``_load_dependency``) by the qualified
        # name the caller's link table records for it.
        dep_keys: dict[str, str] = {}
        # Each call's callee, by the heap uid of the call node: the file its
        # link-table record names (a polymorphic call's record names a variant;
        # its polymorphic VI is under ``poly_to_callee_key``). Empty when the
        # dependencies are not loaded (``LoadMode.NONE``).
        iuse_to_callee_key: dict[str, str] = {}
        poly_to_callee_key: dict[str, str] = {}
        # Each dispatch call's callee qualified name, from its ``DyOM`` record.
        dispatch_qnames: dict[str, str] = {}

        # Load all dependencies through the single generic walker. NONE stops
        # here — the target VI only, no dependencies at all.
        if mode is not LoadMode.NONE and main_xml and main_xml.exists():
            # Collect all dependency qnames: SubVI/class refs + the class and
            # typedef files the link records name.
            # MINIMAL and FULL both collect the same set — what differs is DEPTH
            # (see _load_dependency): MINIMAL leaf-loads each SubVI (its connector
            # pane, for param names; no recursion into ITS SubVIs) and field-loads
            # each class (no methods); FULL loads the whole transitive tree.
            all_dep_qnames = collect_direct_dep_qnames(
                metadata.subvi_qualified_names, own_qname
            )
            # Each class and typedef record is a dependency at its recorded path.
            for qname, ref in type_file_records(metadata.link_path_refs).items():
                dep_ref_map.setdefault(qname, ref)
            # Union the recorded LinkSavePathRef deps too. The call table
            # (subvi_qualified_names) can be EMPTY on some VIs — e.g.
            # a file whose VITS block doesn't decode (issue #29's Test.vi) — yet
            # the diagram still references those SubVIs and the render resolves +
            # reads each one. dependency_refs is LabVIEW's own complete record of
            # what the file references, so enumerating only the call table lets a
            # referenced SubVI vanish from _dep_graph entirely (never even a
            # stub) and the render degrades to a bare box. Progressive load then
            # resolves each as usual: present -> path-keyed node, absent -> qname
            # stub whose recorded path ``RecordedPaths`` holds (as good as known
            # until we know better). Guards test_issue_corpus.py::…issue29….
            all_dep_qnames.update(dep_ref_map)

            # A qualified-name -> recorded-path index from LabVIEW's link
            # tables (``build_link_index``): supplies a recorded PATH for a
            # call-table dep that carries no LinkSavePathRef of its own, from a
            # record that gives its file exactly that name. It only ENRICHES
            # deps already in all_dep_qnames — never adds one. List-valued:
            # several records can name one file by DISTINCT recorded paths, all
            # available to _pick_link_ref below.
            link_index = build_link_index(
                metadata.link_path_refs, metadata.owning_libraries
            )

            # A malleable-VI call is recorded as an embedded instance named by a
            # GUID; the file it is an instance of is what the call is to.
            instance_sources = read_instance_sources(
                main_xml.parent, metadata.instance_files
            )

            # Sorted: dependency load ORDER decides which same-named candidate
            # file claims a name first, so a hash-ordered set here made the
            # loaded VI SET non-deterministic across runs (146 vs 109 on the
            # JKI Programmatic API tree). Deterministic order → reproducible docs.
            resolved_paths = self._prefetch_dependencies(
                sorted(all_dep_qnames),
                instance_sources,
                dep_ref_map,
                link_index,
                caller_file,
            )
            for qname in sorted(all_dep_qnames):
                instance_source = instance_sources.get(qname.rsplit(":", 1)[-1])
                if instance_source is not None:
                    dep_ref_map[qname] = instance_source
                    instance_qnames[qname], instance_key = (
                        self._load_instance_dependency(
                            instance_source,
                            caller_file,
                            caller_qname=vi_key,
                            mode=mode,
                            resolved_paths=resolved_paths,
                        )
                    )
                    if instance_key is not None:
                        dep_keys[qname] = instance_key
                    continue
                dep_ref = self._recorded_dep_ref(
                    qname, dep_ref_map, link_index, caller_file
                )
                dep_key = self._load_dependency(
                    qname,
                    dep_ref,
                    caller_file,
                    caller_qname=vi_key,
                    mode=mode,
                    resolved_paths=resolved_paths,
                )
                if dep_key is not None:
                    dep_keys[qname] = dep_key

            iuse_to_callee_key = {
                uid: dep_keys[qname]
                for uid, qname in metadata.iuse_to_qualified_name.items()
                if qname in dep_keys
            }
            poly_to_callee_key = {
                uid: dep_keys[ref.qualified_name]
                for uid, ref in metadata.poly_refs.items()
                if ref.qualified_name in dep_keys
            }
            poly_info = self._poly_info.get(vi_key)
            if poly_info is not None:
                poly_info.variant_keys = [
                    dep_keys[variant]
                    for variant in poly_info.variants
                    if variant in dep_keys
                ]
            # A recursive call's link record names no file: it calls this VI.
            for uid in metadata.self_call_uids:
                iuse_to_callee_key[uid] = vi_key
            # A static VI reference names the dependency its caller records under
            # the reference's own name (the ``BSVR`` record).
            # The reference is labelled with the full qualified name; a record
            # in the caller's own library omits that library.
            own_library = ":".join(metadata.owning_libraries) or None
            by_label: dict[str, str] = {}
            for qname, dep_key in dep_keys.items():
                by_label.setdefault(qname, dep_key)
                if own_library:
                    by_label.setdefault(f"{own_library}:{qname}", dep_key)
            for pnode in bd.nodes:
                if pnode.node_type == "statVIRef" and pnode.name in by_label:
                    iuse_to_callee_key[pnode.uid] = by_label[pnode.name]
            # A dynamic-dispatch call is listed under its ``dynLink`` uid by the
            # ``DyOM`` record naming the class method it calls.
            for pnode in bd.nodes:
                link_uid = getattr(pnode, "dyn_link_uid", None)
                dyn_ref = metadata.dyn_refs.get(link_uid) if link_uid else None
                if dyn_ref is None:
                    continue
                dispatch_qnames[pnode.uid] = dyn_ref.qualified_name or dyn_ref.name
                if dyn_ref.qualified_name in dep_keys:
                    iuse_to_callee_key[pnode.uid] = dep_keys[dyn_ref.qualified_name]

        self._store_type_records(vi_key, metadata, dep_keys, caller_file)

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
                iuse_to_qname={
                    **{
                        uid: instance_qnames.get(qname, qname)
                        for uid, qname in metadata.iuse_to_qualified_name.items()
                    },
                    **dispatch_qnames,
                },
                iuse_to_qpath=iuse_to_qpath,
                iuse_to_callee_key=iuse_to_callee_key,
                poly_to_callee_key=poly_to_callee_key,
                iuse_refs=metadata.iuse_refs,
                shared_variable_links=metadata.shared_variable_links,
                shared_variable_failures=metadata.shared_variable_failures,
            )
        else:
            self._stamp_callee_keys(vi_key, iuse_to_callee_key, poly_to_callee_key)

        return vi_key

    def _stamp_callee_keys(
        self,
        vi_key: str,
        iuse_to_callee_key: dict[str, str],
        poly_to_callee_key: dict[str, str],
    ) -> None:
        """Give the call nodes of a VI that was already built -- first loaded
        with no dependencies -- the callees its dependency walk has now found."""
        for node_id in sorted(self._vi_nodes[vi_key]):
            node = self._graph.nodes[node_id].get("node")
            if not isinstance(node, VINode) or node.id == node.vi_path:
                continue
            callee_key, variant_key = callee_keys(
                node.node_type,
                node_id.rsplit("::", 1)[-1],
                iuse_to_callee_key,
                poly_to_callee_key,
            )
            if callee_key is not None:
                node.callee_key = callee_key
            if variant_key is not None:
                node.variant_key = variant_key

    def _ctl_root(
        self,
        ctl_path: Path,
        source: Path,
    ) -> tuple[
        LVType | None,
        dict[int, LVType],
        ParsedFrontPanel | None,
        ParsedVIMetadata | None,
    ]:
        """The root type + full type_map + front-panel geometry of a control
        (.ctl). The single ``.ctl`` extraction, used by ``load_typedef`` for a
        standalone typedef AND (through it) for a class whose private data is
        a ``.ctl`` control rather than an inline cluster. The root is the
        control's own type -- a cluster (``.fields``), an enum
        (``.values``), a ring, a scalar, ... -- so a non-cluster control keeps
        its type. ``metadata`` carries the paths the control records for the
        files it references. ``source`` is the file read for the control at
        ``ctl_path`` (``load_typedef``); a failure is recorded on ``ctl_path``.
        Returns ``(None, {}, None, None)`` when the control's XML can't be
        produced.

        ``front_panel`` is the control's OWN front-panel layout (real
        per-control bounds + nested cluster field geometry, see
        ``ParsedFPControl.cluster_geom``) — decoded by the SAME
        ``parse_vi``/``_parse_front_panel`` path a VI's own front panel uses;
        a ``.ctl``'s FPHb carries the identical shape (verified: this needs
        no special-casing, just feeding the SAME ``bd_xml``/``fp_xml``/
        ``main_xml`` triple ``extract_vi_xml`` already produces for a
        typedef's near-empty block diagram). None when ``fp_xml`` is absent
        or carries no parseable front panel.
        """
        # Guard the whole extract+parse. A control can extract to XML that is
        # then malformed, so parse_vi / _get_fp_root_type_id can raise
        # ET.ParseError (a SyntaxError subclass — NOT an OSError/ValueError) or
        # ValueError. Honor this method's documented "(None, {}, None, None)
        # on failure" contract for those too (load_typedef relies on it).
        try:
            bd_xml, fp_xml, main_xml = self._extract(source)
            if not (main_xml and main_xml.exists()):
                self.record_load_failure(
                    ctl_path,
                    UnreadableVIError("no control data could be read", path=ctl_path),
                    node_type=NodeType.TYPEDEF,
                )
                return None, {}, None, None
            root_type_id = _get_fp_root_type_id(fp_xml)
            parsed = parse_vi(bd_xml=bd_xml, fp_xml=fp_xml, main_xml=main_xml)
            # The types the control's records are applied to
            # (``_store_type_records``) are the ones its root and fields are.
            type_map = parsed.metadata.type_map
        except (RuntimeError, OSError, ValueError, ET.ParseError) as exc:
            # Only an unreadable file is "unreadable"; anything else (a malformed
            # extraction, a parse bug) is an lvkit error. Either way the control's
            # node carries it and the contract below holds.
            self.record_load_failure(ctl_path, exc, node_type=NodeType.TYPEDEF)
            return None, {}, None, None
        if root_type_id is None:
            root_type_id = 1  # cluster control default
        return (
            type_map.get(root_type_id),
            type_map,
            parsed.front_panel,
            parsed.metadata,
        )

    @_with_extraction_pool
    def load_typedef(
        self,
        ctl_path: Path | str,
        typedef_qname: str | None = None,
        source: Path | None = None,
        mode: LoadMode = LoadMode.FULL,
    ) -> str:
        """Load a .ctl typedef and add it to the dep_graph as a ``typedef`` node
        carrying its ``root_type``, cluster ``fields`` and ``front_panel``.
        Returns the dep-graph KEY (the resolved .ctl path) it registered under;
        the node is a STUB when the file is absent or can't be read as a control.

        As for load_vi / load_lvclass / load_lvlib, the path is the typedef's
        identity: a control already loaded at that path is returned as it is, so
        each control is one node. Its name and qualified name are display text.
        ``source`` is the file to read when it is not ``ctl_path`` itself (the
        copy of a private-data control a class keeps inside itself); the
        control's identity, and the paths its records resolve against, stay
        ``ctl_path``. ``mode`` is how the files the control's records name are
        loaded.
        """
        ctl_path = Path(ctl_path)
        source = ctl_path if source is None else source

        qname = typedef_qname or ctl_path.name
        # PATH-keyed even when absent: the intended resolved path is computable
        # and lets the stub upgrade by identity when the file appears.
        ctl_path_r = ctl_path.resolve()
        ctl_key = str(ctl_path_r)
        if self._dep_graph.has_node(ctl_key) and ctl_key not in self._stubs:
            return ctl_key

        if not source.exists():
            # Not routed through _add_stub: load_typedef returns the key and
            # is edged by its CALLER, not internally (unlike a caller-edging
            # stub site).
            self._dep_graph.add_node(ctl_key, node_type=NodeType.TYPEDEF)
            self._stubs.add(ctl_key)
            self._register_dep_name(ctl_key, ctl_path.name, qname)
            return ctl_key

        root, type_map, front_panel, metadata = self._ctl_root(ctl_path, source)
        if type_map and root is not None:
            # A loaded control is named as a VI is: its own owner chain (its
            # ``LIBN`` block, its own class named by its file) and its file name.
            qname = file_qualified_name(
                self._own_owner_chain(metadata, ctl_path)
                if metadata is not None
                else [],
                ctl_path.name,
            )
            self._dep_graph.add_node(
                ctl_key,
                node_type=NodeType.TYPEDEF,
                fields=root.fields,
                root_type=root,
                front_panel=front_panel,
            )
            self._register_dep_node(ctl_key, ctl_path_r, ctl_path.name, qname)
            self._stubs.discard(ctl_key)

            # Recurse: load the class and typedef files the control's link
            # records name (e.g. a cluster field whose type is an lvclass or
            # ctl), each at the path the control records for it; a type the
            # control records no path for is nested in one it does.
            records = (
                type_file_records(metadata.link_path_refs)
                if metadata is not None
                else {}
            )
            loaded: dict[str, str] = {}
            for name, ref in sorted(records.items()):
                own = self._resolve_dependency_path(name, ref, ctl_path)
                if own is not None and own.resolve() == ctl_path_r:
                    loaded[name] = ctl_key  # the control's own type
                    continue
                dep_key = self._load_dependency(
                    name, ref, ctl_path, caller_qname=ctl_key, mode=mode
                )
                if dep_key is not None:
                    loaded[name] = dep_key
            self._store_type_records(ctl_key, metadata, loaded, ctl_path)
        else:
            # XML not produced, or no root type — stub with what we know
            if not self.problems([ctl_key]):
                self.record_load_failure(
                    ctl_path,
                    UnreadableVIError("the control has no root type", path=ctl_path),
                    node_type=NodeType.TYPEDEF,
                )
            self._dep_graph.add_node(ctl_key, node_type=NodeType.TYPEDEF)
            self._stubs.add(ctl_key)
            self._register_dep_name(ctl_key, ctl_path.name, qname)
        return ctl_key

    def _recorded_path(
        self, dep_ref: ParsedDependencyRef, caller_file: Path
    ) -> Path | None:
        """The path a record names on this machine (``recorded_candidates``):
        the first that exists, else the first (the intended path of a missing
        file); None for a symbolic root with no folder configured or a drive
        this machine has not."""
        candidates = self.recorded_candidates(dep_ref, caller_file)
        if not candidates:
            return None
        return next((c for c in candidates if c.exists()), candidates[0])

    def _locate_recorded(self, candidate: Path, leaf: str) -> Path | None:
        """The file at a dependency's recorded path ``candidate``: the file
        itself, else a member of a ``.llb`` archive on that path
        (``_resolve_through_llb``), else the one file in that folder whose
        name differs only in case (LabVIEW file names are case-insensitive;
        several raise ``AmbiguousVIReferenceError``). A recorded class or
        library path whose dependency is a MEMBER (``leaf``) is the member's
        file beside it (``redirect_member_beside_container``). None when
        nothing is there: the dependency is missing."""
        candidate = redirect_member_beside_container(candidate, leaf)
        if candidate.exists():
            return candidate
        resolved = self._resolve_through_llb(candidate)
        if resolved is None:
            resolved = _case_insensitive_match(candidate.parent, candidate.name)
        return resolved

    def _resolve_dependency_path(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
    ) -> Path | None:
        """The file of a dependency: at the path its record names, in each
        folder of its pseudo path in turn (``recorded_candidates``,
        ``_locate_recorded``), else on the VI Search Path by the recorded path's
        tail (``graph/search_path.py``), recorded as a problem that names both.
        None when it records no path or no file is found. The path-resolution
        half of ``_load_dependency``."""
        if dep_ref is None:
            return None
        leaf = qualified_name.rsplit(":", 1)[-1]
        for candidate in self.recorded_candidates(dep_ref, caller_file):
            found = self._locate_recorded(candidate, leaf)
            if found is not None:
                return found
        return self._search_for(dep_ref, qualified_name, caller_file)

    def _search_for(
        self, dep_ref: ParsedDependencyRef, qualified_name: str, caller_file: Path
    ) -> Path | None:
        """The file the VI Search Path holds for a record that names no file
        here, found by its recorded path's tail among the files with the
        qualified name the caller records; the find is a problem of the caller
        (its record names another place), as LabVIEW warns of a file loaded
        from elsewhere."""
        leaf = qualified_name.rsplit(":", 1)[-1]
        tokens = list(dep_ref.path_tokens or [])
        # The tail drops the record's root: a pseudo path, or the drive of an
        # absolute path; a relative path's leading empty tokens are ``..``.
        if tokens and (
            is_symbolic_root(tokens[0]) or dep_ref.path_type == ABSOLUTE_PATH_TYPE
        ):
            tokens = tokens[1:]
        tail = [t for t in tokens if t]
        if not tail:
            return None
        if tail[-1].lower() != leaf.lower():
            # A member recorded by its library or class path: the member's own
            # file sits beside it.
            tail = [*tail[:-1], leaf]
        found = self._search_path.find(
            tail, lambda path: _has_qualified_name(path, qualified_name)
        )
        if found is None:
            return None
        recorded = "/".join(dep_ref.path_tokens)
        self.record_file_problem(
            caller_file,
            Problem(
                vi=self.vi_label(caller_file),
                stage=ProblemStage.LOAD,
                kind=ProblemKind.FOUND_ELSEWHERE,
                reason=(
                    f"{leaf} is not at its recorded path {recorded!r}; "
                    f"loaded {self.vi_label(found)} from the VI search path"
                ),
            ),
            may_stop=False,
        )
        return found

    def _class_member_file(
        self,
        owner_ref: ParsedDependencyRef,
        method: str,
        caller_file: Path,
        caller_qname: str | None,
    ) -> Path | None:
        """The file of the class method a call's record names: the class is the
        file its recorded path names (loaded here when no call has loaded it),
        and the method is that class's member, else the nearest ancestor's
        (``class_method``). The member sits wherever the class lists it -- in a
        subfolder, or in an ancestor's folder. A class not on disk is stubbed
        by its recorded path (so a progressive load names it) and its method is
        missing. ``None`` when no class lists the file."""
        owner_name = owner_ref.qualified_name or owner_ref.name
        class_path = self._resolve_dependency_path(owner_name, owner_ref, caller_file)
        cls_key = str(class_path.resolve()) if class_path is not None else None
        if (
            cls_key is not None
            and self._dep_graph.has_node(cls_key)
            and cls_key not in self._stubs
        ):
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, cls_key)
        else:
            cls_key = self._load_dependency(
                owner_name,
                owner_ref,
                caller_file,
                caller_qname=caller_qname,
                mode=LoadMode.MINIMAL,
            )
            if class_path is None or cls_key is None:
                return None
        found = self.class_method(cls_key, method)
        if found is not None:
            return Path(found[1])
        # A method file the class does not list is still its method when the file
        # sits beside the class and its own ``VILB`` record's path is this class
        # file (JKI-VI-Tester ``Prototype/.../MyTestCase/MyNewVI.vi``: its VILB
        # names ``/MyClass.lvclass``, which does not list it).
        if class_path is not None:
            beside = class_path.parent / method
            if beside.is_file() and self._records_owner(beside, class_path):
                return beside
        return None

    def _records_owner(self, vi_path: Path, owner_path: Path) -> bool:
        """Whether the VI at ``vi_path`` records, in its ``VILB`` record, the
        class or library file ``owner_path`` as its own (by recorded path)."""
        try:
            _, _, main_xml = extract_vi_xml(vi_path)
        except (UnreadableVIError, CacheWriteError):
            return False
        if main_xml is None:
            return False
        own = own_container_record(main_xml)
        if own is None:
            return False
        target = owner_path.resolve()
        return any(
            c.resolve() == target for c in self.recorded_candidates(own, vi_path)
        )

    def _class_method_stub_key(
        self,
        owner_ref: ParsedDependencyRef,
        method: str,
        caller_file: Path,
    ) -> str:
        """The dependency key of a class method no class lists. The file has no
        path, so the key is not one: the owning class's qualified name, then
        the class's identity (the class file when on disk, else the path the
        record intends, else nothing), then the method. Two different classes
        of one name never share the method's stub."""
        owner_name = owner_ref.qualified_name or owner_ref.name
        class_path = self._resolve_dependency_path(owner_name, owner_ref, caller_file)
        if class_path is None:
            class_path = self._intended_dep_path(owner_name, owner_ref, caller_file)
        if class_path is None:
            return f"{owner_name}:{method}"
        return f"{owner_name}@{class_path.resolve()}:{method}"

    def _intended_dep_path(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
    ) -> Path | None:
        """The resolved-or-INTENDED absolute path of a dependency, even when the
        file is ABSENT — used to key a STUB by identity so it upgrades
        progressively (inv. 3) and the web loop learns the path to fetch. Pure
        path math (``_recorded_path``), no file need exist. Returns None for a
        symbolic-root ref (``<vilib>``, ``<resource>``, … with no
        configured root), a drive this machine has not, or an absolute path
        with no file on this machine (another machine's folder, never shown
        or staged): those have no local path and must never be staged into
        ``/proj``. Applies the same
        member-beside-container redirect (G1) as ``_resolve_dependency_path`` so
        a member ``.ctl``/``.vi`` sits beside its container, not joined into
        it."""
        if dep_ref is None:
            return None
        cand = self._recorded_path(dep_ref, caller_file)
        if cand is None:
            return None
        leaf = qualified_name.rsplit(":", 1)[-1]
        cand = redirect_member_beside_container(cand, leaf)
        return cand.resolve()

    def _load_instance_dependency(
        self,
        source: ParsedDependencyRef,
        caller_file: Path,
        caller_qname: str | None,
        mode: LoadMode,
        resolved_paths: dict[str, Path | None] | None = None,
    ) -> tuple[str, str | None]:
        """Load the file a malleable-VI call is an instance of, as an ordinary
        SubVI of the caller. Returns the qualified name the call's node should
        carry (the loaded file's own, else the one the instance records) and
        the file's identity (see ``_load_dependency``)."""
        qualified_name = source.qualified_name or source.name
        key = self._load_dependency(
            qualified_name,
            source,
            caller_file,
            caller_qname=caller_qname,
            mode=mode,
            resolved_paths=resolved_paths,
        )
        try:
            target = self._resolve_dependency_path(qualified_name, source, caller_file)
        except DependencyFileError:
            target = None
        if target is not None:
            return (
                self._vi_display_names.get(str(target.resolve()), qualified_name),
                key,
            )
        return qualified_name, key

    def _load_dependency(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
        caller_qname: str | None = None,
        mode: LoadMode = LoadMode.FULL,
        resolved_paths: dict[str, Path | None] | None = None,
    ) -> str | None:
        """Load one dependency, whatever kind of file it is and however it was
        reached, and record the path its caller recorded for it. Returns the
        dependency's identity (see ``_load_dependency_node``)."""
        key = self._load_dependency_node(
            qualified_name,
            dep_ref,
            caller_file,
            caller_qname=caller_qname,
            mode=mode,
            resolved_paths=resolved_paths,
        )
        if key is not None:
            self._note_recorded_path(key, dep_ref, qualified_name, caller_qname)
        return key

    def _load_dependency_node(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
        caller_qname: str | None = None,
        mode: LoadMode = LoadMode.FULL,
        resolved_paths: dict[str, Path | None] | None = None,
    ) -> str | None:
        """Load one dependency by its LabVIEW qualified name and optional path ref.
        Returns the dependency's identity: its resolved file path, else the
        path its caller recorded for it (the key of the node the load edged
        to). None when the dependency has no local path (a ``<vilib>`` ref
        with no configured root).

        Single entry point for all dependency loading: SubVI calls, class refs,
        typedef refs, and library refs all funnel through here.

        Uses the recorded LinkSavePathRef for resolution
        (``_resolve_dependency_path``: the recorded path, then the VI Search Path
        by its tail).
        ``resolved_paths`` holds files the caller already resolved by qualified
        name (see ``_prefetch_dependencies``).

        LabVIEW's one-qname-per-memory invariant means the dep_graph node check
        at the top is the definitive dedup — resolution only runs on first visit.

        A dependency whose file cannot be determined (several files of its name,
        a candidate that cannot be read) is missing, with the reason recorded
        (``_stub_undetermined``); the load goes on.
        """
        try:
            return self._load_dependency_file(
                qualified_name,
                dep_ref,
                caller_file,
                caller_qname,
                mode,
                resolved_paths,
            )
        except DependencyFileError as exc:
            return self._stub_undetermined(
                qualified_name, dep_ref, caller_file, caller_qname, exc
            )

    def _load_dependency_file(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
        caller_qname: str | None,
        mode: LoadMode,
        resolved_paths: dict[str, Path | None] | None,
    ) -> str | None:
        """Resolve and load one dependency (see ``_load_dependency_node``)."""
        leaf = qualified_name.rsplit(":", 1)[-1]
        owner_ref = class_owner_ref(qualified_name, dep_ref)
        if owner_ref is not None:
            resolved = self._class_member_file(
                owner_ref, leaf, caller_file, caller_qname
            )
        elif resolved_paths is not None and qualified_name in resolved_paths:
            resolved = resolved_paths[qualified_name]
        else:
            resolved = self._resolve_dependency_path(
                qualified_name, dep_ref, caller_file
            )

        # PATH is the identity (finishes #26). The dep-graph key is ALWAYS the
        # resolved file path — never the qname or the bare name. A built copy
        # beside its source twin shares a qname; two genuinely different VIs
        # share a bare name (Lib1/Do.vi vs Lib2/Do.vi); ONLY the path is unique,
        # so ONLY the path dedups. Dedup on has_node(<path>).
        if resolved is not None:
            dep_key = str(resolved.resolve())
            # Resolved against the caller's own recorded path BEFORE the file's
            # children load, so they resolve against it in turn and the catalog
            # stop below reads it.
            self._note_recorded_path(dep_key, dep_ref, qualified_name, caller_qname)
            if self._dep_graph.has_node(dep_key):
                if caller_qname:
                    self._dep_graph.add_edge(caller_qname, dep_key)
                # A fields-only class placeholder can be UPGRADED by a resolvable
                # ref — fall through to a full load; otherwise it's done.
                if not self._dep_graph.nodes[dep_key].get("fields_only"):
                    return dep_key
            # else fall through to the dispatch below, which loads and keys by
            # this same path (idempotently).
        else:
            # UNRESOLVED on disk: stub by the INTENDED path so it upgrades by
            # identity when the file appears (progressive loading). A
            # pseudo-root ref (<vilib>/... with no configured root) has no
            # local path -> keyed by qname; its recorded path is held by
            # ``RecordedPaths``.
            node_type = (
                NodeType.CLASS
                if leaf.endswith(".lvclass")
                else NodeType.TYPEDEF
                if leaf.endswith(".ctl")
                else NodeType.LIBRARY
                if leaf.endswith(".lvlib")
                else NodeType.VI
            )
            # A class method's record names the class's path, not the method's: the
            # class does not list it, so the file has no recorded path and the
            # stub is identified by its owning class's identity plus the method.
            if owner_ref is not None:
                stub_key = self._class_method_stub_key(owner_ref, leaf, caller_file)
                stub_identity = None
            else:
                computed = self._intended_dep_path(qualified_name, dep_ref, caller_file)
                stub_key = str(computed) if computed is not None else qualified_name
                stub_identity = str(computed) if computed is not None else None
                self._note_recorded_path(
                    stub_key, dep_ref, qualified_name, caller_qname
                )
            self._add_stub(
                stub_key,
                node_type,
                leaf,
                qualified_name,
                caller=caller_qname,
            )
            return stub_identity

        # Dispatch by extension to the matching public loader.
        if is_vi_file(leaf):
            return (
                self._leaf_load_vi(
                    resolved, qualified_name, caller_qname, dep_ref, mode
                )
                or dep_key
            )
        elif leaf.endswith(".lvclass"):
            parts = qualified_name.split(":")
            owner_chain = parts[:-1] if len(parts) > 1 else None
            # MINIMAL: field-load the class (no method bodies) — enough for
            # by-name field names. FULL: load its whole method tree for codegen.
            loaded_key = self.load_lvclass(
                resolved,
                mode,
                owner_chain=owner_chain,
            )
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, loaded_key)
            return loaded_key
        elif leaf.endswith(".lvlib"):
            lib_key = self.load_lvlib(resolved, mode)
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, lib_key)
            return lib_key
        elif leaf.endswith(".ctl"):
            ctl_key = self.load_typedef(
                resolved,
                typedef_qname=qualified_name,
            )
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, ctl_key)
            return ctl_key
        else:
            # Unknown extension — stub rather than guess, but key by the resolved
            # file path (it exists here) so it is identified consistently.
            unknown_key = str(resolved.resolve())
            self._add_stub(
                unknown_key,
                NodeType.UNKNOWN,
                leaf,
                qualified_name,
                caller=caller_qname,
            )
            return unknown_key

    def _leaf_load_vi(
        self,
        resolved: Path,
        qualified_name: str,
        caller_qname: str | None,
        dep_ref: ParsedDependencyRef | None,
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
            bd_xml, fp_xml, main_xml = self._extract(resolved)
            # A VI the catalog gives Python for is generated from that, so a
            # generation load takes only its own pane, never the VIs it calls.
            key = str(resolved.resolve())
            child_mode = (
                LoadMode.FULL
                if mode is LoadMode.FULL and not self._is_catalog_stop(key)
                else LoadMode.NONE
            )
            loaded_name = self._load_vi_recursive(
                bd_xml,
                fp_xml,
                main_xml,
                visited=set(),
                source_dir=resolved.parent,
                # ``resolved`` is this SubVI's real source ``.vi`` path — pin the
                # VI's identity (key + unqualified name) to it, never to the
                # fixed-name ``vi_BDHb.xml`` in the extraction cache.
                source_override=resolved,
                mode=child_mode,
            )
            if loaded_name:
                if caller_qname:
                    self._dep_graph.add_edge(caller_qname, loaded_name)
                if (
                    dep_ref is not None
                    and dep_ref.path_tokens
                    and dep_ref.path_tokens[0] == "<vilib>"
                ):
                    self._cache_vilib_terminal_layout(loaded_name, dep_ref)
            return loaded_name
        except Exception as exc:
            # The file isn't present (e.g. web round 1: it hasn't been staged
            # yet), or it cannot be read (recorded in the run's report). Stub it
            # BY PATH and EDGE it to the caller, so the recorded
            # path surfaces in get_dependency_paths — otherwise the extension has
            # nothing to stage and the SubVI never appears. A later round finds
            # the file and this upgrades to a real leaf-load.
            if file_is_present(resolved):
                self.record_load_failure(resolved, exc)
            stub_key = str(resolved.resolve())
            self._stub_subvi(qualified_name, key=stub_key)
            if caller_qname:
                self._dep_graph.add_edge(caller_qname, stub_key)
            return None

    def _is_catalog_stop(self, key: str) -> bool:
        """Whether a generation load stops at ``key``: the catalog gives Python for
        the path its callers recorded for it (``set_catalog_stop``)."""
        return self._catalog_stop and get_vilib_resolver().has_python(
            self._recorded.path(key)
        )

    def _display_path(self, path: Path, caller_file: Path) -> str:
        """``path`` as a ``./``-prefixed, forward-slash path relative to the
        project of ``caller_file`` (``project_root``); the file name alone when
        no relative path exists."""
        base = project_root(caller_file)
        try:
            rel = os.path.relpath(path.resolve(), base.resolve())
        except (OSError, ValueError):
            return path.name
        return "./" + rel.replace(os.sep, "/")

    def _stub_undetermined(
        self,
        qualified_name: str,
        dep_ref: ParsedDependencyRef | None,
        caller_file: Path,
        caller_qname: str | None,
        error: DependencyFileError,
    ) -> str | None:
        """A dependency whose file cannot be determined (``error``) is a stub
        that carries the reason (``stub_reason``), edged to its caller: a call of
        it is missing, and the reason names the files involved project-relative.
        The reason is also a problem on the stub's node, so the run reports it;
        the load goes on. Keyed by the path its record intends (a class method
        by its qualified name and its class's intended path), else its qualified
        name; returns that path like any other unresolved dependency."""
        leaf = qualified_name.rsplit(":", 1)[-1]
        reason = error.summary()
        if error.candidates:
            files = ", ".join(
                self._display_path(p, caller_file) for p in error.candidates
            )
            reason += f" Files: {files}."
        owner_ref = class_owner_ref(qualified_name, dep_ref)
        if owner_ref is not None:
            owner_name = owner_ref.qualified_name or owner_ref.name
            class_path = self._intended_dep_path(owner_name, owner_ref, caller_file)
            stub_key = (
                f"{owner_name}@{class_path}:{leaf}" if class_path else qualified_name
            )
            label = (
                f"{self.vi_label(class_path)}:{leaf}" if class_path else qualified_name
            )
            identity = None
        else:
            intended = self._intended_dep_path(qualified_name, dep_ref, caller_file)
            stub_key = str(intended) if intended is not None else qualified_name
            label = self.vi_label(intended) if intended is not None else qualified_name
            identity = str(intended) if intended is not None else None
            self._note_recorded_path(stub_key, dep_ref, qualified_name, caller_qname)
        node_type = (
            NodeType.CLASS
            if leaf.endswith(".lvclass")
            else NodeType.TYPEDEF
            if leaf.endswith(".ctl")
            else NodeType.LIBRARY
            if leaf.endswith(".lvlib")
            else NodeType.VI
        )
        self._add_stub(
            stub_key,
            node_type,
            leaf,
            qualified_name,
            caller=caller_qname,
            reason=reason,
        )
        self.record_problem(
            stub_key,
            Problem(
                vi=label,
                stage=ProblemStage.LOAD,
                kind=ProblemKind.UNREADABLE,
                reason=reason,
            ),
            node_type,
        )
        return identity

    def _stub_subvi(self, name: str, key: str) -> None:
        """Record a SubVI reference that could not be resolved as a stub.

        ``key`` is the SubVI's resolved (intended) path, so the stub is PATH-keyed
        (upgrades by identity) and the qname/name index to it; the path its caller
        recorded for it is already held by ``RecordedPaths``. Edging to the caller
        is the caller's job (``_leaf_load_vi``'s except branch), not this
        method's.
        """
        self._add_stub(key, NodeType.VI, name.rsplit(":", 1)[-1], name, caller=None)

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
        from ..project_store import find_project_store, write_json_atomic

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
        existing = resolver.resolve_recorded("/".join(tokens))
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

        write_json_atomic(category_file, {"entries": entries})

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
        write_json_atomic(index_file, index_data)


def load_vi_by_path(
    path: Path | str,
    mode: LoadMode = LoadMode.FULL,
    *,
    vilib_root: Folders = None,
    userlib_root: Folders = None,
    instrlib_root: Folders = None,
    search_path: SearchEntries = None,
    layout: bool = False,
    run_settings: RunSettings | None = None,
    graph: InMemoryVIGraph | None = None,
    original_path: Path | None = None,
) -> tuple[InMemoryVIGraph, str]:
    """Load ONE VI, by its on-disk PATH, into a fresh graph and return
    ``(graph, vi_key)``.

    ``vi_key`` is ``load_vi``'s OWN return value -- the exact identity of the
    file just requested. Path IS a VI's identity (see ``load_vi_by_path``):
    two on-disk VIs routinely share a bare filename under LabVIEW dynamic
    dispatch (every class's override of a method is literally ``run.vi``), so
    re-resolving "the VI I just loaded" by ``path.name`` afterward can
    silently pick the WRONG same-named VI out of the graph. This is the ONE
    place "load a user-given path, get its key" is implemented -- the three
    user-facing single-VI load sites (``cli.cmd_describe``, the MCP
    ``read_vi`` tool, ``vi_diff.diff_vi_files``) share it instead of each
    re-deriving the key by bare name and getting it wrong the same way.

    ``vilib_root``/``userlib_root``/``instrlib_root`` are forwarded to
    ``set_library_roots`` when given; ``original_path`` names the file ``path``
    is a copy of (see ``load_vi``). Raises if ``load_vi`` returns no key
    (only possible for a ``.llb`` container, which none of these single-VI
    callers pass).
    """
    from .core import InMemoryVIGraph  # local: avoid a core<->loading cycle

    path = Path(path)
    if graph is None:
        graph = InMemoryVIGraph(run_settings)
    if vilib_root or userlib_root or instrlib_root or search_path:
        graph.set_library_roots(
            vilib_root=vilib_root,
            userlib_root=userlib_root,
            instrlib_root=instrlib_root,
            search_path=search_path,
        )
    key = graph.load_vi(str(path), mode, layout=layout, original_path=original_path)
    if key is None:
        raise ValueError(
            f"load_vi_by_path: load_vi returned no key for {path!r} "
            "(expected a single .vi file)"
        )
    return graph, key
