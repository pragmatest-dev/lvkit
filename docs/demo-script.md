# vipy Demo Script (30 min including questions)

## 1. What is vipy? (2 min)

LabVIEW-to-Python converter. No LabVIEW license needed. Reads .vi binary files directly.

Three modes: deterministic AST generation, interactive visualization, AI-assisted cleanup.

## 2. Start simple — analyze a VI (3 min)

```bash
# What's in this directory?
vipy structure samples/DAQmx-Digital-IO/ --json
```

Shows VIs, libraries, classes found. All extracted from binaries, no LabVIEW needed.

## 3. Visualize the dataflow (5 min)

```bash
# Interactive dependency graph — what calls what
vipy visualize samples/DAQmx-Digital-IO/In.vi --mode deps -o outputs/demo_deps.html --open

# Dataflow diagram — how data moves through the block diagram
vipy visualize samples/DAQmx-Digital-IO/In.vi --mode dataflow -o outputs/demo_flow.html --open
```

Click nodes. Show the topology. Point out parallel branches, structure nesting.

**Talking point:** This is the same graph the code generator walks. What you see is what gets converted.

## 4. Generate Python (5 min)

```bash
# Convert a single VI
vipy generate samples/DAQmx-Digital-IO/In.vi -o outputs/demo --search-path samples/OpenG/extracted
```

Open `outputs/demo/in/in.py` and walk through:
- DAQmx task creation → `nidaqmx.Task()`
- Parallel write + sleep → `ThreadPoolExecutor`
- Sequential frames → sequential Python
- Cleanup → `.stop()`, `.close()`

```bash
# Convert the whole directory
vipy generate samples/DAQmx-Digital-IO/ -o outputs/demo --search-path samples/OpenG/extracted
```

Both VIs converted, 0 errors.

## 5. Scale up — convert a class (5 min)

```bash
# Convert an entire LabVIEW class with all dependencies
vipy generate samples/JKI-VI-Tester/source/Classes/TestCase/TestCase.lvclass \
  -o outputs/demo --search-path samples/OpenG/extracted
```

**Expected:** ~94 AST files, 0 errors. 139 VIs loaded (full dependency chain).

Show the generated class wrapper. Show a few method implementations.

**Talking point:** This is a real test framework. 94 files, zero manual intervention.

## 6. Generate documentation (3 min)

```bash
vipy docs samples/DAQmx-Digital-IO/ outputs/demo_docs
```

Open `outputs/demo_docs/index.html`. Show:
- Cross-referenced VI pages
- Input/output signatures
- Operation listings
- Navigation between VIs

## 7. AI integration — MCP tools (5 min)

```bash
# The same analysis is available as MCP tools for Claude Code / Copilot
vipy mcp
```

Show the tool list. Explain:
- `load_vi` → `describe_vi` → `get_operations` → `generate_ast_code`
- An LLM can explore the graph, ask questions, then generate code
- Stateful session: load once, query many times

**If Claude Code is available**, demonstrate live:
```
/vipy load samples/DAQmx-Digital-IO/In.vi
/vipy describe In.vi
/vipy operations In.vi
```

## 8. Q&A (remaining time)

**Common questions and answers:**

*"What LabVIEW features does it handle?"*
Primitives, SubVIs, case structures, loops, sequences, property nodes, invoke nodes, error clusters, parallel branches, shift registers, auto-indexing, polymorphic VIs, enums, typedefs, classes, libraries.

*"What doesn't it handle?"*
Event structures, XControls, ActiveX/.NET interop, some VI Server operations. These raise diagnostic errors — they don't silently produce bad code.

*"How does error handling work?"*
LabVIEW error clusters become Python exceptions. Error wires become try/except. Merge Errors becomes ThreadPoolExecutor future.result() wrapping.

*"Is the generated code production-ready?"*
It's syntactically valid and structurally correct. An AI cleanup pass (vipy llm-generate) can improve naming and idioms. The goal is 80% automated, 20% human review.

*"How fast is it?"*
The AST pipeline is deterministic — no LLM calls. Single VI: <1 second. 94-file class: ~10 seconds.
