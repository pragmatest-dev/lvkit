"""A structure's border terminals named in LabVIEW's words, shared by
``describe`` and ``diff``: a terminal's kind (``conditional indexing
tunnel``), what a tunnel carries (``error in``), and what drives a Conditional
tunnel's condition terminal (``not_x``, or a constant's value)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..models import TunnelMode, TunnelTerminal
from ..parser.constants import (
    TUNNEL_CLASS_LEFT_SR,
    TUNNEL_CLASS_LMAX,
    TUNNEL_CLASS_LOOP_TUNNEL,
    TUNNEL_CLASS_RIGHT_SR,
)
from .core import _uid_of
from .op_walk import _const_value_str, _terminal_display_name

if TYPE_CHECKING:
    from .core import InMemoryVIGraph
    from .models import AnyGraphNode, Wire

_LOOP_TUNNEL_MODE_NAMES = {
    TunnelMode.INDEXING: "indexing tunnel",
    TunnelMode.LAST_VALUE: "last value tunnel",
    TunnelMode.CONCATENATING: "concatenating tunnel",
}


def tunnel_kind_name(t: TunnelTerminal) -> str:
    """A structure border terminal's kind in LabVIEW's own words — what its
    right-click menu calls it (shift register, indexing / last value /
    concatenating tunnel, Conditional), not the heap class."""
    if t.tunnel_type in (TUNNEL_CLASS_LEFT_SR, TUNNEL_CLASS_RIGHT_SR):
        depth = t.sr_stack_depth or 1
        name = "shift register" if depth == 1 else f"{depth}-deep shift register"
    elif t.tunnel_type == TUNNEL_CLASS_LMAX:
        name = "count terminal"
    elif t.tunnel_type == TUNNEL_CLASS_LOOP_TUNNEL and t.mode is not None:
        name = _LOOP_TUNNEL_MODE_NAMES.get(t.mode, "tunnel")
    else:
        name = "tunnel"
    return f"conditional {name}" if t.conditional else name


def outer_tunnels(op: AnyGraphNode) -> list[TunnelTerminal]:
    """A structure's border terminals as seen from OUTSIDE it — one per
    tunnel / shift-register side / count terminal — inputs first (the way a
    diagram reads, left to right), then by uid for a deterministic order."""

    def uid_key(t: TunnelTerminal) -> tuple[int, object]:
        uid = _uid_of(t.id)
        return (0, int(uid)) if uid.isdigit() else (1, uid)

    return sorted(
        (
            t
            for t in op.terminals
            if isinstance(t, TunnelTerminal) and t.boundary == "outer"
        ),
        key=lambda t: (t.direction != "input", uid_key(t)),
    )


def carried_name(graph: InMemoryVIGraph, wires: list[Wire], term_id: str) -> str | None:
    """What a tunnel carries, by the name of the terminal at the other end of
    its outside wire (``error in`` feeding an input tunnel, ``error out`` fed
    by an output one) — how a LabVIEW user tells two tunnels apart. None when
    no named terminal is wired to it."""
    names = []
    for w in wires:
        if w.source.terminal_id == term_id:
            other = graph.get_terminal(w.dest.terminal_id)
        elif w.dest.terminal_id == term_id:
            other = graph.get_terminal(w.source.terminal_id)
        else:
            continue
        name = _terminal_display_name(other) if other is not None else None
        if name:
            names.append(name)
    return min(names) if names else None


def constant_names(graph: InMemoryVIGraph, vi_name: str) -> dict[str, str]:
    """Each constant of a VI by node id -> its label, else its value: how a
    wire fed by a constant reads."""
    return {c.id: c.label or _const_value_str(c) for c in graph.get_constants(vi_name)}


def condition_source(wires: list[Wire], t: TunnelTerminal) -> Wire | None:
    """The wire into a Conditional tunnel's condition terminal; None when the
    tunnel isn't Conditional or nothing is wired to its condition."""
    if t.condition_terminal is None:
        return None
    return next((w for w in wires if w.dest.terminal_id == t.condition_terminal), None)


def condition_name(
    graph: InMemoryVIGraph, wire: Wire, constants: Mapping[str, str]
) -> str:
    """What drives a condition terminal, as a LabVIEW user reads the wire: the
    feeding terminal's name (``not_x``), else a constant's label or value
    (``True``), else the feeding node's name."""
    source = graph.get_terminal(wire.source.terminal_id)
    name = _terminal_display_name(source) if source is not None else None
    return (
        name
        or constants.get(wire.source.node_id)
        or wire.source.name
        or wire.source.node_id.rsplit("::", 1)[-1]
    )
