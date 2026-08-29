"""The OLD netlist text renderer -- DEPRECATED.

``render_netlist`` projects a ``NetlistModule`` to the gamma/mu/eta ``:=``
ASCII syntax documented in ``.tmp/netlist-spec.md``. It has been superseded
by ``render_lvnet.render_lvnet`` (the verbose ``lvnet`` format) but is kept
byte-identical for existing consumers: the ``lvkit describe --format
netlist`` CLI path (also ``lvkit setup --git-textconv``) and
``tests/test_netlist_from_graph_parity.py``'s cross-check against the
graph-based builder.

Split out of ``netlist.py`` (see that module's docstring for the full
pipeline) purely to shrink it -- this is a mechanical, behavior-preserving
move, not a design change.

``ambiguous_bares``/``instance_line``/``scope_header`` live here too, even
though ``diff.py``'s node-first text diff also imports and calls them --
``render_netlist``'s own call graph needs all three, and this module must
NEVER import from ``.netlist`` (netlist.py already imports ``render_netlist``
back from here for re-export; a reverse import would be a fragile,
import-order-dependent circular import -- whichever of the two modules
happens to be imported first would work, the other would raise
``ImportError: cannot import name ... from partially initialized module``).
``netlist.py`` re-exports all four names so every existing ``from
...graph.netlist import ambiguous_bares`` (etc.) call site -- ``diff.py``,
tests -- keeps working unchanged.

``index_module``/``component_line`` are NOT here: ``render_netlist``'s call
graph never reaches them (they're used only by ``diff.py`` and the CLI's
``## Components`` table respectively), so they stayed in ``netlist.py``.

``_quoted_frame_label`` comes from ``render_lvnet`` (a prior cut already
moved it there) -- that import is one-way and safe: ``render_lvnet`` never
imports anything back from this module or from ``.netlist``.
"""

from __future__ import annotations

from .netlist_models import (
    DefaultValue,
    EtaMerge,
    GammaMerge,
    MuMerge,
    NetlistConstant,
    NetlistFeedback,
    NetlistFrame,
    NetlistInstance,
    NetlistItem,
    NetlistModule,
    NetlistScope,
    NetlistTerminalBinding,
    NetRef,
)
from .render_lvnet import _quoted_frame_label

# ============================================================
# render_netlist
# ============================================================


def _collect_refs(items: list[NetlistItem]) -> list[NetRef]:
    """Every NetRef appearing anywhere in a body (inputs, outputs, and
    scope selectors), for global bare-name disambiguation.

    Deliberately does NOT recurse into any merge's source refs -- neither a
    scope's ``GammaMerge.cases[].source`` / ``MuMerge.init``/``recur`` /
    ``EtaMerge.value``, nor (symmetrically) a standalone ``NetlistFeedback``'s
    ``init``/``recur``. Every one of those sources aliases a producer already
    reachable elsewhere in the walk -- a boundary control (never ambiguous by
    itself) or some instance's own output (collected when THAT instance is
    walked) -- so re-adding the same identity here is redundant, not a
    genuinely new disambiguation source.
    """
    refs: list[NetRef] = []
    for item in items:
        match item:
            case NetlistInstance():
                # Phase A: an unwired input's binding carries no ``net`` (it
                # has a ``default`` instead, see ``NetlistTerminalBinding``) --
                # never a disambiguation source, so skip it here exactly as
                # it was always absent before Phase A (when it was dropped
                # from ``inputs`` entirely).
                refs.extend(b.net for b in item.inputs if b.net is not None)
                refs.extend(o.net for o in item.outputs)
            case NetlistScope():
                if item.selector is not None:
                    refs.append(item.selector)
                for frame in item.frames:
                    refs.extend(_collect_refs(frame.body))
            case NetlistFeedback():
                pass
            case NetlistConstant():
                # A constant's OWN declaration carries no NetRef of its own
                # (its net is a plain identifier built at the point of use,
                # already collected there via a consumer's
                # ``NetlistTerminalBinding.net`` -- see ``_resolve_source_gn``).
                pass
    return refs


