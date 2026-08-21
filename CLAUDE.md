# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

lvkit converts LabVIEW VI files to Python code without requiring a LabVIEW license. It uses [pylabview](https://github.com/mefistotelis/pylabview) as the core parser for reading VI file formats.

## ⛔ CLEAN-ROOM — NEVER SUGGEST OPENING LabVIEW (READ THIS FIRST)

**The maintainer does NOT have LabVIEW installed and legally CANNOT use it for this work.** NI's EULA prohibits using LabVIEW to reverse-engineer LabVIEW; doing so would poison lvkit's clean room. **The clean room is the entire reason this project exists** — if LabVIEW were an option there would be no project.

So: **NEVER tell the maintainer to "open it in LabVIEW", "click the node", "check quick help / the manual in LabVIEW", or confirm anything by inspecting LabVIEW.** Not once. Identifying a primitive, a vilib VI, a type, or any behavior draws ONLY from clean-room sources:

1. **The parsed graph** — pylabview reads the `.vi` binary with no license: real per-terminal types (incl. array *element* type and refnum `ref_type`), dataflow, structure, constants.
2. **NI's PUBLIC web docs** — `https://docs-be.ni.com/api/bundle/labview-api-ref/page/...` (JSON `topic_html`; the `www.ni.com` SPA returns only a shell).
3. **Algorithm knowledge** (LZW, MD5, GIF, …) — reconstruct the VI's math and let it force the answer.
4. **Deterministic deduction** from 1–3 (e.g. "an output compared against a code *count* must be count-scaled → `2^width` → Power Of 2, not log2").

When stuck: do MORE of 1–4, or write a `"placeholder": true` primitive entry. Ask the maintainer to describe the *algorithm/domain* if needed — **never to inspect LabVIEW.** Also: ship ZERO NI-derived artwork (clean-room glyphs only).

## ⛔ VERIFY BEFORE CLAIMING — NEVER OFFLOAD THE CHECK TO THE MAINTAINER (READ THIS FIRST)

**Every factual statement about this codebase — architecture, behavior, an API, a line, a count, a timing, "X is faster/bigger than Y", "the code does Z" — MUST be backed by a receipt from THIS session: a `file:line` you read, or command/test output you ran, cited inline.** No exceptions.

- **Verify BEFORE you state it, not when challenged.** A claim reaches the maintainer only after you've already checked it. The maintainer must NEVER be the one who catches a wrong claim — if they have to adversarially re-check you, you have already failed.
- **Never infer-then-assert.** Reasoning from memory, from a partial read, or from "this is probably how it works" is the exact failure mode. Grepping one thing and inferring a conclusion is NOT verification — read the whole relevant path end to end.
- **If you have not verified it, do not say it.** Say "I haven't checked — checking now" and go check, or say "I don't know." A plausible guess stated as fact is worse than "I don't know."
- **Memory NEVER holds code facts** (mechanisms, line numbers, counts, timings) — they go stale and become confabulation. Memory holds only how-we-work, hard constraints, LabVIEW-format facts, and pointers. Re-read code facts from the source every time.
- **Plans are claims too.** Every premise in a plan is cited (`file:line`) or measured before it's written — do not build on an unverified premise.
- **NEVER answer a behavioral failure with a verbal promise alone.** "I'll do better" / "I'll hold to that" evaporates on the next compaction or clear. Any behavioral correction MUST be written to disk **in the same turn** — the durable instruction surfaces (this `CLAUDE.md` and the memory files, belt-and-suspenders) — or it does not count. A commitment that isn't written did not happen.

This is not "try harder." It is a hard gate: unverified → unsaid; unwritten commitment → didn't happen.

## ⛔ BUGS — INVESTIGATE ONLY; THE MAINTAINER DECIDES THE FIX (READ THIS FIRST)

**For any bug, independent decision-making on the FIX is revoked.** The workflow is: **investigate → present findings → the maintainer decides → only then implement, exactly as agreed.** Do NOT write code — not a "small" fix, not an "obvious" one, not a docstring — until the maintainer has explicitly approved the approach.

- **Never ship a hard-coded / string-matched / heuristic fix.** If a fix keys off a string label (`flavor.endswith("Waveform")`, a literal `"Digitaldata"`) or a hand-written index→name table, that is precisely the kind of fix that is NOT trusted here — surface it as a finding, flag it as suspect, and let the maintainer direct a generic, data-driven solution.
- **Investigation means:** parse/grep/read to find the real root cause and where the correct data actually lives in the pipeline. Report that. Then STOP and wait.
- This is a hard gate, same standing as VERIFY BEFORE CLAIMING. Persisted in memory: `feedback_bugs_investigate_then_discuss`.

## Commands

Always use `uv run` — it automatically activates the project venv without a separate activation step.

```bash
# Install with dev dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_parser.py::test_parse_vi

# Lint
uv run ruff check .

# Type check
uv run python -m pyright src/

# Scripts
uv run python scripts/generate_python.py "path/to/file.vi" -o outputs
```

## Architecture

The conversion pipeline:

1. **Binary extraction**: pylabview (subprocess) reads the VI binary → XML files (`_BDHb.xml`, `_FPHb.xml`, `.xml`)
2. **Parsing** (`parser/`): `parse_vi()` converts XML → `ParsedVI` dataclasses (nodes, wires, constants, types, front panel)
3. **Graph construction** (`graph/`): `ParsedVI` → `InMemoryVIGraph` NetworkX multi-digraph. `get_vi_context()` returns `VIContext`.
4. **Code generation** (`codegen/`): `build_module(vi_context, vi_name)` walks `VIContext` → Python `ast.Module` → source string
5. **Orchestration** (`pipeline.py`): multi-VI load ordering, dependency resolution, file output

### Key Modules

- `src/lvkit/parser/` — XML → `ParsedVI` dataclasses (nodes, wires, constants, types)
- `src/lvkit/graph/` — `InMemoryVIGraph`, graph construction, queries, operations
- `src/lvkit/models.py` — shared type definitions used by parser, graph, and codegen (`LVType`, `Operation`, `Frame`, `Terminal`, `Tunnel`, etc.)
- `src/lvkit/graph/models.py` — graph/codegen-only types (`GraphNode` hierarchy, `VIContext`, `Wire`, query/info types, `BranchPoint`)
- `src/lvkit/codegen/builder.py` — `build_module()` entry point for AST generation
- `src/lvkit/pipeline.py` — orchestrates multi-VI generation
- `src/lvkit/cli.py` — command-line interface
- `src/lvkit/mcp/` — MCP server (16 tools)

### Standard Test Command

```bash
# Single VI
python scripts/generate_python.py "path/to/file.vi" -o outputs --search-path .lvkit/cache/samples/OpenG/extracted

# LabVIEW class (.lvclass)
python scripts/generate_python.py "path/to/MyClass.lvclass" -o outputs --search-path .lvkit/cache/samples/OpenG/extracted

# LabVIEW library (.lvlib)
python scripts/generate_python.py "path/to/MyLib.lvlib" -o outputs --search-path .lvkit/cache/samples/OpenG/extracted

# Directory of VIs
python scripts/generate_python.py "path/to/vi_folder/" -o outputs --search-path .lvkit/cache/samples/OpenG/extracted
```

## Error Handling Strategy

LabVIEW uses error clusters passed through wires. Python uses exceptions. The conversion strategy:

1. **No error clusters → Natural Python exceptions** - Just let exceptions propagate
2. **Error clusters + parallel branches → Held error model**

### Held Error Model

When a VI has parallel branches AND error terminals, we use this pattern:

```python
def my_vi(input_data):
    _held_error = None  # Track errors from branches

    # Parallel branch 0
    try:
        branch_0_result = branch_0_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_0_result = None

    # Parallel branch 1
    try:
        branch_1_result = branch_1_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_1_result = None

    # Merge point - raise first error
    if _held_error:
        raise _held_error

    return result
```

This preserves LabVIEW's semantics where:
- Branches continue executing even if one errors
- First error is preserved and raised at merge point
- All branches get a chance to clean up

Implementation: `src/lvkit/codegen/error_handler.py`

## Searching for Code in VIs

A `.vi` is a binary — you cannot grep it directly. Finding a construct (a
`primResID`, a node class, a structure) is **two steps**:

1. **Extract the XML** with pylabview. `lvkit.extractor.extract_vi_xml(vi_path)`
   runs pylabview **in-process** and caches `*_BDHb.xml` (block diagram) +
   `*_FPHb.xml` (front panel) in a per-user cache **outside the repo**
   (`extractor.global_cache_root()` — `$LVKIT_CACHE_DIR`, else `~/.lvkit/cache/`),
   keyed by the resolved source root + content hash, so re-runs are no-ops. Use
   `resolve_extracted(vi_path)` to find the cached XML; many corpus VIs are
   already extracted.
2. **Grep the XML.** e.g. `<primResID>1234</primResID>` for a primitive, or
   `class="whileLoop"` / `class="caseStruct"` / `class="flatSequence"` /
   `class="fBox"` for a structure.

**NEVER run the full parser (`parse_vi` / graph build) across the whole corpus
just to locate a known element** — that OOM-crashes WSL. So: extract-then-grep
the XML — grep the pre-extracted `*_BDHb.xml` under the cache root
(`~/.lvkit/cache/**/*_BDHb.xml`), and only
`parse_vi(bd_xml=...)` the handful of VIs that matched. Cap the scan and `del`
each VI's text before the next. (Bulk extraction of a whole corpus uses batched
worker subprocesses — see `scripts/extract_corpus.py` — because in-process
pylabview *accumulates* memory across VIs.)

