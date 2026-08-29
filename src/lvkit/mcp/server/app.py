"""The MCP server instance every tool module registers against, plus the
instructions text handed to the client at ``initialize``.

Two tool groups (understanding only — artifact generation lives in the CLI):

1. **Index-backed, project-scoped** (``index``, ``query``, ``query_schema``,
   ``visualize_project``) — answer *project-wide* questions in one call from the
   persisted, path-keyed facts index (``lvkit.index``). The ``query`` tool is
   read-only SQL over a curated view layer — it returns the *answer* (a
   ``GROUP BY`` histogram), not the source rows, and REPLACES the old
   per-question read tools (``find_terminals``/``find_constants``/…, retired
   2026-08-08) AND the former graph-op tools: the call graph is now the ``node``
   view's ``kind='vi'`` slice (``callee_path``), so callers/callees are one-hop
   selects and blast radius a recursive CTE (``vi.callers_count`` /
   ``vi.impact_score`` give the counts). No per-VI round trips. A per-project
   cache,
   NOT a single global graph any ``clear`` could nuke — safe for an agent
   working across several repos in one session.

2. **Deep single-VI** (``describe`` for prose, ``read_vi`` for the structured
   netlist) — full dataflow detail for ONE VI, loaded live on demand (XML
   already cached). The Serena split: bulk/navigation off the index, depth on
   demand. An AI CONVERTS a VI by understanding it here and writing idiomatic
   Python itself — lvkit's deterministic AST generator is a CLI/oracle tool, not
   an MCP crutch.

Artifact generation (Python packages, HTML docs, pyvis graphs, diffs) is
CLI-only (``lvkit generate``/``docs``/``visualize``): it writes files and
belongs in scripts/CI. The ONE exception is ``render`` — a VI's block-diagram
SVG, which an AI CANNOT reconstruct from the netlist (only lvkit has the
geometry from the ``.vi`` binary), so it's an MCP tool that writes the SVG
artifact and returns its **path** (the markup is large — written, not inlined
into context). Everything else stays a pure in-process read — no subprocess or
non-packaged-``scripts/`` dependency.
"""

from __future__ import annotations

from ._compat import _MCPServer

_INSTRUCTIONS = """\
lvkit reads LabVIEW code. A LabVIEW project (`.vi`, `.lvclass`, `.lvlib`,
`.lvproj`) is a BINARY format — `grep`, `cat`, `find`, and ad-hoc `python`
scripts CANNOT parse it and return nothing usable. In a LabVIEW repo these
tools are your ONLY way to see the code, so reach for them FIRST; do not grep
a `.vi`.

lvkit indexes the whole REPOSITORY — every `.vi` on disk. A repo may hold many
LabVIEW PROJECTS (`.lvproj` files); a VI can belong to several of them or none.
"The project" is therefore ambiguous — don't assume one `.lvproj` scopes the
repo. To filter by an actual LabVIEW project, use the `lvproj` view (membership
is many-to-many): "classes in VIUnit.lvproj" is `SELECT member_name FROM lvproj
WHERE lvproj_name='VIUnit' AND member_type='LVClass'`.

For any question about the project, start here:

- Structure, classes & inheritance, terminals, constants, type usage,
  `.lvproj` membership — `query` runs read-only SQL over the project's facts
  index (views: `vi`, `class_fact`, `terminal`, `constant`, `node`,
  `type_use`, `lvproj`; call `query_schema` for columns). "What classes exist
  and how do they inherit?" is `SELECT owning_class, parent FROM class_fact`.
  It returns the answer (e.g. a GROUP BY histogram), not a row dump.
- Find a block-diagram PATTERN across every VI at once — the `node` view is
  grep for VI code: one row per node (a primitive, SubVI call, structure,
  constant, ...) with its `kind`, robust identity (`prim_id` for primitives,
  `qualified_name` for SubVI calls), and STRUCTURAL containment (`parent_uid`,
  `frame`) — but NO wiring. This is grep-not-read: `query` the `node` view to
  find WHICH VIs match a pattern, then read the actual dataflow of a hit with
  `read_vi`. Robust filters are `prim_id`/`qualified_name`, not `name`.
  Worked slices:
    - Callers of a VI: `SELECT DISTINCT vi_path FROM node WHERE
      callee_path='<abs path of MyVI.vi>'` (or the vi.callers_count column).
    - A structure containing another (e.g. an event-handler loop): self-join
      `node c JOIN node p ON c.parent_uid=p.uid AND c.vi_path=p.vi_path
      WHERE c.kind='event' AND p.kind='while'`; walk full nesting with a
      `WITH RECURSIVE` over `parent_uid`.
    - Producer/consumer (queues, user events): filter by the queue/event
      `prim_id` (enumerate with `SELECT DISTINCT prim_id, name FROM node
      WHERE name LIKE '%Enqueue%'`) or by `qualified_name` for vi.lib VIs,
      then `read_vi` each hit to trace the named message/refnum.
- Who calls what / change impact — the call graph is the `node` view's
  `kind='vi'` slice via `callee_path`. Direct callers of X:
  `SELECT DISTINCT vi_path FROM node WHERE callee_path='<abs path of X>'`;
  direct callees: `SELECT callee_path FROM node WHERE vi_path='<X>' AND
  kind='vi' AND callee_path IS NOT NULL`. Transitive blast radius: a
  `WITH RECURSIVE deps(p) AS (SELECT vi_path FROM node WHERE callee_path=:x
  UNION SELECT n.vi_path FROM node n JOIN deps ON n.callee_path=deps.p) …`.
  For the COUNTS, `vi.callers_count` (0 == no static caller) and
  `vi.impact_score` are precomputed columns — no CTE needed.
- One VI in depth (pass a path, no load step) — `read_vi` returns its FAITHFUL
  structure (the netlist IR: signature, SubVI/primitive calls, wiring, control
  flow). That IR is raw material, not an answer: INTERPRET it and tell the user
  what the VI DOES — its purpose — don't just echo operations. `render` draws
  the block diagram as an interactive **HTML viewer** (the faithful visual for
  "show me / draw / what does this look like") and `diff` compares two versions
  the same way; each writes a file and returns its path (`{render_path}` /
  `{diff_path}`) — relay that path / open it in a browser, do NOT read the file
  into context or hand-draw one from `read_vi`. NEVER suggest
  opening/screenshotting LabVIEW — these tools ARE how you see it, no license
  needed.
- Convert a VI to Python — UNDERSTAND it with `read_vi`/`query`, then write
  idiomatic Python yourself. (lvkit's deterministic AST generator lives in the
  `lvkit generate` CLI — use it as a reference/oracle, not the primary path.)
- Other artifacts (Python packages, HTML docs, pyvis graphs) are the `lvkit`
  CLI's job (`generate`/`docs`/`visualize`) — they write files; point the user
  at the command.

`query` operates on the whole project at once and
build/refresh the index automatically. Prefer them over per-VI round-trips.
"""

mcp = _MCPServer("lvkit-mcp", instructions=_INSTRUCTIONS)

__all__ = ["_INSTRUCTIONS", "mcp"]
