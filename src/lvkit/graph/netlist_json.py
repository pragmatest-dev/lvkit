"""Netlist IR -> JSON projection.

Extracted from ``netlist.py`` (see that module's docstring for the overall
graph -> netlist pipeline). This module holds the ``to_dict`` cluster: the
structured, JSON-able counterpart to ``render_lvnet``'s text projection.
Self-contained -- it only calls model types and its own helpers, never
``netlist.py``'s render/build machinery.
"""

from __future__ import annotations

from dataclasses import asdict as _dataclass_asdict
from typing import Any

from ..models import LVType
from .models import (
    vi_health_to_dict,
    vi_properties_to_dict,
)
from .netlist_models import (
    ConnectorPaneTerminal,
    DefaultValue,
    EtaMerge,
    GammaCase,
    GammaMerge,
    MuMerge,
    NetlistComponent,
    NetlistConstant,
    NetlistDependency,
    NetlistFeedback,
    NetlistFrame,
    NetlistInstance,
    NetlistItem,
    NetlistModule,
    NetlistOutput,
    NetlistPropertyAccess,
    NetlistTerminalBinding,
    NetRef,
)


def _netref_to_dict(ref: NetRef) -> dict[str, Any]:
    return {
        "node": ref.node,
        "terminal": ref.terminal,
        "occurrence": ref.occurrence,
        "bare": ref.bare,
    }


def _frame_to_dict(frame: NetlistFrame, *, verbose: bool = False) -> dict[str, Any]:
    return {
        "label": frame.label,
        "value": frame.value,
        "is_default": frame.is_default,
        "passthrough": frame.passthrough,
        # ``_item_to_dict`` returns ``None`` for a Phase A ``NetlistConstant``
        # -- invisible to this OLD JSON shape, exactly as it was before
        # Phase A (see ``_item_to_dict``'s docstring) -- filtered here so
        # the JSON output never shows one.
        "body": [
            d
            for i in frame.body
            if (d := _item_to_dict(i, verbose=verbose)) is not None
        ],
    }


def _merge_source_to_dict(source: NetRef | DefaultValue) -> dict[str, Any]:
    if isinstance(source, DefaultValue):
        return {
            "kind": "default",
            "type": source.type_descriptor,
            "literal": source.literal,
        }
    return _netref_to_dict(source)


def _gamma_case_to_dict(case: GammaCase) -> dict[str, Any]:
    return {"frame": case.frame_key, "source": _merge_source_to_dict(case.source)}


def _gamma_to_dict(gamma: GammaMerge) -> dict[str, Any]:
    return {
        "net": gamma.net,
        "kind": "gamma",
        "selector": _netref_to_dict(gamma.selector) if gamma.selector else None,
        "cases": [_gamma_case_to_dict(c) for c in gamma.cases],
    }


def _mu_to_dict(mu: MuMerge) -> dict[str, Any]:
    return {
        "net": mu.net,
        "kind": "mu",
        "init": _merge_source_to_dict(mu.init),
        "recur": _netref_to_dict(mu.recur) if mu.recur is not None else None,
    }


def _eta_to_dict(eta: EtaMerge) -> dict[str, Any]:
    return {
        "net": eta.net,
        "kind": "eta",
        "index_mode": eta.index_mode,
        "conditional": eta.conditional,
        "value": _merge_source_to_dict(eta.value),
    }


def _merge_to_dict(merge: GammaMerge | MuMerge | EtaMerge) -> dict[str, Any]:
    if isinstance(merge, GammaMerge):
        return _gamma_to_dict(merge)
    if isinstance(merge, MuMerge):
        return _mu_to_dict(merge)
    return _eta_to_dict(merge)


def _component_to_dict(comp: NetlistComponent) -> dict[str, Any]:
    return {
        "name": comp.name,
        "inputs": [{"name": p.name, "type": p.type} for p in comp.inputs],
        "outputs": [{"name": p.name, "type": p.type} for p in comp.outputs],
    }


def _property_access_to_dict(access: NetlistPropertyAccess) -> dict[str, Any]:
    return {
        "name": access.name,
        "direction": access.direction,
        "net": _netref_to_dict(access.net) if access.net is not None else None,
    }


