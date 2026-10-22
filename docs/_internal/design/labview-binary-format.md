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

## A Property/Invoke Node's own `<ddo>` child (task #51/#55) distinguishes IMPLICIT (bound to a control) from EXPLICIT (wired reference) — draws differently either way

A `propNode` OR `invokeNode` element can carry a DIRECT `<ddo uid="...">`
CHILD — a sibling of its own `<termList>`, never a part of it, never nested
under any part — naming the FRONT-PANEL control it is permanently bound to.
Verified on GTR's "Abort" boolean property node (`_BDHb.xml` uid 16295,
`<ddo uid="10449"/>`, which resolves in `_FPHb.xml` to `<ddo class="stdBool"
uid="10449">`, the "Abort" front-panel control) vs. "Set Front Panel Object
Control Value.vi"'s VI-reference property node (uid 1110), which has NEITHER
a `<ddo>` child NOR a `<label>` at all — and, for invoke nodes, on GTR's
"Test Hierarchy Tree" invoke node (`_BDHb.xml` uid 11387, `<ddo uid="12"/>`,
which resolves in `_FPHb.xml` to `<ddo class="treeControl" uid="12">`) vs.
the same VI's "FP.Open" VI-reference invoke node (uid 982), which has no
`<ddo>` child.

**This is the SOLE discriminator between LabVIEW's two forms of each node**
(never inferred from the label text or from wiring — those only corroborate):
- **IMPLICIT** (`<ddo>` present): created by dragging a control's icon onto
  the diagram, or Right-click control -> Create -> Property/Invoke Node.
  Draws with the BOUND CONTROL's own name as the header (from the node's own
  `<label>` textRec, e.g. "Abort"/"Test Hierarchy Tree" — populated by the
  SAME `extract_label` every node's display name already goes through)
  instead of the object CLASS, and a TYPE-COLOR BAR (the bound control's
  wire color, when its class is reconstructible — see below) under the
  header — no reference (in/out) terminals thread through, since there's
  nothing to wire.
- **EXPLICIT** (no `<ddo>`): identity comes from a wired reference; draws
  the object CLASS in the header (`⚙ <class>`) plus the reference/error
  terminals, unchanged from before this fix.

Independently corroborated by wiring on both verified property-node
instances: the implicit node's reference-IN terminal is UNWIRED (identity
needs no wire); the explicit node's is wired. The heap's own `<ddo>`
presence is used as the primary signal rather than wire state because it's
the direct causal fact (LabVIEW records WHICH control this node is bound
to), not a consequence that could theoretically be absent mid-edit on an
explicit node.

**Resolving the bound control's TYPE (for the color bar) stays entirely
inside `parser/`, never touches `render/`:** `parser.vi.
_resolve_bound_control_types` runs right after block-diagram node extraction
(which already has `fp_xml` in hand — see `extract_fp_terminals`'s
same-signature precedent), parses the FRONT-PANEL heap AT MOST ONCE per VI
(only when some property/invoke node actually has a `bound_control_uid`),
finds that uid's ddo, and reconstructs its `LVType` via the EXISTING
`fp_heap_type.reconstruct_control_lvtype` (the same function the pre-VCTP
fallback front-panel-terminal path already uses) — never a new heap reader.
`render/nodes.py`'s `_property_node_glyph`/`_invoke_node_glyph` then just
read the already-resolved `PrimitiveNode.bound_control_type` and call
`style.wire_style` on it, honoring `layout.py`'s "render never reads heap
XML itself" rule. `reconstruct_control_lvtype` only models ring/cluster/
array/scalar control classes — GTR's "Test Hierarchy Tree" is bound to a
`treeControl`, which it doesn't model, so that node's `bound_control_type`
stays `None` and it draws the target-name header with no color bar (never a
guessed color). See `parser.node_types.PropertyNode`/`InvokeNode`'s class
docstrings for the full field contract (`bound_control_uid`,
`bound_control_type`).

## An unwired Property/Invoke Node reference input uses the default object of the node's class

NI's public Property Node and Invoke Node pages state that when the node's class
is Application or VI the `reference` input need not be wired: the Application
default is the current application instance and the VI default is the VI
containing the node
(`https://docs-be.ni.com/api/bundle/labview-api-ref/page/functions/property-node.html`,
`.../functions/invoke-node.html`). The generated code assigns
`_lv.default_reference(<class>, owner=<VI>)` for such a node, so every unwired
node of one class in one VI shares one object. Other classes (the DAQmx System and
DAQmx Device nodes in `DCAF-DAQModule`'s `Config.vi`) occur unwired in saved VIs
but the public LabVIEW reference states no default for them; they are represented the same
way, named by their class. A WIRED reference with no value is a codegen error.

## `eventRegNode` (Register For Events, task #56) is a growable property-node-style node — its name and row count are BOTH heap-recorded, never hard-coded

`eventRegNode` was previously unhandled (`get_display_name` had no entry,
so it leaked the raw XML class as its label — a plain "eventRegNode" box).
Its heap shape is a direct structural twin of `propNode`/`invokeNode`:

- `<nodeName>` carries this node's OWN display name — verified `"Reg
  Events"` on ALL 23 real corpus instances across 21 files (GTR's "Main
  UI" uid 11756, a 5-row example in DCAF-DAQModule's "Register For
  Events.vi" uid 110, etc.) — no `"Unreg Events"`/other variant found. The
  fix reads this field directly (the SAME mechanism `PropertyNode`/
  `InvokeNode` already use for `object_name`), so an "Unregister For
  Events" node, if the heap ever names one differently, would render
  correctly with ZERO special-casing — never a hard-coded "Register For
  Events" guess.
- `<termList>` holds 4 fixed `hGrowCItem` terms (2 in/out PAIRS, split by
  TYPE, never position: the `Refnum` pair with `ref_type == "EventReg"` is
  the event-registration-refnum in/out; the `Cluster{status,code,source}`
  pair is the standard error in/out) plus N `eventRegItem` terms — ONE per
  registered event source, each a GROWABLE row's own INPUT terminal (the
  source refnum, e.g. `ref_type == "UserEvent"`, to register events on).
- `<dcoList>` lists the `eventRegItem` dco uids in heap/row order — the
  EXACT SAME convention `PropertyNode.dco_terminal_uids`/`InvokeNode.
  row_terminal_uids` already use (see `_dco_list_terminal_uids`), so the
  row COUNT is fully data-driven (GTR's instance: 1 row; DCAF's: 5).

