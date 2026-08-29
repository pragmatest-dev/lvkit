"""The lvnet text-surface RENDERER -- graph/NetlistModule IR -> lvnet text.

Split out of ``netlist.py`` (which still holds the IR build and the OLD
``render_netlist``/``netlist_to_dict`` projections). This module owns ONLY
``render_lvnet`` and everything it exclusively depends on: see
``docs/_internal/design/netlist-language.md`` §2-§10 for the CLOSED grammar
every character here traces to (an OPEN construct, §17, emits its header
keyword plus a literal ``# TODO(lvnet): ...`` and no invented inner syntax).

``_quoted_frame_label`` is also here even though ``render_netlist`` (which
stays in ``netlist.py``) calls it too -- ``netlist.py`` imports it back from
here rather than this module reaching into ``netlist.py`` (this direction
only, no cycle).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ..models import (
    DisableStructureKind,
    LVType,
    LVTypeKind,
    ScalarValue,
    _is_error_cluster,
    _strip_typedef_stem,
)
from ..num_format import format_numeric_const
from .interface_order import WiringRequirement
from .lvnet_grammar import (
    _LOCAL_VARIABLE_TODO,
    _LVNET_ANNOTATION_SEP,
    _LVNET_BLOCK_OPEN,
    _LVNET_CLUSTER_OPEN,
    _LVNET_DEFAULT_KEYWORD,
    _LVNET_DEFAULT_PAREN_PREFIX,
    _LVNET_DEP_INTERFACE_INDENT,
    _LVNET_DEP_KIND_CAP,
    _LVNET_DEP_PATH_SEP,
    _LVNET_DEP_QUALIFIED_CAP,
    _LVNET_DISABLE_KEYWORD,
    _LVNET_DRIVER_OP,
    _LVNET_ENUM_OPEN,
    _LVNET_INDENT,
    _LVNET_INSTANCE_KEYWORDS,
    _LVNET_NAME_CAP,
    _LVNET_RING_OPEN,
    _LVNET_STRING_ESCAPES,
    _LVNET_STRUCTURE_NET_RE,
    _LVNET_TERMINAL_SEP,
    _LVNET_TUNNEL_MODE_WORD,
    _LVNET_TYPE_CAP,
    _LVNET_TYPE_SEP,
    _LVNET_TYPEDEF_NAV_PREFIX,
    _OPEN_INSTANCE_TRAILING_TODO,
    _TYPES_HEADER_LINE,
    _USES_HEADER_LINE,
)
from .models import Constant
from .netlist_models import (
    ConnectorPaneTerminal,
    DefaultValue,
    EtaMerge,
    GammaMerge,
    MuMerge,
    NetlistConstant,
    NetlistDependency,
    NetlistFeedback,
    NetlistInstance,
    NetlistInstanceKind,
    NetlistItem,
    NetlistModule,
    NetlistScope,
    NetRef,
)
from .op_walk import _format_error_cluster

# ============================================================
# Shared with the OLD ``render_netlist`` (netlist.py) -- moved here anyway
# per module ownership; netlist.py imports it back.
# ============================================================


def _quoted_frame_label(label: str) -> str:
    """Wrap a case/disabled frame label in ONE pair of quotes for netlist
    display.

    Most labels (``No Error``, ``True``, ``Default``, an enum item name, an
    integer range) carry no quoting of their own and need one pair added
    here. A STRING selector is the exception: ``op_walk._selector_label``
    already returns it pre-quoted (``'"TestCase.lvclass"'``) so the renderer
    -- ``render/scene.py`` -- can show it as-is with no extra formatting.
    Re-wrapping that case here would double-quote it (``""...""``), so
    detect an already-quoted label and pass it through unchanged instead.
    """
    if len(label) >= 2 and label.startswith('"') and label.endswith('"'):
        return label
    return f'"{label}"'


# ============================================================
# render_lvnet -- the lvnet surface (docs/_internal/design/netlist-language.md)
#
# NEW renderer, sibling of ``render_netlist`` -- see the module's Phase A
# docstring notes throughout this file. Emits ONLY the §2-§10 CLOSED
# grammar; every character traces to a rule in that spec. An OPEN construct
# (§17) gets its header keyword plus a literal ``# TODO(lvnet): ...`` and
# NO invented inner syntax -- see ``_OPEN_INSTANCE_KINDS``.
# ============================================================


@dataclass(frozen=True)
class _TermLine:
    """One terminal-line's pre-render facts, for ``_render_term_group``'s
    shared column-alignment pass -- direction/name/type are always present
    (lvnet §3: never dropped, wired or not); ``trailing`` is the already-
    rendered ``"= <driver>"``/``"default <value>"`` text for an INPUT line,
    or ``None`` for an OUTPUT line (never a driver -- §3: "with NO driver").
    """

    direction: str  # "in " or "out" -- already the 3-char padded keyword
    name: str
    type: str
    trailing: str | None


def _lvnet_capped_pad(text: str, width: int, cap: int) -> str:
    """Left-justify ``text`` to ``width`` -- UNLESS it exceeds ``cap``, in
    which case it renders as-is plus exactly ONE trailing space (never
    zero -- the guard against a column's next part touching it) instead of
    stretching to ``width``. ``width`` itself is computed by the caller
    from only the entries that fit under the cap (see
    ``_render_term_group``), so an overflowing entry never influences its
    siblings' alignment either.
    """
    if len(text) > cap:
        return text + " "
    return text.ljust(width)


def _render_term_group(entries: list[_TermLine], indent: str) -> list[str]:
    """Render one column-aligned GROUP of terminal lines -- a VI's own
    boundary block, or one node's own in/out lines -- matching §16's golden
    whitespace: ``<indent><in |out>  <name (padded)>: <type (padded)><trailing>``.

    Column widths are computed PER GROUP (never globally across the whole
    VI -- confirmed empirically: ``loadTestsFromTestCase.vi``'s own boundary
    block aligns to width 20, while each subVI CALL's own terminal block
    aligns to its own, narrower width) -- AND, since this pass, only over
    the entries whose name/type fits under ``_LVNET_NAME_CAP``/
    ``_LVNET_TYPE_CAP`` (§14): ``max(length) + 1`` of just those, never the
    group's true global max. An entry over its cap doesn't stretch the
    column at all -- it renders via ``_lvnet_capped_pad``, which gives it
    exactly one space before the next column instead of the padded amount.
    The ``+1`` on an in-cap width keeps at least one space before the next
    column even when a name/type fills the whole (capped) field; confirmed
    against 3 of the golden's 4 aligned blocks (the fourth --
    ``TestSuite_Init.vi``'s ``out  TestSuite out: TestSuite.lvclass`` line,
    with ZERO gap before ``:`` -- is 1 space short of this same rule; likely
    a hand-transcription slip in the md's hand-written example, not a
    distinct rule, since that VI has no other line whose name reaches the
    group's own max length to cross-check against; see this module's Phase
    A investigation notes in the implementation report).
    """
    if not entries:
        return []
    under_cap_names = [len(e.name) for e in entries if len(e.name) <= _LVNET_NAME_CAP]
    name_width = (max(under_cap_names) if under_cap_names else 0) + 1
    needs_type_pad = any(e.trailing is not None for e in entries)
    type_width = 0
    if needs_type_pad:
        under_cap_types = [
            len(e.type) for e in entries if len(e.type) <= _LVNET_TYPE_CAP
        ]
        type_width = (max(under_cap_types) if under_cap_types else 0) + 1
    lines: list[str] = []
    for e in entries:
        name_part = _lvnet_capped_pad(e.name, name_width, _LVNET_NAME_CAP)
        if e.trailing is not None:
            type_part = _lvnet_capped_pad(e.type, type_width, _LVNET_TYPE_CAP)
            lines.append(f"{indent}{e.direction}  {name_part}: {type_part}{e.trailing}")
        else:
            lines.append(f"{indent}{e.direction}  {name_part}: {e.type}")
    return lines


def _lvnet_default_token(dv: DefaultValue) -> str:
    """The ``default <value>`` VALUE token (lvnet §4) for an unwired
    terminal -- reuses ``_type_default``/``_default_literal``'s already-
    resolved facts, never re-deriving a default. A type with a real literal
    default (``_default_literal`` returned something other than the honest
    ``"?"``) shows just that literal (``""``, ``0``, ``False``, ...) -- the
    ``: <Type>`` on the SAME line already names the type, so repeating it
    here (the OLD inline-annotation form's ``"0 (I32 default)"``, still used
    by ``_render_merge_source`` for a gamma/mu/eta merge) would be
    redundant. A type with NO literal default (a class/refnum reference --
    ``_default_literal`` fell through to ``"?"``) instead shows
    ``(default <Type>)`` -- the golden's ``(default TestSuite.lvclass)`` --
    naming what LabVIEW substitutes, since there is no bare literal to show.
    """
    if dv.literal == "?":
        return f"{_LVNET_DEFAULT_PAREN_PREFIX}{dv.type_descriptor})"
    return dv.literal


def _lvnet_default_trailing(dv: DefaultValue) -> str:
    """The ``default <value>`` TRAILING text for a terminal LINE specifically
    (lvnet §4, revised this pass) -- distinct from ``_lvnet_default_token``
    above, which is the DRIVE-POSITION form (``_render_lvnet_source``, e.g.
    ``case0::out2 = (default TestSuite.lvclass)``) and keeps naming the type
    there, since a drive position has no ``: <Type>`` column of its own to
    read it from. A terminal line ALREADY has that column -- so when the
    default has NO literal value (``dv.literal == "?"``, a class/refnum
    reference), this renders the bare word ``default`` alone (the type is
    right there on the same line); when it DOES have a literal, unchanged:
    ``default <literal>`` (``default ""``, ``default 0``).
    """
    if dv.literal == "?":
        return _LVNET_DEFAULT_KEYWORD
    return f"{_LVNET_DEFAULT_KEYWORD} {dv.literal}"


def _lvnet_type_label(type_str: str, lv_type: LVType | None) -> str:
    """The TERSE type label for a terminal line (lvnet §10/§11's terse
    mode): a NAMED enum/ring/cluster/typedef renders its bare name alone
    (``lveventtype``, ``LVPoint32TypeDef``) -- full member expansion is
    VERBOSE-only, and there is no verbose mode yet, so terse simply never
    expands one; an ANONYMOUS type (no name to fall back on) renders its
    full structural form, identical either way. Recurses into containers
    (an array's element, a parametrized refnum's element) via
    ``LVType.type_descriptor(expand_named=False)``, so ``[NamedThing]``/
    ``refnum{NamedThing}`` collapse the SAME way one level down.

    Falls back to the already-flattened ``type_str`` untouched when
    ``lv_type`` isn't available -- the Operation-based (non-``_gn``)
    builder's terminals never carry one (``render_lvnet`` only ever
    consumes ``build_netlist_from_graph``'s output, so this is a defensive
    fallback, never an expected path) -- and NEVER guesses a name from the
    string alone (the no-string-matching law).
    """
    if lv_type is None:
        return type_str
    return lv_type.type_descriptor(expand_named=False)


# ============================================================
# §10 lossless ``types :`` footnotes (verbose-only) -- the type-REHYDRATION
# counterpart to ``_lvnet_type_label``'s by-name terse/verbose-inline label.
# A NAMED enum/ring/cluster/typedef always renders by bare name inline
# (above); its FULL structure -- enum ordinals, cluster field TYPES (not
# just names), a refnum's inner type -- lives ONCE, here, in a bottom
# appendix. Primitives (even one wrapped in its own scalar ``.ctl``
# typedef) and class IDENTITIES never get an entry: a primitive has no
# structure beyond its own faithful token, and a class is identified by
# ``classname``, never ``typedef_name`` -- see ``_lvnet_named_stem``, the
# single source of truth both this section and ``netlist_signature``'s
# strengthened type comparison (``lvnet_parse.py``) key off, so the two can
# never disagree about what counts as "named".
# ============================================================


def _lvnet_named_stem(lv_type: LVType) -> str | None:
    """This type's own bare stripped display name IFF it is one of §10's
    NAMED kinds (enum/ring/cluster/typedef_ref) with a real ``typedef_name``
    -- the SAME check ``LVType.type_descriptor(expand_named=False)`` already
    applies to decide when to collapse to a bare name (mirrored here rather
    than re-derived from that method's return value, since a bare "cluster"
    fallback for an UNNAMED-but-fieldless cluster would otherwise be
    ambiguous with a genuinely named one).

    ``None`` for: an anonymous type; a PRIMITIVE (even one wrapped in its
    own scalar ``.ctl`` typedef -- §10: "primitives ... get no [types:]
    entry"); a CLASS identity (uses ``classname``, never ``typedef_name``);
    and an error cluster (``type_descriptor`` renders ``"Error"`` for one
    unconditionally, never falling through to its ``typedef_name`` either).
    """
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        if not lv_type.typedef_name:
            return None
        return _strip_typedef_stem(lv_type.typedef_name)
    if lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        if not lv_type.typedef_name or _is_error_cluster(lv_type):
            return None
        return _strip_typedef_stem(lv_type.typedef_name)
    return None


def _lvnet_type_ref(lv_type: LVType | None) -> str:
    """One type REFERENCE inside the §10 lossless grammar -- a cluster
    field's type, an array's element, a refnum's inner type: a NAMED type
    renders BY NAME alone (its own definition lives in its own ``types :``
    entry -- never re-inlined here, so every footnote stays FLAT, one entry
    per name, and a cyclic/self-referential named type can't recurse
    forever); an ANONYMOUS composite renders its own full structural
    definition recursively (``_lvnet_type_lossless_def`` -- nothing else
    faithful to show, mirroring ``type_descriptor``'s own "anonymous still
    expands" rule); ``None`` (no type resolved) is the honest ``"?"``.
    """
    if lv_type is None:
        return "?"
    name = _lvnet_named_stem(lv_type)
    if name is not None:
        return name
    return _lvnet_type_lossless_def(lv_type)


def _lvnet_type_lossless_def(lv_type: LVType) -> str:
    """The FULL lossless structural definition (§10's ``types :`` footnote
    grammar) for one ``LVType`` -- the type-REHYDRATION form, distinct from
    ``type_descriptor()`` (terse-faithful but intentionally lossy: no field
    types, no enum ordinals). Used both as a NAMED type's own top-level
    footnote entry (called directly on that type -- it never re-collapses
    to its own bare name) and, via ``_lvnet_type_ref``, recursively for an
    ANONYMOUS nested composite.

    - enum/ring: ``Enum{ m0 = 0, m1 = 1, ... }`` / ``Ring{ ... }``, ordinals
      explicit in ORDINAL order.
    - cluster/typedef_ref: ``Cluster{ f0 : <type-ref>, f1 : <type-ref>, ... }``
      -- each field's own faithful type via ``_lvnet_type_ref`` (by name if
      named, structural if anonymous, a scalar token otherwise).
    - array: ``[<type-ref>]``, nested once per ``dimensions``.
    - refnum: a class refnum shows its class name verbatim; a parametrized
      refnum shows ``<ref_type> refnum{ <type-ref> }``; otherwise
      ``<ref_type> refnum`` / ``"refnum"``.
    - any other primitive: its own faithful scalar token
      (``type_descriptor``'s scalar path -- unaffected by ``expand_named``).
    - class identity: its ``classname`` verbatim.

    Never fabricates: a cluster/typedef_ref with no ``fields`` loaded (e.g.
    an unresolved ``TYPEDEF_REF`` placeholder) or an enum/ring with no
    ``values`` loaded renders the honest ``Cluster{ ? }`` / ``Enum{ ? }``
    rather than guessing a member/field list.
    """
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        is_enum = lv_type.kind == LVTypeKind.ENUM
        open_token = _LVNET_ENUM_OPEN if is_enum else _LVNET_RING_OPEN
        if not lv_type.values:
            return f"{open_token} ? }}"
        members = sorted(lv_type.values.items(), key=lambda kv: kv[1].value)
        body = ", ".join(f"{name}{_LVNET_DRIVER_OP}{ev.value}" for name, ev in members)
        return f"{open_token} {body} }}"
    if lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        if not lv_type.fields:
            return f"{_LVNET_CLUSTER_OPEN} ? }}"
        body = ", ".join(
            f"{f.name}{_LVNET_TYPE_SEP}{_lvnet_type_ref(f.type)}"
            for f in lv_type.fields
        )
        return f"{_LVNET_CLUSTER_OPEN} {body} }}"
    if lv_type.kind == LVTypeKind.ARRAY:
        dims = lv_type.dimensions or 1
        inner = _lvnet_type_ref(lv_type.element_type) if lv_type.element_type else "?"
        return "[" * dims + inner + "]" * dims
    if lv_type.kind == LVTypeKind.PRIMITIVE:
        if lv_type.underlying_type == "Refnum":
            if lv_type.classname:
                return lv_type.classname
            if lv_type.ref_type:
                if lv_type.element_type is not None:
                    return (
                        f"{lv_type.ref_type} refnum{{ "
                        f"{_lvnet_type_ref(lv_type.element_type)} }}"
                    )
                return f"{lv_type.ref_type} refnum"
            return "refnum"
        return lv_type.type_descriptor(expand_named=False)
    if lv_type.kind == LVTypeKind.CLASS:
        return lv_type.classname or "?"
    return "?"


def _iter_lv_types_in_items(items: list[NetlistItem]) -> Iterator[LVType]:
    """Every structured ``LVType`` reachable from a body item's own
    terminals -- an INSTANCE's input/output terminal types (``NetlistScope``
    recurses into its frames'/loop's own body). A ``NetlistFeedback``/
    ``NetlistConstant`` carries no structured ``LVType`` (only an already-
    flattened label string), so neither yields anything here -- matching
    the ``types :`` footnote's documented scope (boundary, body terminals,
    dependency interfaces; never a constant's own type or a case/loop
    merge's synthesized type)."""
    for item in items:
        if isinstance(item, NetlistInstance):
            for b in item.inputs:
                if b.lv_type is not None:
                    yield b.lv_type
            for o in item.outputs:
                if o.lv_type is not None:
                    yield o.lv_type
        elif isinstance(item, NetlistScope):
            # Every scope kind's contents live in ``frames`` -- a loop's
            # single implicit body is ``frames[0].body`` (see
            # ``lvnet_parse._module_loop_scope_signature`` for the same
            # convention), a case/sequence/disabled/event scope has one
            # frame per branch/event.
            for frame in item.frames:
                yield from _iter_lv_types_in_items(frame.body)


def _iter_named_subtypes(
    lv_type: LVType, _visited: set[int] | None = None
) -> Iterator[tuple[str, LVType]]:
    """Every NAMED type (§10) reachable from ``lv_type`` -- itself (if
    named) plus every named type nested inside it (an array's element, a
    cluster's field, a parametrized refnum's inner type) -- regardless of
    whether ``lv_type`` ITSELF is named, so a named type nested inside an
    anonymous container is still found. ``_visited`` (keyed by ``id()``,
    not name) guards a genuinely self-referential ``LVType`` graph from
    infinite recursion; a real cycle already bottoms out at a ``Recursive``
    PRIMITIVE placeholder in practice (``type_mapping.py``), so this is a
    defensive belt only.
    """
    if _visited is None:
        _visited = set()
    if id(lv_type) in _visited:
        return
    _visited.add(id(lv_type))
    name = _lvnet_named_stem(lv_type)
    if name is not None:
        yield name, lv_type
    if lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        yield from _iter_named_subtypes(lv_type.element_type, _visited)
    elif (
        lv_type.kind == LVTypeKind.PRIMITIVE
        and lv_type.underlying_type == "Refnum"
        and lv_type.element_type is not None
    ):
        yield from _iter_named_subtypes(lv_type.element_type, _visited)
    elif (
        lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF) and lv_type.fields
    ):
        for f in lv_type.fields:
            if f.type is not None:
                yield from _iter_named_subtypes(f.type, _visited)


