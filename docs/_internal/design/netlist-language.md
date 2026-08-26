# lvnet — the netlist language (design spec)

> **Status:** reconstructed 2026-08-26 from the design conversation
> (session `38451279`) after the original uncommitted draft was lost. This is
> the durable record of the language we designed; it is **not yet implemented**
> — the shipped `render_netlist` still emits the old `gamma`/`:=` form. Sections
> marked **OPEN** were never finalized. Companion durable record:
> `memory/project_netlist_is_a_schematic.md`.

## 1. What it is, and why

A **generated, lossless, textual IR of a VI**. LabVIEW dataflow *is* a schematic
— nodes are parts, wires are nets, everything runs concurrently — so the netlist
represents it as connectivity (parts + named nets), never as sequential `:=`
assignments.

**Prior art: FIRRTL** ("Flexible Intermediate Representation for RTL", UC
Berkeley / Chisel). We borrow its *structure* — typed ports, node instances,
connections, registers, `when`/`else` ≈ case frames, primitive-op nodes — but
write **100% LabVIEW vocabulary**. You do **not** need to know FIRRTL to read an
lvnet file; none of FIRRTL's words survive into the surface. The analogy is
`VHDL : FIRRTL ≈ C : LLVM IR` — our netlist is *generated from* the VI, so an IR
(FIRRTL) is the right model, not a hand-written HDL (VHDL). Rejected alternatives:
SPICE/BLIF (no types/control flow), EDIF (LISP-y, machine-only), gate-level
Verilog (no control flow), Yosys RTLIL (verbose, tool-facing).

**Lossless only if emitted from the graph.** The current emitter builds from the
`Operation` projection — a codegen-oriented *thumbnail* that has already dropped
facts (disable-structure kind, `displayed_frame`, hidden loop terminals, Event
Filter-vs-Data, local-variable read/write, `primResID`-as-data, the `wiring_rule`
tri-state → a bool, structured `LVType` → one opaque string). So a lossless
lvnet **must emit straight from the full `GraphNode` model + `VIContext`
facets** — never `Operation`, never `describe --format json` (which collapses the
tri-state to `required: bool`). "No reason to start lossy — start full and throw
away per format on the way out." The correctness gate is a round-trip:
`reparse(emit_verbose) == graph`.

## 2. Document skeleton

```
vi <VIName.vi> :
  in   <name> : <Type> [<requirement>] [default <value>]
  out  <name> : <Type>
                                        # blank line
  <body — nodes and structures, 2-space indent per scope level>
                                        # blank line
  <out name> = <source-net>            # boundary outputs driven at the bottom
```

- **`vi <name> :`** header (top-level VI). A VI with an empty connector pane
  (a top-level/main VI) simply has no `in`/`out` lines.
- **Boundary block:** the connector-pane terminals — `in ` lines then `out`
  lines. A boundary input whose default is notable renders it into the name:
  `in error in (no error) : Error`.
- **Body** in graph order (`_node_order_key`).
- **Boundary outputs** are declared at the top *and driven at the very bottom*
  (`error out = case0.out0`). So an input appears twice (declared, then read),
  an output twice (declared, then driven) — follow either by its net name.
- **Indentation is structural** — 2 spaces per scope level; blocks open with a
  `… :` header and nest by indent (enables editor folding for free).

## 3. The terminal line (core grammar)

A terminal line composes up to four parts:

```
<in |out> <name> : <Type> [<requirement>] [ = <driver> | default <value> ] [; <annotation>]
```

- **`in `/`out`** — 3-char, space-padded keyword on *every* terminal line, at
  the VI boundary and on each node. A node's outputs list `out <name> : <Type>`
  with **no** driver (their value flows out by net name).
- **`: <Type>`** — the faithful LVType on **every** terminal, wired or not
  (§8). Never dropped, never a Python type.
- **`<requirement>`** — the three-state connector `wiring_rule` (§5).
- **wired vs unwired** — the operator (§4).
- **`; <annotation>`** — trailing comment (§6).

## 4. The binding operator `=` (the decisive rule)

`=` means **"connected to a driver," and nothing else.** The default is a
separate word, so unwired is never disguised as a wire to a signal named `""`.

```
in   TestCase in : TestCase.lvclass = listAllTestMethods.vi.TestCase out   # net (a signal)
in   methodName  : String           = "runTest"                            # a constant
in   GUID        : String           default ""                             # UNWIRED → default
```

