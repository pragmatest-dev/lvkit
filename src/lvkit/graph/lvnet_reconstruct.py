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
  thread that identity through a handle -> identity map (``HandleTarget``,
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

from collections import Counter
from collections.abc import Sequence

from ..models import (
    ClusterField,
    DisableStructureKind,
    EnumValue,
    LVType,
    LVTypeKind,
    ScalarValue,
    TunnelMode,
)
from .interface_order import WiringRequirement
from .lvnet_grammar import (
    _LVNET_CLUSTER_OPEN,
    _LVNET_DEFAULT_KEYWORD,
    _LVNET_DISABLE_KEYWORD,
    _LVNET_DRIVER_OP,
    _LVNET_ENUM_OPEN,
    _LVNET_INPLACE_SCOPE_KEYWORD,
    _LVNET_INSTANCE_KEYWORDS,
    _LVNET_LOOP_CONDITION_TERMINAL,
    _LVNET_RING_OPEN,
    _LVNET_TERMINAL_SEP,
    _LVNET_TUNNEL_MODE_WORD,
    _LVNET_TYPE_SEP,
    lvnet_id_suffix,
)
from .lvnet_parse import (
    _FRAME_ONLY_SCOPE_KINDS,
    ParsedBodyItem,
    ParsedBoundaryTerminal,
    ParsedConstant,
    ParsedControlRead,
    ParsedDependency,
    ParsedFeedback,
    ParsedGlobalVariable,
    ParsedIndicatorDrive,
    ParsedLocalVariable,
    ParsedLvnet,
    ParsedNode,
    ParsedScope,
    ParsedShiftRegister,
    ParsedTunnel,
    ParsedTypeDef,
    ParsedVIServerReference,
    _find_first_top_level,
    _split_event_name,
    _split_top_level_commas,
    _unescape_lvnet_string,
    _unquote_name_token,
    split_shared_variable_component,
)
from .lvnet_reconstruct_common import (
    HandleTarget,
    LvnetReconstructError,
    check_shared_references,
    parse_lvnet_literal,
    parse_source_token,
    referenced_terminals,
    require_netref,
)
from .lvnet_reconstruct_front_panel import (
    check_control_reads,
    claim_indicator_drives,
    control_reads,
    indicator_drive_output,
    mark_read_outputs,
    placed_drives,
)
from .lvnet_reconstruct_scopes import (
    LoopWires,
    case_outputs,
    disabled_frame_value,
    event_outputs,
    sequence_outputs,
    structure_input,
)
from .netlist import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    DefaultValue,
    DependencyKind,
    EtaMerge,
    InPlaceBorderDrive,
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
from .netlist_models import (
    LoopCondition,
    NetlistControlRead,
    NetlistIndicatorDrive,
)
from .render_lvnet import _lvnet_type_inline, lvnet_control_name_token

# ============================================================
# Pass 1: handle -> (name, uid) identity map
# ============================================================


def _handle_base_and_suffix(handle: str) -> tuple[str, str]:
    """Split a rendered ``<base>_<uid>`` handle (lvnet §7/§9) into its base
    and its trailing uid -- Phase 3: that suffix is the node's own stable BD
    uid (see ``uid_of``), not a positional occurrence counter. Every real
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
    if kind == NetlistInstanceKind.SHARED_VARIABLE.value:
        # The component is ``<library>::<variable name> : <read|write>``; the
        # variable name is the instance's name, exactly (``_lvnet_component``
        # renders it).
        return split_shared_variable_component(component).variable
    base, _ = _handle_base_and_suffix(handle)
    return base


def _index_handles(
    items: tuple[ParsedBodyItem, ...],
    registry: dict[str, HandleTarget],
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
            registry[item.handle] = HandleTarget(name=name, uid=uid, is_constant=False)
        elif isinstance(item, ParsedVIServerReference):
            base, uid = _handle_base_and_suffix(item.handle)
            registry[item.handle] = HandleTarget(name=base, uid=uid, is_constant=False)
        elif isinstance(item, ParsedLocalVariable):
            # Same base+uid derivation as a plain node (no ``component`` to
            # feed ``_derive_instance_name`` with -- a local-variable's own
            # handle base already IS the tapped control's display name, see
            # ``_render_lvnet_local_variable``, so the handle's own text is
            # enough).
            base, uid = _handle_base_and_suffix(item.handle)
            registry[item.handle] = HandleTarget(name=base, uid=uid, is_constant=False)
        elif isinstance(item, ParsedGlobalVariable):
            # The handle's base is a despaced form of the control's name, so
            # the name comes from the line's own ``<Global VI>::<control>``.
            _, uid = _handle_base_and_suffix(item.handle)
            registry[item.handle] = HandleTarget(
                name=item.control, uid=uid, is_constant=False
            )
        elif isinstance(item, ParsedConstant):
            base, uid = _handle_base_and_suffix(item.handle)
            registry[item.handle] = HandleTarget(name=base, uid=uid, is_constant=True)
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
# lvnet_literal_token)
# ============================================================


def _fill_enum_values(lv: LVType, body: str) -> None:
    body = body.strip()
    if not body or body == "?":
        lv.values = None
        return
    values: dict[str, EnumValue] = {}
    for part in _split_top_level_commas(body):
        name, _, ordinal_text = part.rpartition(_LVNET_DRIVER_OP)
        values[_unquote_name_token(name)] = EnumValue(value=int(ordinal_text.strip()))
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
        sep_pos = _find_first_top_level(part, _LVNET_TYPE_SEP)
        name = part[:sep_pos] if sep_pos != -1 else part
        type_text = part[sep_pos + len(_LVNET_TYPE_SEP) :] if sep_pos != -1 else ""
        field_type = _reconstruct_type_ref(type_text.strip(), types_dict, memo)
        fields.append(ClusterField(name=_unquote_name_token(name), type=field_type))
    lv.fields = fields


def _reconstruct_named_type(
    name: str, types_dict: dict[str, ParsedTypeDef], memo: dict[str, LVType]
) -> LVType:
    """Build the ONE reconstructed ``LVType`` for a ``types :`` footnote
    NAME (memoized, cycle-safe -- the in-progress placeholder is stashed in
    ``memo`` before recursing into its own fields/members). Every real
    footnote entry's def text always starts with ``Enum{``/``Ring{``/
    ``Cluster{`` -- ``lvnet_named_stem`` (the module side) only ever
    contributes a footnote name for those three ``LVType`` kinds, and each
    one's OWN ``lvnet_type_lossless_def`` dispatch is keyed on that same
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
    """The reverse of ``render_lvnet.lvnet_type_ref``/``lvnet_type_lossless_def``:
    one type-reference TEXT (an inline terminal label, or a nested
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
    -- while ``lvnet_type_lossless_def`` (the NESTED/footnote render path)
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
        event_name, inner = _split_event_name(text[refnum_idx + len(" refnum{") : -1])
        elem = _reconstruct_type_ref(inner, types_dict, memo)
        return LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type="Refnum",
            ref_type=ref_type,
            element_type=elem,
            user_event_name=event_name,
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
_REVERSE_TUNNEL_MODE_WORD: dict[str, TunnelMode] = {
    word: mode for mode, word in _LVNET_TUNNEL_MODE_WORD.items()
}
_REVERSE_DISABLE_KEYWORD: dict[str, DisableStructureKind] = {
    word: kind for kind, word in _LVNET_DISABLE_KEYWORD.items()
}


def _reconstruct_local_variable(
    item: ParsedLocalVariable, registry: dict[str, HandleTarget]
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
        source = parse_source_token(item.source, registry)
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


def _reconstruct_vi_server_reference(
    item: ParsedVIServerReference, registry: dict[str, HandleTarget]
) -> NetlistInstance:
    """The reverse of ``_render_lvnet_vi_server_reference``: a SOURCE with one
    output, like a local-variable read, that a later ``<handle>::<port>``
    reference resolves through ``producer_uid``."""
    target = registry[item.handle]
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
        kind=NetlistInstanceKind.VI_SERVER_REFERENCE,
        reference=item.target,
    )


def _reconstruct_global_variable(
    item: ParsedGlobalVariable, registry: dict[str, HandleTarget]
) -> NetlistInstance:
    """The reverse of ``_render_lvnet_global_variable``: a ``read`` becomes a
    SOURCE (one output, no inputs), a ``write`` a SINK (one input driven by the
    parsed ``source``, no outputs) -- exactly as ``_reconstruct_local_variable``,
    plus the Global VI and control the line names. The terminal type is the
    honest ``"?"`` (not in the text)."""
    target = registry[item.handle]
    if item.is_write:
        assert item.source is not None
        source = parse_source_token(item.source, registry)
        net = source if isinstance(source, NetRef) else None
        default = source if isinstance(source, DefaultValue) else None
        binding = NetlistTerminalBinding(
            terminal=item.control,
            type="?",
            net=net,
            default=default,
            inverted=False,
            pane_rank=0,
            lv_type=None,
        )
        return NetlistInstance(
            uid=target.uid,
            name=item.control,
            occurrence=None,
            inputs=[binding],
            outputs=[],
            kind=NetlistInstanceKind.GLOBAL_VARIABLE,
            global_vi=item.global_vi,
            access="write",
        )
    ref = NetRef(
        node=item.control,
        terminal=item.control,
        occurrence=None,
        bare=f"{item.handle}{_LVNET_TERMINAL_SEP}{item.control}",
        producer_uid=target.uid,
    )
    return NetlistInstance(
        uid=target.uid,
        name=item.control,
        occurrence=None,
        inputs=[],
        outputs=[NetlistOutput(net=ref, type="?", pane_rank=0, lv_type=None)],
        kind=NetlistInstanceKind.GLOBAL_VARIABLE,
        global_vi=item.global_vi,
        access="read",
    )


def _reconstruct_instance(
    item: ParsedNode,
    registry: dict[str, HandleTarget],
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
    access: str | None = None
    library: str | None = None
    network_path: str | None = None
    library_unreadable: str | None = None
    if kind_enum is NetlistInstanceKind.SHARED_VARIABLE:
        shared = split_shared_variable_component(item.component)
        library, access = shared.library, shared.access
        network_path = shared.network_path
        library_unreadable = shared.library_unreadable
    elif kind_enum is NetlistInstanceKind.SUBVI:
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
                net = require_netref(
                    parse_source_token(t.driver, registry),
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
                NetlistOutput(
                    net=ref,
                    type=t.type,
                    pane_rank=out_rank,
                    lv_type=lv_type,
                )
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
        access=access,
        library=library,
        network_path=network_path,
        library_unreadable=library_unreadable,
    )


def _reconstruct_constant(
    item: ParsedConstant, registry: dict[str, HandleTarget]
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


def _uninitialized_register() -> DefaultValue:
    """The ``init`` of a register whose initializer is unwired: it takes the
    value it held when the VI last ran, so the text names no value."""
    return DefaultValue(literal="?", type_descriptor="?")


def _reconstruct_feedback(
    item: ParsedFeedback, registry: dict[str, HandleTarget], fresh_uid: _UidSource
) -> NetlistFeedback:
    depth = item.attribute.split(", ")[0]
    if depth == "? iterations":
        delay = None
    else:
        delay = int(depth.split()[0])
    init = (
        _uninitialized_register()
        if item.init is None
        else parse_source_token(item.init, registry)
    )
    recur = (
        require_netref(parse_source_token(item.each, registry), f"{item.net} each")
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
    sr: ParsedShiftRegister, registry: dict[str, HandleTarget]
) -> MuMerge:
    init = (
        _uninitialized_register()
        if sr.init is None
        else parse_source_token(sr.init, registry)
    )
    recur = (
        require_netref(parse_source_token(sr.each, registry), f"{sr.net} each")
        if sr.each is not None
        else None
    )
    return MuMerge(net=sr.net, init=init, recur=recur)


def _reconstruct_tunnel(
    t: ParsedTunnel, registry: dict[str, HandleTarget]
) -> EtaMerge:
    mode_text = t.mode
    conditional = False
    if mode_text.endswith("+conditional"):
        conditional = True
        mode_text = mode_text[: -len("+conditional")]
    mode = _REVERSE_TUNNEL_MODE_WORD.get(mode_text)
    if mode is None:
        raise LvnetReconstructError(f"unrecognized tunnel mode word: {t.mode!r}")
    value = parse_source_token(t.source, registry)
    condition = (
        require_netref(
            parse_source_token(t.condition, registry), f"{t.net} condition"
        )
        if t.condition is not None
        else None
    )
    return EtaMerge(
        net=t.net,
        mode=mode,
        conditional=conditional,
        value=value,
        condition=condition,
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
    registry: dict[str, HandleTarget],
    types_dict: dict[str, ParsedTypeDef],
    memo: dict[str, LVType],
    fresh_uid: _UidSource,
) -> NetlistScope:
    def body_of(parsed_body: tuple[ParsedBodyItem, ...]) -> list[NetlistItem]:
        return _reconstruct_items(parsed_body, registry, types_dict, memo, fresh_uid)

    inputs = [structure_input(d, registry) for d in item.inputs]

    if item.kind == "case":
        frames = [
            NetlistFrame(
                label=f.label,
                value=f.label,
                is_default=f.is_default,
                body=body_of(f.body),
                values=f.values,
            )
            for f in item.frames
        ]
        # The builder keys a frame's output by "default" if is_default else its
        # label: both sides must agree, or a default frame's case is dropped
        # (two frames both labeled "Error", only one the default).
        outputs = case_outputs(
            item.frames,
            ["default" if f.is_default else f.label for f in frames],
            registry,
        )
        selector = None
        if item.selector is not None and item.selector != "?":
            selector = require_netref(
                parse_source_token(item.selector, registry), "case selector"
            )
        case_uid = item.uid
        if case_uid is None:
            case_uid = _first_structure_uid(outputs)
        return NetlistScope(
            uid=case_uid if case_uid is not None else fresh_uid.next("case"),
            kind="case",
            selector=selector,
            frames=frames,
            outputs=outputs,
            inputs=inputs,
        )

    if item.kind in ("for-loop", "while-loop"):
        kind = "while" if item.kind == "while-loop" else "for"
        frame = NetlistFrame(
            label="", value="", is_default=False, body=body_of(item.body)
        )
        mu_list = [
            _reconstruct_shift_register(sr, registry) for sr in item.shift_registers
        ]
        eta_list = [_reconstruct_tunnel(t, registry) for t in item.tunnels]
        condition_mode = (
            LoopCondition(item.condition_mode)
            if item.condition_mode is not None
            else None
        )
        wires = LoopWires(item, registry)
        if (condition_mode is not None) != wires.has_condition:
            raise LvnetReconstructError(
                f"a {item.kind} with a conditional terminal needs exactly one "
                f"'{_LVNET_LOOP_CONDITION_TERMINAL}' line, and one without has none"
            )
        loop_uid = item.uid
        if loop_uid is None:
            loop_uid = (
                _first_structure_uid([*mu_list, *eta_list]) or wires.structure_uid
            )
        return NetlistScope(
            uid=loop_uid if loop_uid is not None else fresh_uid.next("loop"),
            kind=kind,
            selector=wires.selector,
            frames=[frame],
            outputs=[*mu_list, *eta_list],
            inputs=wires.inputs,
            condition_mode=condition_mode,
            count=wires.count,
        )

    if item.kind in ("flat-sequence", "stacked-sequence"):
        values = [_sequence_frame_value(f.label) for f in item.frames]
        frames = [
            NetlistFrame(label=v, value=v, is_default=False, body=body_of(f.body))
            for f, v in zip(item.frames, values, strict=True)
        ]
        outputs = sequence_outputs(item.frames, values, registry)
        sequence_uid = item.uid or _first_structure_uid(outputs)
        return NetlistScope(
            uid=sequence_uid
            if sequence_uid is not None
            else fresh_uid.next("sequence"),
            kind="sequence",
            selector=None,
            frames=frames,
            outputs=outputs,
            sequence_is_flat=(item.kind == "flat-sequence"),
            inputs=inputs,
        )

    if item.kind in ("diagram-disable", "conditional-disable", "type-specialization"):
        disable_kind = _REVERSE_DISABLE_KEYWORD.get(item.kind)
        if disable_kind is None:
            raise LvnetReconstructError(f"unrecognized disable kind: {item.kind!r}")
        labels = [disabled_frame_value(f.label, disable_kind) for f in item.frames]
        frames = [
            NetlistFrame(
                label=label,
                value=label,
                is_default=f.is_default,
                body=body_of(f.body),
            )
            for f, label in zip(item.frames, labels, strict=True)
        ]
        return NetlistScope(
            uid=item.uid if item.uid is not None else fresh_uid.next("disabled"),
            kind="disabled",
            selector=None,
            frames=frames,
            disable_kind=disable_kind,
            outputs=case_outputs(
                item.frames,
                ["default" if f.is_default else f.label for f in frames],
                registry,
            ),
            inputs=inputs,
        )

    if item.kind == "event-structure":
        labels = [_unescape_lvnet_string(f.label) for f in item.frames]
        frames = [
            NetlistFrame(
                label=label, value=label, is_default=False, body=body_of(f.body)
            )
            for f, label in zip(item.frames, labels, strict=True)
        ]
        return NetlistScope(
            uid=item.uid if item.uid is not None else fresh_uid.next("event"),
            kind="event",
            selector=None,
            frames=frames,
            outputs=event_outputs(item.frames, labels, registry),
            inputs=inputs,
        )

    if item.kind == _LVNET_INPLACE_SCOPE_KEYWORD:
        # A single implicit body (like a loop) plus one drive per
        # ``inplace_<uid>::put<k> = <source>`` line. The header's verbose
        # ``(id <uid>)`` suffix is the preferred uid source; a drive net
        # spells it too, and a fresh uid is the honest fallback when the text
        # was terse and drove nothing.
        frame = NetlistFrame(
            label="", value="", is_default=False, body=body_of(item.body)
        )
        border_drives = [
            InPlaceBorderDrive(
                net=d.net,
                value=require_netref(
                    parse_source_token(d.source, registry), f"{d.net} drive"
                ),
            )
            for d in item.drives
        ]
        inplace_uid = item.uid or _first_structure_uid(border_drives)
        return NetlistScope(
            uid=inplace_uid if inplace_uid is not None else fresh_uid.next("inplace"),
            kind="inplace",
            selector=None,
            frames=[frame],
            outputs=[*border_drives],
        )

    raise LvnetReconstructError(f"unrecognized scope kind: {item.kind!r}")


def _sequence_frame_value(label: str) -> str:
    """A Sequence frame's value from its ``[i]`` header label."""
    if label.startswith("[") and label.endswith("]"):
        return label[1:-1]
    return label


def _first_structure_uid(nets: Sequence[object]) -> str | None:
    """The structure uid the first net with one spells (a defensive fallback for
    text that predates the header's ``(id <uid>)``)."""
    for entry in nets:
        net = getattr(entry, "net", None)
        if isinstance(net, str) and (uid := _structure_uid_from_net(net)) is not None:
            return uid
    return None


def _check_terminal_line_order(items: tuple[ParsedBodyItem, ...]) -> None:
    """A body writes its ``read`` lines before everything else and its indicator
    drive lines after everything else."""
    stage = 0  # 0: reads, 1: the rest, 2: indicator drives
    for item in items:
        here = (
            0
            if isinstance(item, ParsedControlRead)
            else 2
            if isinstance(item, ParsedIndicatorDrive)
            else 1
        )
        if here < stage:
            line = "'read <Control>'" if here == 0 else "an item"
            after = "an indicator drive" if stage == 2 else "another item"
            raise LvnetReconstructError(
                f"{line} comes after {after}: a body writes its 'read' lines "
                f"first and its indicator drives last"
            )
        stage = max(stage, here)


def _reconstruct_items(
    items: tuple[ParsedBodyItem, ...],
    registry: dict[str, HandleTarget],
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
        elif isinstance(item, ParsedVIServerReference):
            out.append(_reconstruct_vi_server_reference(item, registry))
        elif isinstance(item, ParsedGlobalVariable):
            out.append(_reconstruct_global_variable(item, registry))
        elif isinstance(item, ParsedConstant):
            out.append(_reconstruct_constant(item, registry))
        elif isinstance(item, ParsedFeedback):
            out.append(_reconstruct_feedback(item, registry, fresh_uid))
        elif isinstance(item, ParsedScope):
            out.append(_reconstruct_scope(item, registry, types_dict, memo, fresh_uid))
        elif isinstance(item, ParsedIndicatorDrive):
            out.append(
                NetlistIndicatorDrive(
                    indicator_drive_output(item, registry, placed=True)
                )
            )
        elif isinstance(item, ParsedControlRead):
            out.append(NetlistControlRead(item.name, item.uid))
        else:  # pragma: no cover -- exhaustive over ParsedBodyItem
            raise LvnetReconstructError(f"unrecognized parsed body item: {item!r}")
    _check_terminal_line_order(items)
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
        unreadable=d.unreadable,
    )


def _pane_terminal_of_row(
    t: ParsedBoundaryTerminal,
    types_dict: dict[str, ParsedTypeDef],
    memo: dict[str, LVType],
) -> ConnectorPaneTerminal:
    """The connector-pane terminal one ``front-panel :`` row says."""
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
        default_scalar = parse_lvnet_literal(t.default)
    return ConnectorPaneTerminal(
        name=t.name,
        type=t.type,
        direction="input" if t.direction == "in" else "output",
        index=t.index,
        wiring_requirement=requirement,
        default=default_scalar,
        lv_type=_maybe_attach_lvtype(t.type, types_dict, memo),
        caption=t.caption,
        caption_visible=t.caption_visible,
        terminal_uid=t.uid,
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

    registry: dict[str, HandleTarget] = {}
    _index_handles(parsed.body, registry)

    for terminal in parsed.boundary:
        token = lvnet_control_name_token(terminal.name)
        target = HandleTarget(
            name=terminal.name,
            uid=terminal.uid or "",
            is_constant=False,
            is_boundary=True,
        )
        registry.setdefault(token, target)
        if terminal.uid is not None:
            registry[token + lvnet_id_suffix(terminal.uid)] = target

    check_shared_references(parsed, registry)
    body = _reconstruct_items(parsed.body, registry, types_dict, memo, fresh_uid)
    mark_read_outputs(body, referenced_terminals(parsed, registry))

    # A boundary line with NO ``@<index>`` is an OFF-PANE front-panel
    # control/indicator: a ``connector_pane.terminals`` entry only (an off-pane
    # indicator something drives also has an indicator drive line).
    panes = [(t, _pane_terminal_of_row(t, types_dict, memo)) for t in parsed.boundary]

    def rows_of(
        direction: str, *, on_pane: bool
    ) -> list[tuple[ParsedBoundaryTerminal, ConnectorPaneTerminal]]:
        return [
            (t, p)
            for t, p in panes
            if t.direction == direction and (t.index is not None) == on_pane
        ]

    on_pane_inputs = rows_of("in", on_pane=True)
    on_pane_outputs = rows_of("out", on_pane=True)
    off_pane_outputs_rows = rows_of("out", on_pane=False)

    check_control_reads(
        [t for t in parsed.boundary if t.direction == "in"], control_reads(body)
    )
    # Every indicator drive line, in document order: the ones inside a
    # structure, then the ones at the end of the diagram.
    drives = [
        *placed_drives(body),
        *(
            indicator_drive_output(d, registry, placed=False)
            for d in parsed.output_drives
        ),
    ]
    claimed = claim_indicator_drives(
        [t for t, _ in (*on_pane_outputs, *off_pane_outputs_rows)], drives
    )
    on_pane_claims = claimed[: len(on_pane_outputs)]
    off_pane_claims = claimed[len(on_pane_outputs) :]

    outputs: list[BoundaryOutput] = []
    for (t, pane), drive in zip(on_pane_outputs, on_pane_claims, strict=True):
        if drive is None:
            raise LvnetReconstructError(
                f"output {t.name!r} has no drive line (§2): write "
                f"'{lvnet_control_name_token(t.name)} = <source>' or '= ?'"
            )
        outputs.append(_complete_indicator(drive, pane))
    off_pane_outputs = [
        _complete_indicator(drive, pane)
        for (t, pane), drive in zip(off_pane_outputs_rows, off_pane_claims, strict=True)
        if drive is not None
    ]
    # A shared label is only unambiguous when every holder has its drive line.
    row_counts = Counter(t.name for t, _ in off_pane_outputs_rows)
    claimed_counts = Counter(out.name for out in off_pane_outputs)
    for name, rows in row_counts.items():
        if rows > 1 and claimed_counts[name] != rows:
            raise LvnetReconstructError(
                f"{rows} off-pane indicators are labelled {name!r} but "
                f"{claimed_counts[name]} drive line(s) name it: a drive line "
                f"cannot be matched to one of them"
            )

    inputs = [
        NetlistBoundaryInput(
            name=pane.name,
            type_descriptor=pane.type,
            lv_type=pane.lv_type,
            caption=pane.caption,
            caption_visible=pane.caption_visible,
            terminal_uid=pane.terminal_uid,
        )
        for _, pane in on_pane_inputs
    ]
    dependencies = [_reconstruct_dependency(d, types_dict, memo) for d in parsed.uses]

    return NetlistModule(
        vi_name=parsed.vi_name,
        inputs=inputs,
        outputs=outputs,
        off_pane_outputs=off_pane_outputs,
        body=body,
        connector_pane=ConnectorPane(
            pattern_id=parsed.pattern_id, terminals=[p for _, p in panes]
        ),
        dependencies=dependencies,
    )


def _complete_indicator(
    drive: BoundaryOutput, pane: ConnectorPaneTerminal
) -> BoundaryOutput:
    """The drive line's indicator with the type and caption of its row."""
    drive.type_descriptor = pane.type
    drive.lv_type = pane.lv_type
    drive.caption = pane.caption
    drive.caption_visible = pane.caption_visible
    drive.terminal_uid = pane.terminal_uid
    return drive
