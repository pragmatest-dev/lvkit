# Netlist

A schematic always has a text form: a netlist. In EDA that's SPICE
(`R1 n1 n2 1k`) or structural Verilog (`module M(.a(w1), .b(w2))` declared
once, then instantiated) — the component's typed interface is declared, then
every instance wires into it by named port. A LabVIEW block diagram is a
schematic too, so lvkit gives it the same declare-then-wire text form: a
`## Components` section (every distinct subVI/primitive's typed port
interface, declared once) followed by a `## Netlist` section (every node
instantiated, node-first, with each input bound to its source net by name).

```text
## Components
  Close Generic Object Refnum (Scalar)__ogtk.vi(error in (no error): dict[str, Any], Gen Refnum: Any) -> (error out: dict[str, Any])
  Merge Errors.vi(1: None, 2: None, 3: None, 4: list[dict[str, Any]], 5: dict[str, Any], 6: dict[str, Any], 7: dict[str, Any]) -> (0: dict[str, Any])

## Netlist
Close Generic Object Refnum (Array)__ogtk.vi (Gen Refnum Array, error in (no error)) -> (error out)
for:
  Close Generic Object Refnum (Scalar)__ogtk.vi(Gen Refnum=Gen Refnum Array) -> error out
Merge Errors.vi(4=error out, 7=error in (no error)) -> Merge Errors.vi.0
```