- **`= <net>`** — wired to a **signal**. Net names are *identifiers*
  (`loop0.shift0`, `listAllTestMethods.vi.TestCase out`).
- **`= <literal>`** — wired to a **constant**. Constants are *literals*
  (`"runTest"`, `5`, `True`). Same `=`, because a constant *is* a driver.
- **`= <constant-node-name>`** — wired to a named `constant` node (`= GUID#1`).
- **`default <value>`** — **no wire.** The terminal falls back to its default;
  no `=`, because nothing is connected. For a type with no literal default:
  `default (default TestSuite.lvclass)`.

**Net vs. constant is told apart lexically (the Verilog convention):** a literal
(quoted string / number / boolean) is a constant; an identifier is a net.

**Direction is `sink = source`** — target on the left, source on the right —
because a terminal reads like a named argument (`f(name = "runTest")`), the most
universal target-left pattern in text languages. **Not `<=`** (reads as ≤, HDL
muscle-memory), **not `->`** (target-on-right, reads like a pipe). Tracing does
not depend on the operator: you follow a wire by **searching its net name**,
which is direction-independent.

**Locked rule:** `=` always means "connected to a driver" (net or constant);
`default` means "not connected."

## 5. The three-state connector (`wiring_rule`) — an orthogonal axis

A connector-pane terminal's **wiring requirement** — `required` / `recommended`
/ `optional` (and `unknown`) — is a **separate axis** from whether it is wired.
`models.py:294` carries the full tri-state; `describe --format json` collapses it
to a `required` bool (which is exactly *why* the emitter must read the graph).

Rendered as a **bare keyword after `: <Type>`**, composing on the same line with
the wired/unwired part:

```
in   TestLoader in       : TestLoader.lvclass   required
in   TestCase            : TestCase.lvclass     recommended
in   error in (no error) : Error                optional   default (no error)
```

The two axes are independent: a terminal can be `required` **and** unwired
(`required default …` = a broken/required wire), or `optional` and wired
(`optional = …`). An unwired *required* input is a broken wire; an unwired
*optional* one just takes its default — the netlist shows which is which. The
tri-state is part of the lossless surface (verbose); its treatment at subVI call
sites is verbose-only nuance (terse may omit it there).

## 6. Trailing `;` annotations

| Annotation | Meaning |
|---|---|
| `; ./path` | a file-backed node's project-relative path (click-to-jump nav), e.g. on a `subVI`/typedef-constant header |
| `; <Qualified:Name>` | the qualified identity as a comment on a node header |
| `; inverted` | a Boolean input wired through inversion |
| `; unconnected` | an unwired **node output** — net declared, no reader |
| `; unread` | an unread **control** — a source with no sink |

`; unwired` is **superseded** by the `default <value>` keyword (the keyword makes
unwired obvious, so the comment is redundant). `#` is reserved for a future
legend/section line. (`; unconnected`/`; unread` are decided in principle; exact
final form not yet sample-confirmed.)

## 7. Nodes

