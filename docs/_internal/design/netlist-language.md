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
  uses :                                 # dependency manifest (§7a), OPTIONAL -- omitted with no deps
    <kind> <qualified-identity>   [; ./path]
  in   <name> : <Type> [<requirement>] [default <value>]     # boundary terminal line (§3)
  out  <name> : <Type>
                                        # blank line
  <keyword> <handle> : <component>       # a node's DECLARATION line (§7)
    in   <name> : <Type> [= <driver> | default <value>]      # its terminal lines (§3)
    out  <name> : <Type>
  <structure> :                          # a structure opens an indented block (§8)
    …
                                        # blank line
  <out name> = <source-net>            # boundary outputs driven at the bottom
```

- **`vi <name> :`** header (top-level VI). A VI with an empty connector pane
  (a top-level/main VI) simply has no `in`/`out` lines.
- **`uses :`** dependency manifest (§7a) — a plain reference list of every
  external file this VI directly depends on, right after the header.
  Present in BOTH terse and verbose (it's the first "element" of the
  terse/verbose design — a later, verbose-only element adds each
  dependency's own inline interface/type structure on top). Omitted
  entirely when the VI has no dependencies. LAYOUT IS PROVISIONAL: this
  placement (right after the header) is where it lands until every element
  of the design exists and the maintainer picks final section ordering.
- **Boundary block:** the connector-pane terminals — `in ` lines then `out`
  lines. A boundary input whose default is notable renders it into the name:
  `in error in (no error) : Error`.
- **Body** in graph order (`_node_order_key`).
- **Boundary outputs** are declared at the top *and driven at the very bottom*
  (`error out = case0::out0`). So an input appears twice (declared, then read),
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

The other core line is the **node-declaration line** — the header that names a
node and (for most kinds) owns a block of terminal lines:

```
<kind> <handle> [ : <more-specific-type> ] [ (<attributes>) ] [; <annotation>]
```

- **`<kind>`** — the node category (`subVI`/`function`/`constant`/`property-node`/
  `feedback-node`/…), **always present**. It's the invariant discriminator: it
  tells you how to read the rest *before* you read it, it's greppable, and it's the
  only identifier a node with no more-specific-type (a register) has.
- **`<handle>`** — the instance label (§9).
- **`: <more-specific-type>`** — OPTIONAL: *which* one within the kind — the VI a
  `subVI` calls, the primitive an `Add` is, a `constant`'s data type, the object a
  `property-node` acts on (§7). A **state register** (`shift-register`,
  `feedback-node`) has none, so its `:` simply opens the block.
- **`(<attributes>)`** — OPTIONAL parenthetical settings that are *not* an identity
  — a feedback node's `(1 iteration)`, a boundary control's notable default
  `(no error)`.

Its indented `in`/`out` terminal lines (or `init`/`each`) follow.

## 4. The binding operator `=` (the decisive rule)

`=` means **"connected to a driver," and nothing else.** The default is a
separate word, so unwired is never disguised as a wire to a signal named `""`.

```
in   TestCase in : TestCase.lvclass = listAllTestMethods_1::TestCase out   # net (a signal)
in   methodName  : String           = "runTest"                           # a constant
in   GUID        : String           default ""                            # UNWIRED → default
```

- **`= <net>`** — wired to a **signal**. Net names are *identifiers*
  (`loop0::shift0`, `listAllTestMethods_1::TestCase out`).
- **`= <literal>`** — wired to a **constant**. Constants are *literals*
  (`"runTest"`, `5`, `True`). Same `=`, because a constant *is* a driver.
- **`= <constant-node-name>`** — wired to a named `constant` node (`= GUID_1`).
- **`default <value>`** — **no wire.** The terminal falls back to its default;
  no `=`, because nothing is connected. On a **terminal line specifically**
  (it already has its own `: <Type>` column to read the type from): a type
  *with* a literal default shows it (`default ""`, `default 0`); a type with
  **no** literal default (a class/refnum reference) shows the bare word
  `default` alone — repeating the type (`default (default TestSuite.lvclass)`)
  would just echo the same column twice. A **drive-position** default (no
  type column of its own — a case-output tunnel left unwired in one frame,
  `case0::out2 = (default TestSuite.lvclass)`) keeps naming the type, since
  there's nothing else on that line to read it from.

**Net vs. constant is told apart lexically (the Verilog convention):** a literal
(quoted string / number / boolean) is a constant; an identifier is a net.

**A string literal is double-quoted with standard backslash escapes** —
`\\`, `\"`, `\n`, `\r`, `\t`; any other C0 control char (U+0000–U+001F) as
`\xHH` — so a real LabVIEW string constant carrying a raw CR/LF (a
multi-line UI-status default, a CRLF delimiter) still renders on ONE
physical line, the way every other lvnet construct does (§10, §17 item 5).

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
| `; inverted` | a Boolean input wired through inversion |
| `; unconnected` | an unwired **node output** — net declared, no reader |
| `; unread` | an unread **control** — a source with no sink |

