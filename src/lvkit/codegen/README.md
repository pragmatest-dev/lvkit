# AST Code Generator

Deterministic Python code generator for LabVIEW VIs. No LLM — pure dataflow analysis.

## The Big Idea

LabVIEW is a dataflow language: operations execute when their inputs arrive. This generator:

1. Builds a **unified graph** of all VIs with typed edges between terminals
2. Performs a **tiered topological sort**: each tier's nodes do not depend on each other
3. Generates **Python AST nodes** directly (not strings), guaranteeing valid syntax
4. Writes each tier's nodes one after another: any order of independent nodes is one LabVIEW could run them in, and errors are values on the wire (`docs/_internal/design/errors-as-values.md`), so a failing branch does not stop the others

The output is working Python that preserves LabVIEW's behaviour. Only Start Asynchronous Call runs code on another thread (`concurrent.futures`).

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
│      → topological_sort_tiered()│  Dependency tiers
│      → for each node of a tier: │
│        → nodes.generate(node)   │  Dispatch to handler
│          → handler.generate()   │  Emit AST nodes
│          → error_flow.guard()   │  Skip on an incoming error
│    → build_return_stmt()        │  Frozen result dataclass
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
| `error_flow.py` | Errors as values: a node's own error terminals, the guard that skips it on an error, Merge Errors, an input that reads an error cluster's status |
| `native_failure.py` | A node's Python failure becomes its error out, with the code its catalog entry names |
| `output_binding.py` | Whether a node's output goes straight into its reader or into a variable |
| `local_indicators.py` | Indicators held in variables: read by a Local Variable, kept between calls, written inside a frame |
| `error_path.py` | Which nodes handle an error wire: the vi.lib error VIs a call site generates itself, Merge Errors, the error case selector |
| `gaps.py` | `Gap`, `catch_gap`: a node that cannot be converted raises `NotImplementedError(<reason>)` where it would run, and is recorded as a gap |
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
function, written once into the module through `ctx.module_declarations.declare`.

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
the node's setup is loaded the first time a node that uses it runs (a cached
`@functools.cache` function per library, and one per function that looks it up and sets
its `argtypes`/`restype`), so a module imports on a machine without the library and the
node raises `OSError` when it runs; a library wired to `path in` is loaded each time the
node runs.

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

LabVIEW operations execute when their inputs are ready. The tiered sort groups operations by dependency depth:

```
Tier 0: [Create Task]           ← no dependencies, runs first
Tier 1: [Add Channel]           ← depends on Create Task
Tier 2: [Start Task]            ← depends on Add Channel
Tier 3: [Write, Wait]           ← both depend on Start, independent of each other
Tier 4: [Stop Task]             ← depends on Write
Tier 5: [Clear Task]            ← depends on Stop
```

Every tier's nodes are written one after another, in a deterministic order. Nodes of one tier do not depend on each other, so this is an order LabVIEW could run them in. Each branch carries its own error value, so a failure on one branch does not stop the other; consecutive nodes skipped on the same error share one `if` (`error_flow.share_guards`).

## Variable Resolution

Variables live on the graph, not in a dict. `CodeGenContext` wraps `InMemoryVIGraph`:

- `ctx.bind(terminal_id, var_name)` — sets `var_name` on a terminal node
- `ctx.resolve(terminal_id)` — BFS through incoming edges to find a bound terminal
- `ctx.wired_input(terminal_id, node)` — the expression feeding a node's input: `None` when unwired or an error cluster, `"None"` when the source is a gap constant, `CodeGenError` naming the source when a wired input has no variable. Every generator that reads a node's input goes through it
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
        input_val = ctx.wired_input(node.terminals[0].id, node)

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
