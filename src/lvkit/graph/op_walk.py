"""Small shared operation-tree helpers used by both ``describe`` and
``netlist``.

Split out to avoid a circular import: ``netlist.py`` needs these to trace
wires back to their producing operation, and ``describe.py`` needs
``build_netlist``/``render_netlist`` for its ``## Netlist`` section. Neither
module may import the other at module level, so the shared walk helpers
live here instead.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import (
    CaseFrame,
    CaseOperation,
    ClusterField,
    DisableStructureOperation,
    LVType,
    Operation,
    SelectorRange,
    SequenceOperation,
    Terminal,
    TunnelTerminal,
    _is_error_cluster,
)
from ..parser.constants import NODE_CLASS_NMUX
from ..vilib_resolver import get_resolver as _get_vilib_resolver
from .models import Constant

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def _find_op_owning_terminal(
    operations: list[Operation], terminal_id: str | None,
) -> tuple[Operation, Terminal] | None:
    """Recursively find the operation that owns the given terminal."""
    if terminal_id is None:
        return None
    for op in operations:
        for t in op.terminals:
            if t.id == terminal_id:
                return op, t
        hit = _find_op_owning_terminal(op.inner_nodes, terminal_id)
        if hit:
            return hit
        if isinstance(
            op, (CaseOperation, SequenceOperation, DisableStructureOperation),
        ):
            for frame in op.frames:
                hit = _find_op_owning_terminal(frame.operations, terminal_id)
                if hit:
                    return hit
    return None


def _has_output_tunnel(op: Operation) -> bool:
    """True if the structure routes any value out (so an empty frame is a
    pass-through, not truly empty -- LV requires output tunnels wired in
    every frame)."""
    return any(t.direction == "output" for t in op.terminals)


def _paired_tunnel_id(op: Operation, term: Terminal) -> str | None:
    """Hop across a structure's tunnel: given ``term`` (an outer or inner
    ``TunnelTerminal`` owned by structure ``op``), return the terminal id
    on the OTHER side (``op.tunnels`` -- the same outer/inner pairing table
    ``codegen/nodes/case.py::_bind_input_tunnels``/``_bind_output_tunnels``
    and ``CodeGenContext.resolve`` use), or ``None`` if ``term`` isn't a
    tunnel endpoint on this op.
    """
    if not isinstance(term, TunnelTerminal):
        return None
    for tunnel in op.tunnels:
        if tunnel.inner_terminal_uid == term.id:
            return tunnel.outer_terminal_uid
        if tunnel.outer_terminal_uid == term.id:
            return tunnel.inner_terminal_uid
    return None


def _flatten_fields(
    fields: list[ClusterField],
) -> list[tuple[list[str], ClusterField]]:
    """Flatten cluster fields depth-first with path.

    LabVIEW nMux ``<i>`` tags use flattened indices across the entire
    cluster hierarchy, not just the top level. Shared by codegen's nmux
    field assignment (``codegen/nodes/nmux.py``, which applies its own
    Python-identifier naming on top) and the netlist/describe field-name
    display below (which shows the raw LabVIEW name).
    """
    result: list[tuple[list[str], ClusterField]] = []
    for f in fields:
        result.append(([f.name], f))
        if f.type and f.type.fields:
            for sub_path, sub_field in _flatten_fields(f.type.fields):
                result.append(([f.name] + sub_path, sub_field))
    return result


def _nmux_raw_field_name(
    term: Terminal, class_fields: list[ClusterField] | None,
) -> str | None:
    """Raw (un-mangled) LabVIEW field name for an nMux LIST terminal, via
    ``Terminal.nmux_field_index``. Same index resolution as codegen's
    ``nmux.py::_field_name``, but returns the LabVIEW name as-is (for
    display in the netlist/describe projections) instead of a
    Python-safe identifier.
    """
    if term.nmux_field_index is None or not class_fields:
        return None
    if term.nmux_field_index < len(class_fields):
        return class_fields[term.nmux_field_index].name
    flat = _flatten_fields(class_fields)
    if term.nmux_field_index < len(flat):
        path, _field = flat[term.nmux_field_index]
        return path[-1]
    return None


def _nmux_agg_fields(
    op: Operation, graph: InMemoryVIGraph,
) -> list[ClusterField] | None:
    """The nMux op's aggregate (cluster/class) terminal's fields, via
    ``InMemoryVIGraph.get_type_fields`` -- same lookup
    ``codegen/nodes/nmux.py::generate`` uses to resolve field names."""
    for t in op.terminals:
        if t.nmux_role == "agg" and t.lv_type:
            return graph.get_type_fields(t.lv_type)
    return None


def _is_nmux(op: Operation) -> bool:
    """True if ``op`` is a Bundle/Unbundle By Name (nMux) node."""
    return op.node_type == NODE_CLASS_NMUX


def _format_error_cluster(value: object) -> str:
    """Render an error-cluster value as ``code N: "source"``."""
    data = value
    if isinstance(value, str):
        try:
            data = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    if isinstance(data, dict):
        code = data.get("code", 0)
        source = data.get("source", "")
        status = data.get("status", False)
        if not status and not code:
            return "no error"
        if source:
            return f'code {code}: "{source}"'
        return f"code {code}"
    return str(value)


def _const_value_str(c: Constant) -> str:
    """Human-readable value for a constant (no redundant quoting)."""
    if c.lv_type and _is_error_cluster(c.lv_type):
        return _format_error_cluster(c.value)
    return str(c.value)


def _format_ranges(ranges: list[SelectorRange], fmt: Callable[[int], str]) -> str:
    """Render a frame's selector ranges the way LabVIEW builds the label:
    singles as ``fmt(v)``, closed ranges as ``a..b``, open ranges as ``a..``
    / ``..b``, joined with ``, `` (e.g. ``1, 3, 5..8``). Shared by the
    renderer's frame-chrome label and the netlist's case/disable frame
    label -- both need the identical faithful text."""
    parts: list[str] = []
    for r in ranges:
        if r.open_start:
            parts.append(f"..{fmt(r.end)}")
        elif r.open_end:
            parts.append(f"{fmt(r.start)}..")
        elif r.is_single:
            parts.append(fmt(r.start))
        else:
            parts.append(f"{fmt(r.start)}..{fmt(r.end)}")
    return ", ".join(parts)


def _selector_label(frame: CaseFrame, lv_type: LVType | None, is_error: bool) -> str:
    """The faithful case-selector text for one frame, by selector type:
    ``Default``; error cluster → ``No Error``/``Error``; enum → item name(s);
    integer → value(s)/range(s); string → quoted; boolean → ``True``/``False``.
    """
    sv = str(frame.selector_value)
    if is_error:
        # The error-cluster case switches on the status boolean: 0 = no error,
        # anything else is an error (LabVIEW: "No Error" / "Error", plus code
        # ranges like "Error 3..10" since 2019). The Error frame is often the
        # structure's default — LabVIEW still labels it "Error", not "Default",
        # so this precedes the plain-default branch below.
        if sv == "0":
            return "No Error"
        codes = [r for r in frame.selector_ranges if not (r.is_single and r.start == 1)]
        if codes:
            return f"Error {_format_ranges(codes, str)}"
        return "Error"
    if frame.is_default or sv == "Default":
        return "Default"
    if lv_type and lv_type.kind in ("enum", "ring") and lv_type.values \
            and frame.selector_ranges:
        int_to_name = {ev.value: name for name, ev in lv_type.values.items()}
        return _format_ranges(
            frame.selector_ranges, lambda i: int_to_name.get(i, str(i)),
        )
    if frame.selector_ranges:  # integer selector
        return _format_ranges(frame.selector_ranges, str)
    if frame.selector_strings:  # string selector — one frame, several strings
        return ", ".join(f'"{s}"' for s in frame.selector_strings)
    if lv_type and lv_type.underlying_type == "String":
        return f'"{sv}"'
    return sv  # boolean True/False, or an already-display token


# ============================================================
# Component declarations (Verilog-module / VHDL-entity half of the netlist)
# ============================================================
#
# Shared by ``describe.py``'s ``## Dependencies`` (non-verbose) and
# ``netlist.py``'s ``NetlistModule.components`` (verbose ``## Components``) --
# both need the SAME typed subVI interface, just rendered differently (a
# one-line ``name: (ins) -> (outs) -- description`` vs a node-first
# ``name(ins) -> (outs)`` declaration), so the lookup lives here rather than
# in either consumer.


@dataclass(frozen=True)
class ComponentPort:
    """One named, typed port on a component's declared interface."""

    name: str
    type: str


