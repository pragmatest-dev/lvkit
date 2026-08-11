# labview-mcp follow-ups: aixml, NI corpus, VI-Server reference

Investigation notes (task #13) prompted by [zuehlke/labview-mcp](https://github.com/zuehlke/labview-mcp)
(MIT). **Clean-room rule holds throughout: lvkit never uses or requires
LabVIEW.** labview-mcp deliberately does the opposite — it drives a live LabVIEW
to reverse-engineer LabVIEW — so we can learn from its *findings* but must not
copy any artifact it extracted from a running LabVIEW.

## 1. aixml — NOT a clean-room source; the netlist IR is already the equivalent

**What it is.** aixml is LabVIEW 2026's own textual block-diagram format from its
AI feature: nodes with a `uid`, wires as `terminal:uid.terminal` refs in
`inputs`/`outputs`, **no coordinates** (LabVIEW re-lays-out on import).

**How labview-mcp gets it.** `lvai_convert_vi_to_aixml`, one of 23 *private*
gRPC RPCs on NI's `lvai.LVAI` interface, recovered via gRPC reflection against a
running **LabVIEW 2026 with the AI feature active**. Producing OR consuming
aixml requires that live server — there is no offline path.

**Assessment.** lvkit cannot produce aixml (it needs LabVIEW), and shouldn't
chase a byte-identical emitter of NI's proprietary, LV-derived format. But note
the parallel: aixml's model (nodes+uid, wires as `producer.terminal` refs, no
coordinates) is **exactly what `graph/netlist.py`'s IR already captures** —
`netlist_to_dict` is the clean-room, faithful-typed equivalent. So:

> **Caveat (do NOT record as fact):** the aixml "gaps" catalogued from
> labview-mcp's `docs/aixml-reference.md` (no class identity, dropped refnum
> element, lost property/method names, palette-only subVIs) are limitations of
> **labview-mcp's decoding** of aixml, NOT verified properties of aixml itself.
> aixml comes out of NI's own **Nigel** service and must round-trip VI↔aixml for
> the AI feature, so it is likely far more faithful than labview-mcp captured
> (their own wording — invoke `target` is "a binary ID", names "not looked up
> from outside LabVIEW" — points to under-decoding, not aixml loss). We CANNOT
> verify real aixml clean-room (that needs LabVIEW). So this is NOT evidence that
> "lvkit > aixml"; it only tells us where labview-mcp's parser stops.
>
> The one thing that survives as verified: comparing aixml's *documented*
> `ref{Queue}{ELEM}` notation to lvkit's OWN output exposed a real gap **in
> lvkit** — bare `Queue refnum`, element dropped. Fixed: the VCTP refnum parser
> now resolves the single nested element and `lv_label` renders it consistently
> (`Queue refnum{error cluster}`, even nested `EventReg refnum{UserEvent
> refnum{...}}`). Pre-LV9 CONP refnum elements remain a follow-up.
- **No literal aixml emitter** unless a concrete interop goal appears (feeding
  lvkit output INTO a LabVIEW-AI pipeline). Even then, matching a proprietary
  format reverse-engineered from LabVIEW is a moving, EULA-adjacent target.
- If desired later, an aixml-*shaped* export is a thin projection of the same IR
  (`_item_to_dict` already emits `uid` + `port -> source.net`).

## 2. Permissively-licensed NI corpus — ni/labview-icon-editor ADDED

NI's official org (`github.com/ni`, ~300 repos) has real MIT-licensed LabVIEW
code. labview-mcp itself ships **no** `.vi` (helper VIs are generated to `%TEMP%`
at runtime), so it is not a corpus source.

**Added to `scripts/pull_samples.sh`:**
- **ni/labview-icon-editor** (MIT) — the *actual* icon editor shipped with
  LabVIEW, a real OOP app: **416 `.vi`, 15 `.lvclass`, 7 `.lvlib`, 3 `.lvproj`**,
  LabVIEW 2020. Verified: lvkit parses it (`SAMPLE_lv_icon.vi` →
  `imagedata{image type, image depth, image, mask, colors, Rectangle}`). MIT
  permits vendoring, but per our policy it stays local-only/pulled like the rest.

**Other verified-MIT candidates (not yet added):** ni/labview-fpga-examples
(FPGA-target VIs — specialized primitives, lower priority), ni/VireoSDK
(WebVI examples), ni/systemlink-labview-examples, ni/python_labview_automation.
`ni/measurement-plugin-labview` (MIT) was already in the corpus.

## 3. VI-Server property/method reference — build from PUBLIC docs, not the TSVs

labview-mcp ships `docs/vi-server-methods.tsv` (3078 invoke targets, 153 classes)
and `docs/vi-server-properties.tsv` (6410 property fields). **We cannot use
these** — they were extracted from a running LabVIEW via gRPC reflection
(LV-derived → poisons the clean room), and (user caveat) their ordering is NOT
known to match the property/method **index** encoded in the `.vi` binary, so the
list can't even be trusted to map name↔ID positionally.

**Why lvkit wants one.** Property-node field names already resolve from the FP
heap (`b59b937`). The open gap is **invoke-node PARAM names**: they live in the
method's VI-server signature, not the VI file (`_invoke_node_glyph` notes this).
A clean-room method/param table would let lvkit name invoke-node params.

**Plan (clean-room).** Derive it from NI's PUBLIC docs (`docs-be.ni.com/api/...`,
the same backend used for primitives — see `reference_ni_web_docs_fetch`), keyed
by class → method → ordered params. It's large (~153 classes); build
incrementally, store under `src/lvkit/data/` like the other clean-room tables.
**Verify ID/order against real corpus VIs** (now including the icon editor, which
uses VI-Server heavily) rather than trusting any external list's ordering.

## Recommendation

- **Do now:** the corpus add (done) — free, real NI code, exercises OOP/VI-Server.
- **Worth building:** the public-docs VI-Server method/param reference (part 3) —
  closes a real naming gap, fully clean-room.
- **Defer:** aixml — the netlist IR already is the clean-room equivalent; revisit
  only for a concrete LabVIEW-AI interop need.
