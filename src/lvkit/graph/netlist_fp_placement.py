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
from .netlist_models import (
    BoundaryOutput,
    NetlistControlRead,
    NetlistIndicatorDrive,
    NetlistItem,
    NetlistModule,
    NetlistScope,
)

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
            group.reads.append(NetlistControlRead(terminal.name or "input"))

    def add_indicator(self, terminal: Terminal, output: BoundaryOutput) -> None:
        group = self._group(terminal)
        if group is not None:
            output.placed = True
            group.drives.append(NetlistIndicatorDrive(output))

    def take(self, key: PlacementKey) -> PlacedTerminals:
        """The reads and drives of the body ``key`` names. The structure builder
        calls this for every body it builds."""
        self._claimed.add(key)
        return self._groups.get(key, PlacedTerminals())

    def release_unclaimed(self) -> None:
        """Back to the end of the diagram: the indicators of every body no
        structure builder asked for."""
        for key, group in self._groups.items():
            if key in self._claimed:
                continue
            for drive in group.drives:
                drive.output.placed = False


@dataclass(frozen=True)
class PlacedScopes:
    """Where each placed terminal's line sits, as ``(structure uid, frame
    value)`` (a loop body has frame value ``""``): ``reads`` by control name,
    ``drives`` by the identity of the module's ``BoundaryOutput``. A terminal on
    the VI's own diagram is in neither."""

    reads: dict[str, tuple[str, str]] = field(default_factory=dict)
    drives: dict[int, tuple[str, str]] = field(default_factory=dict)


def placed_scopes(module: NetlistModule) -> PlacedScopes:
    """The scope of every placed control and indicator of ``module``."""
    found = PlacedScopes()

    def walk(items: list[NetlistItem]) -> None:
        for item in items:
            if not isinstance(item, NetlistScope):
                continue
            for frame in item.frames:
                where = (item.uid, frame.value)
                for inner in frame.body:
                    if isinstance(inner, NetlistControlRead):
                        found.reads.setdefault(inner.name, where)
                    elif isinstance(inner, NetlistIndicatorDrive):
                        found.drives[id(inner.output)] = where
                walk(frame.body)

    walk(module.body)
    return found
