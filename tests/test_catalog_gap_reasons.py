"""An entry with no Python yet says why in the gap its node raises."""

from __future__ import annotations

import pytest

from tests.prim_vi import Pin, generate, scalar


@pytest.mark.parametrize(
    ("prim_id", "name", "reason"),
    [
        (9000, "Current VI's Menubar", "menu bar"),
        (8069, "Read Datalog", "datalog file"),
        (24201, "Flatten To JSON", "mapping of a LabVIEW type to JSON"),
        (1427, "Default Directory", "current directory"),
    ],
)
def test_the_gap_carries_the_entry_reason(prim_id: int, name: str, reason: str) -> None:
    resolved_pins = {
        9000: [Pin(0, "output", scalar("Refnum"), sink="out")],
        8069: [Pin(0, "output", scalar("Refnum"), sink="out")],
        24201: [Pin(3, "output", scalar("String"), sink="out")],
        1427: [Pin(0, "output", scalar("Path"), sink="out")],
    }[prim_id]
    out_type = resolved_pins[0].lv_type
    _, fn = generate(prim_id, name, resolved_pins, {}, {"out": out_type})
    with pytest.raises(NotImplementedError, match=reason):
        fn()