def _feedback_to_dict(fb: NetlistFeedback) -> dict[str, Any]:
    """A standalone Feedback Node body item -- the JSON counterpart of
    ``render_lvnet``'s ``feedback-node <handle> (<N> iteration[s]) :`` text
    (see ``_render_lvnet_feedback``). ``net``
    leads (matching ``_gamma_to_dict``/``_mu_to_dict``/``_eta_to_dict``'s key
    order -- a Feedback Node IS a standalone mu merge, see ``NetlistFeedback``'s
    docstring), then the ``kind`` discriminator (here always ``"feedback"``,
    distinguishing this from the ``instance``/``scope`` body-item kinds
    ``_item_to_dict`` also tags)."""
    return {
        "net": fb.net,
        "kind": "feedback",
        "uid": fb.uid,
        "delay": fb.delay,
        "init": _merge_source_to_dict(fb.init),
        "recur": _netref_to_dict(fb.recur) if fb.recur is not None else None,
    }


def _lv_type_to_dict(lv_type: LVType) -> dict[str, Any]:
    """The FULL lossless structured type -- JSON's counterpart to lvnet's
    verbose-only ``types :`` footnote body (``_lvnet_type_lossless_def``),
    but shaped as a direct recursive mirror of the ``LVType`` dataclass
    itself (every field, unflattened) rather than lvnet's by-name-
    deduplicated appendix: JSON has none of lvnet's line-length/whitespace
    pressure forcing a footnote indirection, so each terminal's own
    ``lv_type`` just nests its own full structure inline (repeated verbatim
    at every occurrence of the same named type -- still lossless, since a
    JSON reader pays no textual-length cost for the repetition, unlike
    lvnet's rendered text). ``verbose``-only caller (``netlist_to_dict``);
    never called for the default (non-verbose) output.

    - ``values``: ``{member_name: {"value": ordinal, "description": ...}}``,
      sorted by ordinal (mirrors ``_lvnet_type_lossless_def``'s enum/ring
      ordinal order) -- ``None`` when the type has none loaded (an
      unresolved enum/ring, or any non-enum/ring kind).
    - ``fields``: ``[{"name": ..., "type": <recursive dict or None>}, ...]``
      in declared order -- ``None`` when the type has none loaded (an
      unresolved cluster/typedef_ref, or any non-cluster kind).
    - ``element_type``: the recursive dict for an array's element or a
      parametrized refnum's inner type -- ``None`` when absent.
    - every other ``LVType`` field carried through verbatim.
    """
    return {
        "kind": lv_type.kind.value,
        "underlying_type": lv_type.underlying_type,
        "ref_type": lv_type.ref_type,
        "classname": lv_type.classname,
        "values": (
            {
                name: {"value": ev.value, "description": ev.description}
                for name, ev in sorted(
                    lv_type.values.items(), key=lambda kv: kv[1].value
                )
            }
            if lv_type.values is not None
            else None
        ),
        "fields": (
            [
                {
                    "name": f.name,
                    "type": _lv_type_to_dict(f.type) if f.type is not None else None,
                }
                for f in lv_type.fields
            ]
            if lv_type.fields is not None
            else None
        ),
        "element_type": (
            _lv_type_to_dict(lv_type.element_type)
            if lv_type.element_type is not None
            else None
        ),
        "dimensions": lv_type.dimensions,
        "typedef_path": lv_type.typedef_path,
        "typedef_name": lv_type.typedef_name,
        "description": lv_type.description,
        "measure_flavor": lv_type.measure_flavor,
    }


def _dependency_terminal_to_dict(t: ConnectorPaneTerminal) -> dict[str, Any]:
    """One ``uses :`` dependency's own interface terminal -- the JSON
    counterpart of ``_render_lvnet_dependency_interface``'s rendered line,
    which shows only name/type/direction (no pane index, wiring-rule, or
    default -- those are the OWNING VI's own connector-pane concerns, not
    reproduced for a dependency's borrowed interface). ``verbose``-only
    caller (``_dependency_to_dict``)."""
    d: dict[str, Any] = {"name": t.name, "type": t.type, "direction": t.direction}
    if t.lv_type is not None:
        d["lv_type"] = _lv_type_to_dict(t.lv_type)
    return d


