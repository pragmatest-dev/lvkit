"""The drive lines of a scope: ``<net> = <source>`` pairs a structure writes.

A structure opens its block with the wires into it that no frame reads, closes
each frame with the values the frame drives onto the structure's output nets,
and a loop writes its count and conditional-terminal wires. Render writes these
lines and the signature that gates the round trip compares them, so both read
them from here. Each function takes ``render_source``, the one function that
spells a source (``render_lvnet._render_lvnet_source`` bound to its handle map).
"""

from __future__ import annotations

from collections.abc import Callable

from .lvnet_grammar import (
    _LVNET_LOOP_CONDITION_TERMINAL,
    _LVNET_LOOP_COUNT_TERMINAL,
    _LVNET_SEQUENCE_LOCAL_UNREADABLE,
    _LVNET_TERMINAL_SEP,
    _LVNET_UNWIRED,
    lvnet_net_separator,
)
from .lvnet_scalar import lvnet_literal_token
from .netlist_models import (
    DefaultValue,
    FrameOutputDrive,
    GammaMerge,
    NetlistFrame,
    NetlistScope,
    NetRef,
    SequenceLocalDrive,
    SequenceLocalUnreadable,
)

SourceText = Callable[[NetRef | DefaultValue], str]


def is_dead_slot(type_descriptor: str, wired: bool) -> bool:
    """Whether a ``Void`` terminal is a dead connector-pane slot, which lvnet does
    not show. A ``Void`` terminal with a wire is real -- its type is simply not
    recorded -- and keeps its line."""
    return type_descriptor == "Void" and not wired


def lvnet_loop_net(scope: NetlistScope, terminal: str) -> str:
    """A loop's own terminal as a net name: ``loop_<uid>::<terminal>``."""
    return f"loop_{scope.uid}{_LVNET_TERMINAL_SEP}{terminal}"


def loop_count_drives(
    scope: NetlistScope, render_source: SourceText
) -> list[tuple[str, str]]:
    """The ``(net, source)`` line for a For Loop's count wire
    (``loop_<uid>::N = <source>``); none when nothing is wired to it."""
    if scope.count is None:
        return []
    return [
        (
            lvnet_loop_net(scope, _LVNET_LOOP_COUNT_TERMINAL),
            render_source(scope.count),
        )
    ]


def loop_condition_drives(
    scope: NetlistScope, render_source: SourceText
) -> list[tuple[str, str]]:
    """The ``(net, source)`` line for the wire on a loop's conditional terminal
    (``loop_<uid>::condition = <source>``, ``?`` when it is unwired); none for a
    loop with no conditional terminal."""
    if scope.condition_mode is None:
        return []
    source = (
        render_source(scope.selector)
        if scope.selector is not None
        else _LVNET_UNWIRED
    )
    return [(lvnet_loop_net(scope, _LVNET_LOOP_CONDITION_TERMINAL), source)]


def structure_input_drives(
    scope: NetlistScope, render_source: SourceText
) -> list[tuple[str, str]]:
    """The ``(net, source)`` lines a structure opens its block with: each wire
    into it that no frame reads (``sequence_<uid>::in0 = <source>``), an Event
    Structure's ``timeout`` and the wire into the left terminal of its
    ``dynamic`` pair."""
    return [
        (lvnet_net_separator(i.net), render_source(i.value))
        for i in scope.inputs
    ]


def frame_drives(
    scope: NetlistScope, frame: NetlistFrame, render_source: SourceText
) -> list[tuple[str, str]]:
    """The ``(net, source)`` drive lines closing one frame: the value it puts on
    each case / event output tunnel (an unwired frame takes ``(default <Type>)``),
    then each sequence local it writes, each output tunnel a Sequence or Disable
    structure's frame wires, and the dynamic-pair value an Event frame wires."""
    lines: list[tuple[str, str]] = []
    frame_key = "default" if frame.is_default else frame.label
    for merge in scope.outputs:
        if isinstance(merge, GammaMerge):
            entry = next((c for c in merge.cases if c.frame_key == frame_key), None)
            if entry is not None:
                lines.append(
                    (
                        lvnet_net_separator(merge.net),
                        render_source(entry.source),
                    )
                )
        elif isinstance(merge, SequenceLocalDrive | FrameOutputDrive):
            if merge.frame == frame.value:
                lines.append(
                    (
                        lvnet_net_separator(merge.net),
                        render_source(merge.value),
                    )
                )
        elif isinstance(merge, SequenceLocalUnreadable) and merge.frame == frame.value:
            attribute = (
                f"({_LVNET_SEQUENCE_LOCAL_UNREADABLE} "
                f"{lvnet_literal_token(merge.reason)})"
            )
            lines.append((lvnet_net_separator(merge.net), attribute))
    return lines
