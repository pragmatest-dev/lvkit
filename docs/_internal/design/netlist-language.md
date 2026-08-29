# lvnet — the netlist language (design spec)

> **Status:** originally reconstructed 2026-08-26 from the design conversation
> (session `38451279`) after the original uncommitted draft was lost, and now
> **implemented and shipped** as `render_lvnet` (`src/lvkit/graph/render_lvnet.py`)
> — `lvkit describe --format lvnet [-v]` and the MCP `read_vi` tool's
> `format="lvnet"` both produce exactly this surface (§15); a parser
> (`lvnet_parse.py`) and full-model reconstructor (`lvnet_reconstruct.py`)
> round-trip it back to a `NetlistModule` (§1). The OLD `render_netlist`
> (`gamma`/`mu`/`eta`, `:=`) still exists as the deprecated `--format netlist`
> alias. This is the durable record of the language, kept in sync with the
> shipped renderer as it evolves — sections marked **OPEN** are the genuine
> remaining gaps, not the whole document. Companion durable record:
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
away per format on the way out."

**The gate is GRAPH-IDENTITY round-trip, not merely text stability.** It is not
enough for `render_lvnet(reconstruct_module(parse_lvnet(T)), verbose=True) == T`
(byte-identical re-render — a real, necessary gate, but a proxy: two different
models can render to identical text if a field the renderer never reads happens
to differ). The stronger, decisive gate is that `reconstruct_module(parse_lvnet(
render_lvnet(m, verbose=True)))` recovers the SAME graph identity `m` itself
carried — every node/constant/local-variable's own trailing BD `uid`, every
structure's (case/for-loop/while-loop/flat-sequence/stacked-sequence/diagram-
disable/conditional-disable/type-specialization/event-structure) own `uid`, the
connector pane's `pattern_id`, every connector-pane terminal's own pane `index`
(on-pane and off-pane alike), and every net reference resolving to the SAME
producer by identity — not just a model that happens to re-render to the same
bytes. `tests/test_lvnet_identity.py` is this gate; `tests/test_lvnet_
reconstruct.py`'s byte-identity check is kept alongside it as the weaker,
necessary-but-not-sufficient proof. See §8's `(id <uid>)` structure-header
annotation below for the piece that closes the gap this gate exists to catch:
a structure that drives no output net spelling its own uid (every sequence/
disabled/event structure; a case/loop with no output tunnel or shift register)
otherwise has no way to recover its identity from the text at all.

## 2. Document skeleton