def ambiguous_bares(module: NetlistModule) -> set[str]:
    """Bare names that map to more than one distinct producing net.

    Disambiguation rule: if two DIFFERENT source terminals would render the
    same ``bare``, both must be qualified wherever referenced (e.g.
    ``startTest.TestCase`` vs ``defaultTestResult.TestCase`` instead of two
    ambiguous ``TestCase``).
    """
    bare_to_identities: dict[str, set[tuple[str | None, int | None, str]]] = {}
    for ref in _collect_refs(module.body):
        identity = (ref.node, ref.occurrence, ref.terminal)
        bare_to_identities.setdefault(ref.bare, set()).add(identity)
    return {bare for bare, ids in bare_to_identities.items() if len(ids) > 1}


def _instance_name_display(instance: NetlistInstance) -> str:
    """The instance's own header text -- name, ``#n`` occurrence tag, and any
    bracket suffix -- with NO ports/connections (that's ``instance_line``'s
    job). Split out of ``instance_line`` since the header assembly (occurrence
    + operation suffix + object/method suffix) is a self-contained concern.

    A Property Node's ``object_name`` (its target CLASS, e.g. "Bool") gets
    the same bracket-suffix treatment as ``operation`` -- the two never
    co-occur (a ``PropertyOperation`` is never a ``PrimitiveOperation``), so
    one visual slot serves both without collision: ``Property Node#1 [Bool]``.

    An Invoke Node's ``method_name`` gets the SAME bracket suffix slot,
    rendered ``object:method`` (``Invoke Node#1 [Library:Open Project]``) --
    ``:`` reads as "the method OF this object", the same idiom the
    qualified-name display already uses elsewhere (``owning_libraries``
    joined with ``:``). When ``object_name`` is absent the bracket holds
    just the method.
    """
    tag = f"#{instance.occurrence}" if instance.occurrence else ""
    op_suffix = f" [{instance.operation}]" if instance.operation else ""
    if instance.method_name:
        obj = (
            f"{instance.object_name}:{instance.method_name}"
            if instance.object_name
            else instance.method_name
        )
        obj_suffix = f" [{obj}]"
    elif instance.object_name:
        obj_suffix = f" [{instance.object_name}]"
    else:
        obj_suffix = ""
    return f"{instance.name}{tag}{op_suffix}{obj_suffix}"


