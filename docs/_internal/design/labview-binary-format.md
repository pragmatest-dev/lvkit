# LabVIEW `.vi` binary-format reference (clean-room)

Reverse-engineered facts about the FIXED LabVIEW `.vi`/heap/dataspace binary
format — connector-pane geometry, wire tables, structure/dataspace layout, class
and type records, primitive discriminators. Every fact here traces to a PUBLIC
source (pylabview's read of the binary, NI's public web docs, labviewwiki, or
algorithm/deduction) per the clean-room rules; ship ZERO NI-derived artwork.

This is a **reference** (facts), companion to `ARCHITECTURE.md` (our code shape).
It was consolidated out of the auto-memory, which is behaviors-only. When you learn
a new format fact, add it HERE, not to memory. `[[slug]]` links point at memory
notes (behaviors) or other sections here.

---

## Property nodes execute sequentially top-to-bottom with stop-on-error; sRN (Self-Reference Node) is loop body mapping, not property nodes
<!-- was memory: feedback_property_nodes -->

**Property Nodes (propNode in XML):**
- Execute each row sequentially from top to bottom — NOT parallel
- If an error occurs, execution stops at that point and returns the error
- "Ignore Errors Inside Node" option forces all properties to execute (error still returned)
- The existing `property_node.py` codegen may need updating to model sequential cascade semantics

**Self-Reference Nodes (sRN in XML):**
- sRN is NOT a property node. It's a Self-Reference Node used in formal verification.
- Splits control structures (loops, sequences) into inner and outer components
- The sRN holds the body of a loop/structure; the outer node handles mapping external variables
- Facilitates formal analysis of graphical code structure
- In lvkit's context: sRN nodes hold inner tunnel terminals that route wires through structure boundaries (e.g., flat sequence frames)
- `constants.py` currently mislabels sRN as "Shift register node" — it's actually "Self-Reference Node"

**Why:** User corrected multiple wrong assumptions about sRN during investigation of DAQmx ordering bug.

**How to apply:** Don't conflate sRN with propNode. sRN is wiring infrastructure for structure boundaries. propNode is the actual property get/set drawer.

## Flat sequence frames execute ALL contained nodes — unwired nodes still run and block frame completion
<!-- was memory: feedback_sequence_frames -->

Flat sequence frames act like implicit subVIs. ALL nodes inside a frame execute (potentially in parallel), and the frame does not complete until every node finishes. The next frame cannot start until the previous frame ends.

**Why:** A Wait(500ms) primitive in the same frame as a Write doesn't need to be wired to the Write. Both run when the frame runs, and the frame takes at least 500ms. Ignoring unwired nodes in a frame loses timing, cleanup, and side effects.

**How to apply:** When generating code for a flat sequence frame, emit code for EVERY node in the frame, not just the ones in the wired dataflow chain. Unwired nodes (like Wait) must still appear in the generated Python for the frame.

## "Pre-LV9 (LV8.2) VIs have no VCTP; connector-pane types live in the CONP block (decoded, #11 DONE); non-interface controls fall back to FP-heap reconstruction"
<!-- was memory: project_pre_lv9_no_vctp_trec -->

