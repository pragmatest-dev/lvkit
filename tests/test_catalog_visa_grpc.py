"""VISA Open for gRPC Session.vi runs as Python on pyvisa, here a stand-in that
records each open.

NI grpc-device wiki, "Sharing Driver Sessions Between Clients": the VISA resource
name ``grpc://{address}/{resource_name}?init_behavior={n}&session_name={name}``
(NI-VISA 2024Q2 or later); init_behavior 0 to 4 are the open/close behavior items
Auto, Initialize and Close, Attach and Detach, Initialize and Detach, Attach and
Close. The pane numbers are the ones the measurement-plugin-labview callers
record."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.helpers import error_cluster_type, install_generated_package
from tests.vilib_caller import STRING, U32, primitive, run_catalog_call

ERROR = error_cluster_type()
REF = primitive("Refnum")
RECORDED = ["<vilib>", "NI-VISA", "VISA Open for gRPC Session.vi"]
OPTIONS = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="session name", type=STRING),
        ClusterField(name="address (localhost)", type=STRING),
        ClusterField(name="port (31763)", type=U32),
    ],
)
BEHAVIOR = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16")


class VisaIOError(Exception):
    def __init__(self, error_code: int) -> None:
        super().__init__(f"VI_ERROR ({error_code})")
        self.error_code = error_code


class FakeManager:
    opened: list[tuple[Any, ...]] = []

    def open_resource(self, name: str, access_mode: int = 0, open_timeout: int = 0):
        FakeManager.opened.append((name, access_mode, open_timeout))
        if "BAD" in name:
            raise VisaIOError(-1073807343)
        return SimpleNamespace(name=name)


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


def _open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: Any,
    behavior: int | None = 0,
    resource: str = "GPIB::2::INSTR",
    call_by_reference: bool = False,
    **given: Any,
):
    pane = [
        (0, "output", ERROR),
        (3, "output", REF),
        (6, "input", OPTIONS),
        (7, "input", U32),
        (8, "input", ERROR),
        (9, "input", U32),
        (10, "input", BEHAVIOR),
        (11, "input", REF),
    ]
    arguments: dict[str, Any] = {
        "options": options,
        "resource": resource,
        **given,
    }
    inputs: dict[str, tuple[int, LVType]] = {
        "options": (6, OPTIONS),
        "resource": (11, REF),
    }
    if behavior is not None:
        arguments["behavior"] = behavior
        inputs["behavior"] = (10, BEHAVIOR)
    for key, index in (("timeout", 7), ("access", 9)):
        if key in given:
            inputs[key] = (index, U32)
    if "error_in" in given:
        inputs["error_in"] = (8, ERROR)
    return run_catalog_call(
        tmp_path,
        monkeypatch,
        RECORDED,
        pane,
        inputs,
        {"opened": (3, REF), "error": (0, ERROR)},
        arguments,
        call_by_reference=call_by_reference,
    )


@pytest.mark.parametrize("behavior", [0, 1, 2, 3, 4])
def test_each_behavior_is_its_init_behavior_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behavior: int
) -> None:
    options = SimpleNamespace(session_name="MySession", address="host", port=31756)
    source, result = _open(tmp_path, monkeypatch, options, behavior)
    assert FakeManager.opened == [
        (
            f"grpc://host:31756/GPIB::2::INSTR?init_behavior={behavior}"
            "&session_name=MySession",
            0,
            0,
        )
    ]
    assert result.opened.name == FakeManager.opened[0][0]
    assert result.error is None
    assert "lvkit" not in source


def test_empty_address_and_zero_port_are_the_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = SimpleNamespace(session_name="s", address="", port=0)
    _open(tmp_path, monkeypatch, options, 2)
    assert FakeManager.opened[0][0] == (
        "grpc://localhost:31763/GPIB::2::INSTR?init_behavior=2&session_name=s"
    )


def test_an_unnamed_cluster_is_read_by_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open(tmp_path, monkeypatch, ("n", "10.0.0.5", 5000), 3)
    assert FakeManager.opened[0][0] == (
        "grpc://10.0.0.5:5000/GPIB::2::INSTR?init_behavior=3&session_name=n"
    )


def test_open_timeout_and_access_mode_are_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = SimpleNamespace(session_name="s", address="", port=0)
    _open(tmp_path, monkeypatch, options, 0, timeout=2500, access=1)
    assert FakeManager.opened[0][1:] == (1, 2500)


def test_a_visa_error_is_the_error_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = SimpleNamespace(session_name="s", address="", port=0)
    _, result = _open(tmp_path, monkeypatch, options, 0, resource="BAD")
    assert result.error.code == -1073807343
    assert result.error.source == "VISA Open for gRPC Session.vi"
    assert result.opened is None


def test_an_error_in_skips_the_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_generated_package()
    from generated_package.errors import LabVIEWError

    earlier = LabVIEWError(code=5, source="earlier", message="")
    options = SimpleNamespace(session_name="s", address="", port=0)
    _, result = _open(tmp_path, monkeypatch, options, 0, error_in=earlier)
    assert FakeManager.opened == []
    assert result.error is earlier


def test_a_call_by_reference_node_that_chooses_the_vi_runs_the_same_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = SimpleNamespace(session_name="s", address="a", port=1)
    _open(tmp_path, monkeypatch, options, 4, call_by_reference=True)
    assert FakeManager.opened[0][0] == (
        "grpc://a:1/GPIB::2::INSTR?init_behavior=4&session_name=s"
    )


def test_a_call_by_reference_of_a_vi_the_catalog_lacks_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Any] = []
    with pytest.raises(NotImplementedError):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            ["<vilib>", "NI-VISA", "No Such Session.vi"],
            [(0, "output", ERROR), (8, "input", ERROR)],
            {},
            {"error": (0, ERROR)},
            {},
            gaps=gaps,
            runtime_not_implemented=True,
            call_by_reference=True,
        )
    assert gaps
    assert "is not loaded with this VI" in gaps[0].message
