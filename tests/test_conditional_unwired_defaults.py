"""An unwired input whose default NI states in terms of the node's other inputs
(Set File Position's ``from``) or as a behaviour (File Dialog's ``start path``):
the generated code runs with the documented default."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import get_resolver
from lvkit.terminal_default import TerminalDefault, default_for_wiring
from tests.native_vi import generate, node, scalar

I32_T = scalar("NumInt32")
I64_T = scalar("NumInt64")
BOOL_T = scalar("Boolean")
PATH_T = scalar("Path")
REFNUM_T = scalar("Refnum")
ERROR_T = LVType(kind=LVTypeKind.CLUSTER)


def _set_position(
    wire_offset: bool, wire_from: bool
) -> tuple[object, list]:
    terminals = [
        (0, "output", ERROR_T),
        (3, "output", REFNUM_T),
        (4, "input", ERROR_T),
        (5, "input", I32_T),
        (6, "input", I64_T),
        (7, "input", REFNUM_T),
    ]
    inputs = {"file": REFNUM_T}
    wires = [("fp:file", "sp:7"), ("sp:3", "fp:out")]
    if wire_offset:
        inputs["offset"] = I64_T
        wires.append(("fp:offset", "sp:6"))
    if wire_from:
        inputs["start"] = I32_T
        wires.append(("fp:start", "sp:5"))
    gaps: list = []
    generated = generate(
        inputs,
        {"out": REFNUM_T},
        [node("sp", 8073, terminals)],
        wires,
        gaps=gaps,
    )
    assert gaps == []
    return generated.function, gaps


def _file() -> io.BytesIO:
    handle = io.BytesIO(b"abcdefgh")
    handle.seek(3)
    return handle


def test_from_unwired_with_offset_wired_sets_the_mark_from_the_start() -> None:
    function, _ = _set_position(wire_offset=True, wire_from=False)
    handle = _file()
    function(handle, 2)  # type: ignore[operator]
    assert handle.tell() == 2


def test_from_and_offset_unwired_leave_the_mark_at_the_current_position() -> None:
    function, _ = _set_position(wire_offset=False, wire_from=False)
    handle = _file()
    function(handle)  # type: ignore[operator]
    assert handle.tell() == 3


@pytest.mark.parametrize(
    ("mode", "offset", "expected"),
    [(0, 2, 2), (1, -1, 7), (2, 2, 5)],  # start, end, current (LabVIEW's numbers)
)
def test_a_wired_from_is_labviews_start_end_current(
    mode: int, offset: int, expected: int
) -> None:
    function, _ = _set_position(wire_offset=True, wire_from=True)
    handle = _file()
    function(handle, offset, mode)  # type: ignore[operator]
    assert handle.tell() == expected


def test_set_file_position_catalog_records_the_conditional_default() -> None:
    entry = get_resolver().resolve(prim_id="8073")
    assert entry is not None
    terminal = next(t for t in entry.terminals if t.name == "from (0:start)")
    assert terminal.default == 0
    assert terminal.default_when_unwired == {6: 2}


def _dialog(wire_start_path: bool) -> object:
    terminals = [
        (0, "output", ERROR_T),
        (8, "input", ERROR_T),
        (1, "output", BOOL_T),
        (2, "output", BOOL_T),
        (3, "output", PATH_T),
        (11, "input", PATH_T),
    ]
    inputs: dict = {}
    wires = [("dlg:1", "fp:cancelled"), ("dlg:2", "fp:exists"), ("dlg:3", "fp:path")]
    if wire_start_path:
        inputs["start"] = PATH_T
        wires.append(("fp:start", "dlg:11"))
    gaps: list = []
    generated = generate(
        inputs,
        {"cancelled": BOOL_T, "exists": BOOL_T, "path": PATH_T},
        [node("dlg", 8058, terminals)],
        wires,
        gaps=gaps,
    )
    assert gaps == []
    return generated.function


def test_file_dialog_without_start_path_selects_no_path() -> None:
    result = _dialog(wire_start_path=False)()  # type: ignore[operator]
    assert result.path is None
    assert result.exists is False


def test_file_dialog_with_a_start_path_returns_it(tmp_path: Path) -> None:
    result = _dialog(wire_start_path=True)(tmp_path)  # type: ignore[operator]
    assert result.path == tmp_path
    assert result.exists is True


def test_the_first_unwired_input_named_decides_the_default() -> None:
    entry = TerminalDefault(default=0, default_when_unwired={6: 2, 9: 5})
    assert default_for_wiring(entry, {6, 9}) == 0
    assert default_for_wiring(entry, {9}) == 2
    assert default_for_wiring(entry, {6}) == 5
    assert default_for_wiring(TerminalDefault(), set()) is None
