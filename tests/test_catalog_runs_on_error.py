"""The catalog's ``runs_on_error`` field: the entries whose NI page says the node
runs even when an error comes in on its error in. Each was checked against NI's
public page (the polymorphic Digital waveform VIs against their instance pages).
JKI State Machine's Parse State Queue is not an NI VI: its own diagram reads the
error in, so it runs on one."""

from __future__ import annotations

import json
from pathlib import Path

from lvkit.primitive_resolver import get_resolver

DATA = Path(__file__).parent.parent / "src" / "lvkit" / "data"

VILIB_ENTRIES = frozenset(
    {
        "addons:Parse State Queue__jki_lib_state_machine",
        "application-control:Close Reference Function",
        "application-control:Delete Data Value Reference Function",
        "cluster-class-variant:Preserve Run-Time Class Function",
        "connectivity:Close Pipe",
        "connectivity:Close Python Session Function",
        "connectivity:Close Reference Function",
        "connectivity:Close Registry Key",
        "connectivity:Unregister For Events Function",
        "data-communication:Bluetooth Close Connection Function",
        "data-communication:Close Variable Connection Function",
        "data-communication:DataSocket Close Function",
        "data-communication:Destroy Stream Endpoint Function",
        "data-communication:IrDA Close Connection Function",
        "data-communication:TCP Close Connection Function",
        "data-communication:UDP Close Function",
        "dialog-user-interface:Destroy User Event Function",
        "dialog-user-interface:Unregister For Events Function",
        "dialog-user-interface:Unset Busy",
        "file-io:Close File Function",
        "file-io:Close Zip File",
        "file-io:TDMS Close Function",
        "instrument-io:GPIB Status Function",
        "instrument-io:VISA Close Function",
        "synchronization:Acquire Semaphore",
        "synchronization:Destroy Rendezvous",
        "synchronization:Release Notifier Function",
        "synchronization:Release Queue Function",
        "synchronization:Release Semaphore",
        "synchronization:Release Semaphore_71",
        "synchronization:Release Semaphore Reference",
        "waveform:Append Digital Samples",
        "waveform:Append Digital Signals",
        "waveform:Compress Digital",
        "waveform:Digital Comparison",
        "waveform:Digital Signal Subset",
        "waveform:Digital Size",
        "waveform:Invert Digital",
        "waveform:Search for Digital Pattern",
        "waveform:Uncompress Digital",
    }
)

PRIMITIVE_ENTRIES = frozenset(
    {
        "1085:DataSocket Close",
        "1405:Close File",
        "1713:TCP Close Connection",
        "1726:UDP Close",
        "1922:VISA Close",
        "1959:GPIB Status",
        "2013:TDMS Close",
        "2075:Destroy User Event",
        "2076:Unregister For Events",
        "23783:Close Variable Connection",
        "23794:Destroy Stream Endpoint",
        "2409:Delete Data Value Reference",
        "24113:TDMS Close",
        "24207:Close Python Session",
        "8011:Close Reference",
        "8018:Preserve Run-Time Class",
        "8052:Close File",
        "9102:Release Notifier",
        "9109:Release Queue",
        "9144:IrDA Close Connection",
        "9148:Bluetooth Close Connection",
    }
)


def _marked(directory: str) -> set[str]:
    """``<file>:<name>`` of each vi.lib entry, ``<primResID>:<name>`` of each
    primitive, that carries the field (which must be ``true``)."""
    marked: set[str] = set()
    for path in sorted((DATA / directory).glob("*.json")):
        data = json.loads(path.read_text())
        if directory == "vilib":
            entries = [(path.stem, e) for e in data.get("entries", [])]
        else:
            entries = list((data.get("primitives") or {}).items())
        for ident, entry in entries:
            if isinstance(entry, dict) and "runs_on_error" in entry:
                assert entry["runs_on_error"] is True, (path.name, entry["name"])
                marked.add(f"{ident}:{entry['name']}")
    return marked


def test_the_vilib_entries_that_run_despite_an_error_are_the_pinned_ones() -> None:
    assert _marked("vilib") == VILIB_ENTRIES


def test_the_primitives_that_run_despite_an_error_are_the_pinned_ones() -> None:
    assert _marked("primitives") == PRIMITIVE_ENTRIES


def test_the_resolver_reports_the_field() -> None:
    resolver = get_resolver()
    assert resolver.get_by_id(8052)["runs_on_error"] is True
    closes = resolver.resolve(prim_id=8052)
    assert closes is not None and closes.runs_on_error is True
    other = resolver.resolve(prim_id=8063)
    assert other is not None and other.runs_on_error is False
