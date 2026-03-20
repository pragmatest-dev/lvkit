---
description: Iterative design review of a subsystem. Audits for dead code, logical inconsistencies, duplication, separation of concerns, consistent patterns, and pythonic style. Repeats until clean.
argument-hint: "<file or directory path>"
---

# Design Review Skill

Run an iterative design review on the file or directory specified by `$ARGUMENTS`.

## Procedure

### Phase 1: Load scope and plan context

1. Resolve `$ARGUMENTS` to a list of Python files. If it's a directory, include all `.py` files recursively.
2. Check for an active plan file at the path shown in any `plan mode` system messages, or look for recent specs in `agent-os/specs/`. If a plan exists, read it — findings in Phase 2 will be checked against plan objectives.

### Phase 2: Audit

Spawn an Explore agent with subagent_type=Explore. Give it the full file list from the scope. The agent prompt MUST include ALL of these review criteria:

1. **Dead code** — unused functions, unreachable branches, vestigial imports, unused model fields, exports with no consumers
2. **Logical inconsistencies** — contradictory logic, redundant checks, inconsistent error handling across similar functions
3. **Pythonic violations** — non-idiomatic patterns, bare `except Exception`, repeated local imports, overly imperative code where comprehensions/builtins would be clearer
4. **Reuse & consolidation** — duplicated logic, copy-paste patterns, near-identical functions that should share a helper or be parameterized
5. **Separation of concerns** — business logic in wrong layer, god functions mixing multiple responsibilities, models containing service logic
6. **Consistent patterns** — do similar operations follow the same structure? Are naming conventions, signatures, error handling, and return shapes uniform?
7. **Plan adherence** — if a plan/spec was found in Phase 1, check: does the code match the plan's stated storage layout, event types, interfaces, and architecture? Flag any deviations, stale references to removed concepts, or unimplemented plan items that should already be done.

The agent MUST:
- Read every file in scope thoroughly (not just grep)
- Report specific findings with `file_path:line_number` references
- NOT say "looks clean" without evidence — dig for issues
- For each finding, propose a concrete fix (what to change and how)

### Phase 3: Present findings and get user approval

Present the agent's findings as a **numbered table** with these columns:

| # | Category | Location | Issue | Proposed Fix |
|---|----------|----------|-------|--------------|
| 1 | `[dead code]` | `file.py:42` | One-sentence description | Concrete fix description |

Categories: `[dead code]`, `[duplication]`, `[consistency]`, `[pythonic]`, `[separation]`, `[logical]`, `[plan adherence]`

**MANDATORY: You MUST ask the user which findings to fix. NEVER decide on your own which to fix or skip.** Wait for the user to respond (they may say "all", list numbers like "1-5, 7", or skip some). Do NOT proceed to Phase 4 until the user has answered.

### Phase 4: Fix

Apply the user's chosen fixes. After fixing:
- Run `ruff check` on all changed files
- Run `mypy src/` on all changed files
- Run `pytest -q`
- Fix any errors introduced

### Phase 5: Re-audit

Repeat Phase 2-4 with a FRESH Explore agent on the same scope. The new agent should NOT know about prior findings — it starts from scratch.

Continue the audit-fix-reaudit loop until the audit returns no actionable findings or the user decides not to fix any remaining findings.

### Phase 6: Done

Commit all changes.