def _dependency_to_dict(dep: NetlistDependency) -> dict[str, Any]:
    """One ``uses :`` manifest entry -- the JSON counterpart of
    ``_render_lvnet_uses``'s rendered line (+ its verbose-only nested
    interface). ``verbose``-only caller (``netlist_to_dict``); ``interface``
    is omitted entirely (never an empty list) when this dependency has none
    loaded -- a ``class``/``typedef`` dependency, or an unresolved ``subVI``
    (see ``NetlistDependency.interface`` docstring)."""
    d: dict[str, Any] = {
        "kind": dep.kind.value,
        "qualified": dep.qualified,
        "path": dep.path,
    }
    if dep.interface:
        d["interface"] = [_dependency_terminal_to_dict(t) for t in dep.interface]
    return d


def _instance_input_to_dict(
    b: NetlistTerminalBinding, *, verbose: bool
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "terminal": b.terminal,
        "net": _netref_to_dict(b.net),  # type: ignore[arg-type]
        "inverted": b.inverted,
    }
    if verbose and b.lv_type is not None:
        d["lv_type"] = _lv_type_to_dict(b.lv_type)
    return d


def _instance_output_to_dict(o: NetlistOutput, *, verbose: bool) -> dict[str, Any]:
    d = _netref_to_dict(o.net)
    if verbose and o.lv_type is not None:
        d["lv_type"] = _lv_type_to_dict(o.lv_type)
    return d


def _item_to_dict(item: NetlistItem, *, verbose: bool = False) -> dict[str, Any] | None:
    """One body item, tagged with a ``kind`` discriminator so the
    ``instance``/``scope``/``feedback`` union survives JSON (``asdict`` would
    erase it). Returns ``None`` for a Phase A ``NetlistConstant`` -- this OLD
    JSON shape never surfaced a constant before Phase A either (only
    ``render_lvnet`` does); callers filter the ``None`` out (see
    ``_frame_to_dict``/``netlist_to_dict``) so the JSON output never shows
    one.

    ``verbose`` (default ``False``, threaded from ``netlist_to_dict``)
    additionally nests each wired input/output's own structured ``lv_type``
    (see ``_instance_input_to_dict``/``_instance_output_to_dict``) -- terse
    output is completely unaffected."""
    if isinstance(item, NetlistConstant):
        return None
    if isinstance(item, NetlistInstance):
        # Phase A: ``item.inputs`` now carries EVERY real terminal, wired or
        # not (see ``NetlistTerminalBinding``) -- this OLD JSON shape only ever
        # showed wired ones, so filter back down to only the wired bindings.
        wired_inputs = [b for b in item.inputs if b.net is not None]
        return {
            "kind": "instance",
            "uid": item.uid,
            "name": item.name,
            # The callee's class/lib-qualified identity (bare ``name`` stays the
            # lookup key); a dynamic-dispatch call reports its declaring parent.
            "qualified_name": item.qualified_name,
            "occurrence": item.occurrence,
            "operation": item.operation,
            # Property Node / Invoke Node only (see NetlistInstance
            # docstring) -- ``None``/``[]`` for every other instance kind.
            "object": item.object_name,
            # Invoke Node only -- the method it calls. ``None`` otherwise.
            "method": item.method_name,
            "properties": [_property_access_to_dict(p) for p in item.properties],
            "inputs": [
                _instance_input_to_dict(b, verbose=verbose) for b in wired_inputs
            ],
            "outputs": [
                _instance_output_to_dict(o, verbose=verbose) for o in item.outputs
            ],
        }
    if isinstance(item, NetlistFeedback):
        return _feedback_to_dict(item)
    d: dict[str, Any] = {
        "kind": "scope",
        "uid": item.uid,
        "scope_kind": item.kind,
        "selector": _netref_to_dict(item.selector) if item.selector else None,
        "frames": [_frame_to_dict(f, verbose=verbose) for f in item.frames],
        # Always present (empty for sequence/disabled/event scopes) -- see
        # NetlistScope.outputs docstring: a case scope's GammaMerge, or a
        # loop scope's MuMerge/EtaMerge, tagged-union by "kind".
        "outputs": [_merge_to_dict(m) for m in item.outputs],
    }
    # Loop-only facts (see NetlistScope docstring) -- omitted for non-loop
    # scope kinds rather than always-present-but-empty, to keep the JSON
    # shape for case/sequence/disabled/event scopes unchanged.
    if item.kind in ("for", "while"):
        d["parallel"] = item.parallel
        d["parallel_static_workers"] = item.parallel_static_workers
        d["tunnels"] = [
            {
                "tunnel_type": t.tunnel_type,
                "mode": t.mode,
                "sr_initialized": t.sr_initialized,
                "sr_stack_depth": t.sr_stack_depth,
            }
            for t in item.tunnels
        ]
    return d