Render draws a property-node-style box: header (gear + the heap-recorded
name) above one "event N" row per registered source (always a LEFT input
arrow — an event source is registered ON, never read back) with a "▼"
grow-handle on the LAST row (reference image #68). The registration-refnum
and error terminals thread the box edges at the header level, placed by the
scene from the node's real heap terminal geometry — not drawn by the glyph,
same as `PropertyNodeGlyph`'s reference/error terminals. See
`parser.node_types.EventRegNode`'s class docstring for the full field
contract (`object_name`, `event_row_terminal_uids`).

## Flat sequence frames execute ALL contained nodes — unwired nodes still run and block frame completion
<!-- was memory: feedback_sequence_frames -->

Flat sequence frames act like implicit subVIs. ALL nodes inside a frame execute (potentially in parallel), and the frame does not complete until every node finishes. The next frame cannot start until the previous frame ends.

**Why:** A Wait(500ms) primitive in the same frame as a Write doesn't need to be wired to the Write. Both run when the frame runs, and the frame takes at least 500ms. Ignoring unwired nodes in a frame loses timing, cleanup, and side effects.

**How to apply:** When generating code for a flat sequence frame, emit code for EVERY node in the frame, not just the ones in the wired dataflow chain. Unwired nodes (like Wait) must still appear in the generated Python for the frame.

## "Pre-LV9 (LV8.2) VIs have no VCTP; the diagram and front-panel type pool is the DTHP block, the connector-pane types are the CONP block (both decoded)"
<!-- was memory: project_pre_lv9_no_vctp_trec -->

**Pre-LV9 VIs (LabVIEW 8.2) have no `<VCTP>` block.** pylabview reads types only
from VCTP, so it resolves nothing for them. But the types ARE recoverable
clean-room. Three sources, by what they cover:

0. **DTHP block = the diagram + front-panel type pool (every `TypeID(N)` a
   terminal, constant or control names).** In an LV9+ VI, `DTHP` holds only a
   `TypeDescSlice` into VCTP. In an LV8.2 VI it IS the pool, laid out like VCTP;
   pylabview misreads it as the LV9 slice and leaves it raw, so the extractor
   writes `*_DTHP.bin` (an LV9+ VI parses to XML and has no sidecar). Layout:
   `u32 count`, `count` TDs (the TD framing of item 1 below), then
   `u16 ntop` + `ntop x u16` TD-index. **Heap `TypeID(N)` = flat TD
   `top[N-1]`** (`parser/heap_types.py`; TD decode shared with CONP in
   `parser/td_pool.py`). Wired into `parse_type_map_rich`, which every view reads,
   so the pool is single-sourced: VCTP first; no VCTP and a `DTHP` sidecar -> this.
   *Evidence:* (a) all 232 `DTHP.bin` sidecars in the corpus (62 distinct VIs --
   exactly the 62 corpus files with no `VCTP` ident, i.e. every LV8.2 VI) satisfy
   `2 + 2*ntop == bytes after the TD list` and every `top` entry `< count`;
   (b) apart from `TypeID(0)`, no diagram or front-panel `TypeID` exceeds `ntop`
   (e.g. `Error Cluster From Error Code`: ids up to 150, `ntop` = 152); (c) all 231
   front-panel terminals of the 62 VIs resolve through DTHP to a type equal to
   the one CONP -- an independent block -- gives (cluster field names, enum
   labels, typedef names, refnum classes included); (d) a by-name Unbundle's
   field wires carry the types of the fields its drawer indices select.
   `TypeID(0)` is "no type" (a local variable), as in LV9+. Measured on the 62
   VIs: untyped diagram terminals 3236 -> 232 of 3440 (the remainder are terminals
   the file itself gives no `typeDesc`, ~6.7% -- 62 LV9+ OpenG VIs measure 5.5%); arrays
   with no element type 16 -> 0.
   Extra TD facts the pool needed (verified on the 232 sidecars): an array body is
   `u16 ndims`, `ndims x u32` size, `u16` element ref, label (the CONP decoder had
   read the element at the wrong offset); an enum's label list is padded to an
   even offset before the terminal label; a typedef (`0xf1`) body is `u32 flag`,
   `u32 n` + `n` pstr qualified name (owning libraries then the `.ctl`), then the
   inner TD as a nested `[u16 len][flags][type][body]` running to the end of the
   body; string, path, picture and substring bodies start with a `u32` before the
   label; `0x53` (variant) is label-only; a Queue/Notifier/User Event/Data Value/
   FIFO/DataLog refnum body is `u16 kind`, `u16 n`, `n x u16` element TD ref.
   A pre-LV9 structure defines its DCOs on itself -- by-name Bundle/Unbundle
   drawers (with their field index `<i>`) in `dcoList`, the aggregate as `dcoAgg`,
   a case selector as `caseSelDCO`, tunnels in `tunnelList` -- and each border
   `<term>` holds a bare `<dco uid/>`; `_inline_dco_definitions` copies the
   definition into the term so every reader sees the LV9+ form. (Without it every
   drawer read as field 0 and every case selector as unwired: 42 `case ? :`
   headers in 27 of the 62 VIs, 0 after.)
   A node such as Delete From Array or Reshape Array does the same with named
   direct children (`delDCO`, `srcDCO`, `lenDCO`, `dcoAgg`, `dcoList`); the same
   copy gives its terminals their direction flags and types.
   `TM80` is NOT this pool: it is the data-space type map (same framing, other
   numbering), which only the case-selector tables read.
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
   pane (~37/2192 in JKI) is NOT in CONP -> its type comes from DTHP (item 0); the
   **FP-heap DDO reconstruction**
   (`parser/fp_heap_type.py`, commit 11caa50): structure + enum labels from the
   heap subtree (ring items in a `multiLabel <buf>`, cluster fields via
   `<ddoList>`), but no field NAMES (pre-LV9 label text is a dropped text-pool
   ref). Precedence: VCTP (LV9+) > DTHP pool (LV8.2 diagram and front panel); a connector-pane
   terminal's CONP type overrides its DTHP type (same structure, plus the terminal's
   name); FP-heap reconstruction only when neither yields a type.

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

## A global variable node is a `gRef` with a `connectorTM`; its Global VI is named by the `IUVI` link-table record

A **global variable** node and a **local variable** node are both `gRef` nodes. A
global variable's `gRef` carries a `<connectorTM>` TypeID that names a cluster
type: the cluster of the Global VI's controls, as the calling VI last saw them.
The node's `paramIdx` indexes that cluster's fields (and the Global VI's own
control order, `ddoList`/`dcoIndex`). A local variable's `gRef` has TypeID 0 in
`<connectorTM>` (no connector type), so TypeID(0) means local.

The node does not name the Global VI itself. The calling VI's block-diagram link
table (`LIbd`) does: one `IUVI` record per referenced VI, carrying the VI's
qualified name and path and a list of the heap uid of EVERY node that uses it,
subVI calls and global variable nodes alike. Look the node's uid up in those
lists to find its Global VI.

Evidence: across the corpus's 159 global variable nodes (the `gRef` nodes with a
`connectorTM`), 159 have their uid in some `IUVI` node-uid list and resolve to a
qualified name; none is left unresolved. `tests/test_global_variable.py::
test_every_global_variable_node_in_the_corpus_resolves_its_global_vi` asserts it.
The record layout is in the `parser/link_table.py` module docstring.

## A Shared Variable node's library is named by the `HpVL` link-table record, found by the node's uid

A **Shared Variable** node (`class="sharedVariable"`) names its variable in its
`<name4>` (hex text) and nothing else: its label text is a project path
(`.\SV Sample.lvlib\Value`), not an identity. The variable's **library** is in
the calling VI's block-diagram link table (`LIbd`), in an `HpVL` record that
lists the heap uid of EVERY Shared Variable node using that variable (one record
per variable). pylabview has no decoder for it (`LinkObj b'HpVL' parsing not
implemented`, which also makes it drop the whole `LIbd` section into the raw
`_LIbd.bin` sidecar), so the sidecar is always where it is read. The VI-level
table (`LIvi`) holds a sibling `VIVl` record with the same name and path but no
node uids; it is not needed and not read.

The record's head is the one an `IUVI` record starts with (qualified name as
parts -- the library's name parts then the variable's name -- an empty path, link
flag, type id, link reference); then the item type (`NI_Variable_Variable`), the
target (`My Computer`), the variable's persistent ID (a braced GUID, the same one
the `.lvproj` records as `varPersistentID:{...}`), the node uid list, a second GUID
(role not established), two u16 values (role not established), then one of two
layouts. **No byte selects the layout**: the decoder reads the single-process
layout first and keeps it only if its type string reads `Global`; otherwise it
reads the network-published layout and keeps it only if its type string reads
`Network`.

