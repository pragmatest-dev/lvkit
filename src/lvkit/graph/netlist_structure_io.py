"""What flows through a structure's border, as netlist model entries.

A structure's output tunnels are driven per frame, its input tunnels that no
frame reads are lines of the structure's own block, and an Event Structure has a
timeout and a dynamic-terminal pair. This module builds those entries from the
structure node's terminals; the wire lookups it needs are handed in as
``StructureWires`` (the builder in ``netlist_build`` owns wire resolution).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..models import Terminal, TunnelTerminal
from ..parser.constants import (
    TUNNEL_CLASS_INIT_FEEDBACK,
    TUNNEL_CLASS_LEFT_SR,
    TUNNEL_CLASS_LMAX,
)
from .core import uid_of
from .models import (
    AnyGraphNode,
    CaseStructureNode,
    DisableStructureNode,
    EventStructureNode,
    LoopNode,
    SequenceNode,
)
from .netlist_models import (
    DefaultValue,
    FrameOutputDrive,
    GammaCase,
    GammaMerge,
    NetlistFrame,
    NetRef,
    ScopeOutput,
    StructureInput,
)

EVENT_DYNAMIC_TUNNEL = "eventDynDCO"
EVENT_TIMEOUT_TUNNEL = "eventTimeOut"

# The net-vocabulary word of each structure kind: ``<word>_<uid>.out<K>``.
FRAME_ONLY_NET_PREFIX: dict[type, str] = {
    SequenceNode: "sequence",
    DisableStructureNode: "disabled",
    EventStructureNode: "event",
}
STRUCTURE_NET_PREFIX: dict[type, str] = {
    **FRAME_ONLY_NET_PREFIX,
    CaseStructureNode: "case",
    LoopNode: "loop",
}

# Tunnel classes that are not plain input tunnels: a loop's shift register and
# count, a Feedback Node initializer, an Event Structure's timeout and dynamic
# pair. A structure's ``in<K>`` numbering skips them.
_NON_PLAIN_INPUT_TUNNELS = frozenset(
    {
        TUNNEL_CLASS_LEFT_SR,
        TUNNEL_CLASS_LMAX,
        TUNNEL_CLASS_INIT_FEEDBACK,
        EVENT_DYNAMIC_TUNNEL,
        EVENT_TIMEOUT_TUNNEL,
    }
)


@dataclass(frozen=True)
class StructureWires:
    """The builder's wire lookups: the net feeding a terminal (``None`` when
    unwired), the same with the type default for an unwired one, and whether a
    terminal feeds anything."""

    wire: Callable[[str], NetRef | None]
    wire_or_default: Callable[..., NetRef | DefaultValue]
    has_reader: Callable[[str], bool]


def frame_output_tunnel_outers(node: AnyGraphNode) -> list[Terminal]:
    """Output-tunnel outer terminals of a Sequence / Disable / Event structure,
    in terminal order -- ``K`` of ``<kind>_<uid>.out<K>``. An Event Structure's
    dynamic pair is its own net, not an output tunnel."""
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.boundary == "outer"
        and t.direction == "output"
        and t.tunnel_type != EVENT_DYNAMIC_TUNNEL
    ]


def _input_tunnel_outers(node: AnyGraphNode) -> list[TunnelTerminal]:
    """The structure's plain input tunnels (outer faces), in terminal order --
    ``K`` of ``<kind>_<uid>.in<K>`` counts ALL of them, read or not."""
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.boundary == "outer"
        and t.direction == "input"
        and t.tunnel_type not in _NON_PLAIN_INPUT_TUNNELS
    ]


def _inner_faces(node: AnyGraphNode, outer: Terminal) -> list[TunnelTerminal]:
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.boundary == "inner"
        and t.paired_id == outer.id
    ]


def _event_outers(
    node: EventStructureNode, tunnel_class: str, direction: str
) -> list[TunnelTerminal]:
    return [
        t
        for t in node.terminals
        if isinstance(t, TunnelTerminal)
        and t.tunnel_type == tunnel_class
        and t.boundary == "outer"
        and t.direction == direction
    ]


def build_structure_inputs(
    wires: StructureWires, node: AnyGraphNode
) -> list[StructureInput]:
    """The wires into ``node`` that no frame reads (a plain input tunnel whose
    inner faces feed nothing) plus, for an Event Structure, its timeout and the
    wire into the left terminal of its dynamic pair."""
    prefix = STRUCTURE_NET_PREFIX[type(node)]
    uid = uid_of(node.id)
    inputs: list[StructureInput] = []
    for k, outer in enumerate(_input_tunnel_outers(node)):
        if any(wires.has_reader(i.id) for i in _inner_faces(node, outer)):
            continue
        value = wires.wire(outer.id)
        if value is not None:
            inputs.append(StructureInput(net=f"{prefix}_{uid}.in{k}", value=value))
    if isinstance(node, EventStructureNode):
        for tunnel_class, name in (
            (EVENT_TIMEOUT_TUNNEL, "timeout"),
            (EVENT_DYNAMIC_TUNNEL, "dynamic"),
        ):
            for outer in _event_outers(node, tunnel_class, "input"):
                value = wires.wire(outer.id)
                if value is not None:
                    net = f"event_{uid}.{name}"
                    inputs.append(StructureInput(net=net, value=value))
    return inputs


def _frame_inner(
    node: AnyGraphNode, outer: Terminal, position: int
) -> Terminal | None:
    """The inner face of ``outer`` in the frame at ``position``: a Disable
    structure's by the frame's selector value, a Sequence's and an Event
    Structure's by the frame position the file records."""
    if isinstance(node, DisableStructureNode):
        selector = node.frames[position].selector_value
        return next((t for t in _inner_faces(node, outer) if t.frame == selector), None)
    return next(
        (t for t in _inner_faces(node, outer) if t.frame_position == position), None
    )


