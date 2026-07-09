"""Node glyph resolver chain — the scalable node system (P2).

``resolve_glyph(node, ctx)`` tries an ORDERED list of resolvers; the first
one to return a non-``None`` ``Glyph`` wins. This is the whole extensibility
story: adding a new node's visual means ONE of —

1. Shipping the SubVI's own ``_ICON.png`` next to its ``.vi`` file — free,
   no code, no data change (``ExtractedIconResolver``).
2. Adding an ``icon`` field to a ``primitives.json`` / vilib JSON entry — a
   declaration, no code (``JsonGlyphResolver``).
3. Registering a new case in ``GeneratedGlyphResolver`` for a code-drawn
   built-in (arithmetic triangle, bracket, ...).
4. Doing nothing — ``FallbackBoxResolver`` always succeeds with a labeled
   box, so resolution can never fail.

Resolvers are a plain ordered list of instances (not a plugin framework —
there is nothing here that benefits from dynamic registration; the list in
this module IS the registration point).
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .._data import data_dir as _bundled_data_dir
from ..extractor import extract_vi_xml
from ..graph.core import InMemoryVIGraph
from ..graph.models import (
    AnyGraphNode,
    ConstantNode,
    FormulaNode,
    LocalVariableNode,
    PrimitiveNode,
    VINode,
)
from ..primitive_resolver import NodeIcon
from ..primitive_resolver import get_resolver as get_prim_resolver
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .glyph import (
    ArithGlyph,
    BooleanConstantGlyph,
    BracketGlyph,
    CenteredSvgGlyph,
    CompoundArithGlyph,
    ConstantGlyph,
    ErrorClusterGlyph,
    Glyph,
    IconImageGlyph,
    InlineSvgGlyph,
    LabeledBoxGlyph,
    VariantGlyph,
    WrappedBoxGlyph,
)
from .style import numeric_repr, type_family, wire_style

logger = logging.getLogger(__name__)


def _format_const(value: object) -> str:
    """LabVIEW-style scalar constant text: a whole-valued float (or a numeric
    string like '0.0') shows with no trailing '.0'; non-whole and non-numeric
    values stringify as-is. Only call this for NUMERIC-typed constants."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            return value
        return str(int(f)) if f.is_integer() else value
    return str(value)


