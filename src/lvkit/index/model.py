"""Facts model — the persisted projection of a VI repo's graph.

Plain, string-only dataclasses (JSON/SQLite-serializable, no Pydantic, no
``LVType`` trees). Each ``VIFacts`` is the resolved facts of ONE VI, keyed by its
**resolved absolute path** — NOT its bare name — so same-named loose VIs
(``setUp.vi`` x17, …) don't silently collide the way the name-keyed in-memory
graph does (measured: 487 files -> 422 in ``list_vis()``). ``name`` /
``qualified_name`` are secondary, ambiguity-reporting indexes.

Almost every field is intrinsic to a VI's own bytes (own connector pane, own
constants, own ``subvi_qualified_names`` / ``type_map``), so ``VIFacts`` is a
(near-)pure function of the VI's content hash — which is what makes the index
soundly per-VI incremental via ``cache_paths.meta_fresh``.

The exception is ``ClassFact.parent`` and ``ClassFact.private_data``: these come
from the owning ``.lvclass`` (``get_class_parent`` / ``get_class_fields``), not
the member VI, so editing a class's parent or private data WITHOUT touching a
member VI leaves those two fields stale until that VI (or the whole index, on an
lvkit-code change — see ``store._facts_fingerprint``) is rebuilt. Keying member
freshness on the owning ``.lvclass`` hash too is the sound fix (TODO); today the
staleness window is a class-only edit between member-VI rebuilds.

Sources (see graph/queries.py, models.py):
- terminals  <- get_inputs/get_outputs (FPTerminal); type_descriptor +
               type_kind from Terminal (models.py).
- constants  <- get_all_constants + outgoing_edges/is_indicator for ``wired_to``.
- calls      <- caller's metadata.subvi_qualified_names (caller-intrinsic).
- type_uses  <- type_map classnames/typedef_names.
- class_fact <- dep_graph class node (parent_class) + owns-edge (scope/accessor).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models import LVTypeKind

# Terminal direction values (mirror models.Terminal.direction).
INPUT = "input"
OUTPUT = "output"


class WiredTo(str, Enum):
    """What a block-diagram constant's output wire feeds — precomputed on
    ``ConstantFact.wired_to`` so "constants wired to indicators" is a filter, not
    a per-query wire trace. ``(str, Enum)`` (not ``StrEnum`` — 3.11+) so a member
    IS its string: it lands verbatim in a SQLite ``TEXT`` column and compares
    equal to the plain string read back out."""

    INDICATOR = "indicator"
    CONTROL = "control"
    OTHER = "other"
    UNWIRED = "unwired"


@dataclass
class TerminalFact:
    """One connector-pane terminal (an FP control or indicator).

    ``type_descriptor`` is the EXACT faithful type descriptor
    (``Terminal.type_descriptor()``), ``""`` when the type didn't resolve.
    ``type_kind`` is the type's KIND (``LVTypeKind``: primitive/enum/cluster/
    array/ring/typedef_ref/class), or ``None`` when genuinely unknown — the
    small, discoverable value set to filter by (error clusters are
    ``type_descriptor='Error'``). ``field_names`` are the cluster's field names
    (a cluster/typedef terminal), enough to classify by field set without the
    full ``LVType`` tree. ``enum_values`` are the enum/ring member names in
    ORDINAL order (empty for non-enum terminals).
    """

    name: str | None
    direction: str  # INPUT | OUTPUT (OUTPUT == indicator on a connector pane)
    is_indicator: bool
    is_public: bool
    control_type: str | None
    field_names: list[str] = field(default_factory=list)
    # The FP DCO uid — the durable BD<->FP bridge, stable across a rename
    # (same uid, changed name). Carried for correlation/diff parity.
    fp_dco_uid: str | None = None
    # Exact faithful type descriptor (Terminal.type_descriptor()); "" when the
    # type didn't resolve — type_kind still identifies the family.
    type_descriptor: str = ""
    # The type's KIND family (LVTypeKind), or None when genuinely unknown.
    type_kind: LVTypeKind | None = None
    # Enum/ring member names in ORDINAL order (by EnumValue.value); empty for
    # non-enum terminals.
    enum_values: list[str] = field(default_factory=list)


@dataclass
class ConstantFact:
    """A block-diagram constant, plus what its output wire feeds.

    ``wired_to`` is precomputed (WiredTo.INDICATOR/CONTROL/OTHER/UNWIRED) so
    "constants wired to indicators" is a filter, not a per-query wire trace.
    """

    value: str  # stringified constant value (raw_value or str(value))
    label: str | None
    type_descriptor: str = ""  # exact faithful type descriptor; "" if unresolved
    type_kind: LVTypeKind | None = None  # type KIND (LVTypeKind), or None
    wired_to: WiredTo = WiredTo.UNWIRED


@dataclass
class ClassFact:
    """Class facts for a VI that is a ``.lvclass`` method.

    ``parent`` is the owning class's parent (qualified, or None). ``scope`` /
    accessor / ``is_static``/``must_override``/``must_call_parent`` come from
    the class's ``rel="owns"`` edge (this specific method's own item
    properties). ``class_version``/``ancestors`` come from the owning class
    itself (like ``parent``), duplicated per method the same way ``parent``/
    ``private_data`` already are. None on ``VIFacts`` for non-method VIs.
    """

    owning_class: str
    parent: str | None = None
    scope: str | None = None
    is_accessor: bool = False
    accessor_field: str | None = None
    # The owning class's private-data fields (incl. inherited), each rendered
    # FAITHFULLY as "name: <type_descriptor>" — e.g. "testName: String". Empty for a
    # class with no resolvable private data.
    private_data: list[str] = field(default_factory=list)
    # NI.ClassItem.IsStaticMethod on THIS method's own Item — parsed by
    # structure.LVMethod but previously dropped here on the way into ClassFact
    # (a real gap: the index couldn't answer "which methods are static").
    is_static: bool = False
    # NI.ClassItem.MustOverride / MustCallParent on THIS method's own Item.
    must_override: bool = False
    must_call_parent: bool = False
    # The owning class's NI.Lib.Version (dotted-quad string), or None if
    # absent/unresolved.
    class_version: str | None = None
    # The owning class's FULL ancestor chain, nearest-first (see
    # structure.LVClass.ancestors) — may be a PREFIX of the true chain when an
    # ancestor's .lvclass isn't present in this checkout.
    ancestors: list[str] = field(default_factory=list)


@dataclass
class LVProjMemberFact:
    """One membership edge: a ``.lvproj`` declares a member (VI/class/library).

    A **project-level** fact, NOT a per-VI one — a repository holds many
    ``.lvproj`` and a VI can belong to several (or none), so membership is a
    separate many-to-many relation keyed by ``(lvproj_path, member)``, not a
    column on ``VIFacts``. lvkit indexes the whole REPOSITORY (every ``.vi`` on
    disk); the ``.lvproj`` is LabVIEW's own scoping unit within it.

    ``resolved_path`` is the on-disk file the member's ``URL`` points at, or
    None when it points outside the checkout (many ``.lvproj`` URLs encode a
    layout above the repo — installed vi.lib copies, developer-desktop paths).
    ``is_in_repo`` is True only when it resolves to a file UNDER the indexed
    root. ``is_dependency`` (auto-collected transitive ref, mostly vi.lib) and
    ``target`` (the build/execution target it sits under) come straight from
    the ``.lvproj`` tree — see ``structure.LVProjectMember``.
    """

    lvproj_path: str  # abs path to the .lvproj file
    lvproj_name: str  # .lvproj stem, e.g. 'VIUnit'
    member_name: str  # member item name as declared in the .lvproj
    member_url: str  # raw URL string from the .lvproj (pre-resolution)
    resolved_path: str | None  # abs on-disk path, or None if it doesn't resolve
    member_type: str  # VI | Control | LVClass | Library
    is_in_repo: bool  # resolves to a file under the indexed project root
    target: str  # target Item it lives under (e.g. 'My Computer')
    is_dependency: bool  # in the auto-collected Dependencies group


@dataclass
class VIFacts:
    """The resolved facts of one VI. Keyed by ``path`` (the stable identity).

    ``content_sha`` (from ``cache_paths.sha256_file``) is the incremental key:
    an index row is reused when the stored sha still matches the on-disk VI
    (plus a schema/version guard) — see ``cache_paths.meta_fresh``.

    ``calls`` / ``type_uses`` hold callee/type **keys** (LabVIEW qualified names,
    e.g. ``TestCase.lvclass:run.vi``); the store/query layer resolves those to
    ``path`` keys where the target is in the repo. ``impact_score`` (transitive
    dependent count) is filled at merge time from the inverted call graph.
    """

    path: str  # resolved absolute path — THE key
    name: str  # bare filename
    qualified_name: str | None = None
    library: str | None = None
    is_stub: bool = False
    content_sha: str = ""
    # VI Properties (Protection/Execution/Window/…) from the VI's own <LVSR>
    # block -- intrinsic to its bytes, like terminals/constants (never
    # coalesced across a partial re-save; see store.save()). Flattened from
    # graph.models.VIProperties's grouped sub-structs (including its
    # kind_* sub-struct, plus the sibling VIHealth facet's health_*
    # columns below) with a group-prefixed column per field -- a WIDE table
    # is intended, every flag queryable
    # (e.g. ``query vi WHERE kind_has_no_block_diagram=1``).
    # "Major.Minor.Bugfix", e.g. "21.0.0", or None if absent.
    lv_version: str | None = None
    vi_type: str | None = None  # <Instrument Type="..."> verbatim
    # One of graph.models.LockState's values ("unlocked" / "locked" /
    # "password_protected") -- kept a plain string here (this module is
    # deliberately Pydantic/LVType-free, see the module docstring).
    lock_state: str = "unlocked"
    # exec_* <- graph.models.ExecutionProps. ``exec_priority``/``reentrancy``/
    # ``exec_system`` are FAITHFUL enum .value strings (graph.models.Priority/
    # Reentrancy/ExecSystem) -- the legacy is_subroutine flag is redundant
    # with exec_priority == "subroutine" and is no longer stored.
    exec_priority: str = "normal"
    reentrancy: str = "non_reentrant"
    exec_system: str = "same_as_caller"
    exec_run_when_opened: bool = False
    exec_show_fp_when_loaded: bool = False
    exec_show_fp_when_called: bool = False
    exec_close_fp_after_call: bool = False
    exec_auto_preallocate_arrays: bool = False
    exec_inline: bool = False
    exec_inlinable: bool = False
    exec_auto_error_handling: bool = False
    exec_allow_debugging: bool = False
    exec_always_calls_parent: bool = False
    exec_print_after_exec: bool = False
    # window_* <- graph.models.WindowProps
    window_show_title_bar: bool = False
    window_show_menu_bar: bool = False
    window_show_toolbar: bool = False
    window_show_scrollbar: int | None = None
    window_auto_center: bool = False
    window_size_to_screen: bool = False
    window_no_runtime_popup_menu: bool = False
    window_scale_with_window: bool = False
    window_mark_return_button: bool = False
    window_auto_handle_menus: bool = False
    window_can_close: bool = False
    window_can_resize: bool = False
    window_can_minimize: bool = False
    window_transparent: bool = False
    # toolbar_* <- graph.models.ToolbarProps
    toolbar_hide_run_button: bool = False
    toolbar_hide_abort_button: bool = False
    toolbar_hide_free_run_button: bool = False
    # instance_* <- graph.models.InstanceProps
    instance_is_system_vi: bool = False
    instance_show_poly_selector: bool = False
    instance_hide_instance_caption: bool = False
    instance_draw_instance_icon: bool = False
    instance_remote_panel: bool = False
    # kind_* <- graph.models.VIProperties.kind (KindProps -- what ROLE the
    # VI plays; a sub-struct of VIProperties, like exec_*/window_*/…).
    # ``kind_typedef_status`` is a FAITHFUL enum .value string
    # (graph.models.TypedefStatus).
    kind_typedef_status: str = "not_a_typedef"
    kind_dynamic_dispatch: bool = False
    kind_source_only: bool = False
    kind_has_no_block_diagram: bool = False
    kind_is_instance_vi: bool = False
    # health_* <- graph.models.VIHealth (compile-health -- a SIBLING facet
    # to VIProperties, never nested under it: emergent state, not a
    # user-settable property. See graph._vi_health.)
    health_bad_node: bool = False
    health_bad_subvi: bool = False
    health_bad_subvi_link: bool = False
    health_bad_compile: bool = False
    health_broken_poly: bool = False
    health_is_broken: bool = False  # VIHealth.is_broken, precomputed
    terminals: list[TerminalFact] = field(default_factory=list)
    constants: list[ConstantFact] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # callee qualified-name keys
    type_uses: list[str] = field(default_factory=list)  # class/typedef keys
    class_fact: ClassFact | None = None
    impact_score: int = 0  # transitive dependents (filled at merge)
    # Direct in-repo callers of this VI (filled at merge from the inverted call
    # graph, same machinery as ``impact_score``). ``callers_count == 0`` is the
    # reliable dead-code / uncalled-VI signal: it is computed on PATH identity
    # (via ``build_call_graph``'s callee-key -> path resolution), so it is
    # correct even for the many VIs whose ``qualified_name`` is None and whose
    # ``calls`` rows hold bare filenames rather than qualified names — a
    # name-matching anti-join over those columns silently misfires.
    callers_count: int = 0
