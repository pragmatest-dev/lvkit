# Improving the lvkit MCP server

Changes made locally against `lvkit 0.5.7` (`lvkit/mcp/server.py`), written up so
they can be applied upstream. They came out of one real task — *count the names
this project uses for error indicators*, across the 487 VIs of VI Tester — which
turned out to be impossible through the MCP surface as shipped.

Local edits were made in a non-editable install
(`.venv/Lib/site-packages/lvkit/`), so any `uv pip install -U lvkit` wipes them.
That is the reason for this document.

---

## 1. Unbounded `mcp` dependency — the server stops starting

**Severity: critical.** This is not a nice-to-have; it silently disables the
entire server.

`lvkit` declares:

```
Requires-Dist: mcp>=0.9.0
```

No upper bound. `mcp` 2.0.0 removed the decorator API that `server.py` is built
on, so a fresh resolve produces an install that dies at *import* time:

```
File "lvkit/mcp/server.py", line 74, in <module>
    @app.list_tools()
     ^^^^^^^^^^^^^^
AttributeError: 'Server' object has no attribute 'list_tools'
```

In `mcp` 2.0.0, `mcp.server.Server` has no tool-related attributes at all:

```python
>>> from mcp.server import Server
>>> [a for a in dir(Server) if 'tool' in a.lower()]
[]
```

### What this looks like to a user

Nothing. The MCP client spawns the process, the process dies on import, the
client registers zero tools and carries on. The server appears *absent* rather
than *broken* — indistinguishable from never having configured it. Diagnosing it
requires manually piping JSON-RPC into `lvkit mcp` to see the traceback.

### Fix

Cap the dependency:

```
Requires-Dist: mcp>=0.9.0,<2
```

Then port to the 2.x API deliberately and lift the cap in the same commit.

### Also worth doing

Make startup failure *visible* and *tested*:

- A `lvkit mcp --selftest` that initializes, lists tools, and exits non-zero on
  failure — so a broken install is one command away from being diagnosed.
- A CI job that runs the real handshake (`initialize` → `tools/list`) against the
  built package. An import-time `AttributeError` should never reach a release;
  nothing in the current test surface catches it.

---

## 2. `load` cannot load a project

**Severity: high.** This is the gap that blocks whole-project questions.

`load` accepted exactly one `.vi`:

```
Expected .vi or *_BDHb.xml file: .../repo/source
```

The graph is persistent and cross-VI by design, but the only tool that populates
it worked one file at a time. Loading VI Tester meant 487 `load` calls. The one
tool that *did* accept a directory was `generate_documents` — which writes an
HTML site to disk, a strange prerequisite for asking a question about types.

### Fix

A helper that resolves a load target into the VIs it stands for:

```python
def _expand_vi_paths(target: str | Path) -> list[Path]:
    """Resolve a load target into the VI files it stands for.

    A single .vi (or *_BDHb.xml) passes through untouched. A directory
    expands to every VI beneath it, and an .lvproj/.lvlib/.lvclass expands
    the same way from its containing folder, so a whole project can be
    pulled into the graph in one call. Sorted so a batch load is repeatable.
    """
    p = Path(target)
    if p.is_dir():
        root = p
    elif p.suffix.lower() in {".lvproj", ".lvlib", ".lvclass"}:
        root = p.parent
    else:
        return [p]
    return sorted(root.rglob("*.vi"))
```

And a `load` handler that iterates, collecting per-VI failures instead of
aborting the batch:

```python
targets = _expand_vi_paths(vi_path)
failed = []
for target in targets:
    try:
        graph.load_vi(target, LoadMode(load_mode), search_paths=search_path_objs)
    except Exception as exc:
        # One unreadable VI shouldn't sink a whole-project load.
        failed.append({"vi": str(target), "error": str(exc)})
return list(graph.list_vis()), len(targets), failed
```

The response reports batch shape only when it is a batch, so single-VI callers
see an unchanged payload:

