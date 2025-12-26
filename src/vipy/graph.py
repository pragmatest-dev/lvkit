"""Neo4j graph database integration for VI hierarchy."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

from neo4j import GraphDatabase, Driver

from .cypher import from_vi, extract_vi_xml


@dataclass
class GraphConfig:
    """Neo4j connection configuration."""
    uri: str = "bolt://localhost:7687"
    username: str = "neo4j"
    password: str = "password"
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

    def __enter__(self) -> "VIGraph":
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

            # Accumulate CREATE statements
            if line.startswith("CREATE"):
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
        """Get data flow edges within a VI."""
        return self.query("""
            MATCH (v:VI {name: $name})-[:CONTAINS|RETURNS|PARAMETER_OF*]-(n1)
            MATCH (n1)-[:FLOWS_TO]->(n2)
            RETURN n1.name AS from_name, labels(n1) AS from_type,
                   n2.name AS to_name, labels(n2) AS to_type
        """, {"name": vi_name})

    def trace_input_to_output(self, input_name: str) -> list[dict]:
        """Trace data flow from an input to outputs."""
        return self.query("""
            MATCH path = (i:Input {name: $name})-[:FLOWS_TO*]->(o:Output)
            RETURN [n IN nodes(path) | {name: n.name, labels: labels(n)}] AS path
        """, {"name": input_name})


def connect(
    uri: str = "bolt://localhost:7687",
    username: str = "neo4j",
    password: str = "password",
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
