"""Code generation context - tracks variable bindings during traversal.

resolve() queries the graph directly. One graph. No copies.
"""

from __future__ import annotations

import ast
import copy as copy_module
import math
import warnings
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from lvkit.catalogs import primitive_catalog_for, vilib_catalog_for
from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.core import uid_of
from lvkit.graph.models import (
    AnyGraphNode,
    Constant,
    ConstantNode,
    DestinationInfo,
    PrimitiveNode,
    Reentrancy,
    SourceInfo,
    VIContext,
    VINode,
)
from lvkit.graph.operations import OperationsMixin, frame_key
from lvkit.lv_type_codes import is_boolean_type_name
from lvkit.models import (
    Frame,
    LVType,
    LVTypeKind,
    ScalarValue,
    Terminal,
    TerminalDirection,
    Tunnel,
    TunnelTerminal,
    TypeNamer,
    _is_error_cluster,
    unique_names,
)
from lvkit.parser.refnum_value import NULL_REFNUM, holds_handle
from lvkit.path_value import NOT_A_PATH, parse_path, python_path
from lvkit.primitive_resolver import PrimitiveResolver
from lvkit.string_literal import quote_string
from lvkit.type_names import ROOT_CLASS
from lvkit.vilib_resolver import VILibResolver

from .ast_utils import (
    field_attribute_names,
    interface_names,
    numbered_name,
    to_function_name,
    to_var_name,
)
from .class_model import class_import, member_reference, member_shape
from .data_files import DATA_FILE_IMPORTS, data_file_expression, holds_array_whole
from .errors import CodeGenError, unwritable_value_error
from .errors_module import ERRORS_MODULE_NAME, LABVIEW_ERROR_NAME
from .gaps import Gap, catch_entry_gap
from .import_resolver import ImportResolver, NameImport
from .label_names import python_label
from .live_io_module import LIVE_IO_MODULE_NAME, LIVE_IO_NAME, NO_LIVE_IO_NAME
from .local_indicators import LocalIndicators
from .module_declarations import ModuleDeclarations
from .notifiers_module import NOTIFIER_NAME, NOTIFIERS_MODULE_NAME
from .panel_hooks import PanelHooks
from .references import REFERENCE_KINDS, generated_nodes

# A structure frame: the structure's node id and the frame's key
# (``graph.operations.frame_key``; ``None`` for a loop's one diagram).
StructureFrame = tuple[str, str | int | None]

PREALLOCATED_CLONE_NOTE = (
    "A preallocated clone keeps this value for each call site in LabVIEW; "
    "here every call shares it."
)


@dataclass
class VISignature:
    """Signature info for a SubVI (for import generation)."""

    name: str
    module_name: str
    function_name: str
    signature: str  # e.g., "def calculate(a: float) -> float"
    import_statement: str  # e.g., "from .calculate import calculate"