def instance_line(instance: NetlistInstance, ambiguous: set[str]) -> str:
    """NODE-FIRST netlist line: ``"name(ins) -> outs"`` (``"name(ins)"`` when no
    outputs) -- NO indent or gutter; callers own that.

    Node-first is the real netlist convention: SPICE (``R1 n1 n2 1k``) and
    Verilog (``and2 u1 (.a(w1), .b(w2))``) both lead with the COMPONENT, then
    its connections. The node is the subject; wires are its attributes. It also
    makes a VI and each node inside it read the SAME shape (``NAME(ins) -> outs``,
    matching the ``render_netlist`` signature line) and keeps a diff node-centric
    -- the changed node's name sits right after the ``+/-/~`` gutter. The
    header itself (``name``/``#n``/bracket suffixes) is ``_instance_name_display``.

    Inputs use NAMED-TERMINAL association (Verilog ``.port(net)`` / VHDL
    ``port => signal`` / Python kwargs), rendered ``terminal=net`` -- each wire
    is tied to the declared input terminal it feeds, not left positional. An
    inverted input (``NetlistTerminalBinding.inverted`` -- the "Not" bubble
    LabVIEW draws directly on that input, negating it before the node's own
    operation runs) renders ``terminal=not(net)``: a function-form wrapper around
    the net, ASCII and arrow-safe (``->`` only, never ``<-``), the same idiom
    ``_render_merge_source``/the module docstring already reserve arrows for.
    A non-inverted input is unchanged from before this flag existed.

    Each accessed property's terminal is already labelled by its real NAME (not
    a numeric index) via the load-time ``op_walk.stamp_property_value_names``
    stamp on its VALUE terminal's ``display_name`` -- a WRITTEN property
    shows as an input binding (``Value=<net>``), a READ property as a named
    output net -- so no further special-casing is needed here; direction is
    unambiguous from which side of ``->`` a property's terminal appears on (see
    ``NetlistInstance.properties`` for the JSON-only structured mirror of
    the same facts).

    Parameter terminal NAMES are never available in the VI file for an Invoke
    Node (they live in the method's VI-server signature) -- ``ins``/``outs``
    below stay numeric for an invoke node; only the node's OWN identity
    (``_instance_name_display``) gains the method it calls.
    """
    name_disp = _instance_name_display(instance)

    def _bind(b: NetlistTerminalBinding) -> str:
        assert b.net is not None
        net = b.net.render(qualified=b.net.bare in ambiguous)
        # An inverted input wraps the net in `not(...)` -- a function form that
        # reads clearly and can't be mistaken for the primitive "Not Equal?"
        # the way a bare `NOT `/`!` prefix glued to the name would.
        return f"{b.terminal}={f'not({net})' if b.inverted else net}"

    # Phase A: ``instance.inputs`` now carries EVERY real terminal, wired or
    # not (see ``NetlistTerminalBinding``) -- this OLD renderer only ever showed
    # wired ones, so filter back down to keep it byte-identical to the
    # Operation-based builder (which never produces an unwired binding).
    wired_inputs = [b for b in instance.inputs if b.net is not None]
    ins = ", ".join(_bind(b) for b in wired_inputs)
    base = f"{name_disp}({ins})"
    if instance.outputs:
        outs = ", ".join(o.net.render(qualified=False) for o in instance.outputs)
        return f"{base} -> {outs}"
    return base


def scope_header(scope: NetlistScope, ambiguous: set[str]) -> str:
    """``"case (sel):"`` / ``"while (sel):"`` / ``"for (sel):"`` /
    ``"sequence:"`` / ``"disabled:"`` -- NO indent, no frames."""
    if scope.kind == "sequence":
        return "sequence:"
    sel_str = None
    if scope.selector is not None:
        sel_str = scope.selector.render(qualified=scope.selector.bare in ambiguous)
    return f"{scope.kind} ({sel_str}):" if sel_str else f"{scope.kind}:"


def _render_instance(
    instance: NetlistInstance,
    indent: int,
    lines: list[str],
    ambiguous: set[str],
) -> None:
    prefix = "  " * indent
    lines.append(f"{prefix}{instance_line(instance, ambiguous)}")


def _render_frame_body(
    frame: NetlistFrame,
    indent: int,
    lines: list[str],
    ambiguous: set[str],
) -> None:
    if frame.body:
        _render_items(frame.body, indent, lines, ambiguous)
    elif frame.passthrough:
        lines.append("  " * indent + "(pass-through)")
    else:
        lines.append("  " * indent + "(empty)")


def _render_scope(
    scope: NetlistScope,
    indent: int,
    lines: list[str],
    ambiguous: set[str],
) -> None:
    prefix = "  " * indent
    lines.append(f"{prefix}{scope_header(scope, ambiguous)}")

    if scope.kind in ("case", "disabled"):
        for frame in scope.frames:
            default = " (default)" if frame.is_default else ""
            label = _quoted_frame_label(frame.label)
            lines.append(f"{prefix}  {label}{default}:")
            _render_frame_body(frame, indent + 2, lines, ambiguous)
    elif scope.kind == "sequence":
        for frame in scope.frames:
            lines.append(f"{prefix}  frame {frame.label}:")
            _render_frame_body(frame, indent + 2, lines, ambiguous)
    elif scope.kind == "event":
        # frame.label is already LabVIEW's own faithful bracketed rendering
        # (or an honest "[N]" placeholder) -- no extra quoting/prefix needed,
        # unlike a case's plain selector-value label.
        for frame in scope.frames:
            lines.append(f"{prefix}  {frame.label}:")
            _render_frame_body(frame, indent + 2, lines, ambiguous)
    else:  # "for" / "while"
        _render_frame_body(scope.frames[0], indent + 1, lines, ambiguous)

    # One merge definition line per structural value-merge point on this
    # scope (see NetlistScope.outputs docstring) -- a case's GammaMerge, or
    # a loop's MuMerge/EtaMerge -- at the same indent as this scope's own
    # frame labels/body.
    for merge in scope.outputs:
        lines.append(f"{prefix}  {_merge_definition_line(merge, ambiguous)}")