- single process: a u8 (seen as `01` in the committed sample and `00` in a
  LabVIEW 2025 project, so it is not a layout flag), a u32 (seen as 1), then the
  type string `Global` (LabVIEW's recorded name for a single-process variable);
- network-published: the **network path** as a u32-length string
  (`\\.\SV Sample\Value`; `\\.\` is the local machine), four u32 (seen as
  `ffffffff 1 2 1`, roles not established), then the type string `Network`.

The two u16 values are not a fixed marker: in a LabVIEW 2025 project the first
differs from record to record within a VI and the second is the same for every
record of that VI (seen as 2, 3, 5 and 6); both committed samples read `2, 3`.
Their meaning, and the meaning of the u8 and the u32, are not established; the
link keeps them as read. A record that fits neither layout is a
`SharedVariableRecordFailure` (offset, reason, the node uids if the failure came
after the uid list) instead of an exception, so the VI still loads and the
affected nodes say their library link is unreadable. Exact layout:
`parser/link_table.py` module docstring.

Evidence: `tests/corpus/issues/149/SV Sample.vi` (LabVIEW 25.0, reporter
metman-oss): one `HpVL` record, library `SV Sample.lvlib`, variable `Value`, type
`Global`, uids 1033, 1112, 977 -- the three `sharedVariable` nodes of the VI.
Terminal `terminal_ID` 3 exists only on the node set to show the timestamp, as an
output of the timestamp type. The persistent ID equals the project's
`varPersistentID`. `tests/corpus/issues/149/network/SV Sample.vi` is the same project with `Value`
switched to Network-Published (no buffering, no binding): its `HpVL` record has
the same head, uids 1112, 977, 1033, the network path `\\.\SV Sample\Value`,
and type `Network`. The reporter also saw an `HpVr` record (a three-part name:
`My Computer`, `SV Sample.lvlib`, `Value`; type `Network`; a network path with the
machine's address, `\\<host>\SV Sample\Value`) for nodes placed after the variable
was already network-published. It is decoded by the same code as `HpVL`; the one
difference is that its first name part is the record's own `target` field, which is
dropped before the library and variable are split (a first part that is not the
target is a `SharedVariableRecordFailure`). **This layout comes from the reporter's
description plus the structure shared with `HpVL`; it has not been checked against a
real `HpVr` file yet.** The link keeps which ident it came from
(`SharedVariableLink.record`); nothing user-facing shows it.

## A .NET assembly is named by a `DNVA` (`LIvi`) and a `DNDA` (`LIbd`) link-table record; each terminal of a .NET node records its .NET type in a `dnetItemInfo` (issue #161)

A VI that calls a .NET assembly records the assembly in both link tables, like
the project library of a Shared Variable: a `DNVA` record in the VI-level table
(`LIvi`) and a `DNDA` record in the block-diagram table (`LIbd`) that also lists
the heap uid of every node using it. pylabview defines both layouts
(`LinkObjVIToAssembly` and `LinkObjHeapToAssembly` in `LVlinkinfo.py`); lvkit reads
the main XML when pylabview decoded the table and the `_LIvi.bin` / `_LIbd.bin`
sidecar otherwise.

The record's qualified name has FOUR parts, not a path: the assembly's name,
version, culture and public key token (`MccDaq`, `3.0.0.0`, `neutral`,
`a37eefcf5c6ca10a`). The path holds only the name -- there is no file, the
assembly comes from the GAC. A `DNVA` record is the basic link head (qualified
name, path, flag) and a second path; a `DNDA` record is the head an `IUVI`
record starts with (adds the type id and link reference), the typed-link flags
(>= 12.0.0.3), the node uids and a second path. The second path is not read.
Exact layout: `parser/link_table.py` module docstring. A record without four name
parts, or that runs past the table, is a `DotNetAssemblyRecordFailure`; the VI
still loads and describe and the JSON say the link is unreadable.

**Evidence and its limits.** Taken from a reported sample, a LabVIEW VI calling
`MccDaq` 3.0.0.0 (`DNDA ... 06 "MccDaq" 07 "3.0.0.0" 07 "neutral" 10
"a37eefcf5c6ca10a" PTH0 ... 06 "MccDaq"`), and pylabview's layouts. The sample's
bytes were reported, not shared: no VI with a `DNDA`/`DNVA` record is in the
corpus, so the decoder is tested on records built from that layout, and the
bytes the report elides (alignment, the counts and lengths) are the ones the
layout implies.

Each terminal of a .NET node is an `invokeItem` DCO whose `privDataList` holds a
`dnetItemInfo` element. Its `DotNetItemType` field is the terminal's .NET type as
an assembly-qualified name, hex-encoded, ending in a NUL byte:
`MccDaq.DaqDeviceDescriptor[], MccDaq, Version=3.0.0.0, Culture=neutral,
PublicKeyToken=a37eefcf5c6ca10a` (the return value of `GetDaqDeviceInventory`,
an array), `MccDaq.DaqDeviceInterface, ...` (a parameter the VI types as I32: a
.NET enum) and `MccDaq.ErrorInfo, ...` (the return value of `IgnoreInstaCal`).
The VI's own type for the terminal stays the generic DotNet refnum (or I32).
Member and parameter names are not stored in the VI. The three types come from the
same report; the element's exact heap shape (whether the `dnetItemInfo` is a tag or
a `class`, and where the DCO is defined) is read both ways in `parser/vi.py`
`_dotnet_item_type`.

## "Compound Arithmetic (cpdArith) operation is in objFlags bits 16-18, NOT dcoFiller — corpus-verified decode that fixed"
<!-- was memory: reference_cpdarith_objflags -->

The LabVIEW **Compound Arithmetic operation** lives in the cpdArith node's
`objFlags`, bits **16-18** = `(objFlags >> 16) & 0x7`. Enum (matches LV's mode
order): **0=add, 1=multiply, 2=and, 3=or, 4=xor**. Bit 19 is a separate flag,
usually but not always set: across 923 cpdArith nodes in 671 cached sample VIs
the values are 0xA0000 (603), 0xB0000 (252), 0x80000 (59), 0x30000 (4),
0xC0000 (3), 0xB1000 (2).

**A missing `objFlags` means 0 = Add with no other flag set.** The heap stores
only non-zero `objFlags`: no cached sample `_BDHb.xml` holds a literal
`<objFlags>0</objFlags>`, across many thousands of elements. Treating "absent"
as "unknown mode" was #116.

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

### Case selector values in the heap (LabVIEW 2010+)

Verified against the stored label of the displayed frame (`selString/textRec/text`
of the `select`/`caseStruct` element, frame `dIdx`; LabVIEW omits `dIdx` when it
is 0) over a fixed corpus list (`tests/test_case_frame_labels_corpus.py`):

- `SelectRangeArray32` entries are `start`/`end` plus `startRangeType`/
  `endRangeType` (each omitted when 0). **0** = a lone value; **1** = a literal
  end of a range (`0..2` is `(0, 2, 1, 1)`); **3** = an OPEN side, whose number is
  filler (the selector type's limit: `0..` is `(0, 2147483647, 1, 3)`, `..-1` is
  `(-2147483648, -1, 3, 1)`, I16 `126..` is `(126, 32767, 1, 3)`). Both sides open
  is the whole domain: the error cluster's second frame, stored as `Error ..`
  (`Error ` plus the range text) with `SelectDefaultCase` FF, so it is not a
  default frame; one open side whose filler equals the bounded end is its "No
  Error" point.
- A string frame lists `SelectStringArray` indices (a frame can match several);
  the empty string is an element without text.
- Enum selectors are named `UnitUInt8/16/32` by pylabview; the type's kind is
  `ENUM`.
- A heap case's `SelectDefaultCase` is authoritative for its default frame and
  its `dIdx` (absent = 0) for the displayed frame. A dataspace table paired to it
  by order may belong to another case, so tables are applied only to cases
  whose values are not in the heap.
- A pre-2010 case has no `SelectRangeArray32`; its `dIdx` is a global diagram
  ordinal (e.g. 36, 38, 52 on 2-frame cases), not a frame index.
- Stored labels quote enum item names and strings (`"Up", Default`), pad with a
  space, and write a control character as a backslash code.

## Boolean and two-frame error case structures have NO default frame; only an error case with error-code frames has one (`Default`)

- **Boolean:** NI: "You cannot specify a default subdiagram if you wire a Boolean control to the case selector" (labview help, *specifying-a-default-subdiagram-for-case-structures*). Frames are `True`/`False`, never a Default marker.
- **Error, two frames (`No Error` / `Error`):** fully described (status Boolean), so no default. Never infer one (no "first frame without a value" fallback, no frame-0 rule).
- **Error with error-code frames (LV 2019+):** NI: "If you add more error cases without customizing the selector label of the Error case, the Error case changes to Default". The file stores it: `Enumerate Compute Nodes.vi` (measurement-plugin-labview) case uid 496 has `SelectDefaultCase 01`, no range on frame 1, saved label ` Default `. Label that frame `Default`; a frame storing status 1 reads `Error`, never `Error, Default`.
- **The No Error frame can also list codes** (`No Error, Error 18`, NI help *handling-errors-using-case-structures*).
- Rule of thumb: mark a Boolean/error frame default ONLY when the VI stores it.

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

## A decoration's positive ``ImageResID`` names a DSIM (embedded-picture) resource section; the top-level `<stem>.xml`'s `<PICC>`/`<DSIM>` blocks map Section Index -> file (issue #82)

A block-diagram decoration (`class="cosm"`, and `class="attachment"` — see
below) carries an `<image><ImageResID>N</ImageResID></image>`. The
Decorations-palette shapes (Flat Frame, Thin/Thick Line, Thin/Thick Line with
Arrow) always use NEGATIVE ids (`-35`, `-233`, `-502`, ...). A **positive** id
is a different thing entirely: a resource **Section Index** into the VI's own
extracted top-level `<stem>.xml` (the sibling of `<stem>_BDHb.xml`), which
records index -> file for two resource kinds:

```xml
<PICC><Section Index="2" File="..._PICC2.bin"/><Section Index="3" File="..._PICC3.bin"/></PICC>
<DSIM><Section Index="4" File="..._DSIM.bin"/></DSIM>
```

- **DSIM** ("Dynamic Size Image") sections hold an embedded PICTURE the
  developer pasted onto the diagram (LabVIEW's Picture decoration / a picture
  control's default, on the BD it's the "cosm" catch-all). The section's raw
  bytes are a small binary preamble (size/scaling fields LabVIEW itself needs)
  followed by a standard PNG. The PNG payload is found by its own signature
  (`\x89PNG\r\n\x1a\n`) and ends at the first `IEND` chunk's CRC (+8 bytes past
  the `IEND` tag) — verified on issue #82's repro: PNG at DSIM offset 46,
  1402x1122 RGB, carving reproduces the embedded picture byte-for-byte.
- **PICC** ("Generic block") sections hold per-instance geometry a decoration's
  `<ImageInternalsResID>` names — see the next entry for the byte layout.

The drawn footprint is the decoration's own on-diagram `<bounds>`, NOT the
PNG's intrinsic size (issue #82's repro: a 1402x1122 PNG scaled into an
803x643 box) — LabVIEW always stretches a pasted picture to whatever box the
developer sized it to. Implementation: `parser/image_resources.py`
(`resource_sections`/`resources_for_heap`, `carve_png`), threaded into
`parser/layout.py`'s `_LayoutBuilder` as an optional `resources` map (default
`None` -> no picture/PICC resolution, today's behavior for every caller that
doesn't pass one) and carried on `Layout.images: dict[uid, bytes]`; rendered by
`render/glyphs/decorations/picture.py`'s `PictureGlyph` (a plain `<image>`,
smooth-scaled — NOT the `.lv-raster`/`pixelated` treatment connector-pane icon
pixel art gets, since this is arbitrary photographic artwork).

## PICC section byte layout: a 4-byte header then BE-int16 `(y, x)` point pairs — absolute LabVIEW pixels, same frame as the owning element's `<bounds>` (issue #82)

A PICC section (see above) named by a decoration's `<ImageInternalsResID>`
decodes as: a 4-byte header (purpose not yet decoded — every sample seen so
far is a distinct 4-byte value, `00 02 01 02` / `00 92 09 02`, so it likely
carries a style/type discriminator, not a length) followed by
`(len(data) - 4) / 4` points, each **two big-endian signed int16 in `(y, x)`
order**. Verified byte-for-byte on issue #82's repro:

- `PICC3 = 00 02 01 02 | 02 3d 06 78 | 03 1c 04 ce` decodes to `(y=573,
  x=1656)` then `(y=796, x=1230)` — i.e. `(x, y)` points `(1656, 573)` and
  `(1230, 796)`, the real endpoints of a Thick-Line-With-Arrow (`cosm`
  `ImageResID -502`) whose own `<bounds>` is `(572, 1230, 797, 1657)`
  (top/left/bottom/right) — the TOP-RIGHT/BOTTOM-LEFT diagonal, not the
  main (top-left/bottom-right) diagonal `line_endpoints()`'s bounds-only
  guess draws.
- `PICC2 = 00 92 09 02 | 01 01 01 7c | 02 5c 02 63` decodes to `(y=257,
  x=380)` then `(y=604, x=611)` — the endpoints of a `class="attachment"`
  label leader, running from the label's own corner to the target picture's
  top edge. Note these points do **not** lie on the attachment element's own
  (small, unrelated) `<bounds>` — an attachment's `<bounds>` is a hit-test
  rect, not the leader's drawn extent; the real line is wherever the PICC
  points say, absolute.

These are ABSOLUTE LabVIEW-pixel coordinates in the SAME frame as the owning
element's own `<bounds>` (`_rect()`'s `(top, left, bottom, right)` ->
`(x1, y1, x2, y2)`) — decode with the identical walk offset (`ox, oy`) the
element's bounds gets in `_LayoutBuilder._visit`, never the element's own
`(ax1, ay1)` (which already has the offset baked in). Implementation:
`parser/image_resources.decode_picc_points`.

**Head/tail convention (inferred from the two samples above, not from the
4-byte header):** in BOTH decoded pairs, the FIRST point sits at the
originating label's edge and the SECOND at the target's edge, and in both
cases LabVIEW draws the arrowhead at the target end (confirmed against the
issue #82 reference image) — so `points[0]` is the tail, `points[-1]` is the
head. This is a positional-order fact read off real data on two independent
decorations, not a "closest to X" geometric heuristic; if a future sample
contradicts it, the 4-byte header is the next place to look for an explicit
head/tail flag.

## `class="attachment"` is a distinct heap element from `class="cosm"` — a label-to-object leader/pushpin, only drawable when it carries an `ImageInternalsResID` (issue #82)

Besides `class="cosm"` (Decorations-palette shapes + embedded pictures, see
above), a diagram's `zPlaneList` can hold `class="attachment"` elements — the
leader LabVIEW draws from a free label to the object it's attached to (the
"pushpin"). Two different roles show up under the same class, distinguished by
which optional children are present:

- **A drawable leader**: carries `<ImageInternalsResID>` (a PICC section, see
  above) and an `<attachedObject uid="..."/>` naming the target node. Decode
  its PICC exactly like a `cosm` arrow's; draw a leader line/arrowhead at the
  decoded absolute endpoints. Issue #82's repro has one (uid 947, `ImageResID
  -770`, internals PICC2, target uid 842 — the embedded picture).
- **A bare marker**: NO `<ImageInternalsResID>` and no `<attachedObject>` — a
  tiny (12x12px in the repro) placeholder with `ImageResID -771`. It has no
  decodable line geometry and (checked against the issue #82 reference image)
  draws NO visible mark at all in the static diagram — it's LabVIEW's
  invisible drag-handle for the label's attachment behavior, not something
  with its own paint. Render as **nothing** (never a `FallbackGlyph` dashed
  box — a box there is pure noise no reference image ever shows).

This is a SEPARATE mechanism from a free label's own `<attachment uid="X"/>`
CHILD element (`ParsedFreeLabel.attach_uid`, `parser/nodes/free_label.py`) —
that's the label pointing AT one of these `class="attachment"` elements by
uid; the `class="attachment"` element itself is the thing this section
describes, with its own bounds/image/internals in `zPlaneList`.

## A cluster constant's `paneHierarchy`/`zPlaneList` carries every field's REAL geometry, in the front-panel typedef-editor's OWN (unrelated-scale) coordinate space — recovered by extent-normalizing, not by the pane's own `<origin>` (issue #45 reopened)

A block-diagram cluster constant's `dco class="bDConstDCO"` → `ddo
class="stdClust"` carries its own `<bounds>` (the real drawn box) and a
`<paneHierarchy class="pane">` child. That pane's OWN `<partsList>` (5-6
entries) is chrome only — a "Pane" caption + scrollbar-corner `cosm` parts —
**not** the fields. The fields live in the pane's `<zPlaneList>`, one
`SL__arrayElement` per field, `class` naming the field's control kind
(`stdString` / `stdRefNum` / `stdBool` / `stdNum` / `stdPath` / `absTime` /
`indArr` / `stdClust` for a NESTED cluster field / …). Each field element has
the exact same shape as any top-level constant DDO: a `<bounds>` (the field's
full box, caption included) and a `<partsList>` whose non-`label` parts union
to the VALUE box (`_const_value_box`) and whose first `class="label"` part is
the name caption (`_const_label_box`) — hidden when its `<objFlags>` bit
`0x8` is set, exactly like `_LayoutBuilder._record_label_hidden`. Verified on
Graphical Test Runner "Main UI" ddo uid 13666 ("SMUI Template App Data", 23
fields): only 6 of 23 fields have a visible caption; the other 17 have their
caption hidden and draw value-only.

**The coordinate trap:** a field's own `<bounds>` are numbered in the
typedef front-panel EDITOR's coordinate space (e.g. `(2000, 1000, 2020,
1040)`, hundreds to thousands) — a completely different scale from the
pane's own `<bounds>` (tens, e.g. `(3, 3, 121, 1028)` relative to the ddo's
own origin) or its `<origin>` (a scroll-position field, `(334, -317)` on the
verified example) — `<origin>` is NOT a translation offset into the field
coordinate space and using it directly misplaces every field. The two
spaces reconcile only through NORMALIZATION: the verified example's 23
fields' COLLECTIVE extent (min/max over every field's value-box AND
label-box rect) is **exactly** `118 × 1025` px — identical, to the pixel, to
the pane's own real inner content area (`<bounds>` inset from the ddo's own
origin: `121-3=118`, `1028-3=1025`). I.e. a cluster constant never scrolls;
every field is shown, and the extent-to-pane-area ratio is the correct
mapping scale (1.0 in every verified case — pure translation; implemented as
`min(inner_w/extent_l, inner_h/extent_t)` so a future mismatched extent falls
back to a uniform, non-distorting scale rather than a per-axis stretch).

**Extracted geometry is relative, not absolute.** `_cluster_field_geoms`
returns a `ClusterGeom` (`width`, `height` = the cluster's own real
`_const_value_box` size; `fields` = each `ClusterFieldGeom`'s `value_rect`/
`label_rect` relative to that box's OWN `(0, 0)` origin) — never pre-baked
against a specific drawn position. A caller (the glyph, at draw time) fits
this into whatever real box it's ACTUALLY drawing at via ONE uniform scale,
`s = min(box_w / width, box_h / height)`, then translates: a field's
on-screen rect is `(box_x1 + s*x1, box_y1 + s*y1, ...)`. At the top level
(a cluster constant drawn at its own real heap box) `s` is always `1.0` —
pure translation. This composes correctly across recursion levels without
the extractor ever needing to know its caller's target box size.

**Two heap shapes carry a nested cluster that the RENDERER actually
recurses into — `_cluster_shape` finds both, generic over the field's own
class:**

1. **A field directly `class="stdClust"`** — recurse using the field
   itself, into its own `<paneHierarchy>`/`<zPlaneList>` exactly like a
   top-level ddo. Verified two levels deep in one VI (heap-uid chain
   752→769→903: a `typeDef`-wrapped constant's inner `stdClust` PART 769 has
   a field "test error", uid 903, itself `class="stdClust"`, with its own 3
   sub-fields status/code/source).
2. **A cluster used as a named `.ctl` typedef control** — the ddo's own
   class is `"typeDef"`, and the real `stdClust` shape is embedded as ONE
   `partsList` PART (identified by carrying its own `paneHierarchy`, never
   by name/position) — `_cluster_shape` unwraps this transparently, so a
   typedef-wrapped cluster constant gets full real geometry the same as a
   bare one (no special-cased fallback).

See `lvkit.parser.layout._cluster_shape` / `_cluster_field_geoms` for the
implementation and
`tests/test_parser.py::test_cluster_field_geoms_maps_real_field_geometry`
and `test_array_element_cluster_geometry_typedef_wrapped` for fixtures
covering both triggers plus the coordinate mismatch.

**A THIRD heap shape exists: a data-typed refnum's own registered payload.**
A control whose own class is something else entirely (verified:
`class="stdRefNum"`) can carry a nested cluster (or scalar) as its own
DIRECT `<ddo>` CHILD — not inside its `partsList`, a sibling of it — the
SAME convention an array ddo's element control uses (see below). Verified
on GTR's "SMUI Template App Data" cluster: a User Event refnum field
(`ResultChangedRef`, real heap box height 206px) has a nested ddo (uid
14006, `<bounds>(5, 31, 201, 106)` relative to the field's own origin —
bounds `75×196`) that decodes to the SAME cluster type as the field's
graph-level `LVType.element_type` (`kind=CLUSTER`, matching field names).

**The refnum's real per-field heap `<bounds>` is genuine, per-field, and
NEVER a size to compact or re-flow** — cross-checked against the field's
`.ctl` typedef: some User Event refnums are truly EXPANDED in this VI
(`ResultChangedRef` h=206, `SuiteChangedRef`/`TestStartedEventRef` h=83),
others genuinely COMPACT (`AbortEventRef`/`ExitEventReference`/`TextStream`,
h=48 each). The field's box size and position come straight from these
real bounds, same as any other cluster field — `_cluster_field_geoms`,
`ClusterFieldGeom.value_rect`/`label_rect`, and the cluster's
extent-normalize scale are UNCHANGED for a refnum field.

**What DOES vary, and is the actual heap-recorded signal, is whether the
box's CONTENT draws "compact" (a small type-mnemonic badge) or "expanded"
(the registered payload's own REAL per-field elements, dimmed, filling the
box).** This is `_refnum_type_display_expanded` in `parser/layout.py`: the
refnum's own `partsList` carries a `multiCosm` part (drawing the
type-display's background) whose `<index>` is the discriminator — verified
on all 5 `stdRefNum` instances in this VI: every EXPANDED one
(`ResultChangedRef`, `SuiteChangedRef`, `TestStartedEventRef`) has
`<index>1</index>` on that `multiCosm`; every COMPACT one (`AbortEventRef`,
`ExitEventReference`, `TextStream`) omits `<index>` entirely (LabVIEW's own
default, 0). The field's real `<bounds>` height independently agrees with
this bit on every verified instance (expanded ones are taller), but the
renderer reads the `<index>` directly rather than inferring expand/compact
from size. Either state renders the payload as a TYPE, never as editable
VALUE glyphs (F/0/testPass) — a refnum's payload data only ever appears
elsewhere (e.g. an Event Structure's own data node).

**LabVIEW is a visual language: every refnum draws as a clean-room dog-ear
frame (a folded-corner box) + a kind symbol (from `LVType.ref_type`) + a
TERMINAL showing the payload's TYPE — `render.glyphs.nodes.refnum_glyph.
RefnumGlyph`** — never bare wrapped text ("UserEvent Refnum"). For the
COMPACT case the terminal is a small `TypeTerminalGlyph` badge (the
payload's `style.type_repr` mnemonic). For the EXPANDED case the terminal is
the payload's own REAL per-field elements — composed by the SAME recursive
`render.nodes._cluster_value_glyph` a genuine nested cluster field uses,
wrapped `DimmedGlyph` (the established `lv-disabled-mask` wash) — positioned
at the heap's OWN recorded placement within the refnum's box, never
centered or invented: `parser.layout._refnum_payload_layout` reads the
payload `<ddo>`'s own `<bounds>` (verified relative to the REFNUM's raw-
bounds origin, the same "relative to the owning ddo's top-left" convention
every `partsList` part uses) and expresses it as 0..1 FRACTIONS of the
refnum's own native box (`RefnumPayload.offset`) — fractions survive ANY
later uniform rescale of the refnum's drawn box exactly, since every scale
this renderer ever applies is uniform (never per-axis). `RefnumPayload.geom`
is the payload's own self-contained `ClusterGeom` (`_cluster_field_geoms`
applied to the payload `<ddo>`, recursing exactly like a genuine nested
cluster field's `ClusterFieldGeom.nested` does — including a further-nested
GENUINE value cluster inside the payload, e.g. `ResultChangedRef`'s "test
error" field, which still draws real box-in-box VALUE glyphs per rule 6:
only a field whose OWN kind is CLUSTER ever draws editable-looking value
content, dimmed here only because its PARENT terminal is).

A class-typed field (`LVType.classname` set — verified heap ddo class
`udClassDDO`) is NOT a `RefnumGlyph` at all: LabVIEW draws a class as a
CUBE, not a reference frame, so it gets its own `render.glyphs.nodes.
class_glyph.ClassGlyph` (a clean-room isometric-cube outline + the class's
short name) — replacing the earlier bare "LabVIEW Object" text box.

An EARLIER revision of this renderer recursed into the nested `<ddo>` and
composed the field as a fully-expanded nested `ClusterConstantGlyph`
showing VALUE glyphs — wrong regardless of expanded/compact state, since a
refnum's payload is a type, not data. A maintainer review against
reference LabVIEW renders (a compact User Event control and a compact
Queue control) first corrected this toward "always compact, flattened
'name: type' text rows for the expanded case" — also wrong on two counts:
it doesn't match this VI's own heap (which genuinely records some refnums
expanded, confirmed against the `.ctl` typedef), and LabVIEW never draws a
control as spaced text — every element is a real positioned glyph. The
current implementation reads the heap's own recorded bit (`<index>`) AND
the payload's own real per-field geometry, and composes real recursive
element glyphs at every level instead of assuming a fixed answer or
flattening to text.

**Per-kind refnum symbol + terminal chrome are verified against the
maintainer's reference images (57/59 User Event, 58 Queue) AND, for kinds no
reference image covers, against NI's own public docs.** The User Event kind
symbol is a FILLED CIRCLE (`refnum_glyph._kind_user_event`) — an earlier
arc/radar mark was wrong; 57 and 59 both clearly show a solid circle, and
NI's own "Generate User Event" function icon (fetched from
`unofficial-lvdocs.github.io/glang/GenEvent.gif` — a public mirror of the
same NI-published `docs-be.ni.com` content) independently shows a circle
with a small interior mark, confirming the outer shape. The compact
`TypeTerminalGlyph` badge draws a DASHED border in the FIXED
`Theme.refnum_terminal_border` pink, NOT the payload's own wire color — 57
(string payload, "abc") and 59 (class payload, "OBJ") both draw the SAME
dashed pink box, so it's fixed terminal chrome; the payload wire color tints
only the mnemonic TEXT.

For kinds with NO maintainer reference image, the actual LabVIEW pictograph
was fetched from NI's public docs (via the `unofficial-lvdocs.github.io`
mirror of `docs-be.ni.com` content — same clean-room source class the
codebase already cites elsewhere, e.g. `scripts/build_ni_function_catalog.
py`) and re-drawn as a simplified clean-room motif, never traced pixel-for-
pixel:
- **Queue** (`_kind_queue`): a 3-compartment comb with a line entering left
  and an arrow exiting right — from the "Obtain Queue" function icon
  (`glang/creatque.gif`), whose own pictograph is exactly this comb+arrow
  shape (a stack of separate bars, the earlier guess, was wrong).
- **Notifier** (`_kind_notifier`): an exclamation mark in a ring — from the
  "Obtain Notifier" function icon (`glang/creatnot.gif`), a circled "!" (a
  pennant-on-a-pole, the earlier guess, was wrong).
- **Menu** (`_kind_menu`): 3 stacked horizontal bars of decreasing width, no
  enclosing box — from the "Types of Refnum Controls" page's Menu Refnum
  icon (`lvhowto/noloc_env_menuref.gif`), a menu-item list with no border.
- **Generic fallback** (`_kind_generic`, covers `LVObjCtl`/`EventReg`/
  `DataLog`/anything unmapped): a 2x2 grid with one cell filled — from the
  same page's VI Refnum icon (`lvhowto/noloc_env_viref.gif`), a 2x2 grid
  with one cell highlighted (the earlier ring+dot guess is replaced).

Every `noloc_env_*ref.gif` icon on that NI docs page independently confirms
the DOG-EAR (folded top-right corner) shape this renderer already uses for
every refnum, at every kind — cross-validating that choice too.

**No value glyph is ever a blank/typeless rectangle** (a blank box was the
issue-#45 regression). Every leaf field identifies its type even when
empty/unset (`render.nodes._leaf_const_glyph`): an empty STRING draws a
DIMMED "abc" type-placeholder (`ConstantGlyph.dim` → `Theme.disabled_mask`,
never mistaken for real data); a PATH draws a clean-room folder mark
(`render.glyphs.nodes.path_glyph.PathGlyph`) with or without path text; an
unset TIMESTAMP (heap ddo class `absTime` → graph `underlying_type=
"MeasureData"`, `measure_flavor="TimeStamp"`) draws `0.0` (LabVIEW's own
epoch default, matching codegen's) rather than falling through to a blank
box; an ARRAY-typed cluster FIELD composes a real `ArrayConstantGlyph` (index
control + a real default element) instead of the generic leaf's empty box.

**Small refnum/class boxes use a DISTINCT MINI form, not the full glyph
shrunk** — LabVIEW's own small-control behavior. Below
`refnum_glyph._MINI_MAX_W/_H` (`class_glyph._MINI_MAX`) a refnum drops the
dog-ear + terminal and draws just a bordered box holding the kind symbol
scaled up; a class drops the border + name and draws just the cube. Verified
against GTR's real `menubar` field (21×27), too small for the full form.

**An array ddo's ELEMENT control is its own direct `<ddo>` child, at the
array's OWN real coordinate scale — not the field-extraction typedef-canvas
trap.** A block-diagram array CONSTANT's ddo is verified `class="indArr"`
on the corpus (the same class an array-typed field/indicator uses;
`stdArray` is `parser.fp_heap_type`'s established name for the same control
shape, kept as a second recognized class though unverified in this corpus).
Its single visible element control is a DIRECT `<ddo>` child (a sibling of
`indArr`'s own `<partsList>` chrome, never a part of it) — verified on
`TestResult_Init.vi`'s array-of-clusters constant: `indArr` ddo 502
(real box `92×116`) has a direct `<ddo class="typeDef" uid="569">` child
(bounds `(3, 35, 113, 89)` relative to 502's own origin — width/height
`54×110`, at the array's OWN scale) wrapping a `stdClust` shape (uid 573,
own `<bounds>` also `110×54`, i.e. THE SAME size — a plain offset
relationship, confirming this is NOT the typedef-canvas trap). Since an
array is homogeneous (one element type for every value), the array glyph
draws every visible row at this ONE real size (`ArrayConstantGlyph.cell_w`/
`cell_h`) instead of stretching to a synthetic fixed row height — never
scaled, since the cell IS the element's own real size.

## A by-name drawer's field index counts every sub-cluster as its own slot (issue #134)

Bundle/Unbundle By Name (`nMux`, `<i>`), the In Place Element cluster node
(`decomposeClusterNode`, `<index>`) and Event Data/Filter Nodes
(`eventDataNode`, `<i>`) all select a field by an index into the aggregate's
DEPTH-FIRST flatten in which a nested sub-cluster is a slot of its own,
followed by its fields — for class private data, typedefs and event data
alike. An absent index element means 0.

Ground truth needs no field list: each drawer's `typeDesc TypeID(N)` is a
heap TypeID → consolidated TypeID (the XML comments) → VCTP
`TopLevel/TypeDesc[@Index]` FlatTypeID → that flat TypeDesc's `Label` (or its
nested typedef type's `Label`), which is the selected field's name. Across 75
corpus VIs, every drawer whose name differs between a leaf-only and a full
flatten matched the full flatten: nMux 349/349, event data 51/51, decompose
24/24. Example: `MeasurementContext` private data
`[pin map context {pin_map_id, sites}, gRPC server call]` — a drawer with
`<i>`=0 is typed as the whole `pin map context` cluster, `<i>`=3 is `gRPC
server call`.

## A Property Node row is ONE `propItem`; its `propList` holds one `propItemInfo` per path segment, so a nested property (`Label.Text`) is one row, not two

A `class="propNode"`'s rows are its `class="propItem"` elements, and their
document order (`elem.iter()`) is the order of the node's `<dcoList>` — the
i-th row owns the i-th `dcoList` terminal. Across 2,030 Property Nodes in 529
cached corpus VIs (both layouts below) the two orders agree in 2,030/2,030.

A row's property PATH is its `<propList>`: one `<SL__arrayElement
class="propItemInfo">` per segment, each with its own `<PropItemName>` and
`<PropItemCode>`. Of the 2,470 common-layout rows in that survey, 95 have two
segments and none more. Example:
JKI `GetControlRef.vi`, node uid 156 — the row with dco 502 is `Label` (code
104013829) then `Text` (code 103995392), i.e. `Label.Text`; the row with dco
281 is `Indicator`; `<dcoList>` = [502, 281]. Counting every `propItemInfo`
as a row shifts each later row's name by one.

Two layouts carry the same `propItem > propList > propItemInfo` shape. Common:
the `propItem` is the `<dco class="propItem">` inside a `termList` term.
Older: the `propItem` is its own `<SL__arrayElement class="propItem">` inside
the node, the term's `<dco uid=...>` only references it, and its
`propItemInfo` carries no `PropItemName` (the name is empty).

## A reentrant VI is recorded with EITHER the `IsReentrant` OR the `SaveParallel` Execution flag; `PooledReentrancy` then picks shared vs preallocated clones (issue #132)

In the main XML's `<LVSR><Section><Execution .../>`, pylabview names two flag
bits that both mean "this VI is reentrant": `IsReentrant` (1<<5) and
`SaveParallel` (1<<24, pylabview's "Save parallel"). A reentrant VI has one or
the other set. lvkit decodes: reentrant if `IsReentrant=1` OR `SaveParallel=1`;
then `PooledReentrancy=1` is a shared clone and `PooledReentrancy=0` is a
preallocated clone. Neither flag set is non-reentrant, and `PooledReentrancy`
is ignored there (a sticky leftover bit). Reading `IsReentrant` alone calls
every `SaveParallel` VI non-reentrant.

Evidence (4,799 corpus files):

- No file has both flags set.
- 206 VIs that NI's public docs say must be reentrant have `SaveParallel` only.
  An inlined subVI must also select Preallocated clone reentrant execution:
  "To inline a subVI ... You must also select Preallocated clone reentrant
  execution"
  (https://docs-be.ni.com/api/bundle/labview/page/vi-execution-speed.html).
- All 23 Actor Framework `Do.vi` overrides agree on reentrancy only when
  reading `IsReentrant` OR `SaveParallel`. NI requires every VI that implements
  a dynamic dispatch method to use the same reentrancy
  (https://docs-be.ni.com/api/bundle/labview-api-ref/page/errors/method-reentrancy-mismatch.html;
  see also
  https://docs-be.ni.com/api/bundle/labview-api-ref/page/errors/dynamic-reentrancy-not-allowed.html).
  Spot checks: `Core/ActorFramework/Message/Do.vi` is a shared clone;
  `Core/ActorFramework/Message Priority Queue/Dequeue Timed Out.vi` is a
  preallocated clone.
- Issue #132's attached `Run.vi` (`tests/corpus/issues/132/`) has
  `IsReentrant=0`, `SaveParallel=1`, `PooledReentrancy=0` -> preallocated clone.

## The `<Instrument Type>` code is the VI KIND, in its own numbering — NOT NI's VI Type property numbering (issue #144)

The LVSR Instrument record stores a 2-byte VI-kind code. pylabview labels it
with its `VI_TYPE` enum (`pylabview/LVinstrument.py`), which is NI's numbering
for the VI Type *property*; the stored field uses a different numbering, so
those labels are wrong (an ordinary VI prints as "Control"). lvkit recovers the
number from pylabview's label with pylabview's own enum
(`VI_TYPE[label].value`; a label that is already a number is used as is) and
maps the number with `lvkit.models.VIKind`:

| Stored code | VI kind | Corpus files |
|---|---|---|
| 2 | ordinary VI | 4,031 |
| 4 | global VI | 12 |
| 6 | control (.ctl: custom control, type definition and strict type definition alike) | 635 |
| 11 | polymorphic VI | 59 |

Each kind was verified by an independent structural test in both directions
(every file with the structure has the code, every file with the code has the
structure), stable across LabVIEW 9–25. Any other code is shown as
`unknown (code N)`, never guessed. Spot checks:
`tests/corpus/issues/39/zorder-not-respected.vi` is an ordinary VI; the icon
editor's `Global Variables/Data.vi` is a global VI; OpenG `Find Focus State__ogtk.ctl`
is a control; OpenG `Reorder Array__ogtk.vi` is a polymorphic VI.

The value exposed as `vi_type` (describe text and `--format json`, the index
`vi_type` column) is the readable kind: `VI`, `Global VI`, `Control`,
`Polymorphic VI`, or `unknown (code N)`.

## A connector-pane terminal's wiring rule is Flags bits 11-12 of the Function TypeDesc that CONP points to (issue #173)

Each connected slot is a child of the VI's own connector-pane Function
`TypeDesc` (`CONP` TypeID -> `VCTP/TopLevel` -> FlatTypeID). Its `Flags`:

| Bit | Meaning |
|---|---|
| 12 (`0x1000`) | Required |
| 11 (`0x0800`) | Recommended (when bit 12 is clear) |
| neither | Optional (`Flags="0x0000"` is a normal Optional input) |
| 8 (`0x0100`) | output |
| 15 (`0x8000`) | Dynamic Dispatch (with bit 12 on an input, bit 11 on an output) |

Evidence: the reporter's scan of 17,514 terminals in 3,890 VIs (LabVIEW 9.0 to
24.0): bit 8 on 7,983 of 7,983 outputs and 0 of 9,531 inputs; bit 12 on 3,987
inputs and 0 outputs; bits 11 and 12 never together. A re-check on a capped
sample (295 VIs, direction from the FP DCO `objFlags` bit 0): bit 8 on 553 of
553 outputs and 0 of 639 inputs; bit 12 on 274 inputs and no output; bit 11 on
360 inputs and 553 outputs; neither on 5 inputs; no slot with both 11 and 12;
bit 15 on 43 inputs and 38 outputs. Labels agree: `custom report message ("")`
is `0x0000`; JKI-EasyXML `Easy Write XML File.vi` has `XML File Path`
`0x1000` (Required), `Overwrite File? (false)` `0x0800` (Recommended),
`Encoding (none)` `0x0000` (Optional). NI's public docs name the dynamic-dispatch
settings "Dynamic Dispatch Input (Required)" / "Dynamic Dispatch Output
(Recommended)". Bits 0-3, 9, 10, 16, 17 are unrelated to the wiring rule
(meaning unknown).

A VCTP lists every type used in the VI, including other VIs' interfaces, so
more than one same-shaped `Function` TypeDesc can exist; only the one CONP
names is the VI's own (JKI-EasyXML `Easy Parse XML.vi`).

## A flattened array stores one 32-bit size per dimension, slowest-varying first, then the elements row-major; a Picture is a 4-byte length plus that many bytes (issues #174, #175)

An array default (front-panel `DefaultData`) or diagram constant (`ConstValue`)
is the flattened form of the value. NI's public "Flattened Data" page, section
"Arrays" (https://docs-be.ni.com/api/bundle/labview/page/flattened-data.html):
"Flattened 32-bit integers that record the size, in elements, of each of the
dimensions of an array precede the data for a flattened array. The slowest
varying dimension is first ... The flattened data follows immediately after
these dimension sizes in the same order in which LabVIEW stores them in
memory." Its example, a 2D array of six 8-bit integers `{{1,2,3},{4,5,6}}`,
flattens to `0000 0002 0000 0003 0102 0304 0506`.

So an N-dimensional array is `4 x N` bytes of sizes, then `product(sizes)`
elements; the dimension count N is the number of `<Dimension>` children of its
VCTP `Array` descriptor. Checked against the corpus (stored byte count =
`4 x N` + elements):

| VI (control) | Type | Sizes | Bytes |
|---|---|---|---|
| OpenG `Index Array Elements (2D) Example 01.vi` (`array`) | `[[U32]]` | 3 x 4 | 56 |
| OpenG `Reverse 2D Array Example 01.vi` (`Array`) | `[[TF]]` | 4 x 6 | 32 |
| measurement-plugin `Next Generation.vi` (`cells in`) | `[[TF]]` | 13 x 16 | 216 |
| OpenG `Write GIF File Example.vi` (`Array`) | `[[[U32]]]` | 75 x 36 x 36 | 388,812 |
| LabVIEW-OOP-Classes `AI_New.vi` (`Data`, a cluster with an empty 0 x 0 `[[DBL]]`) | cluster | 0 x 0 | 62 |

A reader that takes the first size as the whole element count decodes the
second size as an element, and inside a cluster every following field
misaligns.

A **Picture** is not described on that page. In the ni-labview-icon-editor
`Install Glyphs.vi` control `Glyph list` (an array of a cluster
`{Install: TF, Current: Picture, Icon Library: Picture, Subfolder: String,
Name: String}`), the stored default is 565 bytes with one element, which
equals `4 (array size) + 1 (TF) + 2 x (4 + 272) (the two pictures, each a
4-byte length 272 then 272 bytes) + 4 + 4 (the two empty strings)`: a Picture
is a length-prefixed byte run, deduced from the byte count.

That layout was then checked against every Picture-bearing value in the
extracted corpus (130 distinct VI and control files; all but three of the 409
values below come from the ni-labview-icon-editor sample project, the rest from
JKI-VI-Tester and actor-framework): 409 default and constant values in 206
extracts (338 front-panel defaults, 71 diagram constants). 374 of them are
consumed to exactly their stored byte count, so every Picture and every field
after it lines up; those 374 hold 169 Pictures (56 non-empty, in 21 distinct
files; 113 empty). The largest, `LayerList.ctl` `User layers`, is a cluster of
four non-empty Pictures in 12,452 bytes; `lv_icon.vi` `Framework Templates` is
1,172. The 35 that are not exact are not Picture mismatches: 34 are all-zero
diagram constants stored with one extra trailing zero byte (an empty array
`0000000000` is 5 bytes; the same extra byte appears on Picture-free empty
array and cluster constants), and 1 is a front-panel default whose array holds
a LabVIEW class object (`Layer.lvclass`), whose flattened private data (see
"Classes" on the NI page) the decoder does not read yet. Every value holding a
non-empty Picture is exact. Nearly all the evidence comes from one project, so a Picture saved by
another LabVIEW version is lightly tested. The drawing bytes themselves are not
documented publicly, so a non-empty Picture is shown as `<Picture, N bytes of
drawing not decoded>` (and listed as undecoded by the audit); an empty one
(length 0) shows as `None`.

A `TypeDef` descriptor holds the type it wraps as its single `Nested='True'`
child TypeDesc; for an `Array` that child carries the `<Dimension>` children
and the element's `TypeID`, and for a `Refnum` its `RefType` and payload
`TypeID` (child `TypeID`s are FlatTypeIDs like everywhere else in VCTP).

## A LabVIEW class value is a flattened object: level, class name section, version list, private data (issues #174, #175)

A class-typed value (a `Refnum` descriptor with `RefType="UDClassInst"` and an
`<Item>` chain naming the class) in a front-panel default or diagram constant is
not a 4-byte handle but the flattened object. NI's public "Flattened Data" page,
section "Classes" (https://docs-be.ni.com/api/bundle/labview/page/flattened-data.html):

- **level in hierarchy**: 4-byte unsigned integer. 0 is an instance of LabVIEW
  Object and the other three fields are omitted (the value is 4 bytes).
- **class name**: a length byte for the qualified name, then Pascal strings, the
  last of length zero, padded with zero bytes so the whole section is a multiple
  of 4.
- **version list**: one cluster of four 2-byte unsigned integers (8 bytes) per
  level. At level 1 with version 0 the value is the class's default data and the
  remaining field is omitted.
- **private data**: per level, a 4-byte byte count then exactly that many bytes of
  flattened cluster. NI's page says each cluster "ends with enough pad bytes to
  increase the cluster to a multiple of 4"; the stored count already includes
  them, and nothing follows the counted bytes. Decoding those bytes needs the
  class's own private data type, so lvkit skips them and shows the class name.

Evidence: in `Extract Default Icon Data.vi` the `All User Layers` default holds
one `Layer.lvclass` object of 112 bytes: `00000001` (level) + `0f 0d Layer.lvclass
00` (the 16-byte name section, already a multiple of 4) + 8 bytes of version +
`00000049` (73 bytes of private data, nothing after them). The fields after it
then start at the right byte: reading a pad up to 76 leaves the next field's
4-byte length 3 bytes late and the default 12 bytes short of its stored 181, and
without the pad all 181 bytes are consumed (the `Font` string in that cluster,
which had looked like a 1-byte-length string, is an ordinary 4-byte-length one). Across 236 class-bearing defaults and constants in 149
extracts (a deterministic quarter of the 2,302 distinct VIs that name a class;
723 class objects, 714 at level 1 and 9 at level 0), 210 are consumed to exactly
their stored byte count. Of the other 26, 24 are diagram constants
with one extra trailing zero byte (the same extra byte seen on constants with no
class or Picture), and 2 are one `Data` cluster whose last field is a type
descriptor lvkit does not decode (its 4 remaining bytes are zero). No value in
the sample has a level above 1, so the multi-level layout rests on the NI page
alone.


## Every stored default and constant reads to its exact size (audit of 14,914 values, issues #174, #175)

`scripts/audit_default_data.py` decodes every front-panel `DefaultData` and every
diagram constant of every extracted VI/control in the corpus (4,850 distinct
files; 14,914 values in 2,928 of them -- 1,485 front-panel defaults, 13,429
diagram constants) with `_decode_element` and requires each to consume exactly
its stored byte count AND read every field and element its type declares
(`lvkit.parser.audit`). Before the fixes below 10,573 were exact; after, all
14,914. What each group needed:

- **A diagram constant whose flattened value is all zero bytes carries one extra
  trailing zero byte.** 4,139 constants had a surplus of exactly one `00`; in all
  4,139 the value bytes were all zero, and in none of the other 9,290 diagram
  constants was there a surplus. It follows from nothing in NI's pages, so it is
  a stored-form fact of the constant (a front-panel default never has it):
  `lvkit.parser.audit.constant_padding`.
- **Extended-precision numbers are IEEE binary128** (1 sign bit, 15 exponent
  bits, 112 fraction bits, big-endian, 16 bytes; a complex is two). NI: "stores
  flattened extended-precision, floating-point numbers as 16-byte quantities".
  A physical quantity (a number with a unit, `UnitFloat*`/`UnitComplex*`) flattens
  as its plain number; it was being read as an enum. 33 values.
- **Tag refnums store flattened strings, not a 4-byte handle.** NI, "Refnums":
  type codes 0xE (`VisaRef`), 0xF (`IVIRef`) and 0x15 (`UsrDefndTag`) hold "a
  flattened string" (the tag); 0x1D (`UsrDefTagFlt`) holds the tag, a string, a
  string, a 32-bit integer and a string (an IMAQ image's pixels are that last
  string -- values up to 2 MB). Together with waveforms and unit-tagged extended
  numbers below, 46 values.
- **A Variant is `[version][type count][type descriptors][has value]
  [value type index][value][attribute count][attributes]`**, an attribute being a
  name string and another Variant. The type descriptors are those of NI's "Type
  Descriptors" page (`[size][flags][type code][info][name]`; an array names its
  element, a cluster its members, by index into the same list); the value is read
  with the type its descriptor names. Every Variant in the corpus (134) is the
  empty 20-byte form (version, one Void descriptor, has-value 1, type 0, no
  attributes); a Variant that holds a value is read from its descriptors.
- **A waveform is flattened like a cluster** (NI "Waveforms"): t0 (the 16-byte
  timestamp), dt (a double), Y (a 1D array of the sample type), the error
  cluster (Boolean, 32-bit integer, string) and the attributes Variant. Digital
  data is a cluster of the 1D U32 transition array and the 2D U8 data array;
  dynamic data a cluster of an array of waveforms. 57-byte example: 16 + 8 + 4 +
  9 + 20.
- **Set (type code 0x73) and Map (0x74)**: their type descriptors are
  `[header][element index][label]` and `[header][2][key index][value index]
  [label]` (the corpus's own descriptors; 15 values), which pylabview reads as a
  refnum. Their values are a 32-bit count then each element, or each key and
  value (NI "Sets" and "Maps"); the 37-byte Map `{'PinGroup1': ['Pin1', 'Pin2']}`
  is `00000001 00000009 "PinGroup1" 00000002 00000004 "Pin1" 00000004 "Pin2"`.
- **A type definition over a refnum pylabview cannot finish reading** (an IMAQ
  image's body ends in a Variant it misaligns) still names its refnum kind: the
  16-bit code right after the 4-byte header (15 values).
- **A cluster with no members stores no bytes**, and neither does a Void; a
  cluster may repeat a field name (`Left, Bottom, Right, Bottom`), so values keyed
  by name use `unique_names` (`Bottom`, `Bottom (2)`).
- **An array of more than 4,000,000 elements** (a camera frame of 18,001,000
  pixels is a front-panel default) is read to its end but its values are not kept.

A class object's private data is decoded when its class is loaded: the object
holds one cluster per level of the class hierarchy, oldest ancestor first, and
the graph gives the type's class the private-data fields of each level
(`InMemoryVIGraph.get_class_private_levels`, from the loaded `.lvclass` nodes and
their parent chain; `LVType.class_levels`). Each level's stored bytes are read
with that level's fields and must be consumed exactly; the value then shows as
`Layer.lvclass{'Origin': {...}, 'Data': {...}, 'Type': 2}` (all levels' fields in
one cluster display). In `Extract Default Icon Data.vi` the 73 stored bytes of
`Layer.lvclass` read exactly as its three private-data fields: `Origin` (cluster
`Horizontal`, `Vertical`), `Data` (cluster `LayerIcon` Picture, `LayerName`
`'VI Icon'`, `Opacity` 100, `Visibility?` True) and `Type` (a 16-bit enum, 2).
Where the class is not loaded, the number of levels differs from the object's,
or a level does not read exactly, the private data is skipped by its stored
count and the value shows the class name only; the bytes are still accounted
for. A class held in another class's private data is stamped the same way, down
to any depth; a class already being stamped further up (its private data holds
itself, directly or through another class) is left without levels, so that
inner instance shows its name and keeps its exact extent. The only corpus value with stored private data is that one (every other class object in
the corpus is the class's default, a one-level object at version 0).
