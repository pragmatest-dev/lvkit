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

This rule governs how lvkit is DEVELOPED (by the maintainer and agents). lvkit's USERS own LabVIEW and their VIs, so text lvkit shows a user (a gap message, a hint) may tell them to open a VI in LabVIEW; that is not a clean-room breach.

When stuck: do MORE of 1–4, or write a `"placeholder": true` primitive entry. A decode gap with "thin evidence" is never parked as a leftover: find more examples in the corpus and decode it. It ships as a gap only when the raw bytes prove it can't be decoded. Ask the maintainer to describe the *algorithm/domain* if needed — **never to inspect LabVIEW.** Also: ship ZERO NI-derived artwork (clean-room glyphs only).

## ⛔ VERIFY BEFORE CLAIMING — NEVER OFFLOAD THE CHECK TO THE MAINTAINER (READ THIS FIRST)

**Every factual statement about this codebase — architecture, behavior, an API, a line, a count, a timing, "X is faster/bigger than Y", "the code does Z" — MUST be backed by a receipt from THIS session: a `file:line` you read, or command/test output you ran, cited inline.** No exceptions.

- **Verify BEFORE you state it, not when challenged.** A claim reaches the maintainer only after you've already checked it. The maintainer must NEVER be the one who catches a wrong claim — if they have to adversarially re-check you, you have already failed.
- **Never infer-then-assert.** Reasoning from memory, from a partial read, or from "this is probably how it works" is the exact failure mode. Grepping one thing and inferring a conclusion is NOT verification — read the whole relevant path end to end.
- **If you have not verified it, do not say it.** Say "I haven't checked — checking now" and go check, or say "I don't know." A plausible guess stated as fact is worse than "I don't know."
- **Memory NEVER holds code facts** (mechanisms, line numbers, counts, timings) — they go stale and become confabulation. Memory holds only how-we-work, hard constraints, LabVIEW-format facts, and pointers. Re-read code facts from the source every time.
- **A quoted rule is read in its whole section.** Before saying "by design" or "the recorded rule says", read the entire section the quoted lines sit in and state what that rule governs; a subagent's quote is not that read.
- **Plans are claims too.** Every premise in a plan is cited (`file:line`) or measured before it's written — do not build on an unverified premise.
- **NEVER answer a behavioral failure with a verbal promise alone.** "I'll do better" / "I'll hold to that" evaporates on the next compaction or clear. Any behavioral correction MUST be written to disk **in the same turn** — the durable instruction surfaces (this `CLAUDE.md` and the memory files, belt-and-suspenders) — or it does not count. A commitment that isn't written did not happen.

This is not "try harder." It is a hard gate: unverified → unsaid; unwritten commitment → didn't happen.

## ⛔ ARCHITECTURE LIVES IN `ARCHITECTURE.md` — READ IT BEFORE ANY ARCHITECTURAL CLAIM (READ THIS FIRST)

**All architecture — the one progressively-built graph that is the substrate for EVERY command, the pipeline, how each view (render/describe/lvnet/diff/codegen) PROJECTS from that graph, and the CLI/desktop/web frontends — lives in `ARCHITECTURE.md` at the repo root.** It is the canonical reference, every claim cited to `file:line`.