```python
payload: dict[str, Any] = {"loaded_vis": loaded}
if requested > 1:
    payload["requested"] = requested
    payload["loaded_count"] = len(loaded)
if failed:
    payload["failed"] = failed
```

Partial failure matters here: this corpus is LabVIEW 2013 and older, and the
parser emits warnings on plenty of it. One VI it cannot read must not cost you
the other 486.

`_configure_resolvers_for_vi()` already handled directory inputs — its docstring
explicitly contemplates "a directory (an .lvlib, .lvclass, or a folder of VIs)".
Only `load_vi` rejected them. The intent was there; the entry point hadn't caught
up.

### Verified

- `_expand_vi_paths("source")` → **487**, exactly matching `rglob("*.vi")`.
- Over real MCP stdio against `Classes/TestCase`:
  `requested: 31, loaded: 31, failed: 0`.
- Over real MCP stdio against `source/`: `requested: 487, loaded_count: 422,
  failed: 0`. (On that 422, see §4.)

---

## 3. No bulk query — the graph is loaded but not askable

**Severity: high.** This is the deeper design gap, and fixing §2 alone does not
address it.

Every query tool — `describe`, `get_context`, `get_operations`, `get_dataflow`,
`get_structure`, `get_constants` — takes a single `vi_name`. So even with the
whole project in the graph, any project-wide question costs one round trip per
VI. For 487 VIs, asking "what names do error indicators use?" through MCP means
487 `get_context` calls, each returning full operations, wires and constants —
megabytes of payload to extract a few hundred terminal names.

The persistent graph is the server's best idea. Without a bulk read it cannot be
used for the class of question it is uniquely good at.

### Fix — a `get_signatures` tool

Connector panes for every loaded VI in one call:

```python
Tool(
    name="get_signatures",
    description=(
        "Get the connector-pane terminals of every loaded VI in one "
        "call. Use after load, for project-wide questions.\n\n"
        "Each terminal reports its name, whether it is a control or an "
        "indicator, its type, and — for clusters — its field names. "
        "That is enough to classify terminals (finding every error "
        "cluster, say) without a round trip per VI.\n\n"
        "Optionally restrict to vi_names; omit for all loaded VIs."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "vi_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only report these VIs. Omit for every loaded VI.",
                "default": [],
            },
        },
    },
),
```

Payload size is the whole point, so terminals are summarized rather than dumped:

```python
def _summarize_terminal(term: Any) -> dict[str, Any]:
    """Compact record for one connector-pane terminal.

    A full LVType is far too heavy to repeat across a whole project, so we
    keep the type kind plus any cluster field names — enough to classify a
    terminal (an error cluster, say) without shipping the whole type tree.
    """
    lv_type = getattr(term, "lv_type", None)
    summary: dict[str, Any] = {
        "name": term.name,
        "direction": (
            "indicator" if getattr(term, "is_indicator", False) else "control"
        ),
        "type": (
            getattr(lv_type, "underlying_type", None)
            or getattr(lv_type, "kind", None)
        ),
    }
    fields = getattr(lv_type, "fields", None) or []
    if fields:
        summary["fields"] = [getattr(f, "name", None) for f in fields]
    return summary
```

Handler mirrors the existing ones, including `asyncio.to_thread`:

```python
elif name == "get_signatures":
    wanted = arguments.get("vi_names") or []

    def _signatures():
        graph = _get_graph()
        vi_names = wanted or list(graph.list_vis())
        vis = []
        for vi_name in vi_names:
            ctx = graph.get_vi_context(vi_name)
            if not ctx.inputs and not ctx.outputs and not ctx.operations:
                vis.append({"vi": vi_name, "error": "not loaded"})
                continue
            vis.append({
                "vi": vi_name,
                "terminals": [
                    _summarize_terminal(t)
                    for t in list(ctx.inputs) + list(ctx.outputs)
                ],
            })
        return vis

    vis = await asyncio.to_thread(_signatures)
    return [TextContent(
        type="text", text=json.dumps({"vis": vis}, indent=2, default=str)
    )]
```