## Adding New Primitives

LabVIEW primitives are identified by `primResID`. When a conversion fails with `PrimitiveResolutionNeeded`, add an entry to `src/lvkit/data/primitives.json`:

```json
{
  "1234": {
    "name": "My Primitive",
    "category": "numeric",
    "python_code": "{a} + {b}",
    "inputs": [
      {"index": 0, "name": "a", "type": "DBL"}
    ],
    "outputs": [
      {"index": 2, "name": "result", "type": "DBL"}
    ]
  }
}
```

Use the caller's dataflow in the exception output to determine correct terminal indices — do not guess.

## Adding New VILib VIs

When a conversion fails with `VILibResolutionNeeded`, add the VI to the appropriate `src/lvkit/data/vilib/<category>.json`. The exception output shows terminal names from XML and actual wire indices from the caller — use those indices to fill in the `"index"` field for each terminal.

**Workflow:**
1. Run the code generator; note the exception output
2. Match "Wire types from dataflow" indices to the terminal names listed
3. Add entries to the vilib JSON with the correct `"index"` values
4. Re-run to verify

## VILib Terminal Resolution Workflow

When the code generator encounters a vilib VI with missing terminal indices, it raises a `VILibResolutionNeeded` exception. This is intentional - use the caller's dataflow info to fill in the missing indices.