- **Before making ANY architectural claim, or proposing ANY solution that touches the graph, the pipeline, or a VI view, READ `ARCHITECTURE.md` first and cite it.** Do not reason about the tool's shape from memory — memory is the part that's unreliable. Unverified architecture → unsaid (same hard gate as VERIFY BEFORE CLAIMING). Do NOT flail or guess out loud on architecture (this session's web-`viText` failure — proposing lvnet through the SVG renderer, then questioning whether graphs are even built — is exactly the banned behavior).
- **Memory (`MEMORY.md` and `memory/*`) is BEHAVIORS ONLY** — how-we-work, feedback, hard constraints. NEVER put architecture/code facts in memory (they rot and it bloats the index). When you get architecture wrong or learn a new architecture fact, **ADD/CORRECT it in `ARCHITECTURE.md` in the same turn** — never a new memory file. Keep `ARCHITECTURE.md` filled with what you keep forgetting.

## ⛔ BUGS — INVESTIGATE ONLY; THE MAINTAINER DECIDES THE FIX (READ THIS FIRST)

**For any bug, independent decision-making on the FIX is revoked.** The workflow is: **investigate → present findings → the maintainer decides → only then implement, exactly as agreed.** Do NOT write code — not a "small" fix, not an "obvious" one, not a docstring — until the maintainer has explicitly approved the approach.

- **Never ship a hard-coded / string-matched / heuristic fix.** If a fix keys off a string label (`flavor.endswith("Waveform")`, a literal `"Digitaldata"`) or a hand-written index→name table, that is precisely the kind of fix that is NOT trusted here — surface it as a finding, flag it as suspect, and let the maintainer direct a generic, data-driven solution.
- **Investigation means:** parse/grep/read to find the real root cause and where the correct data actually lives in the pipeline. Report that. Then STOP and wait.
- This is a hard gate, same standing as VERIFY BEFORE CLAIMING. Persisted in memory: `feedback_bugs_investigate_then_discuss`.
- **Inside an APPROVED task, "done" means it ALL works — no trail of follow-on fixes.** A bug you hit while finishing an approved task (a regression it exposes, a failing check, a broken round-trip, a stale test) is part of that task: find the root cause AND fix it generically (the no-hard-coded rule above still applies), then report the finished result ONCE. Never end with "fixed X, found Y, didn't fix it". Ask only for a genuine design fork, once, with a recommendation. The investigate-then-wait gate above is for a standalone bug report. **Bugs you find yourself — related to the task or not — you FIX (generically), you don't deliver them:** no "found Y, want me to file it?", no self-filed issues, no leftover-bug lists ending a report. The goal is a working library; the open-issue count must go down, never up from your own findings. Only a genuine design fork goes to the maintainer. Persisted in memory: `feedback_done_means_no_trail`.
- **A bug our own change introduced is NEVER deferred.** If a PR's change is wrong anywhere (a reviewer's finding, a platform it breaks on, a case it mis-handles), it is fixed in that PR before merge — never "logged as its own change", never "outside this PR", never answered with a task. Deliberately deferring is allowed only for a bug that already existed before the change, and only with the maintainer's explicit decision. (Maintainer, 2026-10-29: "introducing a bug in a change and leaving it? No. We don't do that.")
- **An approved OUTLINE is not an approved MECHANISM — nothing lands on `main` unseen.** Before any merge or push to `main`, the maintainer has seen, in plain words, the concrete mechanism of every change in it AND every extra fix or deviation (a fix found along the way, a design that differs from what you described, anything you wrote yourself). Fix found bugs in a branch, verify, then present the finished fix (what / how / evidence) and merge on their yes. Never merge first and explain after. Persisted in memory: `feedback_approve_mechanism_not_outline`.
- **Few larger PRs, grouped by theme.** Related fixes and follow-ons are pushed onto the open PR's branch of the same theme (its title and description updated, its suites re-run), never one PR per fix (maintainer, 2026-11-11: "I don't love 1 million PRs"). Persisted in memory: `feedback_fewer_larger_prs`.
- **24-HOUR HOLD, ONLY FOR A PR THAT ADDRESSES A USER-FILED ISSUE: it stays open at least 24 hours after it is opened (and after any new push the user should see) before it is merged**, so the user who filed the issue can test it and their findings are fixed IN that PR. A PR not tied to a user-filed issue (our own fixes, features, docs, cleanup) has NO hold: once it is reviewed, its suites pass and the maintainer has seen its mechanism, ask to merge (maintainer, 2026-11-11: "For the PRs unrelated to issues, I don't really want 24 hour holds. I want them on user-filed issues so that the users have a chance to test"). Merging still needs the maintainer's yes (approved mechanism, above). For a held PR, never ask to merge sooner; report it as "open, waiting for review", then after the hold read every comment on it before asking. A merge-ready PR younger than 24 h is not merge-ready. Only the maintainer can waive the hold, explicitly, for one named PR; a waiver never carries over to the next PR. Persisted in memory: `feedback_prs_wait_a_day_for_review`.

## ⛔ NEW INSTRUCTIONS EXTEND RECORDED DESIGN — THEY NEVER SILENTLY REPLACE IT (READ THIS FIRST)

A new instruction from the maintainer is fitted INTO the design already recorded (this file, `ARCHITECTURE.md`, `docs/_internal/design/*`, the code), never written over it.

