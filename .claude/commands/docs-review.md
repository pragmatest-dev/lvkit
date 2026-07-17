---
description: Run the documentation audit coordinator (ordering, voice, audience, accuracy, gaps, crosslinks) over one or more lvkit doc pages.
argument-hint: "[page path]"
---

# Docs Review

Audit lvkit documentation pages with the `audit-coordinator` agent.

## Procedure

1. Resolve `$ARGUMENTS` to a list of page paths:
   - If a path is given, use that single page (resolve relative to the repo root).
   - If no argument is given, audit every page under `docs/reference/*.md`, including `index.md`.
2. For each page, dispatch a fresh `audit-coordinator` agent with that page's absolute path. Run pages in parallel (multiple Agent tool calls in one message) when auditing more than one page.
3. Each `audit-coordinator` run writes its own combined report to `.tmp/page-audits/<slug>.md` and fans out to the six dimension agents (`audit-ordering`, `audit-voice`, `audit-audience`, `audit-accuracy`, `audit-gaps`, `audit-crosslinks`) itself — do not call those six directly.
4. After all coordinators return, summarize: total CRITICAL / WARNING / SUGGESTION counts per page, and the output file paths.

## Notes

- `docs/_internal/**` is contributor-only and is never in scope for this command.
- Corpus-wide coverage (code → docs, not per-page) is a separate agent, `audit-coverage` — invoke it directly if that's what's wanted; it's not part of this command's default sweep.
