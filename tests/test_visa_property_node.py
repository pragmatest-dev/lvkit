"""A Property Node on a VISA resource (an ``Instr`` reference): each row is a VISA
attribute, whose identifier the row records as its property code (Serial
Settings:End Mode for Writes records 0x3FFF00B4, TCP/IP Settings:Computer Hostname
0xBFFF0196, Message Based Settings:Send End Enable 0x3FFF0016). Each case is a
handmade diagram whose generated package is imported and run against a stand-in
for ``pyvisa`` that records the attribute reads and writes."""

from __future__ import annotations

import enum
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import LVType, LVTypeKind
from tests.class_graphs import Project, Recipe, pane, wire
from tests.test_control_references import property_node
from tests.test_front_panel_reference import BOOL, ERROR, STRING

VISA_REF = LVType(
    kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="VisaRef"
)
U16 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16")

# LabVIEW stores the code as a signed 32-bit number.
END_OUT = 0x3FFF00B4
HOSTNAME = 0xBFFF0196 - (1 << 32)
SEND_END = 0x3FFF0016

READ_ONLY = -1073807331
TIMEOUT = -1073807339


class VisaIOError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"VISA status {code}")
        self.error_code = code


class ResourceAttribute(enum.IntEnum):
    end_output = END_OUT
    send_end = SEND_END
    tcpip_hostname = 0xBFFF0196


class Session:
    """A VISA session: its attributes, and the calls made on it."""

    def __init__(self) -> None:
        self.attributes: dict[int, Any] = {
            END_OUT: 0,
            0xBFFF0196: "dev.local",
            SEND_END: 1,
        }
        self.log: list[tuple[str, int, Any]] = []

    def get_visa_attribute(self, name: ResourceAttribute) -> Any:
        self.log.append(("get", int(name), None))
        return self.attributes[int(name)]

    def set_visa_attribute(self, name: ResourceAttribute, state: Any) -> None:
        self.log.append(("set", int(name), state))
        if int(name) == 0xBFFF0196:
            raise VisaIOError(READ_ONLY)
        self.attributes[int(name)] = state


@pytest.fixture(autouse=True)
def fake_pyvisa(monkeypatch: pytest.MonkeyPatch) -> None:
    pyvisa = types.ModuleType("pyvisa")
    constants = types.ModuleType("pyvisa.constants")
    constants.ResourceAttribute = ResourceAttribute  # type: ignore[attr-defined]
    errors = types.ModuleType("pyvisa.errors")
    errors.VisaIOError = VisaIOError  # type: ignore[attr-defined]
    pyvisa.constants = constants  # type: ignore[attr-defined]
    pyvisa.errors = errors  # type: ignore[attr-defined]
    for name, module in (
        ("pyvisa", pyvisa),
        ("pyvisa.constants", constants),
        ("pyvisa.errors", errors),
    ):
        monkeypatch.setitem(sys.modules, name, module)


