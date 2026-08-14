---
name: lvkit
description: Start here for any task on a LabVIEW VI/lvclass/lvlib/lvproj repo — routes to the right lvkit skill. Works via CLI or MCP.
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

Every lvkit skill works two ways: the **MCP tools**, when the lvkit MCP
server (`lvkit mcp`) is connected — structured output, a session-persistent
project index, and path-defaulting to the client's workspace root — or the
identical `lvkit …` CLI otherwise. Capability is the same either way; each
skill states which mode it's using and flags the handful of commands
(`lvkit diff`, `lvkit render`, `lvkit structure`) that have no MCP twin.

lvkit is an independent, clean-room project: it reads the `.vi` binary
format and public NI documentation, never LabVIEW itself. It ships zero
NI-derived artwork or documentation prose.