- **Before editing this file, `ARCHITECTURE.md` or a design doc, or sending a brief that changes existing behaviour:** grep those docs and `git log` for the area, quote each existing decision it touches, and state for each: kept / extended / replaced. Diff the edited doc against `git show HEAD:<file>` and read every removed line.
- **Replacing a recorded design needs the maintainer's explicit yes for that design by name.** A new instruction that seems to conflict with one is first reconciled; if they truly conflict, ask once, quoting both.
- **Every design note starts with "What LabVIEW represents"**: who owns the construct, how long it lives, and how LabVIEW code reaches it (which wire or reference), cited; each LabVIEW access path maps to exactly one Python path, and any global, registry or injected object with no LabVIEW counterpart is removed (2026-11-11: "why would subVIs have access to another VI's front panel automatically? Did you consider what we're … representing?"). Persisted in memory: `feedback_model_labview_first`.
- **Every design note and every agent brief carries a "Recorded design this touches" section**, written before it is sent: from a grep of this file, `ARCHITECTURE.md` and `docs/_internal/design/*` for the area's terms, every recorded decision and existing mechanism in the area, each marked kept / extended / replaced with `file:line`. Anything that parallels an existing mechanism (a second holder beside `LiveIO`, an attribute beside the label maps, a scan beside the catalog's `"requires"`) is named as such and justified, or dropped. The diff review checks the list against the diff. **An independent design audit runs** (`.claude/agents/audit-design.md`) on every design note before it goes to the maintainer and on every PR branch before it is opened or updated; its findings are fixed or answered in the PR. (2026-11-11, maintainer: "Who is running an audit on the designs? No one?"; `FrontPanel` was built beside `live_io` without reading §11 — "Remember how we got free expansion from the io class? … what other designs did you fail to check before implementing??")
- Persisted in memory: `feedback_cite_design_decisions_dont_ask`.

## ⛔ GENERATED PYTHON IS NATIVE PYTHON — NO LABVIEW IN IT (READ THIS FIRST)

The output of `generate` is a Python port, not LabVIEW running in Python. **It is a one-way conversion:** the generated code will never run with LabVIEW or lvkit again, so it is standalone Python. **The generator transliterates.** Each LabVIEW construct maps to Python by one simple, uniform rule that preserves its behaviour exactly: a VI is a multi-output function returning one result object read by name; error wires are values; the connector pane is the signature. Idiomatic Python (single-value returns, exceptions, dropped plumbing) is a later conversion from that base, never a guess the generator makes (maintainer, 2026-11-01). **Every connection is made at generation time and the generated code uses Python's own constructs:** a VI is a function; a subVI call or VI reference is an `import` and a direct call with keyword arguments; Open VI Reference resolved at generation time is an `import`; asynchronous calls are `concurrent.futures` when the caller collects the result, and a call started and forgotten (or Run VI without waiting) is a `threading.Thread(..., daemon=True)`, as the target VI in LabVIEW runs to its end on its own and does not keep the program from exiting (maintainer, 2026-11-11); VI properties are reflection (`ref.__name__`, `inspect.getfile(ref)`); a LabVIEW class is a plain class; a queue is `queue.Queue`; array math is comprehensions/indexing/`len`/slices; flatten and Type Cast are `struct` calls with the types written out, except that a Type Cast between a reference and an integer passes the Python object itself through (code that keeps a reference in a U32 gets the same object back; `0` cast to a reference is `None`; no table of handles — maintainer, 2026-11-10); a constant pattern is an `re` pattern; errors are values on the wire, `error_in` in and `error_out` out (`docs/_internal/design/errors-as-values.md`, approved 2026-11-01; "Error Handling Strategy" below); a call into a DLL is `ctypes` code written out in the module, every parameter packed into the C type the DLL takes the way Python does it (a LabVIEW string/array handle or Adapt to Type cluster is just a C layout: a `ctypes.Structure` and pointers, defined in the generated module); a call to LabVIEW's own functions (`LabVIEW:` FGetInfo …) is the Python equivalent (`os.stat` …).

