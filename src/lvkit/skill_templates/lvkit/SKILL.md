---
name: lvkit
description: Use when the user has a task involving a LabVIEW `.vi`/`.lvclass`/`.lvlib`/`.lvproj` file or repo and it's unclear which specific lvkit skill applies — routes to the right one (describe, query, review, convert, resolve, document).
allowed-tools: Bash, Read, Grep
---

# lvkit

A `.vi`, `.lvclass`, `.lvlib`, or `.lvproj` file is a **binary format**.
`grep`, `cat`, `find`, and ad-hoc scripts return nothing usable against it.
**Never grep a `.vi`.** lvkit parses the binary — no LabVIEW license required
— and exposes it as a dataflow graph, a queryable facts index, generated
Python, or a diff.

## Find the right skill

| You want to... | Use |
|---|---|
| Understand what one VI does (signature, dataflow, structures, constants) | `/lvkit-describe` |
| Answer a question about the whole repo — class hierarchy, terminal/constant facts, dead code, `.lvproj` membership | `/lvkit-query` |
| See what changed between two VI versions and who's affected | `/lvkit-review` |
| Convert a VI (or a whole library) to Python | `/lvkit-convert` |
| Identify an unknown primitive or vi.lib VI and persist a mapping | `/lvkit-resolve` |
| Generate a browsable documentation site for a library/class | `/lvkit-document` |

## Getting the facts

lvkit is **understanding-only over MCP** — 6 tools, all read: `index`,
`query`, `query_schema`, `describe`, `read_vi`, `unresolved`. Each has an
identical CLI twin (`lvkit index`/`query`/`describe`/`unresolved`; `lvkit
describe --format json` is the CLI form of `read_vi`'s structured netlist
IR). Prefer the MCP tool when the lvkit MCP server (`lvkit mcp`) is
connected — structured output, a session-persistent project index, and
path-defaulting to the client's workspace root — else the CLI.

Everything that **writes** an artifact — `generate`, `docs`, `visualize`,
`diff`, `render` — is CLI-only, with no MCP tool at all: an AI converts a VI
by understanding it (`read_vi`/`describe --format json`/`query`) and writing
the code itself; `lvkit generate` is a verification oracle to diff against,
not something callable over MCP. `callers`/`callees`/`blast-radius` and
`structure`/`setup`/`detect` are also CLI-only — reachability questions run
as `query` over the `node` view's `callee_path` column instead (no
`get_callers`/`get_callees`/`blast_radius` MCP tool exists). Each skill
states which mode it's using.

lvkit is an independent, clean-room project: it reads the `.vi` binary
format and public NI documentation, never LabVIEW itself. It ships zero
NI-derived artwork or documentation prose.
