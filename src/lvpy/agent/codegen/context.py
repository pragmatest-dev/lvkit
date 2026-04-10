"""Code generation context - tracks variable bindings during traversal.

resolve() queries the graph directly. One graph. No copies.
"""

from __future__ import annotations

import ast
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lvpy.graph_types import (
    Constant,
    DestinationInfo,
    Operation,
    PrimitiveNode,
    SourceInfo,
    Terminal,
    TunnelTerminal,
    VIContext,
)
from lvpy.memory_graph import InMemoryVIGraph
from lvpy.vilib_resolver import derive_python_name

from .ast_utils import to_var_name


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
    imports: set[str] = field(default_factory=set)
    loop_depth: int = 0
    use_held_error_model: bool = False
    # When True, unknown primitives / vi.lib VIs do NOT raise — instead
    # the codegen emits a `raise PrimitiveResolutionNeeded(...)` /
    # `raise VILibResolutionNeeded(...)` statement inline in the generated
    # Python. The same exception class, same fields, just deferred to
    # runtime. Lets a downstream LLM see the diagnostic with full context
    # and either write a mapping into .lvpy/ or replace the raise with a
    # contextual fix.
    soft_unresolved: bool = False
    # Lives on context (not builder) because child() must share it
    # across the same generation pass.
    _branch_counter: int = field(default=0, repr=False)
    _allocated_vars: set[str] = field(default_factory=set, repr=False)
    vi_inputs: list[Terminal] = field(default_factory=list)
    # Lives on context because subvi.py reads it at arbitrary depth
    # in the codegen tree. Passing as parameter would thread through
    # every generate() call.
    import_resolver: Callable[[str], str] | None = field(default=None, repr=False)
    # Callback for recursive body generation. Set by builder.py,
    # used by case/loop codegen to generate inner node code without
    # importing back into the builder (which would create a cycle).
    _body_generator: Callable[
        [list[Operation], CodeGenContext], list[ast.stmt]
    ] | None = field(default=None, repr=False)

    def generate_body(self, operations: list[Operation]) -> list[ast.stmt]:
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

    def is_wired(self, terminal_id: str) -> bool:
        """Check if a terminal has any edge connected."""
        if self.graph is None:
            return False
        return self.graph.terminal_is_wired(terminal_id)

    def bind(self, terminal_id: str, var_name: str) -> None:
        """Set var_name on a terminal in the graph."""
        if self.graph is not None:
            self.graph.set_var_name(terminal_id, var_name)

    def resolve(self, terminal_id: str) -> str | None:
        """Get variable name for a terminal by walking the graph.

        BFS through incoming edges until a terminal with var_name is found.
        One graph. No separate bindings dict. No scoping issues.
        """
        if self.graph is None:
            return None

        queue: deque[str] = deque([terminal_id])
        seen: set[str] = set()

        while queue:
            tid = queue.popleft()
            if tid in seen:
                continue
            seen.add(tid)

            # Check var_name on this terminal
            var = self.graph.get_var_name(tid)
            if var and var != "None":
                return var

            # Walk incoming edges
            for src in self.graph.incoming_edges(tid):
                if src.terminal_id not in seen:
                    queue.append(src.terminal_id)

        return None

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
        self, base_name: str, node_id: str, terminal_id: str | None = None,
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
        if var_name in self._allocated_vars:
            op_suffix = node_id.split("::")[-1] if "::" in node_id else node_id
            var_name = f"{var_name}_{op_suffix}"
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
                outer_id = term.paired_id

                # Priority 1: downstream consumer of the outer terminal
                for outer_dest in self.graph.outgoing_edges(outer_id):
                    # Skip self-edges (tunnel inner terminals on same structure)
                    if outer_dest.node_id == dest.node_id:
                        continue
                    if outer_dest.name:
                        name = to_var_name(outer_dest.name)
                        self._allocated_vars.add(name)
                        return name

                # Priority 2: outer terminal's own name
                outer_term = next(
                    (t for t in dest_gnode.terminals if t.id == outer_id),
                    None,
                )
                if outer_term and outer_term.name:
                    name = to_var_name(outer_term.name)
                    self._allocated_vars.add(name)
                    return name

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
            src_parent_labels=list(src.labels),
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
                dest_parent_labels=list(dst.labels),
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

    def child(self, increment_loop_depth: bool = False) -> CodeGenContext:
        """Create a child context. var_name lives on the graph — no scoping."""
        return CodeGenContext(
            graph=self.graph,
            vi_name=self.vi_name,
            qualified_vi_name=self.qualified_vi_name,
            imports=self.imports,
            loop_depth=self.loop_depth + (1 if increment_loop_depth else 0),
            use_held_error_model=self.use_held_error_model,
            soft_unresolved=self.soft_unresolved,
            _allocated_vars=self._allocated_vars,  # Shared — same scope
            vi_inputs=self.vi_inputs,
            import_resolver=self.import_resolver,
            _body_generator=self._body_generator,
        )

    _LOOP_INDEX_VARS = "ijklmn"

    def get_loop_index_var(self) -> str:
        """Get index variable name for current loop depth."""
        if self.loop_depth < len(self._LOOP_INDEX_VARS):
            return self._LOOP_INDEX_VARS[self.loop_depth]
        return f"idx_{self.loop_depth}"

    def add_import(self, import_stmt: str) -> None:
        """Add an import statement."""
        self.imports.add(import_stmt)

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
            vi_inputs=list(graph.get_inputs(vi_name)),
        )

        _bind_inputs_and_constants(
            ctx, graph.get_inputs(vi_name), graph.get_constants(vi_name),
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
                id=nid, vi="test.vi", name=nid,
                terminals=[
                    Terminal(id=tid, index=i, direction="output")
                    for i, tid in enumerate(sorted(tids))
                ],
            )
            graph._graph.add_node(nid, node=node)
            for tid in tids:
                graph._term_to_node[tid] = nid

        for w in wires:
            graph._graph.add_edge(
                w.source.node_id, w.dest.node_id,
                source=w.source, dest=w.dest,
            )

        # Add nodes for binding terminals not already in the graph
        if bindings:
            for tid in bindings:
                if tid not in graph._term_to_node:
                    nid = f"_bind_{tid}"
                    node = PrimitiveNode(
                        id=nid, vi="test.vi", name=nid,
                        terminals=[Terminal(id=tid, index=0, direction="output")],
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
    ) -> CodeGenContext:
        """Create context from VIContext.

        Prefer from_graph() for new code.
        If no graph provided, builds one from data_flow wires and
        input/constant terminal IDs so bind/resolve work correctly.
        """
        if graph is None:
            graph = cls._build_graph_from_vi_context(vi_context)

        ctx = cls(
            graph=graph,
            vi_inputs=vi_context.inputs,
        )

        _bind_inputs_and_constants(ctx, vi_context.inputs, vi_context.constants)
        return ctx

    @classmethod
    def _build_graph_from_vi_context(
        cls, vi_context: VIContext,
    ) -> InMemoryVIGraph | None:
        """Build a minimal graph for input/constant terminals only.

        Only creates terminal nodes for inputs and constants so that
        bind/resolve work. Does NOT add wire edges to avoid the codegen
        discovering auto-created graph structure.
        """
        inputs = vi_context.inputs
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
                vi="test.vi",
                name=nid,
                terminals=[
                    Terminal(id=tid, index=0, direction="output"),
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
    for inp in inputs:
        if inp.id and not inp.is_error_cluster:
            ctx.bind(inp.id, to_var_name(inp.name or "input"))
    for const in constants:
        if const.id:
            ctx.bind(const.id, _format_constant(const))


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
        return str(float(value))
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
        return str(float_val)
    if "Float32" in underlying_type or "SGL" in underlying_type:
        hex_padded = value.zfill(8)
        float_val = struct.unpack(">f", bytes.fromhex(hex_padded))[0]
        return str(float_val)
    # Integer types (Int8, Int16, Int32, Int64, UInt*, etc.)
    return str(int(value, 16))


def _format_constant(const: Constant) -> str:
    """Format a constant value as a Python expression.

    Note: enum imports are handled by the SubVI codegen (subvi.py) which
    adds the correct relative import when generating the function call.
    """
    if (
        const.lv_type and const.lv_type.values
        and const.lv_type.typedef_name and const.value is not None
    ):
        try:
            int_value = int(const.value)
            for member_name, enum_val in const.lv_type.values.items():
                if enum_val.value == int_value:
                    if not member_name.isidentifier():
                        break  # e.g. "<Null>" — not valid Python
                    class_name = derive_python_name(const.lv_type.typedef_name)
                    return f"{class_name}.{member_name}"
        except (ValueError, TypeError):
            pass

    python_hint = getattr(const, "python", None)
    if python_hint:
        return str(python_hint)

    value = const.value
    underlying = const.lv_type.underlying_type if const.lv_type else None

    if value is None:
        return "None"

    # Type-driven decoding: use underlying_type when available.
    if underlying == "Boolean":
        return "True" if value in ("True", "1", "01") else "False"
    if underlying == "Path":
        return f"Path('{value}')"
    if underlying and underlying.startswith("Num") and isinstance(value, str):
        return _decode_numeric_constant(value, underlying)
    if underlying == "String" and isinstance(value, str):
        if value == '""':
            return "''"
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            return repr(value[1:-1])
        return repr(value)

    # Already-decoded Python values
    if isinstance(value, int | float):
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
            return str(float(value))
        except ValueError:
            pass
        if len(value) == 1 and not value.isprintable():
            return str(ord(value))
        return repr(value)
    return repr(value)
