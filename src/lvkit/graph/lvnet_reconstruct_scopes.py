"""The drive lines of a structure, read back into the structure's model.

The text lists a structure's drives frame by frame; the model keeps them by
output tunnel (a case's or event's ``GammaMerge``, a Sequence's or Disable
structure's ``FrameOutputDrive`` list, a loop's count and conditional wires).
Each function here takes the already-parsed frames' drives and rebuilds one
structure family's share, in the order the structure's builder makes it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..models import DisableStructureKind
from .lvnet_grammar import _LVNET_SEQUENCE_LOCAL_UNREADABLE, _LVNET_UNWIRED
from .lvnet_parse import (
    DriveKind,
    ParsedDrive,
    ParsedFrame,
    ParsedScope,
    _unescape_lvnet_string,
)
from .lvnet_reconstruct_common import (
    HandleTarget,
    LvnetReconstructError,
    parse_source_token,
    require_netref,
)
from .netlist_models import (
    FrameOutputDrive,
    GammaCase,
    GammaMerge,
    NetRef,
    ScopeOutput,
    SequenceLocalDrive,
    SequenceLocalUnreadable,
    StructureInput,
)

Registry = dict[str, HandleTarget]
FrameDrive = SequenceLocalDrive | SequenceLocalUnreadable | FrameOutputDrive
_TRAILING_NUMBER_RE = re.compile(r"(\d+)$")


def structure_input(drive: ParsedDrive, registry: Registry) -> StructureInput:
    """A structure's opening ``<kind>_<uid>::in<K>`` / ``::timeout`` /
    ``::dynamic`` line."""
    return StructureInput(
        net=drive.net,
        value=require_netref(
            parse_source_token(drive.source, registry), f"{drive.net} input"
        ),
    )


def disabled_frame_value(label: str, kind: DisableStructureKind) -> str:
    """A Disable frame's value from its header label: a Conditional Disable
    frame's label is its symbol condition, a string literal."""
    if kind is DisableStructureKind.CONDITIONAL and label.startswith('"'):
        return _unescape_lvnet_string(label)
    return label


def _frame_output_drive(
    drive: ParsedDrive, frame: str, registry: Registry
) -> FrameOutputDrive:
    return FrameOutputDrive(
        net=drive.net,
        frame=frame,
        value=require_netref(
            parse_source_token(drive.source, registry), f"{drive.net} drive"
        ),
    )


def _in_tunnel_order(
    drives: Sequence[FrameDrive], frame_values: list[str]
) -> list[ScopeOutput]:
    """The per-frame drives in the order the structure's builder makes them:
    sequence locals, then output tunnels; each by the number ending its net,
    then by frame. The text lists them frame by frame."""

    def key(drive: FrameDrive) -> tuple[int, int, int]:
        m = _TRAILING_NUMBER_RE.search(drive.net)
        return (
            int(isinstance(drive, FrameOutputDrive)),
            int(m.group(1)) if m else 0,
            frame_values.index(drive.frame),
        )

    return list(sorted(drives, key=key))


def _sequence_local_unreadable_reason(source: str) -> str | None:
    """The reason in a sequence-local drive written as ``(sequence local
    unreadable "<reason>")``, or ``None`` for an ordinary source."""
    prefix = f'({_LVNET_SEQUENCE_LOCAL_UNREADABLE} "'
    text = source.lstrip()
    if not (text.startswith(prefix) and text.endswith(")")):
        return None
    return _unescape_lvnet_string(text[len(prefix) - 1 : -1])


def sequence_outputs(
    frames: Sequence[ParsedFrame], values: list[str], registry: Registry
) -> list[ScopeOutput]:
    """A Sequence's sequence locals and frame output drives; ``values`` is each
    frame's value."""
    drives: list[FrameDrive] = []
    for frame, value in zip(frames, values, strict=True):
        for d in frame.drives:
            reason = _sequence_local_unreadable_reason(d.source)
            if reason is not None:
                drives.append(
                    SequenceLocalUnreadable(net=d.net, frame=value, reason=reason)
                )
            elif d.kind is DriveKind.SEQUENCE_LOCAL:
                drives.append(
                    SequenceLocalDrive(
                        net=d.net,
                        frame=value,
                        value=require_netref(
                            parse_source_token(d.source, registry),
                            f"{d.net} drive",
                        ),
                    )
                )
            else:
                drives.append(_frame_output_drive(d, value, registry))
    return _in_tunnel_order(drives, values)


def case_outputs(
    frames: Sequence[ParsedFrame], keys: list[str], registry: Registry
) -> list[ScopeOutput]:
    """A Case structure's output tunnels: one ``GammaMerge`` per net, with the
    source each frame (keyed by ``keys``) drives onto it."""
    cases_by_net: dict[str, list[GammaCase]] = {}
    for frame, key in zip(frames, keys, strict=True):
        for d in frame.drives:
            cases_by_net.setdefault(d.net, []).append(
                GammaCase(frame_key=key, source=parse_source_token(d.source, registry))
            )
    return [
        GammaMerge(net=net, selector=None, cases=cases)
        for net, cases in cases_by_net.items()
    ]


def event_outputs(
    frames: Sequence[ParsedFrame], labels: list[str], registry: Registry
) -> list[ScopeOutput]:
    """An Event Structure's output tunnels (a ``GammaMerge`` each) followed by the
    frames that drive the dynamic pair's right terminal."""
    dynamic = [
        _frame_output_drive(d, label, registry)
        for frame, label in zip(frames, labels, strict=True)
        for d in frame.drives
        if d.kind is DriveKind.EVENT_DYNAMIC
    ]
    tunnels = case_outputs(
        [
            ParsedFrame(
                label=f.label,
                drives=tuple(
                    d for d in f.drives if d.kind is not DriveKind.EVENT_DYNAMIC
                ),
            )
            for f in frames
        ],
        labels,
        registry,
    )
    return [*tunnels, *dynamic]


class LoopWires:
    """What a loop's body-level drive lines say: its structure inputs, the
    count wire (``count``), the conditional-terminal wire (``selector``,
    ``None`` for ``?``) and whether the line is present."""

    def __init__(self, scope: ParsedScope, registry: Registry) -> None:
        self.inputs: list[StructureInput] = []
        self.count: NetRef | None = None
        self.selector: NetRef | None = None
        self.has_condition = False
        self.structure_uid: str | None = None
        for d in scope.drives:
            if d.kind is DriveKind.STRUCTURE_INPUT:
                self.inputs.append(structure_input(d, registry))
                continue
            if d.kind not in (DriveKind.LOOP_COUNT, DriveKind.LOOP_CONDITION):
                raise LvnetReconstructError(
                    f"unrecognized drive line in a {scope.kind} body: {d.net!r}"
                )
            self.structure_uid = self.structure_uid or d.structure_uid
            if d.kind is DriveKind.LOOP_COUNT:
                self.count = require_netref(
                    parse_source_token(d.source, registry), f"{d.net} drive"
                )
                continue
            self.has_condition = True
            if d.source != _LVNET_UNWIRED:
                self.selector = require_netref(
                    parse_source_token(d.source, registry), f"{d.net} drive"
                )
