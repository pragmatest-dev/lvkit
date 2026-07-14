---
name: lvkit-resolve-primitive
description: Resolve a single unknown LabVIEW primitive by following a strict verification process against documentation and graph context. Called when PrimitiveResolutionNeeded fires during lvkit generate.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch
---

# Resolve Unknown Primitive

When `PrimitiveResolutionNeeded` fires for an unknown primitive during `lvkit generate`, this skill resolves it by identifying the function and writing a JSON mapping. Follow ALL steps IN ORDER. Do NOT skip. Do NOT guess.

(`TerminalResolutionNeeded` is a separate exception for known primitives where a specific terminal index doesn't match — that's a different problem with the same workflow.)

## Step 0: Detect mode

This skill runs in two contexts. The destination directory and the cross-reference corpus differ. Decide which one applies BEFORE you do anything else.

Read `pyproject.toml` from the current directory (walk up if needed). If it contains `name = "lvkit"`, you are working **inside lvkit itself**:

- Destination: `data/primitives.json` (lvkit's shipped, cleanroom data)
- Cross-reference corpus: lvkit's `samples/` directory of real VIs
- Mark `"verified": true` only after multi-instance cross-check
- The mapping must be cleanroom — derived from public documentation, NOT from licensed LabVIEW source

Otherwise, you are working **inside a downstream user's project**:

- Destination: `.lvkit/primitives.json` (project-local store; run `lvkit init` first if `.lvkit/` doesn't exist)
- Cross-reference corpus: the user's own VI tree. The diagnostic includes the **qualified VI name** of the caller — use it to find the calling VI on disk.
- Do NOT cross-reference lvkit's `samples/` — the user's project doesn't have them
- The mapping you write may be derived from licensed sources; that's the user's call. lvkit itself never reads `.lvkit/`.

In **both** modes, the function-identification step (Step 5) uses live web search against NI's online documentation. lvkit does not bundle the LabVIEW reference manual.

## Step 1: Record the diagnostic

Write down the EXACT diagnostic output:
- primResID **and the node's XML class** (`prim`, `aIndx`, `cpdArith`, … — the
  same resID can mean different ops across classes; see Step 4.5)
- Every terminal: index, direction, and **full type** — for arrays record the
  ELEMENT type (`Array[NumInt32]`, not just `Array`); the output type is the
  single most important field for identification (Step 4.5)
- The VI name (and the qualified name if present — "In VI: ..." line)

**IMPORTANT: the heap carries the primitive's FULL connector pane — identify by it, not just wired terminals.** LabVIEW serializes EVERY terminal of a primitive in its `termList`, including UNWIRED ones, each with its own index, direction (from `objFlags`), and declared type (from its own `typeDesc`). So the full terminal count + per-terminal type signature ARE available in the parsed data (verified in-tree: prim 1302 carries an unwired output typed `NumUInt32`; prim 1056 an unwired output typed `NumFloat64`). This full connector-pane signature is the strongest discriminator — use it.

**The `PrimitiveResolutionNeeded` diagnostic already lists this full pane.** It reports EVERY terminal — each marked `wired` or `UNWIRED` — with its index, direction, and declared type (the graph carries unwired terminals; the diagnostic does not drop them). So identify from the whole signature shown in the diagnostic: total terminal count + each terminal's direction + type. Do NOT match only the wired subset, and do NOT assume the count is short.

For a **polymorphic/adaptive** primitive, an UNWIRED terminal may show its adapt/placeholder type rather than a concrete one — count, direction, and position stay reliable; concrete types come from the wired terminals.

## Step 2: Get more instances of this primResID — grep the XML, DON'T parse the corpus

**NEVER parse every VI in the corpus to find instances.** The sample tree is thousands of
VIs / ~1 GB of XML; `rglob('*.vi')` + `parse_vi` on all of them loads everything into memory at
once and can exhaust RAM and CRASH the machine (WSL especially — this has happened). Instead:
**grep the already-dumped block-diagram XML** for the primResID, then parse ONLY the matched VIs
— and only those VIs, **not their subVIs**.

The primResID is serialized verbatim as `<primResID>PRIM_ID</primResID>` in each VI's
`*_BDHb.xml` (grep is what reliably found prim 1907's real call sites — including one in a
*subVI* of the failing VI that a shape-based parse would have missed).

**1. Ensure the block-diagram XML is dumped (pylabview — no LabVIEW license needed).**
- lvkit mode: `samples/` is already extracted — every VI has a `*_BDHb.xml` beside it. Nothing to do.
- user-project mode: extract each VI's XML once — ONE subprocess per VI, so memory stays flat:
  ```bash
  # <root> = the user's project root
  python3 -c "
  from pathlib import Path
  from lvkit.extractor import extract_vi_xml
  for vi in Path('<root>').rglob('*.vi'):
      try: extract_vi_xml(str(vi))   # writes *_BDHb.xml beside the VI (cached); no subVIs
      except Exception as e: print('skip', vi.name, e)
  "
  ```

**2. Grep the dumped XML for the primResID (memory-flat, instant):**
```bash
# <root> = samples (lvkit mode) or the user's project root
grep -rl "<primResID>PRIM_ID</primResID>" <root> --include=*_BDHb.xml
```
This lists exactly the VIs that contain the primitive — usually a handful.

**3. Parse ONLY those matched VIs — no subVIs, no dependency loading.** Feed the matched
`*_BDHb.xml` paths straight to `parse_vi(bd_xml=...)`, which parses just that ONE VI's block
diagram (it does NOT load subVIs or dependencies). Cap the count and `del` each parse so memory
never accumulates:
```bash
# grep -rlZ (null-delimited) pipes matches to the parser — handles SPACES in paths
# (sample paths like "File Group 0" break unquoted $(grep ...)). Caps at 10; frees each parse.
grep -rlZ "<primResID>PRIM_ID</primResID>" <root> --include=*_BDHb.xml | python3 -c "
import sys
from lvkit.parser.vi import parse_vi
from lvkit.parser.node_types import PrimitiveNode
PRIM_ID = <PRIM_ID>
paths = [p.decode() for p in sys.stdin.buffer.read().split(b'\0') if p]
for bd in paths[:10]:                             # matched _BDHb.xml paths, capped
    diagram = parse_vi(bd_xml=bd).block_diagram   # this VI ONLY — no subVIs
    for node in diagram.nodes:
        if isinstance(node, PrimitiveNode) and node.prim_res_id == PRIM_ID:
            terms = sorted((ti for ti in diagram.terminal_info.values() if ti.parent_uid == node.uid), key=lambda t: t.index)
            sig = [f'{\"out\" if ti.is_output else \"in\"}:idx{ti.index}:{ti.parsed_type.type_name if ti.parsed_type else \"?\"}' for ti in terms]
            print(f'{bd.split(\"/\")[-1]}: primIdx={node.prim_index} {sig}')
    del diagram
"
```
Same command for both modes — only `<root>` differs. Grep first, parse only the matches, never
`rglob('*.vi')` + parse, and never expand subVIs. (Verified: on prim 1907 this parses ~6 matched
VIs at 48 MB peak / 0.5 s, vs a multi-GB whole-corpus parse.)

## Step 3: Examine graph context

For each instance, check what operations feed into and consume from this primitive. **Trace beyond immediate neighbors** — follow wires through structure boundaries (tunnels, shift registers), nMux nodes, and into/out of SubVI calls. The name of the VI that CALLS the primitive, and the names of VIs/primitives that consume its outputs, are often the strongest identification signal.

The fastest way to see context is `lvkit describe` on the calling VI:

```bash
lvkit describe "<path-to-calling-vi>" --search-path "<library-path>"
```

Or programmatically — parse ONLY the matched `*_BDHb.xml` from Step 2 (no rglob, no subVIs):

```bash
python3 -c "
from lvkit.parser.vi import parse_vi
from lvkit.parser.node_types import PrimitiveNode
BD = '<matched _BDHb.xml from Step 2>'
PRIM_ID = <PRIM_ID>
bd = parse_vi(bd_xml=BD).block_diagram        # this VI ONLY — subVIs are NOT loaded
for node in bd.nodes:
    if isinstance(node, PrimitiveNode) and node.prim_res_id == PRIM_ID:
        my_terms = {uid for uid, ti in bd.terminal_info.items() if ti.parent_uid == node.uid}
        for w in bd.wires:
            if w.from_term in my_terms or w.to_term in my_terms:
                other_uid = w.to_term if w.from_term in my_terms else w.from_term
                other_ti = bd.terminal_info.get(other_uid)
                if other_ti:
                    other_node = next((n for n in bd.nodes if n.uid == other_ti.parent_uid), None)
                    direction = 'output →' if w.from_term in my_terms else 'input ←'
                    print(f'  {direction} {other_node.name if other_node else other_ti.parent_uid}')
        break
"
```

If immediate neighbors are generic (nMux, structure boundaries, constants), trace further —
staying within THIS VI's XML (do not load subVIs; their name in the caller is enough context):
- What VI contains this primitive? The VI's name and purpose give context.
- What are the connected SubVIs *named*? The caller's XML carries each subVI-call node's name — read that; you don't need to open the subVI.
- What primitives feed into or consume from this one? Check their primResIDs against known entries.

The connected operations, their names, and the data types flowing through reveal what this primitive does.

## Step 4: Cross-check primResID range

Related LabVIEW primitives share primResID ranges:
- 1044-1064: Array operations
- 1061-1081: Numeric/arithmetic
- 1083-1128: Path/comparison/boolean
- 1140-1170: Type conversion, variant, data manipulation
- 1300-1340: Timing, constants, clusters
- 1419-1435: Path operations
- 1500-1540: String operations
- 1600-1610: Flatten/unflatten
- 1809-1911: Array index/sort/delete
- 1999: Path constant
- 2073-2076: Error handling
- 2401: Merge Errors
- 8003-8083: File I/O
- 8100-8101: VI info
- 8201-8205: Variant operations
- 9000-9114: VI Server, references, scripting

Does the terminal signature (types, count, directions) fit the range?

## Step 4.5: Types are the discriminator (read this every time)

Shape (how many inputs/outputs) is NOT enough to identify a primitive. The
**exact I/O types — especially the OUTPUT type and array element types — are
what distinguish operations that look identical by shape.** Most mislabeled
entries in `data/primitives.json` come from guessing by shape and ignoring
type.

**Get ground-truth types, not display labels.** The parser sometimes flattens
an array terminal to just `Array` (no element type) or types an output by
context. When an output type is surprising or pivotal, resolve the raw
`<typeDesc>TypeID(N)</typeDesc>` against the type map instead of trusting the
graph's resolved kind:

```bash
python3 -c "
from lvkit.parser.type_mapping import parse_type_map_rich
tm = parse_type_map_rich('<main>.xml')
lt = tm.get(N); print(lt.kind, lt.underlying_type, getattr(lt.element_type,'underlying_type',None))
"
```

**Array → scalar is the classic trap.** Many different ops take an array and
return a scalar, and they are only told apart by the OUTPUT type:
- `Array[X] → NumInt32` (a count, element-type-independent) → **Array Size** /
  a search/index returning a position. The output is an integer regardless of
  the element type.
- `Array[X] → X` (output is the element type — `Array[DBL] → DBL`) → a
  **reduction**: Add Array Elements (sum), Multiply Array Elements (product),
  Array Max/Min, etc. NOT Array Size.

So **never label an `Array → scalar` op "Array Size" unless the output is an
integer count.** If the output carries the element type, it is a reduction.

**Cross-reference with VARIED element types (Step 2) to force the answer.** An
op that yields `NumInt32` for *non-numeric* element types (Path, Refnum,
String, Cluster) can ONLY be a count — you cannot sum paths. An op whose
output type tracks the input element type is a reduction. One instance over a
`DBL` array (where sum and size both look plausible) is ambiguous; instances
over mixed element types are decisive.

**A Refnum's `ref_type` IS the identity — read it FIRST, don't guess.** When a
terminal resolves to `Refnum`, the parser also carries **`ref_type`** on that
type (`parsed_type.ref_type` / the graph terminal's `lv_type.ref_type`): `Queue`,
`Notifier`, `DVR`, `Occurrence`, `Semaphore`, etc. That single field tells you
the whole family — a `Queue` refnum threaded through with error clusters, a
value cluster (the element subtype), and a timeout int is Obtain/Enqueue/Dequeue/
Get-Queue-Status, NOT some "VI Server variant" op. Dump the FULL parsed_type of
the reference terminal (`vars(ti.parsed_type)`), see `ref_type`, then read that
family's function pages (Step 5) and match by connector pane. **Real example
(2026-07-11):** primResIDs 9108/9111/9113/9129/9109 in an XML "Fast Parser Stack"
were flailed at as "Open/Call Variant" for ages — the idx8 refnum's
`ref_type` was `Queue` the entire time; they are Obtain Queue / Enqueue Element /
Dequeue Element / Enqueue At Opposite End / Get Queue Status. Never label a
refnum op without checking `ref_type`.

**Watch for resID collisions across XML classes.** The same primResID can be
assigned to two different operations depending on the node's XML class. Codegen
resolves `node_type` (XML class) BEFORE `primResID`, so an expandable node
(e.g. `aIndx` = Index Array) and a plain `prim` with the same numeric resID can
coexist as different functions. Always note the node's XML class, not just the
resID, and check whether a specialized handler already covers the class.

**Audit for duplicate names before adding.** If `data/primitives.json` already
has several entries with the same `name` (e.g. multiple "Array Size"), that is
a red flag that earlier resolutions guessed by shape. Do not add another —
verify by output type which entries are actually that function and treat the
rest as suspect.

**Force the identity from the consumer dataflow (extends Step 3).** When types
narrow it to a small set, reconstruct the surrounding expression as code and
ask which candidate makes the algorithm coherent. Example from a real VI: a
unary `DBL→DBL` op fed `deltas → ? → sum → ? → To Byte Integer → direction
flag (±1)`. Only **Sign** makes that a wrap-robust majority-vote direction
detector; Negate/Abs/Decrement produce a value that can't be a ±1 flag. The
consumer's required output (here, ±1) plus the types uniquely determine the
function. This is deterministic deduction, not guessing — but only when both
the types and the consumer constraint pin it.

## Step 5: Identify the function via web search

lvkit does not bundle NI's reference manual. Use the WebSearch tool to look up candidate functions on NI's documentation site.

Search queries that work well:

- `LabVIEW <CANDIDATE FUNCTION NAME> function terminals` — broad lookup
- `site:ni.com/docs <CANDIDATE FUNCTION NAME>` — restrict to NI docs
- `LabVIEW <CATEGORY from Step 4> primitives` — when you only know the category

### Read NI docs RELIABLY via the backend API (do this — don't fight the SPA)

`https://www.ni.com/docs/...` and `https://labviewwiki.org/...` are JS single-page
apps: `WebFetch` on them returns the nav shell / a stub, NOT the connector pane.
Every LabVIEW function DOES have a full page — fetch it from the **content
backend** instead. The SPA loads each page as JSON from:

```
https://docs-be.ni.com/api/bundle/<bundle>/page/<page-path>.html
```

- `<bundle>` for LabVIEW functions is `labview-api-ref` (the "LabVIEW Programming
  Reference Manual"). Other bundles exist (e.g. `labview`) — the SPA URL's
  `/bundle/<X>/page/<Y>` segments map 1:1 onto the backend path.
- The response is JSON. The connector pane is in the **`topic_html`** field
  (full Inputs/Outputs prose); `breadcrumbs` give the palette category (e.g.
  "Programming ▸ Cluster, Class, & Variant ▸ Variant").

Do it with `curl` + a tiny parse (WebFetch also works on the backend URL):

```bash
curl -sL "https://docs-be.ni.com/api/bundle/labview-api-ref/page/functions/get-variant-attribute.html" \
  | python3 -c "import json,sys,re,html; d=json.load(sys.stdin); \
    t=re.sub(r'<[^>]+>',' ',d['topic_html']); print(html.unescape(re.sub(r'\s+',' ',t)))"
```

**Finding the page path when you only know the function name:** the path mirrors
the palette, e.g. `functions/<kebab-name>.html` (`get-variant-attribute.html`,
`look-in-map.html`). Confirm the exact slug via a WebSearch for the function on
`ni.com/docs` (search finds it fine — it's only *reading* the SPA that fails),
then read the real content from the backend URL above. To browse a category's
functions, fetch its menu: `.../page/menus/categories/programming/cluster/variant-mnu.html`.

When you have the page, use it to confirm:

- The terminal TYPES match the actual types from Step 1
- The terminal NAMES and DIRECTIONS match the documentation
- The diagnostic lists the FULL connector pane (every terminal, each marked wired/UNWIRED, with its type) — the whole signature should match the documentation, not just the wired terminals.
- The CONTEXT (Step 3) makes sense for what the function does

If the backend + WebSearch still turn up nothing useful:

- **lvkit mode**: cross-reference the `samples/` corpus more aggressively (Step 2), and ask the user
- **user-project mode**: ask the user to open the calling VI in LabVIEW and read the primitive's context menu / quick help. The qualified VI path from the diagnostic tells you which file to point them at.

## Step 6: Add the JSON entry

Only after completing steps 1-5. The destination depends on the mode you detected in Step 0:

- **lvkit mode** → `data/primitives.json`
- **user-project mode** → `.lvkit/primitives.json`

Add under the `primitives` key:

```json
"PRIM_ID": {
    "name": "Confirmed Function Name",
    "terminals": [
        {"index": N, "direction": "in", "name": "descriptive_python_name", "type": "actual_type"},
        {"index": N, "direction": "out", "name": "descriptive_python_name", "type": "actual_type"}
    ],
    "python_code": {"output_name": "in_N op in_M"},
    "inline": true,
    "verified": true,
    "doc_url": "https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/functions/<slug>.html"
}
```

For a **node-type** primitive (resolved by XML class, e.g. `aBuild`/`cpdArith`
— see Step 4.5), add the entry under the top-level `node_types` key instead of
`primitives`, keyed by the class name; `doc_url` works there the same way (the
resolver surfaces it for hover/render links).

Rules:
- **`doc_url`**: the PUBLIC NI docs URL of the page you identified in Step 5.
  Reuse that page's exact `nav_path` from the docs backend — the public URL is
  `https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/<nav_path>` (usually
  `functions/<kebab-name>.html`). Include it whenever the primitive has a public
  NI page; a constant or internal op with no page simply has no `doc_url`.
- Terminal **indices** MUST be read from THIS VI's observed wiring/heap (Steps 2-3), NEVER assumed from documentation order — and NEVER copied from another VI. **There is no shared index rule, not even a reliable baseline: every VI/primitive carries its own connector-pane pattern AND orientation, and the index origin corner + direction differ by pattern.** That is exactly what makes connector-pane indices hard — you have to read each and every one individually. Known-divergent examples: the common **4x2x2x4** pattern indexes Right→Left, Bottom→Top (idx 0 = bottom-right, outputs on the right get the LOW indices); but **5x2x2x2x5** (misnomer "5x3x3x3x5") starts idx 0 in the **top-left and increments the other way** — same-family patterns, opposite origins. Documentation lists terminals in reading order, which the pane indices do not follow (e.g. Split 1D Array docs list "array, index" but the pane has index=2 = numeric index, index=3 = array). So never infer an index from a pattern name or a "usual" rule — read the observed indices for this specific VI.
- Terminal **directions** MUST be confirmed from observed `is_output` flags in the parser data, not from documentation
- Terminal names MUST be valid Python identifiers (no `x=y?`, no `NaN/Path/Refnum?`)
- Template expressions MUST use `in_N` index references matching the OBSERVED connector pane indices
- Include ALL terminals that appear in the actual VI data, including error clusters
- `python_code` dict keys match output terminal names
- Mark `"verified": true` only if indices confirmed from observed connections
- The parser reports **element types** for array terminals (e.g., NumUInt8 for Array of UInt8). Don't confuse element types with scalar types — check the output terminal types and wiring context

## Step 7: Re-run generation

```bash
lvkit generate "<vi-path>" -o "<output-dir>" --search-path "<library-path>"
```

If the same primitive fails again, the terminal matching is wrong — go back to Step 1.
If a NEW primitive fails, start this process again for that one.

## Placeholder entries (`"placeholder": true`) — LAST RESORT ONLY

If after completing ALL steps 1-5 you **cannot identify the primitive**, you may add a placeholder entry. This is the LAST RESORT — only after:
1. You ran Step 2 and checked all instances across the available corpus
2. You ran Step 3 and traced context beyond immediate neighbors
3. You checked the primResID range and searched documentation thoroughly
4. You asked the user if they recognize the terminal signature

A placeholder allows generation to proceed with a warning instead of crashing:

```json
"PRIM_ID": {
    "name": "Unknown Category Primitive PRIM_ID",
    "placeholder": true,
    "terminals": [...all terminals from the graph...],
    "python_code": "pass"
}
```

Placeholder entries emit a `warnings.warn()` and generate `pass` + a TODO comment. They NEVER silently succeed — they always leave a visible marker in the output.

**Alternative: soft codegen mode.** Instead of authoring a placeholder, you can re-run `lvkit generate --placeholder-on-unresolved`. The generated Python contains an inline `raise PrimitiveResolutionNeeded(...)` statement with the same diagnostic context. You can fix the gap contextually in the Python (or come back and write a real mapping later).

**You MUST run this skill for EVERY unknown primitive.** No exceptions. No skipping steps. No adding placeholders without completing the full investigation first.

## NEVER do these things

- NEVER guess a function name from terminal SHAPE (input/output count) alone — the I/O TYPES, especially the output and array element types, are the discriminator
- NEVER label an `Array → scalar` op "Array Size" unless the output is an integer count; an element-typed output (`Array[DBL] → DBL`) is a reduction (sum/product/max), not a count
- NEVER trust an existing `primitives.json` label — duplicate names (e.g. several "Array Size") signal earlier shape-based guesses; re-verify by output type
- NEVER identify a primitive by resID alone when its XML class has a specialized handler — the same resID can mean different ops across classes (codegen resolves node_type before resID)
- NEVER say "polymorphic" to explain away type mismatches — ask the user
- NEVER copy a name from another entry because "it looks similar"
- NEVER fill python_code without confirming semantics from the documentation
- NEVER assume terminal indices from documentation listing order — always confirm from observed wiring in Steps 2-3
- NEVER assume a primResID maps to a different function based on terminal types — primitive polymorphism must be observed in data or confirmed by the user
- NEVER skip the context check (Step 3) — it reveals what the function actually does
- NEVER batch-fill entries — one at a time, fully verified
- NEVER add a placeholder without completing ALL steps 1-5 first
- NEVER skip running this skill — it is MANDATORY for every unknown primitive
- NEVER write user-mode mappings into lvkit's `data/` (cleanroom contamination)
- NEVER write lvkit-mode mappings into a user project's `.lvkit/` (wrong destination)