`; unwired` is **superseded** by the `default <value>` keyword (the keyword makes
unwired obvious, so the comment is redundant). **`#` begins a comment** — a
line-scope cousin of the trailing `;` annotation, and the marker for an OPEN
construct's `# TODO(lvnet): …` placeholder (§17). `#` is **never** an occurrence
marker: the instance number is `_N` (§7/§9), which reads as a continuation of the
identifier (a variable-like label) and leaves `#` for comments alone.
(`; unconnected`/`; unread` are decided in principle; exact final form not yet
sample-confirmed.)

## 7. Nodes

Every node — even a primitive — presents its own typed terminals wired by nets,
exactly as it appears on the diagram. **There is no separate `component`/interface
block** (that was a rejected FIRRTL import; "component" isn't a LabVIEW word).
Two calls to the same subVI each show their own wiring.

**Instance handles ("labeling copies of an IC").** Each node is *declared* with a
short **instance handle** bound to its faithful component —
`<keyword> <handle> : <component>` — and every net references the **handle**, not
the component (§9). Like reference designators on a schematic (two copies of one
IC → `U1`, `U2`, the part number annotated once per copy), the handle names the
*instance* while the component names the *part*. This is **not** the rejected
component block: each instance still declares itself inline and shows its own
wiring; the handle just gives its copies distinct, variable-like names.

**The handle** is *our* label: the node's display name with its **file extension
stripped** and **spaces replaced by `_`**, suffixed **`_N`** for every instance —
including the first (schematics don't leave `U1` blank). The faithful
**component** — a subVI's fully-qualified `Class.lvclass:Name.vi`, a primitive's
LabVIEW name — is spelled **once**, at the declaration, so the verbose qualifier
never repeats on every wire and the handle carries no `.vi`/class (that is the
declaration's job, and answers "which class/library?" authoritatively). The
faithful identity lives on the declaration line, so the handle is a display
designator only — round-trip reads the component from the `:` clause, never by
parsing the handle.

**Every producing node gets a handle** (§9), CLOSED or OPEN — so a net pointing
at any node is uniform (`propRef_1::Value`) and a downstream reader never has to
special-case its source. Naming the wire and drawing the node's insides are
separate problems: the handle solves the first for *all* kinds; the second is
designed per kind. `property-node`, `invoke-node`, and `feedback-node` now have a
designed rendering (rows below) — they emit like any other node. Still pending
only their *special content* (handle + terminals still render): `formula-node`
(the `script` body — needs the `script` field plumbed), `local`/`global-variable`
(tap resolution to the control's net), `in-place-element` (the
decompose↔recompose pairing). Those emit their handle + typed terminals with a
`# TODO(lvnet): …` on the one undesigned part — never an invented form.

| Node | keyword | rendering |
|---|---|---|
| SubVI call | `subVI` | `subVI <handle> : <Qualified:Name.vi>` header (+ `; ./path` nav comment), then its `in`/`out` terminal lines. The **handle** (`<despaced-name>_N`, §9) names the instance; the **component** after `:` is **always fully qualified** and spelled only here (e.g. `subVI listAllTestMethods_1 : TestCase.lvclass:listAllTestMethods.vi`). |
| Primitive | `function <handle> : <LabVIEW name>` | Rendered **exactly like a subVI** — `function Add_1 : Add`, `function Index_Array_1 : Index Array`, `function Bundle_By_Name_1 : Bundle By Name` — handle left of `:`, faithful primitive name right of it, then typed terminals wired by nets. Interface from `primitives.json` **plus per-call parsed types** (primitives are polymorphic); variadic ones (Compound Arithmetic, Build Array) show their actual per-call terminals. **No inline operators** — LabVIEW has an `Add` *node*, so we show a node (keeps fidelity and diffs cleanly). |
| Property Node | `property-node <handle> : <ObjectClass>` | a property's value terminal IS a terminal: a **read** renders as an `out` (value flows out), a **write** as an `in` (`= <driver>`), each named by the property; drawer order = line order (top-to-bottom sequential). No special syntax — the standard terminal block carries it. |
| Invoke Node | `invoke-node <handle> : <ObjectClass>.<Method>` | the method IS the node's identity, so it sits in the component; parameters render **by index** (`in  0 : <Type> = <net>`, `out 1 : <Type>`) because LabVIEW stores no param names in the VI (only the VI-server signature does). |
| Feedback Node | `feedback-node <handle> (<N> iteration[s]) :` | a state **register** — no more-specific-type (like a shift register), so the `:` just opens its `init = <src>` (first iteration) / `each = <src>` (fed back next) block. The count — how many iterations back the value is handed — rides as a parenthetical **attribute**: `(1 iteration)` / `(3 iterations)`. **Not** `delay` (reads as time) or `z^-N` (DSP glyph). Handle is its `fbK` net; `(? iterations)` when the depth didn't parse (LabVIEW enforces ≥1). |
| Constant | `constant` | `constant <handle> : <Type> = <value>` (handle `<name>_N`, e.g. `constant GUID_1 : String = "TC-001"`). A one-off literal stays inline on the terminal; a shared/named constant becomes a `constant` node referenced by net (`= GUID_1`). |
| Local / Global Variable | `local-variable` / `global-variable` | **a terminal, not a node** — a tap on a control/indicator's named net. A **read = source**, a **write = sink**. |
| Control / Indicator (internal) | `control` / `indicator` | a front-panel control/indicator **not** on the connector pane → a body node. `control` = source, `indicator` = sink. |
| Formula Node | `formula-node` | C-like text body; the `script` is lossless-required. *(rendering: OPEN)* |
| Free label / comment | `comment` | non-executing text. |

**Boundary vs. internal front panel:** an FP control/indicator **on** the
connector pane is the `vi` block's `in`/`out`. One **off** the connector pane is
an internal `control`/`indicator` **node** in the body. Local/global variables
are extra taps on that net. **Multiple writes to one control = one net, several
drivers** (LabVIEW's race/ordering) — surface it plainly, don't hide it.

## 7a. The `uses :` dependency manifest

The first "element" of the terse/verbose design: a plain reference list of
every external FILE this VI directly depends on — a subVI it calls, a
referenced typedef (`.ctl`), or a referenced class (`.lvclass`) — each once,
right after the `vi <name> :` header (§2; LAYOUT PROVISIONAL — the maintainer
decides final section placement once every element of the design exists).
Present in BOTH terse and verbose. Omitted entirely when the VI has no
dependencies (never an empty `uses :` header).

```
uses :
  <kind> <qualified-identity>   ; ./<project-relative-path>
```

- **`<kind>`** — derived from the identity's file extension, never guessed
  independently: `.vi` → `subVI`, `.ctl` → `typedef`, `.lvclass` → `class`.
- **`<qualified-identity>`** — the SAME fully-qualified identity a `subVI`/
  `class`/`typedef` component spells at its own declaration elsewhere in the
  file (§7/§9) — e.g. `TestCase.lvclass:listAllTestMethods.vi`,
  `TestLoader.lvclass`.
- **`; ./path`** — the §6 project-relative nav annotation, omitted (not
  fabricated as an empty/guessed value) when the dependency's recorded or
  searched reference doesn't resolve to a real on-disk file.
- **Sorted by qualified identity** for byte-reproducibility.
- **Verbose-only: each `subVI` entry inlines its own connector-pane
  interface** right under its line — the later element the bullet above used
  to forward-reference, now implemented. Terse omits it (the plain reference
  list only, as above); this is a documented addition — the syntax was not
  pinned by an existing golden example, so it is spelled out here rather than
  guessed:

  ```
  uses :
    subVI <qualified-identity>   ; ./<project-relative-path>
      in  <name> : <Type>
      out <name> : <Type>
  ```

  One `in`/`out` line per connector-pane terminal (inputs then outputs, same
  canonical pane order as everywhere else, §3's shape reused verbatim), at one
  indent level deeper than the entry's own line (2 further spaces) — no `=
  <driver>` clause and no §5 requirement keyword: this is the dependency's
  own SIGNATURE, not a call site's wiring (a call site's actual bindings
  still render under its own `subVI <handle> : ...` instance block in the
  body, §7). A `class`/`typedef` entry never gets one (a connector pane is a
  VI-only concept), and an unresolved `subVI` dependency (not reachable in
  the loaded graph) renders with no interface block rather than a fabricated
  one — same "omit, never guess" rule as `; ./path` above. This is exactly
  the payload verbose mode needs to rehydrate the MINIMAL graph's own
  leaf-loaded connector pane for each direct dependency straight from the
  text.

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
shift-register loop0::shift0 :
  init = <source>          # value on the first iteration
  each = <source>          # value fed to the next iteration
tunnel loop0::out0 : auto-indexing = <source>   # mode: auto-indexing | last-value | concatenating | pass-through [+ conditional]
case0::out0 = <source>      # a case output, driven inside each frame
```

## 9. Net naming

**One name per net, spelled identically at its driver and every reader** — follow
a wire by grepping its name and land once (VHDL-like "one name → one
declaration"). No scope-local abbreviation that makes `out0` mean different nets
in different places.

- Node-port nets: `<handle>::<port>` — the producing node's **instance handle**
  (§7), a `::` (scope resolution, "the port *within* this instance"), then the
  faithful terminal name, or the terminal's **index** when it is unnamed
  (`listAllTestMethods_1::test methods`, `Not_2::0`). `::` (never `.`) keeps an
  indexed port off the handle's `_N`, so `Not_2::0` can't be misread as the float
  `2.0`, and `::` doesn't pile onto the already four-way-overloaded `:` (type,
  declaration, block header, class qualifier).
- Structure nets use the same `::`: `caseN::outK` (case output), `loopN::shiftK`
  (shift register), `loopN::outK` (loop output tunnel), `fbK` (feedback — a whole
  net, no port). Structure prefixes keep their reserved bare-numbered form — they
  are not node handles. `::` is the **only** instance→port separator, so `.` never
  appears in a net name (it survives only inside a component path at a
  declaration, §7).
- **Instance number `_N`:** every node/constant instance carries `_N` (from 1),
  the first copy included — the handle's uniquifier and the stable diff identity.
  (Replaces the old `#n`; `#` is now the comment marker, §6.)
- **Component identity lives at the declaration** (§7), always fully qualified
  there; the net prefix is the **handle**, unique VI-wide by construction — the
  `_N` uniquifier absorbs both a repeated copy *and* a same-base-name collision
  across classes (`Do.vi` in two `.lvclass`es → `Do_1`, `Do_2`, each declared
  against its own `Lib1.lvclass:Do.vi` / `Lib2.lvclass:Do.vi`). So a net still
  names exactly one instance, spelled identically at its declaration and every
  reader — grep the handle, land once.

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
- **A NAMED enum/ring/cluster/typedef renders by name alone in terse** (the
  only mode implemented so far) — `lveventtype`, `LVPoint32TypeDef`, not
  `lveventtype{Mouse Down, Mouse Up, …}` — recursing into a container
  (`[NamedThing]`, `refnum{NamedThing}`) the same way. This is the SAME move
  as a typedef already getting referenced by name instead of inline-expanded
  (previous bullet) applied uniformly to every named type, not just
  typedefs proper — an *anonymous* type (no name to fall back on) still
  renders its full structural form (`cluster{f1, f2}`, `enum{a,b,c}`), since
  there's nothing else faithful to show. Full member expansion for a NAMED
  type is verbose-only (§11); this is what stops one ~300-member named enum
  from padding every sibling terminal line's column out to match it.

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
`kind` is `"instance"` or `"scope"`. An `"instance"` carries `node_kind`
(`subVI`/`function`/`constant`/…), `handle` (the `_N` instance label), the
fully-qualified `component`, and its `inputs[]`/`outputs[]` ports each with a
faithful `type` (plus any per-node annotations). A scope carries `scope_kind`
(`case`/`for`/`while`/`sequence`/`disabled`/`event`), `selector`, `frames[]`
(`label`, `value` stable key, `is_default`, `passthrough`, `body[]`), and
`outputs[]` carrying the merge syntheses with **internal** `kind` tags —
`"select"` (case output), `"shift"` (shift register), `"collect"` (loop output
tunnel). **These `select`/`shift`/`collect` names survive only as internal JSON
tags** — they are rendered into the LV-native surface (`shift-register` /
`tunnel` / per-frame `caseN::outK =`) and never appear as words in lvnet text.
(The tri-state `wiring_rule` and structured `LVType` must be surfaced here in
verbose, not collapsed to `required: bool` / one opaque string.)

## 13. Diff

The diff is a **structured tree on the same netlist model**, not primarily a
gutter-annotated text. Reshape the netlist and you reshape the diff tree — they
are designed together. Named nets + stable node identity (the instance handle /
`_N` designator) make a diff **proportional to the edit**: an added node is one
added block; a rewire is one changed line ("err2 now feeds err_carry instead of
X" = a single tree node); nothing cascades. An explicit `+`/`-`/`~` gutter for
lvnet *text* was not designed — **OPEN** if needed.

## 14. Presentation (a view concern, not a second IR)

One canonical lossless IR; density lives in the viewer.

- **Folding** — the text is block-structured by indentation, so the viewer
  collapses a `subVI`, a `frame`, a whole `case`/`for-loop` for free.
- **Optional compact one-liner** — a viewer toggle, not a different IR: one line
  per node, wired ports inline, unwired-defaults hidden until expanded.
- **Column alignment is capped, not global-max.** A terminal block's `name`/
  `: <Type>` columns align to the widest entry *under* a cap; an entry over
  the cap overflows on its own line (one guaranteed space before its
  trailing part, never zero) instead of stretching every sibling line's
  column out to match it. This is what a 300-member named enum rendered
  anonymously (or just one long name) would otherwise do to a whole block —
  observed as a single VI render line reaching 1267 characters, almost all
  padding, before this fix (alongside §10's named-type collapsing, which
  usually avoids the anonymous case entirely).

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

This is the ACTUAL, verified output of `render_lvnet(build_netlist_from_graph(...))`
on the real corpus VI (captured via `.tmp/render_golden.py`, byte-identical to
`tests/test_render_lvnet.py`'s `_GOLDEN_LVNET` fixture) -- not hand-typed. It
diverges from an earlier hand-written draft of this section in ways the test
file's module docstring documents with receipts (verbatim FP-control port
labels including their `(...)` annotation; `TestSuite_Init.vi`'s real 5th
input; its real `error in` wiring from `loop0::shift0`; no `constant` node,
since this VI's own diagram has none; one alignment nit) -- none of them are
invented syntax, and none of them are the naming-scheme revision itself.
`TestSuite_Init.vi`'s block also shows this pass's three type-readability
fixes on real data: `testSuiteStatusChanged EventRef`'s refnum collapses to
its inner cluster's NAME (`UserEvent refnum{suiteStatusChanged--Cluster}`,
§10) instead of expanding its fields; that same line and `TestSuite in`
render the bare word `default` (no literal to show, §4); and the block's
own column width is capped, so that one refnum type doesn't stretch the
`tests (none)`/`GUID ("")`/`error in (no error)` lines' `: <Type>` column
out to match it (§14). It now also carries the §7a `uses :` dependency
manifest right after the header -- this VI's real six direct dependencies
(its three SubVI calls plus the three classes its own connector pane is
typed with), sorted by qualified identity, every one resolving to a real
on-disk file in this corpus so every line carries a `; ./path` nav comment.

```
vi loadTestsFromTestCase.vi :
  uses :
    class TestCase.lvclass                       ; ./Classes/TestCase/TestCase.lvclass
    subVI TestCase.lvclass:TestCase_Init.vi      ; ./Classes/TestCase/TestCase_Init.vi
    subVI TestCase.lvclass:listAllTestMethods.vi ; ./Classes/TestCase/listAllTestMethods.vi
    class TestLoader.lvclass                     ; ./Classes/TestLoader/TestLoader.lvclass
    class TestSuite.lvclass                      ; ./Classes/TestSuite/TestSuite.lvclass
    subVI TestSuite.lvclass:TestSuite_Init.vi    ; ./Classes/TestSuite/TestSuite_Init.vi
  in   TestLoader in       : TestLoader.lvclass
  in   TestCase            : TestCase.lvclass
  in   error in (no error) : Error
  out  TestLoader out      : TestLoader.lvclass
  out  TestSuite           : TestSuite.lvclass
  out  error out           : Error

  case error in (no error) :
    frame "No Error" :
      subVI listAllTestMethods_1 : TestCase.lvclass:listAllTestMethods.vi
        in   TestCase in         : TestCase.lvclass = TestCase
        in   error in (no error) : Error            = error in (no error)
        out  TestCase out        : TestCase.lvclass
        out  test methods        : [String]
        out  error out           : Error
      for-loop :
        subVI TestCase_Init_1 : TestCase.lvclass:TestCase_Init.vi
          in   TestCase in            : TestCase.lvclass = listAllTestMethods_1::TestCase out
          in   methodName ("runTest") : String           = listAllTestMethods_1::test methods
          in   GUID ("")              : String           default ""
          in   error in (no error)    : Error            = loop0::shift0
          out  TestCase out           : TestCase.lvclass
          out  error out              : Error
        shift-register loop0::shift0 :
          init = listAllTestMethods_1::error out
          each = TestCase_Init_1::error out
        tunnel loop0::out0 : auto-indexing = TestCase_Init_1::TestCase out
      subVI TestSuite_Init_1 : TestSuite.lvclass:TestSuite_Init.vi
        in   TestSuite in                    : TestSuite.lvclass default
        in   tests (none)                    : [LabVIEW Object]  = loop0::out0
        in   testSuiteStatusChanged EventRef : UserEvent refnum{suiteStatusChanged--Cluster} default
        in   GUID ("")                       : String            default ""
        in   error in (no error)             : Error             = loop0::shift0
        out  TestSuite out                   : TestSuite.lvclass
        out  error out                       : Error
      case0::out0 = TestSuite_Init_1::error out
      case0::out1 = TestLoader in
      case0::out2 = TestSuite_Init_1::TestSuite out
    frame "Error" :
      case0::out0 = error in (no error)
      case0::out1 = TestLoader in
      case0::out2 = (default TestSuite.lvclass)

  TestLoader out = case0::out1
  TestSuite      = case0::out2
  error out      = case0::out0
```

A `constant` node (not exercised by this particular VI, which has none on its
own diagram) would declare and read as:

```
constant GUID_1 : String = "TC-001"
...
in   GUID : String = GUID_1
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
   The §4 backslash-escaping rule covers SCALAR string escaping only (a
   plain String constant/default's own text); a cluster/array/enum/path
   constant's field-value syntax remains OPEN.
6. **Concrete line syntax** — DESIGNED for property-node / invoke-node /
   feedback-node (§7). Still open: in-place-element decompose↔recompose pairing,
   and formula-node script rendering (needs the `script` field plumbed).
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
