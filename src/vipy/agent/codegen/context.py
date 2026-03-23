"""Code generation context - tracks variable bindings during traversal.

resolve() queries the graph directly. One graph. No copies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from vipy.graph_types import Constant, Terminal

from .ast_utils import to_var_name

if TYPE_CHECKING:
    from vipy.memory_graph import InMemoryVIGraph


@dataclass
class CodeGenContext:
    """Context that flows through code generation traversal.

    Tracks variable bindings (terminal_id -> variable_name).
    Queries the graph for edge traversal. One graph, no copies.
    """

    graph: InMemoryVIGraph | None = field(default=None, repr=False)
    vi_name: str | None = None
    imports: set[str] = field(default_factory=set)
    loop_depth: int = 0
    use_held_error_model: bool = False
    _branch_counter: int = field(default=0, repr=False)
    vi_inputs: list[Terminal] = field(default_factory=list)
    import_resolver: Any = field(default=None, repr=False)

    def is_wired(self, terminal_id: str) -> bool:
        """Check if a terminal has any edge connected."""
        if self.graph is None:
            return False
        return self.graph.terminal_is_wired(terminal_id)

    def bind(self, terminal_id: str, var_name: str) -> None:
        """Set var_name on a terminal in the graph."""
        if self.graph:
            self.graph.set_var_name(terminal_id, var_name)

    def resolve(self, terminal_id: str) -> str | None:
        """Get variable name for a terminal by walking the graph.

        BFS through incoming edges until a terminal with var_name is found.
        One graph. No separate bindings dict. No scoping issues.
        """
        if self.graph is None:
            return None

        queue = [terminal_id]
        seen: set[str] = set()

        while queue:
            tid = queue.pop(0)
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

    def get_source(self, terminal_id: str) -> dict | None:
        """Get source info for a terminal (first incoming edge)."""
        if self.graph is None:
            return None
        sources = self.graph.incoming_edges(terminal_id)
        if not sources:
            return None
        src = sources[0]
        return {
            "src_terminal": src.terminal_id,
            "src_parent_id": src.node_id,
            "src_parent_name": src.name,
            "src_parent_labels": src.labels,
            "src_slot_index": src.index,
        }

    def get_destinations(self, terminal_id: str) -> list[dict]:
        """Get all destinations for a terminal."""
        if self.graph is None:
            return []
        return [
            {
                "dest_terminal": dst.terminal_id,
                "dest_parent_id": dst.node_id,
                "dest_parent_name": dst.name,
                "dest_parent_labels": dst.labels,
                "dest_slot_index": dst.index,
            }
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
            imports=self.imports,
            loop_depth=self.loop_depth + (1 if increment_loop_depth else 0),
            use_held_error_model=self.use_held_error_model,
            vi_inputs=self.vi_inputs,
            import_resolver=self.import_resolver,
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

        for inp in graph.get_inputs(vi_name):
            if inp.id:
                ctx.bind(inp.id, to_var_name(inp.name or "input"))

        for const in graph.get_constants(vi_name):
            if const.id:
                ctx.bind(const.id, _format_constant(const))

        return ctx

    @classmethod
    def from_wires(cls, wires: list, bindings: dict[str, str] | None = None) -> CodeGenContext:
        """Create context from Wire list by building a graph. For tests."""
        from vipy.memory_graph import InMemoryVIGraph

        graph = InMemoryVIGraph()
        nodes: set[str] = set()
        for w in wires:
            src_nid = w.source.node_id
            dst_nid = w.dest.node_id
            if src_nid not in nodes:
                graph._graph.add_node(src_nid, node=None)
                nodes.add(src_nid)
            if dst_nid not in nodes:
                graph._graph.add_node(dst_nid, node=None)
                nodes.add(dst_nid)
            graph._graph.add_edge(src_nid, dst_nid, source=w.source, dest=w.dest)
            graph._term_to_node[w.source.terminal_id] = src_nid
            graph._term_to_node[w.dest.terminal_id] = dst_nid

        ctx = cls(graph=graph)
        if bindings:
            for tid, vname in bindings.items():
                ctx.bind(tid, vname)
        return ctx

    @classmethod
    def from_vi_context(
        cls,
        vi_context: dict[str, Any],
        graph: InMemoryVIGraph | None = None,
    ) -> CodeGenContext:
        """Create context from VI context dict (legacy).

        Prefer from_graph() for new code.
        """
        ctx = cls(
            graph=graph,
            vi_inputs=vi_context.get("inputs", []),
        )

        for inp in vi_context.get("inputs", []):
            if inp.id:
                ctx.bind(inp.id, to_var_name(inp.name or "input"))

        for const in vi_context.get("constants", []):
            if const.id:
                ctx.bind(const.id, _format_constant(const))

        return ctx


def _format_constant(const: Constant) -> str:
    """Format a constant value as a Python expression."""
    if const.lv_type and const.lv_type.kind == "enum" and const.lv_type.values:
        try:
            int_value = int(const.value)
            for member_name, enum_val in const.lv_type.values.items():
                if enum_val.value == int_value:
                    if const.lv_type.typedef_name:
                        from vipy.vilib_resolver import derive_python_name

                        class_name = derive_python_name(const.lv_type.typedef_name)
                        return f"{class_name}.{member_name}"
                    return str(const.value)
        except (ValueError, TypeError):
            pass

    python_hint = getattr(const, "python", None)
    if python_hint:
        return str(python_hint)

    value = const.value
    underlying = const.lv_type.underlying_type if const.lv_type else None

    if value is None:
        return "None"
    if underlying == "Boolean":
        return "True" if value in ("True", "1", "01") else "False"
    if underlying == "Path":
        return f"Path('{value}')"
    if isinstance(value, (int, float)):
        return str(value)
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
        return repr(value)
    return repr(value)