def visa_vi(rows: list[tuple[int, str, LVType]]) -> Recipe:
    """A VI with one Property Node on its input ``session``: a row for each
    ``(code, "w" or "r", type)`` in order, a write row taking the input ``w<i>`` and
    a read row giving the output ``r<i>``. Its error out is the output
    ``error out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(
            vi,
            "prop",
            "Instr",
            [
                (f"row{i}", (code,), "input" if kind == "w" else "output", lv_type)
                for i, (code, kind, lv_type) in enumerate(rows)
            ],
        )
        node.terminals[0].lv_type = VISA_REF
        node.terminals[1].lv_type = VISA_REF
        wires = [
            wire(vi, "session", "prop.ref"),
            wire(vi, "prop.err_out", "err"),
        ]
        inputs = [pane(vi, "input", "session", VISA_REF, "session", 0)]
        outputs = []
        for i, (_, kind, lv_type) in enumerate(rows):
            if kind == "w":
                inputs.append(pane(vi, "input", f"w{i}", lv_type, f"w{i}", len(inputs)))
                wires.append(wire(vi, f"w{i}", f"prop.row{i}"))
            else:
                outputs.append(
                    pane(vi, "output", f"r{i}", lv_type, f"r{i}", len(outputs))
                )
                wires.append(wire(vi, f"prop.row{i}", f"r{i}"))
        outputs.append(pane(vi, "output", "err", ERROR, "error out", len(outputs)))
        return (
            VIContext(name=vi, inputs=inputs, outputs=outputs, data_flow=wires),
            [node],
        )

    return build


def generated(tmp_path: Path, rows: list[tuple[int, str, LVType]]) -> Any:
    project = Project(tmp_path)
    project.add_vi("Visa", visa_vi(rows))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    return package


def test_a_read_and_a_write_go_through_the_attribute_of_the_row(
    tmp_path: Path,
) -> None:
    package = generated(
        tmp_path,
        [(END_OUT, "r", U16), (END_OUT, "w", U16), (END_OUT, "r", U16)],
    )
    with package.use() as module:
        visa = module("visa").visa
        session = Session()
        result = visa(session=session, w1=3)
        assert (result.r0, result.r2, result.error_out) == (0, 3, None)
        assert session.log == [
            ("get", END_OUT, None),
            ("set", END_OUT, 3),
            ("get", END_OUT, None),
        ]


def test_a_string_property_is_read_as_the_string(tmp_path: Path) -> None:
    package = generated(tmp_path, [(HOSTNAME, "r", STRING)])
    with package.use() as module:
        result = module("visa").visa(session=Session())
        assert (result.r0, result.error_out) == ("dev.local", None)


def test_a_boolean_property_is_visas_zero_or_one(tmp_path: Path) -> None:
    package = generated(tmp_path, [(SEND_END, "w", BOOL), (SEND_END, "r", BOOL)])
    with package.use() as module:
        visa = module("visa").visa
        session = Session()
        result = visa(session=session, w0=False)
        assert result.r1 is False
        assert session.attributes[SEND_END] == 0
        assert ("set", SEND_END, 0) in session.log
        session.attributes[SEND_END] = 1
        assert visa(session=session, w0=True).r1 is True


def test_a_visa_failure_is_the_error_out_and_stops_the_rows_after_it(
    tmp_path: Path,
) -> None:
    package = generated(
        tmp_path,
        [(HOSTNAME, "w", STRING), (END_OUT, "w", U16), (END_OUT, "r", U16)],
    )
    with package.use() as module:
        session = Session()
        result = module("visa").visa(session=session, w0="x", w1=2)
        assert result.error_out.code == READ_ONLY
        assert session.attributes[END_OUT] == 0
        assert [entry[0] for entry in session.log] == ["set"]
        assert result.r2 == 0


def test_a_property_of_another_kind_of_reference_stays_a_gap(tmp_path: Path) -> None:
    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        context, nodes = visa_vi([(END_OUT, "r", U16)])(vi)
        # the same row on a reference that is not a VISA resource
        plain = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
        nodes[0].terminals[0].lv_type = plain  # type: ignore[attr-defined]
        context.inputs[0].lv_type = plain
        return context, nodes

    project = Project(tmp_path)
    project.add_vi("Visa", build)
    gaps: list[Gap] = []
    project.generate(gaps)
    assert len(gaps) == 1
    assert "has no Python equivalent" in gaps[0].reason


def test_a_failure_of_a_read_gives_the_default_of_its_output(tmp_path: Path) -> None:
    package = generated(tmp_path, [(END_OUT, "r", U16), (HOSTNAME, "r", STRING)])

    class Failing(Session):
        def get_visa_attribute(self, name: ResourceAttribute) -> Any:
            raise VisaIOError(TIMEOUT)

    with package.use() as module:
        result = module("visa").visa(session=Failing())
        assert result.error_out.code == TIMEOUT
        assert (result.r0, result.r1) == (0, "")
