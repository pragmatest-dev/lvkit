# Reverse-engineering the `compressedWireTable` — an investigation log

This is the story of how lvkit decoded LabVIEW's on-disk wire-routing format
(`compressedWireTable`) from nothing but open-source `.vi` files — no LabVIEW
license, no NI documentation, no source. It is a companion to
[`wire-compression-format.md`](wire-compression-format.md), which is the *spec*;
this document is the *method* — the iterations, the scripts, the false trails,
and the corpus we leaned on. It is written so the next person (or the next model)
can reproduce the reasoning and extend it.

## The problem

Every wire on a LabVIEW block diagram is drawn along an orthogonal path that
LabVIEW itself routed. That routed geometry is serialized, per signal, as a hex
blob called `compressedWireTable` on the VI heap:

```xml
<compressedWireTable>0400080503200B18</compressedWireTable>
```

lvkit renders block diagrams. It can *auto-route* wires from scratch, but the
result never matches LabVIEW pixel-for-pixel and the router is O(N²)-ish per wire
(slow on big VIs). If we could **decode** the blob instead, wires would be
byte-faithful *and* fast. The catch: the format is undocumented and the bytes are
dense. We had to crack it by observation alone.

## The corpus — why open source mattered

We cannot legally ship or study NI's own example VIs, so the entire investigation
ran on **permissively-licensed** LabVIEW source (see task #60):

- **OpenG** (`_ogtk` VIs — BSD): array/string utilities. Small, dense, varied
  fan-outs. `Slice 1D Array (I32)`, `Number to Proper Engl Text`,
  `Reverse 2D Array`, `List VI Hierarchy`, `Close Generic Object Refnum` …
- **JKI EasyXML / VI-Tester** (BSD): nested cases, recursion, variant + error
  wires — `XML Loop Stack Recursion`.
- **A PCO-camera acquisition VI** (`MasterAcquisitionFile_PCO_IOS`): the
  flagship stress case — hundreds of signals and deep "comb" fan-ins that broke
  every early hypothesis.

Variety was the point: a rule that holds on `Slice` *and* the PCO combs *and*
recursion is a rule, not a coincidence. The final fork rule was verified across
**764 extracted VIs**.

## The method, in one loop

Every advance came from the same cycle:

1. **Extract, don't parse.** A `.vi` is binary; you cannot grep it. pylabview
   dumps each VI to `*_BDHb.xml` (block-diagram heap) in a memory-flat
   subprocess. Grepping that XML for `<compressedWireTable>` or a `primResID` is
   instant and safe. (Parsing the whole corpus at once OOM-crashes WSL — so we
   never did; we extract-then-grep and parse only the handful of matches. See
   CLAUDE.md.)
2. **Hypothesize a rule** from a few blobs read by hand.
3. **Mine ground truth.** For each fan-out, run a *validating search*: try every
   fork direction, keep only the decode whose leaves land on the **known** sink
   terminals (their centers come from the heap independently). Record what the
   winning decode actually did — `(incoming trunk, token) → chosen directions`.
   This turns "what could the bytes mean" into "what does LabVIEW actually draw."
4. **Measure faithful %** — the fraction of wires that decode to within a
   terminal's width of their known sink — on a fixed reference set.
5. **Refine and re-measure.** Keep the change only if the number goes up with no
   regression. Then **re-verify at corpus scale** before believing it.

The research scripts that ran this loop live in `.tmp/` (gitignored, throwaway);
the durable, reproducible harness is
[`scripts/wire_decode_probe.py`](../scripts/wire_decode_probe.py).

## The layers, as they fell

### 1. The scalar format (vertex count + variable-width lengths)

`byte0` is the vertex count `V` (so `V-1` segments). Lengths are a byte stream,
not fixed-width: a byte `< 0xFF` is a one-byte length; `0xFF hi lo` is a 16-bit
big-endian length for segments ≥ 256 px. This is why the blob length is not a
fixed function of `V`. Confirmed by making the length stream consume *exactly*
`V-1` values with no leftover.

### 2. The single wire (chain)

For a 2-terminal wire, `byte1` is `dir0` (a direction nibble: `08`=E, `04`=S,
`02`=W, `01`=N, screen y-down) and the bends alternate axis, so each stores only
a **sign** bit. The last segment's length is *implied* — it's whatever reaches
the terminal we already know. This gave clean L- and Z-shapes immediately.

### 3. The fan-out is a DFS token stream

For 3+ terminals, `byte1` is a flag, not a direction, and the middle bytes are a
**token stream**: `0x00/0x01` bend (bit0 = sign), `0x02` straight, `0x03` pop,
`0x04-0x07` branch. A leaf is a run of bends ending at a pop; a branch pushes a
junction resumed by a later pop. Classic depth-first tree serialization.

### 4. The forks — three months of the actual difficulty

The fork *topology* was easy; the fork *directions* were not. Progress came in
painful steps, several of them wrong before they were right:

- **`0x04` is the 3-way.** The branch token's low 2 bits select a subset of
  `{N, S, E}`: `0x4→{N,S,E}`, `0x5→{S,E}`, `0x6→{N,E}`, `0x7→{N,S}`. The **4th
  direction, W, is the "unused bit"** for eastbound trunks — a hint the user
  supplied that unlocked the 3-way case (which had been mis-handled as a bad
  fork and thrown to the router).
- **Absolute, not relative — settled by measurement.** It was tempting to read
  the fork directions as *left/right/straight* relative to the incoming wire.
  We built it and measured: it **regressed** faithful-rate from 98.6% → 92.4%.
  The directions are absolute; they only *look* relative because ~99% of the
  corpus forks off an eastbound trunk where `left/right/straight ≡ N/S/E`. The
  data overruled the hand-argument.
- **The turned-trunk anomaly.** On a trunk that had turned *south* (the PCO
  combs), the absolute rule mis-fired: a `0x07` fork needed to tap **west**, not
  north. Mining `(trunk, token)` across the corpus isolated it to a single
  degree of freedom.
- **A dead end: length-sign.** We tested whether a sign bit on the tap segment's
  length encoded the side. It doesn't — the "sign" bit is just the magnitude
  boundary (128–254 = one byte with the high bit set). Disproven and recorded.
- **A wrong conclusion, then overturned.** We briefly concluded the tap side was
  *endpoint-derived* (not in the bytes). It is in the bytes: the fix is the
  **backward → negative-axis perpendicular** remap — any base fork direction that
  would point *backward into the trunk it came from* is drawn along the trunk's
  negative-axis perpendicular (N for a horizontal trunk, W for a vertical one).
  It is a **routing invariant** — LabVIEW never draws a branch back into its own
  trunk — so the geometry is recoverable without ever consulting the endpoint.
  This lifted the deterministic decoder to **99.5%** with no search and no
  tolerance, and was then confirmed across all 764 VIs (e.g. `0x06` on a
  southbound trunk decodes to `{E,W}` = `{N,E}` with the backward `N` deflected,
  34 samples in agreement).

### 5. The tapped sub-format (`flag = 0x01`)

A sink can sit **on** a wire, not at a leaf tip. Those blobs carry `flag=0x01`,
an extra header byte `b[2]`, and a `0x02` STRAIGHT token that runs the trunk
through the terminal. Corpus mining showed `0x02` occurs *only* in tapped blobs,
and `b[2]` indexes the tap vertex. This sub-format is understood but only
partially integrated (mid-wire sink matching is the remaining work).

## What "solved" means here — completeness without guessing

Two fork configurations (`0x07` on W- and N-trunks) are genuinely rare — one W
sample and zero N samples in 764 VIs. Inventing a universal formula from n=1
would be the kind of guessing lvkit forbids ("know it from data, or fail with a
diagnostic"). So completeness is **structural, not formulaic**: the decoder never
emits a wrong wire. The deterministic rule handles the verified ~99.5%; where a
fork's deterministic branch lands on *no* known sink, a bounded, sink-validated
search disambiguates using the `termList` uids we already have; if nothing lands,
the wire falls back to the auto-router. Correct-or-safe for any uploaded VI.

The remaining non-decode residual is **misplaced terminals** (`sRN`/`rSR`
property-node drawers with degenerate heap bounds, task #96) — there the bytes
are fine and the *terminal position* is wrong, so the wire can't be blamed on the
decoder.

## Tooling lessons worth keeping

- **Extract-then-grep, never mass-parse.** Memory-flat. The corpus is ~1 GB of
  XML; the loop stays cheap because we only ever fully parse the matches.
- **Ground-truth mining beats hand-reading.** The validating search, run over
  the corpus and *recorded*, converts guesses into `(trunk, token) → directions`
  tables with counts. Every rule in the spec has a sample count behind it.
- **Measure before believing; verify at scale before shipping.** The relative
  rule *looked* right and was wrong; the absolute rule was verified on 764 VIs.
- **A negative result is a result.** Length-sign and endpoint-derived were both
  recorded as disproven so no one re-walks them.

## Where to look

- Spec + ASCII diagrams: [`wire-compression-format.md`](wire-compression-format.md)
- Reproducible harness: [`scripts/wire_decode_probe.py`](../scripts/wire_decode_probe.py)
- Implementation: [`src/lvkit/parser/wire_table.py`](../src/lvkit/parser/wire_table.py)
- Rendered proof (10 sample VIs): the wire-decoder samples artifact.
