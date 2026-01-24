# Two-Graph Architecture

`InMemoryVIGraph` maintains two separate graph structures.

## Dependency Graph

VI-to-VI relationships (caller -> callee). Used to determine processing order.

```python
self._dep_graph: nx.DiGraph  # VI name -> VI name
```

Usage:
```python
for vi_group in graph.get_generation_order():
    # Process each VI (groups handle recursive VIs)
```

## Per-VI Dataflow Graphs

Operation/wire graphs for execution order within each VI.

```python
self._dataflow: dict[str, nx.DiGraph]  # vi_name -> graph
```

Nodes have `kind`: `input`, `output`, `constant`, `subvi`, `primitive`, `operation`

Usage:
```python
for op_id in graph.get_operation_order(vi_name):
    op = graph.get_node(vi_name, op_id)
```

## Why Separate

- Dependency graph: Coarse-grained, determines which VI to generate next
- Dataflow graph: Fine-grained, determines statement order within a function
