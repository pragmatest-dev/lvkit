"""Open Application Reference (8012): the local application is ``None``."""

from __future__ import annotations

import pytest

from tests.prim_vi import Pin, generate, scalar

REF = scalar("Refnum")
STRING = scalar("String")


def _open(machine_wired: bool):
    pins = [
        Pin(11, "input", STRING, source="machine" if machine_wired else None),
        Pin(10, "input", scalar("NumUInt16"), source=None),
        Pin(9, "input", scalar("NumInt32"), source=None),
        Pin(3, "output", REF, sink="app"),
    ]
    return generate(
        8012,
        "Open Application Reference",
        pins,
        {"machine": STRING} if machine_wired else {},
        {"app": REF},
    )


def test_no_machine_name_is_the_local_application() -> None:
    source, fn = _open(False)
    assert "lvkit" not in source
    assert fn().app is None


def test_a_machine_name_is_a_gap() -> None:
    _, fn = _open(True)
    with pytest.raises(NotImplementedError, match="remote machine"):
        fn("lab-pc")
