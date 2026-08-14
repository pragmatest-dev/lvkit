---
name: lvkit-eval
description: Run the lvkit MCP correctness/adoption eval loop — the automated regression harness, an adoption approximation, LLM-judged open-ended questions, and a scorecard — for iterating on the lvkit MCP.
allowed-tools: Bash, Read, Agent, Skill
---

# lvkit MCP Eval Loop

Question bank: `docs/_internal/mcp-evals.md` (24 questions, ground truth from
the JKI VI Tester corpus). Grades three axes per question — **Used lvkit? /
Correct? / Fabricated?**

Judging has four lanes, pick per question:

| Lane | Questions | Judge |
|---|---|---|
| (a) Deterministic facts | Q1, Q2, Q10, Q20 (count), Q22, Q24, gap #18/#19 | `tests/test_mcp_evals.py` |
| (b) Open-ended query/facts | Q3–9, 11–19, 21, 23 (magic numbers, naming, "public API", …) | `eval-judge` skill |
| (c) Conversion faithfulness | **cat. I (Q27–29)** — convert a VI, verify vs the oracle | `judge-output` skill + execute-both |
| (d) Other-skill behavior | **cat. J/K (Q30–32)** — `/lvkit-review`, `/lvkit-document` | `eval-judge` skill |

Categories A–H are the query/facts surface (lanes a/b). **Categories I–L
(Q27–33) are the skill-behavior evals for `convert`/`review`/`document`/
`resolve`** — behavior, not pinned facts, so judge them with the skill named in
the eval bank, not the pytest harness. Cat. L (Q33, `lvkit unresolved` coverage)
becomes lane (a) — pin it in the harness — once that command lands.

## Step 1 — Run the automated correctness harness

```bash
uv run pytest tests/test_mcp_evals.py -q -m slow -n0
```

`-m slow` is required — the full-corpus tests are excluded by default
(`pyproject.toml` addopts). `-n0` is **also required**: the default addopts run
`-n auto`, but these tests all share ONE on-disk index (built by the
module-scoped `jki_index` fixture into the real cache), so parallel workers race
the same SQLite DB → `OperationalError` + partial-read flakes. `-n0` overrides
`-n auto` to run serially (don't use `-p no:xdist` — that removes the plugin and
leaves `-n auto` an unrecognized arg).
This needs the local JKI-VI-Tester sample corpus
(`.lvkit/cache/samples/JKI-VI-Tester`); pull it with `scripts/pull_samples.sh`
if it's absent (tests auto-skip rather than fail).

Report per question: **pass** (baseline holds — no regression), **fail** (a
pinned baseline broke — a real regression, stop and fix before continuing),
or **xfail/XPASS** (a known gap — XPASS means the fix landed; update
`docs/_internal/mcp-evals.md`'s [GAP #N] tag AND the now-stale `xfail` in
`tests/test_mcp_evals.py` to a real assertion).

## Step 2 — Adoption approximation (optional)

Per the "sharpest discriminators" in the eval bank (questions 1, 10, 12, 17,
20): for each, spawn a **fresh** `general-purpose` subagent given ONLY the
repo path and the raw question text — do NOT hint at lvkit, MCP, or any tool
name. Inspect the agent's transcript/tool calls: did it reach for `lvkit`
(CLI or MCP tools) or fall back to `grep`/`cat`/`python` on the `.vi`
binaries? Record used-lvkit yes/no per question.

**Honest caveat — state this to the user, don't bury it:** a subagent here
only has shell/Bash access, so at best it exercises the `lvkit` **CLI**. It
never sees the MCP server's tool descriptions or system instructions the way
a real MCP client (e.g. Claude Desktop/Code with the lvkit MCP server
configured) does. This is an *approximation* of adoption, not the faithful
signal — the faithful signal is running the question bank manually against
a real MCP client and watching which tools it reaches for.

## Step 3 — Judge open-ended answers

For each open-ended question answered (by the adoption-approximation
subagents, or by a manual MCP-client run), invoke the **`eval-judge`** skill,
passing it:
- the question (verbatim from `docs/_internal/mcp-evals.md`),
- the answer + tool-use trace,
- that question's *Expected*/*Watch for* lines from the same file.

`eval-judge` returns Used-lvkit?/Correct?/Fabrication? verdicts plus the
likely MCP fix per question — don't re-derive that rubric here, just invoke
it.

Conversion questions (cat. I, Q27–29) are judged with `judge-output` (it
compares the port against the VI's graph — signature, control flow, SubVI
calls, parallelism) PLUS an execute-both behavioral-equivalence run: run the
hand-written port and `lvkit generate`'s oracle on the same inputs and diff the
results. Review/document questions (cat. J/K) use `eval-judge`.

## Step 4 — Synthesize the scorecard

Fill in the scorecard table at the bottom of `docs/_internal/mcp-evals.md`:

| # | Used lvkit? | Correct? | Fabricated? | Notes |
|---|:-:|:-:|:-:|---|

- Questions from Step 1 → PASS/FAIL from the harness (harness only proves
  *Correct?*; note *Used lvkit?*/*Fabricated?* as N/A — those are adoption
  properties of a live agent run, not something a pytest assertion checks).
- Questions from Steps 2–3 → the subagent's used-lvkit call + `eval-judge`'s
  three verdicts.
- List each non-PASS with the likely MCP fix (which view/tool/data gap —
  `eval-judge` already names one; for harness fails, name the file/line that
  regressed).

## Step 5 — The loop

Fix the identified gap → re-run Step 1 (and Steps 2–4 if the fix touches
adoption/open-ended behavior) → converge when the scorecard is clean and no
harness baseline moved unexpectedly.