Every node — even a primitive — presents its own typed terminals wired by nets,
exactly as it appears on the diagram. **There is no separate `component`/interface
block** (that was a rejected FIRRTL import; "component" isn't a LabVIEW word).
Two calls to the same subVI each show their own wiring.

| Node | keyword | rendering |
|---|---|---|
| SubVI call | `subVI` | `subVI <Qualified:Name.vi>` header + `; ./path` nav comment, then its `in`/`out` terminal lines. Identity is **always fully qualified**. |
| Primitive | `function <LabVIEW name>` | Rendered **exactly like a subVI**: `function Add`, `function Index Array`, `function Bundle By Name`, with typed terminals wired by nets. Interface from `primitives.json` **plus per-call parsed types** (primitives are polymorphic); variadic ones (Compound Arithmetic, Build Array) show their actual per-call terminals. **No inline operators** — LabVIEW has an `Add` *node*, so we show a node (keeps fidelity and diffs cleanly). |
| Property Node | `property-node` | drawers run top-to-bottom (sequential). *(per-drawer line syntax: OPEN)* |
| Invoke Node | `invoke-node` | method + params. *(line syntax: OPEN)* |
| Feedback Node | `feedback-node` | a `z^-N` register off the loop border (init/each), net `fbK`. *(line syntax: OPEN)* |
| Constant | `constant` | `constant <name>#<n> : <Type> = <value>` (e.g. `constant GUID#1 : String = "TC-001"`). A one-off literal stays inline on the terminal; a shared/named constant becomes a `constant` node referenced by net. |
| Local / Global Variable | `local-variable` / `global-variable` | **a terminal, not a node** — a tap on a control/indicator's named net. A **read = source**, a **write = sink**. |
| Control / Indicator (internal) | `control` / `indicator` | a front-panel control/indicator **not** on the connector pane → a body node. `control` = source, `indicator` = sink. |
| Formula Node | `formula-node` | C-like text body; the `script` is lossless-required. *(rendering: OPEN)* |
| Free label / comment | `comment` | non-executing text. |

**Boundary vs. internal front panel:** an FP control/indicator **on** the
connector pane is the `vi` block's `in`/`out`. One **off** the connector pane is
an internal `control`/`indicator` **node** in the body. Local/global variables
are extra taps on that net. **Multiple writes to one control = one net, several
drivers** (LabVIEW's race/ordering) — surface it plainly, don't hide it.

## 8. Structures

Each structure is a node that presents its outputs on its own header; **each
frame declares what it drives onto the structure's output nets, inside the
frame** (never hoisted to a bottom-of-block merge).

| LabVIEW structure | keyword | frames |
|---|---|---|
| Case Structure | `case <selector-net> :` | `frame "<value>" :` per case (default frame keyed by its value) |
| For Loop | `for-loop :` | single implicit body (index `i`, count `N`) |
| While Loop | `while-loop :` | single implicit body (stop/continue on `cond`) |
| Flat Sequence | `flat-sequence :` | `frame [0] :`, `frame [1] :`, … |
| Stacked Sequence | `stacked-sequence :` | numbered frames, one drawn at a time |
| Event Structure | `event-structure :` | `frame "<event>" :` per event case |
| Diagram Disable | `diagram-disable :` | `frame Enabled/Disabled :` |
| Conditional Disable | `conditional-disable :` | `frame "<symbol cond>" :` |
| Type Specialization | `type-specialization :` | `frame [i] :` |
| In Place Element | `in-place-element :` | decompose / recompose (no control flow) *(line syntax: OPEN)* |
| Timed Loop / Sequence | `timed-loop` | **PROPOSED — coverage unverified in the model** |

`for-loop`/`while-loop`, **not** bare `for`/`while` — deliberately, so a reader
from text languages doesn't import C's `init; cond; incr` (a LabVIEW For Loop
runs `N` times / auto-indexes; a While Loop checks its condition at the end).

### Border constructs

```
shift-register loop0.shift0 :
  init = <source>          # value on the first iteration
  each = <source>          # value fed to the next iteration
tunnel loop0.out0 : auto-indexing = <source>   # mode: auto-indexing | last-value | concatenating | pass-through [+ conditional]
case0.out0 = <source>      # a case output, driven inside each frame
```

## 9. Net naming

**One name per net, spelled identically at its driver and every reader** — follow
a wire by grepping its name and land once (VHDL-like "one name → one
declaration"). No scope-local abbreviation that makes `out0` mean different nets
in different places.

- Node-port nets: `<node>.<port>` (`listAllTestMethods.vi.TestCase out`).
- Case output nets: `caseN.outK`. Shift-register nets: `loopN.shiftK`. Loop
  output-tunnel nets: `loopN.outK`. Feedback nets: `fbK`.
- **Occurrence `#n`:** a node/constant whose display name repeats is disambiguated
  with `#N` (also the stable diff identity).
- **Ambiguity qualification:** node **identity** is always fully qualified
  (`subVI TestCase.lvclass:listAllTestMethods.vi`); the **net prefix** uses the
  shortest-unique form — the bare filename when unambiguous within the VI,
  falling back to the qualified name/occurrence on any collision
  (`Lib1.lvclass:Do.vi.result` vs `Lib2.lvclass:Do.vi.result`).

## 10. Types

Faithful LVType on every terminal, **never a Python type**:

- Scalars: `DBL`, `I32`, `Boolean`, `String`, `Path`.
- Error cluster: `Error` (default `(no error)`).
- Array: `[T]` — `[String]`, `[LabVIEW Object]`.
- Cluster: `Cluster{…}` — `Cluster{TestSuite, suiteStatus}`.
- Enum: `Enum{a,b,c}`.
- Class: `MyClass.lvclass` (qualified).
- Refnum: `refnum{…}` — `UserEvent refnum{suiteStatusChanged--Cluster{TestSuite, suiteStatus}}`.
- Typedefs referenced by qualified name, never expanded inline (full field
  expansion is verbose-only).

Complex-constant literal *values* (a cluster/array/enum/path constant's field
values inline) are **OPEN** — the *types* render, the literal-value syntax was
never pinned.

## 11. verbose vs terse

Only **netlist + JSON** have the two modes and are lossless targets. **`text` is
human-only, NOT a lossless target** — its "losses" (no wiring, no locals) are by
design; an agent needing full understanding uses netlist/JSON.

- **Terse (default) = structurally understandable.** The *complete dataflow
  program* — every node, terminal, wire, type, value, structure, control flow —
  is always present; terse never drops anything that changes what the VI does.
  Plus only consequential VI settings (`inline`-true, non-default reentrancy).
- **Verbose (`-v`) = lossless (round-trips to the graph).** Terse **plus** the
  authoring/cosmetic detail: all VI properties (Window/execution flags,
  `inlinable`, `allow_debugging`, `priority`, reentrancy) + health; node
  labels/captions/descriptions & comment text; positions/decorations; constant
  display formats & raw values; a *wired* terminal's now-unused default; hidden
  loop terminals revealed; full cluster/enum type expansions; the `wiring_rule`
  nuance at call sites; class-context fields/methods; property/invoke detail;
  `poly_variant_name`; `primResID` as data; disable-structure `kind`;
  `displayed_frame`/`active_frame`/`case_insensitive`; Event Filter-vs-Data;
  local-var `is_write`/`control_terminal_id`; `VIContext.description`.

Terse is a *documented reduction* of the proven-lossless verbose, never its own
thing. The exact per-construct terse reduction is otherwise **OPEN**.

## 12. JSON form

The same model, serialized. Top level: `vi`, `connector_pane.terminals[]`
(`name`/`type`/`direction`/`index`/`required`/`default`), `body[]`, `outputs[]`
(`name`/`source`), plus `properties`/`health`/`class_context`. A body item's
`kind` is `"instance"` or `"scope"`. A scope carries `scope_kind`
(`case`/`for`/`while`/`sequence`/`disabled`/`event`), `selector`, `frames[]`
(`label`, `value` stable key, `is_default`, `passthrough`, `body[]`), and
`outputs[]` carrying the merge syntheses with **internal** `kind` tags —
`"select"` (case output), `"shift"` (shift register), `"collect"` (loop output
tunnel). **These `select`/`shift`/`collect` names survive only as internal JSON
tags** — they are rendered into the LV-native surface (`shift-register` /
`tunnel` / per-frame `caseN.outK =`) and never appear as words in lvnet text.
(The tri-state `wiring_rule` and structured `LVType` must be surfaced here in
verbose, not collapsed to `required: bool` / one opaque string.)

## 13. Diff

The diff is a **structured tree on the same netlist model**, not primarily a
gutter-annotated text. Reshape the netlist and you reshape the diff tree — they
are designed together. Named nets + stable node identity (the qualified name /
`#N` designator) make a diff **proportional to the edit**: an added node is one
added block; a rewire is one changed line ("err2 now feeds err_carry instead of
X" = a single tree node); nothing cascades. An explicit `+`/`-`/`~` gutter for
lvnet *text* was not designed — **OPEN** if needed.

## 14. Presentation (a view concern, not a second IR)

One canonical lossless IR; density lives in the viewer.

- **Folding** — the text is block-structured by indentation, so the viewer
  collapses a `subVI`, a `frame`, a whole `case`/`for-loop` for free.
- **Optional compact one-liner** — a viewer toggle, not a different IR: one line
  per node, wired ports inline, unwired-defaults hidden until expanded.

Density never costs data.

## 15. `.lvnet` file + syntax coloring

- **`.lvnet`** is the format name and the syntax-highlighting trigger. The
  read-only text view's virtual doc takes a format suffix — `.lvnet` (netlist) /
  `.json` (IR) / `.txt` (description).
- **We ship no colors.** A TextMate grammar (`syntaxes/lvnet.tmLanguage.json`)
  tags tokens with **standard scopes** — `entity.name.function` (node functions),
  `keyword.control`, `variable.other` (nets), `entity.name.class` (callees/
  classes), `string`, `comment`, `keyword.operator` — and the reader's own theme
  paints them.
- Delivered as a **read-only native text tab on a custom URI scheme, opened by a
  command/button** — not a "Reopen With" entry, not a webview (only a
  custom-scheme `TextDocumentContentProvider` doc can carry the `.lvnet` grammar
  and native theme colors). A `language-configuration.json` gives bracket-match +
  folding.

## 16. Golden reference render

`loadTestsFromTestCase.vi`, the canonical example to build against:

```
vi loadTestsFromTestCase.vi :
  in   TestLoader in       : TestLoader.lvclass
  in   TestCase            : TestCase.lvclass
  in   error in (no error) : Error
  out  TestLoader out      : TestLoader.lvclass
  out  TestSuite           : TestSuite.lvclass
  out  error out           : Error

  case error in (no error) :
    frame "No Error" :
      subVI listAllTestMethods.vi
        in   TestCase in  : TestCase.lvclass = TestCase
        in   error in     : Error            = error in (no error)
        out  TestCase out : TestCase.lvclass
        out  test methods : [String]
        out  error out    : Error
      for-loop :
        constant GUID#1 : String = "TC-001"
        subVI TestCase_Init.vi
          in   TestCase in  : TestCase.lvclass = listAllTestMethods.vi.TestCase out
          in   methodName   : String           = listAllTestMethods.vi.test methods
          in   GUID         : String           = GUID#1
          in   error in     : Error            = loop0.shift0
          out  TestCase out : TestCase.lvclass
          out  error out    : Error
        shift-register loop0.shift0 :
          init = listAllTestMethods.vi.error out
          each = TestCase_Init.vi.error out
        tunnel loop0.out0 : auto-indexing = TestCase_Init.vi.TestCase out
      subVI TestSuite_Init.vi
        in   TestSuite in : TestSuite.lvclass default (default TestSuite.lvclass)
        in   tests        : [LabVIEW Object] = loop0.out0
        in   GUID         : String           default ""
        in   error in     : Error            = listAllTestMethods.vi.error out
        out  TestSuite out: TestSuite.lvclass
        out  error out    : Error
      case0.out0 = TestSuite_Init.vi.error out
      case0.out1 = TestLoader in
      case0.out2 = TestSuite_Init.vi.TestSuite out
    frame "Error" :
      case0.out0 = error in (no error)
      case0.out1 = TestLoader in
      case0.out2 = (default TestSuite.lvclass)

  TestLoader out = case0.out1
  TestSuite      = case0.out2
  error out      = case0.out0
```

## 17. Open items (never finalized)

1. **VI properties / health / class-context block syntax** in verbose lvnet text
   — required for losslessness, but no concrete block format was rendered.
2. **Per-construct terse reduction** — categories known (§11), not field-by-field.
3. **Auto-indexing input-side marker** — the output tunnel shows `auto-indexing`;
   the array-indexed *input* side still shows the whole-array net and needs a
   matching marker.
4. **Degenerate tunnel collapse** — an all-frames-identical tunnel: collapse to a
   plain wire vs. show faithfully per frame.
5. **Complex constant literal values** (cluster/array/enum/path field values).
6. **Concrete line syntax** for property-node drawers, invoke-node params,
   feedback-node `z^-N`, in-place decompose/recompose, formula-node script.
7. **Not-yet-modeled constructs** — `timed-loop`, `global-variable`, Shared
   Variable, Call By Reference, MathScript (proposed keywords, unverified).
8. **lvnet text diff gutter** (`+`/`-`/`~`) — the diff was designed as a tree.
9. **`; unconnected` / `; unread`** dangling annotations — decided in principle,
   not sample-confirmed in the final form.

## 18. Implementation notes

- **New renderer, not a rename.** Nothing shipped emits this surface;
  `render_netlist` still produces the old `gamma`/`:=` form. The basis is the
  prototype `.tmp/ir_to_lvnet.py` (JSON → lvnet), which is stale on the operator
  only (`<=` → `=`) and still carries the internal `select`/`shift`/`collect`
  dispatch tags.
- The committed **Phase-1 `build_netlist_from_graph`** builds the *model*; the
  *surface* (this language) is what changes on top of it.
- **Emit from `GraphNode` + `VIContext` facets**, not `Operation`, not
  `describe --format json` — the losslessness gate is `reparse(emit_verbose) ==
  graph`.
- The **same model backs the diff tree**, so the netlist and the diff are
  designed together (stable node uid + net name).
