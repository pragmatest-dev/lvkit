"""Where a front-panel terminal sits on the block diagram.

A control or indicator whose terminal lies inside a loop or a case, event,
sequence or disable frame is read, or written, only when that scope runs. The
netlist puts its line in that scope: ``read <Control>`` for a control, the drive
line of an indicator. A terminal on the VI's own diagram has no scope; an
indicator's drive line is then the end of the diagram and a control has no line.

``FrontPanelPlacements`` collects the placed terminals once per VI (each holds
its structure id and frame key, ``FPTerminal.parent`` / ``.frame``), hands them
to the structure builder that owns them, and puts back on the diagram any whose
structure the netlist did not build.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import FPTerminal, Terminal
from .core import uid_of
from .netlist_models import (
    BoundaryOutput,
    NetlistControlRead,
    NetlistIndicatorDrive,
    NetlistItem,
    NetlistModule,
    NetlistScope,
)


class PlacementError(ValueError):
    """A front-panel terminal sits inside a structure the netlist has no body for."""


# ``(structure node id, frame key)``: the key ``operations.frame_key`` gives a
# frame, ``None`` for a loop or In Place Element body.
PlacementKey = tuple[str, str | int | None]


@dataclass(frozen=True)
class PlacedTerminals:
    """The terminals one structure body holds."""

    reads: list[NetlistControlRead] = field(default_factory=list)
    drives: list[NetlistIndicatorDrive] = field(default_factory=list)


class FrontPanelPlacements:
    """The VI's placed front-panel terminals, grouped by the body that holds
    them."""

    def __init__(self) -> None:
        self._groups: dict[PlacementKey, PlacedTerminals] = {}
        self._claimed: set[PlacementKey] = set()

    def _group(self, terminal: Terminal) -> PlacedTerminals | None:
        if not isinstance(terminal, FPTerminal) or terminal.parent is None:
            return None
        key = (terminal.parent, terminal.frame)
        return self._groups.setdefault(key, PlacedTerminals())

    def add_control(self, terminal: Terminal) -> None:
        group = self._group(terminal)
        if group is not None:
            group.reads.append(
                NetlistControlRead(terminal.name or "input", uid_of(terminal.id))
            )

    def add_indicator(self, terminal: Terminal, output: BoundaryOutput) -> None:
        group = self._group(terminal)
        if group is not None:
            group.drives.append(NetlistIndicatorDrive(output))

    def take(self, key: PlacementKey) -> PlacedTerminals:
        """The reads and drives of the body ``key`` names. The structure builder
        calls this for every body it builds."""
        self._claimed.add(key)
        group = self._groups.get(key, PlacedTerminals())
        for drive in group.drives:
            drive.output.placed = True
        return group

    def check_all_claimed(self) -> None:
        """Every placed terminal sits in a body the netlist built, else the
        netlist would drop its line."""
        for (owner, frame), group in self._groups.items():
            if (owner, frame) in self._claimed:
                continue
            names = [r.name for r in group.reads] + [
                d.output.name for d in group.drives
            ]
            where = f"structure {uid_of(owner)}"
            if frame is not None:
                where += f", frame {frame}"
            raise PlacementError(
                f"front-panel terminals {', '.join(names)} sit inside {where}, "
                f"which lvnet does not write"
            )


# Where a placed terminal's line sits: ``(structure uid, frame value)``; the frame
# value is ``None`` for a loop or In Place Element body.
Scope = tuple[str, str | None]
_SINGLE_BODY_KINDS = frozenset({"for", "while", "inplace"})


@dataclass(frozen=True)
class PlacedScopes:
    """Where each placed terminal's line sits. A terminal is keyed by its uid on the
    block diagram (its name when a hand-built module has none); ``drives`` is also
    keyed by the identity of the module's ``BoundaryOutput``. A terminal on the VI's
    own diagram is in neither."""

    reads: dict[str, Scope] = field(default_factory=dict)
    drives: dict[str, Scope] = field(default_factory=dict)
    drives_by_output: dict[int, Scope] = field(default_factory=dict)


def placed_scopes(module: NetlistModule) -> PlacedScopes:
    """The scope of every placed control and indicator of ``module``."""
    found = PlacedScopes()

    def walk(items: list[NetlistItem]) -> None:
        for item in items:
            if not isinstance(item, NetlistScope):
                continue
            for frame in item.frames:
                where: Scope = (
                    item.uid,
                    None if item.kind in _SINGLE_BODY_KINDS else frame.value,
                )
                for inner in frame.body:
                    if isinstance(inner, NetlistControlRead):
                        found.reads[inner.terminal_uid or inner.name] = where
                    elif isinstance(inner, NetlistIndicatorDrive):
                        out = inner.output
                        found.drives[out.terminal_uid or out.name] = where
                        found.drives_by_output[id(out)] = where
                walk(frame.body)

    walk(module.body)
    return found
