"""VISA Configure Serial Port (Instr): the caller diagram generated from the
catalog entry the recorded path names (the connector pane numbers are the ones the
corpus callers record) sets pyvisa's serial attributes, and its Python is run
against a stand-in for ``pyvisa`` that records each attribute.

pyvisa's names are those of its documented ``SerialInstrument``: ``timeout``,
``baud_rate``, ``data_bits``, ``parity``, ``stop_bits``, ``flow_control`` and
``end_input`` (``constants.SerialTermination``), and ``set_visa_attribute`` with
``constants.ResourceAttribute.termchar`` for the termination character. The values
(parity 0 to 4, stop bits 10, 15 and 20, flow control 0 to 5) are the ones NI's
page for the VI gives, which pyvisa's constants share."""

from __future__ import annotations

import enum
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.vilib_caller import BOOL, I32, primitive, run_catalog_call

REF = primitive("Refnum")
RECORDED = ["<vilib>", "Instr", "_visa.llb", "VISA Configure Serial Port (Instr).vi"]
PANE = [
    (0, "input", REF),
    (1, "input", I32),
    (2, "input", I32),
    (3, "input", BOOL),
    (4, "output", REF),
    (5, "input", I32),
    (7, "input", I32),
    (9, "input", I32),
    (11, "input", ERROR_CLUSTER),
    (12, "input", I32),
    (13, "input", I32),
    (15, "output", ERROR_CLUSTER),
]
INPUTS = {
    "instr": (0, REF),
    "timeout": (1, I32),
    "termination_char": (2, I32),
    "enable": (3, BOOL),
    "baud": (5, I32),
    "bits": (7, I32),
    "parity": (9, I32),
    "stop": (12, I32),
    "flow": (13, I32),
}


class SerialTermination(enum.IntEnum):
    none = 0
    termination_char = 2


class ResourceAttribute(enum.IntEnum):
    termchar = 0x3FFF0018


class FakeSerial:
    """Records the attributes set on it, in order."""

    def __init__(self) -> None:
        object.__setattr__(self, "sets", [])

    def __setattr__(self, name: str, value: Any) -> None:
        self.sets.append((name, value))

    def set_visa_attribute(self, name: Any, state: Any) -> None:
        self.sets.append((f"visa:{name.name}", state))


@pytest.fixture
def fake_pyvisa(monkeypatch: pytest.MonkeyPatch) -> None:
    pyvisa = types.ModuleType("pyvisa")
    constants = types.ModuleType("pyvisa.constants")
    constants.SerialTermination = SerialTermination  # type: ignore[attr-defined]
    constants.ResourceAttribute = ResourceAttribute  # type: ignore[attr-defined]
    pyvisa.constants = constants  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    monkeypatch.setitem(sys.modules, "pyvisa.constants", constants)


def _configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **given: Any
) -> tuple[FakeSerial, Any]:
    port = FakeSerial()
    wired = {name: INPUTS[name] for name in ("instr", *given)}
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        RECORDED,
        PANE,
        wired,
        {"out": (4, REF)},
        {"instr": port, **given},
    )
    return port, result


def test_every_input_sets_its_pyvisa_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_pyvisa: None
) -> None:
    port, result = _configure(
        tmp_path,
        monkeypatch,
        timeout=5000,
        termination_char=0xD,
        enable=False,
        baud=115200,
        bits=7,
        parity=2,
        stop=20,
        flow=3,
    )
    assert port.sets == [
        ("timeout", 5000),
        ("end_input", SerialTermination.none),
        ("visa:termchar", 0xD),
        ("baud_rate", 115200),
        ("data_bits", 7),
        ("parity", 2),
        ("stop_bits", 20),
        ("flow_control", 3),
    ]
    assert result.out is port


def test_an_unwired_input_has_the_default_NI_gives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_pyvisa: None
) -> None:
    """10 s timeout, termination character 0xA recognised, 9600 baud, 8 data
    bits, no parity, 1 stop bit (10) and no flow control."""
    port, _ = _configure(tmp_path, monkeypatch)
    assert port.sets == [
        ("timeout", 10000),
        ("end_input", SerialTermination.termination_char),
        ("visa:termchar", 0xA),
        ("baud_rate", 9600),
        ("data_bits", 8),
        ("parity", 0),
        ("stop_bits", 10),
        ("flow_control", 0),
    ]


def test_the_polymorphic_vi_name_finds_the_same_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_pyvisa: None
) -> None:
    """A caller that records the polymorphic VI's own file reaches the entry."""
    port = FakeSerial()
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "Instr", "_visa.llb", "VISA Configure Serial Port.vi"],
        PANE,
        {"instr": (0, REF), "baud": (5, I32)},
        {"out": (4, REF)},
        {"instr": port, "baud": 19200},
    )
    assert ("baud_rate", 19200) in port.sets
    assert result.out is port
