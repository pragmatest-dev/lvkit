"""Code generation context - tracks variable bindings during traversal.

resolve() queries the graph directly. One graph. No copies.
"""

from __future__ import annotations

import ast
import math
import warnings
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.core import uid_of
from lvkit.graph.models import (
    AnyGraphNode,
    Constant,
    ConstantNode,
    DestinationInfo,
    PrimitiveNode,
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
    unique_names,
)
from lvkit.path_value import NOT_A_PATH, parse_path, python_path
from lvkit.string_literal import quote_string
from lvkit.vilib_resolver import VILibResolver

from .ast_utils import (
    field_attribute_names,
    interface_names,
    to_function_name,
    to_var_name,
)
from .class_model import member_reference, member_shape
from .data_files import DATA_FILE_IMPORTS, data_file_expression, holds_array_whole
from .errors import CodeGenError, unwritable_value_error
from .errors_module import ERRORS_MODULE_NAME, LABVIEW_ERROR_NAME
from .gaps import Gap, catch_entry_gap
from .import_resolver import ImportResolver, NameImport
from .local_indicators import LocalIndicators
from .module_declarations import ModuleDeclarations
from .references import REFERENCE_KINDS, generated_nodes


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
    # Imports of the classes and typedefs the module's annotations name. They are
    # needed only by a type checker, so the module imports them under
    # ``TYPE_CHECKING`` and no import cycle can form at run time.
    annotation_imports: set[str] = field(default_factory=set)
    loop_depth: int = 0
    # Whether the VI has parallel branches with error wires: independent
    # nodes then run as branches that hold their errors (see error_handler).
    use_held_error_model: bool = False
    # The name of the caught ``LabVIEWError`` while the code of an error handler
    # (the error frame of an error case structure) is generated: an Unbundle of
    # the error cluster there reads the caught error. None outside a handler,
    # where an error cluster is the no-error one.
    error_var: str | None = None
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
    # Files the generated module reads at run time (an array too large to write
    # into the module, a library of a Call Library Function Node), by path from
    # the module's folder; the caller writes each there.
    # None when the caller has nowhere to put them. ``module_stem`` prefixes
    # their names so modules in one folder never collide.
    data_files: dict[str, bytes] | None = field(default=None, repr=False)
    module_stem: str = ""
    # Lives on context (not builder) because child() must share it
    # across the same generation pass.
    _branch_counter: int = field(default=0, repr=False)
    _allocated_vars: set[str] = field(default_factory=set, repr=False)
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
    # a name, so insertion order alone would not be byte-reproducible.
    module_globals: dict[str, ast.stmt] = field(default_factory=dict, repr=False)
    # Nodes that lie on a cycle of the wires of one structure level, found by
    # the dependency sort, with why. One dict per module, shared by child contexts.
    circular_nodes: dict[str, str] = field(default_factory=dict, repr=False)
    # The indicators a Local Variable reads, as variables of the function. One
    # per function, shared by child contexts.
    local_indicators: LocalIndicators = field(
        default_factory=LocalIndicators, repr=False
    )
    # Callback for recursive body generation. Set by builder.py,
    # used by case/loop codegen to generate inner node code without
    # importing back into the builder (which would create a cycle).
    _body_generator: (
        Callable[[list[AnyGraphNode], CodeGenContext], list[ast.stmt]] | None
    ) = field(default=None, repr=False)

    def parameter_var_names(self) -> set[str]:
        """The Python names the VI's parameters are bound to."""
        names: set[str] = set()
        for inp in self.vi_inputs:
            var = self.graph.get_var_name(inp.id) if self.graph is not None else None
            names.add(var or to_var_name(inp.name or "input"))
        return names

    def generate_body(self, operations: list[AnyGraphNode]) -> list[ast.stmt]:
        """Generate code for a list of operations.

        Delegates to the registered body generator (set by builder.py).
        Used by case/loop/sequence codegen for recursive inner-node
        generation without importing builder (which would be circular).

        If no body generator was registered (e.g. unit tests calling a
        node codegen directly), falls back to sequential dispatch via
        generate(). This import is safe at runtime because node files
        only reference CodeGenContext inside TYPE_CHECKING guards.
        """
        if self._body_generator is not None:
            return self._body_generator(operations, self)

        # Fallback: sequential dispatch (no parallel tiers)
        from .nodes import generate as generate_node

        stmts: list[ast.stmt] = []
        for node in operations:
            fragment = generate_node(node, self)
            stmts.extend(fragment.statements)
            self.merge(fragment.bindings)
            self.imports.update(fragment.imports)
        return stmts

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

        None when the terminal is unwired, or it or the terminal feeding it
        carries an error cluster (errors are exceptions, so it has no value).
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
        if (
            not self.graph.terminal_is_fed(terminal_id)
            or (terminal is not None and terminal.is_error_cluster)
            or self.graph.terminal_fed_by_error_cluster(terminal_id)
        ):
            return None
        value = self.resolve(terminal_id, allow_none=True)
        if value is None:
            if label is None:
                label = terminal.name or terminal.index if terminal else terminal_id
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

        var_name = to_var_name(base_name)
        count = 1
        while var_name in self._allocated_vars:
            count += 1
            var_name = f"{to_var_name(base_name)}_{count}"
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
        if name in self._allocated_vars:
            suffix = outer_id.split("::")[-1]
            name = f"{name}_{suffix}"
        self._allocated_vars.add(name)
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
            )
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
        needed).

        A tunnel or shift register carrying an error cluster is left out: an
        error is an exception in Python, so it has no value to bind, merge or
        carry between iterations."""
        return OperationsMixin._tunnels_from_terminals(
            [t for t in node.terminals if not t.is_error_cluster]
        )

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
            use_held_error_model=self.use_held_error_model,
            error_var=self.error_var,
            gaps=self.gaps,  # Shared — one list per module
            local_indicators=self.local_indicators,
            circular_nodes=self.circular_nodes,  # Shared — one dict per module
            learned_vilib=self.learned_vilib,  # Shared — one per module
            entry_raises=self.entry_raises,  # Shared — one list per function
            data_files=self.data_files,  # Shared — one dict per module
            module_stem=self.module_stem,
            function_name=self.function_name,
            _allocated_vars=self._allocated_vars,  # Shared — same scope
            _tunnel_var_names=self._tunnel_var_names,  # Shared — same scope
            array_vars=self.array_vars,  # Shared — one set across the VI
            vi_inputs=self.vi_inputs,
            import_resolver=self.import_resolver,
            formula_helpers=self.formula_helpers,  # Shared — one list
            flat_functions=self.flat_functions,  # Shared — one dict
            module_declarations=self.module_declarations,  # Shared — one per module
            module_globals=self.module_globals,  # Shared — one dict
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

    def import_labview_error(self) -> None:
        """Import ``LabVIEWError`` from the package's ``errors`` module."""
        if isinstance(self.import_resolver, ImportResolver):
            self.add_import(self.import_resolver.error_import())
        else:
            self.add_import(f"from .{ERRORS_MODULE_NAME} import {LABVIEW_ERROR_NAME}")

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
        name this module calls it by."""
        imported = self.function_import(vi_name, callee_key)
        self.add_import(imported.statement)
        return imported.name

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

    def type_namer(self) -> TypeNamer | None:
        """The name this module spells a named class or typedef as, or None for
        one it cannot import; ``None`` when generated without import paths."""
        return type_namer(self.import_resolver)

    def constant_expression(self, const: Constant | ConstantNode) -> str:
        """The Python expression for a constant, importing the enum class when
        the constant is one of its members, and the function when it is a
        reference to a VI."""
        lv_type = const.lv_type
        if (
            lv_type is not None
            and lv_type.underlying_type == "VIRefnum"
            and isinstance(const.value, str)
        ):
            return self.vi_reference_expression(const.value, const.referenced_vi_key)
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
        names = self.type_namer()
        if enum_member_constant(const, names) is not None:
            self.import_types(const.lv_type, at_run_time=True)
        return _format_constant(const, names)

    def import_types(self, lv_type: LVType | None, *, at_run_time: bool) -> None:
        """Import the class of every class or typedef ``lv_type`` names: for the
        module's annotations (``at_run_time`` False) or for a value the module
        builds from the class, such as an enum member."""
        if lv_type is None or not isinstance(self.import_resolver, ImportResolver):
            return
        target = self.imports if at_run_time else self.annotation_imports
        for named in lv_type.named_types():
            type_ref = named.type_ref()
            imported = self.import_resolver.type_import(type_ref) if type_ref else None
            if imported is not None:
                target.add(imported.statement)

    def add_module_global(self, name: str, value: ast.expr) -> None:
        """Register a module-level global variable, seeded to ``value``.

        Idempotent by name: a second registration for the same name is a
        no-op (the same call site is only lowered once per generation
        pass). The builder emits these above the VI function, sorted by
        name (see ``module_globals`` docstring for why).
        """
        if name not in self.module_globals:
            self.module_globals[name] = ast.Assign(
                targets=[ast.Name(id=name, ctx=ast.Store())],
                value=value,
            )

    def add_module_function(self, func_def: ast.FunctionDef) -> None:
        """Register a module-level helper function, emitted above the VI function.
        Idempotent by name."""
        if not any(
            isinstance(stmt, ast.FunctionDef) and stmt.name == func_def.name
            for stmt in self.formula_helpers
        ):
            self.formula_helpers.append(func_def)

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

    Shared by from_graph() and from_vi_context(). Skips error cluster
    inputs (Python uses exceptions instead).
    """
    names = interface_names([i for i in inputs if i.id], [], []).parameters
    for inp in inputs:
        if inp.id and not inp.is_error_cluster:
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
        type_ref = lv_type.type_ref()
        imported = resolver.type_import(type_ref) if type_ref else None
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