The document mirrors LabVIEW's own two-pane mental model — a `front-panel :`
section (the connector pane's identity + boundary terminals) followed by a
`block-diagram :` section (the body) — rather than spelling the boundary
terminals loose under the `vi :` header:

```
vi <VIName.vi> :
  uses :                                 # dependency manifest (§7a), OPTIONAL -- omitted with no deps
    <kind> <qualified-identity>   [; ./path]
  front-panel :                          # OPTIONAL -- omitted when there is nothing to show at all
    pattern : <conId>                    # ConnectorPane.pattern_id, OMITTED when unknown
    in   <name> : <Type> [<requirement>] [default <value>] @<index>   # on-pane boundary terminal (§3)
    out  <name> : <Type>                                   @<index>
    in   <name> : <Type>                                              # OFF-pane terminal -- no `@<index>`
  block-diagram :                        # ALWAYS present
    <keyword> <handle> : <component>       # a node's DECLARATION line (§7)
      in   <name> : <Type> [= <driver> | default <value>]      # its terminal lines (§3)
      out  <name> : <Type>
    <structure> :                          # a structure opens an indented block (§8)
      …
    <out name> = <source-net>            # boundary outputs driven at the end of block-diagram
  types :                                # verbose-only lossless footnote (§10.1), OPTIONAL
    <Name> = <lossless-def>   [; ./path]
```

- **`vi <name> :`** header (top-level VI).
- **`uses :`** dependency manifest (§7a) — a plain reference list of every
  external file this VI directly depends on, right after the header.
  Present in BOTH terse and verbose (it's the first "element" of the
  terse/verbose design — a later, verbose-only element adds each
  dependency's own inline interface/type structure on top). Omitted
  entirely when the VI has no dependencies. LAYOUT IS PROVISIONAL: this
  placement (right after the header) is where it lands until every element
  of the design exists and the maintainer picks final section ordering.
- **`front-panel :`** — the connector pane's own identity (`pattern :
  <conId>`, omitted when unknown) followed by the boundary terminals: `in `
  lines then `out` lines, each carrying a trailing `@<index>` pane-slot
  column (the terminal's `ConnectorPaneTerminal.index` — present in BOTH
  terse and verbose, since pane-slot identity is structural, not a
  lossless-verbosity nicety), then any OFF-pane front-panel controls/
  indicators (a control/indicator that exists on the front panel but was
  never wired onto the connector pane) as their own group, with no
  `@<index>` (they have no pane slot). Omitted entirely — no header at all —
  when there is nothing to show: no pattern, no on-pane terminals, and no
  off-pane terminals (a top-level/main VI with an empty connector pane and
  no front-panel objects). A boundary input whose default is notable
  renders it into the name: `in error in (no error) : Error`.
- **`block-diagram :`** (always present) — the body, in graph order
  (`_node_order_key`), followed by the boundary-output-drive lines at its
  own end: `<out name> = <source-net>` (`error out = case_139::out0`). So a
  boundary output is declared once (in `front-panel :`) and driven once (at
  the end of `block-diagram :`) — follow its net name to connect the two.
- **`types :`** (§10.1, verbose-only) — the bottom lossless-types footnote,
  omitted entirely when the VI has no named types.
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
stripped** and **spaces replaced by `_`**, suffixed **`_<uid>`** — the node's own
stable BD `uid` (`netlist_build._uid_of`), not a positional occurrence counter —
for every instance, including the first (schematics don't leave `U1` blank).
Because a `uid` is globally unique on its own, every instance carries its
handle regardless of whether its base name repeats elsewhere in the VI, with
no VI-wide grouping/visitation-order bookkeeping needed to stay
collision-free — and, as a side effect, the handle also doubles as the
node's own graph-identity round-trip key (§1). The faithful **component** —
a subVI's fully-qualified `Class.lvclass:Name.vi`, a primitive's LabVIEW
name — is spelled **once**, at the declaration, so the verbose qualifier
never repeats on every wire and the handle carries no `.vi`/class (that is the
declaration's job, and answers "which class/library?" authoritatively). The
faithful identity lives on the declaration line, so the handle is a display
designator only — round-trip reads the component from the `:` clause, never by
parsing the handle.

**Every producing node gets a handle** (§9), CLOSED or OPEN — so a net pointing
at any node is uniform (`propRef_1580::Value`) and a downstream reader never has to
special-case its source. Naming the wire and drawing the node's insides are
separate problems: the handle solves the first for *all* kinds; the second is
designed per kind. `property-node`, `invoke-node`, `feedback-node`, and
`local-variable` now have a designed rendering (rows below) — they emit like
any other node (`local-variable`'s own shape is a single `read`/`write` line,
not the generic handle+terminal-block form the others use). Still pending only
their *special content* (handle + terminals still render): `formula-node` (the
`script` body — needs the `script` field plumbed), `in-place-element` (the
decompose↔recompose pairing), and `global-variable` (a separate Global-VI
construct, not yet modeled at all — see §17). Those emit their handle + typed
terminals with a `# TODO(lvnet): …` on the one undesigned part — never an
invented form.

| Node | keyword | rendering |
|---|---|---|
| SubVI call | `subVI` | `subVI <handle> : <Qualified:Name.vi>` header (+ `; ./path` nav comment), then its `in`/`out` terminal lines. The **handle** (`<despaced-name>_<uid>`, §9) names the instance; the **component** after `:` is **always fully qualified** and spelled only here (e.g. `subVI listAllTestMethods_359 : TestCase.lvclass:listAllTestMethods.vi`). |
| Primitive | `function <handle> : <LabVIEW name>` | Rendered **exactly like a subVI** — `function Add_1 : Add`, `function Index_Array_1 : Index Array`, `function Bundle_By_Name_1 : Bundle By Name` — handle left of `:`, faithful primitive name right of it, then typed terminals wired by nets. Interface from `primitives.json` **plus per-call parsed types** (primitives are polymorphic); variadic ones (Compound Arithmetic, Build Array) show their actual per-call terminals. **No inline operators** — LabVIEW has an `Add` *node*, so we show a node (keeps fidelity and diffs cleanly). |
| Property Node | `property-node <handle> : <ObjectClass>` | a property's value terminal IS a terminal: a **read** renders as an `out` (value flows out), a **write** as an `in` (`= <driver>`), each named by the property; drawer order = line order (top-to-bottom sequential). No special syntax — the standard terminal block carries it. |
| Invoke Node | `invoke-node <handle> : <ObjectClass>.<Method>` | the method IS the node's identity, so it sits in the component; parameters render **by index** (`in  0 : <Type> = <net>`, `out 1 : <Type>`) because LabVIEW stores no param names in the VI (only the VI-server signature does). |
| Feedback Node | `feedback-node <handle> (<N> iteration[s]) :` | a state **register** — no more-specific-type (like a shift register), so the `:` just opens its `init = <src>` (first iteration) / `each = <src>` (fed back next) block. The count — how many iterations back the value is handed — rides as a parenthetical **attribute**: `(1 iteration)` / `(3 iterations)`. **Not** `delay` (reads as time) or `z^-N` (DSP glyph). Handle is its own `fbK` net — a small sequential id, one per feedback node in the VI, unlike every other node's `_<uid>` handle suffix (a feedback node has no separate "declared name" for a base to attach a uid to; the net name doubles as the handle); `(? iterations)` when the depth didn't parse (LabVIEW enforces ≥1). |
| Constant | `constant` | `constant <handle> : <Type> = <value>` (handle `<name>_<uid>`, e.g. `constant GUID_142 : String = "TC-001"`). A one-off literal stays inline on the terminal; a shared/named constant becomes a `constant` node referenced by net (`= GUID_142`). |
| Local Variable | `local-variable` | a TAP on a control/indicator's named net, not a computation — but it still gets its own handle (§9) since a read is a genuine producer. A **read** is a SOURCE: `local-variable <handle> : read`, with no component and no terminal block (the tapped control's own type is already spelled at its `front-panel :` row); a downstream reader references its value as `<handle>::<terminal>`, exactly like any other node-terminal net. A **write** is a SINK: `local-variable <handle> : write = <source>`, terminating its one driven source into the control — no output of its own. The netlist does not resolve which write a given read observes (stateful/runtime); reads and writes are independent access points linked only by tapping the same control. `global-variable` (a separate Global-VI construct) is not yet modeled — OPEN, §17. |
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
| Case Structure | `case <selector-net> :` | `frame "<value>"[, default] :` per case (`default` appended as the header's own comma-list entry when the frame is the default; `frame default :` when it carries no specific value of its own) |
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

**The structure-identity annotation `(id <uid>)` (verbose-only).** Every
structure header above may carry a trailing `` (id <uid>)`` right before its
block-opening `:` — e.g. `case Params_1480::Params (id 139) :`, `while-loop
(id 42) :`, `flat-sequence (id 7) :` — the structure's own real BD `uid`
(the same identity `case_<uid>::outK`/`loop_<uid>::shiftK` net names already
carry, and `index_module`/the diff renderer key a changed structure by).
Shown in verbose mode only (lvnet §11's render-rehydration axis, exactly
like the `types :` footnote or a `uses :` entry's inlined interface — never
a readability nicety, so terse output is unaffected). Without it, a
structure that never drives an output net spelling its own uid — every
`flat-sequence`/`stacked-sequence`/`diagram-disable`/`conditional-disable`/
`type-specialization`/`event-structure` (none of these carry ANY output
merge, so none of them ever get a `case_UID::`/`loop_UID::`-shaped net at
all), or a `case`/`for-loop`/`while-loop` with no output tunnel or shift
register — has no way to recover its own identity from the text at all;
this is the piece that closes that gap (§1's graph-identity round-trip
gate).

**A case frame's `is_default` flag, in the header.** A case frame's header
lists its selector value(s) as the existing quoted comma list, and — mirroring
LabVIEW's own `"Error", Default` selector convention — the bare, unquoted
keyword `default` is appended as that list's **last entry** whenever the frame
is the structure's default frame, and is the list's **sole entry** when the
frame carries no specific selector value of its own (the ordinary case:
`_selector_label` collapses a non-error default frame to the `"Default"`
sentinel with no real value left to show):

- a pure default frame (no specific value): `frame default :`
- a frame that catches a value AND is the default: `frame "Error", default :`
- a plain value frame (unchanged): `frame "Error" :`
- a multi-value frame stays a comma list (unchanged): `frame "A", "B" :`

This closes a real gap an earlier pass of this doc flagged as open: an
Error-cluster default frame keeps a real value (`_selector_label`'s
`is_error` branch never returns the `"Default"` sentinel), so on a real
corpus VI — `TextTestRunner/run.vi`'s dynamic-dispatch cascade — both the
default frame and an ordinary `"1"`-valued frame are labeled `"Error"`; before
this fix both rendered the identical `frame "Error" :` header with nothing in
the text to tell them apart, breaking §1's graph-identity round trip
(`reconstruct_module` re-attached per-frame output-tunnel sources by label
text and could resolve the wrong frame's source for the other). Scoped to the
case/select structure only — sequence/disabled/event frames keep their plain
`frame <label> :` header; none of them encode a comparable "default" concept
in their own header text.

### Border constructs

```
shift-register loop_879::shift0 :
  init = <source>          # value on the first iteration
  each = <source>          # value fed to the next iteration
tunnel loop_879::out0 : auto-indexing = <source>   # mode: auto-indexing | last-value | concatenating | pass-through [+ conditional]
case_139::out0 = <source>      # a case output, driven inside each frame
```

(`879`/`139` are the loop's/case's own real BD `uid` — see §9.)

## 9. Net naming

**One name per net, spelled identically at its driver and every reader** — follow
a wire by grepping its name and land once (VHDL-like "one name → one
declaration"). No scope-local abbreviation that makes `out0` mean different nets
in different places.

- Node-terminal nets: `<handle>::<terminal>` — the producing node's **instance
  handle** (§7), a `::` (scope resolution, "the terminal *within* this
  instance"), then the faithful terminal name, or the terminal's **index**
  when it is unnamed (`listAllTestMethods_359::test methods`, `Not_212::0`). `::`
  (never `.`) keeps an indexed terminal off the handle's `_<uid>` suffix, so
  `Not_212::0` can't be misread as the float `212.0`, and `::` doesn't pile onto
  the already four-way-overloaded `:` (type, declaration, block header, class
  qualifier).
- Structure nets use the same `::`, keyed by the structure's own real BD `uid`
  (never a small per-structure counter, §1/§8): `case_<uid>::outK` (case
  output), `loop_<uid>::shiftK` (shift register), `loop_<uid>::outK` (loop
  output tunnel), `sequence_<uid>::outK` (flat/stacked-sequence output),
  `disabled_<uid>::outK` (disable-family output), `event_<uid>::outK` (event
  structure output), `fbK` (feedback — a whole net, no terminal, its own small
  sequential id rather than a uid, §7). Structure prefixes keep their
  reserved `<kind>_<uid>` form — they are not node handles. `::` is the
  **only** instance→terminal separator, so `.` never appears in a net name
  (it survives only inside a component path at a declaration, §7; the
  MODEL's own internal storage keeps a `.` separator here instead of `::` —
  `_lvnet_net_separator` reformats it for this render only).
- **Instance suffix `_<uid>`:** every node/constant instance carries
  `_<uid>` — the node's own stable BD `uid` (`netlist_build._uid_of`), not a
  positional occurrence counter — the first copy included: the handle's
  uniquifier and the stable diff/round-trip identity. (Replaces the old
  `#n`; `#` is now the comment marker, §6.)
- **Component identity lives at the declaration** (§7), always fully qualified
  there; the net prefix is the **handle**, unique VI-wide by construction — the
  `_<uid>` suffix absorbs both a repeated copy *and* a same-base-name collision
  across classes (`Do.vi` in two `.lvclass`es → `Do_44`, `Do_207`, each declared
  against its own `Lib1.lvclass:Do.vi` / `Lib2.lvclass:Do.vi`) for free, since a
  real BD `uid` is already globally unique — no VI-wide grouping/visitation-order
  bookkeeping needed to stay collision-free. So a net still names exactly one
  instance, spelled identically at its declaration and every reader — grep the
  handle, land once.

## 10. Types

Faithful LVType on every terminal, **never a Python type**:

- Scalars: `DBL`, `I32`, `Boolean`, `String`, `Path`.
- Error cluster: `Error` (default `(no error)`).
- Array: `[T]` — `[String]`, `[LabVIEW Object]`.
- Cluster: an ANONYMOUS cluster's inline form is field **NAMES only**, lower-case
  `cluster{…}` — `cluster{Source, Type, Time}` — never field types (there is no
  name to hang a `types :` footnote entry off, so this is genuinely the most
  faithful inline form available); a NAMED cluster's inline form is its bare
  `typedef_name` alone (§10's next bullet) — never the generic `Cluster{…}`
  word. `Cluster{…}` (capitalized) is reserved for the verbose `types :`
  footnote's own FULL lossless definition (§10.1), which does show every
  field's type.
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

### 10.1 The lossless `types :` footnote (verbose-only)

The by-name form above (`lveventtype`, `Cluster{TestSuite, suiteStatus}` — no
field *types*, no enum ordinals) is **intentionally lossy** — it's what makes a
terminal line readable. It is *not* enough to **rehydrate** a type: verbose
needs the FULL structure of every NAMED type, once, somewhere. That's the
`types :` section — a bottom appendix (LAYOUT PROVISIONAL, trivial to move):
one `<Name> = <lossless-def>` line per NAMED enum/ring/cluster/typedef
reachable anywhere in the VI (boundary, every node's own terminals, `uses :`
dependency interfaces), sorted by name, carrying a `; ./path` nav suffix when
the type's own file is known. **Terse never emits this section.** Anonymous
types stay inline everywhere (there's no name to hang a footnote off) — a
named type's *inline* occurrence (a terminal line, a cluster field) still
renders by bare name (§10 above); its footnote entry is the ONE place its
full structure lives.

The lossless def grammar, keyed to the same closed set of `LVType` kinds:

- **enum/ring**: `Enum{ m0 = 0, m1 = 1, … }` / `Ring{ … }` — ordinals
  **explicit**, in ordinal order (never omitted the way the inline form does).
- **cluster**: `Cluster{ f0 : <type-ref>, f1 : <type-ref>, … }` — every
  field's own faithful type, not just its name. `<type-ref>` is: a NAMED
  sub-type BY NAME (its own footnote entry, never re-inlined — footnotes stay
  FLAT, one entry per name, so a self-referential type can't recurse
  forever); an ANONYMOUS composite expanded fully and recursively (nothing
  else faithful to show); a scalar as its own token.
- **array**: `[<type-ref>]`, nested once per `dimensions` (`[[DBL]]` for a 2D
  array of `DBL`).
- **refnum**: a class refnum shows its class name verbatim; a parametrized
  refnum shows `<ref_type> refnum{ <type-ref> }`; otherwise `<ref_type>
  refnum` / `"refnum"`.
- **typedef wrapping something with no enum/cluster shape of its own** (e.g.
  a `.ctl` typedef of a bare scalar): falls out of the same dispatch as its
  own underlying kind — there is no separate "typedef" keyword, the footnote
  entry just shows whatever that type's real structure is.
- Never fabricated: a cluster/typedef with no field list loaded, or an
  enum/ring with no member list loaded (an unresolved reference), renders the
  honest `Cluster{ ? }` / `Enum{ ? }` rather than guessing.

**Known lossy corner case — ambiguous same-name types.** A `.ctl` typedef name
is not a global identity: the SAME nominal name can genuinely resolve to
*different* structures at different call sites in one VI (observed on a real
corpus VI, `WaveGen.vi`'s `Event Data.ctl` — its `Value` field is a
Variant-typed User Event data field that resolves to one enum at one
registration site and a different cluster at another). Because a footnote is
ONE entry per name (this section's own rule — never per-occurrence variants),
that single entry cannot be faithful to every occurrence of an ambiguous
name. The round-trip gate treats this honestly rather than either fabricating
a per-occurrence footnote (not part of this design) or silently reporting a
false structural match: a name known (from the real graph) to carry more than
one distinct structure is excluded from the strengthened structural
comparison everywhere it's used, falling back to the same by-name comparison
§10's terse form already provides. This is a genuine, documented information
loss of the flat one-entry-per-name model, not a bug in the comparison logic.

**Known limitation — a named type reachable ONLY through an anonymous
cluster's field is not captured.** The footnote collects every NAMED type
reachable from the boundary/body/dependency interfaces (§10.1 above), and a
NAMED cluster's OWN footnote entry recurses into its fields (a named field's
type is itself collectible, since the named cluster's own def spells that
field's real type via `<type-ref>`, §10.1's cluster bullet). An ANONYMOUS
cluster's fields are different: its only occurrence anywhere in the rendered
text (§10's Cluster bullet above) is `cluster{f1, f2, …}` — field NAMES
only, never field types — so even if one of those fields' own type is a
NAMED type (e.g. an Event Data Node's `cluster{Source, Type, Time}`, whose
`Type` field is the named enum `lveventtype`), there is nothing in the
rendered text that names that field's type at all. Collecting it into the
footnote anyway would list a type reconstruction can never re-derive from
the text (`reconstruct_module` has no way to learn "the anonymous cluster's
`Type` field is `lveventtype`" from `cluster{Source, Type, Time}` alone),
breaking §1's round-trip gate. So a named type reachable *exclusively*
through an anonymous cluster's field is correctly excluded from the
footnote (it is still collected normally when *some other* reachable path
also reaches it directly). The full fix — rendering an anonymous cluster's
field types inline, `cluster{f1 : T1, f2 : T2, …}`, closing the gap at its
source — is a separate, not-yet-designed enhancement (it would also make
this corner case moot for future cases): not implemented here.

**Why it is not a one-liner (verified 2026-08-29): it forks the inline
grammar.** The footnote lossless-def already spells anonymous composites with
field types — but in the *capitalized* grammar `Cluster{ a : DBL }` /
`Enum{ m0 = 0 }`, which uses bare ` : ` and ` = ` tokens. The inline
terminal-line parser (`_split_node_terminal_tail`) is whitespace-tokenized and
treats any bare `=` as the `= <driver>` operator (and any bare `default` as
the default keyword) — it is **not brace-aware** — so pasting that grammar onto
a terminal line misparses (`in x : Enum{ m0 = 0 }` → type `Enum{ m0`, driver
`0 }`). Pure-scalar anonymous clusters (`Cluster{ a : DBL, b : I32 }`) happen
to survive as an opaque string but are compared by string equality, gaining no
structural recovery, and any enum/named field inside re-triggers the collision.
Closing the gap therefore requires a **maintainer grammar decision**, between:
(a) **harden the inline line parser to be brace-aware** (ignore
`=`/`default`/`@index` inside `{}`/`[]`), then reuse the capital lossless-def
grammar inline — consistent and fully structural, but it changes the inline
`<Type>` contract the graph-identity gate rests on; or (b) **give each
anonymous cluster a synthetic footnote handle** — but §10 reserves the footnote
for NAMED types and anonymous types have no stable cross-occurrence name, so
this invents new grammar. Not to be chosen silently.

## 11. verbose vs terse

Only **lvnet + JSON** have the two modes and are lossless targets. **`text` is
human-only, NOT a lossless target** — its "losses" (no wiring, no locals) are by
design; an agent needing full understanding uses lvnet/JSON.

- **Terse (default) = structurally understandable.** The *complete dataflow
  program* — every node, terminal, wire, type, value, structure, control flow —
  is always present; terse never drops anything that changes what the VI does.
  Plus only consequential VI settings (`inline`-true, non-default reentrancy).
- **Verbose (`-v`) = lossless (round-trips to the graph).** Terse **plus** the
  authoring/cosmetic detail: all VI properties (Window/execution flags,
  `inlinable`, `allow_debugging`, `priority`, reentrancy) + health; node
  labels/captions/descriptions & comment text; positions/decorations; constant
  display formats & raw values; a *wired* terminal's now-unused default; hidden
  loop terminals revealed; full cluster/enum type expansions via the `types :`
  footnote (§10.1 — lossless: enum ordinals, cluster field types, one entry
  per NAMED type, the piece that makes verbose actually type-rehydratable);
  the `wiring_rule` nuance at call sites; class-context fields/methods;
  property/invoke detail; `poly_variant_name`; `primResID` as data;
  disable-structure `kind`; `displayed_frame`/`active_frame`/`case_insensitive`;
  Event Filter-vs-Data; `VIContext.description`; every structure header's own
  `(id <uid>)` identity annotation (§8 — the piece that makes a structure's
  identity recoverable even when it drives no output net that would
  otherwise spell it). (A local variable's
  `is_write` is NOT verbose-only — it decides the `read`/`write` keyword
  itself, §7, present in every mode.)

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

Verbose (`-v`/`verbose=True`) adds, on top of everything terse already
carries, the JSON counterparts of lvnet's own verbose-only elements —
**never present, not even as an empty value, in non-verbose output**:

- `connector_pane.terminals[]` gain `wiring_rule` (the tri-state +
  unknown, replacing the always-present terse `required: bool` collapse).
- A top-level `dependencies[]` — the `uses :` manifest: one entry per
  directly-referenced file, `{kind, qualified, path}`
  (`kind` ∈ `subVI`/`typedef`/`class`); a resolved `subVI` entry also
  carries `interface[]` — its own connector pane, ordered inputs then
  outputs, each `{name, type, direction, lv_type?}` (omitted entirely,
  never `[]`, for a `class`/`typedef` dependency or an unresolved `subVI`).
- Every terminal that carries a structured type — `connector_pane.
  terminals[]`, boundary `inputs[]`/`outputs[]`, a dependency's own
  `interface[]`, and each body instance's wired `inputs[]`/`outputs[]` —
  gains an `lv_type` object alongside its existing flattened `type` string:
  a direct recursive mirror of the `LVType` dataclass itself (`kind`,
  `underlying_type`, `ref_type`, `classname`, `values` — enum/ring members
  keyed by name with explicit ordinals, `fields` — cluster fields with
  their own nested `type`, `element_type`, `dimensions`, `typedef_path`,
  `typedef_name`, `description`, `measure_flavor`). Unlike lvnet's `types :`
  footnote (one entry per NAMED type, referenced by name to stay
  line-length-sane in text), the JSON form nests each type's full structure
  inline at every occurrence — JSON has no whitespace/line-length pressure
  forcing a by-name indirection, so repetition costs nothing and every
  terminal is self-contained. `lv_type` is omitted (never `null`) when the
  underlying type didn't resolve.

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

- **CLI**: `lvkit describe --format lvnet [-v]` produces exactly this text
  (terse by default; `-v`/`--verbose` inlines each direct SubVI's
  connector-pane interface plus the trailing `types :` appendix). The MCP
  `read_vi` tool mirrors it via `format="lvnet"` (+ `verbose=True`). The
  older `--format netlist` (old `gamma`/`mu`/`eta` render) still works,
  unchanged, but is deprecated in favor of `lvnet`.

## 16. Golden reference render

`loadTestsFromTestCase.vi`, the canonical example to build against:

This is the ACTUAL, verified output of `render_lvnet(build_netlist_from_graph(...))`
on the real corpus VI (byte-identical to `tests/test_render_lvnet.py`'s
`_GOLDEN_LVNET` fixture -- re-verified against the current renderer, not
hand-typed) in **terse** mode. Notable real facts it locks in: every node
handle carries the node's own real BD `uid` (`listAllTestMethods_359`,
`TestCase_Init_772`, never a small sequential counter, §7/§9); every
structure net is keyed the same way (`loop_879::shift0`, `case_139::out0`);
the document is wrapped in the `front-panel :` (connector-pane `pattern :`
id + boundary terminals, each with its own `@<index>` pane-slot column) /
`block-diagram :` (body + boundary-output-drive lines at the end) section
layout (§2); `testSuiteStatusChanged EventRef`'s refnum collapses to its
inner cluster's NAME (`UserEvent refnum{suiteStatusChanged--Cluster}`, §10)
instead of expanding its fields; `TestSuite in`/that same refnum terminal
render the bare word `default` (no literal to show, §4); and the block's own
column width is capped, so that one refnum type doesn't stretch the
`tests (none)`/`GUID ("")`/`error in (no error)` lines' `: <Type>` column out
to match it (§14). It also carries the §7a `uses :` dependency manifest right
after the header -- this VI's real six direct dependencies (its three SubVI
calls plus the three classes its own connector pane is typed with), sorted
by qualified identity, every one resolving to a real on-disk file in this
corpus so every line carries a `; ./path` nav comment. This VI has no
`constant` node on its own diagram (see the synthetic example below for that
shape) and no off-pane front-panel controls (see §2/§7's `front-panel :`
description for that shape).

```
vi loadTestsFromTestCase.vi :
  uses :
    class TestCase.lvclass                       ; ./Classes/TestCase/TestCase.lvclass
    subVI TestCase.lvclass:TestCase_Init.vi      ; ./Classes/TestCase/TestCase_Init.vi
    subVI TestCase.lvclass:listAllTestMethods.vi ; ./Classes/TestCase/listAllTestMethods.vi
    class TestLoader.lvclass                     ; ./Classes/TestLoader/TestLoader.lvclass
    class TestSuite.lvclass                      ; ./Classes/TestSuite/TestSuite.lvclass
    subVI TestSuite.lvclass:TestSuite_Init.vi    ; ./Classes/TestSuite/TestSuite_Init.vi
  front-panel :
    pattern : 4815
    in   TestLoader in       : TestLoader.lvclass @11
    in   TestCase            : TestCase.lvclass   @10
    in   error in (no error) : Error              @8
    out  TestLoader out      : TestLoader.lvclass @3
    out  TestSuite           : TestSuite.lvclass  @2
    out  error out           : Error              @0
  block-diagram :
    case error in (no error) :
      frame "No Error" :
        subVI listAllTestMethods_359 : TestCase.lvclass:listAllTestMethods.vi
          in   TestCase in         : TestCase.lvclass = TestCase
          in   error in (no error) : Error            = error in (no error)
          out  TestCase out        : TestCase.lvclass
          out  test methods        : [String]
          out  error out           : Error
        for-loop :
          subVI TestCase_Init_772 : TestCase.lvclass:TestCase_Init.vi
            in   TestCase in            : TestCase.lvclass = listAllTestMethods_359::TestCase out
            in   methodName ("runTest") : String           = listAllTestMethods_359::test methods
            in   GUID ("")              : String           default ""
            in   error in (no error)    : Error            = loop_879::shift0
            out  TestCase out           : TestCase.lvclass
            out  error out              : Error
          shift-register loop_879::shift0 :
            init = listAllTestMethods_359::error out
            each = TestCase_Init_772::error out
          tunnel loop_879::out0 : auto-indexing = TestCase_Init_772::TestCase out
        subVI TestSuite_Init_115 : TestSuite.lvclass:TestSuite_Init.vi
          in   TestSuite in                    : TestSuite.lvclass default
          in   tests (none)                    : [LabVIEW Object]  = loop_879::out0
          in   testSuiteStatusChanged EventRef : UserEvent refnum{suiteStatusChanged--Cluster} default
          in   GUID ("")                       : String            default ""
          in   error in (no error)             : Error             = loop_879::shift0
          out  TestSuite out                   : TestSuite.lvclass
          out  error out                       : Error
        case_139::out0 = TestSuite_Init_115::error out
        case_139::out1 = TestLoader in
        case_139::out2 = TestSuite_Init_115::TestSuite out
      frame "Error" :
        case_139::out0 = error in (no error)
        case_139::out1 = TestLoader in
        case_139::out2 = (default TestSuite.lvclass)
    TestLoader out = case_139::out1
    TestSuite = case_139::out2
    error out = case_139::out0
```

A `constant` node (not exercised by this particular VI, which has none on its
own diagram) would declare and read as (`142` illustrative -- the node's own
real BD `uid`, §7/§9):

```
constant GUID_142 : String = "TC-001"
...
in   GUID : String = GUID_142
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
   feedback-node / local-variable (§7). Still open: in-place-element
   decompose↔recompose pairing, and formula-node script rendering (needs the
   `script` field plumbed).
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
