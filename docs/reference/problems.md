# Problems, exit codes and `--fail-fast`

Every lvkit command and MCP tool reports a problem with a VI the same way. A VI
that cannot be read, or that fails in the view you asked for, never stops the
run: it is recorded, the command goes on with the other VIs, and the VI shows as
unreadable wherever another VI refers to it (in [`generate`](generate.md), a call
of it is a [gap](generate.md#gaps)).

## What you see

Each problem prints one line on stderr as it is found:

```
warning: <VI path>: <stage>: <reason>
```

The path is relative to your project (a vi.lib VI shows its recorded
`<vilib>/...` path; a VI inside an `.llb` shows the `.llb`'s path and its name) —
never an absolute path. The stage is where it happened (`extract`, `load`,
`index`, `generate`, `render`, `document`, `describe`, `diff`, `visualize`,
`structure`, `cache`). When the file could not be read, the lines pylabview printed about
it follow the warning, indented.

A run that found any problem ends with a summary on stderr: `Complete`,
`Partial: N problem(s)` or `Stopped: N problem(s)` (with `(includes an lvkit error)` when lvkit itself failed on a VI, which also makes the exit code `1`), then **every** problem on one
line — the list is never cut off. `--format json` (and `index`'s JSON line) carry
the same `status` and `problems`.

## Exit codes

One principle: `1` means the command stopped (or lvkit itself failed), `3` means it
produced its output but something went wrong on the way, `0` means nothing did.
The kind of problem does not pick the code by itself.

| Code | Meaning |
|------|---------|
| `0` | Complete: nothing went wrong. |
| `1` | Stopped: the VI a command is about could not be read, its output could not be produced or written, `--fail-fast` ended the run, or lvkit itself failed on a VI (a bug; the warning says where to report it). |
| `2` | Usage error: bad arguments (rejected by the argument parser), a path that does not exist, a file of the wrong kind, or a SQL statement `query` refuses. |
| `3` | Partial: the command finished and wrote its output, but some VIs were left out, some nodes could not be converted, or lvkit could not write its cache for some of them. |

`generate` also prints its own counts line on stdout (`Partial: 87 VIs generated,
12 with gaps (31 gaps) — see warnings`).

**Cache failures.** lvkit's extraction cache and index live in a cache folder. When a
write to it fails (a full disk, permissions, a Windows path over the limit) the
problem is stage `cache` with no bug hint, and its reason names the file as `~/…`.
For one VI of many it is that VI's problem and the run is Partial (`3`). A command
that cannot do its job without the cache stops (`1`): `describe` of a VI whose
extraction cannot be cached, and `index`, whose output is the index. Commands such
as `describe`, `render` and `generate` that cannot update the index after
producing their output are Partial (`3`), and that problem is not stored with a
cached render or diff.

## `--fail-fast`

`--fail-fast` ends the run at the first problem, prints its warning and a `Stopped`
summary listing every problem recorded so far, and exits `1` (`index` still prints
its JSON line, with `status: "Stopped"`). It is on every command that processes
many VIs: `index`, `query`, `callers` / `callees` / `blast-radius`, `generate`,
`unresolved`, `render`, `docs` and `visualize`. Use it to avoid waiting for a long
run that you already know is incomplete. A failed index update does not stop even
a fail-fast run.

## Empty answers say why

- An unknown VI is an error: `callers`, `callees` and `blast-radius` print
  `'X.vi' was not found in the index` and exit `1` (a bare name that matches
  several VIs lists them).
- A VI the index has, with no callers or callees, prints an empty result and a
  `note:` on stderr.
- A `query` that returns no rows prints a `note:` on stderr.

## MCP tools

Every tool returns its result plus `status` and `problems`. A call fails only for
a bad argument (a path that does not exist). When the one VI a tool is about
cannot be read there is no result to give: the answer is `status: "Stopped"` with
the problem that says why.