def build_frame_output_drives(
    wires: StructureWires, node: AnyGraphNode, frames: list[NetlistFrame]
) -> list[FrameOutputDrive]:
    """For each output tunnel of a Sequence or Disable structure, the value each
    frame that WIRES it passes out (no line for a frame that leaves it unwired)."""
    prefix = STRUCTURE_NET_PREFIX[type(node)]
    uid = uid_of(node.id)
    drives: list[FrameOutputDrive] = []
    for k, outer in enumerate(frame_output_tunnel_outers(node)):
        for i, frame in enumerate(frames):
            inner = _frame_inner(node, outer, i)
            value = wires.wire(inner.id) if inner is not None else None
            if value is not None:
                drives.append(
                    FrameOutputDrive(
                        net=f"{prefix}_{uid}.out{k}", frame=frame.value, value=value
                    )
                )
    return drives


def build_event_outputs(
    wires: StructureWires, node: EventStructureNode, frames: list[NetlistFrame]
) -> list[ScopeOutput]:
    """An Event Structure's output tunnels, each frame driving its tunnel like a
    case frame (an unwired frame takes the type default), then the frames that
    wire the inside right terminal of the dynamic pair, which replaces the value
    for what follows (an unwired frame passes the left value through)."""
    uid = uid_of(node.id)
    outputs: list[ScopeOutput] = []
    for k, outer in enumerate(frame_output_tunnel_outers(node)):
        cases: list[GammaCase] = []
        for i, frame in enumerate(frames):
            inner = _frame_inner(node, outer, i)
            source = wires.wire_or_default(
                inner.id if inner is not None else None, inner, outer
            )
            cases.append(GammaCase(frame_key=frame.label, source=source))
        outputs.append(
            GammaMerge(net=f"event_{uid}.out{k}", selector=None, cases=cases)
        )
    for outer in _event_outers(node, EVENT_DYNAMIC_TUNNEL, "output"):
        for i, frame in enumerate(frames):
            inner = _frame_inner(node, outer, i)
            value = wires.wire(inner.id) if inner is not None else None
            if value is not None:
                outputs.append(
                    FrameOutputDrive(
                        net=f"event_{uid}.dynamic", frame=frame.value, value=value
                    )
                )
    return outputs
