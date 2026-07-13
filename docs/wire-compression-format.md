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

**The token alphabet is exactly `0x00`–`0x07` (8 values). Bit `0x08` is never
set in a token** — verified across every fan-out in the reference set, including
the ones that fail to decode (so it is a real absence, not a decode-filter
artifact). `0x02` STRAIGHT is vanishingly rare (2 occurrences). Note the
contrast with `dir0`, which DOES use bit `0x08`: `dir0` is a
`{N=0x01, W=0x02, S=0x04, E=0x08}` direction **bitset** and is not mutually
exclusive (`dir0 = 0x0D` = E|S|N sets three bits at once — that is the compound
source-tee). Fork tokens use a **different** scheme: reading a fork token's low
bits as that same direction bitset does NOT reproduce the observed eastbound-trunk
directions (it would give `0x05 → {N,S}` where the corpus shows `0x05 → {S,E}`),
so the fork's low 2 bits are not absolute direction bits — see the open problem
below.

A leaf is a chain of BEND/STRAIGHT segments ending at a POP (or end-of-stream).
A BRANCH pushes a junction (resumed later by a POP) and continues the current
leaf. **Every direction is deterministic from the bytes** — bend turns from a
sign bit (relative to the running axis), fork directions from the branch token
(absolute; next section). There is no search and no tolerance: the whole geometry
is recovered from the bytes.

### Source branch (compound `dir0`)

