"""Facts model — the persisted projection of a VI repo's graph.

Plain, string-only dataclasses (JSON/SQLite-serializable, no Pydantic, no
``LVType`` trees). Each ``VIFacts`` is the resolved facts of ONE VI, keyed by its
**resolved absolute path** — NOT its bare name — so same-named loose VIs
(``setUp.vi`` x17, …) don't silently collide the way the name-keyed in-memory
graph does (measured: 487 files -> 422 in ``list_vis()``). ``name`` /
``qualified_name`` are secondary, ambiguity-reporting indexes.

Every field here is intrinsic to a VI's own bytes (own connector pane, own
constants, own ``subvi_qualified_names`` / ``type_map``), so ``VIFacts`` is a
pure function of the VI's content hash — which is what makes the index soundly
per-VI incremental via ``cache_paths.meta_fresh``.

Sources (see graph/queries.py, models.py):
- terminals  <- get_inputs/get_outputs (FPTerminal); is_error_cluster is
               Terminal.is_error_cluster (models.py), precomputed here.
- constants  <- get_all_constants + outgoing_edges/is_indicator for ``wired_to``.
- calls      <- caller's metadata.subvi_qualified_names (caller-intrinsic).
- type_uses  <- type_map classnames/typedef_names.
- class_fact <- dep_graph class node (parent_class) + owns-edge (scope/accessor).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Terminal direction values (mirror models.Terminal.direction).
INPUT = "input"
OUTPUT = "output"

# ConstantFact.wired_to values.
WIRED_INDICATOR = "indicator"
WIRED_CONTROL = "control"
WIRED_OTHER = "other"
WIRED_NONE = "unwired"


@dataclass
class TerminalFact:
    """One connector-pane terminal (an FP control or indicator).

    ``field_names`` are the cluster's field names when the terminal carries a
    cluster/typedef (empty otherwise) — enough to classify a terminal
    structurally (an error cluster is ``{status, code, source}``) without the
    full ``LVType`` tree. ``is_error_cluster`` is precomputed from
    ``Terminal.is_error_cluster`` so the demo query ("count error-indicator
    names") is a straight filter, not a re-derivation.

    ``py_type`` is the LOSSY codegen-target projection (``Terminal.
    python_type()`` — enum/ring collapse to ``"int"``, cluster to
    ``"dict[str, Any]"``, …); kept for existing callers. ``lv_type`` is the
    FAITHFUL LabVIEW type label (``Terminal.lv_type.lv_label()``) — prefer it
    for anything that reads the type back. ``enum_values`` are the enum/ring
    member names in ORDINAL order (empty for non-enum terminals), so "does
    this project use an enum with member X" is a straight filter over the
    index, not a full VI reload.
    """

    name: str | None
    direction: str  # INPUT | OUTPUT (OUTPUT == indicator on a connector pane)
    is_indicator: bool
    is_public: bool
    control_type: str | None
    py_type: str  # Terminal.python_type() — LOSSY codegen-target projection
    is_error_cluster: bool
    field_names: list[str] = field(default_factory=list)
    # The FP DCO uid — the durable BD<->FP bridge, stable across a rename
    # (same uid, changed name). Carried for correlation/diff parity.
    fp_dco_uid: str | None = None
    # FAITHFUL LabVIEW type label — Terminal.lv_type.lv_label(), or "Any" when
    # lv_type is None. Never a Python annotation; see LVType.lv_label().
    lv_type: str = "Any"
    # Enum/ring member names in ORDINAL order (by EnumValue.value); empty for
    # non-enum terminals. From Terminal.lv_type.values.
    enum_values: list[str] = field(default_factory=list)


@dataclass
class ConstantFact:
    """A block-diagram constant, plus what its output wire feeds.

    ``wired_to`` is precomputed (WIRED_INDICATOR/CONTROL/OTHER/NONE) so
    "constants wired to indicators" is a filter, not a per-query wire trace.
    """

    value: str  # stringified constant value (raw_value or str(value))
    label: str | None
    py_type: str  # LOSSY codegen projection; prefer lv_type to read the type
    lv_type: str = "?"  # FAITHFUL LabVIEW type label (LVType.lv_label)
    wired_to: str = WIRED_NONE


@dataclass
class ClassFact:
    """Class facts for a VI that is a ``.lvclass`` method.

    ``parent`` is the owning class's parent (qualified, or None). ``scope`` /
    accessor come from the class's ``rel="owns"`` edge. None on ``VIFacts`` for
    non-method VIs.
    """

    owning_class: str
    parent: str | None = None
    scope: str | None = None
    is_accessor: bool = False
    accessor_field: str | None = None
    # The owning class's private-data fields (incl. inherited), each rendered
    # FAITHFULLY as "name: <lv_label>" — e.g. "testName: String". Empty for a
    # class with no resolvable private data.
    private_data: list[str] = field(default_factory=list)


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

    lvproj_path: str        # abs path to the .lvproj file
    lvproj_name: str        # .lvproj stem, e.g. 'VIUnit'
    member_name: str        # member item name as declared in the .lvproj
    member_url: str         # raw URL string from the .lvproj (pre-resolution)
    resolved_path: str | None  # abs on-disk path, or None if it doesn't resolve
    member_type: str        # VI | Control | LVClass | Library
    is_in_repo: bool        # resolves to a file under the indexed project root
    target: str             # target Item it lives under (e.g. 'My Computer')
    is_dependency: bool      # in the auto-collected Dependencies group


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