(`lvkit describe "Close Generic Object Refnum (Array)__ogtk.vi" --search-path samples/OpenG/extracted -v`,
`## Components` + `## Netlist` sections.) The component is declared once,
with its port name and type (`Gen Refnum: Any`); the instance inside the
`for:` loop wires that same port to a net by name (`Gen Refnum=Gen Refnum
Array`) — Verilog `.port(net)` / VHDL `port => signal` / Python-kwargs
association, not position. `error in`/`error out` are ordinary ports here
too — a netlist shows a VI's error cluster exactly like any other terminal;
only [`generate`](generate.md)'s Python codegen turns it into an exception
(see [VI signature line](#vi-signature-line) below).

This is the ONE projection of a VI's graph shared by three consumers:
[`describe --verbose`](describe.md)'s `## Components` + `## Netlist`
sections, [`diff`](diff.md)'s text output, and the interactive diff viewer's
Tree change list. Learn the grammar once and it reads the same everywhere.

## Grammar

### Components: the typed interface, declared once

`name(port: type, ...) -> (port: type, ...)` — one line per distinct
subVI or primitive actually called anywhere in the VI, **sorted by name**.
Unlike an instance line (a call), a component's outputs are parenthesized
like its inputs, matching the VI's own signature line:

```text
Bundle/Unbundle By Name(0: TestCase) -> (isSkipped: bool)
CallTestMethod.vi(error in: dict[str, Any], TestCase in: TestCase, method: methodEnum) -> (error out: dict[str, Any], Test Method Error: dict[str, Any], TestCase out: TestCase, execution time (sec): float)
Clear All Errors__jki_lib_error_handling -- VI Tester.vi(error in: dict[str, Any]) -> (no error out: dict[str, Any])
Not(x: bool) -> (not_x: bool)
```

(`TestCase.lvclass:run.vi`'s `## Components`.) `CallTestMethod.vi` and
`Clear All Errors__jki_lib_error_handling -- VI Tester.vi` both declare
error-cluster ports (`error in`/`error out`, `no error out`) alongside
their other terminals — a netlist never hides an error cluster. A
component with no outputs at all prints `name(ins)`, same as an instance
(see [`Comment__ogtk.vi()`](#instance-node-line--node-first-named-port)
below). Structures (`case`/`for`/`while`/
`sequence`/`disabled`) are containers, not components — they never appear
here; `## Netlist` already shows their scopes. Two calls to the same
primitive that resolve to genuinely different typed interfaces (a
polymorphic instance dispatched differently per call site) get separate
entries, the second tagged `Name (2)`:

```text
  Match Pattern(string: str, regular expression: str, offset: int) -> (before substring: Any, match substring: Any, after substring: Any, offset past match: int)
  Match Pattern (2)(string: str, regular expression: str, offset: int) -> (before substring: str, match substring: str, after substring: str, offset past match: int)
```

(`lvkit describe "Convert File Extension (String)__ogtk.vi" --search-path samples/OpenG/extracted -v`.)

### VI signature line

`NAME (ins) -> (outs)` — the VI's own connector pane, faithfully: an
`error in`/`error out` pair is a real input/output like any other and
appears in the signature exactly where the connector pane places it, e.g.
`Close Generic Object Refnum (Array)__ogtk.vi (Gen Refnum Array, error in
(no error)) -> (error out)` in the [Components](#components-the-typed-interface-declared-once)
example above. A netlist never filters error clusters out of any surface —
`describe`, `diff`, and `netlist` all show them as real terminals and
wires. The ONE place an error cluster becomes something else is
[`generate`](generate.md)'s Python codegen, which turns it into a
raised/caught exception; that transformation is scoped to codegen only. A
VI with no inputs or outputs still prints empty parens:

```text
VI Tree - string__ogtk.vi () -> ()
```

### VI properties & structure header

The grammar reserves two metadata lines directly below the VI signature
line — the LOCKED syntax for a VI's own settings and its kind/health, the
netlist analog of a SPICE `.options` block or a Verilog `(* … *)` design
attribute. Each would print only when it has notable content, so an
unlocked, healthy, default VI would show neither:

```text
VITester_Item_Init.vi (Object) -> ()
properties: lock=password_protected, reentrant
structure: broken, typedef
```

- `properties:` — notable VI Properties: `lock=<state>` when the VI isn't
  `unlocked`, then bare flags for the high-signal settings (`reentrant`,
  `subroutine`, `run-on-open`, …).
- `structure:` — set kind/health flags: `broken`, `typedef`,
  `dynamic-dispatch`, `source-only`, `no-block-diagram`, `instance-vi`.

**`render_netlist` does not emit this header inline today** — `describe`
already has its own `## Properties` / `## Structure` sections, so inlining
the same facts into `## Netlist` too would just duplicate them on the same
page. This section instead fixes the canonical SYNTAX and curated flag
vocabulary for those facts wherever they DO appear today:
[`diff`](diff.md)'s own `Properties:` / `Structure:` sections (one
`~ <flag>: <old> -> <new>` gutter line per changed flag, off the same
curated set) and the interactive viewer's status chips + collapsible
Properties panel. Both read the same `VIProperties` / `VIStructure` facets
this header would draw from, carried on the shared `build_netlist` IR (see
`netlist_to_dict`'s `properties` / `structure` keys) — one data source,
several renderings. Emitting this header inline in `render_netlist` itself
is a tracked follow-up (#21).

The exhaustive set — every flag, window cosmetics, numeric execution
priority — lives in `describe`'s `## Properties` / `## Structure` sections
and the JSON `get_context`; every rendering of this curated subset (here,
`diff`, the viewer) is a high-signal SUMMARY only.

### Instance (node) line — node-first, named-port

`name(port=net, port=net) -> outNet, outNet`, or `name(port=net, ...)` when
the node has no outputs. The node leads; its wired inputs follow as
`port=net` bindings — Verilog `.port(net)` / VHDL `port => signal` / Python
kwargs, never positional — then `->`, then its output nets:

```text
CallTestMethod.vi#1(error in=Clear All Errors__jki_lib_error_handling -- VI Tester.vi#1.no error out, method=0, TestCase in=TestCase in) -> error out, execution time (sec), Test Method Error, TestCase out
Comment__ogtk.vi()
```

`Comment__ogtk.vi()` has neither a wired input nor an output, so it's just
`name()`. Every `port=` on the left is the input terminal's own declared
name (the same name `## Components` used to declare it); what follows `=`
is that input's SOURCE net.

### Occurrence tag `#n`

When a node's display name repeats in the VI, EVERY occurrence — including
the first — is tagged `#n` (1-based, in diagram order), so a reference to
one instance is never confused with another:

```text
CallTestMethod.vi#1(error in=Clear All Errors__jki_lib_error_handling -- VI Tester.vi#1.no error out, method=0, TestCase in=TestCase in) -> error out, execution time (sec), Test Method Error, TestCase out
CallTestMethod.vi#2(error in=startTest.vi.error out, method=1, TestCase in=CallTestMethod.vi#1.TestCase out) -> error out, execution time (sec), Test Method Error, TestCase out
CallTestMethod.vi#3(error in=Clear All Errors__jki_lib_error_handling -- VI Tester.vi#2.no error out, method=2, TestCase in=CallTestMethod.vi#2.TestCase out) -> error out, execution time (sec), Test Method Error, TestCase out
```

A name that's unique in the VI carries no tag at all.

### Nets: naming a wire's source

An input's net is named after the terminal that PRODUCES it, evaluated in
this order:

1. **A named terminal** — an input/output with a real name on the connector
   pane — uses that name bare: `TestCase in`, `execution time (sec)`.
   Unless two different nets in the VI would render the identical bare name,
   in which case every reference to either is qualified with its producing
   node's display name (and occurrence tag): `startTest.vi.TestTestResult
   out` vs. `addFailure.vi.TestTestResult out` — both produce an output
   literally named `TestTestResult out`, so neither renders bare.
2. **An unnamed terminal** — most primitive output pins — is always
   identified as `node.index`, whether or not it collides with anything:
   `Compound Arithmetic.0` is the (unnamed) output at index 0 of the
   `Compound Arithmetic` primitive. A bare index alone would be
   meaningless, so this form is qualified up front.
3. **A constant** feeding the wire directly renders as that constant's
   literal value (see [Constants](#constants)), not a node reference.
4. **The VI's own boundary control** feeding the wire renders as that
   control's name, unqualified — it isn't produced by any node in the
   diagram.

```text
Equal?(x=TestResult in (None: Create New), y=Refnum(1)) -> equal
Get LVClass Name from TD.vi(error in=error in (no error), Variant=TestCase in) -> error out, LVClass Name
addFailure.vi(execution time (sec)=CallTestMethod.vi#2.execution time (sec), error in (no error)=startTest.vi.error out, test error=CallTestMethod.vi#2.Test Method Error, test=CallTestMethod.vi#2.TestCase out, TestTestResult in=startTest.vi.TestTestResult out) -> error out, TestTestResult out
Compound Arithmetic(1=Not#1.not_x, 2=Not#2.not_x, 3=Not#4.not_x, 4=Not#3.not_x) -> Compound Arithmetic.0
```

`Refnum(1)` is case 3 (a constant, rendered inline); `TestCase in` feeding
`Get LVClass Name from TD.vi`'s `Variant` port is case 4 (the VI's own
boundary control, unqualified); `startTest.vi.TestTestResult out` is case 1
(named, qualified because that bare name is ambiguous); `Not#1.not_x` is
case 2 (an unnamed terminal would be `node.index`, but `Not`'s output IS
named `not_x`, so it renders bare — `Compound Arithmetic.0` on the left of
that same line is the case-2 example: `Compound Arithmetic`'s own output has
no name at all).

### Constants

A constant feeding a wire renders as its literal value, inline, where it's
consumed — never as a separate line:

```text
Equal?(x=TestResult in (None: Create New), y=Refnum(1)) -> equal
CallTestMethod.vi#1(error in=Clear All Errors__jki_lib_error_handling -- VI Tester.vi#1.no error out, method=0, TestCase in=TestCase in) -> error out, execution time (sec), Test Method Error, TestCase out
Format Variant Into String__ogtk.vi(Variant=Variant()) -> Output String, error out
Convert EOLs__ogtk.vi(String in='') -> String out
```

(`Refnum(1)`, the integer `0`, `Variant()`, and the empty string `''` are all
constants.) This differs from `describe`'s non-verbose `## Constants`
section, which lists an unnamed constant on its own line
(`(unnamed): str = '^\\.'`) — the netlist never does that; the value always
sits inline at its point of use.

### Unwired inputs are omitted

A netlist lists connections. A subVI call's declared `## Components`
interface enumerates its whole connector pane, but most calls leave optional
pins at their default — those don't appear at all, not as a placeholder:

```text
  Search or Split String__ogtk.vi(offset (0): int, string: str, search string/char (-): str) -> (offset after match: int, substring after match: str, substring before match: str)
```

```text
Search or Split String__ogtk.vi(string='') -> offset after match, substring after match, substring before match
```

`Search or Split String__ogtk.vi` declares three inputs on its connector
pane (`offset (0)`, `string`, `search string/char (-)`); this call only
wires one (`string`, the constant `''`) — the other two are simply absent
from the line, not rendered as an empty or default placeholder.

### Scopes

Structures recurse; each opens with a header line, then its frame(s)
indented beneath it.

**`case (selector):`** — one quoted sub-header per frame, `(default)` tagged
on the default frame. The selector is a net reference, named by the same
[net-naming rule](#nets-naming-a-wires-source) as any other input — including
hopping through a structure's own input/output tunnels to the real producing
terminal, never stopping at a bare tunnel index:

```text
case (LVClass Name):
  "TestCase.lvclass":
    (pass-through)
  "Default" (default):
    openMethodViReference.vi(error in (no error)=Get LVClass Name from TD.vi.error out, TestCase in=TestCase in) -> error out, TestCase out

case (Bundle/Unbundle By Name#1.isSkipped):
  "False":
    case (startTest.vi.error out):
```

The second example's selector, `Bundle/Unbundle By Name#1.isSkipped`, is a
`Bundle/Unbundle By Name` node's named output (`isSkipped`) — the case
selects on that named value, not a bare tunnel index or terminal number.
The nested `case (startTest.vi.error out):` is an ERROR-GATED case: the
frame it opens (`"No Error"`/`"Error"`) is selected by an actual error
cluster's status, named and qualified exactly like any other net — a case
that branches on an error is not special-cased or hidden.

**`for:`** / **`while (selector):`** — a single implicit body, no quoted
frame header. The selector in parens appears only when the loop's
stop-condition terminal resolves to a net; otherwise it's bare `for:` /
`while:`:

```text
for:
  Close Generic Object Refnum (Scalar)__ogtk.vi(Gen Refnum=Gen Refnum Array) -> error out
```

```text
while (Or.result):
  Greater?(x=While Loop.0, y=Increment.result) -> x_greater_than_y
  Open/Create/Replace File(operation=2, file_path=VI names) -> error_out, refnum_out
```

```text
while (False):
  (pass-through)
```

**`sequence:`** — frames are numbered, unquoted, with the `frame` keyword
(not a quoted string like a case frame):

```text
sequence:
  frame 0:
    Convert EOLs__ogtk.vi(String in='') -> String out
    Multi-line String to Array__ogtk.vi(string='') -> lines
```

**`disabled:`** — a Diagram/Conditional Disable structure. It renders like
`case`, with quoted frame sub-headers, except it never has a selector (which
frame is active is fixed at edit time, not wired at runtime), so the header
is always bare `disabled:`.

**Empty frames.** A frame with no operations renders `(pass-through)` when
the structure still has an output tunnel wired through it (LabVIEW routes a
value out even though the frame does nothing), or `(empty)` when it doesn't:

```text
"":
  (pass-through)
```

## Where it appears

- [`describe --verbose`](describe.md) — a `## Components` section, then a
  `## Netlist` section covering the whole VI. Verbose output REPLACES the
  non-verbose `## Dependencies`/`## Control Flow`/`## Operations` sections
  with this declare-then-wire pair — a case/loop/sequence's own scope line
  already covers control flow, so there is no separate `## Control Flow`
  section in verbose output.
- [`diff`](diff.md) (`--format text`, both the default and `--verbose` tier)
  — the entire change tree is netlist syntax; see
  [Diff annotations](#diff-annotations) below.
- The interactive diff viewer's **Tree** change-list mode (the **Flat / Tree**
  toggle) — the same rows `diff --format text` prints, rendered as HTML
  instead of plain text (a small icon glyph per row kind, a numbered badge,
  click-to-select) — see [Interactive diff viewer](diff.md#interactive-diff-viewer).

## Diff annotations

`diff`'s text output is the netlist containment tree with a one-character
change gutter in column 0: `+` added, `-` removed, `~` modified, or a space
for an unchanged structure that merely CONTAINS a change (so a nested change
always shows its enclosing `case (...):` context, even when that case itself
didn't change):

```text
  case (error in (no error)):
    "No Error":
      case (Bundle/Unbundle By Name#1.isSkipped):
        "False":
          case (startTest.vi.error out):
            "No Error":
+             Bundle/Unbundle By Name#2(0=CallTestMethod.vi#1.TestCase out) -> isSkipped
              case (CallTestMethod.vi#1.Test Method Error):
                "No Error":
+                 case (Bundle/Unbundle By Name#2.isSkipped):
                    "True":
+                     addSkipped.vi#2(error in (no error)=startTest.vi.error out, test=CallTestMethod.vi#1.TestCase out, TestTestResult in=startTest.vi.TestTestResult out) -> error out, TestTestResult out
+                 Bundle/Unbundle By Name#4(0=CallTestMethod.vi#3.TestCase out) -> isSkipped
-                 x = isSkipped
```

Because instance lines are node-first, the gutter always sits directly
against the changed node's own name — the added `Bundle/Unbundle By
Name#2(...)` line reads as "this instance was added here," not a wire buried
at the end of a line. The last line, `- x = isSkipped`, is a DELETION that
shows even though nothing else at that depth changed: an unchanged `Not`
node's own `x` input port (`Not(x: bool) -> not_x`, per `## Components`)
lost the wire that used to feed it from an `isSkipped` net produced
elsewhere in the OLD version — the `Not` node itself is unchanged (so it
prints no instance line of its own here), but the rewiring away from it is
still a real change, so the standalone `sink = source` line renders it. A
removed wire always shows, even when no owning node change would otherwise
announce it.

The full annotation rules (constant `=` lines, wire suppression when a
sibling node's own instance line already shows the same net, frame-header
folding for an added/removed/reselected case value) are documented in
[diff](diff.md#example).

## ASCII-only

The netlist is strictly ASCII, on purpose — `git` textconv, CI log capture,
and generic string-matching tools can mishandle wider Unicode. The only arrow
is `->`; there's no `<-`, and no box-drawing characters. The interactive
viewer, being HTML, adds small icon glyphs (`◻`, `↔`, `⬚`) to its Tree rows —
those are a viewer-only affordance, not part of the netlist text itself.

## See also

- [describe](describe.md) — `--verbose` produces the `## Components` and `## Netlist` sections documented here.
- [diff](diff.md) — renders its whole change tree in this syntax; see its [Example](diff.md#example) for the full annotation rules.