def netlist_to_dict(module: NetlistModule, *, verbose: bool = False) -> dict[str, Any]:
    """The netlist IR as a faithful JSON-able tree — the STRUCTURED counterpart
    to :func:`render_lvnet`'s text projection.

    One canonical structure for every ``format="json"`` surface (describe, diff,
    the MCP tools) so they never drift into per-command ad-hoc shapes. Lossless
    against the IR: boundary ``inputs``/``outputs`` carry the FAITHFUL LabVIEW
    type label (not a Python annotation), the ``instance``/``scope`` union is
    ``kind``-tagged, and scopes nest their frames' bodies recursively.

    ``verbose`` (default ``False``, lvnet §11/§12) additionally surfaces:

    - each connector-pane terminal's full ``wiring_rule`` tri-state
      (+ unknown) -- non-verbose keeps the plain ``required: bool`` exactly
      as before;
    - a top-level ``dependencies`` array -- the JSON counterpart of lvnet's
      ``uses :`` manifest (each entry's own ``interface`` when it's a
      resolved ``subVI`` dependency), see ``_dependency_to_dict``;
    - each terminal's own structured ``lv_type`` (connector pane, boundary
      inputs/outputs, dependency interfaces, and every instance's wired
      input/output throughout ``body``) alongside the existing flattened
      ``type`` string -- the JSON counterpart of lvnet's ``types :``
      footnote, see ``_lv_type_to_dict``.

    Non-verbose keeps every one of these OMITTED entirely, so default output
    (and the netlist-from-graph parity test, which compares non-verbose
    output) is byte/JSON-identical to pre-verbose output.
    """
    result: dict[str, Any] = {
        "vi": module.vi_name,
        # Authored connector pane (pattern + canonically-ordered terminals), a
        # sibling to the connectivity inputs/outputs below.
        "connector_pane": {
            "pattern": module.connector_pane.pattern_id,
            "terminals": [
                {
                    "name": p.name,
                    "type": p.type,
                    "direction": p.direction,
                    "index": p.index,
                    "required": p.is_required,
                    "default": p.default,
                    **({"wiring_rule": p.wiring_requirement.value} if verbose else {}),
                    **(
                        {"lv_type": _lv_type_to_dict(p.lv_type)}
                        if verbose and p.lv_type is not None
                        else {}
                    ),
                }
                for p in module.connector_pane.terminals
            ],
        },
        "inputs": [
            {
                "name": inp.name,
                "type": inp.type_descriptor,
                **(
                    {"lv_type": _lv_type_to_dict(inp.lv_type)}
                    if verbose and inp.lv_type is not None
                    else {}
                ),
            }
            for inp in module.inputs
        ],
        "outputs": [
            {
                "name": o.name,
                "type": o.type_descriptor,
                "source": _netref_to_dict(o.source) if o.source else None,
                **(
                    {"lv_type": _lv_type_to_dict(o.lv_type)}
                    if verbose and o.lv_type is not None
                    else {}
                ),
            }
            for o in module.outputs
        ],
        "components": [_component_to_dict(c) for c in module.components],
        # ``_item_to_dict`` returns ``None`` for a Phase A ``NetlistConstant``
        # -- filtered out, see its docstring.
        "body": [
            item_d
            for i in module.body
            if (item_d := _item_to_dict(i, verbose=verbose)) is not None
        ],
        "properties": vi_properties_to_dict(module.properties),
        "health": vi_health_to_dict(module.health),
        "class_context": (
            _dataclass_asdict(module.class_context)
            if module.class_context is not None
            else None
        ),
    }
    if verbose:
        # The lvnet §7 ``uses :`` dependency manifest -- populated by
        # ``build_netlist_from_graph`` (``NetlistModule.dependencies``
        # docstring); gated on ``verbose`` so non-verbose output stays
        # unaffected.
        result["dependencies"] = [
            _dependency_to_dict(dep) for dep in module.dependencies
        ]
    return result