def _render_merge_source(source: NetRef | DefaultValue, ambiguous: set[str]) -> str:
    if isinstance(source, DefaultValue):
        return source.render()
    return source.render(qualified=source.bare in ambiguous)


def _short_net(net: str) -> str:
    """The SHORT local name (``out0``/``shift0``/...) of a fully qualified
    merge net (``case3.out0``/``loop1.shift0``/...) -- a definition line
    sits inside its own scope, so it never repeats that scope's own id
    prefix (the same convention a frame's own header doesn't repeat the
    case's selector name either). Feedback Nodes intentionally do NOT use
    this -- a standalone ``fb{k}`` net has no scope prefix to strip, so
    ``_feedback_definition_line`` renders ``fb.net`` in full.
    """
    return net.rsplit(".", 1)[-1]


def _gamma_definition_line(gamma: GammaMerge, ambiguous: set[str]) -> str:
    """``"out0 := gamma(selector; True -> subtract3.difference, default -> 0
    (I32 default))"`` -- the SHORT local name (``out{k}``, not the fully
    qualified ``case{id}.out{k}``) since this line sits inside that case's
    own scope, the same convention a frame's own header doesn't repeat the
    case's selector name either. Arrow is ``->`` ONLY (the netlist syntax is
    locked ASCII, no ``<-``, see ``.tmp/netlist-spec.md``).
    """
    sel_str = (
        gamma.selector.render(qualified=gamma.selector.bare in ambiguous)
        if gamma.selector is not None
        else "?"
    )
    cases_str = ", ".join(
        f"{c.frame_key} -> {_render_merge_source(c.source, ambiguous)}"
        for c in gamma.cases
    )
    return f"{_short_net(gamma.net)} := gamma({sel_str}; {cases_str})"


def _render_mu(
    net: str,
    tag: str,
    init: NetRef | DefaultValue,
    recur: NetRef | None,
    ambiguous: set[str],
) -> str:
    """``"{net} := mu{tag}(init -> ..., recur -> ...)"`` -- the shared mu
    render both a loop shift register's ``_mu_definition_line`` (short local
    ``shift{k}`` name, no tag) and a standalone Feedback Node's
    ``_feedback_definition_line`` (full ``fb{k}`` name, ``[z^-N]`` tag) use.
    ``recur`` is omitted entirely (never rendered as an unresolved ``?``)
    when the shift register / Feedback Node is genuinely never written to --
    see ``MuMerge.recur``'s docstring. ``->`` ONLY (locked ASCII, no ``<-``).
    """
    init_str = _render_merge_source(init, ambiguous)
    if recur is None:
        return f"{net} := mu{tag}(init -> {init_str})"
    recur_str = _render_merge_source(recur, ambiguous)
    return f"{net} := mu{tag}(init -> {init_str}, recur -> {recur_str})"


def _mu_definition_line(mu: MuMerge, ambiguous: set[str]) -> str:
    """``"shift0 := mu(init -> seed_net, recur -> Increment.result)"`` -- the
    SHORT local name (``shift{k}``) -- see ``_render_mu``.
    """
    return _render_mu(_short_net(mu.net), "", mu.init, mu.recur, ambiguous)