def string_const_display(raw: object) -> str:
    """DISPLAY text for a string constant. The parser stores the value as a
    Python string literal wrapped in single quotes with ``\\`` and ``'``
    escaped (parser/vi.py — ``f"'{escaped}'"``); codegen needs those quotes,
    but the diagram must show the bare text. Strip the surrounding quotes and
    unescape ``\\'`` -> ``'`` and ``\\\\`` -> ``\\`` (a single left-to-right
    scan, so escapes never re-corrupt each other). Real newlines in the value
    are untouched. Non-quoted values (defensive) pass through unchanged."""
    s = str(raw) if raw is not None else ""
    if len(s) < 2 or s[0] != "'" or s[-1] != "'":
        return s
    body = s[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] in ("'", "\\"):
            out.append(body[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_BOOL_TRUE_TOKENS = frozenset({"true", "t", "1", "yes", "on"})


def _bool_value(raw: object) -> bool:
    """Truthiness of a boolean constant's stored value — the parser may hand
    back a real ``bool``, or a string/number token ('True'/'T'/'1'/...)."""
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _BOOL_TRUE_TOKENS

# Arithmetic-primitive name/operation -> triangle symbol (moved here from
# the old draw.py dispatch dict — this IS "add a code-drawn built-in").
_ARITH_SYMBOL = {
    "Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷",
    "Increment": "+1", "Decrement": "-1",
}

_DEFAULT_ICON_SIZE = (24, 24)


@dataclass(frozen=True)
class GlyphContext:
    """Context available to node glyph resolvers.

    Deliberately small: resolvers work off the graph node itself plus the
    owning graph/VI (to look up a SubVI's own source path). They never see
    the ``Scene``/``RenderNode`` or heap geometry — a glyph's shape doesn't
    depend on where it sits on the diagram.
    """

    graph: InMemoryVIGraph
    vi_name: str


class NodeGlyphResolver(Protocol):
    """One link in the resolver chain. Return ``None`` to fall through."""

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None: ...


@functools.lru_cache(maxsize=256)
def _vectorized_icon(path_str: str, mtime: float) -> CenteredSvgGlyph | None:
    """Vectorize a SubVI's ``_ICON.png`` into an SVG glyph, cached by path +
    mtime so repeated renders don't re-vectorize an unchanged icon (and an
    edited icon re-vectorizes since ``mtime`` is part of the cache key)."""
    from .icons import png_to_svg  # noqa: PLC0415 - optional dependency, guarded

    try:
        data = Path(path_str).read_bytes()
    except OSError:
        return None
    result = png_to_svg(data)
    if result is None:
        return None
    fragment, size = result
    return CenteredSvgGlyph(fragment, size)


class ExtractedIconResolver:
    """Best-effort real SubVI ``_ICON.png`` for VINode subVI calls.

    The caller's heap XML only carries the CALLER's own icon (drawn as the
    corner decoration in ``draw_scene``) — a subVI's icon requires the
    subVI's own file. This resolver only uses what's cheaply already known:

    - ``graph.get_vi_source_path(name)``, if the subVI happens to already
      be loaded in the same graph (free — a dict lookup); or
    - ``node.qualified_path``, if it happens to already be a literal,
      existing file path (rare: today's ``qualified_path`` values are raw
      LabVIEW path TOKENS like ``"<vilib>/Utility/error.llb/Foo.vi"``, not
      resolved filesystem paths — resolving the ``<vilib>``/``<userlib>``
      tokens would need a library root the graph doesn't expose publicly;
      see the JUDGMENT CALL note in this module's tests / the P2 report).

    If neither is available, it returns ``None`` (fall through) rather than
    loading/parsing the subVI's own graph — that would be exactly the
    "force-load subVIs expensively" this resolver must avoid. Extracting
    the (cached) heap XML of an already-located file is still a subprocess
    call on a cache miss, so every failure mode here is wrapped and
    fail-soft: an icon is a decoration, never a reason to fail rendering.
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if not isinstance(node, VINode):
            return None
        name = node.name
        if not name:
            return None

        src_path = ctx.graph.get_vi_source_path(name)
        if src_path is None and node.qualified_path:
            candidate = Path(node.qualified_path)
            if candidate.is_file():
                src_path = candidate
        if src_path is None:
            return None

        try:
            bd_xml, _, _ = extract_vi_xml(src_path)
        except Exception:
            logger.debug(
                "subVI icon extraction failed for %r (%s)", name, src_path,
                exc_info=True,
            )
            return None

        icon_path = bd_xml.parent / f"{bd_xml.stem.replace('_BDHb', '')}_ICON.png"
        if not icon_path.is_file():
            return None
        glyph = _vectorized_icon(str(icon_path), icon_path.stat().st_mtime)
        if glyph is not None:
            return glyph
        return IconImageGlyph(icon_path)


class JsonGlyphResolver:
    """The optional, declarative ``icon`` field on a primitive/vilib entry.

    Mirrors the exact lookup order ``graph/construction.py`` already uses
    to resolve these nodes semantically (prim_id/name, then node_type for
    primitives; poly-variant then plain name for VI calls) — so "does this
    node have a declared icon" asks the same question codegen already
    answers, just adding one optional field to the result.
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        icon: NodeIcon | None = None
        if isinstance(node, PrimitiveNode):
            icon = self._primitive_icon(node)
        elif isinstance(node, VINode):
            icon = self._vi_icon(node)
        if icon is None:
            return None
        return self._glyph_from_icon(icon)

    @staticmethod
    def _primitive_icon(node: PrimitiveNode) -> NodeIcon | None:
        resolver = get_prim_resolver()
        resolved = resolver.resolve(prim_id=node.prim_id, name=node.name)
        if resolved is not None and resolved.icon is not None:
            return resolved.icon
        if node.node_type:
            nt_resolved = resolver.resolve_by_node_type(node.node_type)
            if nt_resolved is not None:
                return nt_resolved.icon
        return None

    @staticmethod
    def _vi_icon(node: VINode) -> NodeIcon | None:
        resolver = get_vilib_resolver()
        entry = None
        if node.poly_variant_name and node.name:
            entry = resolver.resolve_poly_variant(node.name, node.poly_variant_name)
        if entry is None and node.name:
            entry = resolver.resolve_by_name(node.name)
        return entry.icon if entry is not None else None

    @staticmethod
    def _glyph_from_icon(icon: NodeIcon) -> Glyph | None:
        if icon.svg is not None:
            return InlineSvgGlyph(icon.svg, icon.size or _DEFAULT_ICON_SIZE)
        if icon.file is not None:
            path = _bundled_data_dir() / "glyphs" / icon.file
            try:
                fragment = path.read_text()
            except OSError:
                logger.debug("glyph asset not found: %s", path)
                return None
            return InlineSvgGlyph(fragment, icon.size or _DEFAULT_ICON_SIZE)
        return None


@functools.lru_cache(maxsize=1)
def _glyph_asset_stems() -> frozenset[str]:
    """Cached set of extracted PDF-icon asset filename stems under
    ``data/glyphs/`` (see ``scripts/extract_lv_icons.py``) — avoids
    re-globbing the directory on every node's glyph resolution."""
    glyphs_dir = _bundled_data_dir() / "glyphs"
    if not glyphs_dir.is_dir():
        return frozenset()
    return frozenset(p.stem for p in glyphs_dir.glob("*.png"))


@functools.lru_cache(maxsize=1)
def _svg_asset_stems() -> frozenset[str]:
    """Cached set of vectorized icon asset filename stems under
    ``data/glyphs/`` (see ``scripts/vectorize_icons.py``) — mirrors
    ``_glyph_asset_stems`` but for the pixel-faithful ``.svg`` assets."""
    glyphs_dir = _bundled_data_dir() / "glyphs"
    if not glyphs_dir.is_dir():
        return frozenset()
    return frozenset(p.stem for p in glyphs_dir.glob("*.svg"))


@functools.lru_cache(maxsize=1)
def _svg_sizes() -> dict[str, tuple[int, int]]:
    """Natural ``(w, h)`` per SVG icon stem, from
    ``data/glyphs/_svg_sizes.json`` (see ``scripts/vectorize_icons.py``)."""
    path = _bundled_data_dir() / "glyphs" / "_svg_sizes.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: (int(v[0]), int(v[1])) for k, v in raw.items()}


class PdfIconResolver:
    """Real, PDF-extracted LabVIEW icons for boxed primitives (built by
    ``scripts/extract_lv_icons.py`` from the reference manual — see
    FIDELITY_PLAN.md step 5). Boxed array/cluster/string/variant primitives
    (Build Array, Index Array, String Length, ...) get their real
    connector-pane icon here; borderless arithmetic/comparison primitives
    are never boxed in the manual, so they have no extracted asset and fall
    through unchanged to ``GeneratedGlyphResolver``'s triangle.

    Lookup mirrors codegen's node_type-before-prim_id precedence
    (``codegen/nodes/primitive.py``): several array primitives are dispatched
    by XML class (node_type) rather than primResID, and some of those
    classes SHARE a primResID with an unrelated "prim"-class primitive (e.g.
    "Index Array" runs under primResID 1809, which primitives.json
    separately and correctly labels "Array Size" for the generic "prim"
    class). Checking the node_type-keyed asset (``prim_nt_<node_type>.png``)
    before the prim_id-keyed one (``prim_<prim_id>.png``) prevents a
    node_type-dispatched primitive from ever inheriting a same-numbered but
    unrelated primitive's icon.
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if not isinstance(node, PrimitiveNode):
            return None
        png_stems = _glyph_asset_stems()
        svg_stems = _svg_asset_stems()
        if node.node_type and node.node_type != "prim":
            glyph = self._load(f"prim_nt_{node.node_type}", png_stems, svg_stems)
            if glyph is not None:
                return glyph
        if node.prim_id is not None:
            glyph = self._load(f"prim_{node.prim_id}", png_stems, svg_stems)
            if glyph is not None:
                return glyph
        return None

    @staticmethod
    def _load(
        stem: str, png_stems: frozenset[str], svg_stems: frozenset[str]
    ) -> Glyph | None:
        if stem in svg_stems:
            natural = _svg_sizes().get(stem)
            if natural is not None:
                path = _bundled_data_dir() / "glyphs" / f"{stem}.svg"
                try:
                    fragment = path.read_text()
                except OSError:
                    logger.debug("svg glyph asset not found: %s", path)
                else:
                    return CenteredSvgGlyph(fragment, natural)
        if stem in png_stems:
            path = _bundled_data_dir() / "glyphs" / f"{stem}.png"
            return IconImageGlyph(path)
        return None


# cpdArith operation -> Boolean-context translation. Same mapping codegen
# applies in ``codegen/nodes/compound.py::generate_compound_arith`` — a
# Boolean compound-arith's "add" is a logical OR and "multiply" is a
# logical AND, not the raw arithmetic operator.
_CPD_ARITH_BOOLEAN_OP = {"add": "or", "multiply": "and"}


def _cpd_arith_boolean(node: PrimitiveNode) -> bool:
    """Whether ``node`` (a cpdArith primitive) operates on Booleans — true if
    its output OR any input terminal carries a Boolean type. Mirrors
    ``codegen/nodes/compound.py::_is_boolean``/its use in
    ``generate_compound_arith``, so the glyph and the generated code agree."""
    return any(
        t.lv_type is not None and t.lv_type.underlying_type == "Boolean"
        for t in node.terminals
    )


def _cpd_arith_operation(node: PrimitiveNode) -> str:
    """The operator symbol key to feed ``CompoundArithGlyph`` for a cpdArith
    node: ``node.operation`` translated to its Boolean-context equivalent
    (add->or, multiply->and) when the node's terminals are Boolean."""
    operation = node.operation or "or"
    if _cpd_arith_boolean(node):
        return _CPD_ARITH_BOOLEAN_OP.get(operation, operation)
    return operation


class GeneratedGlyphResolver:
    """Code-drawn built-ins — migrated from the old ``draw.py`` dispatch
    dict. This is where "adding a visual" still means writing a branch,
    reserved for shapes that are cheap to describe procedurally and don't
    warrant a hand-authored SVG asset (arithmetic triangles, brackets)."""

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if isinstance(node, PrimitiveNode):
            return self._primitive_glyph(node)
        if isinstance(node, VINode):
            # No custom icon: draw the VI name wrapped inside the box (up to 4
            # lines), LabVIEW's default no-icon subVI look — see WrappedBoxGlyph.
            return WrappedBoxGlyph(node.name or "")
        if isinstance(node, ConstantNode):
            fam = type_family(node.lv_type)
            if fam == "error_cluster":
                return ErrorClusterGlyph()
            if fam == "variant":
                return VariantGlyph()
            if fam == "bool":
                raw = node.raw_value if node.value is None else node.value
                return BooleanConstantGlyph(_bool_value(raw))
            color = wire_style(node.lv_type).color
            raw = node.raw_value if node.value is None else node.value
            if numeric_repr(node.lv_type) is not None:
                value = _format_const(raw)
            elif fam == "string":
                # Show the bare text (quotes/escapes are a codegen artifact).
                value = string_const_display(raw)
            else:
                value = str(raw) if raw is not None else ""
            # String constants word-wrap to fill their (already content-sized)
            # box instead of collapsing to one ellipsized line.
            return ConstantGlyph(value or "", color, multiline=fam == "string")
        if isinstance(node, FormulaNode):
            return LabeledBoxGlyph(
                node.name or "Formula", "prim_fill", "prim_stroke", 1.5,
            )
        if isinstance(node, LocalVariableNode):
            # LabVIEW's Local Variable glyph: a plain box with the
            # referenced control's NAME inside — no icon, unlike a subVI.
            return WrappedBoxGlyph(
                node.control_name or node.name or "Local Variable",
                "localvar_fill", "localvar_stroke", 1.0,
                max_lines=2, text_size=7.0,
            )
        return None

    @staticmethod
    def _primitive_glyph(node: PrimitiveNode) -> Glyph:
        if node.node_type == "cpdArith":
            num_inputs = sum(1 for t in node.terminals if t.direction == "input")
            return CompoundArithGlyph(
                _cpd_arith_operation(node), num_inputs=max(1, num_inputs),
            )
        sym = _ARITH_SYMBOL.get(node.operation or node.name or "")
        if sym:
            return ArithGlyph(sym)
        if node.node_type == "aBuild" or node.name == "Build Array":
            return BracketGlyph()
        # No icon yet: wrap the primitive's name inside the box (up to 4 lines,
        # adaptive font) — same treatment as an icon-less subVI.
        return WrappedBoxGlyph(node.name or "?", "prim_fill", "prim_stroke", 1.0)


class FallbackBoxResolver:
    """The labeled box. ALWAYS returns a ``Glyph`` — resolution can't fail."""

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph:
        label = node.name or node.node_type or "?"
        return WrappedBoxGlyph(label, "prim_fill", "prim_stroke", 1.0)


# The registration point: an ordered list, tried in order, first hit wins.
# Add a resolver here to extend the mechanism; add an icon/asset/JSON entry
# to extend WITHOUT touching this list at all.
_RESOLVERS: list[NodeGlyphResolver] = [
    ExtractedIconResolver(),
    JsonGlyphResolver(),
    PdfIconResolver(),
    GeneratedGlyphResolver(),
    FallbackBoxResolver(),
]


def resolve_glyph(node: AnyGraphNode, ctx: GlyphContext) -> Glyph:
    """Resolve ``node``'s visual via the ordered resolver chain.

    Always returns a ``Glyph`` — the last resolver (``FallbackBoxResolver``)
    never returns ``None``.
    """
    for resolver in _RESOLVERS:
        glyph = resolver.resolve(node, ctx)
        if glyph is not None:
            return glyph
    raise AssertionError("FallbackBoxResolver must always return a Glyph")
