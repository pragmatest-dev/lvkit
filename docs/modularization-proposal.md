# Modularization proposal: adopt the node-handler pattern everywhere

## Thesis

lvkit's biggest "god-code" isn't long files — it's a **missing abstraction**.
Handling a LabVIEW node type (parse it, build its graph node, generate its code,
draw its glyph) is one cross-cutting concern, and the codebase **already
implements the right pattern for it in one stage** — the parser's
`NodeTypeHandler` Strategy + registry. The other stages re-implement the same
"dispatch on node type" concern in weaker, inlined forms. The fix is not to chop
files up; it's to **extend the handler pattern the parser already proves** to the
graph-build, codegen, and render stages.

Symptom of the missing abstraction: adding a node type today (we added
`decimate`/`interLeave` capture this session) means editing **four scattered
places in four different styles**. With per-stage registries, a new node type is
**one handler class, registered** — no god-code is touched (open/closed).

## Evidence: how each stage dispatches on node type

| Stage | Location | Mechanism | Verdict |
|-------|----------|-----------|---------|
| **Parse** | `parser/node_types.py` | `NodeTypeHandler(ABC)` + `_HANDLERS` list → `NODE_HANDLERS` dict → `parse_node()` factory with `GenericHandler` fallback | ✅ **The template** |
| **Codegen** | `codegen/nodes/__init__.py` | per-type **modules already exist** (`case.py`, `nmux.py`, `compound.py`, `loop.py`, `subvi.py`…), but wired by `match node.node_type:` — a 20-case hand-maintained string switch | ⚠️ Half-there: bodies split, **dispatch inlined** |
| **Graph build** | `construction.py::_add_vi_to_graph` | 19 `node_type ==` branches in **one 885-line method**, no modules | ❌ Strategy fully inlined into a method |
| **Render** | `render/nodes.py` | scattered `isinstance(...)` + `node_type ==` glyph selection | ❌ Ad-hoc |

## The template (what the parser already does)

```python
class NodeTypeHandler(ABC):
    xml_class: str          # "cpdArith", "prim", ...
    display_name: str
    @abstractmethod
    def parse(self, elem) -> ParsedNode: ...
    def _extract_common(self, elem) -> dict: ...   # shared helper

_HANDLERS = [PrimitiveHandler(), SubVIHandler(), ...]     # register once
NODE_HANDLERS = {h.xml_class: h for h in _HANDLERS}       # the registry

def parse_node(elem):
    return (NODE_HANDLERS.get(elem.get("class"))
            or GenericHandler(elem.get("class"))).parse(elem)   # + fallback
```

Three properties to copy into every stage: an **abstract handler**, a **registry
keyed by node type**, and a **generic fallback** for unhandled classes (the same
fallback that lets #83's captured unknown nodes render as boxes and fail loudly
in codegen).

## The design: one handler registry per stage

Recommended, because it mirrors the existing per-stage structure (parser handlers
+ codegen modules are already per-stage) and keeps stages independent:

| New abstraction | Replaces | Keyed by | Fallback |
|-----------------|----------|----------|----------|
| `GraphBuildHandler` + `BUILD_HANDLERS` (new `graph/build/`) | the 19 branches of `_add_vi_to_graph` | `node_type` | generic passthrough node |
| `CodegenHandler` + `CODEGEN_HANDLERS` (in `codegen/nodes/`) | the `match node.node_type:` string switch | `node_type` | `_emit_unknown` (already loud) |
| `GlyphHandler` + `GLYPH_HANDLERS` (in `render/`) | `nodes.py` isinstance/`node_type` scatter | `node_type` | labelled-box glyph |

Each handler is small, single-node, and unit-testable in isolation. `_add_vi_to_graph`
collapses to a dispatch loop; `_generate_primitive`'s switch becomes a dict lookup
over the modules that **already exist**.

> **North-star option (not recommended now):** one class per node type owning
> *all* stages (`class DecimateNode: parse/build/codegen/glyph`). Maximal
> cohesion — everything about a node in one file — but it fights the current
> per-stage grain and couples the stages. Note it; don't do it. Per-stage
> registries get 90% of the benefit at a fraction of the risk.

## The strangler recipe (per node-kind, always green)

This is a **control-flow** change, not a code move, so the byte-identical gate is
load-bearing:

```
For each stage, then each node kind within it:
1. Add the handler class (body = the existing branch/module, moved verbatim).
2. Register it; route ONE node kind through the registry, leaving the switch
   for the rest (strangler-fig — both paths coexist).
3. uv run pytest -q ; ruff ; pyright
4. Byte-identical gate: render + codegen a fixed VI set, diff SVG/py hashes
   vs. pre-change (the #64/#65 gates). MUST be identical — proves pure relocation.
5. Commit. Repeat for the next kind; delete the switch when the last kind moves.
```

Because both paths coexist mid-migration, the tree is always shippable and every
step is independently revertable.

## Sequencing (value × leverage)

1. **`_add_vi_to_graph` → `GraphBuildHandler` registry.** The flagship: an
   885-line untestable method becomes N small handlers. Highest value; the
   byte-identical codegen gate makes it safe.
2. **Codegen switch → `CODEGEN_HANDLERS`.** Cheapest — the handler bodies are
   *already* separate modules, so this is just formalizing the dispatch as a
   dict. Do it right after #1 to lock the pattern in.
3. **Render glyph dispatch → `GLYPH_HANDLERS`.** Medium; consolidates the
   scattered isinstance checks.

Each new node type afterwards registers into these three (plus the existing
parser handler) — four small registrations, zero god-code edits.

## Benefits

- **Open/closed.** New node type = new handler files + registrations. No existing
  dispatch code is modified — the exact opposite of today's four-place edit tax.
- **Testable.** Each handler is a small unit with a focused test, vs. today's
  885-line method and 20-case switch that can only be tested end-to-end.
- **Consistent.** One pattern across all four stages; a contributor learns it once
  (from the parser) and applies it everywhere.
- **Leverages the loud fallback.** The generic-handler fallback is where #83's
  unknown-node capture and "codegen fails loudly" already live — the pattern
  makes that behaviour uniform instead of special-cased.

## Honest costs and limits

- **Bigger than file-splitting.** It changes control flow, so it leans entirely on
  the byte-identical gate. Do it strangler-style, one kind at a time — never a
  big-bang rewrite.
- **Registry indirection.** A registry is only worth it because node types here
  are an **open set** (we keep adding them). For a closed set a `match` is
  clearer; this is justified *here*, not universally.
- **Keep the good match.** Codegen's *outer* `match node:` on `Operation`
  subclasses (in_place/case/loop/…) is idiomatic and stays — only the inner
  `node_type` string switch becomes a registry.
- **This does NOT address the non-dispatch bulk.** `scene.py`'s wire
  routing/build and `draw.py`'s connector-pane/FP-terminal code are large but
  *cohesive* — not a missing node abstraction. Leave them, or split
  opportunistically only when they actively hurt (merge conflicts, onboarding).
  Don't conflate "big file" with "missing Strategy."

## Bottom line

The high-value, principled work is **the pattern adoption**, not the file
surgery: turn the 885-line method and the codegen switch into registries that
match the parser the codebase already got right. Start with `_add_vi_to_graph`.