- **The generated package mirrors the project's folder hierarchy.** A VI's module path is its project-relative file path (each folder a package, the file stem the module, one fixed string transform). A file is placed from the path LabVIEW stores for it: a library file, inside the project or not, by its pseudo path (`<userlib>/…` is the package `userlib/…`), and a file outside the project and every library folder by `external/<its own project folder>/…`, so the output folder can be copied anywhere. Paths are unique, so modules are unique: no flat output, no collision numbering. Generated code that computes a VI path at run time imports it with `importlib` by the same transform.
- **A timestamp is a UTC `hightime.datetime`** (NI's `hightime`, what `nidaqmx` returns), exact to LabVIEW's 2^-64 s (maintainer, 2026-11-11): LabVIEW's I64 whole seconds since 1904-01-01 UTC plus U64 fraction of 2^-64 s (NI, "How LabVIEW Stores Data in Memory") decode to `hightime.datetime(...)` with `femtosecond`/`yoctosecond`, the way `nidaqmx` converts its `AbsoluteTime`. The default of the type is 1904-01-01 00:00:00 UTC (an immutable value, so also a `def` default); Get Date/Time In Seconds and a file's last-modified time are `hightime.datetime` in UTC; Subtract of two timestamps is `(a - b).total_seconds()`, a timestamp plus or minus a number adds or subtracts `hightime.timedelta(seconds=n)`; To Double Precision Float of a timestamp is the seconds since 1904 and To Time Stamp the reverse (`codegen/timestamp_templates.py`, `timestamp_value.py`).
- **Generated code imports nothing from `lvkit`.** No `lvkit.runtime`, no `lvkit.labview_error`, no `LabVIEWObject`, no `labview_queue`, no registry or table that re-implements LabVIEW at run time. A count of `lvkit` imports in the generated sample corpus only ever goes down.
- **Where Python's standard library has no equivalent, generated code may import a standard third-party package** (maintainer, 2026-11-08): TDMS files are `npTDMS`; NI drivers are NI's own `nidaqmx` / `nifgen` / `nidigital`; a timestamp is NI's `hightime` (below). The catalog entry that needs one names it (`"requires"`), and the generated package lists every such package once, sorted, in `requirements.txt` at its root (no file when there are none); `hightime`, which generation writes into a module without a catalog entry (a default, an annotation, a constant), is listed because the written module imports it (`PackageRequirements.add_imported`). **Prefer a Python-native equivalent where LabVIEW's exact bytes are not expected to persist** ("we can insert things like pkl or things that are Python equivalents. Especially where there's no expectation of persistence and can be just run-time Python-native"): a value whose type is known at generation time keeps LabVIEW's flattened format, and one known only at run time is flattened with `pickle`. Text files are opened with `encoding="utf-8"` and hold `str`; flattened or binary data goes through binary file I/O. A variant is the Python value itself; LabVIEW's "variant as lookup table" (an empty variant given attributes) is a `dict` of the attributes, decided at generation time. Approved helpers generated once into the package beside `errors.py`: `notifiers.py` (a `Notifier` on `threading.Condition`: Python has no "latest value, wake every waiter, wait for the next" construct); `panels.py` (`PanelValues`, the control and indicator values a VI's front panel keeps between calls, and `Panel`, which adds the VI's run state, Abort, Pause and events, with its attached views: Python has no front-panel construct; approved 2026-11-11).
- A VI reference whose path is computed only at run time resolves against the project's VIs, listed at generation time: a plain dict in the generated module of the VIs matching the reference's connector pane, keyed by project-relative path. If the project has the VIs it works; if it has none, that node is a gap (below). What has no Python meaning is never emulated.
- **ONE rule for every gap, across the whole generation process: warn at generation time, raise at run time.** A gap is anything generation cannot convert (unknown primitive or vi.lib VI, placeholder primitive, a property with no Python meaning, a library file not found, a VI reference with nothing to resolve to, …). Every gap goes through one mechanism: generation prints a warning naming the VI, the node and the reason (`lvkit unresolved` lists the same warnings), and the generated code raises `NotImplementedError("<reason>")` where the node would run, its outputs bound to `None`; a gap outside any node makes the VI's function body that `raise`. A run with gaps is a PARTIAL success and never looks like a full one: `generate` says so in its summary and exits with its own nonzero code (distinct from a stopped run), and every warning and every raise message carries enough detail (VI, node, kind, what was needed) for a person or an AI to finish the conversion. No per-site variant, no lvkit import, and no flag that changes what a gap generates; the one run option is `--fail-fast`, which stops the run at the first gap's warning (exit 1, the output is incomplete) for people who don't want to wait for a long run. Generation stops only for what makes the package itself unwritable (two files mapping to one module, a VI with no file and no recorded path) and for lvkit's own internal checks (a bug in lvkit, not a gap in the VI).
- **Exit codes (every command):** 0 complete, 1 stopped or an lvkit error, 2 usage error (a bad flag, a missing input path, or an input that is not a VI), 3 partial (output written, with problems or gaps).
- **A general converter, never sample-specific code.** A sample (JKI VI Tester, a "zero gaps" target) shows where a construct is used, never how to convert it. Each node gets the general LabVIEW behaviour for every input of its kind, or a gap; never an encoding or code path shaped to what one sample's VIs parse or check (maintainer, 2026-11-11: "We're making a general converter, not a JKI-only converter").
- A brief or plan that adds a runtime helper for generated code must name why Python has no construct for it, and the maintainer approves it first. Any agent report that "differs from the plan" is stopped and brought to the maintainer.

## ⛔ ONE RULE FOR PROBLEMS, IN EVERY COMMAND AND MCP TOOL (READ THIS FIRST)

Every command (`index`, `render`, `docs`, `describe`, `generate`, `unresolved`, `callers`/`callees`/`blast-radius`, `query`, `diff`, …) and every MCP tool reports problems the same way (maintainer's decision, 2026-10-28):

- **A problem with one VI never stops the run.** A VI that can't be extracted, parsed or built, or that fails in the view, is recorded and the command continues; the VI is shown as unreadable wherever it is referenced (for `generate`, a call to it is a gap).
- **One record type and one reporter** (`lvkit/run_report.py`): each problem prints one warning line to stderr (VI path, stage, reason); pylabview's block warnings are captured into that VI's problem, not dumped raw; `generate`'s gaps go through the same reporter.
- **One summary:** Complete / Partial / Stopped, with the FULL list, never cut off; `--format json` gives the same list.
- **One set of exit codes** (above: 0 / 1 / 2 / 3), and `--fail-fast` on every command that processes many VIs.
- **An empty answer says why:** an unknown VI is an error ("not found in the index", exit 1); a found VI with no results gives an empty result and a note on stderr.
- **MCP tools** return their result plus the same problem list; a whole call fails only for bad arguments.
- **An lvkit bug in one VI** is recorded as an lvkit error (with a hint to report it); the run continues and exits 1.
- **A path is a field in the graph, written the way LabVIEW stores it.** LabVIEW stores each path relative to the file that records it, or as a pseudo path (`<vilib>/…`, `<userlib>/…`, `<instrlib>/…`) so code moves between computers and LabVIEW versions. lvkit resolves both when a command runs, against the calling file and the configured library folders.
  - Every view writes a dependency's path in that stored form (lvnet, describe, JSON, MCP results, warnings, `unresolved`, `callers`/`callees`), and the CLI and MCP accept it back as input. A file no caller records (the VI a command was run on) is named relative to the project.
  - A dependency lvkit did not find keeps its stored path. That it was not found is a problem in the one report (and lvnet verbose's `(missing)` status), never part of the path.
  - Never the extraction cache: a file extracted from an `.llb` is named by its archive.
  - The one exception is a problem about the cache itself (it can't be written), which names the cache folder as `~/…` so the user can fix it.

## ⛔ NO JARGON — SAY IT IN LABVIEW'S WORDS (READ THIS FIRST)

In replies to the maintainer AND in anything lvkit shows a user (CLI text, `--format json` values, MCP results, viewer labels), use the LabVIEW term: "shift register", "loop output tunnel", "case output tunnel", "connector pane", "Bundle/Unbundle By Name", "In Place Element". Never compiler/Gated-SSA/heap jargon (gamma, mu, eta, decompose, nMux, dco, VCTP) or an internal class name as the explanation — an identifier appears only in backticks as a code pointer. Scan every reply for it before sending. Persisted in memory: `feedback_no_jargon`.

## ⛔ NEVER REFERENCE A REMOVED/OLD DESIGN IN CODE (READ THIS FIRST)

When we remove or replace something, **strip every mention of the old thing from code, docstrings, and comments** — describe what the code does NOW, on its own terms. NO "graph-native mirror of the removed `Operation.X`", NO "byte-identical to the old builder", NO "used to be `inner_nodes`", NO "replaces the old `build_netlist`", NO "Phase 2 swaps that". **No one knows the old design; git history is the only record and anyone who cares reads it there.** This includes references *I* author during the same refactor — those are the exact violation. Doing this cleanup is part of FINISHING the refactor, never an optional follow-up. Keep only CURRENT identifiers that happen to share a word (a surviving `OperationsMixin`), never a docstring that *explains* current code via the deleted thing. Persisted in memory: `feedback_no_old_design_references`.

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
- `src/lvkit/graph/models.py` — graph/codegen-only types (`GraphNode` hierarchy, `VIContext`, `Wire`, query/info types)
- `src/lvkit/codegen/builder.py` — `build_module()` entry point for AST generation
- `src/lvkit/pipeline.py` — orchestrates multi-VI generation
- `src/lvkit/cli.py` — command-line interface
- `src/lvkit/mcp/` — MCP server (9 tools)

### Standard Test Command

```bash
# Single VI
python scripts/generate_python.py "path/to/file.vi" -o outputs --userlib ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib"

# LabVIEW class (.lvclass)
python scripts/generate_python.py "path/to/MyClass.lvclass" -o outputs --userlib ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib"

# LabVIEW library (.lvlib)
python scripts/generate_python.py "path/to/MyLib.lvlib" -o outputs --userlib ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib"

# Directory of VIs
python scripts/generate_python.py "path/to/vi_folder/" -o outputs --userlib ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib"
```

## Error Handling Strategy

**Errors are values on the wire, as in LabVIEW** (`docs/_internal/design/errors-as-values.md`, approved 2026-11-01/02). An error cluster is `None` (no error) or a `LabVIEWError` (`status`, `code`, `source`, `message`), generated once into the package (`errors.py`) and imported from there; generated code imports nothing from lvkit.

- A VI with error terminals takes `error_in` and returns its result object with `error_out`. A call passes the error in and the called function decides.
- A node is skipped when an error occurred on its error in: `if err is None or not err.status:`. A warning (status False, code set) does not skip. Its outputs then take their defaults and its error out is the incoming error (`codegen/error_flow.py`, `guard`). A node NI says runs despite an error runs always, and its error out is the incoming error, else its own (first error wins).
- A native Python failure becomes the node's error out with the code its catalog entry names, else NI's generic code 42 (`codegen/native_failure.py`). With no error out wired, it stays a Python exception.
- Error case structures, Merge Errors, Clear Errors, Simple Error Handler, Bundle / Unbundle of an error and a loop stopped on an error all read and write the value. Parallel branches are plain sequential code with their own error variables.
- Decisions and their sources (Call By Reference's own error terminals, an error cluster wired to a Boolean input, accessors, indicators kept between calls, …): the "Decisions" list in the design doc.

## Searching for Code in VIs

A `.vi` is a binary — you cannot grep it directly. Finding a construct (a
`primResID`, a node class, a structure) is **two steps**:

1. **Extract the XML** with pylabview. `lvkit.extractor.extract_vi_xml(vi_path, roots=LibraryRoots())`
   runs pylabview **in-process** and caches `*_BDHb.xml` (block diagram) +
   `*_FPHb.xml` (front panel) in a per-user cache **outside the repo**
   (`extractor.global_cache_root()` — `$LVKIT_CACHE_DIR`, else `~/.lvkit/cache/`),
   keyed by the resolved source root + content hash, so re-runs are no-ops. Use
   `resolve_extracted(vi_path, roots=LibraryRoots())` to find the cached XML; many corpus VIs are
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

LabVIEW primitives are identified by `primResID`. When `lvkit generate` warns about an unknown primitive (`lvkit unresolved` prints its diagnostic), add an entry to `src/lvkit/data/primitives.json`:

```json
{
  "1234": {
    "name": "My Primitive",
    "python_code": "in_1 + in_2",
    "terminals": [
      {"index": 1, "direction": "input", "name": "x", "type": "NumFloat64"},
      {"index": 2, "direction": "input", "name": "y", "type": "NumFloat64"},
      {"index": 0, "direction": "output", "name": "x+y", "python_name": "sum_of_x_and_y", "type": "NumFloat64"}
    ]
  }
}
```

A terminal's `name` is NI's label as LabVIEW shows it, taken from NI's public page (`error in (no error)`, `x+y`), not a Python identifier. Its Python name is derived from the label (a default parenthetical is dropped); give a terminal a `"python_name"` only when the derived name is a poor one for generated code (`x+y` derives `xy`). A dict `python_code` is keyed by the output Python names. Use the caller's dataflow in the exception output to determine correct terminal indices — do not guess.

## Adding New VILib VIs

When `lvkit generate` warns about an unmapped vi.lib VI, add the VI to the appropriate `src/lvkit/data/vilib/<category>.json`. `lvkit unresolved` prints the terminal names from XML and the actual wire indices from the caller — use those indices to fill in the `"index"` field for each terminal.

**Workflow:**
1. Run the code generator; note the exception output
2. Match "Wire types from dataflow" indices to the terminal names listed
3. Add entries to the vilib JSON with the correct `"index"` values
4. Re-run to verify

## VILib Terminal Resolution Workflow

When the code generator encounters a vilib VI with missing terminal indices, it records a gap (a `VILibResolutionNeeded`, shown by `lvkit unresolved`) and the generated code raises `NotImplementedError` there. This is intentional - use the caller's dataflow info to fill in the missing indices.

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

**The full coding-style rules live in `docs/_internal/design/code-style.md`** — read and follow them when writing lvkit code (KISS/DRY, no god-modules, one decomposed class hierarchy per variant, types-over-dicts, module-level imports, no inline `python -c`, qualified type names, deterministic/sorted order at every order-materializing site, no string-matching (use graph indices), single-job fields, unknown-node = error not silent warning, no heuristic guessing). The essentials:

- Python 3.11+ required
- Ruff for linting (rules: E, F, I, UP)
- mypy with strict mode for type checking
- Line length: 88 characters
- **Prefer dataclasses over dicts** - Use typed dataclasses from `models.py` or `graph/models.py` instead of raw dictionaries. Use attribute access (`obj.field`) not `.get("field")`
- **Enums, and how to pick a base** - A closed, named value set is an `Enum` — never a bare-constants namespace class (`class X: A = 0; B = 1`) or a loose module-level group. Choose the base by how the value lives **at rest** and whether raw scalars still flow through the code:
  - Raw `int`/`str` values are compared with `==`/`in` or built positionally at many sites (a discriminator read from the VI binary, a value stored in a SQLite `TEXT` column) → a closed `str` value set is a **`StrEnum`** (e.g. `TunnelMode`, `LVTypeKind`) and a closed `int` one an **`IntEnum`** (e.g. `ParsedWiringRule` — a `0–4` from the binary), so the member equals the raw value drop-in and prints as it (`str(member)` and an f-string give the value). Never spell it `class X(str, Enum)`: that prints `X.MEMBER`.
  - The value space is fully owned — members are passed around as typed objects and `.value` is read only for display/serialization, never compared to a raw literal → **bare `Enum`** (e.g. `Priority`, `LockState`). If you ever write `member == "literal"` against one of these, it's a bug (always `False`); fix the call site or change the base.
  - Never a **plain `Enum`** over values that are compared with `== <raw>` elsewhere — that silently breaks every such site.

## Output Directory

**ALWAYS use `outputs/` for generated code.** NEVER use `/tmp/` or any other temporary directory.

```bash
# CORRECT - outputs go to outputs/ folder in the repo
python scripts/generate_python.py "path/to/file.vi" -o outputs --userlib ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib"

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

## ⛔ ARCHITECTURE UP FRONT, QUALITY REVIEW PER COMMIT (READ THIS FIRST)

Working code is not done code. Two gates, for every change, including work handed to a subagent:

1. **Before any code: a placement plan of a few lines.** Name the module each piece goes in, its new types/functions, and which existing seam it extends (read `ARCHITECTURE.md` and `docs/_internal/design/code-style.md`). New concern → its own module, never another few hundred lines in an already-large file (`parser/vi.py`, `graph/netlist_build.py`, `render/nodes.py` are the usual suspects). Check for an existing type or helper that already does it. A subagent brief carries this plan and says HOW to implement it: the functions to change (`file:line`), the existing helper to call, real example data, the steps, each edge case with its expected result, and the tests with their expected values, so the agent never re-does research already done. Implementation goes to Sonnet agents; Opus agents only rarely, when the maintainer agrees. A new task gets a fresh agent, never a resumed huge one. An agent that has to put code somewhere the plan didn't name stops and says so instead of picking a spot. When several issues in one batch touch the same code, name the shared piece and settle it BEFORE the parallel branches start. `/design-review` is only for a brand-new subsystem, not per bug fix.
2. **Every commit: I review the diff myself.** I am the supervisor and have the context. I read every logic change an agent makes against its brief, the design and the edge cases, and run the targeted tests, before I report it or build on it. A Sonnet review agent may add a pass for mechanical diffs (data, changelog, renames, test-only); an Opus review agent only when the maintainer agrees. Checklist: god-module growth, duplication of an existing mechanism, simplest form, single-job functions and fields, code-style rules. Findings get fixed in that commit.

Keep it cheap: mechanical checks (file size, complexity) belong in the pre-commit hook, not in a model review. No full-source review rounds at the end of a batch; the merge only checks overlaps between branches.

## Commit Rules

**Test each feature case end to end on a tiny input.** For every case a change handles, write a minimal block-diagram XML for just that construct (`tmp_path / "X_BDHb.xml"`, as `tests/test_disable_graph.py` does), run it through parser → graph → every view the feature shows in (describe text, lvnet, JSON, the rendered diagram, and `generate`), and assert the exact text each view prints for it. A node is not supported, and its PR is not ready, until all of those work: the render is checked by screenshotting real corpus VIs and comparing them with NI's connector-pane image and the XML's terminal bounds, and `generate` produces Python that runs (a loud "no code generation yet" failure is not support). Cover the neighbouring cases the change did NOT mean to touch, too. A change to a node's terminals asserts the node's whole terminal list.

**Before every PR: a targeted before/after diff of every view, read line by line, in minutes.** Pick the VIs, classes and typedefs the changed code can reach (by the construct it handles) plus the tester's examples, run describe, lvnet (terse and `-v`), JSON and generate on them at the base and the head, and diff. Every changed line gets a cause; every information-losing line (a typedef or class name replaced by a bare type, a type turning `?` or less specific, a field, terminal or default disappearing, a count dropping) is fixed or explained. A whole-corpus run is never the per-PR check (maintainer, 2026-11-01: "CANNOT TAKE ONE HOUR TO CHECK OUR CHANGES").

**Test runs: the hook on every commit, the full suite once per PR.** The pre-commit hook runs lint, pyright and every test that does not read the sample corpus. Run the full suite, corpus included (`uv run pytest -q` and `uv run pytest -q -m slow -n 2` with the corpus pulled), once per PR before merge, and report its result in the PR.

**NEVER commit broken, regressed, or non-working code.** Verify generation output is equal or better than the last working state before committing. If changes regress, fix the regression first. "Commit and fix later" is never acceptable.

**`git commit --no-verify` is BANNED — never bypass the pre-commit hook.** The hook is the commit gate. If it fails — even on something that looks unrelated or environmental (e.g. the `tests/test_mcp_roots.py` cwd failures when committing from a nested `.claude/worktrees/...` worktree) — STOP: resolve the real cause, commit from an environment where the hook legitimately passes (e.g. the repo root instead of a nested worktree), or ask. "I already ran the suite / only unrelated failures" is never a license to bypass.

## Release Rules

**Date the changelog BEFORE tagging — never after.** At the commit you tag, `CHANGELOG.md` must already have a dated `## [X.Y.Z] - <date>` section for the version (with a fresh empty `## [Unreleased]` reopened above it) — never the version's changes stranded under `[Unreleased]`. PyPI/the release consume the changelog and the published version is immutable, so dating it afterward does not fix the shipped release. Release order: (1) merge → (2) **date the changelog** → (3) bump the version sites — **FIVE** of them: `pyproject` + `__init__` + `uv.lock` + `editors/vscode/package.json` + **`editors/vscode/web/extension.js`'s `LVKIT_WHEEL = "lvkit==X.Y.Z"`** (the web extension installs this exact wheel from PyPI; its version is DECOUPLED from the extension version, so a CORE release must bump it too). `ci.yml`'s `lint` step "Web lvkit wheel pin matches core version" fails the build if `LVKIT_WHEEL` != `pyproject`. **Confirm the `main`-push CI is GREEN before pushing any `v*`/`ext-v*` tag** — `publish.yml` (tag `v*`) and `ci.yml` (push `main`) are INDEPENDENT workflows with NO CI→publish gate, so a red CI does NOT stop the immutable PyPI publish (learned the hard way on 0.8.0) → (4) tag+push `vX.Y.Z` (PyPI) **FIRST, then WAIT for its `Publish to PyPI` job to SUCCEED**, and ONLY THEN tag+push `ext-vX.Y.Z` (bundles). **Never push both tags together** — the bundles build compiles the WEB extension by `pip install lvkit==X.Y.Z` FROM PyPI, so `ext-v*` DEPENDS on the `v*` publish; running them in parallel races the two workflows and the web build fails (or grabs a stale wheel) → (5) the Cloud Run render service redeploys **automatically**. The render service **builds lvkit from repo source, not PyPI**, so a release is only fully shipped once the tagged source is redeployed — this is now the `deploy-render` job in `.github/workflows/publish.yml`, which fires on the `v*` tag (after the PyPI publish) and deploys via keyless Workload Identity Federation (no stored SA key). **So do NOT run `gcloud builds submit` by hand** on a normal release — the tag does it. Only deploy manually to recover from a failed CI deploy or to push un-tagged `main` source ahead of a release (`gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml ...` from the repo root — see `deploy/cloudrun/README.md`). Watch the `deploy-render` job on the tag to confirm it succeeded; a red deploy job means the render service is still on the old source.

## Temp Scripts

Never use multi-line inline `python3 -c` calls. Write scripts to `.tmp/` (gitignored) and run them.
