# AST Code Generator

Deterministic Python code generator for LabVIEW VIs. No LLM — pure dataflow analysis.

## The Big Idea

LabVIEW is a dataflow language: operations execute when their inputs arrive, independent operations run in parallel. This generator:

1. Builds a **unified graph** of all VIs with typed edges between terminals
2. Performs **tiered topological sort** to discover natural parallelism
3. Generates **Python AST nodes** directly (not strings), guaranteeing valid syntax
4. Emits `concurrent.futures.ThreadPoolExecutor` for genuinely parallel operations

The output is working Python that preserves LabVIEW's execution semantics — including parallelism that most "converters" would serialize away.

## Pipeline

```
VI file
  │
  ▼
┌─────────────────────────────────┐
│  Parser (parser/)               │  pylabview XML → typed dataclasses
│  BlockDiagram, Nodes, Wires     │  (Node, Wire, Constant, Terminal)
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Unified Graph (graph/)         │  All VIs in one nx.MultiDiGraph
│  InMemoryVIGraph                │  Cross-VI edges for type propagation
│  Terminal resolution by         │  Topological sort for execution order
│  type + direction matching      │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Code Generator (codegen/)      │  This directory
│                                 │
│  build_module()                 │  Entry point
│    → CodeGenContext.from_graph() │  Bind inputs + constants
│    → generate_body()            │  Walk operations
│      → topological_sort_tiered()│  Discover parallel tiers
│      → for each tier:           │
│          if 1 op: emit directly │
│          if N ops: ThreadPool   │
│        → get_codegen(op)        │  Dispatch to handler
│          → handler.generate()   │  Emit AST nodes
│    → build_return_stmt()        │  NamedTuple result
│    → build_module_ast()         │  Wrap in module
│    → optimize_module()          │  Dead code, unused imports
│    → ast.unparse()              │  AST → Python source
└─────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `builder.py` | `build_module()` entry point, tiered topological sort, module assembly |
| `context.py` | `CodeGenContext` — variable bindings on the graph, resolve/bind/get_source |
| `fragment.py` | `CodeFragment` — statements + bindings + imports returned by handlers |
| `ast_utils.py` | Helper functions: `parse_expr()`, `to_var_name()`, `build_assign()` |
| `ast_optimizer.py` | Post-processing: remove duplicate/unused imports, eliminate dead code |
| `dataflow.py` | `DataFlowTracer` — trace wires to find producing operations |
| `error_handler.py` | Held-error model for parallel branches with error clusters |
| `nodes/` | Per-node-type code generators (see below) |

## Node Handlers (`nodes/`)

Each handler takes an `Operation` and `CodeGenContext`, returns a `CodeFragment`.

| Handler | Node Types | What It Generates |
|---------|-----------|-------------------|
| `subvi.py` | SubVI calls (`iUse`, `polyIUse`, `dynIUse`) | Function calls, inline vilib templates, dynamic dispatch (`obj.method()`) |
| `primitive.py` | LabVIEW primitives (Add, Compare, etc.) | Template substitution from `primitives.json` |
| `loop.py` | While/For loops | `while`/`for` with shift registers, auto-indexing, accumulators |
| `case.py` | Case structures | `if/elif/else` chains from case frames |
| `sequence.py` | Flat/stacked sequences | Sequential code blocks, one per frame |
| `compound.py` | Compound arithmetic (`cpdArith`, `aBuild`) | `x or y or z`, `[a, b, c]` |
| `property_node.py` | Property read/write (`propNode`) | `obj.attr` / `obj.attr = val` |
| `invoke_node.py` | Invoke nodes (`invokeNode`) | `obj.method(args)` |
| `printf.py` | Format Into String | f-string or `%` formatting |
| `nmux.py` | Node multiplexer | Passthrough / conditional select |
| `constant.py` | Constants | Literal values (handled during context init, rarely needs handler) |

## Tiered Topological Sort

The core insight: LabVIEW operations execute when inputs are ready. Independent operations run in parallel. The tiered sort groups operations by dependency depth:

```
Tier 0: [Create Task]           ← no dependencies, runs first
Tier 1: [Add Channel]           ← depends on Create Task
Tier 2: [Start Task]            ← depends on Add Channel
Tier 3: [Write, Wait]           ← both depend on Start, independent of each other → PARALLEL
Tier 4: [Stop Task]             ← depends on Write
Tier 5: [Clear Task]            ← depends on Stop
```

Single-operation tiers emit sequential code. Multi-operation tiers emit:
```python
with concurrent.futures.ThreadPoolExecutor() as _executor:
    def _branch_0():
        task.write(False)
        return task
    _f0 = _executor.submit(_branch_0)

    def _branch_1():
        time.sleep(0.5)
    _executor.submit(_branch_1)
task = _f0.result()
```

## Variable Resolution

Variables live on the graph, not in a dict. `CodeGenContext` wraps `InMemoryVIGraph`:

- `ctx.bind(terminal_id, var_name)` — sets `var_name` on a terminal node
- `ctx.resolve(terminal_id)` — BFS through incoming edges to find a bound terminal
- `ctx.get_source(terminal_id)` — returns `SourceInfo` for the first incoming edge
- `ctx.is_wired(terminal_id)` — checks if a terminal has any connections

This means variable resolution automatically traces through wires, tunnels, and cross-VI edges. No separate "flow map" or "bindings dict" — the graph IS the binding store.

## Adding a New Node Handler

1. Create `nodes/my_handler.py` implementing `NodeCodeGen.generate()`
2. Add dispatch in `nodes/base.py:get_codegen()` — check `node_type` or `labels`
3. Return a `CodeFragment` with AST statements, output bindings, and imports

```python
class MyNodeCodeGen(NodeCodeGen):
    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        # Resolve inputs
        input_val = ctx.resolve(node.terminals[0].id)

        # Build AST
        stmt = build_assign("result", parse_expr(f"my_func({input_val})"))

        # Bind outputs
        bindings = {node.terminals[1].id: "result"}

        return CodeFragment(
            statements=[stmt],
            bindings=bindings,
            imports={"from my_lib import my_func"},
        )
```
