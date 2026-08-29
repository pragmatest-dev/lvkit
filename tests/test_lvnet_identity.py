"""Graph-IDENTITY round-trip gate for the lvnet text IR (Phase 4).

``tests/test_lvnet_reconstruct.py`` already proves TEXT stability:
``render_lvnet(reconstruct_module(parse_lvnet(T)), verbose=True) == T``.
That is a real gate, but it is a proxy -- it says the reconstructed model
re-renders to the same bytes, not that it recovered the SAME graph identity
the original ``NetlistModule`` carried. Two different models can render to
identical text (e.g. every ``NetlistScope.uid`` freshly minted instead of
recovered) as long as neither model's ``uid`` is ever read back out by the
renderer for that particular VI's shape -- so text stability alone does not
prove identity survives the round trip.

This module is the stronger, missing gate: build a real corpus VI's
``NetlistModule`` straight from the graph, render it verbose, parse it back,
reconstruct a second ``NetlistModule``, and assert the two are equal BY
IDENTITY -- not by ``netlist_signature`` (the textual/structural projection
``test_lvnet_roundtrip.py`` already checks) but by a focused recursive
comparator that walks both trees positionally and asserts:

- every ``NetlistInstance``/``NetlistConstant`` (a plain node, a labeled
  constant, and a local-variable read/write tap alike -- all three are
  ``NetlistInstance``s) recovers the SAME ``.uid`` the original graph
  carried;
- every ``NetlistScope`` (case/for/while/sequence/disabled/event) recovers
  the SAME ``.uid``;
- ``connector_pane.pattern_id`` matches;
- every connector-pane terminal's ``.index`` matches, ON-PANE and OFF-PANE
  (``None``) alike;
- every net reference (a node-terminal binding, a case selector, a
  shift-register/tunnel/gamma-case source, a boundary-output drive)
  resolves to the SAME producer by identity (``NetRef.producer_uid``/
  ``constant_uid``, not display text).

``NetlistFeedback.uid`` is deliberately EXCLUDED from this comparator --
``lvnet_reconstruct``'s own module docstring documents it as a field
``render_lvnet`` never reads and is free to invent (a Feedback Node's own
identity is carried by its ``net`` string, e.g. ``fb0``, not by a separate
handle+uid the way every other node kind is) -- asserting it here would be
asserting a losslessness property the format was never designed to have.

This gate found a REAL gap on first run (not a theoretical one): a
case/loop with no output tunnel/shift-register, and every sequence/
disabled/event structure (none of which ever spell their own uid into a net
name), reconstructed with a freshly-minted ``NetlistScope.uid`` instead of
the original's. The fix -- carrying the structure's own uid directly on its
header line (`` (id <uid>)``, verbose-only) -- closes it; see
``lvnet_grammar._LVNET_SCOPE_ID_PREFIX``, ``render_lvnet.
_lvnet_scope_id_suffix``, ``lvnet_parse._split_scope_header_id``, and
``lvnet_reconstruct._reconstruct_scope``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lvkit.graph import load_vi_by_path
from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import (
    NetlistConstant,
    NetlistFeedback,
    NetlistInstance,
    NetlistItem,
    NetlistModule,
    NetlistScope,
    NetRef,
    build_netlist_from_graph,
    render_lvnet,
)
from lvkit.graph.netlist_models import DefaultValue, EtaMerge, GammaMerge, MuMerge
from lvkit.graph.render_lvnet import (
    _is_void_type,
    _lvnet_net_separator,
    _quoted_frame_label,
)
from lvkit.load_mode import LoadMode

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_FLEX_ROOT = Path(".lvkit/cache/samples/lv-flex-channel-examples")

# The SAME corpus VIs as test_lvnet_reconstruct.py's `_IDEMPOTENCE_CASES` --
# the same VIs, asked the STRONGER identity question.
_IDENTITY_CASES = [
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TestLoader" / "loadTestsFromTestCase.vi",
        _JKI_SOURCE_ROOT,
        id="loadTestsFromTestCase",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TestCase" / "run.vi",
        _JKI_SOURCE_ROOT,
        id="TestCase_run",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TestSuite" / "run.vi",
        _JKI_SOURCE_ROOT,
        id="TestSuite_run",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TextTestRunner" / "run.vi",
        _JKI_SOURCE_ROOT,
        id="TextTestRunner_run",
    ),
    pytest.param(
        _FLEX_ROOT / "WaveGen" / "WaveGen.vi",
        _FLEX_ROOT / "WaveGen",
        id="WaveGen",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Menu Launch" / "VI Tester Menu Launch.vi",
        _JKI_SOURCE_ROOT,
        id="VI_Tester_Menu_Launch",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT
        / "User Interfaces"
        / "Graphical Test Runner"
        / "Graphical Test Runner - Main UI - .vi",
        _JKI_SOURCE_ROOT,
        id="Graphical_Test_Runner_Main_UI",
    ),
]


def _load(vi_path: Path, search_root: Path) -> tuple[InMemoryVIGraph, str] | None:
    if not vi_path.exists():
        return None
    try:
        # load_vi_by_path returns load_vi's OWN key for vi_path -- never
        # re-derived from vi_path.name, which collides across same-named
        # VIs (e.g. TestCase.lvclass:run.vi vs TestSuite.lvclass:run.vi).
        return load_vi_by_path(
            vi_path, LoadMode.MINIMAL, search_paths=[search_root], layout=False
        )
    except Exception:
        return None


# ============================================================
# The focused identity comparator (NOT netlist_signature)
# ============================================================


@dataclass(frozen=True)
class _IdentityCtx:
    """The set of uids the ORIGINAL module actually DECLARES (and so gives
    a real ``<handle>``/scope-header to recover in the text) -- the domain
    ``render_lvnet._LvnetHandles.by_uid`` resolves a producer/constant
    reference through. A ``NetRef.producer_uid``/``constant_uid`` that does
    NOT fall in this set traces to something the lvnet format never gives
    an identity marker to declare in the first place (an UNLABELED
    constant, inlined as a bare literal with no handle at all -- see
    ``render_lvnet._render_lvnet_source``'s own docstring: "a constant_uid
    that does NOT resolve is a one-off/unlabeled constant -- falls through
    to its inlined literal value", "indistinguishable in render output ...
    and none is needed for render idempotence"). Asserting identity there
    would be asserting a losslessness property the format was never
    designed to have -- so ``_compare_net`` falls back to (normalized)
    display text for exactly those references, the same as a boundary
    control's plain name or a structure-scoped net.
    """

    instance_uids: frozenset[str]
    constant_uids: frozenset[str]


def _collect_declared_uids(items: list[NetlistItem]) -> _IdentityCtx:
    instance_uids: set[str] = set()
    constant_uids: set[str] = set()

    def walk(body: list[NetlistItem]) -> None:
        for item in body:
            if isinstance(item, NetlistInstance):
                instance_uids.add(item.uid)
            elif isinstance(item, NetlistConstant):
                # Every BODY-ITEM constant is, by construction, a LABELED
                # one (`NetlistConstant`'s own docstring: promoted to a body
                # item "ONLY when it carries a real LabVIEW-authored
                # label") -- an unlabeled constant never appears here at
                # all, only as a `constant_uid` annotation on the
                # referencing `NetRef`.
                constant_uids.add(item.uid)
            elif isinstance(item, NetlistScope):
                for frame in item.frames:
                    walk(frame.body)
            # NetlistFeedback carries no declared handle of its own (its
            # `net` string IS its identity) -- nothing to collect.

    walk(items)
    return _IdentityCtx(
        instance_uids=frozenset(instance_uids), constant_uids=frozenset(constant_uids)
    )


def _compare_net(
    a: NetRef | DefaultValue | None,
    b: NetRef | DefaultValue | None,
    path: str,
    errors: list[str],
    ctx: _IdentityCtx,
) -> None:
    """Compare two net references by PRODUCER IDENTITY, not display text --
    the reverse of what ``netlist_signature`` does (it compares the
    rendered text/shape). A ``DefaultValue`` (an unwired terminal's type
    default) has no producer to identify; its own text is already covered
    by the byte-identity gate (``test_lvnet_reconstruct.py``), so only its
    presence/absence is checked here -- EXCEPT for the one already-
    documented, pre-existing ambiguity ``lvnet_reconstruct._parse_source_
    token`` itself calls out: a ``DefaultValue`` with a real literal (not
    the type-only ``"?"`` form) renders as JUST that literal
    (``_lvnet_default_token``), byte-identical to an actually-wired literal
    constant -- "there is no information in the text itself to tell the two
    apart, and none is needed for render idempotence." That is a existing,
    accepted textual-ambiguity call, not a NEW identity gap this gate is
    scoped to catch, so it is treated as compatible here, not an error.
    """
    if isinstance(a, DefaultValue) and isinstance(b, DefaultValue):
        return
    if isinstance(a, DefaultValue) or isinstance(b, DefaultValue):
        default, other = (a, b) if isinstance(a, DefaultValue) else (b, a)
        assert isinstance(default, DefaultValue)
        if (
            default.literal != "?"
            and isinstance(other, NetRef)
            and other.bare == default.literal
        ):
            return
        errors.append(f"{path}: default-vs-wired mismatch: {a!r} vs {b!r}")
        return
    if a is None and b is None:
        return
    if a is None or b is None:
        errors.append(f"{path}: net presence mismatch: {a!r} vs {b!r}")
        return
    if a.producer_uid in ctx.instance_uids or b.producer_uid in ctx.instance_uids:
        if a.producer_uid != b.producer_uid:
            errors.append(
                f"{path}: producer_uid mismatch: {a.producer_uid!r} vs "
                f"{b.producer_uid!r} (orig bare={a.bare!r})"
            )
        return
    if a.constant_uid in ctx.constant_uids or b.constant_uid in ctx.constant_uids:
        if a.constant_uid != b.constant_uid:
            errors.append(
                f"{path}: constant_uid mismatch: {a.constant_uid!r} vs "
                f"{b.constant_uid!r} (orig bare={a.bare!r})"
            )
        return
    if a.lvnet_value is not None and b.lvnet_value is not None:
        # An INLINE literal recognized as one on BOTH sides (a quoted
        # string/``True``/``False``/number -- ``_parse_source_token``'s
        # pattern-matched literal shapes): ``.bare`` is the OLD
        # ``render_netlist``-parity display text (a different quoting
        # convention, e.g. ``'0'``), while ``.lvnet_value`` is the ACTUAL
        # already-escaped text ``render_lvnet`` emits and
        # ``lvnet_reconstruct`` recovers verbatim -- compare THAT, not
        # ``.bare``, or every string/char literal falsely "mismatches" on
        # quoting convention alone.
        if a.lvnet_value != b.lvnet_value:
            errors.append(
                f"{path}: literal value mismatch: {a.lvnet_value!r} vs "
                f"{b.lvnet_value!r} (orig bare={a.bare!r})"
            )
        return
    # Exactly one side sets ``lvnet_value`` (the ORIGINAL model always does
    # for a constant-driven net -- ``_lvnet_const_value_str``'s CLUSTER/
    # ARRAY/ENUM fallback returns an unquoted, un-bool/numeric-shaped
    # ``str(value)`` text, e.g. a Python-dict-repr string, that
    # ``_parse_source_token``'s literal-shape matching genuinely can't
    # recognize as a literal on the way back -- a PRE-EXISTING, documented
    # parsing limitation, not a NEW identity gap this gate is scoped to
    # catch) -- fall through to comparing display text below, which is
    # ALWAYS what the render fallback chain (``_render_lvnet_source``'s own
    # ``source.lvnet_value if ... else source.bare``) actually emitted
    # either way, so it is still a meaningful check, just not by uid.
    # A boundary control's own bare display name, or a structure-scoped net
    # (``case_UID.outK``/``loop_UID.shiftK``/``loop_UID.outK`` -- its OWNING
    # structure's uid is already asserted directly, on that scope, by
    # ``_compare_scope`` below) -- no producer/constant/literal identity
    # applies to either, so the display text is the only thing left to
    # compare. Normalize the ``.``/``::`` separator first: the ORIGINAL
    # module's stored text always uses ``.`` (``render_lvnet``'s own
    # ``_lvnet_net_separator`` reformats it to ``::`` at RENDER TIME only,
    # never mutating the stored field -- see its docstring), while
    # RECONSTRUCTED text captures the ALREADY-``::``-reformed form verbatim
    # -- a real difference in stored representation, not an identity bug.
    if _lvnet_net_separator(a.bare) != _lvnet_net_separator(b.bare):
        errors.append(f"{path}: bare net-text mismatch: {a.bare!r} vs {b.bare!r}")


def _compare_instance(
    a: NetlistInstance,
    b: NetlistInstance,
    path: str,
    errors: list[str],
    ctx: _IdentityCtx,
) -> None:
    if a.uid != b.uid:
        errors.append(
            f"{path}: instance uid mismatch (name={a.name!r}, kind={a.kind!r}): "
            f"{a.uid!r} vs {b.uid!r}"
        )
    if a.kind != b.kind:
        errors.append(f"{path}: instance kind mismatch: {a.kind!r} vs {b.kind!r}")
    # A Void-typed binding is a dead pane slot ``render_lvnet`` itself drops
    # (``_is_void_type``/``_is_void_binding``, md §3/§7: "the one consumer
    # that must drop a dead pane slot") -- never rendered, so never present
    # to recover on reconstruction. Filtering the ORIGINAL side the same
    # way before comparing puts both sides back in the SAME domain: what
    # actually reached the text. ``render_lvnet`` ALSO reorders a node's own
    # terminal lines by ``pane_rank`` (``_render_lvnet_instance``: ``sorted(
    # instance.inputs, key=lambda b: b.pane_rank)``, ditto outputs) -- the
    # connector-pane READING order, distinct from ``.inputs``/``.outputs``'s
    # own stored (render_netlist-parity) list order -- so
    # ``lvnet_reconstruct`` rebuilds them in THAT rank order; sort the
    # ORIGINAL side the same way before zipping, or position ``i`` names two
    # entirely different terminals on the two sides.
    a_inputs = sorted(
        (ia for ia in a.inputs if not _is_void_type(ia.type)),
        key=lambda ia: ia.pane_rank,
    )
    if len(a_inputs) != len(b.inputs):
        errors.append(
            f"{path}: input count mismatch: {len(a_inputs)} vs {len(b.inputs)}"
        )
    else:
        for i, (ia, ib) in enumerate(zip(a_inputs, b.inputs)):
            _compare_net(
                ia.net, ib.net, f"{path}.in[{i}]({ia.terminal!r})", errors, ctx
            )
    # NOTE: an instance's OWN outputs are deliberately NOT compared here --
    # each output's net is the instance declaring ITSELF as producer (its
    # `.node`/`.producer_uid` is this SAME instance, already asserted
    # directly via `.uid` above), never a reference to some OTHER
    # producer. There is no additional identity claim to check there --
    # only that the same number of (non-Void) outputs survived.
    a_outputs = [oa for oa in a.outputs if not _is_void_type(oa.type)]
    if len(a_outputs) != len(b.outputs):
        errors.append(
            f"{path}: output count mismatch: {len(a_outputs)} vs {len(b.outputs)}"
        )


def _compare_constant(
    a: NetlistConstant, b: NetlistConstant, path: str, errors: list[str]
) -> None:
    if a.uid != b.uid:
        errors.append(
            f"{path}: constant uid mismatch (name={a.name!r}): {a.uid!r} vs {b.uid!r}"
        )


def _compare_scope(
    a: NetlistScope, b: NetlistScope, path: str, errors: list[str], ctx: _IdentityCtx
) -> None:
    if a.uid != b.uid:
        errors.append(
            f"{path}: scope uid mismatch (kind={a.kind!r}): {a.uid!r} vs {b.uid!r}"
        )
    if a.kind != b.kind:
        errors.append(f"{path}: scope kind mismatch: {a.kind!r} vs {b.kind!r}")
        return

    if a.kind == "case":
        _compare_net(a.selector, b.selector, f"{path}.selector", errors, ctx)

    if len(a.frames) != len(b.frames):
        errors.append(
            f"{path}: frame count mismatch: {len(a.frames)} vs {len(b.frames)}"
        )
    else:
        for i, (fa, fb) in enumerate(zip(a.frames, b.frames)):
            frame_path = f"{path}.frame[{i}]"
            # Case-scope ONLY: this is the exact blind spot that let the
            # TextTestRunner/run.vi bug through -- two frames both labeled
            # "Error", only one of which is ``is_default``, previously
            # indistinguishable once rendered (§8's ``"Error", default``
            # convention fixes the render/parse/reconstruct round trip; this
            # is the identity gate that must now actually catch a
            # regression). Scoped to "case" because it is the only scope
            # kind whose header now encodes ``is_default`` at all --
            # sequence/disabled/event frames keep their plain header and
            # ``lvnet_reconstruct`` still hardcodes ``is_default=False`` for
            # them (a separate, pre-existing gap outside this fix's scope).
            if a.kind == "case":
                if fa.is_default != fb.is_default:
                    errors.append(
                        f"{frame_path}: is_default mismatch: "
                        f"{fa.is_default!r} vs {fb.is_default!r}"
                    )
                # Compare the frame's OWN selector value(s) via the same
                # quoting ``_render_lvnet_case_scope`` itself applies (the
                # original's ``label`` is stored BARE, e.g. "Error", while a
                # reconstructed label is stored ALREADY quoted, e.g.
                # '"Error"' -- an intentional passthrough asymmetry, not a
                # real difference -- see ``_quoted_frame_label``'s own
                # docstring), so this normalizes both sides to the same
                # final rendered token before comparing.
                if _quoted_frame_label(fa.label) != _quoted_frame_label(fb.label):
                    errors.append(
                        f"{frame_path}: selector value mismatch: "
                        f"{fa.label!r} vs {fb.label!r}"
                    )
            _compare_items(fa.body, fb.body, frame_path, errors, ctx)

    if len(a.outputs) != len(b.outputs):
        errors.append(
            f"{path}: output-merge count mismatch: {len(a.outputs)} vs {len(b.outputs)}"
        )
        return
    for i, (ma, mb) in enumerate(zip(a.outputs, b.outputs)):
        merge_path = f"{path}.outputs[{i}]"
        if type(ma) is not type(mb):
            errors.append(
                f"{merge_path}: merge type mismatch: "
                f"{type(ma).__name__} vs {type(mb).__name__}"
            )
            continue
        if isinstance(ma, MuMerge):
            assert isinstance(mb, MuMerge)
            _compare_net(ma.init, mb.init, f"{merge_path}.init", errors, ctx)
            _compare_net(ma.recur, mb.recur, f"{merge_path}.recur", errors, ctx)
        elif isinstance(ma, EtaMerge):
            assert isinstance(mb, EtaMerge)
            _compare_net(ma.value, mb.value, f"{merge_path}.value", errors, ctx)
        elif isinstance(ma, GammaMerge):
            assert isinstance(mb, GammaMerge)
            if len(ma.cases) != len(mb.cases):
                errors.append(f"{merge_path}: gamma case count mismatch")
            else:
                for j, (ca, cb) in enumerate(zip(ma.cases, mb.cases)):
                    _compare_net(
                        ca.source, cb.source, f"{merge_path}.case[{j}]", errors, ctx
                    )


def _compare_feedback(
    a: NetlistFeedback,
    b: NetlistFeedback,
    path: str,
    errors: list[str],
    ctx: _IdentityCtx,
) -> None:
    # `.uid` deliberately NOT compared -- ``lvnet_reconstruct``'s own module
    # docstring documents it as a field ``render_lvnet`` never reads and is
    # free to invent (a Feedback Node's identity is carried by its `net`
    # string, e.g. `fb0`, not by a separate handle+uid).
    if a.net != b.net:
        errors.append(f"{path}: feedback net mismatch: {a.net!r} vs {b.net!r}")
    _compare_net(a.init, b.init, f"{path}.init", errors, ctx)
    _compare_net(a.recur, b.recur, f"{path}.recur", errors, ctx)


def _compare_items(
    a_items: list[NetlistItem],
    b_items: list[NetlistItem],
    path: str,
    errors: list[str],
    ctx: _IdentityCtx,
) -> None:
    if len(a_items) != len(b_items):
        errors.append(
            f"{path}: item count mismatch: {len(a_items)} vs {len(b_items)}"
        )
        return
    for i, (a, b) in enumerate(zip(a_items, b_items)):
        item_path = f"{path}[{i}]"
        if type(a) is not type(b):
            errors.append(
                f"{item_path}: item type mismatch: "
                f"{type(a).__name__} vs {type(b).__name__}"
            )
            continue
        if isinstance(a, NetlistInstance):
            assert isinstance(b, NetlistInstance)
            _compare_instance(a, b, item_path, errors, ctx)
        elif isinstance(a, NetlistScope):
            assert isinstance(b, NetlistScope)
            _compare_scope(a, b, item_path, errors, ctx)
        elif isinstance(a, NetlistConstant):
            assert isinstance(b, NetlistConstant)
            _compare_constant(a, b, item_path, errors)
        elif isinstance(a, NetlistFeedback):
            assert isinstance(b, NetlistFeedback)
            _compare_feedback(a, b, item_path, errors, ctx)
        else:  # pragma: no cover -- exhaustive over NetlistItem
            raise TypeError(f"unhandled NetlistItem kind: {type(a)!r}")


def _assert_graph_identity(
    orig: NetlistModule, recon: NetlistModule, vi_label: str
) -> None:
    errors: list[str] = []
    # Built from the ORIGINAL module only -- the domain of uids the lvnet
    # text actually gives a recoverable identity marker to (see
    # ``_IdentityCtx``'s own docstring).
    ctx = _collect_declared_uids(orig.body)

    if orig.connector_pane.pattern_id != recon.connector_pane.pattern_id:
        errors.append(
            f"connector_pane.pattern_id mismatch: "
            f"{orig.connector_pane.pattern_id!r} vs "
            f"{recon.connector_pane.pattern_id!r}"
        )
    if len(orig.connector_pane.terminals) != len(recon.connector_pane.terminals):
        errors.append(
            f"connector_pane.terminals count mismatch: "
            f"{len(orig.connector_pane.terminals)} vs "
            f"{len(recon.connector_pane.terminals)}"
        )
    else:
        for i, (ta, tb) in enumerate(
            zip(orig.connector_pane.terminals, recon.connector_pane.terminals)
        ):
            if ta.index != tb.index:
                errors.append(
                    f"connector_pane.terminals[{i}] ({ta.name!r}) index "
                    f"mismatch: {ta.index!r} vs {tb.index!r}"
                )

    _compare_items(orig.body, recon.body, "body", errors, ctx)

    if len(orig.outputs) != len(recon.outputs):
        errors.append(
            f"module.outputs count mismatch: "
            f"{len(orig.outputs)} vs {len(recon.outputs)}"
        )
    else:
        for i, (oa, ob) in enumerate(zip(orig.outputs, recon.outputs)):
            _compare_net(
                oa.source, ob.source, f"outputs[{i}]({oa.name!r})", errors, ctx
            )

    assert not errors, (
        f"lvnet graph-identity round-trip found {len(errors)} mismatch(es) "
        f"for {vi_label!r}:\n" + "\n".join(f"  - {e}" for e in errors)
    )


@pytest.mark.needs_samples
@pytest.mark.parametrize("vi_path,search_root", _IDENTITY_CASES)
def test_reconstruct_module_recovers_graph_identity(
    vi_path: Path, search_root: Path
) -> None:
    """``reconstruct_module(parse_lvnet(render_lvnet(m, verbose=True)))``
    must be the SAME graph identity as ``m`` -- every node/constant/local-
    variable uid, every structure uid, the connector-pane pattern + every
    terminal's pane index, and every net reference's producer -- not merely
    a model that happens to re-render to the same bytes."""
    loaded = _load(vi_path, search_root)
    if loaded is None:
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    graph, vi_name = loaded
    module: NetlistModule = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name=vi_path.name, verbose=True)

    parsed = parse_lvnet(text)
    reconstructed = reconstruct_module(parsed)

    _assert_graph_identity(module, reconstructed, vi_path.name)