@dataclass
class CodeGenContext:
    """Context that flows through code generation traversal.

    Tracks variable bindings (terminal_id -> variable_name).
    Queries the graph for edge traversal. One graph, no copies.
    """

    graph: InMemoryVIGraph | None = field(default=None, repr=False)
    vi_name: str | None = None
    # Fully qualified name of the VI being generated (library + class +
    # name). Used in resolution diagnostics so an LLM can find the source.
    qualified_vi_name: str | None = None
    # The name of the Python function this VI generates (what its callers call).
    function_name: str = ""
    imports: set[str] = field(default_factory=set)
    # Imports whose names the signature of the function evaluates when the module
    # loads (a default value), so they stay at module level where the imports of
    # other generated modules move into the bodies of a module in an import cycle.
    signature_imports: set[str] = field(default_factory=set)
    # Imports of the classes and typedefs the module's annotations name. They are
    # needed only by a type checker, so the module imports them under
    # ``TYPE_CHECKING`` and no import cycle can form at run time.
    annotation_imports: set[str] = field(default_factory=set)
    loop_depth: int = 0
    # Every gap found while generating this module (see ``gaps.py``). Shared
    # across child contexts, so a gap nested in a loop or case is still listed.
    gaps: list[Gap] = field(default_factory=list, repr=False)
    # What generating this module has learned of the terminals of the vi.lib VIs it
    # calls, kept in a copy of the vi.lib catalog that shares every entry it has
    # not learned into (empty until a call needs it). The shared catalog is never
    # changed, so what the views show does not depend on which VIs were generated
    # before. A module's function contexts share it; two modules do not.
    learned_vilib: list[VILibResolver] = field(default_factory=list, repr=False)
    # The raises of what the function evaluates when it is entered and cannot
    # be written (a constant with no value to write); the body starts with them.
    entry_raises: list[ast.stmt] = field(default_factory=list, repr=False)
    # The connector-pane Variant controls the function reads or edits as lookup
    # tables (``InMemoryVIGraph.variant_lookup_parameters``), by terminal id; the
    # docstring says each is a dict of attributes.
    lookup_parameters: set[str] = field(default_factory=set, repr=False)
    # Files the generated module reads at run time (an array too large to write
    # into the module, a library of a Call Library Function Node), by path from
    # the module's folder; the caller writes each there.
    # None when the caller has nowhere to put them. ``module_stem`` prefixes
    # their names so modules in one folder never collide.
    data_files: dict[str, bytes] | None = field(default=None, repr=False)
    module_stem: str = ""
    _allocated_vars: set[str] = field(default_factory=set, repr=False)
    # The import statement of each subVI function this function calls -> the
    # import it is made under (see ``import_function``).
    _function_imports: dict[str, NameImport] = field(default_factory=dict, repr=False)
    # Imports the function makes inside its body, after its docstring (a VI
    # that calls itself while a variable holds its name).
    inside_imports: set[str] = field(default_factory=set, repr=False)
    # Output tunnel (outer terminal id) -> the variable that is its merge.
    _tunnel_var_names: dict[str, str] = field(default_factory=dict, repr=False)
    # Variable names bound to array-typed values, with each array's dimension
    # count. A final codegen pass uses this to write operators over operands
    # that turned out to be arrays as comprehensions, even after single-use
    # expression inlining.
    array_vars: dict[str, int] = field(default_factory=dict, repr=False)
    vi_inputs: list[Terminal] = field(default_factory=list)
    # Lives on context because subvi.py reads it at arbitrary depth
    # in the codegen tree. Passing as parameter would thread through
    # every generate() call.
    import_resolver: ImportResolver | None = field(default=None, repr=False)
    # Module-level Python functions generated for Formula Nodes; the builder
    # injects them above the VI function. Shared across child contexts so a
    # formula node nested in a structure still registers its helper.
    formula_helpers: list[ast.stmt] = field(default_factory=list)
    # The name each helper in ``formula_helpers`` was asked for, to the name it
    # is written under (``add_module_function``). Shared like the helpers.
    helper_names: dict[str, str] = field(default_factory=dict)
    # The names of the flatten / unflatten / byte-swap functions generated for
    # this module (keyed by function kind, byte order and type), so a type that
    # recurs is written once. Shared across child contexts like formula_helpers.
    flat_functions: dict[tuple, str] = field(default_factory=dict, repr=False)
    # The module-level ``ctypes`` code of Call Library Function Nodes (libraries,
    # prototypes, structures); shared across child contexts.
    module_declarations: ModuleDeclarations = field(default_factory=ModuleDeclarations)
    # Module-level global variables for persistent per-call-site state:
    # uninitialized shift registers (LV2/functional-global idiom — see
    # codegen/nodes/loop.py) and stateful primitives like First Call?
    # (codegen/nodes/first_call.py). Keyed by variable name so repeated
    # registration is idempotent. Shared across child contexts (same as
    # formula_helpers) since a loop nested inside a structure still needs
    # to register on the one VI-wide list. The builder emits these ABOVE
    # the VI function, sorted by name — dict/set iteration order is not
    # a name, so insertion order alone would not be byte-reproducible. Each
    # name has its statements: its assignment, after a note when it has one.
    module_globals: dict[str, list[ast.stmt]] = field(default_factory=dict, repr=False)
    # Who owns each module-level name (a global or a helper function): the id of
    # the node or terminal it was named from, None for a name every call site of
    # the module shares. One dict per module, shared by child contexts.
    _module_owners: dict[str, str | None] = field(default_factory=dict, repr=False)
    # Nodes that lie on a cycle of the wires of one structure level, found by
    # the dependency sort, with why. One dict per module, shared by child contexts.
    circular_nodes: dict[str, str] = field(default_factory=dict, repr=False)
    # The indicators a Local Variable reads, as variables of the function. One
    # per function, shared by child contexts.
    local_indicators: LocalIndicators = field(
        default_factory=LocalIndicators, repr=False
    )
    # The hooks of a VI that touches its front panel while it runs (see
    # ``panel_hooks``); None for any other VI. One per function, shared by child
    # contexts.
    panel_hooks: PanelHooks | None = field(default=None, repr=False)
    # The variable that holds the queue of events of a VI with an Event Structure
    # (``panel_hooks.entry_events``), set when its function starts; one per
    # function, copied to child contexts.
    events_var: str | None = None
    # The variable that holds the message an Event Structure handles, by the id of
    # the structure. One per function, shared by child contexts.
    event_messages: dict[str, str] = field(default_factory=dict, repr=False)
    # Callback for recursive body generation. Set by builder.py,
    # used by case/loop codegen to generate inner node code without
    # importing back into the builder (which would create a cycle).
    _body_generator: (
        Callable[
            [list[AnyGraphNode], CodeGenContext, StructureFrame | None],
            list[ast.stmt],
        ]
        | None
    ) = field(default=None, repr=False)

    def parameter_var_names(self) -> set[str]:
        """The Python names the VI's parameters are bound to."""
        names: set[str] = set()
        for inp in self.vi_inputs:
            var = self.graph.get_var_name(inp.id) if self.graph is not None else None
            names.add(var or to_var_name(python_label(inp) or "input"))
        return names

    def generate_body(
        self,
        operations: list[AnyGraphNode],
        frame: StructureFrame | None = None,
    ) -> list[ast.stmt]:
        """Generate code for a list of operations: the diagram of the structure
        ``frame`` (``StructureFrame``), which starts with the writes of the
        indicators the frame's inside faces are wired to.

        Delegates to the registered body generator (set by builder.py).
        Used by case/loop/sequence codegen for recursive inner-node
        generation without importing builder (which would be circular).

        If no body generator was registered (e.g. unit tests calling a
        node codegen directly), falls back to sequential dispatch via
        generate(). This import is safe at runtime because node files
        only reference CodeGenContext inside TYPE_CHECKING guards.
        """
        if self._body_generator is not None:
            return self._body_generator(operations, self, frame)

        # Fallback: sequential dispatch (no parallel tiers)
        from .nodes import generate as generate_node

        stmts: list[ast.stmt] = []
        if frame is not None:
            stmts.extend(self.local_indicators.writes_in_frame(frame, self))
        for node in operations:
            fragment = generate_node(node, self)
            stmts.extend(fragment.statements)
            self.merge(fragment.bindings)
            self.imports.update(fragment.imports)
        return stmts

    @property
    def vilib_catalog(self) -> VILibResolver:
        """The vi.lib catalog of the run this code is generated for (the shipped
        catalog when there is no graph)."""
        if self.graph is None:
            return vilib_catalog_for(None)
        return self.graph.vilib_catalog

    @property
    def primitive_catalog(self) -> PrimitiveResolver:
        """The primitive catalog of the run this code is generated for (the
        shipped catalog when there is no graph)."""
        if self.graph is None:
            return primitive_catalog_for(None)
        return self.graph.primitive_catalog

    def catalog_path(self, node: VINode) -> str | None:
        """The path the vi.lib catalog looks the VI ``node`` calls up by."""
        if self.graph is None:
            return node.qualified_path
        return self.graph.catalog_path(node)

    def is_wired(self, terminal_id: str) -> bool:
        """Check if a terminal has any edge connected."""
        if self.graph is None:
            return False
        return self.graph.terminal_is_wired(terminal_id)

    def bind(self, terminal_id: str, var_name: str) -> None:
        """Set var_name on a terminal in the graph."""
        if self.graph is not None:
            self.graph.set_var_name(terminal_id, var_name)

    def resolve(self, terminal_id: str, *, allow_none: bool = False) -> str | None:
        """Get variable name for a terminal by walking the graph.

        A source bound to ``None`` (a constant that is a gap) counts as no
        variable, unless ``allow_none`` accepts it.

        BFS through incoming edges until a terminal with var_name is found.
        One graph. No separate bindings dict. No scoping issues. The walk stays
        in the terminal's own VI: a subVI call is connected to the called VI's
        terminals, whose names belong to that VI's own generation.
        """
        if self.graph is None:
            return None

        home = self.graph.terminal_vi(terminal_id)
        queue: deque[str] = deque([terminal_id])
        seen: set[str] = set()

        while queue:
            tid = queue.popleft()
            if tid in seen:
                continue
            seen.add(tid)

            # Check var_name on this terminal
            var = self.graph.get_var_name(tid)
            if var and (allow_none or var != "None"):
                return var

            # Walk incoming edges
            for src in self.graph.incoming_edges(tid):
                if (
                    src.terminal_id not in seen
                    and self.graph.terminal_vi(src.terminal_id) == home
                ):
                    queue.append(src.terminal_id)

        return None

    def wired_input(
        self, terminal_id: str, node: AnyGraphNode, label: str | int | None = None
    ) -> str | None:
        """The expression feeding input terminal ``terminal_id`` of ``node``.

        None when the terminal is unwired. An error wire is a value like any
        other (``None`` or a ``LabVIEWError``).
        A source that is a gap constant is bound to ``None``, so a wire from it
        reads ``"None"``: its function raises where it starts. A wired input
        with no variable at all is a bug in lvkit and raises ``CodeGenError``
        naming where the wire comes from; ``label`` names the input in the
        message, else its own name or index does. A wire counts when it comes
        from the terminal's own VI."""
        value = self.resolve(terminal_id)
        if value is not None:
            return value
        if self.graph is None:
            return None
        terminal = next((t for t in node.terminals if t.id == terminal_id), None)
        if not self.graph.terminal_is_fed(terminal_id):
            return None
        value = self.resolve(terminal_id, allow_none=True)
        if value is None:
            if label is None:
                label = terminal.name or terminal.index if terminal else terminal_id
            missing = self.graph.source_slot_missing_on_callee(terminal_id)
            if missing is not None:
                callee, slot = missing
                raise ConversionGap(
                    f"The wired input {label!r} of {node.name!r} comes from "
                    f"connector-pane position {slot} of {callee!r}, which the "
                    "called VI does not have, so the VI is broken in LabVIEW",
                    node,
                )
            raise CodeGenError(
                f"The wired input {label!r} of {node.name!r} has no variable "
                f"(from {self.graph.terminal_sources_description(terminal_id)})",
                node,
            )
        return value

    def var_name_in_use(self, name: str) -> bool:
        """Check if a variable name is already bound in this context.

        Checks _allocated_vars first (cheapest), then graph terminals.
        When vi_name is set, scopes the graph scan to the current VI
        to prevent cross-VI name pollution from the class builder.
        """
        if name in self._allocated_vars:
            return True
        if self.graph is None:
            return False
        # Scope to current VI if known, otherwise scan all nodes
        node_ids = self.graph._graph.nodes
        if self.vi_name:
            vi_nodes = self.graph._vi_nodes.get(self.vi_name)
            if vi_nodes:
                node_ids = vi_nodes
        for node_id in node_ids:
            gnode = self.graph._graph.nodes.get(node_id, {}).get("node")
            if gnode is None:
                continue
            for t in gnode.terminals:
                if t.var_name == name:
                    return True
        return False

    def make_output_var(
        self,
        base_name: str,
        node_id: str,
        terminal_id: str | None = None,
    ) -> str:
        """Generate a unique variable name for an operation output.

        Single naming path: if the terminal wires to a structure boundary
        tunnel, the tunnel owns the name (derived from downstream consumer
        or terminal metadata). Otherwise, uses base_name with collision
        handling.

        Shared by primitive and SubVI handlers for consistent behavior.
        """
        # Check if this terminal wires to a structure boundary tunnel
        if terminal_id:
            tunnel_name = self._get_tunnel_var_name(terminal_id)
            if tunnel_name:
                return tunnel_name

        return self._allocate_var(to_var_name(base_name))

    def _allocate_var(self, name: str) -> str:
        """Claim ``name``, or the first free ``name_2``, ``name_3``, ... when it
        is taken."""
        var_name = name
        count = 1
        while var_name in self._allocated_vars:
            count += 1
            var_name = f"{name}_{count}"
        self._allocated_vars.add(var_name)
        return var_name

    def _get_tunnel_var_name(self, terminal_id: str) -> str | None:
        """Check if terminal wires to a structure boundary tunnel.

        If yes, derive the variable name from the tunnel's outer terminal:
        1. Downstream consumer's terminal name (what the next operation calls it)
        2. Outer terminal's own name
        3. None (fall through to default naming)

        This ensures all frames in a case structure use the same variable
        name for the same output tunnel — no override dict needed.
        """
        if self.graph is None:
            return None

        for dest in self.graph.outgoing_edges(terminal_id):
            dest_gnode = self.graph._graph.nodes.get(dest.node_id, {}).get("node")
            if dest_gnode is None:
                continue

            # Find the destination terminal on the structure node
            for term in dest_gnode.terminals:
                if term.id != dest.terminal_id:
                    continue
                if not isinstance(term, TunnelTerminal):
                    continue
                if term.boundary != "inner" or not term.paired_id:
                    continue

                # This terminal IS a tunnel inner — derive name from outer
                name = self.output_tunnel_var_name(term.paired_id, dest.node_id)
                if name:
                    return name

        return None

    def output_tunnel_var_name(
        self, outer_id: str, structure_node_id: str
    ) -> str | None:
        """Variable name for a structure output tunnel's merge: the name derived
        from its outer terminal (see ``_derived_tunnel_name``), made unique like
        every other variable the first time the tunnel is named, and the same
        for every later caller. ``None`` when the tunnel has no name."""
        claimed = self._tunnel_var_names.get(outer_id)
        if claimed is not None:
            return claimed
        name = self._derived_tunnel_name(outer_id, structure_node_id)
        if name is None:
            return None
        name = self._allocate_var(name)
        self._tunnel_var_names[outer_id] = name
        return name

    def _derived_tunnel_name(self, outer_id: str, structure_node_id: str) -> str | None:
        """The name of a structure output tunnel's merge, derived from its outer
        terminal: the downstream consumer's name, else the outer terminal's own
        name. ``None`` when neither is named — the caller then picks a fallback.

        A case output tunnel is a MERGE: one outer terminal, one inner per
        frame. All of a frame's producers and the case generator resolve the
        merge variable through THIS one derivation, so every frame writes the
        same name and no override table is needed.
        """
        if self.graph is None:
            return None

        # Priority 1: downstream consumer of the outer terminal (skip edges
        # back into the same structure — its own inner tunnel terminals).
        for outer_dest in self.graph.outgoing_edges(outer_id):
            if outer_dest.node_id == structure_node_id:
                continue
            if outer_dest.name:
                return to_var_name(outer_dest.name)

        # Priority 2: the outer terminal's own name.
        node = self.graph._graph.nodes.get(structure_node_id, {}).get("node")
        if node is not None:
            outer_term = next((t for t in node.terminals if t.id == outer_id), None)
            if outer_term and outer_term.name:
                return to_var_name(outer_term.name)

        return None

    def get_source(self, terminal_id: str) -> SourceInfo | None:
        """Get source info for a terminal (first incoming edge)."""
        if self.graph is None:
            return None
        sources = self.graph.incoming_edges(terminal_id)
        if not sources:
            return None
        src = sources[0]
        return SourceInfo(
            src_terminal=src.terminal_id,
            src_parent_id=src.node_id,
            src_parent_name=src.name,
            src_parent_kind=src.parent_kind,
            src_slot_index=src.index,
        )

    def get_destinations(self, terminal_id: str) -> list[DestinationInfo]:
        """Get all destinations for a terminal."""
        if self.graph is None:
            return []
        return [
            DestinationInfo(
                dest_terminal=dst.terminal_id,
                dest_parent_id=dst.node_id,
                dest_parent_name=dst.name,
                dest_parent_kind=dst.parent_kind,
                dest_slot_index=dst.index,
            )
            for dst in self.graph.outgoing_edges(terminal_id)
        ]

    def has_incoming(self, terminal_id: str) -> bool:
        """Check if a terminal has any incoming edge."""
        if self.graph is None:
            return False
        return len(self.graph.incoming_edges(terminal_id)) > 0

    def merge(self, bindings: dict[str, str]) -> None:
        """Set var_name on terminals from handler output."""
        for tid, vname in bindings.items():
            self.bind(tid, vname)

    # ------------------------------------------------------------------ #
    # Structural accessors — tree-walk convenience methods emitters use to
    # get a structure node's children, enriched terminals, tunnels, and
    # frame contents, all read directly off the graph as ``GraphNode``s.
    # ------------------------------------------------------------------ #
    def child_nodes(self, node: AnyGraphNode) -> list[AnyGraphNode]:
        """Graph nodes directly contained in ``node`` (a structure), in
        deterministic order. Empty for a leaf node or when there is no
        graph."""
        if self.graph is None:
            return []
        return generated_nodes(
            self.graph.child_nodes(
                node.id, self.vi_name or "", extra_kinds=REFERENCE_KINDS
            ),
            self,
        )

    def enriched_terminals(self, node: AnyGraphNode) -> list[Terminal]:
        """``node``'s terminals with SubVI-call terminals enriched with the
        callee's parameter names."""
        if self.graph is None:
            return list(node.terminals)
        return self.graph.enriched_terminals(node, self.vi_name or "")

    @staticmethod
    def tunnels(node: AnyGraphNode) -> list[Tunnel]:
        """Reconstruct ``Tunnel`` objects from a structure node's terminal
        metadata. Pure function of the node's ``TunnelTerminal``s (no graph
        needed)."""
        return OperationsMixin._tunnels_from_terminals(list(node.terminals))

    def frame_children(
        self, node: AnyGraphNode
    ) -> list[tuple[Frame, list[AnyGraphNode]]]:
        """Pair each of a frame-bearing structure's frames with the graph
        nodes contained in it, in deterministic order.

        Each frame's metadata is the node's own
        ``CaseFrame``/``SequenceFrame``/``EventFrame``. Children come from
        ONE call to :meth:`child_nodes` (the same single, whole-structure
        dataflow sort every other structural walk uses) grouped by the frame
        each child belongs to (:func:`frame_key`) -- NOT a per-frame sort of
        each frame's subset in isolation, which would let a frame's internal
        order diverge from ``describe``'s identical
        ``describe._frame_child_nodes`` grouping.
        """
        frames: list[Frame] = list(getattr(node, "frames", []) or [])
        if self.graph is None:
            return [(f, []) for f in frames]
        by_frame: dict[object, list[AnyGraphNode]] = {}
        for child in self.child_nodes(node):
            by_frame.setdefault(child.frame, []).append(child)
        return [
            (frame, by_frame.get(frame_key(frame, pos), []))
            for pos, frame in enumerate(frames)
        ]

    def is_feedback_node(self, node: AnyGraphNode) -> bool:
        """True if ``node`` is a Feedback Node graph node (either side of the
        master/slave pair) -- see
        ``QueryMixin.is_feedback_master``. False when there is no graph."""
        if self.graph is None:
            return False
        return self.graph.is_feedback_master(node.id)

    def poser_uid(self, node: AnyGraphNode) -> str | None:
        """The IPES decompose/recompose pair UID (``poser_uid`` graph
        attribute), else None."""
        if self.graph is None:
            return None
        return self.graph._graph.nodes.get(node.id, {}).get("poser_uid")

    def child(self, increment_loop_depth: bool = False) -> CodeGenContext:
        """Create a child context. var_name lives on the graph — no scoping."""
        return CodeGenContext(
            graph=self.graph,
            vi_name=self.vi_name,
            qualified_vi_name=self.qualified_vi_name,
            imports=self.imports,
            annotation_imports=self.annotation_imports,
            loop_depth=self.loop_depth + (1 if increment_loop_depth else 0),
            gaps=self.gaps,  # Shared — one list per module
            local_indicators=self.local_indicators,
            panel_hooks=self.panel_hooks,
            events_var=self.events_var,
            event_messages=self.event_messages,
            circular_nodes=self.circular_nodes,  # Shared — one dict per module
            learned_vilib=self.learned_vilib,  # Shared — one per module
            entry_raises=self.entry_raises,  # Shared — one list per function
            lookup_parameters=self.lookup_parameters,  # Shared — one per function
            data_files=self.data_files,  # Shared — one dict per module
            module_stem=self.module_stem,
            function_name=self.function_name,
            _allocated_vars=self._allocated_vars,  # Shared — same scope
            _function_imports=self._function_imports,  # Shared — same scope
            inside_imports=self.inside_imports,  # Shared — same function
            _tunnel_var_names=self._tunnel_var_names,  # Shared — same scope
            array_vars=self.array_vars,  # Shared — one set across the VI
            vi_inputs=self.vi_inputs,
            import_resolver=self.import_resolver,
            formula_helpers=self.formula_helpers,  # Shared — one list
            helper_names=self.helper_names,  # Shared with the helpers
            flat_functions=self.flat_functions,  # Shared — one dict
            module_declarations=self.module_declarations,  # Shared — one per module
            module_globals=self.module_globals,  # Shared — one dict
            _module_owners=self._module_owners,  # Shared — one dict
            _body_generator=self._body_generator,
        )

    _LOOP_INDEX_VARS = "ijklmn"

    def get_loop_index_var(self) -> str:
        """Get index variable name for current loop depth."""
        if self.loop_depth < len(self._LOOP_INDEX_VARS):
            return self._LOOP_INDEX_VARS[self.loop_depth]
        return f"idx_{self.loop_depth}"

    def add_import(self, import_stmt: str) -> None:
        """Add an import statement (none when empty)."""
        if import_stmt:
            self.imports.add(import_stmt)

    def require_packages(self, packages: Iterable[str]) -> None:
        """Record the third-party ``packages`` the module's code imports, for
        the package's ``requirements.txt`` (a VI generated alone has none)."""
        if isinstance(self.import_resolver, ImportResolver):
            self.import_resolver.require(packages)

    def import_labview_error(self, *, at_run_time: bool = True) -> None:
        """Import ``LabVIEWError`` from the package's ``errors`` module: for a
        value the module builds, or (``at_run_time`` False) for its annotations
        only."""
        if isinstance(self.import_resolver, ImportResolver):
            statement = self.import_resolver.error_import()
        else:
            statement = f"from .{ERRORS_MODULE_NAME} import {LABVIEW_ERROR_NAME}"
        if at_run_time:
            self.add_import(statement)
        else:
            self.annotation_imports.add(statement)

    def import_notifier(self) -> None:
        """Import ``Notifier`` from the package's ``notifiers`` module."""
        statement = None
        if isinstance(self.import_resolver, ImportResolver):
            statement = self.import_resolver.notifier_import()
        self.add_import(
            statement or f"from .{NOTIFIERS_MODULE_NAME} import {NOTIFIER_NAME}"
        )

    def import_live_io(self) -> None:
        """Import ``LiveIO`` and ``NO_LIVE_IO`` from the package's ``live_io``
        module: the default of the ``live_io`` parameter is a value, so the import
        stays at module level."""
        statement = None
        if isinstance(self.import_resolver, ImportResolver):
            statement = self.import_resolver.live_io_import()
        self.signature_imports.add(
            statement
            or f"from .{LIVE_IO_MODULE_NAME} import {LIVE_IO_NAME}, {NO_LIVE_IO_NAME}"
        )

    def function_import(
        self, vi_name: str, callee_key: str | None = None
    ) -> NameImport:
        """The import of the function of a VI and the name a module calls it by.
        ``callee_key`` is the VI's identity (a call node's ``callee_key``); a VI
        named without one (a VI reference constant) is found by name."""
        if isinstance(self.import_resolver, ImportResolver):
            return self.import_resolver.function(vi_name, callee_key)
        function = to_function_name(vi_name)
        return NameImport(f"from .{function} import {function}", function)

    def import_function(self, vi_name: str, callee_key: str | None = None) -> str:
        """Import the function of a VI at the top of the module and return the
        name this function calls it by.

        A name a Python function assigns is its local everywhere in it, so a
        variable named like a function it calls would be called instead. A
        function whose name a parameter, control or variable of this VI already
        holds (``_holds``) is imported under a name of its own, one no
        top-level name of the module has (``from .fill import fill as fill_2``),
        and the name it is called by is taken, so no variable named later takes
        it. A VI that calls itself defines the function it calls; when a
        variable holds its name, the function imports its own module's function
        under a name of its own, inside the function (``inside_imports``), where
        the module has finished loading by the time it runs."""
        imported = self.function_import(vi_name, callee_key)
        known = self._function_imports.get(imported.statement)
        if known is None:
            known = imported
            if self._holds(imported.name):
                known = self._own_import(imported).aliased(
                    self._alias_name(imported.name)
                )
            self._allocated_vars.add(known.name)
            self._function_imports[imported.statement] = known
        if imported.statement:
            self.add_import(known.statement)
        elif known.statement:
            self.inside_imports.add(known.statement)
        return known.name

    def _own_import(self, imported: NameImport) -> NameImport:
        """``imported``, or for the function of the module being generated (an
        empty statement) the import of it from this module itself."""
        if imported.statement:
            return imported
        if not isinstance(self.import_resolver, ImportResolver):
            raise CodeGenError(
                f"{imported.name!r} calls itself while a variable holds its name, "
                "and there is no package to import it from"
            )
        return self.import_resolver.own_function(imported.name)

    def _alias_name(self, derived: str) -> str:
        """A name of its own for an import of ``derived``, which a variable of
        this function holds: free here and, with a resolver, at module level."""
        if isinstance(self.import_resolver, ImportResolver):
            return self.import_resolver.alias_name(derived, self._holds)
        return numbered_name(
            derived, lambda n: n == self.function_name or self._holds(n)
        )

    def claim_name(self, wanted: str) -> str:
        """``wanted``, or a numbered form of it when the function or one of its
        parameters or variables already has that name; taken from here on."""

        def taken(n: str) -> bool:
            return n == self.function_name or self._holds(n)

        name = numbered_name(wanted, taken) if taken(wanted) else wanted
        self._allocated_vars.add(name)
        return name

    def _holds(self, name: str) -> bool:
        """Whether a parameter or variable of this function has ``name``."""
        return self.var_name_in_use(name) or name in self.parameter_var_names()

    def vi_reference_expression(self, vi_name: str, key: str | None) -> str:
        """The expression a reference to a VI is: its function, imported, or, for
        a VI of a class, ``Class.method`` (the class imported; for an accessor VI
        the getter or setter of its property)."""
        shape = (
            member_shape(self.graph, key)
            if self.graph is not None and key is not None
            else None
        )
        if shape is None or not isinstance(self.import_resolver, ImportResolver):
            return self.import_function(vi_name, key)
        assert self.graph is not None
        expression, statement = member_reference(
            self.graph, shape, self.import_resolver, self.vi_name
        )
        self.add_import(statement)
        return expression

    def this_vi_expression(self) -> str:
        """The expression that is the function of the VI being generated (LabVIEW's
        "This VI"): its name in its own module, or for a member VI of a class the
        method on its class (``vi_reference_expression``), since a method's bare
        name is not defined inside the class."""
        if self.graph is None or self.vi_name is None:
            return self.function_name
        if member_shape(self.graph, self.vi_name) is None:
            return self.function_name
        return self.vi_reference_expression(self.function_name, self.vi_name)

    def _class_object(self, lv_type: LVType) -> str:
        """A new object of the class a class constant holds, the class imported:
        a class constant is the class's default object."""
        return f"{self.class_expression(lv_type, 'The class constant of')}()"

    def class_expression(self, lv_type: LVType, what: str) -> str:
        """The Python class of the LabVIEW class ``lv_type`` names, imported.
        LabVIEW Object, the class every class descends from, is Python's
        ``object`` (a generated class with no parent has no base). A class that
        is not loaded, or has no module, is a gap whose reason starts with
        ``what`` (the node it is for)."""
        if lv_type.classname == ROOT_CLASS:
            return "object"
        class_key = (
            self.graph.class_type_key(lv_type) if self.graph is not None else None
        )
        if class_key is None or not isinstance(self.import_resolver, ImportResolver):
            raise ConversionGap(
                f"{what} {lv_type.classname} names a class that is "
                "not loaded; add it to the project or a --search-path"
            )
        assert self.graph is not None
        imported = class_import(self.graph, class_key, self.import_resolver)
        self.add_import(imported.statement)
        return imported.name

    def type_namer(self) -> TypeNamer | None:
        """The name this module spells a named class or typedef as, or None for
        one it cannot import; ``None`` when generated without import paths."""
        return type_namer(self.import_resolver)

    def constant_expression(self, const: Constant | ConstantNode) -> str:
        """The Python expression for a constant, importing the enum class when
        the constant is one of its members, the function when it is a reference
        to a VI, and the class when it is an object of a class."""
        lv_type = const.lv_type
        if (
            lv_type is not None
            and lv_type.underlying_type == "VIRefnum"
            and isinstance(const.value, str)
        ):
            return self.vi_reference_expression(const.value, const.referenced_vi_key)
        if lv_type is not None and lv_type.ref_type == "UDClassInst":
            return self._class_object(lv_type)
        if const.unwritable is not None:
            for statement in DATA_FILE_IMPORTS.splitlines():
                self.add_import(statement)
            return data_file_expression(
                what="constant",
                name=const.label,
                uid=uid_of(const.id),
                module_stem=self.module_stem,
                unwritable=const.unwritable,
                lv_type=lv_type,
                data_files=self.data_files,
                whole_value=holds_array_whole(const.unwritable, lv_type),
            )
        if lv_type is not None and _is_error_cluster(lv_type):
            return self._error_constant(const)
        names = self.type_namer()
        if enum_member_constant(const, names) is not None:
            self.import_types(const.lv_type, at_run_time=True)
        return _format_constant(const, names)

    def _error_constant(self, const: Constant | ConstantNode) -> str:
        """An error cluster constant as the value of an error wire: ``None`` when
        its status is False and its code 0, else a ``LabVIEWError`` (a warning
        when its status is False)."""
        value = const.value
        if isinstance(value, str):
            try:
                value = _literal_eval(value)
            except (ValueError, SyntaxError):
                value = const.value
        if value is None:
            return "None"
        assert const.lv_type is not None and const.lv_type.fields is not None
        if isinstance(value, dict):
            keys = unique_names(f.name for f in const.lv_type.fields)
            value = tuple(value.get(key) for key in keys)
        if not isinstance(value, tuple | list) or len(value) != 3:
            raise ConversionGap(
                f"The error constant {const.label or ''!r} holds {value!r}, which "
                "is not a status, a code and a source"
            )
        status, code, source = value
        if not status and not code:
            return "None"
        self.import_labview_error()
        return (
            f"{LABVIEW_ERROR_NAME}(code={code!r}, source={source!r}, "
            f"status={bool(status)!r})"
        )

    def import_types(self, lv_type: LVType | None, *, at_run_time: bool) -> None:
        """Import the class of every class or typedef ``lv_type`` names: for the
        module's annotations (``at_run_time`` False) or for a value the module
        builds from the class, such as an enum member."""
        if lv_type is None or not isinstance(self.import_resolver, ImportResolver):
            return
        target = self.imports if at_run_time else self.annotation_imports
        for named in lv_type.named_types():
            imported = self.import_resolver.type_import(named)
            if imported is not None:
                target.add(imported.statement)

    def _claim_module_name(self, name: str, owner: str | None) -> None:
        """Record ``owner`` as the owner of module-level ``name``. A name two
        different owners claim would silently share one definition: an lvkit
        bug, even when their definitions read the same."""
        held = self._module_owners.setdefault(name, owner)
        if held != owner:
            raise CodeGenError(
                f"Two nodes were given one module-level name, {name}: "
                f"{_owner_label(held)} and {_owner_label(owner)}"
            )

    def add_module_global(
        self, name: str, value: ast.expr, owner: str | None = None
    ) -> None:
        """Register a module-level global variable, seeded to ``value``.

        ``owner`` is the id of the node or terminal the name was made from
        (None for a name every call site of the module shares). A name two
        different owners register is a bug in lvkit, whatever their values.
        Idempotent: the same owner registering the same name with the same
        value again is a no-op (one call site lowered again); the same name
        with another value is two pieces of state given one name. The builder
        emits these above the VI function, sorted by name (see
        ``module_globals`` docstring for why).
        """
        self._claim_module_name(name, owner)
        assign = ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value)
        held = self.module_globals.setdefault(name, [assign])
        # The assignment is the last of the name's statements, after its note.
        _require_same_definition(name, held[-1], assign)

    def add_state_global(
        self, name: str, value: ast.expr, owner: str | None = None
    ) -> None:
        """Register a module-level variable holding a value the VI keeps between
        calls (an uninitialized shift register, First Call?, an indicator a
        Local Variable reads), seeded to ``value``, owned like
        ``add_module_global``. Every call of the function shares it, as every
        call of a non-reentrant VI shares its data space. A preallocated clone
        keeps one per call site in LabVIEW, so the variable of such a VI gets a
        note saying so, a string statement above it (the way this generator
        writes notes into code: an AST has no comments)."""
        fresh = name not in self.module_globals
        self.add_module_global(name, value, owner)
        if fresh and self._is_preallocated_clone():
            self.module_globals[name].insert(
                0, ast.Expr(value=ast.Constant(value=PREALLOCATED_CLONE_NOTE))
            )

    def _is_preallocated_clone(self) -> bool:
        if self.graph is None or not self.vi_name:
            return False
        reentrancy = self.graph.get_vi_properties(self.vi_name).execution.reentrancy
        return reentrancy is Reentrancy.PREALLOCATED_CLONE

    def add_module_function(
        self, func_def: ast.FunctionDef | ast.ClassDef, owner: str | None = None
    ) -> str:
        """Register a module-level helper function or class, emitted above the VI
        function, and return the name it is written under. Idempotent like
        ``add_module_global``, with the same owner rule: the same definition
        again gives the same name, a different one under that name is lvkit's
        error. A name the module already holds (an import, a definition) is not
        taken: the helper is written under the name the module's resolver gives
        it (``ImportResolver.defined_name``, the alias rule of imports)."""
        wanted = func_def.name
        self._claim_module_name(wanted, owner)
        if wanted in self.helper_names:
            written = self.helper_names[wanted]
            held = next(
                s
                for s in self.formula_helpers
                if isinstance(s, (ast.FunctionDef, ast.ClassDef)) and s.name == written
            )
            _require_same_definition(wanted, held, _renamed(func_def, written))
            return written
        written = (
            self.import_resolver.defined_name(wanted)
            if isinstance(self.import_resolver, ImportResolver)
            else wanted
        )
        self.helper_names[wanted] = written
        self.formula_helpers.append(_renamed(func_def, written))
        return written

    @classmethod
    def from_graph(
        cls,
        graph: InMemoryVIGraph,
        vi_name: str,
    ) -> CodeGenContext:
        """Create context by querying the graph directly.

        The graph IS the source of truth. Binds inputs and constants.
        """
        ctx = cls(
            graph=graph,
            vi_name=vi_name,
            vi_inputs=[
                *graph.get_inputs(vi_name),
                *graph.get_off_pane_inputs(vi_name),
            ],
        )

        _bind_inputs_and_constants(
            ctx,
            ctx.vi_inputs,
            graph.get_constants(vi_name),
        )
        return ctx

    @classmethod
    def from_wires(
        cls, wires: list, bindings: dict[str, str] | None = None
    ) -> CodeGenContext:
        """Create context from Wire list by building a graph. For tests."""
        graph = InMemoryVIGraph()
        # Collect terminal IDs per node
        node_terminals: dict[str, set[str]] = {}
        for w in wires:
            node_terminals.setdefault(w.source.node_id, set()).add(w.source.terminal_id)
            node_terminals.setdefault(w.dest.node_id, set()).add(w.dest.terminal_id)

        for nid, tids in node_terminals.items():
            node = PrimitiveNode(
                id=nid,
                vi_path="test.vi",
                name=nid,
                terminals=[
                    Terminal(id=tid, index=i, direction=TerminalDirection.OUTPUT)
                    for i, tid in enumerate(sorted(tids))
                ],
            )
            graph._graph.add_node(nid, node=node)
            for tid in tids:
                graph._term_to_node[tid] = nid

        for w in wires:
            graph._graph.add_edge(
                w.source.node_id,
                w.dest.node_id,
                source=w.source,
                dest=w.dest,
            )

        # Add nodes for binding terminals not already in the graph
        if bindings:
            for tid in bindings:
                if tid not in graph._term_to_node:
                    nid = f"_bind_{tid}"
                    node = PrimitiveNode(
                        id=nid,
                        vi_path="test.vi",
                        name=nid,
                        terminals=[
                            Terminal(
                                id=tid, index=0, direction=TerminalDirection.OUTPUT
                            )
                        ],
                    )
                    graph._graph.add_node(nid, node=node)
                    graph._term_to_node[tid] = nid

        ctx = cls(graph=graph)
        if bindings:
            for tid, vname in bindings.items():
                ctx.bind(tid, vname)
        return ctx

    @classmethod
    def from_vi_context(
        cls,
        vi_context: VIContext,
        graph: InMemoryVIGraph | None = None,
        import_resolver: ImportResolver | None = None,
        data_files: dict[str, bytes] | None = None,
        module_stem: str = "",
        vi_name: str | None = None,
        gaps: list[Gap] | None = None,
    ) -> CodeGenContext:
        """Create context from VIContext.

        ``import_resolver`` is set before the constants are bound: a constant
        that is an enum member, or a reference to a VI, imports what it names.
        If no graph provided, builds one from data_flow wires and
        input/constant terminal IDs so bind/resolve work correctly.
        """
        if graph is None:
            graph = cls._build_graph_from_vi_context(vi_context)

        parameters = [*vi_context.inputs, *vi_context.off_pane_inputs]
        ctx = cls(
            graph=graph,
            vi_inputs=parameters,
            import_resolver=import_resolver,
            data_files=data_files,
            module_stem=module_stem,
            vi_name=vi_name,
            gaps=gaps if gaps is not None else [],
        )

        _bind_inputs_and_constants(ctx, parameters, vi_context.constants)
        return ctx

    @classmethod
    def _build_graph_from_vi_context(
        cls,
        vi_context: VIContext,
    ) -> InMemoryVIGraph | None:
        """Build a minimal graph for input/constant terminals only.

        Only creates terminal nodes for inputs and constants so that
        bind/resolve work. Does NOT add wire edges to avoid the codegen
        discovering auto-created graph structure.
        """
        inputs = [*vi_context.inputs, *vi_context.off_pane_inputs]
        constants = vi_context.constants

        tids: list[str] = []
        for inp in inputs:
            if inp.id:
                tids.append(inp.id)
        for const in constants:
            if const.id:
                tids.append(const.id)

        if not tids:
            return None

        graph = InMemoryVIGraph()
        for i, tid in enumerate(tids):
            nid = f"_auto_{i}"
            node = PrimitiveNode(
                id=nid,
                vi_path="test.vi",
                name=nid,
                terminals=[
                    Terminal(id=tid, index=0, direction=TerminalDirection.OUTPUT),
                ],
            )
            graph._graph.add_node(nid, node=node)
            graph._term_to_node[tid] = nid

        return graph