**Pre-LV9 VIs (LabVIEW 8.2) have no `<VCTP>` block.** pylabview reads types only
from VCTP, so it resolves nothing for them. But the types ARE recoverable
clean-room (#11 DONE — 148 unresolved connector-pane terminals -> 0):

1. **CONP block = the connector-pane type pool** (the authoritative source). Same
   bottom-up TypeDesc format as VCTP, sidecarred by the extractor as
   `*_CONP.bin` (pylabview leaves it raw: "we do not know how to parse complex
   form of CONP"). Decoded by `parser/conp_types.py` -> fully-named LVTypes:
   cluster FIELD names (so error clusters are detected), class names, enum item
   labels, refnum sub-kinds (Queue/LVObjCtl/...). Layout: `u32 count`, then TDs
   `[u16 len][u8 flags][u8 TD_FULL_TYPE][body ending in a pstr name]`; a `0xf0`
   TD is the connector-pane slot->TD-index map. LV9+ VIs carry an empty 2-byte
   CONP stub (types moved to VCTP), so the decoder is a no-op there. Wired into
   `extract_fp_terminals` as a slot-correlated overlay (commits 632e509, e7ac326).
2. **CONP is INTERFACE-ONLY.** A front-panel control not wired to the connector
   pane (~37/2192 in JKI) is NOT in CONP -> use the **FP-heap DDO reconstruction**
   (`parser/fp_heap_type.py`, commit 11caa50): structure + enum labels from the
   heap subtree (ring items in a `multiLabel <buf>`, cluster fields via
   `<ddoList>`), but no field NAMES (pre-LV9 label text is a dropped text-pool
   ref). Precedence: VCTP (LV9+) > CONP (LV8.2 interface) > FP-heap (private/fallback).

**Dead-end corrected:** `TRec` blocks are GEOMETRY (72-byte terminal records),
NOT the type pool — an early wrong guess. Field names are NOT in the FP heap for
typedef'd clusters (they live in the typedef); CONP carries them. Clean-room only
([[feedback_no_labview_clean_room]]); index freshness via [[project_index_self_invalidates]].

Pre-LV9 terminal NAMES are also recovered from CONP (48efeba + 578434e): fed into
`_recover_or_warn_unresolved_labels` as a slot-keyed source after the (absent)
VCTP flat-type labels, so `control_N` becomes `error out` / `reference in` /
`Version String Out` / `Action`. Result: LV8 PUBLIC terminals are 0 `control_N`.

A class refnum's CONP body is `[class name][terminal label]` (two pstrs); the
label is the pstr AFTER the `.lvclass` name. LabVIEW's DEFAULT label for a
dynamic-dispatch terminal IS its class name, and a SECOND same-class terminal is
disambiguated `<class> 2` (e.g. `Class1.lvclass` / `Class1.lvclass 2`) — that
"2" is a real 16-byte length-prefixed string, NOT garbage (a first wrong guess
in 48efeba treated it as garbled and guarded it out; 578434e corrected this).
Same convention as LV13's `MyClass.lvclass in/out` labels. A developer rename
(`reference in`) sits in the same slot.

Residual: one private class control renders `UDClassInst refnum` (class name not
in CONP, which is interface-only). Remaining `control_N` are all LV9+ globals
(empty CONP), a separate pre-existing label-resolution gap.

## "Compound Arithmetic (cpdArith) operation is in objFlags bits 16-18, NOT dcoFiller — corpus-verified decode that fixed"
<!-- was memory: reference_cpdarith_objflags -->

The LabVIEW **Compound Arithmetic operation** lives in the cpdArith node's
`objFlags`, bits **16-18** = `(objFlags >> 16) & 0x7`. Enum (matches LV's mode
order): **0=add, 1=multiply, 2=and, 3=or, 4=xor**. Bit 19 is a separate
always-set marker (so raw objFlags = 0x8/0x9/0xA/0xB/0xC 0000).

`dcoFiller` is **per-terminal invert/type data and does NOT select the op** —
the earlier "operation = dcoFiller low byte (0=add,1=or,2=and), 0x100=flag"
theory was WRONG and caused #60: it collapsed all 72 boolean "…Changed"
detectors into add(→OR) and turned MD5 H (xor) into add. Proof: dcoFiller 256
co-occurs with add/and/or/xor; and MD5-sum/Reshape (genuine add) share
dcoFiller 256 with the AND family. objFlags disambiguates cleanly.

Corpus check (`.tmp/cpd_objflags.py`, glob `samples/**/*_BDHb.xml`): add→Trim/
Reshape/MD5-FGHI; and→all "X Changed"/"X Array Changed"+Boolean Trigger (63 VIs);
or→Create Dir/file-refnum-wait guards (9); xor→MD5 H (H(x,y,z)=x⊕y⊕z).

Fix (#60, DONE): `CpdArithHandler._extract_operation` in
`src/lvkit/parser/node_types.py` reads objFlags; codegen (`compound.py`) already
handled all 5 ops so no codegen logic change. "I32 Changed" now generates
`state != state_updated and (not first_call)` (was `... or ...`); MD5 H now `x^y^z`.
Method: extract-then-grep XML per memory [[feedback_no_corpus_parse_sweep]];
data-not-guess per [[feedback_no_heuristics]]. Also fixes #115-adjacent
operand-inversion checks.

## "LabVIEW connector-pane terminal INDICES run Right→Left, Bottom→Top — why our primitive/vilib terminal indices look \"reversed\" vs doc order"
<!-- was memory: reference_connector_pane_terminal_index -->

**Connector-pane terminal indices must be read per-VI — there is NO shared rule.**
Each VI/primitive carries its own connector-pane pattern AND orientation, so the
index→terminal mapping differs from one to the next. That is what makes these
indices hard, and why the resolver rule "confirm the index from observed wiring,
NEVER from doc order" is non-negotiable (see [[feedback_no_string_matching]]).

Not even a reliable baseline exists: the index **origin corner and direction differ
by pattern**. 4x2x2x4 indexes **Right→Left, Bottom→Top** (idx0=bottom-right; LabVIEW
Wiki "Connector pane" → *Index of Terminals*; VI Scripting `ConnectorPane
Controls[]` order) — the tendency behind our "reversed vs doc order" surprises
(Search 1D Array output=idx0, Split 1D Array numeric=idx2/array=idx3, 1142 To Long
Integer output=idx0 before input=idx1). But **5x2x2x2x5** (misnomer "5x3x3x3x5")
starts idx0 in the **top-left and increments the other way** — same family, opposite
origin (reason unknown). So there is no rule to apply blind; read each VI's indices.

**Caveat: NOT every VI uses the same grid.** There are many patterns (1-terminal,
2x2x2x2, 4x2x2x4, 5x3x3x5, asymmetric ones; each has an ID in the 4800–48xx
"connector pane values" chart), and Rotate/Flip remaps the whole thing. So the
R→L,B→T rule holds only for the **default orientation**; the concrete index is
**pattern- AND orientation-dependent**. Do not assume 4x2x2x4.

Example — 4x2x2x4 in default orientation (cell → terminal index):

```
 col1(L)  col2  col3  col4(R)
   11       7     5      3
   10       6     4      2
    9                    1
    8                    0
```

Index **0 = bottom-right**; count UP each column, then move LEFT. Right side (low
indices) is normally OUTPUTS, left side (high indices) INPUTS.

Consequence: observed heap `termBounds` / wiring stay the **ground truth**; the
wiki rule EXPLAINS the tendency, it does not license a fixed grid. Geometry
derivation (sort terminals R→L, B→T) works for the default orientation of ANY
pattern but must account for rotate/flip — that's the (bounded) basis for #38
(recover ordering for empty/partial panes) and `scripts/audit_terminal_order.py`,
and why [[feedback_no_string_matching]] holds (index from data/geometry, never
doc order). Fetch wiki PNGs via `labviewwiki.org/w/images/...` (MediaWiki is
server-rendered — see [[reference_ni_web_docs_fetch]]).

## "Connector-pane patterns are ALL column-major; conIds 4816-4825 & 4833-4835 increment (index 0 = top-left), all others decrement (0 = bottom-right). Full catalog is src/lvkit/data/connector_pane_patterns.json. Primitives carry NO conId — termBounds are in the XML directly."
<!-- was memory: reference_connector_pane_patterns_catalog -->

**I keep forgetting this; the maintainer has said it multiple times. STOP re-deriving it.**

LabVIEW connector-pane patterns are keyed by **conId** (the pattern number in the
VI's `FPHb <conPane><conId>`). The full catalog lives at
**`src/lvkit/data/connector_pane_patterns.json`** (36 patterns, conIds 4800-4835),
loaded by `src/lvkit/connector_pane_geometry.py`. Authoritative reference:
**https://labviewwiki.org/wiki/Connector_pane_patterns** — use it to navigate,
don't guess from a rendered image.

**The ordering rule (now encoded as the `order` field on every pattern, added
2026-08-19):**
- EVERY pattern is laid out **column-major** — fill each column top→bottom,
  columns left→right.
- `column_major_increment`: index starts at **0 in the top-left** and increases
  along that path. Set = conIds **4816-4825 and 4833-4835**.
- `column_major_decrement`: index **decreases to 0** (0 = bottom-right; R→L,
  B→T). Set = **all other** conIds.

**PRIMITIVES ARE DIFFERENT — the pattern catalog does NOT apply to them.** A
primitive node (`class="prim"`) has **no `<conId>`** (verified: 1540's node
`has_conId=False`). Instead every primitive terminal serializes its real
**`termBounds` (pixel geometry) + `parmIndex` DIRECTLY** in the block-diagram
`<dco>`. So a primitive's per-index geometry is read straight from the XML — no
pattern lookup, ever. The catalog is a VI/subVI-render concern only.

**Consequence for primitive terminal resolution** (the thing I conflated): reading
the pane pattern was never the blocker — `termBounds` is already in the XML.
Ambiguity between **same-typed** terminals (e.g. 1540's delimiter vs format, both
`String`) is resolved by the **caller's wiring** (which control/constant feeds
each parmIndex) + semantic coherence, NOT by geometry or by NI doc order. See
[[reference_connector_pane_terminal_index]], [[feedback_use_resolve_primitive_skill]].

## "Validate a primitive's parmIndex->name against the NI doc connector-pane IMAGE (independent cross-check) — glyph terminal-role detection"
<!-- was memory: reference_glyph_terminal_validation -->

The strongest clean-room check on a primitive's terminal mapping is the NI
**public connector-pane doc image**, not caller wiring alone. The doc figure
draws the real icon at real bounds, so the icon's pixel box maps **1:1 to
`termBounds`**; each wire's **drop-in point** on the icon perimeter gives that
terminal's parmIndex a second, independent way, and the wire's **far-end label**
is NI's actual terminal NAME.

So every primitive's index+name should be validated as a **three-way cross-check**:
`<dco>` parmIndex  vs  image drop-in geometry  vs  image label. Agreement
corroborates; **disagreement is a bug flag** on the `primitives.json` entry (or on
our XML reading). Same-typed terminals (e.g. 1540's delimiter/format/array, all
String) are just where it matters most — types can't order them and no caller may
name them, so the image is the ONLY resolver there; everywhere else it's the
independent check. Caller wiring shows a terminal's *use* (name still inferred);
the image gives the label outright.

Applies to any primitive whose NI page shows a wired connector pane (nearly all
function pages; a few internal resIDs have none). For expanding nodes it validates
the NAMED terminals, not the positional `arg N` leftovers.

Method + validated PIL-only reference prototype (icon = largest non-wire-ink
connected component; error-cluster wire = olive 127,127,0 + sandwiched black core,
must be masked or it bridges the icon):
`docs/_internal/design/glyph-terminal-role-detection.md` (+ `..._prototype.py`).
Cross-referenced from `docs/_internal/maintainers/primitive-terminals.md`.
Prototype-stage; not yet a package module. See [[feedback_use_resolve_primitive_skill]].

## Case selector per-frame values live in the dataspace *.xml DFDS (not the BD heap); how to decode + correlate them to case nodes
<!-- was memory: reference_case_selector_dataspace -->

Case-structure per-frame selector VALUES are NOT in the block-diagram heap
(`*_BDHb.xml`) for the OpenG corpus — the `SelectStringArray`/`SelectRangeArray32`
elements case.py also reads are absent there. The real data is in the **main
dataspace `*.xml`** (DFDS), as `<DataFill>` clusters of shape:

```
{I32 displayed_frame, I32 range_count,
 Array[Cluster{I32 start, I32 end, U8, U8, I16 diagram_idx}],
 Array[String] value_strings, Cluster trailer}
```

- STRING selector: `start`/`end` index `value_strings` (a frame can match
  several, e.g. jpe/jpeg/jpg); numeric/enum: `start`/`end` are literal values.
- The frame in NO range is the implicit **Default**.
- `displayed_frame` (the leading I32) = the frame LabVIEW last showed → answers
  #81 (the saved displayed frame), now on `ParsedCaseStructure.displayed_frame`.
- pylabview emits each table TWICE (edit + run copy) → dedupe by content.

**Correlation (DFDS TypeID ≠ BD VCTP TypeID — two namespaces, don't cross-ref):**
sort case nodes by their `caseSel` `typeDesc TypeID(N)` (VCTP index), sort unique
tables by `DataFill` TypeID, and zip — both indices are assigned in the same
DCO-enumeration pass, so orders agree. Gate: apply only if counts match AND every
pair is kind-consistent (string table ⟺ string case). Boolean cases store no
table (True/False implicit) — exclude them from the zip. Verified on "Draw Image
from File__ogtk.vi" (5 cases): both string nodes zipped to the two string tables,
integers to numeric — kind cross-check confirms the order invariant.

**Case Insensitive Match (#58):** the select node's `objFlags` **bit 24**. Default
string matching is case-SENSITIVE (bit clear); LabVIEW 2015+ draws "A=a" (string
pink) at the case's bottom-left when set. Verified: set only on Draw Image's
file-extension case (must be insensitive — no lowercasing, lowercase-only values),
clear on empty-vs-nonempty string checks. pylabview does NOT decode this bit.

Implemented in `src/lvkit/parser/nodes/case.py`
(`parse_selector_tables` / `_apply_selector_tables`), threaded from `parser/vi.py`
via `main_xml`. Fixed #82 (was fabricating True/False for all non-boolean cases).
Renderer `_selector_label` + codegen `_build_frame_pattern` now emit multi-value
labels / `case "jpe" | "jpeg" | "jpg":` OR-patterns. See [[feedback_no_heuristics]].

## Event Structure per-frame labels ARE reconstructable from EventSpec (ddoUID->FP caption + type code); only the displayed frame has faithful heap text
<!-- was memory: reference_eventspec_frame_labels -->

An Event Structure's BD heap stores the faithful selector text (`selString`/`textRec`) for the
**displayed frame ONLY**; other frames were showing bare `[N]`. This is NOT undecodable (an earlier
#75 note wrongly said EventSpec "needs an undocumented internal ID scheme"). FIXED in commit
`abf0421` (2026-07-21), `parser/nodes/event.py`.

Each `<SL__arrayElement class="EventSpec">` (under `EventNodeEvents` in the eventStruct heap) fully
specifies one frame's event:
- `diagramIdx` — the 0-based event frame this configures
- `ddoUID` — the SOURCE control's FP-heap DDO uid. Resolve to the control's caption via
  `*_FPHb.xml`: `fp_root.find(".//ddo[@uid='<ddoUID>']")` then the existing `extract_label()`
  (caption at `ddo/partsList/SL__arrayElement[@class='label']/textRec/text`) — the SAME lookup
  `vi.py::_parse_ddo` already uses. `ddoUID=0` = no source control (pane/app/filter/timeout event).
- `type` — event-type code. **Only 2 codes are clean-room confirmed**: `1073741826`=Value Change,
  `1073741825`=Timeout. Others (e.g. `-2147483645`=0x80000003, a filter-event) are NOT named —
  degrade to control-only or bare `[N]`, NEVER fabricate a type name ([[feedback_no_heuristics]]).

Label format `[N] "Control": EventType` (control+type) / `[N] EventType` (no control) / `[N]`
(neither). Displayed frame's own `selString` still wins for its frame. Verified: VI Tester About.vi
→ Cancel/Website Button/copyrights Value Change; pco 9-frame struct → Timeout + Quit/Image Width
Value Change. To extend: add confirmed type codes to `_CONFIRMED_EVENT_TYPES` as they're verified.
Related: [[reference_case_selector_dataspace]] (case selector values live in dataspace DFDS, a
DIFFERENT mechanism — event labels are in the BD heap EventSpec, not the dataspace).

## "BD <compressedWireTable> = LabVIEW's real routed wire geometry — DECODED + rendered by default (FAITHFUL_WIRE_TABLE=True, task #84 DONE); auto-router is the fallback"
<!-- was memory: reference_compressed_wire_table -->

Every block-diagram `<signal>` (wire) carries `<compressedWireTable>HEX</...>` =
**LabVIEW's actual routed wire geometry** (the bend points it drew). pylabview
stores it raw only.

**CURRENT STATE (task #84 COMPLETE — verify vs code, but this is what shipped):
lvkit DECODES it and renders LabVIEW's faithful geometry BY DEFAULT.**
- `render/wire_table.py` decodes single-net (`decode_wire_mid`) and fan-out
  (`decode_fanout`) blobs into bend polylines; endpoint-validated +
  orthogonality-checked so a wrong decode can't pass.
- Module constant `FAITHFUL_WIRE_TABLE = True` (in `parser/wire_table.py`,
  imported by `render/scene.py`). `parser/layout.py` pre-decodes each signal,
  keyed by **destination terminal uid** (exact identity lookup, no rounding).
- In `scene.py` (~1282): if the dest-uid has decoded geometry, use it for the
  wire's `mid` points; else fall back to the visibility-graph auto-router
  (~0.2% of wires — clean-orthogonal, just not pixel-faithful). Set
  `FAITHFUL_WIRE_TABLE=False` for the A/B baseline (byte-identical old output).
- **DO NOT remove the auto-router** (user decision): it's the A/B baseline AND
  the fallback for garbled/undecodable tables. See
  [[project_wire_routing_rearchitecture]] / [[reference_wire_lane_assignment]].

**Wire endpoints are ALWAYS the terminal centers, not the decoded geometry.**
scene.py builds each branch as `_compress([src_center, *mid, dst_center])` —
faithful/router only supplies the MIDDLE; the two ends snap to the terminals.
So a wrong terminal-center X (e.g. the Bundle field-terminal mid-box bug,
[[feedback_coercion_dot_placement]] neighbourhood) makes the final leg jerk
off LabVIEW's own path even when the geometry decoded perfectly. Fixing the
anchor (snap mux field terminals to the node edge) makes the endpoint agree
with the faithful geometry — see `_reposition_mux_terminals` in scene.py.

Payoff realized: pixel-faithful wires + ~400× faster than the A* router (decode
≈2µs/wire vs ≈823µs/wire; a full render dropped ~24%).

---
**Decode grammar (hard-won RE, now IN render/wire_table.py + tests — kept here
only because it's near-impossible to re-derive):**

*Single-net (2-endpoint):* `byte[0]=V` vertex count → `V-1` orthogonal segs.
`byte[1]=`seg-0 dir quadrant (`08`E/`04`S/`02`W/`01`N, screen coords y-down).
Next `V-2` bytes = per-bend sign bits (`00`=+axis, `01`=−axis; axes just
alternate H↔V — that's the compression). Next `V-2` bytes = seg lengths (last
seg implied by endpoint). **Length escape:** a length ≥256 is `0xff <hi> <lo>`
(so the blob is NOT always `2V-2` bytes — the old strict guard wrongly fell
back). Handled by `_decode_lengths()`.

*Fan-out (>2 endpoints):* a **linked-list/chain** token stream, NOT counted
splits. Layout `[V][flags][dir0][V-2 tokens][V-1 lengths in DFS order]`.
Tokens: BEND `00/01` (relative, ±axis); BRANCH bit `0x04` set (`05`=+tap,
`06/07`=−tap); `04` = MULTI-WAY junction (stays open across pops); `0x03` =
POP/leaf terminator (resume last junction's other child). `dir0` may be
COMPOUND (`0c=08|04` = source itself branches). `flags b[1]=0x01` = a sink sits
MID-WIRE (collinear tap, extra `0x02` STRAIGHT header byte). Fork direction/role
is UNDER-DETERMINED in the bytes (LabVIEW re-derives from terminal positions it
already has) → resolve the few free bits by validating leaf-ends against known
sink centers from termList; unresolvable ~0.2% fall back to the auto-router.

Fallback tail: dominated by OpenG type-templated copies of the same wire (e.g.
`Slice 1D Array` ×~19); the n=4 Slice shape (branch-token-position ≠ geometric
junction, ~2 segs deeper) resisted every general rule — left on safe fallback.

## "SVG renderer wire separation = post-routing interval-coloring lane pass (lane_pass.py); why router-integrated net-awareness lost the bake-off"
<!-- was memory: reference_wire_lane_assignment -->

Phase B of the lv-renderer wire work (#23 tangential, #25 junction dots). Shipped as `src/lvkit/render/lane_pass.py` (commit 5922e23). See [[project_wire_routing_rearchitecture]].

**The algorithm (USER'S design — interval-graph coloring / left-edge channel routing):**
Route every wire NOMINALLY with the plain router (untouched), then run a POST-routing pass:
1. Snap each branch strictly orthogonal (router endpoint stubs can be ~2px off-axis via `align_tol`; slicing on strict eps otherwise mangles them into diagonals). This also FIXED a latent bug: the plain router was emitting ~93 diagonal segments in one sample.
2. Slice branches into axis-aligned segments (H: track=y, interval=[x_lo,x_hi]; V: track=x, interval=[y]).
3. Per track, a wire's run is an interval bounded by src↔dst distance. Two DIFFERENT-net segments conflict only where their intervals OVERLAP (>~2px) and tracks are within one pitch (`wire_width + 2·casing ≈ 5px`). Union-find into conflict clusters.
4. Left-edge color each cluster: sort by lo, assign each segment the lowest lane whose occupants are same-net OR non-overlapping. SAME-net shares a lane for free (one signal). Lane k → base_track + k·pitch.
5. A segment that overlaps nobody stays on its track → DEAD STRAIGHT. Wires jog ONLY where they genuinely share a span — not to dodge a mere crossing.
6. Obstacle-aware: refuse an offset that would push a segment through a node/structure interior, out of confinement, or swing a tunnel/border face (leave it at nominal track).
7. Junctions: where two SAME-net branches stop sharing a coincident trunk and diverge → a dot (geometric `_last_common` walk, not exact point-prefix). A 3-way string fan-out → 2 dots.
HARD GATE: scan every output segment, assert zero diagonals (both dx,dy nonzero).

**Why this WON a 3-way bake-off (all measured on xml_loop_recursion / stacked_sequence):**
- **Variant A (per-wire HARD reservation — other nets' segments as obstacle rects):** tangential got WORSE (138→217) — a rect blocks CROSSINGS too, so wires kink to dodge perpendicular crossings that were fine; greedy sequential displaces into fresh conflicts. Also ~14s. DEAD END.
- **Variant B (net-aware COST FIELD in the router's Dijkstra — diff-net penalty + same-net bonus):** worked (tangential 34) but flat per-edge penalty on any overlap>1px forces jogs for brief grazes (bends 730 vs baseline 463 = kinky), emergent junctions unreliable, and ~14x slower (per-edge registry scan + grid inflation). Router became heavy/coupled (+217 lines, 6 knobs, weighted adjacency).
- **Hybrid (A's tap + B's diff-net cost):** best of those two (tang 29) but still kinky (730 bends) and ~19s — kinks vs tangential are a FIXED trade-off on any penalty mechanism (one knob slides between them).
- **Lane pass (winner):** tangential 35, bends 633 (baseline-straight; +150 is orthogonalizing the router's own 93 diagonals, a net FIX), 84 reliable dots, 0 diagonals, O(n log n) ~5ms (no perf regression), 936 tests pass, deterministic.

**KEY LESSON (load-bearing for future routing work):** wire separation is a COORDINATION problem — decide all wires' tracks TOGETHER — which belongs in an ORCHESTRATION pass over a GENERIC router, NOT baked into the router's per-edge cost (the layer hardest to make correct AND fast). Interval coloring is exact + O(n log n) because exclusivity is only needed for the span a wire actually occupies (bounded by src↔dst), and only between OVERLAPPING different-net intervals; same-net shares freely and its divergence gives junctions. See [[feedback_delegate_implementation]] (Opus designed/reviewed via rendered images, Sonnet agents implemented in worktrees).

## "LabVIEW Formula Node numeric oracle (issue #8) — source of the locked semantics test + the open ~/negative-bitwise anomaly and still-unsupported funcs"
<!-- was memory: reference_formula_node_oracle_issue8 -->

**Oracle source:** github.com/pragmatest-dev/lvkit **issue #8** — @Himmelt ran `docs/formula_semantics_probe.md` (Script 1 main, Script 2 edge) in a real LabVIEW Formula Node and pasted RI (int32) / RF (double) / RX (edge) results in the comments. This is the ground truth the NI docs leave underspecified. The understood, consistent rows are locked as executing assertions in `tests/test_formula_semantics.py`. The repo holds the semantics; this note holds what is deliberately NOT locked (the open questions).

**Open anomaly — needs rfried/Himmelt interpretation, do NOT guess a fix:** LV returned **2147483647 (INT32_MAX)** for `~0`, `~5`, and `-1 & 255`, while ordinary bitwise is normal (`12&10=8`, `12|10=14`, `12^10=6`, `1<<31=-2147483648` wraps, `-8>>1=-4`). So bitwise-complement and a negative `&` operand behave unlike two's complement (our Python `~` gives -1/-6, `&` gives 255). Inconsistent with both pure-wrap and pure-saturate (since `1<<31` wrapped to MIN, not MAX). Likely a probe/indicator artifact or a real LV quirk — excluded from the locked test until clarified. See [[feedback_no_heuristics]].

**getexp/getman — DONE (2026-06-22):** added `_lv.getexp`/`_lv.getman` (frexp-based, mantissa ∈ [1,2)).

**rand / sizeOfDim / N-D indexing — DONE (2026-06-23):** `rand()`→`_lv.rand` (`random.random()`; runtime-nondeterministic by design, codegen stable). `sizeOfDim`→`_lv.size_of_dim(arr, dim)` (natural nested convention: dim 0 = `len(arr)`, deeper dims descend; oracle `sizeOfDim(A,0)=5` ✓). N-D indexing: AST `Index.name:str`→`Index.base:Expr` + parser chained-subscript loop, so `a[i][j]` reads and writes emit native nested Python indexing. The oracle's apparent 2-D dim *reversal* dissolved — `sizeOfDim(B,0)=3` means Himmelt's B was entered 3×2, so the natural convention is correct (not a guess). **Still a known divergence (not implemented):** LabVIEW array index OOB → element default (0); lvkit raises `IndexError` for ALL dimensions — orthogonal, would require routing every `a[i]` through a safe-index helper.

Still UNSUPPORTED (fail loud): nothing from the probe remains except the `~`/negative-bitwise anomaly below.

**Script 2 edges (div0 / sqrt(-1) / domain) — DONE (2026-06-22):** rfried confirmed the rationale — *a Formula Node has no error terminal, so LV coerces to IEEE `inf`/`nan` instead of trapping*. Implemented non-raising `_lv` wrappers (`div`, `powf`, `sqrt`, `ln`, `log10`, `log2`, `asin`, `acos`, `acosh`, `atanh`; `rem`/`lvmod` guard zero divisor). Emitter routes domain funcs through them; `/` wraps to `_lv.div` unless the divisor is a nonzero literal; `**` wraps to `_lv.powf` unless the base is a non-negative literal (so `2**n` stays the plain operator, no churn). Locked in `tests/test_formula_semantics.py`. Matches oracle: `1/0→inf`, `0/0→nan`, `sqrt(-1)→nan`, `ln(0)→-inf`, `(-8)**(1/3)→nan` (was a Python *complex*, a latent bug), `0**0→1`. See [[project_formula_node_rc1]].

**Pre-existing non-determinism — FIXED (2026-06-23, branch `deterministic-codegen-ordering`, commit `ebc6daa`).** Codegen output varied with `PYTHONHASHSEED`: per-VI node UIDs live in sets, and ordering-sensitive steps iterated them, so independent parallel ops (and their collision-suffix names `output_array_696`↔`_999`) swapped between runs. Root: `get_operation_order` / `_sort_inner_uids` did `add_nodes_from(set)` + `nx.topological_sort` (hash-tied), plus `_get_children_of` and the `get_operations` disconnected-append iterated sets. Fix: shared `_node_order_key` (VI base, then numeric object id) in `graph/core.py`, applied at every order-materializing site + `nx.lexicographical_topological_sort(key=...)`. Regression guard: `tests/test_determinism.py` generates `samples/DAQmx-Digital-IO/In.vi` (a parallel-tier VI) under 3 hash seeds and asserts byte-identical output. See [[project_parallel_execution_gap]].

## "LabVIEW ordered-comparison primitives share ONE identical 2-scalar->bool pane; the operator is resolvable ONLY from logical context, never the pane — and the resIDs were historically complement-mislabeled"
<!-- was memory: reference_comparison_prim_operator_from_context -->

The LabVIEW ordered comparisons (`<`, `>`, `<=`, `>=`) all serialize with an
**identical connector pane**: two polymorphic scalar inputs (idx2 = TOP = `x`,
idx1 = BOTTOM = `y`; codegen emits `in_2 <OP> in_1` = `x OP y`) and one boolean
output. So the pane/terminal types CANNOT distinguish them — the operator is
recoverable ONLY from the surrounding dataflow's logic. Equal?/Not Equal?
(symmetric) are the easy ones; the four ordered ones need a *forcing* context.

**Forcing contexts that actually pin an ordered operator (clean-room):**
- **Assertion fail-frame polarity** — e.g. JKI `failUnlessEqual.vi` tolerance
  branch: idx2 = `|x-y|` (Subtract->Abs), idx1 = `|delta|` (Abs); TRUE frame =
  pass = within-tolerance forces `|x-y| <= |delta|`.
- **Pluralization idiom** — JKI `TextTestRunner run.vi`/`processResult.vi` port
  CPython unittest `"Ran %d test%s"`: a Select gates the `'s'` suffix on the test
  count vs 1 (FALSE at count==1 -> singular, TRUE at count>=2 -> plural), forcing
  `count > 1`.
- **Wait-with-timeout loop** — OpenG `Close Generic Object Refnum`: loop stops on
  `elapsed >= timeout` (`<` would break at entry); forces `>=`.
- Then **bijection** over the six ops closes the last one.

**Trap this caught (fixed 2026-08-18):** three of the six were complement-
mislabeled in `data/primitives.json` — 1104 Greater?->Less Or Equal?, 1110
Less?->Greater?, 1111 Less Or Equal?->Less? (1102/1103/1105 were already right).
Correct pairs: `==`/`!=` (1102/1105), `<=`/`>` (1104/1110), `>=`/`<` (1103/1111).
Lesson: when ONE ordered comparison is proven wrong, DON'T assume a uniform
"complement-swap" — enumerate all permutations and force >=2 of the three
unknowns from independent contexts (the third follows by bijection). My initial
two-hypothesis framing missed the actual (third) permutation.

Operands arrive through case/loop tunnels the raw block-diagram wire table shows
as `?`; resolve them via `get_vi_context` (the codegen layer threads tunnels) or
read the generated Python. See [[feedback_primitive_polymorphism]],
[[feedback_use_resolve_primitive_skill]].

## "LabVIEW BD zPlaneList is FRONT-to-back — document index 0 is the FRONTMOST object (drawn last / occludes), higher index = further back. Verified against issue-35 reference."
<!-- was memory: reference_zplanelist_front_to_back -->

A block-diagram diagram's `zPlaneList` (the geometry walk's iteration order) is
ordered **FRONT-to-back**: the element at document **index 0 is the FRONTMOST**
(it occludes everything behind it); each later element is further back.

To PAINT correctly, draw **back-to-front**: the highest zPlaneList index first,
index 0 LAST (so it lands on top). In render terms, if you capture a paint rank
= document index (`Layout.z_order`), sort a container's siblings **descending**
by that rank before drawing.

**Evidence (clean-room):** issue #35 repro ("Objects not correctly hidden by
structures.vi"). Two overlapping For loops: the big loop `43` is at root
zPlaneList index 0, the small top-left loop `173` at index 4. The reference
screenshot (`ref-labview.png`, zoom the overlap corner) shows the **big loop
`43` IN FRONT**, occluding the small loop's bottom-right corner. So index 0 =
front. The plan had claimed "backmost first" — that was WRONG; do not trust it.

**How to apply:** when consuming zPlaneList order for paint, the FRONT object is
the LOW index and must draw LAST. See
[[feedback_dont_defend_fossilized_decisions]] and
[[feedback_verify_render_against_reference]].

## "Coercion dots sit on the terminal's BORDER on the wire-ENTRY side, never in the middle of a glyph"
<!-- was memory: feedback_coercion_dot_placement -->

Coercion dots must sit on the **border of the destination terminal**, on the
**side the wire actually enters** — never in the middle of the terminal's glyph.

**Why:** a dot at the terminal CENTER lands on top of a big border-terminal
glyph (a For-Loop's **N** count terminal, tunnels) and obscures its label. And
the entry side is geometric (toward the source), not the terminal's role
default — a wire can enter N from any side.

**How to apply:** place the dot at the edge of the terminal's box crossed by the
ray from the terminal center toward the wire's entry stub. In the renderer:
`render/scene.py::_entry_edge_point(center, bounds, toward=dst_in)`; the
terminal's box is `layout.node_bounds[raw_dst]` (heap termBounds, keyed by
terminal uid — NOT only `border_terminals`, which omits N). The arith-primitive
path (`_arith_coercion_dots`) already seats dots on the input edge via
`_wire_edge_point`. Fixed 2026-07-11 on lv-renderer.

**Corollary:** the `i` (iteration) terminal is an OUTPUT — a coercion dot must
NEVER appear there (coercion is on inputs only). See [[project_task_list]] #11.

## "A .lvclass records its parent in the XML <Item Type=\"Parent\" URL=.../> OR the binary ParentClassLinkInfo — not always both; URLs are relative to the .lvclass FILE-as-directory"
<!-- was memory: reference_lvclass_parent_from_xml_item -->

A `.lvclass` records its **parent class** in one of two places, and NOT every
non-root class has both:

1. **Plain XML** `<Item Name="X.lvclass" Type="Parent" URL="../../X/X.lvclass"/>`
   (inside a `Parent Libraries` group) — carries the parent NAME **and a relative
   URL straight to the parent file**. This is the richest source.
2. **Binary** `NI.LVClass.ParentClassLinkInfo` property (LabVIEW-base64) — name
   only, no clean path.

The LabVIEW-Icon-Editor classes (e.g. `Icon Framework.lvclass extends Layer`)
carry ONLY the XML Item — no binary property. So "no `ParentClassLinkInfo` ⇒ root"
is WRONG: it silently drops inheritance, and every method the class calls **by
inheritance** (a dispatch method declared on an ancestor, e.g. `Layer`'s
`GET_LayerData.vi` called on an `Icon Framework` object) becomes unresolvable —
the SubVI renders as a bare box and the web can't stage it. `parse_lvclass` must
read the Item (prefer it; fall back to the binary).

**`.lvclass` URLs are relative to the class FILE treated as a DIRECTORY.** Member
VIs read `../X.vi` and the parent reads `../../X/X.lvclass`; resolve against
`lvclass_path` ITSELF (not its parent dir) so the extra leading `..` is absorbed:
`Path(lvclass_path) / url` → correct. The parent commonly lives in a **sibling
subtree** (`../../Layer/`), so a directory walk-UP (`_walk_up_find`) can NOT reach
it — only the URL does.

**Every dependency has a recorded PATH — in the link tables.** LabVIEW stores a
`PTH0` path record for EVERY file a VI references (it's what it uses to relink /
to prompt "find <this file>" when one is missing), spread across `_LIvi.bin`
(VI-level), `_LIbd.bin` (block diagram), `_LIfp.bin` (front panel). Each `PTH0`'s
components ARE the caller-relative path (leading empty pops a level, like
`ParsedDependencyRef.resolve_against`) and its LAST component is the file leaf
(`.vi`/`.lvclass`/`.ctl`/`.lvlib`). Parse ALL of them into a leaf→path index so
CLASSES and TYPEDEFS resolve by path too, not just SubVIs — then the whole
closure is path-driven and web==desktop (no name-search). Keep the index SEPARATE
from the dep set (only supply a path to an already-collected dep) — unioning
leaf-keyed refs as new deps makes a library-prefixed dep's bare leaf shadow it
into a duplicate node (broke class private-data field resolution). A nested
typedef used only inside another type (e.g. an enum inside a cluster) has NO
`PTH0` link and resolves via the type system inline — that's the one thing not
in the path index, and it needs no file staging.

**Follow the URL, never rglob.** Ancestor resolution must follow each class's
recorded URL and STOP at the bare name when the file isn't vendored — do NOT fall
back to a climb-and-`rglob` filesystem scan for a URL-declared parent. A class in
a throwaway/tmp tree with an unvendored parent will otherwise rglob a giant
unrelated root (it climbs 6 levels then `rglob`s) — a real hang seen in
`_build_ancestor_chain`. URL-following keeps class/parent resolution fully
path-driven and IDENTICAL on web and desktop (no file scans). Related:
[[feedback_path_is_the_node_key]], [[feedback_no_heuristics]].
