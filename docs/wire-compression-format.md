# `compressedWireTable` — LabVIEW block-diagram wire geometry

Every signal (wire net) on a LabVIEW block diagram carries a
`<compressedWireTable>` hex blob in the `.vi` heap. It encodes the **routed
bend geometry** LabVIEW drew for that wire — the actual orthogonal path, not a
logical connection. lvkit decodes it to render wires faithfully instead of
re-routing them (task #84); the decoder lives in
`src/lvkit/render/wire_table.py` (`decode_signal`).

This document records the format as reverse-engineered from the corpus. Parts
are **verified against hundreds of real wires**; parts are **explicitly marked
unknown**. Nothing here is guessed silently — where the bytes under-determine
the geometry, that is called out.

## Where it lives

Inside a signal element of a diagram's `signalList`:

```xml
<SL__arrayElement class="signal" uid="440">
  <termList elements="3">
    <SL__arrayElement uid="350" />   <!-- [0] = source terminal -->
    <SL__arrayElement uid="329" />   <!-- [1..] = sink terminals -->
    <SL__arrayElement uid="360" />
  </termList>
  <compressedWireTable>0400080503A31059</compressedWireTable>
</SL__arrayElement>
```

- `termList[0]` is the **source** (driver); `termList[1..]` are the **sinks**.
- Every uid's position is known independently (from its terminal's heap bounds),
  so a wire is a tree whose leaves land on **known** terminals. The decoder
  exploits this: it never guesses an endpoint, it snaps each leaf onto its known
  sink center. See "Endpoints are known" below.

## Common primitives (both layouts)

**Coordinates are screen-space, y grows downward.** Direction nibble:

| bit    | dir | (dx, dy) |
|--------|-----|----------|
| `0x08` | E   | (+1, 0)  |
| `0x04` | S   | (0, +1)  |
| `0x02` | W   | (−1, 0)  |
| `0x01` | N   | (0, −1)  |

**Length stream (variable-width).** Lengths are a byte stream, not fixed-width:

- a byte `< 0xFF` is a one-byte length;
- `0xFF hi lo` is a 16-bit big-endian length (used when a segment is ≥ 256 px).

So the blob length is **not** a fixed function of the vertex count. Largest
length observed in the corpus is 1510 px; `0xFF 0xFF` is unobserved and treated
as malformed (→ fall back rather than guess).

**Endpoints are known / the last segment is implied.** For any leaf, the final
segment's length is **not stored** — it is whatever reaches the terminal the
caller already knows. The decoder walks the stored bends, then snaps the last
bend so the final segment lands exactly on the known terminal center. This is
why decoding needs the terminal positions, and why a wire to a **mis-placed
terminal cannot decode** (see "Failure modes").

## Layout A — single wire (2-endpoint / 1-leaf chain)

`byte1` is a **direction** (`0x01/0x02/0x04/0x08`). Used when `termList` has
exactly two terminals.

```
byte0            V   = vertex count (V-1 segments)
byte1            dir0 = direction of segment 0
byte2 .. 2+V-3   V-2 SIGN bits, one per bend (0x00 = +axis E/S, 0x01 = -axis W/N)
then             V-2 lengths (variable-width) for segments 0 .. V-3
```

Segments alternate axis (H, V, H, …), so only a **sign** is stored per bend, not
a full direction. The first segment's direction is `dir0`; each subsequent
segment flips axis and takes its stored sign.

Special cases:

- **`V = 1`** (blob `"01"`): a degenerate/zero-length wire — both terminals
  resolve to the same point. Decodes to *no bends* (draws a point). 62 of these
  in `MasterAcquisitionFile_PCO_IOS.vi` (task #76).
- **`V = 2`** (e.g. `"0208"`): one straight segment, no bends.

Worked example — `"0308011F"` (V=3, dir0=E, one sign byte `0x01`, lengths
`[0x1F]=31`): E31, then flip to vertical with sign `0x01` (N), last segment
snapped to the sink → an L-shape.

## Layout B — fan-out (N-leaf tree)

`byte1` is a **flag** (`0x00` or `0x01`), *not* a direction. Used when
`termList` has three or more terminals. The tree is a **DFS token stream**.

```
byte0                 V     = vertex count
byte1                 flag  0x00 = plain fan-out
                            0x01 = straight-through terminal TAP (one extra
                                   header byte precedes dir0; a sink sits mid-path)
byte(2 | 3)           dir0  = source's first direction, OR a COMPOUND value
                            (several direction bits OR-ed) = the source itself
                            is an N-way junction (see "Source branch")
next V-2 bytes        TOKEN stream (see below)
then V-1 lengths      variable-width
```

### Tokens

Each token advances the current leaf by the next length, or forks/terminates:

| token         | meaning                                                        |
|---------------|----------------------------------------------------------------|
| `0x00`/`0x01` | **BEND** — flip axis; bit0 is the sign (deterministic)         |
| `0x02`        | **STRAIGHT** — continue same axis (passes through a tap)       |
| `0x03`        | **POP** — end current leaf, resume the most recent junction    |
| `0x04`        | **BRANCH**, multi-way junction (may spawn several children)    |
| `0x05`/`0x06`/`0x07` | **BRANCH** (`0x04` \| low bits) — single-use fork        |

A leaf is a chain of BEND/STRAIGHT segments ending at a POP (or end-of-stream).
A BRANCH pushes a junction (resumed later by a POP) and continues the current
leaf. **Every direction — bends AND forks — is deterministic and relative to the
current wire direction** (next section). There is no search and no tolerance: the
whole geometry is recovered from the bytes.

### Source branch (compound `dir0`)

When `dir0` has more than one direction bit set, the **source itself** tees:
segment 0 leaves in one bit's direction and the remaining bits leave as deferred
branches — one junction pushed at the source per remaining bit, spawned on
successive POPs back to it. Verified on a real 3-way source (`dir0 = 0x0D` =
E|S|N, a 6-sink signal in `MasterAcquisitionFile_PCO_IOS.vi`, task #76). This
generalizes to any N; a 2-way source is just N=2.

### Fork direction — RELATIVE turn, fully in the bytes

The fork direction is **not** under-determined and is **not** re-derived from
terminal positions at decode time — it is stored in the branch token's low two
bits as a **turn relative to the incoming wire direction**. Relative encoding is
why two bits suffice: you never store an absolute heading, only the turn off the
current one, so the same three tokens serve a wire flowing any direction.

Let `d` be the incoming direction, `cw(d)` its 90° clockwise perpendicular
(`E→S`), `ccw(d)` its counter-clockwise perpendicular (`E→N`). A branch produces
two children — the **immediate** child (continues the current leaf now) and the
**sibling** (deferred, spawned when this subtree POPs):

| token  | bits   | immediate child | sibling            |
|--------|--------|-----------------|--------------------|
| `0x05` | CW     | `cw(d)`         | `d` (straight)     |
| `0x06` | CCW    | `ccw(d)`        | `d` (straight)     |
| `0x07` | CW+CCW | `ccw(d)`        | `cw(d)`            |

Rule: **bit0 (`0x01`) = a branch goes CW-perp, bit1 (`0x02`) = a branch goes
CCW-perp**; the immediate child is the CCW-perp when bit1 is set, else the
CW-perp; the sibling is the other perpendicular when both bits are set, else it
continues straight. A 180° reverse never occurs. The sibling's direction is thus
**known the instant the branch byte is read** and travels with the junction on
the stack — the POP does not need to encode it (POP is always `0x03`).

Verified deterministically across the corpus: this rule reproduces the geometry
of 277/351 fan-outs byte-for-byte with **zero regressions and zero search**; the
remaining ~70 differed only where the earlier tolerance-search had picked a
valid-but-wrong path, and 240/351 land sub-pixel-exactly on their terminal. What
`#84` read as "recomputed from terminal positions" was really "LV computed it
from positions **at save time** and baked the relative turn into the token."

### Source branch is the same rule at the source

The compound-`dir0` source tee (previous section) is just this fork rule applied
with the source as the first junction: the extra `dir0` bits are the perpendicular
turns leaving the source. It is not a special case.

### Straight-through tap (`flag = 0x01`)

A sink that sits **on** the wire (not at a leaf tip): the `0x02` token runs the
trunk straight through the terminal. One extra header byte precedes `dir0`.
Handled by matching a sink to any vertex on a leaf's path, not only its tip.

## Failure modes (→ auto-router fallback)

`decode_signal` returns `None` (and the caller routes the wire) when:

1. **Malformed / unmodeled bytes** — flag byte not in `{0x00, 0x01}`, a length
   stream that doesn't consume cleanly, `0xFF 0xFF`, etc.
2. **A leaf can't land on a known terminal.** This is usually not a decoder bug
   but a **terminal-position** bug: if a sink's resolved center is wrong, the
   (correct) decoded leaf won't reach it and the whole signal fails. Observed on
   wires terminating on `sRN` (property-node drawer) / `rSR` (shift-ref)
   terminals whose owning node has **degenerate bounds**, so lvkit mis-resolves
   the wire-attach point (task #96). Example: `Close Generic Object Refnum
   (Array VI)__ogtk.vi` signal uid 440 — its east branch to an `nMux` terminal
   decodes within 1 px, but its west branch targets a terminal on an `sRN` with
   bounds `(-142,-571,-142,-571)`; the decoded leaf lands ~147 px from that
   terminal's mis-computed center.

Across 119 diverse corpus VIs this leaves **8 router calls in 2 VIs**; the other
117 render entirely from faithful geometry.

## Byte-level worked examples

- `"0400080503200B18"` — fan-out, V=4, flag 0, dir0 E, tokens `[0x05 BRANCH,
  0x03 POP]`, lengths `[32, 11, 24]`. Source→E32 to a junction; one child taps
  S11 to sink A; resume E24 to sink B. Two clean orthogonal branches.
- `"0E000D000300000505050003030303…"` — fan-out, V=14, dir0 `0x0D` = E|S|N
  (3-way source), 6 sinks. Decodes to six orthogonal branches (task #76).
- `"0400080503A31059"` — same shape as the first example (V=4, dir0 E,
  `[BRANCH, POP]`, lengths `[163, 16, 89]`) but **does not decode**: see failure
  mode 2 above — one sink is mis-placed, not a format problem.

## Cross-references

- Implementation: `src/lvkit/render/wire_table.py` (`decode_signal`,
  `_decode_chain`, `_decode_tree`, `_decode_lengths`, `_perp3`).
- Consumers: `src/lvkit/render/layout.py` (`_resolve_wire_geometry` →
  `Layout.wire_by_uid`), `src/lvkit/render/scene.py` (per-wire lookup).
- Tasks: #84 (decode faithful wire routing), #76 (unify decoder, combs,
  single-vertex, N-way source), #96 (sRN/rSR terminal-center resolution).