def _eta_definition_line(eta: EtaMerge, ambiguous: set[str]) -> str:
    """``"out0 := eta(array, Accumulate.result)"`` -- the SHORT local name
    (``out{k}``), ``index_mode`` first (matching ``EtaMerge`` field order). The
    orthogonal Conditional modifier appends ``+cond`` to the mode token
    (``eta(array+cond, ...)`` = conditionally index into the array).
    """
    value_str = _render_merge_source(eta.value, ambiguous)
    mode = f"{eta.index_mode}+cond" if eta.conditional else eta.index_mode
    return f"{_short_net(eta.net)} := eta({mode}, {value_str})"


def _feedback_definition_line(fb: NetlistFeedback, ambiguous: set[str]) -> str:
    """``"fb0 := mu[z^-1](init -> 0.0 (DBL default), recur -> now.0)"`` -- a
    Feedback Node as a standalone mu, the SAME ``init -> …, recur -> …`` form
    a loop shift register's ``_mu_definition_line`` uses (see ``_render_mu``),
    tagged ``[z^-N]`` with the z-transform delay depth (LabVIEW's own
    "z^-1 block" view) when the file carries one. The FULL ``fb{k}`` net name
    is used (not ``_short_net``) -- unlike a scope's own merges, a Feedback
    Node has no enclosing scope prefix to strip."""
    tag = f"[z^-{fb.delay}]" if fb.delay is not None else ""
    return _render_mu(fb.net, tag, fb.init, fb.recur, ambiguous)


def _merge_definition_line(
    merge: GammaMerge | MuMerge | EtaMerge,
    ambiguous: set[str],
) -> str:
    if isinstance(merge, GammaMerge):
        return _gamma_definition_line(merge, ambiguous)
    if isinstance(merge, MuMerge):
        return _mu_definition_line(merge, ambiguous)
    return _eta_definition_line(merge, ambiguous)


def _render_items(
    items: list[NetlistItem],
    indent: int,
    lines: list[str],
    ambiguous: set[str],
) -> None:
    for item in items:
        match item:
            case NetlistInstance():
                _render_instance(item, indent, lines, ambiguous)
            case NetlistScope():
                _render_scope(item, indent, lines, ambiguous)
            case NetlistFeedback():
                lines.append("  " * indent + _feedback_definition_line(item, ambiguous))
            case NetlistConstant():
                # Phase A's ``NetlistConstant`` body items are invisible to
                # this OLD ASCII renderer -- constants were never surfaced
                # here before Phase A either (only ``render_lvnet`` shows
                # them), so this keeps ``render_netlist`` byte-identical to
                # the Operation-based builder (which never emits one).
                pass


def render_netlist(module: NetlistModule, *, display_name: str | None = None) -> str:
    """Render a ``NetlistModule`` to the locked netlist text syntax.

    See ``.tmp/netlist-spec.md`` -- syntax is LOCKED, ASCII only.

    ``display_name`` overrides the header line's VI label (``module.vi_name``
    is the resolved ``vi_key`` -- a source-path identity, not fit for
    display -- see ``describe.py``'s ``--format netlist`` naming rules,
    which pass the qualified display name or a repo-relative path here).
    Defaults to ``module.vi_name`` for every existing caller (JSON/diff/the
    embedded viewer never pass this).
    """
    lines: list[str] = []
    in_names = ", ".join(inp.name for inp in module.inputs)
    # Show each output's driving net inline as ``name=source`` (arrow-free, the
    # same ``terminal=net`` idiom instance inputs use); bare name when unwired.
    ambiguous = ambiguous_bares(module)
    out_names = ", ".join(
        f"{o.name}={o.source.render(qualified=o.source.bare in ambiguous)}"
        if o.source is not None
        else o.name
        for o in module.outputs
    )
    header_name = display_name if display_name is not None else module.vi_name
    lines.append(f"{header_name} ({in_names}) -> ({out_names})")

    _render_items(module.body, 0, lines, ambiguous)

    return "\n".join(lines)