def _collect_lvnet_named_types(module: NetlistModule) -> dict[str, LVType]:
    """Every NAMED type (§10) reachable ANYWHERE in ``module`` -- the
    connector pane (covers both boundary in/out, positionally), every
    ``uses :`` ``subVI`` dependency's own inline interface, and every
    instance's own terminals throughout the body (recursing structures) --
    keyed by stripped display name (first-seen ``LVType`` wins on a name
    collision; the same typedef has the same structure everywhere it's
    referenced, so this never actually loses information in practice),
    sorted for a deterministic render/collection order.
    """
    seen: dict[str, LVType] = {}
    sources: list[LVType] = [
        t.lv_type for t in module.connector_pane.terminals if t.lv_type is not None
    ]
    for dep in module.dependencies:
        sources.extend(t.lv_type for t in dep.interface if t.lv_type is not None)
    sources.extend(_iter_lv_types_in_items(module.body))
    for lv_type in sources:
        for name, t in _iter_named_subtypes(lv_type):
            seen.setdefault(name, t)
    return dict(sorted(seen.items()))


def _render_lvnet_types(module: NetlistModule, lines: list[str]) -> None:
    """Render the §10 ``types :`` footnote section (verbose-only, a bottom
    appendix -- LAYOUT PROVISIONAL like ``uses :``, its own small function,
    trivial to move once the maintainer settles final placement): one
    ``<Name> = <lossless-def>[ ; ./path]`` line per NAMED type reachable
    anywhere in the module, sorted by name. Omitted entirely (no lines
    appended) when the VI has no named types -- never an empty header.
    """
    named = _collect_lvnet_named_types(module)
    if not named:
        return
    lines.append("")
    lines.append(_TYPES_HEADER_LINE)
    for name, lv_type in named.items():
        body = _lvnet_type_lossless_def(lv_type)
        path = (
            f"{_LVNET_ANNOTATION_SEP}{_LVNET_TYPEDEF_NAV_PREFIX}{lv_type.typedef_path}"
            if lv_type.typedef_path
            else ""
        )
        lines.append(f"    {name}{_LVNET_DRIVER_OP}{body}{path}")


