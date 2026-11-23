"""What changed in a node's or structure's settings between two versions of a VI.

The settings are the ones the netlist carries (``netlist_models`` settings
classes): a Compound Arithmetic node's mode, a loop's stop condition and parallel
options, a case's Case Insensitive Match, a Feedback Node's delay and initializer,
a shift register's initializer, a Shared Variable's path. Each is compared by
value, so a difference in any of them makes the node or structure modified.
"""

from __future__ import annotations

from .lvnet_settings import settings_changes
from .netlist_models import (
    MuMerge,
    NetlistFeedback,
    NetlistInstance,
    NetlistItem,
    NetlistModule,
    NetlistScope,
    NodeSettings,
)

# One item that carries settings: a node, a structure or a Feedback Node.
SettingsHolder = NetlistInstance | NetlistScope | NetlistFeedback


def _entries(item: SettingsHolder) -> list[tuple[str, NodeSettings | None]]:
    """The ``(what, settings)`` of each place ``item`` has settings: itself, and
    for a structure each of its shift registers (``shift register 0``)."""
    entries: list[tuple[str, NodeSettings | None]] = [("", item.settings)]
    if isinstance(item, NetlistScope):
        registers = [m for m in item.outputs if isinstance(m, MuMerge)]
        entries += [
            (f"shift register {k}", register.settings)
            for k, register in enumerate(registers)
        ]
    return entries


def settings_change_text(before: SettingsHolder, after: SettingsHolder) -> str:
    """``mode: and -> or``-style text of every setting that differs between two
    versions of one node or structure, joined by ``; `` (empty when none)."""
    changed: list[str] = []
    old = dict(_entries(before))
    for what, settings in _entries(after):
        if what not in old:
            continue
        for text in settings_changes(old[what], settings):
            changed.append(f"{what} {text}" if what else text)
    return "; ".join(changed)


def holders_by_uid(module: NetlistModule) -> dict[str, SettingsHolder]:
    """Every node, structure and Feedback Node of ``module`` by its uid."""
    holders: dict[str, SettingsHolder] = {}

    def walk(items: list[NetlistItem]) -> None:
        for item in items:
            if isinstance(item, NetlistInstance | NetlistFeedback):
                holders[item.uid] = item
            elif isinstance(item, NetlistScope):
                holders[item.uid] = item
                for frame in item.frames:
                    walk(frame.body)

    walk(module.body)
    return holders