def _subvi_ports(
    graph: InMemoryVIGraph, name: str,
) -> tuple[list[ComponentPort], list[ComponentPort]] | None:
    """Typed (inputs, outputs) port list for a called SubVI.

    Loaded VIs use their resolved front-panel signature; unloaded vilib
    refs fall back to the resolver's terminal layout. Returns ``None`` when
    neither is available -- callers decide how to render that (bare name,
    or an empty-port declaration), never fabricate ports.
    """
    loaded = set(graph.list_vis())
    qname = name
    if qname not in loaded:
        try:
            resolved = graph.resolve_vi_name(name)
        except (KeyError, ValueError):
            resolved = None
        if resolved in loaded:
            qname = resolved  # type: ignore[assignment]

    if qname in loaded:
        sctx = graph.get_vi_context(qname)
        ins = [
            ComponentPort(name=t.name or str(t.index), type=t.python_type())
            for t in sctx.inputs if not t.is_error_cluster
        ]
        outs = [
            ComponentPort(name=t.name or str(t.index), type=t.python_type())
            for t in sctx.outputs if not t.is_error_cluster
        ]
        return ins, outs

    entry = _get_vilib_resolver().resolve_by_name(name)
    if entry is not None and entry.terminals:
        # str(t.type): the vilib resolver's terminal type is frequently
        # unset for real entries (e.g. niDigital.* instrument handles) --
        # ``str(None) == "None"`` reproduces the exact text the old
        # f-string-based signature rendered, so non-verbose output (which
        # still calls through here) is byte-identical to before this
        # module existed.
        ins = [
            ComponentPort(name=t.name, type=str(t.type))
            for t in entry.terminals if t.direction == "input"
        ]
        outs = [
            ComponentPort(name=t.name, type=str(t.type))
            for t in entry.terminals if t.direction == "output"
        ]
        return ins, outs
    return None


def _render_ports(ins: list[ComponentPort], outs: list[ComponentPort]) -> str:
    """``"(a: T, b: U) -> (c: V)"`` -- ASCII-only, shared by describe's
    ``## Dependencies`` line and netlist's ``## Components`` declaration."""
    in_str = ", ".join(f"{p.name}: {p.type}" for p in ins)
    out_str = ", ".join(f"{p.name}: {p.type}" for p in outs)
    return f"({in_str}) -> ({out_str})"
