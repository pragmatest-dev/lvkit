# vipy: LabVIEW-to-Python, No License Required

A deterministic converter that reads LabVIEW `.vi` binaries and produces working Python — without LabVIEW installed, without an LLM, without guessing.

---

## The Problem

LabVIEW code is locked inside proprietary binaries. You can't read it without NI's tools. You can't version-diff it. You can't migrate it. If you want to move a LabVIEW test framework to Python, you're hand-translating thousands of VIs.

vipy reads those binaries directly and generates Python that preserves the original dataflow semantics — parallel execution, error handling, polymorphic dispatch, all of it.

## Clean-Room Design

We went out of our way to keep this clean-room. Every piece of semantic knowledge comes from published sources:

- **Binary format parsing** uses [pylabview](https://github.com/mefistotelis/pylabview), an open-source reverse-engineered RSRC reader. No NI runtime, no LabVIEW APIs, no proprietary libraries.
- **Primitive definitions** (845 identified) are extracted from NI's published PDF documentation, with page references on every entry. See `data/primitives-from-pdf.json` — each has `"source": "NI PDF Documentation"` and a page number.
- **Terminal names and types** come from the same published docs. Terminal *indices* — the connector pane layout, which the PDF doesn't give you — are auto-discovered from caller dataflow at generation time. When vipy sees a wire connected to terminal index 3, it learns that index 3 exists. No guessing.
- **Enum values and typedefs** are transcribed from published documentation into `data/vilib/_types.json`. Clean-room parsing doesn't have access to LabVIEW's enum labels, so we built the mapping by hand from NI's PDFs.
- **vilib and OpenG mappings** (67 VIs) are all documented with their PDF source pages and verified against observed wiring, not against running LabVIEW.

The result: vipy runs on Linux, Mac, Windows. No NI software. No license. No network calls. Just Python reading bytes.

## How It Works

```
VI Binary → pylabview (XML extraction)
         → Parser (typed dataclasses, not dicts)
         → Two NetworkX graphs (dependency + dataflow)
         → Tiered topological sort (parallel detection)
         → 13 node-specific code generators
         → Python AST (not strings)
         → ast.unparse() → valid Python source
```

Every output file is guaranteed syntactically valid because vipy builds `ast.Module` nodes, not format strings. If it compiles, it was generated from real AST.

The entire pipeline is deterministic. No LLM, no sampling, no randomness. Same `.vi` binary in → same `.py` file out, every time. Variable names come from terminal names in the graph, execution order comes from topological sort of data dependencies, structure comes from the AST builders. There's nothing probabilistic in the chain. You can diff the output, commit it, put it in CI — if the output changes, either the input changed or we shipped a bug.

## Two Graphs, Two Jobs

vipy maintains two separate NetworkX graphs that serve different purposes:

**Graph 1 — Dependency graph** (`nx.DiGraph`): VI-level. Nodes are VI names, edges are "calls" relationships. This graph controls *load order* (callees before callers) and *generation order* (topological sort via `nx.condensation()` to handle mutual recursion). When you run `vipy visualize --mode deps`, this is what you see.

**Graph 2 — Dataflow graph** (`nx.MultiDiGraph`): Node-level. Every operation, constant, and structure across all loaded VIs lives in a single unified graph. Nodes are typed Pydantic models (`VINode`, `PrimitiveNode`, `StructureNode`, `ConstantNode`). Edges are wires with typed `WireEnd` endpoints carrying terminal IDs, indices, and data types. MultiDiGraph because two nodes can be connected by multiple wires (bundled clusters, multiple outputs).

The dependency graph tells vipy *what order to generate code*. The dataflow graph tells vipy *what code to generate*. Cross-VI edges in the dataflow graph connect SubVI call terminals directly to the callee's front panel terminals, enabling type propagation across VI boundaries.

## What It Converts

The flagship test target is **JKI VI Tester** — the LabVIEW community's xUnit test framework.

| Input | Loaded | Generated | Errors |
|-------|--------|-----------|--------|
| `TestCase.lvclass` | 139 VIs | 94 Python files | 0 |
| `Get Settings Path.vi` | 13 VIs (dep chain) | 3 AST + 1 vilib | 0 |
| `DAQmx-Digital-IO/` (directory) | 2 VIs | 2 Python files | 0 |

The 139-VI TestCase class includes LabVIEW classes, polymorphic SubVIs, error clusters, case structures, loops with shift registers, property nodes, invoke nodes, flat sequences, and parallel branches. All converted deterministically with zero errors.

## Parallel Execution: Tiered Topological Sort

LabVIEW is inherently parallel — any two operations without a data dependency can run simultaneously. vipy preserves this.

The algorithm:

1. Build a dependency graph from wire connections between operations
2. Run Kahn's algorithm, but group ready operations into *tiers* instead of a flat list
3. Single-op tiers emit sequential Python
4. Multi-op tiers emit `concurrent.futures.ThreadPoolExecutor` blocks

```python
# Tier 0: one op → sequential
task = create_task()

# Tier 1: one op → sequential
add_channel(task, "Dev1/port0/line0")

# Tier 2: one op → sequential
start_task(task)

# Tier 3: two independent ops → parallel
with concurrent.futures.ThreadPoolExecutor() as _executor:
    def _branch_0():
        task.write(True)
        return task
    def _branch_1():
        time.sleep(0.5)
    _f0 = _executor.submit(_branch_0)
    _executor.submit(_branch_1)
task = _f0.result()

# Tier 4: one op → sequential
stop_task(task)
```

The parallelism comes from the graph, not from heuristics. If two operations have no data path between them, they're in the same tier. Period.

## Error Handling: Cluster-to-Exception Translation

LabVIEW passes error clusters through wires. Python uses exceptions. vipy translates between the two models based on what the graph actually does with errors:

**Error case structures** (case selector wired to an error cluster): vipy emits only the no-error frame. In the TestCase class, 45 of 47 error-case structures have empty error frames — they just re-wire the cluster. Python's exception propagation handles this naturally.

**Merge Errors at parallel join points**: When parallel branches can independently fail, vipy wraps `future.result()` in try/except with a held-error pattern:

```python
_held_error = None
try:
    result_0 = _f0.result()
except LabVIEWError as e:
    _held_error = _held_error or e
    result_0 = None
# ... each branch ...
if _held_error:
    raise _held_error  # First error wins
```

This preserves LabVIEW's semantics: all branches get to finish, the first error is re-raised at the merge point.

**The placement is graph-driven**, not pattern-matched. vipy traces error wires backward through the graph to determine scope. Only the operations actually on the error path get wrapped.

## Polymorphic VI Resolution

LabVIEW polymorphic VIs bundle N variant implementations behind a single name — but the variants can have completely different terminal layouts, different input/output counts, different types. This isn't Java-style interface polymorphism; each variant is its own VI with its own connector pane. The caller's VI binary records which variant was selected at edit time via the `polySelector` XML attribute.

vipy reads that selector, looks up the specific variant in `data/vilib/*.json`, and emits variant-specific code with the correct terminal mapping for that variant:

```python
# Array Size(1D) variant — 1 input, 1 output:
size = len(my_array)

# Array Size(2D) variant — 1 input, 2 outputs:
rows, cols = len(my_array), len(my_array[0])
```

No generic wrappers, no runtime dispatch. The variant's terminal indices, types, and code template are all distinct per variant.

## Structure Handling

13 specialized code generators handle every LabVIEW structure type:

| Structure | Python Translation |
|-----------|-------------------|
| Flat Sequence | Sequential statements |
| Case Structure | `if`/`elif`/`else` with tunnel merging |
| For Loop | `for` with auto-indexing, shift registers, accumulators |
| While Loop | `while` with stop condition, shift registers |
| Property Node | `obj.attr` (read) / `obj.attr = val` (write), sequential |
| Invoke Node | `obj.method(args)` with return unpacking |
| Format Into String | f-strings with placeholder substitution |
| SubVI Call | Function call with polymorphic resolution |
| Primitives | Template substitution from 103 codegen-ready definitions |

Property node drawers execute sequentially (top-to-bottom), not in parallel — matching LabVIEW's actual semantics. Flat sequence frames execute ALL contained nodes, even unwired ones. These details matter for correctness.

## The Numbers

| Metric | Count |
|--------|-------|
| Python source (src/) | 33,874 lines |
| Test functions | 520 |
| Test files | 25 |
| Sample VIs in corpus | 1,993 |
| Codegen-ready primitives | 103 |
| Known primitives (from PDF) | 845 |
| vilib + OpenG VIs mapped | 67 |
| Node code generators | 13 |
| MCP tools exposed | 14 |
| CLI commands | 11 |
| Claude Code skills | 7 |

## AI Integration (Optional Layer)

The deterministic pipeline is the foundation. On top of it, vipy offers:

- **MCP server** (`vipy mcp`): 14 tools for Claude Code, Copilot, or any MCP-compatible editor. Load a VI, explore the graph, generate code — all through tool calls.
- **LLM cleanup** (`vipy llm-generate`): Takes the AST output as a reference and asks an LLM to produce idiomatic Python. Falls back to AST if the LLM produces invalid syntax.
- **7 Claude Code skills**: `/convert` (full pipeline with resolution loop), `/describe-vi`, `/resolve-primitive`, `/resolve-vilib`, `/trace-bug`, `/judge-output`, `/idiomatic`.

The AI never sees raw bytes. It queries the *typed dataflow graph* through MCP tools. Every wire, every type, every terminal index comes from the binary — the LLM just makes the output prettier.

## What This Means for LabVIEW Test Framework Teams

If you maintain a LabVIEW test framework like JKI VI Tester:

1. **Read your own code without LabVIEW**: `vipy describe YourVI.vi` shows signature, dependencies, operations, and dataflow — on any machine.
2. **Generate documentation**: `vipy docs YourProject/ output/` produces cross-referenced HTML with dependency graphs, no LabVIEW required.
3. **Migrate to Python incrementally**: Generate a Python skeleton from your existing VIs, then refine. The skeleton preserves parallel execution, error handling, and dependency structure.
4. **Deterministic output**: Same VI always produces the same Python. No LLM variance, no hallucinations, no randomness. Suitable for CI/CD.
5. **Visualize what you have**: Interactive dependency graphs and dataflow diagrams in the browser. See which VIs call which, how data flows, where parallel branches exist.

The JKI VI Tester's 139-VI TestCase class converts to 94 Python files with zero errors. That's not a demo — that's a real test framework, converted mechanically, in under 10 seconds.
