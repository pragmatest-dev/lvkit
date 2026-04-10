# lvpy Demo Script (30 min including questions)

## 1. What is lvpy? (2 min)

LabVIEW-to-Python converter. No LabVIEW license needed. Reads .vi binary files directly.

Three modes: deterministic AST generation, interactive visualization, AI-assisted cleanup.

## 2. Start simple — describe a VI (3 min)

```bash
# What does this VI do? (shows signature, dependencies, operations)
lvpy describe samples/DAQmx-Digital-IO/In.vi

# With Mermaid dataflow chart
lvpy describe samples/DAQmx-Digital-IO/In.vi --chart
```

Shows signature, inputs/outputs, SubVI calls, control flow, operations. All extracted from the binary, no LabVIEW needed.

```bash
# A more interesting example with dependencies
lvpy describe "samples/JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi" \
  --search-path samples/OpenG/extracted
```

## 3. Visualize the dataflow (5 min)

```bash
# Interactive dependency graph — what calls what
lvpy visualize samples/DAQmx-Digital-IO/In.vi --mode deps -o outputs/demo_deps.html --open

# Dataflow diagram — how data moves through the block diagram
lvpy visualize samples/DAQmx-Digital-IO/In.vi --mode dataflow -o outputs/demo_flow.html --open
```

Click nodes. Show the topology. Point out parallel branches, structure nesting.

**Talking point:** This is the same graph the code generator walks. What you see is what gets converted.

## 4. Generate Python (5 min)

```bash
# Convert a single VI
lvpy generate samples/DAQmx-Digital-IO/In.vi -o outputs/demo --search-path samples/OpenG/extracted
```

Open `outputs/demo/in/in.py` and walk through:
- DAQmx task creation → `nidaqmx.Task()`
- Parallel write + sleep → `ThreadPoolExecutor`
- Sequential frames → sequential Python
- Cleanup → `.stop()`, `.close()`

```bash
# Convert the whole directory
lvpy generate samples/DAQmx-Digital-IO/ -o outputs/demo --search-path samples/OpenG/extracted
```

Both VIs converted, 0 errors.

## 5. Real-world example — dependency chain (5 min)

```bash
# Convert a VI from the Graphical Test Runner
lvpy generate "samples/JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi" \
  -o outputs/demo --search-path samples/OpenG/extracted
```

**Expected:** 13 VIs loaded, 3 AST + 1 vilib generated, 0 errors.

Open `outputs/demo/get_settings_path/graphicaltestrunnerlvlib/get_settings_path.py`:

```python
def get_settings_path() -> GetSettingsPathResult:
    result = get_system_directory(directory_type=SystemDirectoryType.PUBLIC_APP_DATA)
    appended_path = Path(result.system_directory_path) / Path("JKI/VI Tester/Settings.ini")
    stripped_path = Path(appended_path).parent
    Path(stripped_path).mkdir(parents=True, exist_ok=True)
    return GetSettingsPathResult(config_path=appended_path)
```

Walk through what lvpy did:
- Resolved `Get System Directory.vi` from vilib (LabVIEW standard library)
- Resolved `Build Path` and `Strip Path` as OpenG polymorphic VIs — inlined at call sites
- Resolved `Create Dir if Non-Existant` — inlined as `mkdir(parents=True, exist_ok=True)`
- Generated enum `SystemDirectoryType` from LabVIEW `.ctl` typedef
- Produced a clean Python package with proper imports

**Talking point:** No manual mapping. The tool traced the full dependency chain and resolved everything.

## 6. Scale up — convert a whole class (5 min)

```bash
# Convert an entire LabVIEW class with all dependencies
lvpy generate samples/JKI-VI-Tester/source/Classes/TestCase/TestCase.lvclass \
  -o outputs/demo --search-path samples/OpenG/extracted
```

**Expected:** ~94 AST files, 0 errors. 139 VIs loaded (full dependency chain).

Show the generated class wrapper. Show a few method implementations.

**Talking point:** This is a real test framework. 94 files, zero manual intervention.

## 6. Generate documentation (3 min)

```bash
lvpy docs samples/DAQmx-Digital-IO/ outputs/demo_docs
```

Open `outputs/demo_docs/index.html`. Show:
- Cross-referenced VI pages
- Input/output signatures
- Operation listings
- Navigation between VIs

## 7. AI integration — MCP tools (5 min)

```bash
# The same analysis is available as MCP tools for Claude Code / Copilot
lvpy mcp
```

Show the tool list. Explain:
- `load_vi` → `describe_vi` → `get_operations` → `generate_ast_code`
- An LLM can explore the graph, ask questions, then generate code
- Stateful session: load once, query many times

**If Claude Code is available**, demonstrate live:
```
/lvpy load samples/DAQmx-Digital-IO/In.vi
/lvpy describe In.vi
/lvpy operations In.vi
```

## 8. AI integration — MCP + Skills (5 min)

lvpy exposes the graph as MCP tools for any AI editor:

```bash
# Start the MCP server (Claude Code, Copilot, etc.)
lvpy mcp
```

12 tools available: `load_vi`, `describe_vi`, `get_operations`, `get_dataflow`, `get_structure`, `get_constants`, `generate_ast_code`, `analyze_vi`, `generate_documents`, `generate_python`, `list_loaded_vis`, `get_vi_context`.

**If Claude Code is available**, demonstrate live:
```
# Use the MCP tools directly
> load samples/DAQmx-Digital-IO/In.vi
> describe In.vi
> get operations for In.vi
```

**Skills for Claude Code** (5 user-facing skills, installable into a downstream project via `lvpy init --skills claude`; 2 more `/judge-output` and `/trace-bug` stay lvpy-maintainer-only):
- `/lvpy-convert` — full conversion pipeline with resolution loop
- `/lvpy-describe` — human-readable VI description
- `/lvpy-resolve-primitive` — resolve unknown LabVIEW primitives via web search
- `/lvpy-resolve-vilib` — resolve unknown vilib VIs via web search or the user's vi.lib install
- `/lvpy-idiomatic` — improve generated code style

**Talking point:** The AI doesn't guess — it queries the actual dataflow graph. Every wire, every type, every terminal index comes from the binary.

## 9. LLM-enhanced generation (optional, 3 min)

```bash
# Generate with LLM cleanup (requires Anthropic API key)
lvpy llm-generate samples/DAQmx-Digital-IO/In.vi -o outputs/demo_llm \
  --search-path samples/OpenG/extracted
```

The LLM gets the AST output as reference, plus the graph description. It produces idiomatic Python while preserving correctness.

## 10. Q&A (remaining time)

**Common questions and answers:**

*"What LabVIEW features does it handle?"*
Primitives, SubVIs, case structures, loops, sequences, property nodes, invoke nodes, error clusters, parallel branches, shift registers, auto-indexing, polymorphic VIs, enums, typedefs, classes, libraries.

*"What doesn't it handle?"*
Event structures, XControls, ActiveX/.NET interop, some VI Server operations. These raise diagnostic errors — they don't silently produce bad code.

*"How does error handling work?"*
LabVIEW error clusters become Python exceptions. Error wires become try/except. Merge Errors becomes ThreadPoolExecutor future.result() wrapping.

*"Is the generated code production-ready?"*
It's syntactically valid and structurally correct. An AI cleanup pass (lvpy llm-generate) can improve naming and idioms. The goal is 80% automated, 20% human review.

*"How fast is it?"*
The AST pipeline is deterministic — no LLM calls. Single VI: <1 second. 94-file class: ~10 seconds.
