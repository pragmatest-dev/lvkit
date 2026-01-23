"""Legacy modules - deprecated, use memory_graph instead.

These modules are kept for backward compatibility but are deprecated:
- cypher.py: Neo4j graph operations (use memory_graph.py instead)
- graph.py: VIGraph wrapper (use InMemoryVIGraph instead)
"""

import warnings

warnings.warn(
    "vipy.legacy is deprecated. Use vipy.memory_graph for graph operations.",
    DeprecationWarning,
    stacklevel=2,
)
