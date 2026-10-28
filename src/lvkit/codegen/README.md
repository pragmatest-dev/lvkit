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
| `error_path.py` | Which nodes handle an error wire, and the nodes upstream of it |
| `gaps.py` | `Gap`, `catch_gap`: a node that cannot be converted raises `NotImplementedError(<reason>)` where it would run, and is recorded as a gap |
| `error_wires.py` | `try`/`except` around the code that raises the error a Clear Errors / error case handles |
| `errors_module.py` | The `errors.py` (`LabVIEWError`) written once into a generated package |
| `package_modules.py` | `PackageModules` — the package-root modules for state VIs share, from graph queries over the package's VIs (`graph/package_queries.py`) |
| `queue_module.py` | `queues.py`: one `queue.Queue` per constant queue name, plus `by_name` for names computed at run time |
| `shared_variable_module.py` | `shared_variables.py`: one module-level name (and `_timestamp`) per Shared Variable |
| `default_objects.py` | Python value of a property of a VI Server class's default object (`default_objects.json`) |
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
| `property_node.py` | Property read/write (`propNode`) | `obj.attr` / `obj.attr = val`; an unwired reference reads the default object's documented value written in |
| `queue_ops.py` | Queue Operations | `queue.Queue` calls: `put`/`get(timeout=...)`, `shutdown()`, named queues from `queues.py` |
| `shared_variable.py` | Shared Variable nodes | attribute read/write on the `shared_variables` module |
| `invoke_node.py` | Invoke nodes (`invokeNode`) | `obj.method(args)` |
| `ext_func.py` | Call Library Function Node (`extFunc`) | module-level `ctypes` library, prototype and structures; a call that packs each parameter and reads it back (library loading: `ext_func_library.py`, below) |
| `printf.py` | Format Into String | f-string or `%` formatting |
| `nmux.py` | Node multiplexer | Passthrough / conditional select |
| `constant.py` | Constants | Literal values (handled during context init, rarely needs handler) |

### Match Pattern

`nodes/ops/match_pattern.py` writes Match Pattern as `re.search` on `string[start:]`
(not `pos=`, so a leading `^` anchors at the offset). A constant `regular expression`
is translated while generating (`match_pattern_regex.to_python_regex`) and written as
the `re` pattern; a computed one calls `_match_pattern_regex`, the source of that same
function, written once into the module through `ctx.ctypes_module.declare`.

Search and Replace String (`nodes/ops/search_and_replace.py`) shares the input reading,
the search start and the translator declaration with Match Pattern
(`nodes/ops/_regex_node.py`). In plain mode the search string is `re.escape`d (a call
needing nothing more is `str.replace`); in Regular Expression mode (the node's
`objFlags` bit 0x2000000) `pcre_regex.py` reads the search string as PCRE and the
replace string's `$n` / `${n}` / `\$` as a Python template, both translated while
generating for a constant and by functions written once into the module for a computed
one.

### Call Library Function Node libraries

`nodes/ext_func_library.py` decides how a generated module loads the library a node
names. The loader is `ctypes.CDLL`, or `getattr(ctypes, "WinDLL", ctypes.CDLL)` for
a `stdcall` node (`WinDLL` exists only on Windows). As in LabVIEW, a library typed in
the node's setup is loaded when the module is imported and a library wired to
`path in` when the node runs.

- **Wildcards** in the typed name (`.*` the platform's extension, `*` 32 or 64, `**`
  nothing or `_64`, per NI's "Configuring the Call Library Function Node") are
  expanded by a small `_library_file` function written into the module only when a
  library needs it; a wired path has its file name expanded at the call.
- **By name** (a name with no folder, or a path rooted in a LabVIEW folder): the
  operating system finds it.
- **A library recorded relative to the VI, inside the project root**: copied into the
  generated package in the package folder its project folder is generated as (the
  folders go through `module_part` like module packages; file names under it stay
  as they are) (a file at a path from the module's
  folder in `data_files`, written by `pipeline._write_data_files`) and loaded with
  `os.path.join(os.path.dirname(__file__), ...)`. Every file another platform or
  bitness expansion of a wildcard name selects is copied too, and so is each library
  the copied file needs (`library_dependencies.py`: PE imports and delay-load
  imports, ELF `DT_NEEDED`, Mach-O `@loader_path`/`@rpath` names, told apart by the
  file's first bytes) that sits in its own folder inside the project, recursively;
  PE names match case-insensitively, the others exactly. A needed library found
  nowhere in that folder is a system library and is left to the operating system.
- **A project library that is not on disk** stops generation, naming the VI, the node
  and the recorded path. A library recorded outside the project root is loaded by
  name.

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
