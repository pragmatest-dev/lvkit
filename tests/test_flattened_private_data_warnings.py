"""What pylabview prints while it reads a class's flattened private-data control
is captured: an unreadable control is an ``UnreadableVIError`` carrying those
lines (the class file's problem), and nothing reaches stderr."""

from __future__ import annotations

import pytest

from lvkit.flattened_typedesc import parse_flattened_private_data
from lvkit.run_report import UnreadableVIError


def test_an_unreadable_private_data_control_is_a_captured_error(capsys) -> None:
    raw = b"\x00\x00RSRC\r\n" + b"\x00\x03\x00\x00" + b"\xff" * 64
    with pytest.raises(UnreadableVIError) as caught:
        parse_flattened_private_data(raw)
    assert "private data control could not be read" in caught.value.reason
    assert capsys.readouterr().err == ""