def _lvnet_ambiguous_named_types(module: NetlistModule) -> frozenset[str]:
    """Every §10 NAMED type name that resolves to MORE THAN ONE DISTINCT
    structure somewhere in ``module`` -- the SAME nominal typedef (e.g. a
    User Event's Variant-typed data field) genuinely carrying a different
    concrete structure at different call sites, observed on a real corpus
    VI (``WaveGen.vi``'s ``Event Data.ctl``, whose ``Value`` field resolves
    to ``Gen Action`` at one occurrence and ``Gen Params`` at another).

    The ``types :`` footnote still emits exactly ONE entry per name (§10's
    decided "flat, one entry per name" rule -- see ``_collect_lvnet_named_
    types``, which keeps the first-seen occurrence) -- so for one of these
    names, that single footnote entry is NECESSARILY unfaithful to at
    least one occurrence. ``netlist_signature``'s strengthened type
    comparison excludes exactly this set from full structural resolution
    (falls back to comparing by bare name, same as before this pass, for
    every terminal typed with one of these names) -- claiming full
    structural equality for an ambiguous name would be unverifiable from
    the flat one-entry-per-name text, never fabricated as a round-trip
    proof it can't actually make.
    """
    seen_defs: dict[str, str] = {}
    ambiguous: set[str] = set()
    sources: list[LVType] = [
        t.lv_type for t in module.connector_pane.terminals if t.lv_type is not None
    ]
    for dep in module.dependencies:
        sources.extend(t.lv_type for t in dep.interface if t.lv_type is not None)
    sources.extend(_iter_lv_types_in_items(module.body))
    for lv_type in sources:
        for name, t in _iter_named_subtypes(lv_type):
            def_text = _lvnet_type_lossless_def(t)
            prior = seen_defs.get(name)
            if prior is None:
                seen_defs[name] = def_text
            elif prior != def_text:
                ambiguous.add(name)
    return frozenset(ambiguous)


