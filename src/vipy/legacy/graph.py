"""Neo4j graph database integration for VI hierarchy.

Requires the 'neo4j' optional dependency:
    pip install vipy[neo4j]

.. deprecated::
    This module is deprecated. Use vipy.memory_graph for in-memory graph
    operations instead. Neo4j support may be removed in a future version.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "vipy.graph is deprecated. Use vipy.memory_graph for graph operations. "
    "Neo4j support may be removed in a future version.",
    DeprecationWarning,
    stacklevel=2,
)

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from neo4j import Driver, GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False
    Driver = None  # type: ignore[misc, assignment]
    GraphDatabase = None  # type: ignore[misc, assignment]

if TYPE_CHECKING:
    from neo4j import Driver

from .cypher import from_vi
from ..extractor import extract_vi_xml


@dataclass
class GraphConfig:
    """Neo4j connection configuration."""
    uri: str = "bolt://localhost:7687"
    username: str = "neo4j"
    password: str = "vipy-password"  # Default for docker-compose
    database: str = "neo4j"


class VIGraph:
    """Neo4j graph database for VI hierarchies.

    Usage:
        graph = VIGraph()
        graph.connect()

        # Load a VI hierarchy
        graph.load_vi("/path/to/Main.vi", expand_subvis=True)

        # Query the graph
        inputs = graph.query("MATCH (i:Input) RETURN i.name")

        graph.close()

    Or as context manager:
        with VIGraph() as graph:
            graph.load_vi("/path/to/Main.vi")
            results = graph.query("MATCH (v:VI) RETURN v.name")
    """

    def __init__(self, config: GraphConfig | None = None):
        self.config = config or GraphConfig()
        self._driver: Driver | None = None

    def connect(self) -> None:
        """Connect to Neo4j database."""
        if not HAS_NEO4J:
            raise ImportError(
                "Neo4j is required for VIGraph. Install with: pip install vipy[neo4j]"
            )
        self._driver = GraphDatabase.driver(
            self.config.uri,
            auth=(self.config.username, self.config.password),
        )
        # Verify connection
        self._driver.verify_connectivity()

    def close(self) -> None:
        """Close the database connection."""
        if self._driver:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> VIGraph:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def clear(self) -> None:
        """Clear all nodes and relationships from the database."""
        self._execute("MATCH (n) DETACH DELETE n")

    def load_vi(
        self,
        vi_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
        clear_first: bool = False,
    ) -> None:
        """Load a VI hierarchy into the graph database.

        Args:
            vi_path: Path to .vi file or *_BDHb.xml file
            expand_subvis: Recursively expand SubVIs
            search_paths: Directories to search for SubVIs
            clear_first: Clear existing graph before loading
        """
        vi_path = Path(vi_path)

        if clear_first:
            self.clear()

        # Handle .vi files by extracting first
        if vi_path.suffix.lower() == ".vi":
            bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)
        elif vi_path.name.endswith("_BDHb.xml"):
            bd_xml = vi_path
            fp_xml = None
            main_xml = None
        else:
            raise ValueError(f"Expected .vi or *_BDHb.xml file: {vi_path}")

        # Build search paths
        if search_paths is None:
            search_paths = [vi_path.parent]

        # Generate Cypher statements
        cypher = from_vi(
            bd_xml,
            fp_xml_path=fp_xml,
            main_xml_path=main_xml,
            expand_subvis=expand_subvis,
            _search_paths=search_paths,
        )

        # Execute each CREATE statement
        self._load_cypher(cypher)

    def _load_cypher(self, cypher: str) -> None:
        """Load Cypher CREATE statements into the database."""
        # Split into individual statements
        statements = []
        current = []

        for line in cypher.split("\n"):
            line = line.strip()

            # Skip comments and empty lines
            if not line or line.startswith("//"):
                continue

            # Accumulate CREATE/MERGE statements
            if line.startswith("CREATE") or line.startswith("MERGE"):
                if current:
                    statements.append(" ".join(current))
                current = [line]
            elif current:
                current.append(line)

        if current:
            statements.append(" ".join(current))

        # Execute all statements in a single transaction
        # Combine into one big CREATE for efficiency
        if statements:
            combined = "\n".join(statements)
            self._execute(combined)

    def query(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Cypher query and return results.

        Args:
            cypher: Cypher query string
            parameters: Optional query parameters

        Returns:
            List of result records as dictionaries
        """
        with self._session() as session:
            result = session.run(cypher, parameters or {})
            return [dict(record) for record in result]

    def query_single(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a query and return single result."""
        results = self.query(cypher, parameters)
        return results[0] if results else None

    def _execute(self, cypher: str) -> None:
        """Execute a Cypher statement (no return value)."""
        with self._session() as session:
            session.run(cypher)

    @contextmanager
    def _session(self) -> Generator:
        """Get a database session."""
        if not self._driver:
            raise RuntimeError("Not connected to database. Call connect() first.")

        with self._driver.session(database=self.config.database) as session:
            yield session

    # === Query Helpers for LLM Tools ===

    def list_vis(self) -> list[str]:
        """List all VIs in the graph."""
        results = self.query("MATCH (v:VI) RETURN v.name AS name")
        return [r["name"] for r in results]

    def is_stub_vi(self, vi_name: str) -> bool:
        """Check if a VI is a stub (missing dependency)."""
        result = self.query_single(
            "MATCH (v:VI {name: $name}) RETURN v.is_stub AS is_stub",
            {"name": vi_name},
        )
        return bool(result and result.get("is_stub"))

    def get_stub_vi_info(self, vi_name: str) -> dict | None:
        """Get stub VI info including terminal types from call site."""
        return self.query_single("""
            MATCH (v:VI:Stub {name: $name})
            RETURN v.name AS name,
                   v.input_types AS input_types,
                   v.output_types AS output_types
        """, {"name": vi_name})

    def get_vi_inputs(self, vi_name: str) -> list[dict]:
        """Get inputs for a VI."""
        return self.query("""
            MATCH (v:VI {name: $name})<-[:PARAMETER_OF]-(i:Input)
            RETURN i.name AS name, i.type AS type, labels(i) AS labels
        """, {"name": vi_name})

    def get_vi_outputs(self, vi_name: str) -> list[dict]:
        """Get outputs for a VI."""
        return self.query("""
            MATCH (v:VI {name: $name})-[:RETURNS]->(o:Output)
            RETURN o.name AS name, o.type AS type, labels(o) AS labels
        """, {"name": vi_name})

    def get_vi_operations(self, vi_name: str) -> list[dict]:
        """Get operations (primitives, SubVI calls) for a VI."""
        return self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS]->(op)
            WHERE op:Primitive OR op:SubVI OR op:Loop OR op:Conditional
            RETURN labels(op) AS type, op.name AS name, op.python AS python
        """, {"name": vi_name})

    def get_subvi_calls(self, vi_name: str) -> list[dict]:
        """Get SubVIs called by a VI."""
        return self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS]->(s:SubVI)-[:CALLS]->(subvi:VI)
            RETURN s.name AS call_name, subvi.name AS vi_name
        """, {"name": vi_name})

    def get_data_flow(self, vi_name: str) -> list[dict]:
        """Get data flow edges within a VI (terminal-level connections)."""
        return self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS|RETURNS|PARAMETER_OF*]-(parent1)
            MATCH (parent1)-[:HAS_TERMINAL]->(t1:Terminal)-[:CONNECTS_TO]->(t2:Terminal)
            OPTIONAL MATCH (parent2)-[:HAS_TERMINAL]->(t2)
            RETURN parent1.name AS from_name, labels(parent1) AS from_type,
                   t1.index AS from_terminal_index, t1.type AS from_terminal_type,
                   parent2.name AS to_name, labels(parent2) AS to_type,
                   t2.index AS to_terminal_index, t2.type AS to_terminal_type
        """, {"name": vi_name})

    def get_vi_cypher(self, vi_name: str) -> str:
        """Reconstruct Cypher representation of a VI from the graph.

        Returns Cypher-style description suitable for LLM code generation.
        This queries the graph and formats it like the original Cypher output.
        """
        lines = [f"// VI: {vi_name}", ""]

        # Get VI node
        vi = self.query_single("MATCH (v:VI {name: $name}) RETURN v", {"name": vi_name})
        if not vi:
            return f"// VI not found: {vi_name}"

        lines.append(f'MERGE (vi:VI {{name: "{vi_name}"}})')
        lines.append("")

        # Inputs
        inputs = self.query("""
            MATCH (v:VI {name: $name})<-[:PARAMETER_OF]-(i)
            RETURN i.id AS id, i.name AS name, i.type AS type, labels(i) AS labels
        """, {"name": vi_name})
        if inputs:
            lines.append("// Inputs")
            for inp in inputs:
                labels = ":".join(inp["labels"])
                name = inp.get("name", "unnamed")
                lines.append(f'CREATE (i_{inp["id"]}:{labels} {{name: "{name}"}})')
                lines.append(f'CREATE (i_{inp["id"]})-[:PARAMETER_OF]->(vi)')
            lines.append("")

        # Outputs
        outputs = self.query("""
            MATCH (v:VI {name: $name})-[:RETURNS]->(o)
            RETURN o.id AS id, o.name AS name, o.type AS type, labels(o) AS labels
        """, {"name": vi_name})
        if outputs:
            lines.append("// Outputs")
            for out in outputs:
                labels = ":".join(out["labels"])
                name = out.get("name", "unnamed")
                lines.append(f'CREATE (o_{out["id"]}:{labels} {{name: "{name}"}})')
                lines.append(f'CREATE (vi)-[:RETURNS]->(o_{out["id"]})')
            lines.append("")

        # Constants
        constants = self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS]->(c:Constant)
            RETURN c.id AS id, c.value AS value, c.type AS type, c.python AS python
        """, {"name": vi_name})
        if constants:
            lines.append("// Constants")
            for c in constants:
                py = c.get("python", "")
                val = c.get("value", "")
                lines.append(f'CREATE (c_{c["id"]}:Constant {{value: "{val}", python: "{py}"}})')
                lines.append(f'CREATE (vi)-[:CONTAINS]->(c_{c["id"]})')
            lines.append("")

        # Operations
        ops = self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS]->(op)
            WHERE op:Primitive OR op:SubVI OR op:Loop OR op:Conditional
            RETURN op.id AS id, op.name AS name, op.python AS python,
                   op.description AS desc, labels(op) AS labels,
                   op.primResID AS primResID, op.type AS opType
        """, {"name": vi_name})
        if ops:
            lines.append("// Operations")
            for op in ops:
                labels = ":".join(op["labels"])
                name = op.get("name") or op.get("desc") or ""
                py = op.get("python") or ""
                prim_res_id = op.get("primResID")
                op_type = op.get("opType") or ""

                # Build properties based on node type
                if "Primitive" in op["labels"] and prim_res_id is not None:
                    # For primitives, primResID is the key identifier
                    props = f'primResID: {prim_res_id}'
                    if name:
                        props += f', name: "{name}"'
                elif "SubVI" in op["labels"]:
                    props = f'name: "{name}"'
                elif "Loop" in op["labels"] or "Conditional" in op["labels"]:
                    props = f'type: "{op_type}"'
                    if name:
                        props += f', name: "{name}"'
                else:
                    props = f'name: "{name}"'

                if py:
                    props += f', python: "{py}"'
                lines.append(f'CREATE (op_{op["id"]}:{labels} {{{props}}})')
                lines.append(f'CREATE (vi)-[:CONTAINS]->(op_{op["id"]})')
            lines.append("")

        # Terminals - show terminal nodes and their parent relationships
        terminals = self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS|RETURNS|PARAMETER_OF*]-(parent)
            MATCH (parent)-[:HAS_TERMINAL]->(t:Terminal)
            RETURN t.id AS term_id, t.index AS term_index, t.type AS term_type,
                   labels(t) AS term_labels, parent.id AS parent_id,
                   parent.name AS parent_name, labels(parent) AS parent_labels,
                   parent.primResID AS parent_prim
        """, {"name": vi_name})
        if terminals:
            lines.append("// Terminals")
            for t in terminals:
                direction = "Output" if "Output" in t["term_labels"] else "Input"
                term_type = t.get("term_type") or "unknown"
                lines.append(
                    f'CREATE (t_{t["term_id"]}:Terminal:{direction} '
                    f'{{index: {t["term_index"]}, type: "{term_type}"}})'
                )
                parent_desc = self._describe_node_simple(t, "parent")
                lines.append(f'CREATE ({parent_desc})-[:HAS_TERMINAL]->(t_{t["term_id"]})')
            lines.append("")

        # Data flow - terminal-to-terminal connections
        flows = self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS|RETURNS|PARAMETER_OF*]-(parent1)
            MATCH (parent1)-[:HAS_TERMINAL]->(t1:Terminal)-[:CONNECTS_TO]->(t2:Terminal)
            OPTIONAL MATCH (parent2)-[:HAS_TERMINAL]->(t2)
            RETURN t1.id AS from_term_id, t1.index AS from_index, t1.type AS from_type,
                   parent1.name AS from_parent_name, parent1.primResID AS from_prim,
                   t2.id AS to_term_id, t2.index AS to_index, t2.type AS to_type,
                   parent2.name AS to_parent_name, parent2.primResID AS to_prim
        """, {"name": vi_name})
        if flows:
            lines.append("// Data Flow (terminal connections)")
            for f in flows:
                from_desc = f.get("from_parent_name") or f"prim_{f.get('from_prim')}" or "source"
                to_desc = f.get("to_parent_name") or f"prim_{f.get('to_prim')}" or "dest"
                lines.append(
                    f'// {from_desc}[{f.get("from_index")}] -> {to_desc}[{f.get("to_index")}]'
                )
                lines.append(
                    f'CREATE (t_{f["from_term_id"]})-[:CONNECTS_TO]->(t_{f["to_term_id"]})'
                )
            lines.append("")

        return "\n".join(lines)

    def _describe_node_simple(self, record: dict, prefix: str) -> str:
        """Generate a simple variable reference for a node.

        Args:
            record: Query result dict
            prefix: Field prefix to extract (e.g., "parent")

        Returns:
            A variable reference like "op_129" or "c_78"
        """
        node_id = record.get(f"{prefix}_id")
        labels = record.get(f"{prefix}_labels", [])

        # Determine the variable prefix
        if "Primitive" in labels:
            return f"op_{node_id}"
        if "SubVI" in labels or "Loop" in labels or "Conditional" in labels:
            return f"op_{node_id}"
        if "Constant" in labels:
            return f"c_{node_id}"
        if "Input" in labels:
            return f"i_{node_id}"
        if "Output" in labels:
            return f"o_{node_id}"
        return f"n_{node_id}"

    def _describe_node(self, flow: dict, prefix: str) -> str:
        """Generate a descriptive name for a node in the data flow.

        Uses the same variable naming as CREATE statements so IDs match up.

        Args:
            flow: The flow dict from the query
            prefix: "from" or "to" to pick the right fields

        Returns:
            A description like "op_129:primitive_1419" or "c_78:constant"
        """
        node_id = flow.get(f"{prefix}_id")
        name = flow.get(f"{prefix}_name")
        labels = flow.get(f"{prefix}_labels", [])
        prim_id = flow.get(f"{prefix}_prim")
        value = flow.get(f"{prefix}_value")

        # Determine the variable prefix (must match CREATE statements)
        if "Primitive" in labels or "SubVI" in labels or "Loop" in labels or "Conditional" in labels:
            var_prefix = "op"
        elif "Constant" in labels:
            var_prefix = "c"
        elif "Input" in labels:
            var_prefix = "i"
        elif "Output" in labels:
            var_prefix = "o"
        else:
            var_prefix = "n"

        var_name = f"{var_prefix}_{node_id}"

        # Generate description - no KNOWN_PRIMITIVES, just use primResID
        if "Primitive" in labels and prim_id:
            return f"{var_name}:primitive_{prim_id}"

        if "SubVI" in labels and name:
            return f"{var_name}:{name}"

        if "Constant" in labels:
            if value:
                short_val = value[:20] + "..." if len(value) > 20 else value
                return f'{var_name}:"{short_val}"'
            return f"{var_name}:constant"

        if "Input" in labels or "Output" in labels:
            return f"{var_name}:{name or 'terminal'}"

        return f"{var_name}:{name or 'node'}"

    # === Connector Pane Queries ===

    def get_function_signature(self, vi_name: str) -> list[dict]:
        """Get exposed terminals in slot order for function signature.

        Returns terminals that are on the connector pane, ordered by slot.
        These are the function parameters (inputs) and return values (outputs).
        """
        return self.query("""
            MATCH (v:VI {name: $name})-[:HAS_CONNECTOR_PANE]->(cp:ConnectorPane)
                  -[e:EXPOSES]->(ctrl)
            OPTIONAL MATCH (ctrl)-[:HAS_TERMINAL]->(t:Terminal)
            RETURN ctrl.id AS id, ctrl.name AS name, labels(ctrl) AS labels,
                   e.slot AS slot, t.type AS terminal_type,
                   CASE WHEN 'Output' IN labels(ctrl) THEN 'output' ELSE 'input' END AS direction
            ORDER BY e.slot
        """, {"name": vi_name})

    def get_internal_controls(self, vi_name: str) -> list[dict]:
        """Get controls not on connector pane (internal state).

        These are front panel controls/indicators that don't appear in
        the VI's external interface - typically configuration or state.
        """
        return self.query("""
            MATCH (v:VI {name: $name})-[:PARAMETER_OF|RETURNS*]-(ctrl)
            WHERE NOT EXISTS {
                (v)-[:HAS_CONNECTOR_PANE]->(:ConnectorPane)-[:EXPOSES]->(ctrl)
            }
            RETURN ctrl.id AS id, ctrl.name AS name, labels(ctrl) AS labels,
                   ctrl.type AS type
        """, {"name": vi_name})

    def get_connector_pane(self, vi_name: str) -> dict | None:
        """Get connector pane info for a VI.

        Returns:
            Dict with pattern and slots, or None if no connector pane
        """
        cp = self.query_single("""
            MATCH (v:VI {name: $name})-[:HAS_CONNECTOR_PANE]->(cp:ConnectorPane)
            RETURN cp.pattern AS pattern
        """, {"name": vi_name})

        if not cp:
            return None

        slots = self.query("""
            MATCH (v:VI {name: $name})-[:HAS_CONNECTOR_PANE]->(cp:ConnectorPane)
                  -[e:EXPOSES]->(ctrl)
            RETURN e.slot AS slot, ctrl.id AS ctrl_id, ctrl.name AS name,
                   labels(ctrl) AS labels
            ORDER BY e.slot
        """, {"name": vi_name})

        return {
            "pattern": cp.get("pattern"),
            "slots": slots,
        }

    def get_vi_interface(self, vi_name: str) -> dict:
        """Get just the interface (signature) of a VI for SubVI context.

        Returns minimal info needed to call this VI as a function:
        - name
        - inputs with types
        - outputs with types

        Much smaller than get_vi_context(). Used when providing
        SubVI signatures during parent VI conversion.
        """
        return {
            "name": vi_name,
            "inputs": self.query("""
                MATCH (v:VI {name: $name})<-[:PARAMETER_OF]-(i)
                RETURN i.name AS name, labels(i) AS labels
            """, {"name": vi_name}),
            "outputs": self.query("""
                MATCH (v:VI {name: $name})-[:RETURNS]->(o)
                RETURN o.name AS name, labels(o) AS labels
            """, {"name": vi_name}),
        }

    def get_conversion_context(self, vi_name: str) -> dict:
        """Get context for converting a VI, including SubVI interfaces.

        Returns:
        - vi: full context for the VI being converted
        - subvi_interfaces: signatures of SubVIs it calls (if they exist in the graph)
        """
        vi_context = self.get_vi_context(vi_name)

        # Get SubVI nodes and match them to VI definitions by name
        subvi_nodes = self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS]->(s:SubVI)
            RETURN DISTINCT s.name AS subvi_name
        """, {"name": vi_name})

        subvi_interfaces = {}
        all_vis = set(self.list_vis())

        for node in subvi_nodes:
            subvi_name = node.get("subvi_name")
            if subvi_name and subvi_name in all_vis:
                subvi_interfaces[subvi_name] = self.get_vi_interface(subvi_name)

        return {
            "vi": vi_context,
            "subvi_interfaces": subvi_interfaces,
        }

    def get_vi_context(self, vi_name: str) -> dict:
        """Get complete VI context for code generation as structured data.

        Returns all information needed to generate Python code:
        - inputs with types
        - outputs with types
        - constants with values and python hints
        - operations with primitives and SubVIs
        - terminals with type and direction
        - terminal-level data flow (CONNECTS_TO)
        - SubVI calls

        For the raw Cypher representation, use get_vi_cypher() instead.
        """
        return {
            "name": vi_name,
            "inputs": self.query("""
                MATCH (v:VI {name: $name})<-[:PARAMETER_OF]-(i)
                RETURN i.id AS id, i.name AS name, i.type AS type, labels(i) AS labels,
                       [(i)-[:CONTAINS*]->(child) | {name: child.name, type: child.type, labels: labels(child)}] AS children
            """, {"name": vi_name}),
            "outputs": self.query("""
                MATCH (v:VI {name: $name})-[:RETURNS]->(o)
                RETURN o.id AS id, o.name AS name, o.type AS type, labels(o) AS labels,
                       [(o)-[:CONTAINS*]->(child) | {name: child.name, type: child.type, labels: labels(child)}] AS children
            """, {"name": vi_name}),
            "constants": self.query("""
                MATCH (v:VI {name: $name})-[:CONTAINS]->(c:Constant)
                RETURN c.id AS id, c.value AS value, c.type AS type, c.python AS python
            """, {"name": vi_name}),
            "operations": self.query("""
                MATCH (v:VI {name: $name})-[:CONTAINS]->(op)
                WHERE op:Primitive OR op:SubVI OR op:Loop OR op:Conditional
                RETURN labels(op) AS labels, op.name AS name, op.type AS type,
                       op.id AS id, op.primResID AS primResID,
                       [(op)-[:HAS_TERMINAL]->(t) | {id: t.id, index: t.index, type: t.type, name: t.name, direction: CASE WHEN 'Output' IN labels(t) THEN 'output' ELSE 'input' END}] AS terminals
            """, {"name": vi_name}),
            "terminals": self.query("""
                MATCH (v:VI {name: $name})-[:CONTAINS|RETURNS|PARAMETER_OF*]-(parent)
                MATCH (parent)-[:HAS_TERMINAL]->(t:Terminal)
                RETURN t.id AS id, t.index AS index, t.type AS type, t.name AS name,
                       CASE WHEN 'Output' IN labels(t) THEN 'output' ELSE 'input' END AS direction,
                       parent.id AS parent_id, labels(parent) AS parent_labels,
                       parent.name AS parent_name, parent.primResID AS parent_prim
            """, {"name": vi_name}),
            "data_flow": self.query("""
                MATCH (v:VI {name: $name})-[:CONTAINS|RETURNS|PARAMETER_OF*]-(parent1)
                MATCH (parent1)-[:HAS_TERMINAL]->(t1:Terminal)-[:CONNECTS_TO]->(t2:Terminal)
                OPTIONAL MATCH (parent2)-[:HAS_TERMINAL]->(t2)
                RETURN t1.id AS from_terminal_id, t1.index AS from_index, t1.type AS from_type,
                       parent1.id AS from_parent_id, parent1.name AS from_parent_name,
                       labels(parent1) AS from_parent_labels, parent1.primResID AS from_prim,
                       t2.id AS to_terminal_id, t2.index AS to_index, t2.type AS to_type,
                       parent2.id AS to_parent_id, parent2.name AS to_parent_name,
                       labels(parent2) AS to_parent_labels, parent2.primResID AS to_prim
            """, {"name": vi_name}),
            "subvi_calls": self.get_subvi_calls(vi_name),
        }

    def trace_input_to_output(self, input_name: str) -> list[dict]:
        """Trace data flow from an input to outputs via terminals."""
        return self.query("""
            MATCH (i:Input {name: $name})-[:HAS_TERMINAL]->(start:Terminal)
            MATCH path = (start)-[:CONNECTS_TO*]->(end:Terminal)<-[:HAS_TERMINAL]-(o:Output)
            RETURN i.name AS input_name,
                   [t IN nodes(path) | {id: t.id, type: t.type, index: t.index}] AS terminal_path,
                   o.name AS output_name
        """, {"name": input_name})

    # === Dependency Ordering for Bottom-Up Conversion ===

    def get_leaf_vis(self) -> list[str]:
        """Get VIs that don't call any SubVIs (leaves of the dependency tree).

        These should be converted first.
        """
        results = self.query("""
            MATCH (v:VI)
            WHERE NOT EXISTS {
                MATCH (v)-[:CONTAINS]->(:SubVI)-[:CALLS]->(:VI)
            }
            RETURN v.name AS name
        """)
        return [r["name"] for r in results]

    def get_vi_dependencies(self, vi_name: str) -> list[str]:
        """Get VIs that this VI depends on (SubVIs it calls)."""
        results = self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS]->(:SubVI)-[:CALLS]->(sub:VI)
            RETURN DISTINCT sub.name AS name
        """, {"name": vi_name})
        return [r["name"] for r in results]

    def get_vi_dependents(self, vi_name: str) -> list[str]:
        """Get VIs that depend on this VI (VIs that call it)."""
        results = self.query("""
            MATCH (caller:VI)-[:CONTAINS]->(:SubVI)-[:CALLS]->(v:VI {name: $name})
            RETURN DISTINCT caller.name AS name
        """, {"name": vi_name})
        return [r["name"] for r in results]

    def get_conversion_order(self) -> list[str]:
        """Get VIs in topological order for bottom-up conversion.

        Returns VIs ordered so that dependencies come before dependents.
        Leaf VIs (no SubVI calls) come first, root VIs (not called by anyone) come last.

        Cyclic dependencies are detected and grouped together - they'll appear
        in the order after their external dependencies are satisfied.

        Uses iterative approach via get_ready_to_convert().
        """
        all_vis = set(self.list_vis())
        converted: set[str] = set()
        order = []

        while True:
            ready = self.get_ready_to_convert(converted)
            if not ready:
                # Check for cycles - any remaining VIs form cycles
                remaining = all_vis - converted
                if remaining:
                    # Find VIs in cycles whose external deps are satisfied
                    cycle_ready = self._get_cycle_ready(remaining, converted)
                    if cycle_ready:
                        order.extend(cycle_ready)
                        converted.update(cycle_ready)
                        continue
                break
            order.extend(ready)
            converted.update(ready)

        return order

    def _get_cycle_ready(self, remaining: set[str], converted: set[str]) -> list[str]:
        """Find VIs in cycles that are ready (external deps satisfied).

        Returns VIs whose only unsatisfied dependencies are within the remaining set
        (i.e., they form cycles with each other).
        """
        ready = []
        for vi in remaining:
            deps = set(self.get_vi_dependencies(vi))
            # External deps = deps not in the cycle group
            external_deps = deps - remaining
            # Ready if all external deps are converted
            if external_deps <= converted:
                ready.append(vi)
        return ready

    def get_cycles(self) -> list[list[str]]:
        """Detect and return all cycles in the VI dependency graph.

        Returns:
            List of cycles, where each cycle is a list of VI names.
            Empty list if no cycles exist.
        """
        # Find VIs that are part of cycles by checking for paths back to themselves
        results = self.query("""
            MATCH (start:VI)
            MATCH path = (start)-[:CONTAINS]->(:SubVI)-[:CALLS]->(:VI)
                         (()-[:CONTAINS]->(:SubVI)-[:CALLS]->(:VI))*
                         ()-[:CONTAINS]->(:SubVI)-[:CALLS]->(start)
            WITH start, [n IN nodes(path) WHERE n:VI | n.name] AS cycle
            RETURN DISTINCT cycle
            ORDER BY size(cycle)
        """)

        # Deduplicate cycles (same cycle can be found starting from different nodes)
        seen = set()
        unique_cycles = []
        for r in results:
            cycle = r.get("cycle", [])
            if cycle and len(cycle) > 1:
                # Normalize: start from alphabetically first node
                min_idx = cycle.index(min(cycle))
                normalized = tuple(cycle[min_idx:] + cycle[:min_idx])
                if normalized not in seen:
                    seen.add(normalized)
                    unique_cycles.append(list(normalized))

        return unique_cycles

    def has_cycles(self) -> bool:
        """Check if the dependency graph contains any cycles.

        Returns True if any VI transitively depends on itself.
        """
        # Check each VI to see if it can reach itself via CALLS
        for vi_name in self.list_vis():
            deps = set(self.get_vi_dependencies(vi_name))
            visited = set()
            to_check = list(deps)

            while to_check:
                dep = to_check.pop()
                if dep == vi_name:
                    return True  # Found cycle back to original
                if dep not in visited:
                    visited.add(dep)
                    to_check.extend(self.get_vi_dependencies(dep))

        return False

    def get_conversion_groups(self) -> list[list[str]]:
        """Get VIs grouped for conversion.

        Returns VIs in groups that should be converted together:
        - Non-cyclic VIs are returned as single-element groups
        - Cyclic VIs are grouped together (they need to be converted as a unit)

        Groups are ordered so dependencies come before dependents.
        """
        all_vis = set(self.list_vis())
        converted: set[str] = set()
        groups: list[list[str]] = []

        while converted != all_vis:
            # First, get non-cyclic VIs that are ready
            ready = self.get_ready_to_convert(converted)

            if ready:
                # Non-cyclic VIs can be converted individually
                for vi in ready:
                    groups.append([vi])
                converted.update(ready)
            else:
                # No ready VIs - remaining must be in cycles
                remaining = all_vis - converted
                if remaining:
                    # Find the cycle group whose external deps are satisfied
                    cycle_group = self._get_cycle_ready(remaining, converted)
                    if cycle_group:
                        # These form a cycle - convert together
                        groups.append(sorted(cycle_group))
                        converted.update(cycle_group)
                    else:
                        # Shouldn't happen, but break to avoid infinite loop
                        break

        return groups

    def get_ready_to_convert(self, converted: set[str]) -> list[str]:
        """Get VIs that are ready to convert (all dependencies already converted).

        Args:
            converted: Set of VI names that have already been converted

        Returns:
            List of VI names whose dependencies are all in the converted set
        """
        results = self.query("""
            MATCH (v:VI)
            WHERE NOT v.name IN $converted
            AND NOT EXISTS {
                MATCH (v)-[:CONTAINS]->(:SubVI)-[:CALLS]->(dep:VI)
                WHERE NOT dep.name IN $converted
            }
            RETURN v.name AS name
        """, {"converted": list(converted)})
        return [r["name"] for r in results]


def connect(
    uri: str = "bolt://localhost:7687",
    username: str = "neo4j",
    password: str = "vipy-password",
) -> VIGraph:
    """Convenience function to connect to Neo4j.

    Usage:
        graph = vipy.graph.connect()
        graph.load_vi("path/to/file.vi")
    """
    config = GraphConfig(uri=uri, username=username, password=password)
    graph = VIGraph(config)
    graph.connect()
    return graph