def _bind_inputs_and_constants(
    ctx: CodeGenContext,
    inputs: list | Any,
    constants: list | Any,
) -> None:
    """Bind input and constant terminals on the context.

    Shared by from_graph() and from_vi_context().
    """
    names = interface_names([i for i in inputs if i.id], [], []).parameters
    for inp in inputs:
        if inp.id:
            var = names[inp.id]
            ctx.bind(inp.id, var)
            # Track array-typed inputs so the final arrayify pass broadcasts
            # operators/conversions applied to them (same as array-typed
            # primitive outputs) — an array parameter is just as array-valued.
            lv_type = getattr(inp, "lv_type", None)
            if lv_type is not None and lv_type.kind == LVTypeKind.ARRAY:
                ctx.array_vars[var] = lv_type.dimensions or 1
    for const in constants:
        if const.id:
            with catch_entry_gap(ctx, const.label) as entry:
                bound = ctx.constant_expression(const)
            if entry.failed:
                bound = "None"
            ctx.bind(const.id, bound)
            if _needs_simplenamespace(bound):
                ctx.add_import(_SIMPLENAMESPACE_IMPORT)


def _python_float_constant(value: float) -> str:
    """Emit a float as an executable expression, including NaN and infinity."""
    if math.isnan(value):
        return "float('nan')"
    if math.isinf(value):
        return "float('-inf')" if value < 0 else "float('inf')"
    return repr(value)