def _lv_type_comparison_shape(
    lv_type: LVType | None,
    seen: frozenset[str] = frozenset(),
    *,
    full: bool = False,
    ambiguous: frozenset[str] = frozenset(),
) -> tuple:
    """The canonical, comparable projection of one type reference for
    ``lvnet_parse.netlist_signature``'s STRENGTHENED type check (§10's
    lossless ``types :`` footnotes) -- the MODULE-side half; the PARSED-side
    counterpart is ``lvnet_parse._parsed_type_ref_shape``, built
    independently from TEXT but producing the identical tuple shape so the
    two are directly comparable.

    Below a NAMED enum/ring/cluster (not yet reached, ``full=False``): an
    array/refnum wrapper still decomposes (``("array", dims, inner)`` /
    ``("refnum", ref_type, inner)`` -- this is NOT new depth, it mirrors
    ``type_descriptor``'s own pre-existing recursion into those two
    containers); anything else is an opaque ``("leaf", <the SAME
    ``type_descriptor(expand_named=False)`` string ``_lvnet_type_label``
    already renders>)`` -- i.e. "compares by its inline structural form as
    today" for anything with no footnote to rehydrate from.

    The MOMENT a named enum/ring/cluster is reached (top-level, or nested
    through an array/refnum wrapper) -- UNLESS its name is in ``ambiguous``
    (``_lvnet_ambiguous_named_types``: the SAME name genuinely resolves to
    different structures at different occurrences elsewhere in the module,
    so the single ``types :`` footnote entry can't be trusted for this
    particular one -- treated as if unnamed, an opaque leaf) -- ``full``
    flips ``True`` and STAYS true for the rest of that subtree: every
    enum/cluster reached from there on (named or anonymous) decomposes
    FULLY -- ordinals, field types -- matching exactly what
    ``_lvnet_type_lossless_def``/``_lvnet_type_ref`` render into that
    type's footnote text (an anonymous nested composite is fully expanded
    there too, never left opaque, since there's nothing else faithful to
    show once the wrapper commits to structural detail).

    ``seen`` cycle-guards a genuinely self-referential named type: re-
    entering an in-progress name renders ``("named", name)`` instead of
    recursing again -- both sides apply the identical rule, so a real cycle
    still compares equal rather than diverging or infinite-looping.
    """
    if lv_type is None:
        return ("leaf", "?")
    if lv_type.kind == LVTypeKind.ARRAY:
        dims = lv_type.dimensions or 1
        return (
            "array",
            dims,
            _lv_type_comparison_shape(
                lv_type.element_type, seen, full=full, ambiguous=ambiguous
            ),
        )
    if (
        lv_type.kind == LVTypeKind.PRIMITIVE
        and lv_type.underlying_type == "Refnum"
        and lv_type.classname is None
        and lv_type.ref_type is not None
        and lv_type.element_type is not None
    ):
        return (
            "refnum",
            lv_type.ref_type,
            _lv_type_comparison_shape(
                lv_type.element_type, seen, full=full, ambiguous=ambiguous
            ),
        )

    name = _lvnet_named_stem(lv_type)
    if name is not None and name in ambiguous:
        name = None
    if name is not None:
        if name in seen:
            return ("named", name)
        seen = seen | {name}
        full = True
    elif not full:
        return ("leaf", lv_type.type_descriptor(expand_named=False))

    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        members = tuple(
            sorted(
                ((n, ev.value) for n, ev in (lv_type.values or {}).items()),
                key=lambda kv: kv[1],
            )
        )
        return ("enum" if lv_type.kind == LVTypeKind.ENUM else "ring", members)
    if lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        if lv_type.fields:
            fields = tuple(
                (
                    f.name,
                    _lv_type_comparison_shape(
                        f.type, seen, full=True, ambiguous=ambiguous
                    ),
                )
                for f in lv_type.fields
            )
            return ("cluster", fields)
        return ("cluster", None)
    # PRIMITIVE / CLASS reached while full=True (e.g. a named cluster's own
    # field typed as a scalar or a class) -- still just its own faithful
    # label, the SAME as ``_lvnet_type_label`` already renders.
    return ("leaf", lv_type.type_descriptor(expand_named=False))