Output shape:

```json
{"name": "error in (no error)", "direction": "control",
 "type": "Cluster", "fields": ["status", "code", "source"]}
```

Cluster field names are what make this useful: an error cluster can be
identified *structurally* (`{status, code, source}`) rather than by guessing from
its label — which matters when the question is precisely "what labels are in
use?"

### Verified

In-process against `Classes/TestCase`: 31 VIs,
`{'error out': 31, 'Test Method Error': 1}`. Tool count 12 → 13; the full
`tools/list` handshake succeeds.

### Follow-on

The same argument applies to other project-wide reads. A `find_terminals`
(filter by type/name/direction) or a bulk `get_dependencies` would each remove a
whole class of N-round-trip work. `get_signatures` is the minimum case, not the
complete answer.

---

## 4. The graph keys VIs by name, so same-named VIs collide

**Severity: medium — silent wrong answers.** Found during this work, *not* fixed
locally.

Loading `source/` reports 487 requested, 422 loaded, zero failures. The missing
65 did not fail — they were overwritten. `graph.list_vis()` is keyed by VI name,
and this corpus has:

```
distinct filenames: 357 | files: 487
names appearing >1x:  61 | extra copies: 130
  17x  setUp.vi
  17x  CleanUp.vi
  16x  tearDown.vi
   7x  New.vi
   4x  Create.vi
   4x  runTest.vi
```

Class-owned VIs get qualified names (`TestCase.lvclass:run.vi`) and survive.
Loose VIs in plain folders do not — and `setUp.vi`/`tearDown.vi`/`CleanUp.vi` are
exactly the shape of a test framework, so the collisions cluster in the most
interesting code.

The failure mode is the bad kind: no error, no warning, a plausible-looking
number that is quietly incomplete. Any project-wide count taken from the graph is
wrong by however many VIs happened to share a name.

### Fix

Key the graph on resolved path, keeping name as a lookup index that reports
ambiguity rather than silently resolving it. Failing that, at minimum:

- qualify unowned VIs by their path relative to the project root, and
- have batch `load` report shadowed VIs explicitly, so `requested` vs
  `loaded_count` is explained rather than left to be noticed.

As a measure of the impact: tallying error-cluster terminals across this project
gives **352 error indicators over 487 VIs** when each VI is loaded into its own
graph, versus a silently short count through the shared-graph MCP path.

---

## 5. Testing note: stdio shutdown cancels in-flight calls

Not a server bug, but it will bite anyone writing tests against the real
transport.

The stdio server shuts down on stdin EOF. A harness that writes its requests and
closes stdin — the obvious thing to do — gets a clean exit code and *no
response* for any call still running. It looks exactly like a hung or crashed
tool. Fast calls like `tools/list` return fine, so the problem only appears once
a call takes real time, which is precisely when you are testing batch behavior.

Keep stdin open until the response arrives:

```python
proc = subprocess.Popen([...], stdin=PIPE, stdout=PIPE, text=True, bufsize=1)
send(initialize); send(initialized); send(tools_call)
for line in proc.stdout:          # blocks until the reply lands
    ...
proc.stdin.close()
```

Worth stating in contributor docs. Worth considering whether a long-running call
should be allowed to finish, or at least return an error, rather than vanishing.

---

## Priority

1. **§1 — cap `mcp<2`.** Without it the server does not run at all, and fails
   invisibly. Everything else is moot.
2. **§3 — bulk query.** The persistent graph is the differentiating feature and
   is currently unusable for project-scale questions.
3. **§2 — project-level `load`.** Cheap, self-contained, and a prerequisite for
   §3 being worth anything.
4. **§4 — path-keyed graph.** Correctness. Produces quietly wrong answers today.
5. **§5 — document the stdio shutdown gotcha** and add the startup smoke test
   from §1.

§2 and §3 are implemented and verified against the real MCP transport; the code
above can be lifted as-is. §1 is a one-line metadata change. §4 needs a design
decision that belongs with the maintainers.
