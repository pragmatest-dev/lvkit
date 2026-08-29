"""Reconstruct a renderable ``NetlistModule`` from parsed lvnet text.

This is the REVERSE of ``render_lvnet``: ``lvnet_parse.parse_lvnet`` turns
text into a ``ParsedLvnet`` tree; this module turns that tree back into a
``NetlistModule`` such that re-rendering it (``render_lvnet(module,
verbose=True)``) reproduces the SAME text byte-for-byte -- the "Element 4"
losslessness gate:

    render_lvnet(reconstruct_module(parse_lvnet(T)), verbose=True) == T

This is a stronger proof than ``lvnet_parse.netlist_signature`` equality
(the existing round-trip gate): it proves the verbose text is enough to
rebuild a full, RENDER-EQUIVALENT model, not just a model whose comparable
projection matches. Fields ``render_lvnet`` never reads (a scope's
``NetlistFeedback.uid``, ...) do NOT need to match the original model --
this module is free to invent any value for those. Phase 3 changes this for
the fields that DO drive the text: ``NetlistInstance.uid``/
``NetlistConstant.uid``/``NetlistScope.uid`` (a local-variable instance's own
``uid`` included, now that its ``read``/``write`` declaration carries a real
``<handle>`` too) are now recovered FROM the handle/net text (the node's real
BD uid), not minted fresh -- see "Handles" below.

See ``docs/_internal/design/netlist-language.md`` §7/§9 (handle/net
derivation) and §10/§10.1 (types) for the grammar this reverses, and
``netlist.py``'s ``render_lvnet``/``_assign_lvnet_handles``/
``_render_lvnet_types`` for exactly what must be re-derived:

- **Handles**: Phase 3: ``render_lvnet`` derives an instance's HANDLE from
  its display ``name`` (extension stripped, despaced) plus the node's own
  stable BD uid (``<base>_<uid>``, ``_assign_lvnet_handles``) -- no longer a
  positional occurrence counter. We already HAVE the handle text in every
  declaration (``ParsedNode.handle`` / ``ParsedConstant.handle``), so
  instead of trying to re-derive a name that happens to hash to the same
  handle, we go the other way: recover ``name``/``uid`` FROM the handle
  text (see ``_derive_instance_name``/``_handle_base_and_suffix``), then
  thread that identity through a handle -> identity map (``_HandleTarget``,
  built by ``_index_handles`` in one pass over the WHOLE body before any
  terminal is resolved) so every ``<handle>::<terminal>`` reference
  elsewhere resolves to the identical ``uid`` the declaration itself
  carries (via ``NetRef.producer_uid``) -- which is all
  ``_assign_lvnet_handles``/``_render_lvnet_source`` need to re-derive the
  SAME handle string on the way back out. This also recovers the node's
  REAL identity: the reconstructed ``NetlistInstance.uid`` is the same BD
  uid the original graph carried, not a fresh mint.
- **Structural nets** (``case_UID::outK``, ``loop_UID::shiftK``,
  ``loop_UID::outK``, ``sequence_UID::outK``, ``disabled_UID::outK``,
  ``event_UID::outK``, ``fbK``): kept EXACTLY as captured from the text
  (``::`` and all) -- ``netlist._lvnet_net_separator`` is a no-op on a
  string that already contains ``::`` (its regex only rewrites a literal
  ``.`` separator), so round-tripping the already-final text through
  unchanged reproduces it byte-for-byte with no ``.``/``::`` bookkeeping
  needed on this side.
- **Structure identity** (Phase 4, the graph-IDENTITY round-trip gate,
  stronger still than Phase 3's byte-identity re-render): a case/loop/
  sequence/disabled/event structure's own real BD ``uid``
  (``NetlistScope.uid``) is recovered from its header's own OPTIONAL
  trailing ``(id <uid>)`` annotation (``ParsedScope.uid``, set by
  ``lvnet_parse._split_scope_header_id``) -- preferred over the OLDER
  net-derived recovery (``_structure_uid_from_net``, which now works for
  any structure kind that drives an output tunnel/shift register --
  ``netlist_build._frame_net_name_gn`` extended the same ``<kind>_<uid>.
  outK`` scheme case/loop already used to sequence/disabled/event's own
  output tunnels -- but still nothing for a structure that drives NO such
  output at all) in every ``_reconstruct_*_scope`` branch below. This
  closes a real gap verified against the corpus: WITHOUT the header
  annotation, a case/loop with no such output net, and a sequence/disabled/
  event structure that likewise drives no output tunnel, reconstructed with
  a freshly-minted uid instead of the original's.
- **Types**: a real ``LVType`` is reconstructed for a terminal's type text
  ONLY when doing so is both possible (the text is a bare name resolvable
  through the ``types :`` footnote, an array/refnum wrapper around one, or an
  anonymous ``Cluster{ f : <type> }`` / ``Enum{ m = 0 }`` spelled inline) and
  SELF-CONSISTENT (``_lvnet_type_inline`` of the rebuilt type reproduces the
  exact text) -- see ``_maybe_attach_lvtype``. This
  seeds ``_collect_lvnet_named_types`` so the footnote re-renders; every
  other terminal keeps ``lv_type=None`` and renders straight from its
  captured text, which is already byte-correct on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    ClusterField,
    DisableStructureKind,
    EnumValue,
    LVType,
    LVTypeKind,
    ScalarValue,
)
from .interface_order import WiringRequirement
from .lvnet_grammar import (
    _LVNET_CLUSTER_OPEN,
    _LVNET_DEFAULT_KEYWORD,
    _LVNET_DEFAULT_PAREN_PREFIX,
    _LVNET_DISABLE_KEYWORD,
    _LVNET_DRIVER_OP,
    _LVNET_ENUM_OPEN,
    _LVNET_INSTANCE_KEYWORDS,
    _LVNET_RING_OPEN,
    _LVNET_TERMINAL_SEP,
    _LVNET_TUNNEL_MODE_WORD,
    _LVNET_TYPE_SEP,
)
from .lvnet_parse import (
    _FRAME_ONLY_SCOPE_KINDS,
    ParsedBodyItem,
    ParsedConstant,
    ParsedDependency,
    ParsedFeedback,
    ParsedLocalVariable,
    ParsedLvnet,
    ParsedNode,
    ParsedScope,
    ParsedShiftRegister,
    ParsedTunnel,
    ParsedTypeDef,
    _scan_quoted_literal,
    _split_top_level_commas,
    _unescape_lvnet_string,
)
from .netlist import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    DefaultValue,
    DependencyKind,
    EtaMerge,
    GammaCase,
    GammaMerge,
    MuMerge,
    NetlistBoundaryInput,
    NetlistConstant,
    NetlistDependency,
    NetlistFeedback,
    NetlistFrame,
    NetlistInstance,
    NetlistInstanceKind,
    NetlistItem,
    NetlistModule,
    NetlistOutput,
    NetlistScope,
    NetlistTerminalBinding,
    NetRef,
)
from .render_lvnet import _lvnet_type_inline


class LvnetReconstructError(ValueError):
    """A parsed lvnet document could not be reconstructed into a renderable
    ``NetlistModule`` -- a genuine shape this module doesn't (yet) know how
    to reverse, named explicitly rather than guessed. Kept a separate
    exception from ``LvnetParseError`` (the TEXT-level grammar failure):
    this one fires only on a document that parsed fine but whose semantic
    shape this reconstructor can't rebuild a model for.
    """


# ============================================================
# Pass 1: handle -> (name, uid) identity map
# ============================================================


@dataclass(frozen=True)
class _HandleTarget:
    """What a declared handle resolves to, for every later ``<handle>::
    <terminal>`` (or bare, for a labeled constant) reference to it: ``name``
    (so ``_assign_lvnet_handles`` re-derives the SAME BASE on the way back
    out) and ``uid`` -- Phase 3: the node's own real BD uid, RECOVERED from
    the handle's own ``_<uid>`` suffix (``_handle_base_and_suffix``), not
    minted. ``uid`` serves both as the reconstructed instance/constant's own
    ``NetlistInstance.uid``/``NetlistConstant.uid`` and, for a later node-
    terminal reference, as ``NetRef.producer_uid`` -- the SAME identity
    ``_assign_lvnet_handles``/``_render_lvnet_source`` resolve through
    ``handles.by_uid`` on the way back out."""

    name: str
    uid: str
    is_constant: bool


def _handle_base_and_suffix(handle: str) -> tuple[str, str]:
    """Split a rendered ``<base>_<uid>`` handle (lvnet §7/§9) into its base
    and its trailing uid -- Phase 3: that suffix is the node's own stable BD
    uid (see ``_uid_of``), not a positional occurrence counter. Every real
    uid is a decimal-digit string, so a non-digit (or missing) suffix is a
    genuine grammar violation, raised rather than guessed at."""
    base, sep, suffix = handle.rpartition("_")
    if not sep or not suffix.isdigit():
        raise LvnetReconstructError(
            f"handle {handle!r} does not end in the mandatory '_<uid>' "
            f"suffix (lvnet §9)"
        )
    return base, suffix


def _derive_instance_name(kind: str, handle: str, component: str) -> str:
    """The display ``name`` to give a reconstructed ``NetlistInstance`` so
    ``_lvnet_handle_base(name)`` reproduces the SAME base ``_assign_lvnet_
    handles`` would have used to mint this handle (see module docstring).

    - ``function``/``in-place-element``/``formula-node``: ``.name`` IS the
      rendered component text verbatim (``_lvnet_component``'s fallback
      branch returns ``instance.name`` directly for these three kinds), so
      it must be the full component text, unstripped -- the SAME field
      the original render derived the handle from, so its base always
      matches by construction (no separate "display name" concept here).
    - ``subVI``/``property-node``/``invoke-node``: ``.name`` is never read
      by ``_lvnet_component`` for these (it reads ``qualified_name``/
      ``object_name``/``method_name`` instead) -- and for ``subVI``
      specifically, a REAL VI's own display name (what actually drove the
      original handle) can genuinely differ from its qualified/component
      text: a polymorphic VI's call site shows the SPECIFIC resolved
      instance name (e.g. ``I32 Changed__ogtk.vi``) while the HANDLE was
      minted from the polymorphic VI's OWN generic icon name (e.g. ``Data
      Changed.vi``) -- two different strings, neither recoverable from the
      other by text alone (a genuine losslessness gap, see the
      implementation report). So for all three of these kinds we don't
      try to recover the "true" display name at all -- the handle's own
      base (the part before its final ``_<uid>``) is used directly as
      ``.name``; it already has no extension/spaces to strip, and since
      ``.name`` is invisible to rendering for these kinds, this always
      reproduces the identical handle with no loss.
    """
    if kind in ("function", "in-place-element", "formula-node"):
        return component
    base, _ = _handle_base_and_suffix(handle)
    return base


def _index_handles(
    items: tuple[ParsedBodyItem, ...],
    registry: dict[str, _HandleTarget],
) -> None:
    """Walk the parsed body ONCE, in the SAME order ``_collect_lvnet_handle_
    targets`` walks the real model, registering every declared handle's
    identity before any terminal/net-reference text is resolved (pass 2,
    ``_reconstruct_items``) -- so a reference can never race its own
    declaration regardless of where in the body it sits."""
    for item in items:
        if isinstance(item, ParsedNode):
            assert item.handle is not None and item.component is not None
            name = _derive_instance_name(item.kind, item.handle, item.component)
            _, uid = _handle_base_and_suffix(item.handle)
            registry[item.handle] = _HandleTarget(
                name=name, uid=uid, is_constant=False
            )
        elif isinstance(item, ParsedLocalVariable):
            # Same base+uid derivation as a plain node (no ``component`` to
            # feed ``_derive_instance_name`` with -- a local-variable's own
            # handle base already IS the tapped control's display name, see
            # ``_render_lvnet_local_variable``, so the handle's own text is
            # enough).
            base, uid = _handle_base_and_suffix(item.handle)
            registry[item.handle] = _HandleTarget(
                name=base, uid=uid, is_constant=False
            )
        elif isinstance(item, ParsedConstant):
            base, uid = _handle_base_and_suffix(item.handle)
            registry[item.handle] = _HandleTarget(
                name=base, uid=uid, is_constant=True
            )
        elif isinstance(item, ParsedFeedback):
            pass  # referenced by its own bare `fbK` net text, never a handle
        elif isinstance(item, ParsedScope):
            if item.kind == "case" or item.kind in _FRAME_ONLY_SCOPE_KINDS:
                for frame in item.frames:
                    _index_handles(frame.body, registry)
            else:  # for-loop / while-loop -- single implicit body
                _index_handles(item.body, registry)


# ============================================================
# Net / literal token parsing (the reverse of _render_lvnet_source /
# _lvnet_literal_token)
# ============================================================


def _parse_lvnet_literal(token: str) -> ScalarValue:
    """The reverse of ``netlist._lvnet_literal_token``: a rendered literal
    VALUE token (a boundary control's own ``default <value>`` text) back to
    a raw ``ScalarValue``. Raises on anything that isn't one of the four
    shapes ``_lvnet_literal_token`` ever emits (quoted string / ``True`` /
    ``False`` / a bare number) -- never guesses."""
    token = token.strip()
    if token.startswith('"'):
        end = _scan_quoted_literal(token, 0)
        if end != len(token):
            raise LvnetReconstructError(
                f"trailing text after quoted literal: {token!r}"
            )
        return _unescape_lvnet_string(token)
    if token == "True":
        return True
    if token == "False":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    raise LvnetReconstructError(f"cannot parse lvnet literal token: {token!r}")


def _is_numeric_literal(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _parse_source_token(
    text: str, registry: dict[str, _HandleTarget]
) -> NetRef | DefaultValue:
    """The reverse of ``netlist._render_lvnet_source``: a rendered
    ``= <driver>``/merge-source VALUE token back to a ``NetRef`` (a net
    reference: node-terminal, boundary control, structural net, or an inlined
    literal) or a ``DefaultValue`` (the drive-position ``(default <Type>)``
    form -- an unwired case-output tunnel/shift-register/feedback init).

    A plain literal token (quoted string / ``True``/``False``/number) is
    reconstructed as a ``NetRef`` with ``node=None`` and ``lvnet_value`` set
    to the SAME text -- indistinguishable in render output from a
    ``DefaultValue`` carrying a real (non-``"?"``) literal, since both
    render through the identical code path (``_lvnet_default_token``
    returns the literal alone when it isn't ``"?"``) -- so either
    reconstruction re-renders byte-identically; there is no information in
    the text itself to tell the two apart, and none is needed for
    render idempotence.
    """
    text = text.strip()
    if text.startswith(_LVNET_DEFAULT_PAREN_PREFIX) and text.endswith(")"):
        return DefaultValue(
            literal="?", type_descriptor=text[len(_LVNET_DEFAULT_PAREN_PREFIX) : -1]
        )
    if text.startswith('"'):
        end = _scan_quoted_literal(text, 0)
        if end != len(text):
            raise LvnetReconstructError(
                f"trailing text after quoted literal in source: {text!r}"
            )
        return NetRef(
            node=None, terminal="", occurrence=None, bare=text, lvnet_value=text
        )
    if text in ("True", "False") or _is_numeric_literal(text):
        return NetRef(
            node=None, terminal="", occurrence=None, bare=text, lvnet_value=text
        )
    if _LVNET_TERMINAL_SEP in text:
        handle_part, _, terminal = text.partition(_LVNET_TERMINAL_SEP)
        target = registry.get(handle_part)
        if target is not None and not target.is_constant:
            # Phase 3: ``producer_uid`` is the recovered identity --
            # ``_render_lvnet_source`` resolves it straight through
            # ``handles.by_uid`` on the way back out (mirrors a labeled
            # constant's ``constant_uid``); ``occurrence`` is no longer
            # part of that resolution (see ``_HandleTarget``).
            return NetRef(
                node=target.name,
                terminal=terminal,
                occurrence=None,
                bare=text,
                producer_uid=target.uid,
            )
        # Not a registered node handle -- a structure-scoped net
        # (`case_UID::outK`/`loop_UID::shiftK`/`loop_UID::outK`) never IS
        # one; fall through to the bare/structural-net form below.
        return NetRef(node=None, terminal="", occurrence=None, bare=text)
    target = registry.get(text)
    if target is not None and target.is_constant:
        return NetRef(
            node=None, terminal="", occurrence=None, bare=text, constant_uid=target.uid
        )
    # A boundary control's own display name, or a bare structural net
    # (`fbK`) -- both render via the same "node is None" fallback.
    return NetRef(node=None, terminal="", occurrence=None, bare=text)


def _require_netref(source: NetRef | DefaultValue, where: str) -> NetRef:
    if not isinstance(source, NetRef):
        raise LvnetReconstructError(
            f"expected a net reference at {where}, got a bare default: {source!r}"
        )
    return source


# ============================================================
# Types (the reverse of _lvnet_type_lossless_def / _lvnet_type_ref)
# ============================================================


def _fill_enum_values(lv: LVType, body: str) -> None:
    body = body.strip()
    if not body or body == "?":
        lv.values = None
        return
    values: dict[str, EnumValue] = {}
    for part in _split_top_level_commas(body):
        name, _, ordinal_text = part.rpartition(_LVNET_DRIVER_OP)
        values[name.strip()] = EnumValue(value=int(ordinal_text.strip()))
    lv.values = values


def _fill_cluster_fields(
    lv: LVType, body: str, types_dict: dict[str, ParsedTypeDef], memo: dict[str, LVType]
) -> None:
    body = body.strip()
    if body == "?":
        lv.fields = None
        return
    if not body:
        lv.fields = []
        return
    fields: list[ClusterField] = []
    for part in _split_top_level_commas(body):
        name, _, type_text = part.partition(_LVNET_TYPE_SEP)
        field_type = _reconstruct_type_ref(type_text.strip(), types_dict, memo)
        fields.append(ClusterField(name=name.strip(), type=field_type))
    lv.fields = fields


def _reconstruct_named_type(
    name: str, types_dict: dict[str, ParsedTypeDef], memo: dict[str, LVType]
) -> LVType:
    """Build the ONE reconstructed ``LVType`` for a ``types :`` footnote
    NAME (memoized, cycle-safe -- the in-progress placeholder is stashed in
    ``memo`` before recursing into its own fields/members). Every real
    footnote entry's def text always starts with ``Enum{``/``Ring{``/
    ``Cluster{`` -- ``_lvnet_named_stem`` (the module side) only ever
    contributes a footnote name for those three ``LVType`` kinds, and each
    one's OWN ``_lvnet_type_lossless_def`` dispatch is keyed on that same
    kind -- so any other shape here is a genuine reconstruction gap, raised
    rather than guessed at.
    """
    if name in memo:
        return memo[name]
    entry = types_dict[name]
    def_text = entry.def_text
    if def_text.startswith(_LVNET_ENUM_OPEN) and def_text.endswith("}"):
        lv = LVType(kind=LVTypeKind.ENUM, typedef_name=name, typedef_path=entry.path)
        memo[name] = lv
        _fill_enum_values(lv, def_text[len(_LVNET_ENUM_OPEN) : -1])
        return lv
    if def_text.startswith(_LVNET_RING_OPEN) and def_text.endswith("}"):
        lv = LVType(kind=LVTypeKind.RING, typedef_name=name, typedef_path=entry.path)
        memo[name] = lv
        _fill_enum_values(lv, def_text[len(_LVNET_RING_OPEN) : -1])
        return lv
    if def_text.startswith(_LVNET_CLUSTER_OPEN) and def_text.endswith("}"):
        lv = LVType(kind=LVTypeKind.CLUSTER, typedef_name=name, typedef_path=entry.path)
        memo[name] = lv
        _fill_cluster_fields(
            lv, def_text[len(_LVNET_CLUSTER_OPEN) : -1], types_dict, memo
        )
        return lv
    raise LvnetReconstructError(
        f"'types :' entry {name!r} has an unexpected def shape: {def_text!r}"
    )


def _reconstruct_type_ref(
    text: str, types_dict: dict[str, ParsedTypeDef], memo: dict[str, LVType]
) -> LVType:
    """The reverse of ``netlist._lvnet_type_ref``/``_lvnet_type_lossless_
    def``: one type-reference TEXT (an inline terminal label, or a nested
    footnote field/element type) -> a real ``LVType``. Recognizes, in
    order: an array wrapper (``[...]``); a refnum wrapper (``<ref_type>
    refnum{...}`` / ``<ref_type> refnum`` / bare ``refnum``); the
    CAPITALIZED lossless structural forms (``Enum{...}``/``Ring{...}``/
    ``Cluster{...}`` -- these appear only INSIDE a footnote's own def text,
    an anonymous nested composite, never as a top-level terse inline
    label); a bare NAME resolving through ``types_dict``; anything else
    (a scalar token, a class identity, an anonymous lowercase ``cluster{
    ...}``/``enum{...}`` inline label whose FIELD TYPES aren't visible from
    text at all) as an opaque leaf, deliberately modeled as
    ``LVTypeKind.CLASS`` -- a kind ``LVType.type_descriptor()`` (the
    TOP-LEVEL render path) has no branch for, so it reliably FAILS
    ``_maybe_attach_lvtype``'s self-check and is never attached to a real
    terminal (falls back to the terminal's own already-correct raw text)
    -- while ``_lvnet_type_lossless_def`` (the NESTED/footnote render path)
    DOES have a ``CLASS`` branch (returns ``classname`` verbatim), so a
    leaf reached while building a NAMED type's own footnote fields still
    reproduces its exact text.
    """
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        dims = 0
        inner = text
        while inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
            dims += 1
        elem = _reconstruct_type_ref(inner.strip(), types_dict, memo)
        return LVType(kind=LVTypeKind.ARRAY, dimensions=dims, element_type=elem)
    refnum_idx = text.find(" refnum{")
    if (
        refnum_idx != -1
        and text.endswith("}")
        and "{" not in text[:refnum_idx]
        and "[" not in text[:refnum_idx]
    ):
        # A brace before `` refnum{`` means it's nested inside a
        # ``Cluster{ ... refnum{...} ... }`` field -- the whole text is a
        # cluster (handled below), not a top-level refnum.
        ref_type = text[:refnum_idx]
        inner = text[refnum_idx + len(" refnum{") : -1].strip()
        elem = _reconstruct_type_ref(inner, types_dict, memo)
        return LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type="Refnum",
            ref_type=ref_type,
            element_type=elem,
        )
    if text.endswith(" refnum") and "{" not in text:
        return LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type="Refnum",
            ref_type=text[: -len(" refnum")],
        )
    if text == "refnum":
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
    if text.startswith(_LVNET_ENUM_OPEN) and text.endswith("}"):
        lv = LVType(kind=LVTypeKind.ENUM)
        _fill_enum_values(lv, text[len(_LVNET_ENUM_OPEN) : -1])
        return lv
    if text.startswith(_LVNET_RING_OPEN) and text.endswith("}"):
        lv = LVType(kind=LVTypeKind.RING)
        _fill_enum_values(lv, text[len(_LVNET_RING_OPEN) : -1])
        return lv
    if text.startswith(_LVNET_CLUSTER_OPEN) and text.endswith("}"):
        lv = LVType(kind=LVTypeKind.CLUSTER)
        _fill_cluster_fields(lv, text[len(_LVNET_CLUSTER_OPEN) : -1], types_dict, memo)
        return lv
    if text in types_dict:
        return _reconstruct_named_type(text, types_dict, memo)
    return LVType(kind=LVTypeKind.CLASS, classname=text)


def _maybe_attach_lvtype(
    type_text: str, types_dict: dict[str, ParsedTypeDef], memo: dict[str, LVType]
) -> LVType | None:
    """Reconstruct a REAL ``LVType`` for one terminal's inline type text
    ONLY when doing so is self-consistent -- ``_lvnet_type_inline(candidate)``
    must reproduce ``type_text`` exactly, the very renderer ``render_lvnet``
    applies to a terminal's type on the way out (so an anonymous cluster
    reconstructed from its ``Cluster{ f : <type> }`` inline text, carrying
    its rebuilt field types, is attached and re-renders identically -- the
    check must mirror THAT renderer, not the lossy ``type_descriptor`` an
    anonymous cluster would fail). Returns ``None`` (never attach) when the
    text is a plain scalar/class leaf at the TOP level (the
    ``LVTypeKind.CLASS`` placeholder's ``type_descriptor()`` renders ``"?"``,
    which can never equal real text) --
    the terminal's own already-correct raw text is used instead (see
    ``netlist._lvnet_type_label``'s ``lv_type is None`` fallback), and no
    named type is ever lost by skipping this: a top-level leaf never wraps
    a nested named type (only array/refnum/named-cluster wrappers do, and
    those DO self-check successfully).
    """
    candidate = _reconstruct_type_ref(type_text, types_dict, memo)
    try:
        rendered = _lvnet_type_inline(candidate)
    except Exception:
        return None
    if rendered != type_text:
        return None
    return candidate


# ============================================================
# Pass 2: build the NetlistItem tree
# ============================================================

_REVERSE_INSTANCE_KEYWORDS: dict[str, NetlistInstanceKind] = {
    word: kind for kind, word in _LVNET_INSTANCE_KEYWORDS.items()
}
_REVERSE_TUNNEL_MODE_WORD: dict[str, str] = {
    word: code for code, word in _LVNET_TUNNEL_MODE_WORD.items()
}
_REVERSE_DISABLE_KEYWORD: dict[str, DisableStructureKind] = {
    word: kind for kind, word in _LVNET_DISABLE_KEYWORD.items()
}


def _reconstruct_local_variable(
    item: ParsedLocalVariable, registry: dict[str, _HandleTarget]
) -> NetlistInstance:
    """The reverse of ``_render_lvnet_local_variable`` (§7, now designed): a
    ``read`` becomes a SOURCE (one output, no inputs) so a LATER
    ``<handle>::<port>`` reference elsewhere resolves through
    ``producer_uid`` exactly like any other node's; a ``write`` becomes a
    SINK (one input driven by the parsed ``source``, no outputs). Neither
    shape carries a real terminal name or type (§7's local-variable syntax
    renders none) -- ``target.name`` (the tapped control's own display name,
    recovered from the handle) doubles as the placeholder terminal name,
    never read back out by ``render_lvnet`` either way; ``type`` is the
    honest ``"?"`` (unobservable from text alone).
    """
    target = registry[item.handle]
    if item.is_write:
        assert item.source is not None
        source = _parse_source_token(item.source, registry)
        net = source if isinstance(source, NetRef) else None
        default = source if isinstance(source, DefaultValue) else None
        binding = NetlistTerminalBinding(
            terminal=target.name,
            type="?",
            net=net,
            default=default,
            inverted=False,
            pane_rank=0,
            lv_type=None,
        )
        return NetlistInstance(
            uid=target.uid,
            name=target.name,
            occurrence=None,
            inputs=[binding],
            outputs=[],
            kind=NetlistInstanceKind.LOCAL_VARIABLE,
        )
    ref = NetRef(
        node=target.name,
        terminal=target.name,
        occurrence=None,
        bare=f"{item.handle}{_LVNET_TERMINAL_SEP}{target.name}",
        producer_uid=target.uid,
    )
    return NetlistInstance(
        uid=target.uid,
        name=target.name,
        occurrence=None,
        inputs=[],
        outputs=[NetlistOutput(net=ref, type="?", pane_rank=0, lv_type=None)],
        kind=NetlistInstanceKind.LOCAL_VARIABLE,
    )


def _reconstruct_instance(
    item: ParsedNode,
    registry: dict[str, _HandleTarget],
    types_dict: dict[str, ParsedTypeDef],
    memo: dict[str, LVType],
    fresh_uid: _UidSource,
) -> NetlistInstance:
    assert item.handle is not None and item.component is not None
    target = registry[item.handle]
    kind_enum = _REVERSE_INSTANCE_KEYWORDS.get(item.kind)
    if kind_enum is None:
        raise LvnetReconstructError(f"unrecognized node keyword: {item.kind!r}")

    qualified_name: str | None = None
    object_name: str | None = None
    method_name: str | None = None
    if kind_enum is NetlistInstanceKind.SUBVI:
        qualified_name = item.component
    elif kind_enum is NetlistInstanceKind.PROPERTY_NODE:
        object_name = item.component
    elif kind_enum is NetlistInstanceKind.INVOKE_NODE:
        object_name, sep, method_name = item.component.rpartition(".")
        if not sep:
            object_name, method_name = item.component, "?"

    inputs: list[NetlistTerminalBinding] = []
    outputs: list[NetlistOutput] = []
    in_rank = 0
    out_rank = 0
    for t in item.terminals:
        lv_type = _maybe_attach_lvtype(t.type, types_dict, memo)
        if t.direction == "in":
            if t.driver is not None:
                net = _require_netref(
                    _parse_source_token(t.driver, registry),
                    f"{item.handle}.{t.name} driver",
                )
                default = None
            elif t.default is not None:
                net = None
                literal = "?" if t.default == _LVNET_DEFAULT_KEYWORD else t.default
                default = DefaultValue(literal=literal, type_descriptor=t.type)
            else:
                raise LvnetReconstructError(
                    f"input terminal {t.name!r} on {item.handle!r} has "
                    f"neither a driver nor a default"
                )
            inputs.append(
                NetlistTerminalBinding(
                    terminal=t.name,
                    type=t.type,
                    net=net,
                    default=default,
                    inverted=t.inverted,
                    pane_rank=in_rank,
                    lv_type=lv_type,
                )
            )
            in_rank += 1
        else:
            ref = NetRef(
                node=target.name,
                terminal=t.name,
                occurrence=None,
                bare=f"{item.handle}::{t.name}",
                producer_uid=target.uid,
            )
            outputs.append(
                NetlistOutput(net=ref, type=t.type, pane_rank=out_rank, lv_type=lv_type)
            )
            out_rank += 1

    return NetlistInstance(
        uid=target.uid,
        name=target.name,
        occurrence=None,
        inputs=inputs,
        outputs=outputs,
        kind=kind_enum,
        qualified_name=qualified_name,
        object_name=object_name,
        method_name=method_name,
    )


def _reconstruct_constant(
    item: ParsedConstant, registry: dict[str, _HandleTarget]
) -> NetlistConstant:
    target = registry[item.handle]
    return NetlistConstant(
        uid=target.uid,
        name=target.name,
        occurrence=None,
        type=item.type,
        value=item.value,
        lvnet_value=item.value,
    )


def _reconstruct_feedback(
    item: ParsedFeedback, registry: dict[str, _HandleTarget], fresh_uid: _UidSource
) -> NetlistFeedback:
    if item.attribute == "? iterations":
        delay = None
    else:
        delay = int(item.attribute.split()[0])
    init = _parse_source_token(item.init, registry)
    recur = (
        _require_netref(_parse_source_token(item.each, registry), f"{item.net} each")
        if item.each is not None
        else None
    )
    return NetlistFeedback(
        uid=fresh_uid.next(f"fb_{item.net}"),
        net=item.net,
        init=init,
        recur=recur,
        delay=delay,
    )


def _reconstruct_shift_register(
    sr: ParsedShiftRegister, registry: dict[str, _HandleTarget]
) -> MuMerge:
    init = _parse_source_token(sr.init, registry)
    recur = (
        _require_netref(_parse_source_token(sr.each, registry), f"{sr.net} each")
        if sr.each is not None
        else None
    )
    return MuMerge(net=sr.net, init=init, recur=recur)


def _reconstruct_tunnel(
    t: ParsedTunnel, registry: dict[str, _HandleTarget]
) -> EtaMerge:
    mode_text = t.mode
    conditional = False
    if mode_text.endswith("+conditional"):
        conditional = True
        mode_text = mode_text[: -len("+conditional")]
    index_mode = _REVERSE_TUNNEL_MODE_WORD.get(mode_text)
    if index_mode is None:
        raise LvnetReconstructError(f"unrecognized tunnel mode word: {t.mode!r}")
    value = _parse_source_token(t.source, registry)
    return EtaMerge(
        net=t.net, index_mode=index_mode, conditional=conditional, value=value
    )


def _structure_uid_from_net(net: str) -> str | None:
    """Recover a structure's own real BD uid from one of its
    ``case_UID::outK``/``loop_UID::shiftK``/``loop_UID::outK``/
    ``sequence_UID::outK``/``disabled_UID::outK``/``event_UID::outK``
    structural net strings (the ``_render_lvnet_source``/
    ``_lvnet_net_separator``-reformed text ``_reconstruct_scope`` already has
    in hand, unchanged -- see the module docstring's "Structural nets" note).
    Generic over the prefix -- it splits on the first ``_`` and takes
    whatever digit run follows, so a new structure-net prefix needs no
    change here. Returns ``None`` for a structure with NO such net anywhere
    in the text -- Phase 4 closed the gap this used to leave open (a
    structure that drives no output tunnel/shift register at all) via the
    header's own ``(id <uid>)`` annotation (``ParsedScope.uid``, preferred by
    every ``_reconstruct_*_scope`` branch below); this function now serves
    only as a defensive fallback for text that predates that annotation."""
    _, sep, rest = net.partition("_")
    if not sep:
        return None
    uid, _, _ = rest.partition(_LVNET_TERMINAL_SEP)
    return uid if uid.isdigit() else None


def _reconstruct_scope(
    item: ParsedScope,
    registry: dict[str, _HandleTarget],
    types_dict: dict[str, ParsedTypeDef],
    memo: dict[str, LVType],
    fresh_uid: _UidSource,
) -> NetlistScope:
    if item.kind == "case":
        frames: list[NetlistFrame] = []
        for f in item.frames:
            body = _reconstruct_items(f.body, registry, types_dict, memo, fresh_uid)
            frames.append(
                NetlistFrame(
                    label=f.label, value=f.label, is_default=f.is_default, body=body
                )
            )
        net_order: list[str] = []
        cases_by_net: dict[str, list[GammaCase]] = {}
        for f, frame_obj in zip(item.frames, frames, strict=True):
            # Matches ``netlist_build._build_case_outputs``'s OWN frame_key
            # convention exactly ("default" if is_default else label) -- both
            # sides must agree, or a default frame's ``GammaCase`` fails to
            # find its match by key and its case-output drive is silently
            # dropped (the TextTestRunner/run.vi bug this closes: two frames
            # both labeled "Error", only one of which is the default).
            frame_key = "default" if frame_obj.is_default else frame_obj.label
            for d in f.drives:
                if d.net not in cases_by_net:
                    cases_by_net[d.net] = []
                    net_order.append(d.net)
                source = _parse_source_token(d.source, registry)
                cases_by_net[d.net].append(
                    GammaCase(frame_key=frame_key, source=source)
                )
        selector = None
        if item.selector is not None and item.selector != "?":
            selector = _require_netref(
                _parse_source_token(item.selector, registry), "case selector"
            )
        outputs: list[GammaMerge | MuMerge | EtaMerge] = [
            GammaMerge(net=net, selector=None, cases=cases_by_net[net])
            for net in net_order
        ]
        case_uid = item.uid
        if case_uid is None and net_order:
            case_uid = _structure_uid_from_net(net_order[0])
        return NetlistScope(
            uid=case_uid if case_uid is not None else fresh_uid.next("case"),
            kind="case",
            selector=selector,
            frames=frames,
            outputs=outputs,
        )

    if item.kind in ("for-loop", "while-loop"):
        kind = "while" if item.kind == "while-loop" else "for"
        body = _reconstruct_items(item.body, registry, types_dict, memo, fresh_uid)
        frame = NetlistFrame(label="", value="", is_default=False, body=body)
        mu_list = [
            _reconstruct_shift_register(sr, registry) for sr in item.shift_registers
        ]
        eta_list = [_reconstruct_tunnel(t, registry) for t in item.tunnels]
        loop_uid = item.uid
        if loop_uid is None:
            loop_uid = next(
                (
                    uid
                    for net in (*(m.net for m in mu_list), *(m.net for m in eta_list))
                    if (uid := _structure_uid_from_net(net)) is not None
                ),
                None,
            )
        return NetlistScope(
            uid=loop_uid if loop_uid is not None else fresh_uid.next("loop"),
            kind=kind,
            selector=None,
            frames=[frame],
            outputs=[*mu_list, *eta_list],
        )

    if item.kind in ("flat-sequence", "stacked-sequence"):
        frames = []
        for f in item.frames:
            value = f.label
            if value.startswith("[") and value.endswith("]"):
                value = value[1:-1]
            body = _reconstruct_items(f.body, registry, types_dict, memo, fresh_uid)
            frames.append(
                NetlistFrame(label=value, value=value, is_default=False, body=body)
            )
        return NetlistScope(
            uid=item.uid if item.uid is not None else fresh_uid.next("sequence"),
            kind="sequence",
            selector=None,
            frames=frames,
            sequence_is_flat=(item.kind == "flat-sequence"),
        )

    if item.kind in ("diagram-disable", "conditional-disable", "type-specialization"):
        disable_kind = _REVERSE_DISABLE_KEYWORD.get(item.kind)
        if disable_kind is None:
            raise LvnetReconstructError(f"unrecognized disable kind: {item.kind!r}")
        frames = [
            NetlistFrame(
                label=f.label,
                value=f.label,
                is_default=f.is_default,
                body=_reconstruct_items(f.body, registry, types_dict, memo, fresh_uid),
            )
            for f in item.frames
        ]
        return NetlistScope(
            uid=item.uid if item.uid is not None else fresh_uid.next("disabled"),
            kind="disabled",
            selector=None,
            frames=frames,
            disable_kind=disable_kind,
        )

    if item.kind == "event-structure":
        frames = [
            NetlistFrame(
                label=f.label,
                value=f.label,
                is_default=False,
                body=_reconstruct_items(f.body, registry, types_dict, memo, fresh_uid),
            )
            for f in item.frames
        ]
        return NetlistScope(
            uid=item.uid if item.uid is not None else fresh_uid.next("event"),
            kind="event",
            selector=None,
            frames=frames,
        )

    raise LvnetReconstructError(f"unrecognized scope kind: {item.kind!r}")


def _reconstruct_items(
    items: tuple[ParsedBodyItem, ...],
    registry: dict[str, _HandleTarget],
    types_dict: dict[str, ParsedTypeDef],
    memo: dict[str, LVType],
    fresh_uid: _UidSource,
) -> list[NetlistItem]:
    out: list[NetlistItem] = []
    for item in items:
        if isinstance(item, ParsedNode):
            out.append(
                _reconstruct_instance(item, registry, types_dict, memo, fresh_uid)
            )
        elif isinstance(item, ParsedLocalVariable):
            out.append(_reconstruct_local_variable(item, registry))
        elif isinstance(item, ParsedConstant):
            out.append(_reconstruct_constant(item, registry))
        elif isinstance(item, ParsedFeedback):
            out.append(_reconstruct_feedback(item, registry, fresh_uid))
        elif isinstance(item, ParsedScope):
            out.append(_reconstruct_scope(item, registry, types_dict, memo, fresh_uid))
        else:  # pragma: no cover -- exhaustive over ParsedBodyItem
            raise LvnetReconstructError(f"unrecognized parsed body item: {item!r}")
    return out


class _UidSource:
    """A trivial unique-string generator for body items whose ``uid`` is
    never looked up by anything (a scope, a feedback node) -- only needs to
    be distinct within one module, never to match the original graph's real
    trailing-node uid. A local-variable instance's ``uid`` is NOT one of
    these -- it is recovered from its own handle (``_reconstruct_local_
    variable``), since a later ``<handle>::<port>`` reference must resolve
    through it."""

    def __init__(self) -> None:
        self._n = 0

    def next(self, tag: str) -> str:
        self._n += 1
        return f"{tag}_{self._n}"


# ============================================================
# Boundary / dependencies / top level
# ============================================================


def _reconstruct_dependency(
    d: ParsedDependency, types_dict: dict[str, ParsedTypeDef], memo: dict[str, LVType]
) -> NetlistDependency:
    interface = []
    for t in d.interface:
        lv_type = _maybe_attach_lvtype(t.type, types_dict, memo)
        interface.append(
            ConnectorPaneTerminal(
                name=t.name,
                type=t.type,
                direction="input" if t.direction == "in" else "output",
                index=None,
                wiring_requirement=WiringRequirement.UNKNOWN,
                default=None,
                lv_type=lv_type,
            )
        )
    return NetlistDependency(
        kind=DependencyKind(d.kind),
        qualified=d.qualified,
        path=d.path,
        interface=interface,
    )


def reconstruct_module(parsed: ParsedLvnet) -> NetlistModule:
    """Build a ``NetlistModule`` from ``parse_lvnet``'s output such that
    ``render_lvnet(reconstruct_module(parsed), verbose=True)`` reproduces
    the ORIGINAL text byte-for-byte (assuming ``parsed`` was itself parsed
    from a real ``render_lvnet`` output) -- see the module docstring for
    the handle/net/type re-derivation strategy.
    """
    types_dict = parsed.types
    memo: dict[str, LVType] = {}
    fresh_uid = _UidSource()

    registry: dict[str, _HandleTarget] = {}
    _index_handles(parsed.body, registry)

    body = _reconstruct_items(parsed.body, registry, types_dict, memo, fresh_uid)

    inputs: list[NetlistBoundaryInput] = []
    outputs: list[BoundaryOutput] = []
    pane_terminals: list[ConnectorPaneTerminal] = []
    for t in parsed.boundary:
        lv_type = _maybe_attach_lvtype(t.type, types_dict, memo)
        requirement = (
            WiringRequirement.UNKNOWN
            if t.requirement is None
            else WiringRequirement(t.requirement)
        )
        default_scalar: ScalarValue = None
        if t.default is not None:
            if t.default == _LVNET_DEFAULT_KEYWORD:
                raise LvnetReconstructError(
                    f"boundary terminal {t.name!r} has a bare 'default' "
                    f"keyword with no value -- not a shape a boundary line "
                    f"is expected to carry (only a NODE terminal line's "
                    f"default can be bare)"
                )
            default_scalar = _parse_lvnet_literal(t.default)
        pane_terminals.append(
            ConnectorPaneTerminal(
                name=t.name,
                type=t.type,
                direction="input" if t.direction == "in" else "output",
                index=t.index,
                wiring_requirement=requirement,
                default=default_scalar,
                lv_type=lv_type,
            )
        )
        # A boundary line with NO ``@<index>`` is an OFF-PANE front-panel
        # control/indicator (``netlist_build._off_pane_terminals`` --
        # ``ConnectorPaneTerminal.index is None``): it has no
        # ``module.inputs``/``.outputs`` counterpart at all -- it never
        # drives/reads the VI's own boundary wiring, only declares itself in
        # ``connector_pane.terminals`` -- so it contributes to
        # ``pane_terminals`` above only, never to ``inputs``/``outputs``
        # (which would wrongly demand an output-drive line for it below).
        if t.index is None:
            continue
        if t.direction == "in":
            inputs.append(
                NetlistBoundaryInput(
                    name=t.name, type_descriptor=t.type, lv_type=lv_type
                )
            )
        else:
            outputs.append(
                BoundaryOutput(
                    name=t.name, type_descriptor=t.type, source=None, lv_type=lv_type
                )
            )

    if len(outputs) != len(parsed.output_drives):
        raise LvnetReconstructError(
            f"boundary output count ({len(outputs)}) does not match the "
            f"number of output-drive lines ({len(parsed.output_drives)})"
        )
    for out_bo, drive in zip(outputs, parsed.output_drives, strict=True):
        if drive.net != out_bo.name:
            raise LvnetReconstructError(
                f"output-drive name {drive.net!r} does not match boundary "
                f"output {out_bo.name!r} at the same position"
            )
        if drive.source == "?":
            out_bo.source = None
        else:
            out_bo.source = _require_netref(
                _parse_source_token(drive.source, registry),
                f"output drive {drive.net!r}",
            )

    dependencies = [_reconstruct_dependency(d, types_dict, memo) for d in parsed.uses]

    return NetlistModule(
        vi_name=parsed.vi_name,
        inputs=inputs,
        outputs=outputs,
        body=body,
        connector_pane=ConnectorPane(
            pattern_id=parsed.pattern_id, terminals=pane_terminals
        ),
        dependencies=dependencies,
    )