**Workflow:**
1. Run the code generator (e.g., `scripts/generate_python.py`)
2. When a VI lacks terminal index info, exception is raised with:
   - Terminal names from the vilib JSON
   - Wire types from the caller's dataflow (shows actual indices being used)
   - Public NI docs reference (when known)
3. Use the **caller's dataflow** to determine correct indices - DO NOT GUESS
4. Update `data/vilib/<category>.json` with the correct terminal indices
5. Re-run to verify

**Example exception output:**
```
VILib resolution needed for 'Error Cluster From Error Code.vi'.

Terminal names from XML:
  - is warning? (False)
  - error code (0)
  ...

Wire types from dataflow:
  - idx_0 (input)    <- These are the actual indices from the caller
  - idx_1 (output)
  ...
```

The "Wire types from dataflow" section shows what terminal indices the caller is actually using. Match these to the terminal names and add `"index": N` to each terminal in the JSON.

## Code Style

- Python 3.10+ required
- Ruff for linting (rules: E, F, I, UP)
- mypy with strict mode for type checking
- Line length: 88 characters
- **Prefer dataclasses over dicts** - Use typed dataclasses from `models.py` or `graph/models.py` instead of raw dictionaries. Use attribute access (`obj.field`) not `.get("field")`
- **Enums, and how to pick a base** - A closed, named value set is an `Enum` — never a bare-constants namespace class (`class X: A = 0; B = 1`) or a loose module-level group. Choose the base by how the value lives **at rest** and whether raw scalars still flow through the code:
  - Raw `int`/`str` values are compared with `==`/`in` or built positionally at many sites (a discriminator read from the VI binary, a value stored in a SQLite `TEXT` column) → **mix in that type** so the member equals the raw value drop-in: `IntEnum` (e.g. `ParsedWiringRule` — a `0–4` from the binary) or `class X(str, Enum)` (e.g. `TunnelMode`, `LVTypeKind`). Do **not** use `StrEnum` — it's 3.11+ and we target 3.10; `(str, Enum)` is the house idiom.
  - The value space is fully owned — members are passed around as typed objects and `.value` is read only for display/serialization, never compared to a raw literal → **bare `Enum`** (e.g. `Priority`, `LockState`). If you ever write `member == "literal"` against one of these, it's a bug (always `False`); fix the call site or change the base.
  - Never a **plain `Enum`** over values that are compared with `== <raw>` elsewhere — that silently breaks every such site.