def _lvnet_handle_base(name: str) -> str:
    """The un-suffixed base of an instance/constant's HANDLE (lvnet §7): the
    display name with a trailing ``.vi``/``.ctl`` file extension stripped,
    then spaces replaced by ``_``. The uniquifying ``_N`` suffix is assigned
    separately, over ALL instances/constants VI-wide (see
    ``_assign_lvnet_handles``), so two different display names that collide
    only AFTER this transform (e.g. ``"Foo Bar.vi"`` and literal ``"Foo_Bar"``)
    still land in one shared numbering group and get distinct ``_N``s.
    """
    base = name
    for ext in (".vi", ".ctl"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    return base.replace(" ", "_")


@dataclass(frozen=True)
class _LvnetHandles:
    """The ONE handle map ``render_lvnet`` builds once per module (via
    ``_assign_lvnet_handles``) and threads through every ``_render_lvnet_*``
    helper -- lvnet §7's "the handle at a node's DECLARATION must be
    identical to the handle used in every net that references that node".

    ``by_uid`` serves a declaration line directly (``NetlistInstance.uid`` /
    ``NetlistConstant.uid``) and a labeled-constant net reference
    (``NetRef.constant_uid`` -- the SAME id, see ``NetlistConstant``'s
    docstring); a ``constant_uid`` NOT present here is a one-off/unlabeled
    constant, rendered as its inlined literal instead (see
    ``_render_lvnet_source``). ``by_name_occurrence`` serves a node-terminal
    ``NetRef``: ``(node, occurrence)`` is the only identity such a reference
    carries back to its producing instance (it has no ``uid``), and -- for a
    given display name -- ``occurrence`` is already a VI-wide-unique
    disambiguator (``_assign_occurrences_gn``), so the pair reliably resolves
    to exactly one instance.
    """

    by_uid: dict[str, str]
    by_name_occurrence: dict[tuple[str, int | None], str]


def _collect_lvnet_handle_targets(
    items: list[NetlistItem],
) -> list[tuple[str, str, str | None, int | None]]:
    """Every PRODUCING instance and every ``constant`` body item under
    ``items``, in body-VISITATION order -- the same document order
    ``_render_lvnet_items`` walks (recursing into a scope's frames in order)
    -- since that's the deterministic order ``_assign_lvnet_handles`` assigns
    ``_N`` suffixes in. §7 (revised): "Every producing node gets a handle,
    CLOSED or OPEN" -- so every ``NetlistInstanceKind`` gets one here EXCEPT
    ``LOCAL_VARIABLE`` (§7 keeps that one "a terminal, not a node", tap-
    resolution still undesigned, so it never declares itself at all -- see
    ``_render_lvnet_instance``). A ``NetlistFeedback`` is NOT collected here:
    its handle IS its own ``net`` string (already a globally-unique ``fbK``,
    assigned elsewhere) -- see ``_render_lvnet_items``'s own ``NetlistFeedback``
    case, which needs no map lookup at all.

    Each entry is ``(uid, base, name_key, occurrence)``: ``name_key`` is the
    instance's raw display ``name`` (for the ``by_name_occurrence`` map), or
    ``None`` for a constant (a constant net reference resolves ONLY via its
    ``constant_uid``, never ``(node, occurrence)`` -- see ``NetRef``).
    """
    found: list[tuple[str, str, str | None, int | None]] = []
    for item in items:
        match item:
            case NetlistInstance():
                if item.kind != NetlistInstanceKind.LOCAL_VARIABLE:
                    base = _lvnet_handle_base(item.name)
                    found.append((item.uid, base, item.name, item.occurrence))
            case NetlistConstant():
                base = _lvnet_handle_base(item.name)
                found.append((item.uid, base, None, None))
            case NetlistScope():
                for frame in item.frames:
                    found.extend(_collect_lvnet_handle_targets(frame.body))
            case NetlistFeedback():
                pass
    return found


def _assign_lvnet_handles(module: NetlistModule) -> _LvnetHandles:
    """Build the ONE handle map for this module (lvnet §7/§9): every CLOSED
    instance/constant, in deterministic body-visitation order (node/graph
    order -- ``_collect_lvnet_handle_targets`` walks the SAME list order
    ``_render_lvnet_items`` renders, so there are no ties left to break; the
    ``uid`` carried alongside each entry is the tie-break of last resort were
    that visitation order ever to repeat an item), grouped by its
    strip-extension+despace BASE name (``_lvnet_handle_base``) -- never by
    the raw display name -- and suffixed ``_N`` from 1 within each group, the
    first copy included. Two instances whose raw names differ but collide
    after the base transform (e.g. ``"Foo.vi"`` and ``"Foo.ctl"`` -> both
    ``"Foo"``) land in the SAME group and get distinct ``_N``s, exactly like
    two calls to the identical VI. (The visitation list itself has no ties to
    break -- each entry is one list position -- so no secondary ``uid`` sort
    is needed; ``uid`` is carried on every entry regardless, as the stable
    per-instance identity a tie-break would use if the walk order were ever
    not already total.)
    """
    targets = _collect_lvnet_handle_targets(module.body)
    counts: dict[str, int] = {}
    by_uid: dict[str, str] = {}
    by_name_occurrence: dict[tuple[str, int | None], str] = {}
    for uid, base, name_key, occurrence in targets:
        counts[base] = counts.get(base, 0) + 1
        handle = f"{base}_{counts[base]}"
        by_uid[uid] = handle
        if name_key is not None:
            by_name_occurrence[(name_key, occurrence)] = handle
    return _LvnetHandles(by_uid=by_uid, by_name_occurrence=by_name_occurrence)


def _lvnet_net_separator(bare: str) -> str:
    """Reformat a structure-scoped net name's separator from the model's
    stored ``.`` to lvnet's ``::`` (§9) -- a RENDER-TIME-ONLY transform of a
    string this same module deterministically constructs in exactly this
    shape (``_tunnel_net_name_gn``/``_mu_net_name_gn``); the model's own
    stored string is never mutated, so ``render_netlist``/``netlist_to_dict``
    (which must keep ``.``) are untouched. A boundary control's bare name or
    an ``fbK`` feedback net (neither ever contains this shape) pass through
    unchanged.
    """
    m = _LVNET_STRUCTURE_NET_RE.match(bare)
    if m is None:
        return bare
    prefix, rest = m.groups()
    return f"{prefix}{_LVNET_TERMINAL_SEP}{rest}"


def _render_lvnet_source(source: NetRef | DefaultValue, handles: _LvnetHandles) -> str:
    """The VALUE half of a ``= <driver>`` / bare merge-source reference
    (lvnet §4/§9).

    - An unwired terminal's ``DefaultValue`` renders via
      ``_lvnet_default_token``.
    - A reference that traces to a LABELED constant (``NetRef.constant_uid``
      resolves in ``handles.by_uid``) renders that constant's own ``<handle>``
      (e.g. ``GUID_1``) -- lvnet §7's "a shared/named constant becomes a
      constant node referenced by net". A constant_uid that does NOT resolve
      is a one-off/unlabeled constant -- falls through to its inlined literal
      value below (``source.lvnet_value``, the lvnet-escaped text, since
      ``source.node`` is ``None`` for every constant reference).
    - A node-terminal reference (``source.node is not None``) ALWAYS renders
      fully qualified as ``<handle>::<terminal>`` (never a bare, unqualified
      form -- unlike ``render_netlist``'s ambiguity-gated qualification;
      lvnet §9 names every node-terminal net this one way).
    - Everything else (``source.node is None``, no constant) is a boundary
      control's plain name or a structure-scoped net (``caseN.outK``/
      ``loopN.shiftK``/``loopN.outK``/``fbK``) -- ``_lvnet_net_separator``
      reformats only the latter; ``source.lvnet_value`` is ``None`` here, so
      the fallback is ``source.bare`` unchanged.
    """
    if isinstance(source, DefaultValue):
        return _lvnet_default_token(source)
    if source.constant_uid is not None:
        handle = handles.by_uid.get(source.constant_uid)
        if handle is not None:
            return handle
    if source.node is not None:
        handle = handles.by_name_occurrence.get((source.node, source.occurrence))
        if handle is not None:
            return f"{handle}{_LVNET_TERMINAL_SEP}{source.terminal}"
        # The producer is a Local/Global Variable's own control/indicator --
        # the ONE instance kind still excluded from the handle map (§7: "a
        # terminal, not a node"; its tap-resolution-to-the-control's-net is
        # still undesigned, §17 item 6), so there is no designed identity to
        # reuse here. Falling back to the raw display name (with the SAME
        # ``::`` terminal separator lvnet §9 mandates) keeps the render from
        # crashing without fabricating a handle scheme the spec never
        # designed for this one remaining kind -- flagged as an OPEN gap.
        return f"{source.node}{_LVNET_TERMINAL_SEP}{source.terminal}"
    # An inlined (unlabeled) constant's literal value: ``lvnet_value`` is
    # the lvnet-escaped text (md §4/§10) -- ``_lvnet_net_separator`` is a
    # no-op on it (a quoted/``True``/``False``/numeric token never matches
    # the ``caseN.outK``-shaped structure-net regex), so routing it through
    # unconditionally is safe and keeps one call site for every ``bare``
    # shape (net name, structure net, or literal).
    literal = source.lvnet_value if source.lvnet_value is not None else source.bare
    return _lvnet_net_separator(literal)


def _is_void_type(type_descriptor: str) -> bool:
    """The stored-``type``-string counterpart of ``_is_real_terminal`` --
    used by ``render_lvnet`` (the one consumer that must drop a dead pane
    slot) directly off ``NetlistTerminalBinding.type``/``NetlistOutput.type``,
    since Phase A's STORED model keeps every terminal Void-included (to
    match the Operation-based builder 1:1 -- see ``_build_instance_gn``)."""
    return type_descriptor == "Void"


def _lvnet_component(instance: NetlistInstance) -> str:
    """The ``<component>`` half of a declaration line (§3/§7) -- the
    faithful identity spelled ONLY at the declaration, never repeated on a
    net reference. Per §7's table: a subVI's fully-qualified
    ``qualified_name``; a Property Node's target object class
    (``object_name``, e.g. ``Bool``, ``Tree (strict)``); an Invoke Node's
    ``<ObjectClass>.<Method>`` (the method IS the node's identity, since
    LabVIEW stores no per-call param names to distinguish it otherwise);
    everything else (``function``/``in-place-element``/``formula-node``) --
    §7's table gives no OTHER identity to spell for these, so the node's own
    display ``name`` is used, exactly like a primitive's LabVIEW name.
    """
    if instance.kind == NetlistInstanceKind.SUBVI:
        return instance.qualified_name or instance.name
    if instance.kind == NetlistInstanceKind.PROPERTY_NODE:
        return instance.object_name or "?"
    if instance.kind == NetlistInstanceKind.INVOKE_NODE:
        return f"{instance.object_name or '?'}.{instance.method_name or '?'}"
    return instance.name


def _render_lvnet_instance(
    instance: NetlistInstance,
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    """One ``<keyword> <handle> : <component>`` node (§3/§7) -- EVERY
    instance kind except Local/Global Variable, which stays a bare
    keyword + placeholder (``_LOCAL_VARIABLE_TODO``): §7 keeps that one "a
    terminal, not a node", so it never declares itself or gets a handle.

    ``<handle>`` (left of ``:``) is OUR label -- looked up in
    ``handles.by_uid``, built once for the whole module by
    ``_assign_lvnet_handles`` so it is IDENTICAL to the handle every net
    reference to this instance resolves to (§7's declaration/reference
    identity rule -- now true of every declaring kind, CLOSED or OPEN).
    ``<component>`` (right of ``:``) is computed per kind by
    ``_lvnet_component``. The ``; ./path`` nav annotation is OMITTED on a
    subVI header -- ``NetlistInstance`` carries no path field to source it
    from, and §7 forbids fabricating one ("if not, omit it and note that").

    Property Node / Invoke Node terminals need NO special-case code here:
    the model already names a property's value terminal by the property
    (stamped at load, ``_component_terminal_name``) and an Invoke Node's
    parameter terminals by their raw index (LabVIEW stores no param names)
    -- the SAME generic terminal-rendering loop below (shared with
    subVI/function) already reads ``b.terminal``/``o.net.terminal`` faithfully
    either way. In-place-element/formula-node render that same terminal
    block, THEN one trailing ``# TODO(lvnet): ...`` for their one remaining
    undesigned part (``_OPEN_INSTANCE_TRAILING_TODO``) -- never more.
    """
    if instance.kind == NetlistInstanceKind.LOCAL_VARIABLE:
        lines.append(f"{indent}{instance.kind.value}")
        lines.append(f"{indent + _LVNET_INDENT}# TODO(lvnet): {_LOCAL_VARIABLE_TODO}")
        return

    header_kw = _LVNET_INSTANCE_KEYWORDS[instance.kind]
    handle = handles.by_uid[instance.uid]
    component = _lvnet_component(instance)
    lines.append(f"{indent}{header_kw} {handle}{_LVNET_TYPE_SEP}{component}")

    entries: list[_TermLine] = []
    for b in sorted(instance.inputs, key=lambda b: b.pane_rank):
        if _is_void_type(b.type):
            continue
        if b.net is not None:
            net_str = _render_lvnet_source(b.net, handles)
            trailing = f"= {net_str}"
            # A Boolean input wired through inversion -- lvnet §6's
            # ``; inverted`` trailing annotation (never the OLD renderer's
            # ``not(...)`` wrapper, which is NOT part of the lvnet grammar).
            if b.inverted:
                trailing += f"{_LVNET_ANNOTATION_SEP}inverted"
        else:
            assert b.default is not None
            trailing = _lvnet_default_trailing(b.default)
        type_label = _lvnet_type_label(b.type, b.lv_type)
        entries.append(_TermLine("in ", b.terminal, type_label, trailing))
    for o in sorted(instance.outputs, key=lambda o: o.pane_rank):
        if _is_void_type(o.type):
            continue
        type_label = _lvnet_type_label(o.type, o.lv_type)
        entries.append(_TermLine("out", o.net.terminal, type_label, None))
    lines.extend(_render_term_group(entries, indent + _LVNET_INDENT))

    trailing_todo = _OPEN_INSTANCE_TRAILING_TODO.get(instance.kind)
    if trailing_todo is not None:
        lines.append(f"{indent + _LVNET_INDENT}# TODO(lvnet): {trailing_todo}")


def _render_lvnet_constant(
    const: NetlistConstant, indent: str, lines: list[str], handles: _LvnetHandles
) -> None:
    """``constant <handle> : <Type> = <value>`` (lvnet §7) -- a single line,
    no column alignment (unlike a node's own in/out block; the golden shows
    exactly one, so there's no evidence a peer group of constants aligns
    with each other). ``<handle>`` (§7/§9's ``_N`` suffix, e.g. ``GUID_1``)
    replaces the OLD ``#N`` occurrence tag. Uses ``const.lvnet_value``
    (lvnet-escaped, md §4/§10) rather than ``const.value`` (the OLD
    ``render_netlist``/``netlist_to_dict``-parity text, unescaped)."""
    handle = handles.by_uid[const.uid]
    lines.append(
        f"{indent}constant {handle}{_LVNET_TYPE_SEP}{const.type}"
        f"{_LVNET_DRIVER_OP}{const.lvnet_value}"
    )


def _render_lvnet_loop_scope(
    scope: NetlistScope,
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    """``for-loop :`` / ``while-loop :`` (§8) -- a single implicit body,
    followed by its border constructs (``shift-register``/``tunnel``, §8) at
    the SAME indent as the body's own items (the golden shows
    ``shift-register loop0::shift0 :`` as a sibling of ``subVI
    TestCase_Init_1``, not nested deeper).

    A while-loop's stop-condition net (``scope.selector``) is NOT rendered
    -- §8's own syntax table shows bare ``while-loop :`` with no selector
    annotation, and no other CLOSED construct documents where that net
    would go; see the implementation report's open-items list.

    ``merge.net`` (``MuMerge``/``EtaMerge``) is the model's OWN stored
    string (shared verbatim with ``render_netlist``, which must keep its
    ``.`` separator) -- ``_lvnet_net_separator`` reformats it to ``::`` for
    THIS render only, never mutating the stored field (§9).
    """
    header_kw = "while-loop" if scope.kind == "while" else "for-loop"
    lines.append(f"{indent}{header_kw}{_LVNET_BLOCK_OPEN}")
    body_indent = indent + _LVNET_INDENT
    _render_lvnet_items(scope.frames[0].body, body_indent, lines, handles)
    for merge in scope.outputs:
        if isinstance(merge, MuMerge):
            net = _lvnet_net_separator(merge.net)
            lines.append(f"{body_indent}shift-register {net}{_LVNET_BLOCK_OPEN}")
            kv_indent = body_indent + _LVNET_INDENT
            init_str = _render_lvnet_source(merge.init, handles)
            lines.append(f"{kv_indent}init{_LVNET_DRIVER_OP}{init_str}")
            if merge.recur is not None:
                recur_str = _render_lvnet_source(merge.recur, handles)
                lines.append(f"{kv_indent}each{_LVNET_DRIVER_OP}{recur_str}")
        elif isinstance(merge, EtaMerge):
            mode_word = _LVNET_TUNNEL_MODE_WORD.get(merge.index_mode, merge.index_mode)
            # The Conditional modifier's exact appended form is only HINTED
            # at in §8 ("[+ conditional]"), never pinned by a worked
            # example -- this is the most literal reading of that hint
            # (not the OLD renderer's own "+cond" abbreviation, which is a
            # DIFFERENT, non-lvnet convention). Flagged in the report.
            if merge.conditional:
                mode_word += "+conditional"
            value_str = _render_lvnet_source(merge.value, handles)
            net = _lvnet_net_separator(merge.net)
            lines.append(
                f"{body_indent}tunnel {net}{_LVNET_TYPE_SEP}{mode_word}"
                f"{_LVNET_DRIVER_OP}{value_str}"
            )


def _render_lvnet_case_scope(
    scope: NetlistScope,
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    """``case <selector-net> :`` (§8) -- ``frame "<value>" :`` per case, each
    followed (at the SAME indent as its own body items -- the golden shows
    ``case0::out0 = ...`` as a sibling of ``subVI TestSuite_Init_1``, not
    nested deeper) by that frame's contribution to every case-output tunnel,
    REDISTRIBUTED from the ``GammaMerge``/``GammaCase`` model built once per
    scope into per-frame ``caseN::outK = <source>`` lines (§8's "each frame
    declares what it drives onto the structure's output nets, INSIDE the
    frame" -- never the OLD renderer's single bottom-of-scope ``gamma(...)``
    line). ``gamma.net`` is reformatted to ``::`` the same render-time-only
    way as the loop scope's ``merge.net`` above.
    """
    sel_str = (
        _render_lvnet_source(scope.selector, handles)
        if scope.selector is not None
        else "?"
    )
    lines.append(f"{indent}case {sel_str}{_LVNET_BLOCK_OPEN}")
    gammas = [m for m in scope.outputs if isinstance(m, GammaMerge)]
    body_indent = indent + _LVNET_INDENT * 2
    for frame in scope.frames:
        label = _quoted_frame_label(frame.label)
        lines.append(f"{indent + _LVNET_INDENT}frame {label}{_LVNET_BLOCK_OPEN}")
        _render_lvnet_items(frame.body, body_indent, lines, handles)
        frame_key = "default" if frame.is_default else frame.label
        for gamma in gammas:
            case_entry = next(
                (c for c in gamma.cases if c.frame_key == frame_key), None
            )
            if case_entry is None:
                continue
            source_str = _render_lvnet_source(case_entry.source, handles)
            net = _lvnet_net_separator(gamma.net)
            lines.append(f"{body_indent}{net}{_LVNET_DRIVER_OP}{source_str}")


def _render_lvnet_sequence_scope(
    scope: NetlistScope,
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    """``flat-sequence :`` / ``stacked-sequence :`` (§8), ``frame [i] :`` per
    frame -- picked from ``scope.sequence_is_flat``, the EXPLICIT
    flat-vs-stacked discriminator surfaced from the parser's own XML class
    (``SequenceNode.is_flat`` / ``SequenceOperation.is_flat``), never
    inferred from the ambiguous ``displayed_frame`` proxy (None for both a
    flat sequence and an out-of-range legacy stacked one)."""
    keyword = "flat-sequence" if scope.sequence_is_flat else "stacked-sequence"
    lines.append(f"{indent}{keyword}{_LVNET_BLOCK_OPEN}")
    body_indent = indent + _LVNET_INDENT
    for frame in scope.frames:
        lines.append(f"{body_indent}frame [{frame.value}]{_LVNET_BLOCK_OPEN}")
        _render_lvnet_items(frame.body, body_indent + _LVNET_INDENT, lines, handles)


def _render_lvnet_disabled_scope(
    scope: NetlistScope,
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    """``diagram-disable :`` / ``conditional-disable :`` / ``type-
    specialization :`` (§8) -- picked from ``scope.disable_kind``, sourced
    from ``DisableStructureNode.kind`` / ``DisableStructureOperation
    .disable_kind`` (never a hard-coded default). Frame-label quoting also
    follows §8's own table: a Diagram Disable frame (``Enabled``/
    ``Disabled``) and a Type Specialization frame (``[i]``) render BARE (no
    quotes -- ``_frame_labels`` in parser/nodes/disable.py already produces
    those exact tokens); a Conditional Disable frame's decoded symbol
    condition (``SYMBOL==VALUE`` / ``Default``) is quoted, matching §8's
    ``frame "<symbol cond>" :``."""
    keyword = _LVNET_DISABLE_KEYWORD[scope.disable_kind]
    lines.append(f"{indent}{keyword}{_LVNET_BLOCK_OPEN}")
    body_indent = indent + _LVNET_INDENT
    quote_labels = scope.disable_kind is DisableStructureKind.CONDITIONAL
    for frame in scope.frames:
        label = _quoted_frame_label(frame.label) if quote_labels else frame.label
        lines.append(f"{body_indent}frame {label}{_LVNET_BLOCK_OPEN}")
        _render_lvnet_items(frame.body, body_indent + _LVNET_INDENT, lines, handles)


def _render_lvnet_event_scope(
    scope: NetlistScope,
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    """``event-structure :`` (§8), ``frame "<event>" :`` per event case."""
    lines.append(f"{indent}event-structure{_LVNET_BLOCK_OPEN}")
    body_indent = indent + _LVNET_INDENT
    for frame in scope.frames:
        label = _quoted_frame_label(frame.label)
        lines.append(f"{body_indent}frame {label}{_LVNET_BLOCK_OPEN}")
        _render_lvnet_items(frame.body, body_indent + _LVNET_INDENT, lines, handles)


def _render_lvnet_scope(
    scope: NetlistScope,
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    if scope.kind == "case":
        _render_lvnet_case_scope(scope, indent, lines, handles)
    elif scope.kind in ("for", "while"):
        _render_lvnet_loop_scope(scope, indent, lines, handles)
    elif scope.kind == "sequence":
        _render_lvnet_sequence_scope(scope, indent, lines, handles)
    elif scope.kind == "disabled":
        _render_lvnet_disabled_scope(scope, indent, lines, handles)
    elif scope.kind == "event":
        _render_lvnet_event_scope(scope, indent, lines, handles)


def _render_lvnet_feedback(
    feedback: NetlistFeedback, indent: str, lines: list[str], handles: _LvnetHandles
) -> None:
    """``feedback-node <handle> (<N> iteration[s]) :`` (§7, now designed) --
    the SAME ``init``/``each`` shape as a loop's own ``shift-register`` border
    construct (§8), since a Feedback Node is Gated-SSA's classic mu exactly
    like a shift register (see ``NetlistFeedback``'s own docstring).

    The handle IS the Feedback Node's own ``net`` (e.g. ``fb0``) -- already
    a globally-unique id assigned elsewhere (``_assign_sequential_ids_gn``),
    so unlike every other kind this needs NO lookup in ``_LvnetHandles`` at
    all: every downstream reference to this net already resolves via the
    bare ``fbK`` string directly (``_resolve_source_gn`` builds it with
    ``node=None``, so ``_render_lvnet_source`` takes the plain
    ``_lvnet_net_separator`` path, which is a no-op here -- ``fbK`` has no
    ``.`` to reformat).

    A Feedback Node is a state REGISTER, not a computation, so (like a
    ``shift-register``) it has NO more-specific-type after the keyword -- the
    ``:`` just opens its ``init``/``each`` block. Its one setting, the number of
    ITERATIONS it hands the value back across (``feedbackNodeDelay``; "delay"
    would read as a time, so we name the unit -- ``(1 iteration)`` /
    ``(3 iterations)``), rides as a parenthetical ATTRIBUTE. LabVIEW enforces a
    delay >= 1, so a ``None`` here means the depth was not parsed, not zero --
    rendered ``(? iterations)`` (the file's established "genuinely unknown"
    ``?``), never a fabricated count. ``each`` is omitted when ``recur`` is
    ``None`` (never written to -- a real, faithful state per the model's own
    docstring), mirroring ``shift-register``'s optional ``each`` line.
    """
    if feedback.delay is None:
        attr = "? iterations"
    else:
        attr = f"{feedback.delay} iteration" + ("" if feedback.delay == 1 else "s")
    lines.append(f"{indent}feedback-node {feedback.net} ({attr}){_LVNET_BLOCK_OPEN}")
    init_str = _render_lvnet_source(feedback.init, handles)
    lines.append(f"{indent + _LVNET_INDENT}init{_LVNET_DRIVER_OP}{init_str}")
    if feedback.recur is not None:
        recur_str = _render_lvnet_source(feedback.recur, handles)
        lines.append(f"{indent + _LVNET_INDENT}each{_LVNET_DRIVER_OP}{recur_str}")


def _render_lvnet_items(
    items: list[NetlistItem],
    indent: str,
    lines: list[str],
    handles: _LvnetHandles,
) -> None:
    for item in items:
        match item:
            case NetlistInstance():
                _render_lvnet_instance(item, indent, lines, handles)
            case NetlistScope():
                _render_lvnet_scope(item, indent, lines, handles)
            case NetlistConstant():
                _render_lvnet_constant(item, indent, lines, handles)
            case NetlistFeedback():
                _render_lvnet_feedback(item, indent, lines, handles)


def _lvnet_requirement_trailing(term: ConnectorPaneTerminal) -> str | None:
    """The §5 bare requirement keyword (``required``/``recommended``/
    ``optional``) for a boundary terminal line, verbose-only. ``None`` for
    an UNKNOWN (unresolved) wiring rule -- §5: terse omits the keyword, and
    an unresolved rule has no keyword to show even in verbose, so it
    renders exactly like terse there.
    """
    if term.wiring_requirement == WiringRequirement.UNKNOWN:
        return None
    return term.wiring_requirement.value


def _lvnet_literal_token(value: ScalarValue) -> str:
    """THE single lvnet §4/§10 literal-value TOKEN renderer for a raw
    ``ScalarValue`` -- a connector-pane control's own authored default
    (``ConnectorPaneTerminal.default``), a wired-constant driver, or a
    labeled ``constant`` node's own value (see ``_lvnet_const_value_str``,
    which feeds the last two through this for their plain-scalar case).
    Replaces the old ``_lvnet_scalar_value_token``, which quoted a string
    with NO escaping at all -- the bug this closes (md §4/§10/§17 item 5's
    "scalar string escaping" note): a raw control char (a CRLF, a bare
    ``"``) rendered VERBATIM inside the quotes, so the literal's own text
    could span multiple physical lines and break ``parse_lvnet``'s line-
    oriented grammar (the ``Graphical Test Runner - Main UI - .vi`` xfail).

    A ``str`` renders double-quoted with standard backslash escapes
    (``\\\\``, ``\\"``, ``\\n``, ``\\r``, ``\\t`` -- ``_LVNET_STRING_ESCAPES``);
    any OTHER C0 control char (U+0000-U+001F) as ``\\xHH``. A ``bool`` checks
    FIRST (Python's ``bool`` is an ``int`` subclass) -> ``True``/``False``;
    ``int``/``float`` -> plain ``str()``. Never called with ``None``.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        out: list[str] = []
        for ch in value:
            escaped = _LVNET_STRING_ESCAPES.get(ch)
            if escaped is not None:
                out.append(escaped)
            elif ord(ch) < 0x20:
                out.append(f"\\x{ord(ch):02X}")
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'
    return str(value)


def _lvnet_const_value_str(c: Constant) -> str:
    """``render_lvnet``-ONLY sibling of ``op_walk._const_value_str`` (kept
    byte-parity, untouched, for ``render_netlist``/``netlist_to_dict``): the
    SAME error-cluster and numeric-display-format special cases (a radix/
    precision string is already correct display text, never re-escaped),
    but the plain-SCALAR fallthrough (``lv_type.kind is PRIMITIVE`` -- a
    real numeric/string/boolean value, no error cluster, no matching
    display format) routes through ``_lvnet_literal_token`` instead of a
    bare ``str()``, so a plain String constant's real value escapes for
    lvnet's grammar (md §4/§10).

    A CLUSTER/ARRAY/ENUM/etc. constant's ``.value`` is ALREADY a pre-
    stringified display text (e.g. a Python-dict-repr-shaped string for a
    cluster) -- not the scalar content ``_lvnet_literal_token`` is designed
    to escape -- so it falls back to the exact OLD ``str(c.value)`` text,
    UNCHANGED: complex-constant literal-value syntax is still §17 item 5
    OPEN, never invented here as a side effect of the scalar-string fix.
    """
    if c.lv_type and _is_error_cluster(c.lv_type):
        return _format_error_cluster(c.value)
    formatted = format_numeric_const(c.lv_type, c.value, c.display_format)
    if formatted is not None:
        return formatted
    if c.lv_type is not None and c.lv_type.kind != LVTypeKind.PRIMITIVE:
        return str(c.value)
    return _lvnet_literal_token(c.value)


def _lvnet_boundary_trailing(
    pane: ConnectorPaneTerminal, *, verbose: bool
) -> str | None:
    """The verbose-only trailing text for a BOUNDARY (connector-pane)
    terminal line: the §5 requirement keyword, the control's own §4
    ``default <value>`` clause, or both space-joined in that order -- §5's
    own worked example composes exactly this way: ``error in (no error) :
    Error optional default (no error)``. Terse (``verbose=False``) renders
    neither (unchanged from before this pass -- the golden §16 fixture's
    boundary has no non-``None`` default on any of its terminals, so it is
    byte-identical either way).

    Closes the round-trip harness's Gap #1 (see
    ``lvkit.graph.lvnet_parse``'s module docstring / the round-trip report):
    until this pass, a boundary line NEVER rendered
    ``ConnectorPaneTerminal.default`` at all, even in verbose mode -- a
    connector-pane control's authored default (e.g. a real ``U16`` output
    defaulting to ``"1"`` in ``Graphical Test Runner - Main UI - .vi``) was
    silently dropped from the lossless surface.
    """
    if not verbose:
        return None
    parts: list[str] = []
    requirement = _lvnet_requirement_trailing(pane)
    if requirement is not None:
        parts.append(requirement)
    if pane.default is not None:
        parts.append(f"{_LVNET_DEFAULT_KEYWORD} {_lvnet_literal_token(pane.default)}")
    return " ".join(parts) if parts else None


def _render_lvnet_dependency_interface(
    interface: list[ConnectorPaneTerminal], lines: list[str]
) -> None:
    """Render a ``subVI`` dependency's inline connector-pane interface (lvnet
    §7a, verbose-only) -- enough to rehydrate the MINIMAL graph's own leaf-
    loaded connector pane for this dependency, without a second parse.
    Indented under the dependency's own ``uses :`` entry line (LAYOUT
    PROVISIONAL, like the manifest itself -- trivial to move once the
    maintainer settles final placement).

    No requirement keyword, no driver (``_TermLine.trailing`` stays ``None``
    for both directions) -- this is the dependency's SIGNATURE, not a call
    site's own bindings; a call site's own wiring already renders under its
    own ``subVI <handle> : ...`` instance block elsewhere in the body (§7).
    Reuses ``_render_term_group``/``_lvnet_type_label`` directly -- the SAME
    column-alignment and named-type-collapsing rules every other terminal
    block in this renderer follows, never a second formatting path.
    """
    if not interface:
        return
    entries = [
        _TermLine(
            "in " if t.direction == "input" else "out",
            t.name,
            _lvnet_type_label(t.type, t.lv_type),
            None,
        )
        for t in interface
    ]
    lines.extend(_render_term_group(entries, _LVNET_DEP_INTERFACE_INDENT))


def _render_lvnet_uses(
    dependencies: list[NetlistDependency], lines: list[str], *, verbose: bool
) -> None:
    """Render the lvnet ``uses :`` dependency manifest (new §2/§7 note,
    ``docs/_internal/design/netlist-language.md``) -- the first "element" of
    the terse/verbose design: a plain reference list of every external file
    this VI directly depends on, present in BOTH modes. Appends directly to
    ``lines`` immediately after the ``vi <name> :`` header -- LAYOUT IS
    PROVISIONAL (the maintainer decides final section placement once every
    element exists), so this is its own small function, trivial to move.
    Omitted entirely (no lines appended) when the VI has no dependencies --
    never an empty ``uses :`` header.

    ``verbose`` gates the SECOND, later element §7a documents: each ``subVI``
    entry's own inline connector-pane interface
    (``_render_lvnet_dependency_interface``), indented right under that
    entry's line. Terse (``verbose=False``) renders the plain reference list
    only -- byte-identical to before this element existed.
    """
    if not dependencies:
        return
    lines.append(_USES_HEADER_LINE)
    under_cap_kinds = [
        len(d.kind.value)
        for d in dependencies
        if len(d.kind.value) <= _LVNET_DEP_KIND_CAP
    ]
    kind_width = (max(under_cap_kinds) if under_cap_kinds else 0) + 1
    needs_path_pad = any(d.path is not None for d in dependencies)
    qualified_width = 0
    if needs_path_pad:
        under_cap_q = [
            len(d.qualified)
            for d in dependencies
            if len(d.qualified) <= _LVNET_DEP_QUALIFIED_CAP
        ]
        qualified_width = (max(under_cap_q) if under_cap_q else 0) + 1
    for dep in dependencies:
        kind_part = _lvnet_capped_pad(dep.kind.value, kind_width, _LVNET_DEP_KIND_CAP)
        if dep.path is not None:
            qualified_part = _lvnet_capped_pad(
                dep.qualified, qualified_width, _LVNET_DEP_QUALIFIED_CAP
            )
            lines.append(
                f"    {kind_part}{qualified_part}{_LVNET_DEP_PATH_SEP}{dep.path}"
            )
        else:
            lines.append(f"    {kind_part}{dep.qualified}")
        if verbose:
            _render_lvnet_dependency_interface(dep.interface, lines)


def render_lvnet(
    module: NetlistModule,
    *,
    display_name: str | None = None,
    verbose: bool = False,
) -> str:
    """Render a ``NetlistModule`` to the lvnet text surface -- see
    ``docs/_internal/design/netlist-language.md`` §2-§10 (CLOSED grammar
    only; an OPEN construct, §17, emits its header keyword plus a literal
    ``# TODO(lvnet): ...`` and no invented inner syntax).

    NEW sibling of ``render_netlist`` (which still emits the OLD ``gamma``/
    ``mu``/``eta`` form) -- see the module's Phase A docstring notes
    throughout this file for exactly what changed underneath both.

    ``display_name`` mirrors ``render_netlist``'s own parameter (same
    reason: ``module.vi_name`` is the resolved ``vi_key`` -- a source-path
    identity, not fit for display, per ``NetlistModule.vi_name``'s own
    docstring). Defaults to ``module.vi_name`` when omitted.

    ``verbose`` (default ``False``, lvnet §11) is the terse/lossless switch:
    terse (default) renders IDENTICALLY to before this parameter existed
    (the §16 golden). Verbose additionally shows each BOUNDARY (connector-
    pane) terminal's §5 requirement keyword and (this pass) its own §4
    ``default <value>`` clause when the pane records one (see
    ``_lvnet_boundary_trailing``) -- subVI call-site wiring_rule nuance is a
    later slice (§11: "the wiring_rule nuance at call sites") -- plus, this
    pass, each ``subVI`` ``uses :`` entry's own inline connector-pane
    interface (see ``_render_lvnet_uses``/``_render_lvnet_dependency_
    interface``): enough to rehydrate that dependency's MINIMAL-load
    connector pane from the text alone; plus, this pass, a bottom-appendix
    ``types :`` section (§10, ``_render_lvnet_types``) giving every NAMED
    type's own FULL lossless structure (enum ordinals, cluster field
    types) -- the piece that makes verbose actually type-REHYDRATABLE,
    not just by-name-referenceable.
    """
    handles = _assign_lvnet_handles(module)
    header_name = display_name if display_name is not None else module.vi_name
    lines: list[str] = [f"vi {header_name}{_LVNET_BLOCK_OPEN}"]
    _render_lvnet_uses(module.dependencies, lines, verbose=verbose)

    # ``connector_pane.terminals`` is built (by BOTH builders) as inputs then
    # outputs, walked over the SAME ``ctx.inputs``/``ctx.outputs`` lists used
    # to build ``module.inputs``/``module.outputs`` -- so it lines up
    # POSITIONALLY, 1:1, with the boundary entries below.
    pane_terminals = module.connector_pane.terminals
    input_panes = pane_terminals[: len(module.inputs)]
    output_panes = pane_terminals[
        len(module.inputs) : len(module.inputs) + len(module.outputs)
    ]

    boundary_entries: list[_TermLine] = [
        _TermLine(
            "in ",
            inp.name,
            _lvnet_type_label(inp.type_descriptor, inp.lv_type),
            _lvnet_boundary_trailing(pane, verbose=verbose),
        )
        for inp, pane in zip(module.inputs, input_panes, strict=True)
    ] + [
        _TermLine(
            "out",
            o.name,
            _lvnet_type_label(o.type_descriptor, o.lv_type),
            _lvnet_boundary_trailing(pane, verbose=verbose),
        )
        for o, pane in zip(module.outputs, output_panes, strict=True)
    ]
    if boundary_entries:
        lines.extend(_render_term_group(boundary_entries, _LVNET_INDENT))

    lines.append("")
    _render_lvnet_items(module.body, _LVNET_INDENT, lines, handles)
    lines.append("")

    if module.outputs:
        name_width = max(len(o.name) for o in module.outputs) + 1
        for o in module.outputs:
            source_str = (
                _render_lvnet_source(o.source, handles) if o.source is not None else "?"
            )
            lines.append(f"  {o.name.ljust(name_width)}= {source_str}")

    if verbose:
        _render_lvnet_types(module, lines)

    return "\n".join(lines)