When `dir0` has more than one direction bit set, the **source itself** tees:
segment 0 leaves in one bit's direction and the remaining bits leave as deferred
branches — one junction pushed at the source per remaining bit, spawned on
successive POPs back to it. Verified on a real 3-way source (`dir0 = 0x0D` =
E|S|N, a 6-sink signal in `MasterAcquisitionFile_PCO_IOS.vi`, task #76). This
generalizes to any N; a 2-way source is just N=2.

### Fork direction — ABSOLUTE direction per token, fully in the bytes

The fork direction is **not** under-determined and is **not** re-derived from
terminal positions at decode time — the branch token maps to a fixed pair of
**absolute (screen) directions**, one for each of the two children it spawns: the
**immediate** child (continues the current leaf now) and the **sibling**
(deferred, spawned when this subtree POPs):

| token  | immediate child | sibling(s)  |
|--------|-----------------|-------------|
| `0x04` | **N**           | **S**, **E** (3-way) |
| `0x05` | **S**           | **E**       |
| `0x06` | **N**           | **E**       |
| `0x07` | **N**           | **S**       |

`0x04` is the **3-way** junction `{N, S, E}` (low 2 bits `00`): continue E, tap N,
tap S. So the low 2 bits select which of the priority-ordered directions
`[N, S, E]` the junction takes — `0x04`→all three, `0x05`→`{S,E}`, `0x06`→`{N,E}`,
`0x07`→`{N,S}` — and `W` (bit `0x02`) is the direction never used here. Verified:
`Slice 1D Array (I32)__ogtk.vi` blob `0800080400030001030F...` is a clean 3-way
off an eastbound trunk.

The direction is **absolute, not relative to the incoming wire.** It only *looks*
relative because block diagrams flow left-to-right, so ~99% of the corpus is
`dir0=E`, and for incoming=E the absolute values coincide exactly with a
CW/CCW-of-incoming reading (`cw(E)=S`, `ccw(E)=N`). A `dir0=S` fan-out breaks the
relative reading and proves the rule is absolute (e.g. `Conditional Auto-Indexing
Tunnel__ogtk.vi`: a `0x05` branch off a southbound trunk continues **S** and taps
**E** — not the westward turn a relative rule predicts). Corpus fork directions
are always in `{N, S, E}` (a left-to-right diagram forks up/down/onward, never
back west). The sibling's direction is **known the instant the branch byte is
read** and travels with the junction on the stack — the POP carries no direction
(it is always `0x03`).

Verified deterministically across the corpus: this rule reproduces the geometry
of **331/351** fan-outs byte-for-byte with **zero regressions and zero search**,
and **265/351** land sub-pixel-exactly on their terminal. The handful that still
miss are terminal-attach-point offsets (~4.5px) or a few genuinely mis-placed
terminals, not decode errors. What `#84` read as "recomputed from terminal
positions" was really "LV computed it from positions **at save time** and baked
the absolute direction into the token."

**Absolute vs relative — settled by measurement (2026-07-13).** A relative
reading (immediate/sibling ∈ {left, right, straight} of the incoming direction)
was tested against this absolute table on a 10-VI reference set: it **regressed**
the deterministic faithful rate from **98.6% → 92.4%**. Absolute wins decisively.
It only *coincides* with a relative reading because ~99% of the corpus forks off
an **eastbound** trunk, where `left/right/straight ≡ N/S/E`.

**Fork base sets + the turned-trunk remap (SOLVED, 2026-07-13).** Each fork's low
2 bits select an ABSOLUTE base pair over `{N, S, E}` — `(immediate, [siblings])`:

| token | immediate | sibling(s) |
|-------|-----------|------------|
| `0x04` | **N** | **S**, **E** (3-way) |
| `0x05` | **S** | **E** |
| `0x06` | **N** | **E** |
| `0x07` | **N** | **S** |

On a **turned trunk**, any base direction that points **backward** (opposite the
current trunk direction) is remapped to the trunk's **negative-axis
perpendicular** (`neg_perp` = **N** for a horizontal E/W trunk, **W** for a
vertical N/S trunk). Examples: `0x07` on a southbound trunk — its immediate `N`
points backward (up, against the S flow) → remapped to `neg_perp(S) = W`, giving
tap W / sib S (the comb teeth); `0x05` on a westbound trunk — its `E` sibling
points backward → `neg_perp(W) = N`. This is **fully endpoint-free** and lifts the
deterministic walk to **99.48%** on the reference set (no fork search, no
tolerance).

This **supersedes an earlier wrong conclusion** in this doc's history that the
`0x07` tap side was "endpoint-derived / not in the bytes." It IS in the bytes:
the side is `neg_perp` of the trunk. The length-sign hypothesis was correctly
disproven (the tap length's high bit is only the magnitude boundary: 128–254 = one
byte with bit7 set, ≥256 uses the `0xFF` escape — not a sign), but the resolution
was the trunk-relative remap, not endpoints.

**Why it is derivable (routing invariant).** The remap is not really a stored bit
— it is a consequence of LabVIEW never drawing a fork branch *backward into the
trunk it just came from*. When a base direction would double back, LV deflects it
to the perpendicular. So the geometry is recoverable without the endpoint.

**Error budget (reference set, 2026-07-13).** After the remap, the deterministic
walk's residual (~0.5%, 10 branches) is:

* **Misplaced terminals** — degenerate-bounds `sRN`/`rSR` terminals (task #96),
  a terminal-position bug, not a decode bug (List VI Hierarchy, Close Generic).
* **One deeply-nested mixed comb** (n=8) — 7 of its 8 leaves land exactly; the
  lone straggler is likely a `neg_perp`-vs-`pos_perp` **side** choice (when
  `neg_perp` would collide, LV appears to take the other perpendicular) — the same
  derivable-from-geometry class, not a new stored bit.
* **Two `flag=0x01` tapped-format** fan-outs (straight-through terminal tap) —
  a sub-format the walk does not yet fully model.
* **Compound `dir0` is NOT a failure source.** All compound multi-bit `dir0`
  source-tees decode correctly; it is the turned trunk *axis*, not the direction
  bitset, that breaks forks.

So the remaining path to ~99.9% deterministic is small and quantified: fix the
`sRN`/`rSR` terminal centers (#96), the lone n=8 `neg_perp`-vs-`pos_perp` side
choice, and the `flag=0x01` tapped sub-format. The turned-trunk fork rule itself
is solved (the remap above); the walk is at 99.48% endpoint-free.

### Source branch is the same rule at the source

The compound-`dir0` source tee (previous section) is just this fork rule applied
with the source as the first junction: the extra `dir0` bits are the perpendicular
turns leaving the source. It is not a special case.

### Straight-through tap (`flag = 0x01`)

A sink that sits **on** the wire (not at a leaf tip): the `0x02` token runs the
trunk straight through the terminal. One extra header byte precedes `dir0`.
Handled by matching a sink to any vertex on a leaf's path, not only its tip.

**`0x02` STRAIGHT is the tapped-format marker (2026-07-13).** Across the whole
reference set the `0x02` token appears exactly twice, and both occurrences are in
`flag=0x01` blobs — so `0x02` STRAIGHT essentially only occurs in the tapped
sub-format. The deterministic walk (`scripts/wire_decode_probe.py`) does not yet
match a sink to a mid-wire vertex, so tapped blobs are the two `None`s in the
residual (not a fork-rule error). Observed samples:

```
03010008024852          v=3 flag=1 b[2]=0x00 tokens=[0x02]            n=2
050102080000023F010952  v=5 flag=1 b[2]=0x02 tokens=[0x00,0x00,0x02]  n=2
```

**`b[2]` is the tap-position index (2026-07-13).** Mined 8 validated tapped
fan-outs corpus-wide: in 6 of them `b[2]` equals the **token index of the `0x02`
STRAIGHT** exactly (e.g. `b[2]=0` ↔ `0x02` at token 0; `b[2]=2` ↔ `0x02` at token
2; a `v=10` case: `b[2]=2`, tokens `[0,0,2,6,0,3,1,0]`). So `b[2]` marks **which
vertex on the path carries the mid-wire sink**, and the `0x02` STRAIGHT is how
that vertex is drawn when the trunk passes straight through it. The 2 exceptions
have no `0x02` token at all (all-bend streams like `[0,0,1]`) — either a
tapped-*chain* variant where the tap sits at a bend vertex `b[2]` still points to,
or regex mis-pairings in the mining (20 of 28 candidates were discarded as
mis-paired, so the clean sample is small). Remaining work for the sub-format:
match the sink at vertex `b[2]` and continue the trunk (mid-wire sink matching).

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
