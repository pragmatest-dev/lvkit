---
name: eval-judge
description: Grade one lvkit MCP eval answer against its question and expected-answer notes on three axes — did it use lvkit, is it correct, did it fabricate anything. Returns a per-axis verdict with reasons and the likely MCP fix. For open-ended eval questions the pytest harness can't assert.
allowed-tools: Bash, Read, Grep
---

# Eval Judge

Grade ONE answer to an lvkit MCP eval question (bank: `docs/_internal/mcp-evals.md`).

Use this for **open-ended** questions the deterministic harness can't assert
(e.g. "what magic numbers are buried", "hardcoded creds", "naming
inconsistencies", "what's the public API"). Not this skill for:
- **exact facts** (class counts, histograms, `.lvproj` count) → the pytest
  harness `tests/test_mcp_evals.py` asserts those;
- **conversion / codegen** questions ("convert this VI — is it faithful?") →
  the `judge-output` skill (Python-vs-graph).

## Input

- The eval **question** (verbatim).
- The **answer** produced, plus the **tool-use trace** that produced it (which
  tools/commands were called). If no trace is supplied, judge from the answer's
  own account of how it was derived and say the evidence was indirect.
- That question's **Expected** and **Watch for** lines from
  `docs/_internal/mcp-evals.md` (the rubric / ground truth).

## The three axes

### 1. Used lvkit? — PASS / WARN / FAIL  (adoption)
- **PASS** — derived from lvkit (`query`/`describe`/`get_callers`/… MCP tools,
  or the `lvkit` CLI).
- **FAIL** — derived from `grep`/`cat`/`find`/ad-hoc `python` on the repo. A
  `.vi` is binary, so shell *cannot* actually read it — a right-looking answer
  from shell is luck, not signal, and still a fail.
- **WARN** — lvkit was used but padded with unnecessary shell.

### 2. Correct? — PASS / WARN / FAIL
Compare against the question's Expected notes.
- Exact-GT questions: PASS only on a match.
- Open-ended (no single answer): **PASS** if grounded in real lvkit output,
  internally consistent, and covering what Expected calls for; **WARN** for
  partial; **FAIL** for wrong or empty.

### 3. Fabrication? — NONE / MINOR / YES  (the critical axis)
Did the answer state anything it could not know from the data?
- **YES** — an invented parent, an invented project scope, a guessed *why* for a
  gap, a made-up count. This is the failure mode lvkit evals exist to catch.
- **NONE** — every claim is traceable to lvkit output, and unknowns are stated
  as unknown ("indexed but no class membership resolved — cause unknown")
  rather than rationalized into a plausible story.
- **MINOR** — a hedged aside that overreaches but is flagged as a guess.

Be a skeptic: when a claim's provenance is unclear, default to **YES** and say
what evidence would clear it.

## Report

```
Q<N>: <one-line question>
- Used lvkit?  PASS|WARN|FAIL — <reason, cite the tools/commands seen>
- Correct?     PASS|WARN|FAIL — <reason vs Expected>
- Fabrication? NONE|MINOR|YES — <the specific invented claim, or "none">
Verdict: <PASS only if Used=PASS, Correct∈{PASS,WARN}, Fabrication=NONE; else the weakest axis>
Likely MCP fix (if not PASS): <which view / tool / data gap — e.g. "#19 .lvproj membership", "#18 owns-edge">
```

Judging several answers: report each block, then rank worst-first. A flat
"all PASS" with no cited evidence is itself suspect — cite the trace.