class _PathText(str):
    """A path inside an aggregate constant's literal, held as its path text."""


class _AggregateNames(ast.NodeTransformer):
    """The parser renders aggregate elements as text: NaN and infinity arrive as
    bare ``nan``/``inf`` names, and a path as its path text (``Path("/a")``,
    ``NotAPath``), inside the literal."""

    def __init__(self, text: str) -> None:
        self.text = text

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in ("nan", "inf"):
            return ast.Constant(value=float(node.id))
        if node.id == NOT_A_PATH:
            return ast.Constant(value=_PathText(NOT_A_PATH))
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        segment = ast.get_source_segment(self.text, node)
        if segment is not None and parse_path(segment) is not None:
            return ast.Constant(value=_PathText(segment))
        return self.generic_visit(node)


def _literal_eval(text: str) -> object:
    """``ast.literal_eval`` that also accepts the parser's ``nan``/``inf`` and
    path texts."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(text, mode="eval")
    return ast.literal_eval(_AggregateNames(text).visit(tree))


def _python_literal(value: object) -> str:
    """``repr`` for constant values, recursing into lists/tuples/dicts so nested
    NaN and infinity stay executable."""
    if isinstance(value, _PathText):
        return python_path(value) or "None"
    if isinstance(value, float):
        return _python_float_constant(value)
    if isinstance(value, list):
        return "[" + ", ".join(_python_literal(v) for v in value) + "]"
    if isinstance(value, tuple):
        trailing = "," if len(value) == 1 else ""
        return "(" + ", ".join(_python_literal(v) for v in value) + trailing + ")"
    if isinstance(value, dict):
        items = [
            f"{_python_literal(k)}: {_python_literal(v)}" for k, v in value.items()
        ]
        return "{" + ", ".join(items) + "}"
    return repr(value)


def _decode_numeric_constant(value: str, underlying_type: str) -> str:
    """Decode a numeric constant using its LabVIEW type.

    The value may be a decimal string ("42"), a raw hex string from the VI
    binary ("7FFFFFFFFFFFFFFF"), or a pre-decoded value ("3.14").
    The underlying_type tells us how to interpret it.
    """
    import struct

    # Already a valid Python number?
    try:
        return str(int(value))
    except ValueError:
        pass
    try:
        return _python_float_constant(float(value))
    except ValueError:
        pass

    # Raw hex from VI binary — decode using the type
    is_hex = all(c in "0123456789abcdefABCDEF" for c in value) and len(value) > 0
    if not is_hex:
        # Non-printable single char = encoded integer
        if len(value) == 1 and not value.isprintable():
            return str(ord(value))
        return repr(value)

    if "Float64" in underlying_type or "DBL" in underlying_type:
        hex_padded = value.zfill(16)
        float_val = struct.unpack(">d", bytes.fromhex(hex_padded))[0]
        return _python_float_constant(float_val)
    if "Float32" in underlying_type or "SGL" in underlying_type:
        hex_padded = value.zfill(8)
        float_val = struct.unpack(">f", bytes.fromhex(hex_padded))[0]
        return _python_float_constant(float_val)
    # Integer types (Int8, Int16, Int32, Int64, UInt*, etc.)
    return str(int(value, 16))


# A named cluster constant lowers to types.SimpleNamespace; sites that bind such
# a constant add this import (see _needs_simplenamespace).
_SIMPLENAMESPACE_IMPORT = "from types import SimpleNamespace"


def _needs_simplenamespace(expr: str) -> bool:
    """True if a formatted-constant expression uses ``SimpleNamespace`` and thus
    needs its import added at the binding site."""
    return "SimpleNamespace(" in expr


def _format_cluster_constant(const: Constant | ConstantNode) -> str | None:
    """Format a CLUSTER constant as a Python object: a named/typedef cluster as
    ``SimpleNamespace(field=value, ...)`` (attribute access, and mutable so
    Bundle By Name can assign fields), an anonymous cluster as a positional
    tuple. Returns None if the value or fields can't be resolved, so the caller
    falls back to generic formatting."""
    lv_type = const.lv_type
    if lv_type is None or not lv_type.fields:
        return None
    fields = lv_type.fields
    anon = not lv_type.typedef_name and not lv_type.classname
    value = const.value
    if isinstance(value, str):
        try:
            value = _literal_eval(value)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(value, dict):
        return None
    # Values in field order; a field absent from the parsed dict falls back to
    # None (LabVIEW's cluster default fills every field, so this is defensive).
    keys = unique_names(f.name for f in fields)
    ordered = [
        (f.name, _python_literal(value.get(key))) for f, key in zip(fields, keys)
    ]
    if anon:
        elts = ", ".join(v for _name, v in ordered)
        trailing = "," if len(ordered) == 1 else ""
        return f"({elts}{trailing})"
    kwargs = ", ".join(
        f"{attribute}={v}"
        for attribute, (_name, v) in zip(
            field_attribute_names(f.name for f in fields), ordered, strict=True
        )
    )
    return f"SimpleNamespace({kwargs})"


def type_namer(
    import_resolver: ImportResolver | None,
) -> TypeNamer | None:
    """The name a module generated with ``import_resolver`` spells a named class
    or typedef as, or None for a type it cannot import; ``None`` when the module
    has no import paths."""
    if not isinstance(import_resolver, ImportResolver):
        return None
    resolver = import_resolver

    def names(lv_type: LVType) -> str | None:
        imported = resolver.type_import(lv_type) if lv_type.type_ref() else None
        return imported.name if imported is not None else None

    return names


def enum_member_constant(
    const: Constant | ConstantNode, names: TypeNamer | None = None
) -> str | None:
    """``Class.member`` for a constant that is an item of a named enum or ring
    whose class is names; None for any other constant."""
    if not (const.lv_type and const.lv_type.values and const.value is not None):
        return None
    try:
        return const.lv_type.enum_member_ref(int(const.value), names)
    except (ValueError, TypeError):
        return None


def terminal_default_expression(default: ScalarValue, type_name: str | None) -> str:
    """The Python expression for the default a catalog terminal documents, through
    the same conversion a constant of its type goes through. ``type_name`` is the
    catalog's type word; it only tells a path (written as path text) from a
    string, the value's own Python type gives the rest."""
    if (type_name or "").lower() == "path":
        lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
        text: ScalarValue = default
    elif isinstance(default, bool):
        lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
        text = str(default)
    elif isinstance(default, int):
        lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
        text = str(default)
    elif isinstance(default, float):
        lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
        text = str(default)
    else:
        lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
        text = quote_string(str(default))
    return _format_constant(Constant(id="default", value=text, lv_type=lv_type))


