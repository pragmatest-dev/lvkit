"""Shared netlist-diff text helpers.

The small text helpers ``diff.py``'s node-first text diff needs off a
``NetlistModule``: ``ambiguous_bares`` (bare net names that collide across
instances), ``instance_line`` and ``scope_header`` (one-line renderers for an
instance / a scope header).

This module must NEVER import from ``.netlist`` -- ``netlist.py`` imports these
names back from here for re-export, and a reverse import would be a fragile,
import-order-dependent circular import. ``netlist.py`` re-exports them so every
existing ``from ...graph.netlist import ambiguous_bares`` (etc.) call site --
``diff.py``, tests -- keeps working unchanged.
"""

from __future__ import annotations

from .netlist_models import (
    NetlistConstant,
    NetlistFeedback,
    NetlistInstance,
    NetlistItem,
    NetlistModule,
    NetlistScope,
    NetlistTerminalBinding,
    NetRef,
)


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
    co-occur (a property node is never a primitive node), so one visual slot
    serves both without collision: ``Property Node#1 [Bool]``.

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
    makes a VI and each node inside it read the SAME shape (``NAME(ins) -> outs``)
    and keeps a diff node-centric -- the changed node's name sits right after
    the ``+/-/~`` gutter. The
    header itself (``name``/``#n``/bracket suffixes) is ``_instance_name_display``.

    Inputs use NAMED-TERMINAL association (Verilog ``.port(net)`` / VHDL
    ``port => signal`` / Python kwargs), rendered ``terminal=net`` -- each wire
    is tied to the declared input terminal it feeds, not left positional. An
    inverted input (``NetlistTerminalBinding.inverted`` -- the "Not" bubble
    LabVIEW draws directly on that input, negating it before the node's own
    operation runs) renders ``terminal=not(net)``: a function-form wrapper around
    the net, ASCII and arrow-safe (``->`` only, never ``<-``), the same
    convention ``_ascii_arrows`` reserves arrows for elsewhere in a diff.
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
    # not (see ``NetlistTerminalBinding``) -- this renderer only ever shows
    # wired ones, so filter back down to only the wired bindings.
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