## Output Directory

**ALWAYS use `outputs/` for generated code.** NEVER use `/tmp/` or any other temporary directory.

```bash
# CORRECT - outputs go to outputs/ folder in the repo
python scripts/generate_python.py "path/to/file.vi" -o outputs --search-path .lvkit/cache/samples/OpenG/extracted

# WRONG - never use /tmp/
python scripts/generate_python.py "path/to/file.vi" -o /tmp/test
```

The `outputs/` folder is in the repo and accessible from the editor. Temporary directories are not.

## Bash Commands

**NEVER use combined commands.** Always use single commands, one per Bash call.

Bad:
```bash
cd /tmp && python app.py
rm -rf /tmp/foo; python script.py
```

Good:
```bash
# First call
rm -rf /tmp/foo
# Second call
python script.py
```

This ensures permission patterns match correctly.

## Plan Execution Rules

**After a plan is approved, it is a contract.** If ANY aspect of execution differs from the approved plan — different approach, different file structure, different abstraction — you MUST:
1. STOP writing code immediately
2. Re-enter plan mode
3. Explain what you found that changes the approach
4. Get a new approval before continuing

NEVER silently change an approved plan. NEVER say "actually this is simpler" and keep going. The user approved a specific design. Changing it without discussion wastes hours.

## Planning Quality Rules

**During planning, READ the actual code before proposing changes.** Do not describe what you think the code looks like — read it. Specifically:
- If the plan says "convert class to function" — read the class first. Is it 10 lines or 400 lines with 15 methods?
- If the plan says "rename X to Y" — grep for X first. Is it in 5 files or 50?
- If the plan says "add field Z" — check if Z already exists elsewhere under a different name
- If the plan creates new types — check for existing types that do the same thing

**Run /design-review on proposed changes during planning.** Catch god objects, duplicate types, wrong naming, and code smells BEFORE the plan is approved, not after execution begins.

## Commit Rules

**NEVER commit broken, regressed, or non-working code.** Verify generation output is equal or better than the last working state before committing. If changes regress, fix the regression first. "Commit and fix later" is never acceptable.

## Release Rules

**Date the changelog BEFORE tagging — never after.** At the commit you tag, `CHANGELOG.md` must already have a dated `## [X.Y.Z] - <date>` section for the version (with a fresh empty `## [Unreleased]` reopened above it) — never the version's changes stranded under `[Unreleased]`. PyPI/the release consume the changelog and the published version is immutable, so dating it afterward does not fix the shipped release. Release order: (1) merge → (2) **date the changelog** → (3) bump the version sites (`pyproject` + `__init__` + `uv.lock` + `editors/vscode/package.json`) → (4) tag `vX.Y.Z` (PyPI) / `ext-vX.Y.Z` (bundles) → (5) the Cloud Run render service redeploys **automatically**. The render service **builds lvkit from repo source, not PyPI**, so a release is only fully shipped once the tagged source is redeployed — this is now the `deploy-render` job in `.github/workflows/publish.yml`, which fires on the `v*` tag (after the PyPI publish) and deploys via keyless Workload Identity Federation (no stored SA key). **So do NOT run `gcloud builds submit` by hand** on a normal release — the tag does it. Only deploy manually to recover from a failed CI deploy or to push un-tagged `main` source ahead of a release (`gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml ...` from the repo root — see `deploy/cloudrun/README.md`). Watch the `deploy-render` job on the tag to confirm it succeeded; a red deploy job means the render service is still on the old source.

## Temp Scripts

Never use multi-line inline `python3 -c` calls. Write scripts to `.tmp/` (gitignored) and run them.
