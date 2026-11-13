"""VISA Open, Read, Write, Flush I/O Buffer and Close run as Python on a pyvisa
resource, here a stand-in that records each call.

pyvisa (readthedocs, "Resource classes" and "Resource Manager"):
``ResourceManager.open_resource(resource_name, access_mode, open_timeout)``,
``MessageBasedResource.read_bytes(count, break_on_termchar)``, ``write_raw(message)``
("Returns: Number of bytes written"), ``flush(mask)`` and ``close()``. A failure is
``pyvisa.errors.VisaIOError`` with the VISA ``error_code``."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from tests.helpers import error_cluster_type
from tests.primitive_caller import run_primitive
from tests.vilib_caller import BOOL, STRING, U32, primitive

ERROR = error_cluster_type()
REF = primitive("Refnum")
U16 = primitive("NumUInt16")
TIMEOUT_CODE = -1073807339


class VisaIOError(Exception):
    def __init__(self, error_code: int) -> None:
        super().__init__(f"VI_ERROR ({error_code})")
        self.error_code = error_code


class FakeResource:
    def __init__(self, incoming: bytes = b"", fail: int | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.incoming = incoming
        self.fail = fail

    def _check(self) -> None:
        if self.fail is not None:
            raise VisaIOError(self.fail)

    def read_bytes(self, count: int, break_on_termchar: bool = False) -> bytes:
        self.calls.append(("read_bytes", count, break_on_termchar))
        self._check()
        return self.incoming[:count]

    def write_raw(self, message: bytes) -> int:
        self.calls.append(("write_raw", message))
        self._check()
        return len(message)

    def flush(self, mask: int) -> None:
        self.calls.append(("flush", mask))
        self._check()

    def close(self) -> None:
        self.calls.append(("close",))
        self._check()


class FakeManager:
    opened: list[tuple[Any, ...]] = []

    def open_resource(self, name: str, access_mode: int = 0, open_timeout: int = 0):
        FakeManager.opened.append((name, access_mode, open_timeout))
        if name == "bad":
            raise VisaIOError(-1073807343)
        return FakeResource()


@pytest.fixture(autouse=True)
def fake_pyvisa(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeManager.opened = []
    pyvisa = types.ModuleType("pyvisa")
    errors = types.ModuleType("pyvisa.errors")
    errors.VisaIOError = VisaIOError  # type: ignore[attr-defined]
    pyvisa.errors = errors  # type: ignore[attr-defined]
    pyvisa.ResourceManager = FakeManager  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    monkeypatch.setitem(sys.modules, "pyvisa.errors", errors)


def test_write_sends_the_bytes_and_returns_the_count() -> None:
    resource = FakeResource()
    source, result = run_primitive(
        1926,
        [
            (0, "output", ERROR),
            (2, "output", U32),
            (3, "output", REF),
            (8, "input", ERROR),
            (10, "input", STRING),
            (11, "input", REF),
        ],
        {"resource": (11, REF), "data": (10, STRING)},
        {"count": (2, U32), "resource_out": (3, REF), "error": (0, ERROR)},
        {"resource": resource, "data": "ab\xe9\r"},
    )
    assert resource.calls == [("write_raw", b"ab\xe9\r")]
    assert result.count == 4
    assert result.resource_out is resource
    assert result.error is None
    assert "lvkit" not in source


def test_a_failed_write_is_the_visa_error_code() -> None:
    resource = FakeResource(fail=TIMEOUT_CODE)
    _, result = run_primitive(
        1926,
        [
            (0, "output", ERROR),
            (2, "output", U32),
            (3, "output", REF),
            (8, "input", ERROR),
            (10, "input", STRING),
            (11, "input", REF),
        ],
        {"resource": (11, REF), "data": (10, STRING)},
        {"count": (2, U32), "error": (0, ERROR)},
        {"resource": resource, "data": "x"},
    )
    assert result.error.code == TIMEOUT_CODE
    assert result.error.source == "VISA Write"
    assert result.count == 0


def _read(resource: FakeResource, count: int):
    return run_primitive(
        1925,
        [
            (0, "output", ERROR),
            (1, "output", U32),
            (2, "output", STRING),
            (3, "output", REF),
            (8, "input", ERROR),
            (10, "input", U32),
            (11, "input", REF),
        ],
        {"resource": (11, REF), "count": (10, U32)},
        {"returned": (1, U32), "data": (2, STRING), "error": (0, ERROR)},
        {"resource": resource, "count": count},
    )


def test_read_returns_the_bytes_and_their_count() -> None:
    resource = FakeResource(incoming=b"OK\xe9\n")
    _, result = _read(resource, 10)
    assert resource.calls == [("read_bytes", 10, True)]
    assert result.data == "OK\xe9\n"
    assert result.returned == 4
    assert result.error is None


def test_read_that_times_out_is_error_with_the_visa_code() -> None:
    _, result = _read(FakeResource(fail=TIMEOUT_CODE), 4)
    assert result.error.code == TIMEOUT_CODE
    assert result.data == ""


def _flush(resource: FakeResource, mask: int | None):
    inputs = {"resource": (11, REF)}
    arguments: dict[str, Any] = {"resource": resource}
    if mask is not None:
        inputs["mask"] = (10, U16)
        arguments["mask"] = mask
    return run_primitive(
        2302,
        [
            (0, "output", ERROR),
            (3, "output", REF),
            (8, "input", ERROR),
            (10, "input", U16),
            (11, "input", REF),
        ],
        inputs,
        {"resource_out": (3, REF), "error": (0, ERROR)},
        arguments,
    )


def test_flush_passes_the_mask_and_returns_the_resource() -> None:
    resource = FakeResource()
    _, result = _flush(resource, 32)
    assert resource.calls == [("flush", 32)]
    assert result.resource_out is resource


def test_flush_mask_defaults_to_16() -> None:
    resource = FakeResource()
    _flush(resource, None)
    assert resource.calls == [("flush", 16)]


def _close(resource: FakeResource, error_in: Any = None, wire_error: bool = False):
    inputs = {"resource": (11, REF)}
    arguments: dict[str, Any] = {"resource": resource}
    if wire_error:
        inputs["error_in"] = (8, ERROR)
        arguments["error_in"] = error_in
    return run_primitive(
        1922,
        [(0, "output", ERROR), (8, "input", ERROR), (11, "input", REF)],
        inputs,
        {"error": (0, ERROR)},
        arguments,
    )


def test_close_closes_the_session() -> None:
    resource = FakeResource()
    source, result = _close(resource)
    assert resource.calls == [("close",)]
    assert result.error is None
    assert "lvkit" not in source


def test_close_runs_although_an_error_came_in() -> None:
    from tests.helpers import install_generated_package

    install_generated_package()
    from generated_package.errors import LabVIEWError

    resource = FakeResource()
    earlier = LabVIEWError(code=5, source="earlier", message="")
    _, result = _close(resource, earlier, wire_error=True)
    assert resource.calls == [("close",)]
    assert result.error is earlier


def _open(name: str, **given: Any):
    pane = [
        (0, "output", ERROR),
        (3, "output", REF),
        (7, "input", U32),
        (8, "input", ERROR),
        (9, "input", U32),
        (10, "input", BOOL),
        (11, "input", REF),
    ]
    arguments = {"name": name, **given}
    inputs = {"name": (11, REF)}
    for key in given:
        inputs[key] = (_OPEN[key], U32)
    return run_primitive(
        2308,
        pane,
        inputs,
        {"opened": (3, REF), "error": (0, ERROR)},
        arguments,
    )


_OPEN = {"open_timeout": 7, "access_mode": 9}


def test_open_gives_a_session_to_the_resource() -> None:
    source, result = _open("ASRL1::INSTR")
    assert FakeManager.opened == [("ASRL1::INSTR", 0, 0)]
    assert isinstance(result.opened, FakeResource)
    assert "lvkit" not in source


def test_open_passes_the_access_mode_and_timeout() -> None:
    _open("GPIB0::1::INSTR", open_timeout=2000, access_mode=1)
    assert FakeManager.opened == [("GPIB0::1::INSTR", 1, 2000)]


def test_open_of_a_bad_resource_is_the_visa_error() -> None:
    _, result = _open("bad")
    assert result.error.code == -1073807343
    assert result.opened is None


def test_a_neighbour_without_python_stays_a_gap() -> None:
    with pytest.raises(NotImplementedError):
        run_primitive(
            1923,
            [(0, "output", ERROR), (11, "input", STRING)],
            {"expression": (11, STRING)},
            {"error": (0, ERROR)},
            {"expression": "?*"},
            gaps=[],
        )