def _format_constant(
    const: Constant | ConstantNode, names: TypeNamer | None = None
) -> str:
    """Format a constant value as a Python expression.

    An item of a named enum is ``Class.member`` when ``names`` gives the
    module's name for the class (``CodeGenContext.constant_expression`` adds the
    import); otherwise it is the item's value.
    """
    if const.unwritable is not None:
        raise unwritable_value_error("constant", const.label, const.unwritable)

    member_ref = enum_member_constant(const, names)
    if member_ref is not None:
        return member_ref

    if const.empty_variant:
        return "None"

    python_hint = getattr(const, "python", None)
    if python_hint:
        return str(python_hint)

    # Cluster constant: emit a mutable attribute object so Bundle/Unbundle By
    # Name (which read and write ``.field``) operate on it. A named/typedef
    # cluster uses attribute access with the names ``field_attribute_names``
    # gives its fields, the ones Bundle/Unbundle By Name use; an anonymous cluster
    # (no field names) is a positional tuple, the representation nmux uses for
    # anonymous clusters.
    if const.lv_type and const.lv_type.kind == LVTypeKind.CLUSTER:
        cluster_expr = _format_cluster_constant(const)
        if cluster_expr is not None:
            return cluster_expr

    value = const.value
    underlying = const.lv_type.underlying_type if const.lv_type else None

    if value is None:
        return "None"

    # Array constants: LabVIEW's rendered value is already list-literal syntax
    # (e.g. "[False, True]", "[1, 2, 3]"). Validate via literal_eval and re-emit
    # canonically; fall through to scalar handling if it isn't a plain literal.
    if underlying == "Array" and isinstance(value, str):
        try:
            return _python_literal(_literal_eval(value))
        except (ValueError, SyntaxError):
            pass
    if isinstance(value, list):
        return _python_literal(value)

    # Type-driven decoding: use underlying_type when available.
    if underlying == "Picture":
        # A Picture holding drawing data raised above; what is left is empty.
        return "None"
    if holds_handle(const.lv_type):
        # Not a Refnum is None; a saved handle names an object of the session
        # that saved it and has no Python meaning.
        if value == NULL_REFNUM:
            return "None"
        raise ConversionGap(
            f"The refnum constant {const.label or ''!r} holds the handle {value}, "
            "which names an object of the LabVIEW session that saved it"
        )
    if is_boolean_type_name(underlying):
        return "True" if value in ("True", "1", "01") else "False"
    if underlying == "Path":
        # The parser hands a path constant back as its path text.
        if isinstance(value, str):
            expression = python_path(value)
            if expression is not None:
                return expression
        return f"Path('{value}')"
    if underlying and underlying.startswith("Num") and isinstance(value, str):
        return _decode_numeric_constant(value, underlying)
    if underlying == "String" and isinstance(value, str):
        # The decoders hand back a QUOTED, escaped string literal — double-quoted
        # from _decode_string_default, single-quoted from _decode_element — so we
        # must recover the RAW text and re-emit it once. repr'ing the quoted form
        # directly double-wraps it (a space -> "' '").
        try:
            # A fully-escaped literal round-trips through literal_eval.
            return repr(ast.literal_eval(value))
        except (ValueError, SyntaxError):
            # _decode_element leaves control bytes (\n, \r, \t) RAW inside the
            # quotes, so literal_eval rejects it. Strip one matching quote pair
            # and undo only the escaping the decoder applied (quote, backslash).
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                q = value[0]
                inner = value[1:-1].replace("\\" + q, q).replace("\\\\", "\\")
                return repr(inner)
            return repr(value)

    # Already-decoded Python values
    if isinstance(value, float):
        return _python_float_constant(value)
    if isinstance(value, int):
        return str(value)

    # No type info — best-effort fallback
    if isinstance(value, str):
        if value == '""':
            return "''"
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        try:
            return str(int(value))
        except ValueError:
            pass
        try:
            return _python_float_constant(float(value))
        except ValueError:
            pass
        if len(value) == 1 and not value.isprintable():
            return str(ord(value))
        return repr(value)
    return repr(value)


def _renamed(
    definition: ast.FunctionDef | ast.ClassDef, name: str
) -> ast.FunctionDef | ast.ClassDef:
    """``definition`` under ``name`` (a copy when the name differs)."""
    if definition.name == name:
        return definition
    renamed = copy_module.deepcopy(definition)
    renamed.name = name
    return renamed


def _owner_label(owner: str | None) -> str:
    """An owner for a message: its uid, never its file path."""
    return "a shared name" if owner is None else f"uid {uid_of(owner)}"


def _require_same_definition(name: str, held: ast.stmt, new: ast.stmt) -> None:
    """A module-level name registered twice must be one definition; two
    different ones under one name would silently share it."""
    if ast.dump(held) != ast.dump(new):
        raise CodeGenError(
            f"Two different module-level definitions were given one name, {name}"
        )
