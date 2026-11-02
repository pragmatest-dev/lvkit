"""Tiny block diagrams with error wires, written as the heap XML the parser reads,
and the harness that loads one, prints every view of it and runs its generated
Python.

A ``Vi`` collects a front panel (controls and indicators on a connector pane), nodes
and wires; every ``add_*`` method returns the terminal uids a test wires with
``Vi.wire``. ``Vi.views`` loads it; ``Views.module`` generates the Python, writes it
beside stub modules for the subVIs and imports it.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
import textwrap
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from xml.sax.saxutils import escape

import pytest

from lvkit.codegen.errors_module import ERRORS_MODULE_SOURCE
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.load_mode import LoadMode
from lvkit.pipeline import generate_python
from tests.case_structure_fixture import FrameSpec, _selector_xml
from tests.structure_diagrams import dco as _dco
from tests.structure_diagrams import term as _term

# Flat type ids of ``TYPES``. ``ERR`` is the error cluster: status, code, source.
BOOL, I32, STR = 0, 1, 2
_STATUS, _CODE, _SOURCE, ERR = 3, 4, 5, 6
TYPES = [
    '<TypeDesc Type="Boolean" />',
    '<TypeDesc Type="NumInt32" />',
    '<TypeDesc Type="String" />',
    '<TypeDesc Type="Boolean" Label="status" />',
    '<TypeDesc Type="NumInt32" Label="code" />',
    '<TypeDesc Type="String" Label="source" />',
    '<TypeDesc Type="Cluster"><TypeDesc TypeID="3" /><TypeDesc TypeID="4" />'
    '<TypeDesc TypeID="5" /></TypeDesc>',
    '<TypeDesc Type="Path" Prop1="0xFFFFFFFF" Format="inline" />',
    '<TypeDesc Type="Refnum" RefType="ByteStream" Format="inline" />',
    '<TypeDesc Type="NumInt64" Prop1="0" Format="inline" />',
    '<TypeDesc Type="Refnum" RefType="Queue" Format="inline" />',
]
PATH, FILE, I64, QUEUE = 7, 8, 9, 10
_TYPE_NAMES = ["Boolean", "NumInt32", "String", "Boolean", "NumInt32", "String"]
_TYPE_NAMES += ["Cluster", "Path", "Refnum", "NumInt64", "Refnum"]
_FUNCTION = len(TYPES)

# The wiring rule flags of a connector pane slot.
REQUIRED, RECOMMENDED, OPTIONAL = 0x1000, 0x0800, 0x0000
_OUTPUT_FLAG = 0x0100

# innerLpTunDCO objFlags bit: auto-indexing enabled (parser/nodes/base.py).
_INDEXING_FLAG = 0x400000

# An error constant's saved value: status, code, source.
ERROR_CONSTANT_HEX = {
    "none": "00" + "00000000" + "00000000",
    "error5": "01" + "00000005" + "00000000",
    "warning5": "00" + "00000005" + "00000000",
}


def _ref(uid: int) -> str:
    return f'<SL__arrayElement uid="{uid}" />'


@dataclass(frozen=True)
class Pane:
    """One front-panel control or indicator."""

    uid: int
    label: str
    type_id: int
    indicator: bool
    rule: int | None  # the connector pane slot's wiring rule; None off the pane
    slot: int | None


def _slot_flags(pane: Pane) -> int:
    return (pane.rule or 0) | (_OUTPUT_FLAG if pane.indicator else 0)


@dataclass
class Scope:
    """The nodes and terminals (constants, faces of structure terminals) of one
    diagram: the VI's own or a structure frame's."""

    nodes: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    uid: int = 0
    srn_uid: int = 0


@dataclass
class Tunnel:
    """A border terminal of a structure: ``outer`` is wired outside, ``faces`` are
    its terminals inside (one per frame of a Case, one for a loop)."""

    outer: int
    faces: list[int]


@dataclass
class Structure:
    """A Case or a loop under construction."""

    selector: int  # the outer terminal of a Case selector, a loop's count
    faces: list[int]  # the inner faces of the selector, count or stop terminal
    scopes: list[Scope]
    tunnels: list[Tunnel]
    shift_registers: list[ShiftRegister] = field(default_factory=list)
    xml: Callable[[], str] = lambda: ""


@dataclass
class ShiftRegister:
    """``left_outer`` starts the register, ``left_face`` reads it in the body,
    ``right_face`` writes the next value, ``right_outer`` is read after the loop."""

    left_outer: int
    left_face: int
    right_face: int
    right_outer: int


@dataclass
class Vi:
    """A VI under construction. Uids are handed out from 100; a test uses the
    ones the ``add_*`` methods return."""

    name: str = "Err.vi"
    _uid: int = 100
    panes: list[Pane] = field(default_factory=list)
    root: Scope = field(default_factory=Scope)
    scope: Scope = field(init=False)
    structures: list[Structure] = field(default_factory=list)
    wires: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    fp_terms: dict[int, int] = field(default_factory=dict)  # pane uid -> terminal
    extra_types: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.scope = self.root

    def add_type(self, type_desc: str) -> int:
        """Add the type ``type_desc`` (a ``<TypeDesc>``) to the VI; its type id."""
        self.extra_types.append(type_desc)
        return len(TYPES) + len(self.extra_types) - 1

    def next_uid(self, count: int = 1) -> int:
        """The first of ``count`` fresh uids."""
        first = self._uid
        self._uid += count
        return first

    # -- front panel --------------------------------------------------------

    def pane(
        self,
        label: str,
        type_id: int,
        *,
        indicator: bool = False,
        rule: int | None = None,
    ) -> int:
        """A control or indicator; ``rule`` puts it on the connector pane with
        that wiring rule. Returns the uid of its terminal on the diagram."""
        uid = self.next_uid(3)
        slot = None
        if rule is not None:
            slot = sum(1 for p in self.panes if p.slot is not None)
        self.panes.append(Pane(uid, label, type_id, indicator, rule, slot))
        term = uid + 1
        self.fp_terms[uid] = term
        return term

    def control(self, label: str, type_id: int, rule: int | None = REQUIRED) -> int:
        return self.pane(label, type_id, rule=rule)

    def indicator(
        self, label: str, type_id: int, rule: int | None = RECOMMENDED
    ) -> int:
        return self.pane(label, type_id, indicator=True, rule=rule)

    def error_in(self, rule: int | None = RECOMMENDED) -> int:
        return self.control("error in", ERR, rule)

    def error_out(self, rule: int | None = RECOMMENDED) -> int:
        return self.indicator("error out", ERR, rule)

    def local_variable(self, pane_term: int) -> int:
        """A local variable of the control or indicator whose terminal is
        ``pane_term``; returns its terminal (a write when wired as a sink, a
        read when wired as a source)."""
        ddo_position = next(
            i for i, p in enumerate(self.panes) if self.fp_terms[p.uid] == pane_term
        )
        uid = self.next_uid(3)
        index = f"<paramIdx>{ddo_position}</paramIdx>" if ddo_position else ""
        self.scope.nodes.append(
            f'<SL__arrayElement class="gRef" uid="{uid}"><termList elements="1">'
            f'<SL__arrayElement class="term" uid="{uid + 1}">'
            f'<dco class="gRefDCO" uid="{uid + 2}">{index}</dco>'
            "</SL__arrayElement></termList></SL__arrayElement>"
        )
        return uid + 1

    # -- nodes --------------------------------------------------------------

    def constant(self, type_id: int, hex_value: str) -> int:
        """A diagram constant holding the saved value ``hex_value``."""
        uid = self.next_uid(2)
        self.scope.terms.append(
            f'<SL__arrayElement class="term" uid="{uid}">'
            f'<dco class="bDConstDCO" uid="{uid + 1}"><typeDesc>TypeID({type_id})'
            f"</typeDesc><ConstValue>{hex_value}</ConstValue></dco>"
            "</SL__arrayElement>"
        )
        return uid

    def int_constant(self, value: int) -> int:
        return self.constant(I32, f"{value & 0xFFFFFFFF:08X}")

    def bool_constant(self, value: bool) -> int:
        return self.constant(BOOL, "01" if value else "00")

    def error_constant(self, kind: str) -> int:
        return self.constant(ERR, ERROR_CONSTANT_HEX[kind])

    def terminals(
        self,
        dco_class: str,
        slots: list[tuple[str, int, int, bool]],
        index_tag: str,
    ) -> tuple[str, dict[str, int]]:
        """The ``termList`` body for ``(name, index, type id, is output)`` slots
        and the terminal uid of each by name."""
        xml = ""
        uids: dict[str, int] = {}
        for name, index, type_id, output in slots:
            uid = self.next_uid(2)
            flags = "<objFlags>1</objFlags>" if output else ""
            xml += (
                f'<SL__arrayElement class="term" uid="{uid}">'
                f'<dco class="{dco_class}" uid="{uid + 1}">{flags}'
                f"<typeDesc>TypeID({type_id})</typeDesc>"
                f"<{index_tag}>{index}</{index_tag}></dco></SL__arrayElement>"
            )
            uids[name] = uid
        return xml, uids

    def primitive(
        self, prim_res_id: int, slots: list[tuple[str, int, int, bool]]
    ) -> dict[str, int]:
        """A primitive with the given terminals; returns terminal uids by name."""
        terms, uids = self.terminals("parm", slots, "parmIndex")
        uid = self.next_uid()
        self.scope.nodes.append(
            f'<SL__arrayElement class="prim" uid="{uid}">'
            f'<termList elements="{len(slots)}">{terms}</termList>'
            f"<primResID>{prim_res_id}</primResID><primIndex>{uid}</primIndex>"
            "</SL__arrayElement>"
        )
        return uids

    def call(
        self,
        name: str,
        slots: list[tuple[str, int, int, bool]],
        *,
        vilib: str | None = None,
    ) -> dict[str, int]:
        """A subVI call. ``vilib`` is the recorded vi.lib path (``Utility/error.llb``)
        of a vi.lib VI; a project VI records its own file name."""
        terms, uids = self.terminals("iUseDCO", slots, "paramIdx")
        uid = self.next_uid()
        self.scope.nodes.append(
            f'<SL__arrayElement class="iUse" uid="{uid}">'
            f'<termList elements="{len(slots)}">{terms}</termList>'
            "</SL__arrayElement>"
        )
        path = ["<vilib>", *vilib.split("/"), name] if vilib else ["", name]
        strings = "".join(
            f"<String>{escape(p)}</String>" if p else "<String />" for p in path
        )
        self.links.append(
            f'<IUVI LinkSaveFlag="0"><LinkSaveQualName><String>{escape(name)}'
            "</String></LinkSaveQualName>"
            f'<LinkSavePathRef Ident="PTH0" TpVal="1">{strings}</LinkSavePathRef>'
            f"<LinkOffsetList><Offset>0x{uid:X}</Offset></LinkOffsetList></IUVI>"
        )
        return uids

    def subvi(self, name: str, **terminals: tuple[int, int, bool]) -> dict[str, int]:
        """A project subVI called ``name``; each keyword is a terminal
        ``(pane index, type id, is output)``."""
        return self.call(
            name,
            [(label, i, t, out) for label, (i, t, out) in terminals.items()],
        )

    def step(self, name: str) -> dict[str, int]:
        """The subVI ``name(x) -> r`` whose error in and error out are wired:
        terminals ``x``, ``error in``, ``r``, ``error out``."""
        return self.call(
            name,
            [
                ("x", 0, I32, False),
                ("error in", 1, ERR, False),
                ("r", 2, I32, True),
                ("error out", 3, ERR, True),
            ],
        )

    def wire(self, source: int, *sinks: int) -> None:
        """One wire from ``source`` to each of ``sinks`` (a fork when several)."""
        for sink in sinks:
            uid = self.next_uid()
            self.wires.append(
                f'<SL__arrayElement class="signal" uid="{uid}">'
                f'<termList elements="2">{_ref(source)}{_ref(sink)}</termList>'
                "</SL__arrayElement>"
            )

    # -- structures ---------------------------------------------------------

    @contextmanager
    def inside(self, scope: Scope) -> Iterator[Scope]:
        """Put the nodes and constants added in the block into ``scope``."""
        previous, self.scope = self.scope, scope
        try:
            yield scope
        finally:
            self.scope = previous

    def _new_scope(self) -> Scope:
        uid = self.next_uid(2)
        return Scope(uid=uid, srn_uid=uid + 1)

    @staticmethod
    def _diagram_xml(scope: Scope) -> str:
        holder = (
            f'<SL__arrayElement class="sRN" uid="{scope.srn_uid}">'
            f'<termList elements="{len(scope.terms)}">{"".join(scope.terms)}'
            "</termList></SL__arrayElement>"
        )
        nodes = [*scope.nodes, holder]
        return (
            f'<SL__arrayElement class="diag" uid="{scope.uid}">'
            '<zPlaneList elements="0" />'
            f'<nodeList elements="{len(nodes)}">{"".join(nodes)}</nodeList>'
            '<signalList elements="0" /></SL__arrayElement>'
        )

    def tunnel_terminal(
        self,
        cls: str,
        type_id: int,
        scopes: list[Scope],
        *,
        output: bool = False,
        element_type: int | None = None,
    ) -> tuple[Tunnel, str]:
        """A tunnel; ``element_type`` makes it an auto-indexing tunnel whose inner
        side carries that type."""
        outer = self.next_uid(2)
        dco_uid = outer + 1
        indexing = ""
        if element_type is not None:
            indexing = (
                f'<innerLpTunDCO class="innerLpTun" uid="{self.next_uid()}">'
                f"<objFlags>{_INDEXING_FLAG}</objFlags>"
                f"<typeDesc>TypeID({element_type})</typeDesc></innerLpTunDCO>"
            )
        faces = []
        for scope in scopes:
            face = self.next_uid()
            faces.append(face)
            scope.terms.append(_term(face, f'<dco uid="{dco_uid}" />'))
        xml = _term(
            outer,
            _dco(
                cls,
                dco_uid,
                type_id=type_id,
                faces=[*faces, outer],
                flags=1 if output else None,
                extra=indexing,
            ),
        )
        return Tunnel(outer, faces), xml

    def case(
        self,
        selector_kind: str,
        selector_type: int,
        frames: list[FrameSpec],
        *,
        default_frame: int | None,
        tunnels: list[tuple[int, bool]] = (),  # type: ignore[assignment]
    ) -> Structure:
        """A Case structure. ``selector_kind`` is ``"error"`` or ``"boolean"``;
        ``frames`` are the stored selector values of each frame; ``tunnels`` are
        ``(type id, is output)``."""
        uid = self.next_uid()
        scopes = [self._new_scope() for _ in frames]
        selector, selector_xml = self.tunnel_terminal("caseSel", selector_type, scopes)
        borders = [selector_xml]
        made = []
        for type_id, output in tunnels:
            tun, xml = self.tunnel_terminal("csTun", type_id, scopes, output=output)
            made.append(tun)
            borders.append(xml)
        values = _selector_xml(selector_kind, frames, True)  # type: ignore[arg-type]
        if default_frame is None:
            default = "<SelectDefaultCase>FF</SelectDefaultCase>"
        elif default_frame == 0:
            default = ""
        else:
            default = f"<SelectDefaultCase>{default_frame:02X}</SelectDefaultCase>"

        def xml() -> str:
            diagrams = "".join(self._diagram_xml(sc) for sc in scopes)
            return (
                f'<SL__arrayElement class="caseStruct" uid="{uid}">'
                f'<termList elements="{len(borders)}">{"".join(borders)}</termList>'
                f'<diagramList elements="{len(scopes)}">{diagrams}</diagramList>'
                f"{values}{default}</SL__arrayElement>"
            )

        structure = Structure(selector.outer, selector.faces, scopes, made, xml=xml)
        self.structures.append(structure)
        return structure

    def loop(
        self,
        kind: str,
        *,
        tunnels: list[tuple[int, bool]] = (),  # type: ignore[assignment]
        shift_registers: list[int] = (),  # type: ignore[assignment]
        indexing_inputs: list[tuple[int, int]] = (),  # type: ignore[assignment]
    ) -> Structure:
        """A ``forLoop`` (count terminal ``selector``) or ``whileLoop`` (stop
        terminal ``faces[0]``, continuing while False). ``tunnels`` are
        ``(type id, is output)``; ``shift_registers`` are the registers' type
        ids; ``indexing_inputs`` are auto-indexing input tunnels, ``(array type
        id, element type id)``, after ``tunnels`` in ``Structure.tunnels``."""
        uid = self.next_uid()
        scope = self._new_scope()
        borders: list[str] = []
        extra = ""
        count = 0
        faces: list[int] = []
        if kind == "forLoop":
            tun, xml = self.tunnel_terminal("lMax", I32, [scope])
            count, faces = tun.outer, tun.faces
            borders.append(xml)
        else:
            lst = self.next_uid(2)
            stop = lst + 1
            scope.terms.append(_term(stop, f'<dco uid="{lst}" />'))
            extra = (
                f'<loopTestDCO class="lTst" uid="{lst}"><objFlags>65536</objFlags>'
                f'<termList elements="1"><SL__arrayElement uid="{stop}" /></termList>'
                "</loopTestDCO>"
            )
            faces = [stop]
        made = []
        for type_id, output in tunnels:
            tun, xml = self.tunnel_terminal("lpTun", type_id, [scope], output=output)
            made.append(tun)
            borders.append(xml)
        for array_type, element_type in indexing_inputs:
            tun, xml = self.tunnel_terminal(
                "lpTun", array_type, [scope], element_type=element_type
            )
            made.append(tun)
            borders.append(xml)
        registers = []
        for type_id in shift_registers:
            left, left_xml = self.tunnel_terminal("lSR", type_id, [scope])
            right, right_xml = self.tunnel_terminal(
                "rSR", type_id, [scope], output=True
            )
            borders += [left_xml, right_xml]
            registers.append(
                ShiftRegister(left.outer, left.faces[0], right.faces[0], right.outer)
            )

        def xml() -> str:
            return (
                f'<SL__arrayElement class="{kind}" uid="{uid}">'
                f'<termList elements="{len(borders)}">{"".join(borders)}</termList>'
                f'{extra}<diagramList elements="1">{self._diagram_xml(scope)}'
                "</diagramList></SL__arrayElement>"
            )

        structure = Structure(count, faces, [scope], made, registers, xml=xml)
        self.structures.append(structure)
        return structure

    # -- serialisation ------------------------------------------------------

    def _fp_xml(self) -> str:
        zplane = ""
        for p in self.panes:
            zplane += (
                f'<SL__arrayElement class="fPDCO" uid="{p.uid}">'
                f"<objFlags>{1 if p.indicator else 0}</objFlags>"
                f"<typeDesc>TypeID({p.type_id})</typeDesc>"
                f'<ddo class="stdNum" uid="{p.uid}1">'
                f"<typeDesc>TypeID({p.type_id})</typeDesc><partsList>"
                f'<SL__arrayElement class="label" uid="{p.uid}2">'
                f'<textRec class="textHair"><text>"{p.label}"</text></textRec>'
                "</SL__arrayElement></partsList></ddo></SL__arrayElement>"
            )
        order = "".join(_ref(p.uid) for p in self.panes)
        slots = sorted(
            (p for p in self.panes if p.slot is not None), key=lambda p: p.slot or 0
        )
        cons = "".join(
            f'<SL__arrayElement class="ConpaneConnection" index="{p.slot}">'
            f'<ConnectionDCO uid="{p.uid}" /></SL__arrayElement>'
            for p in slots
        )
        conpane = (
            f'<conPane class="conPane" uid="9"><conId>4800</conId><cons>{cons}</cons>'
            "</conPane>"
            if slots
            else ""
        )
        return (
            '<SL__rootObject class="oHExt" uid="1"><root class="supC" uid="2">'
            f"{conpane}<ddoList>{order}</ddoList>"
            f'<paneHierarchy class="pane" uid="3"><zPlaneList>{zplane}'
            "</zPlaneList></paneHierarchy></root></SL__rootObject>"
        )

    def _bd_xml(self) -> str:
        fp = "".join(
            f'<SL__arrayElement class="fPTerm" uid="{self.fp_terms[p.uid]}">'
            f'<dco uid="{p.uid}" /></SL__arrayElement>'
            for p in self.panes
        )
        held = [*self.root.terms, fp]
        holder = (
            '<SL__arrayElement class="sRN" uid="99">'
            f'<termList elements="{len(self.root.terms) + len(self.panes)}">'
            f"{''.join(held)}</termList></SL__arrayElement>"
        )
        nodes = [*self.root.nodes, holder]
        structures = [st.xml() for st in self.structures]
        return (
            "<?xml version='1.0' encoding='utf-8'?>\n"
            '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
            f'<zPlaneList elements="{len(structures)}">'
            f"{''.join(structures)}</zPlaneList>"
            f'<nodeList elements="{len(nodes)}">{"".join(nodes)}</nodeList>'
            f'<signalList elements="{len(self.wires)}">{"".join(self.wires)}'
            "</signalList></root></SL__rootObject>\n"
        )

    def _main_xml(self) -> str:
        types = [*TYPES, *self.extra_types]
        slots = sorted(
            (p for p in self.panes if p.slot is not None), key=lambda p: p.slot or 0
        )
        children = "".join(
            f'<TypeDesc TypeID="{p.type_id}" Flags="0x{_slot_flags(p):04X}" />'
            for p in slots
        )
        function = f'<TypeDesc Type="Function" Flags="0x0000">{children}</TypeDesc>'
        fn_id = len(types)
        names = [*_TYPE_NAMES] + ["Other"] * len(self.extra_types) + ["Function"]
        all_types = [*types, function]
        top = "\n".join(
            f'<TypeDesc Index="{i}" FlatTypeID="{i}" />'
            f"<!-- Heap TypeID {i} = Consolidated TypeID {i}: {names[i]} -->"
            for i in range(len(all_types))
        )
        return (
            "<?xml version='1.0' encoding='utf-8'?>\n<RSRC>\n"
            f"<VCTP><TopLevel>{top}</TopLevel><Section>{''.join(all_types)}"
            "</Section></VCTP>\n"
            f'<CONP><Section><TypeDesc TypeID="{fn_id}" /></Section></CONP>\n'
            f'<LIvi><Section><LVIN Unk1="{self.name}" /></Section></LIvi>\n'
            f'<LIbd><Section Index="0"><BDHP Unk1="{self.name}">{"".join(self.links)}'
            "</BDHP></Section></LIbd>\n"
            "</RSRC>\n"
        )

    def files(self) -> tuple[str, str, str]:
        """The block diagram, front panel and main XML of the VI."""
        return self._bd_xml(), self._fp_xml(), self._main_xml()


@dataclass
class ErrorViews:
    """The text every view prints for a VI."""

    graph: InMemoryVIGraph
    vi: str
    lvnet: str
    terse: str
    describe: str
    json: dict

    def describe_body(self) -> str:
        """Describe without its first line, which names the XML file."""
        return self.describe.split("\n", 1)[1]

    @property
    def diagram(self) -> str:
        """The ``block-diagram :`` section of the verbose lvnet."""
        return self.lvnet.split("  block-diagram :\n", 1)[1].split("\n  types :", 1)[0]


@dataclass
class Package:
    """A generated package on disk, with the module of the VI under test."""

    module: ModuleType
    errors: ModuleType
    source: str
    root: Path

    @property
    def function(self) -> Callable[..., Any]:
        return getattr(self.module, self.module.__name__.rsplit(".", 1)[1])

    def error(self, code: int = 7, *, status: bool = True, source: str = "stub") -> Any:
        """A ``LabVIEWError`` as the package defines it."""
        return self.errors.LabVIEWError(code=code, source=source, status=status)

    def stub(self, module: str) -> ModuleType:
        """A stub subVI module of the package, imported."""
        return importlib.import_module(f"{self.module.__package__}.{module}")


class Project:
    """A folder of placeholder ``.vi`` files and the XML a fake extractor returns
    for them, so a caller loads, and generates, with the subVIs it calls."""

    def __init__(self, root: Path) -> None:
        self.base = root
        self.root = root / "src"
        self.root.mkdir()
        self._xml: dict[Path, tuple[Path, Path | None, Path]] = {}
        self._loaded: list[str] = []

    def add(self, vi: Vi, rel: str | None = None) -> Path:
        rel = rel or vi.name
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        slot = self.base / "_xml" / rel.replace("/", "__").replace(".", "_")
        slot.mkdir(parents=True)
        bd, fp, main = vi.files()
        (slot / "vi_BDHb.xml").write_text(bd)
        (slot / "vi_FPHb.xml").write_text(fp)
        (slot / "vi.xml").write_text(main)
        self._xml[path.resolve()] = (
            slot / "vi_BDHb.xml",
            slot / "vi_FPHb.xml",
            slot / "vi.xml",
        )
        return path

    def extract(self, path: Path) -> tuple[Path, Path | None, Path]:
        return self._xml[Path(path).resolve()]

    def views(self, name: str) -> ErrorViews:
        graph = InMemoryVIGraph()
        key = graph.load_vi(self.root / name, LoadMode.MINIMAL)
        assert key is not None
        module = build_netlist_from_graph(graph, key)
        return ErrorViews(
            graph,
            key,
            render_lvnet(module, display_name=name, verbose=True),
            render_lvnet(module, display_name=name),
            describe_vi(graph, key),
            netlist_to_dict(module, verbose=True),
        )

    def generate(self, name: str, stubs: dict[str, str] | None = None) -> Package:
        """Generate the VI ``name`` and the VIs it calls, replace the module of each
        called VI with its stub (``{module name: source}``) and import the VI's
        module."""
        out = self.base / "gen"
        result = generate_python(self.root / name, out)
        package_dir = Path(result["output_dir"])
        errors = package_dir / "errors.py"
        if not errors.exists():
            errors.write_text(ERRORS_MODULE_SOURCE)
        for stem, body in (stubs or {}).items():
            (package_dir / f"{stem}.py").write_text(textwrap.dedent(body))
        stem = _module_stem(name)
        source = (package_dir / f"{stem}.py").read_text()
        sys.path.insert(0, str(package_dir.parent))
        importlib.invalidate_caches()
        pkg = package_dir.name
        self._loaded.append(pkg)
        return Package(
            importlib.import_module(f"{pkg}.{stem}"),
            importlib.import_module(f"{pkg}.errors"),
            source,
            package_dir,
        )

    def close(self) -> None:
        while str(self.base / "gen") in sys.path:
            sys.path.remove(str(self.base / "gen"))
        for key in [k for k in sys.modules if k.split(".")[0] in self._loaded]:
            del sys.modules[key]


def _module_stem(name: str) -> str:
    return re.sub(r"[^\w]+", "_", name.removesuffix(".vi")).strip("_").lower()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Project]:
    built = Project(tmp_path)
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    yield built
    built.close()


# Stub subVIs. A subVI's function takes ``error_in`` and returns a result object
# whose ``error_out`` is its error (``None`` for no error).

OK_STEP = """
    from types import SimpleNamespace

    def {fn}(x=0, error_in=None):
        return SimpleNamespace(r=x + {add}, error_out=error_in)
"""

FAILING_STEP = """
    from types import SimpleNamespace
    from .errors import LabVIEWError

    def {fn}(x=0, error_in=None):
        return SimpleNamespace(
            r=0, error_out=LabVIEWError(code={code}, source="{fn}", status=True)
        )
"""

# A subVI that skips on an incoming error and records that it ran.
SKIPPING_STEP = """
    from types import SimpleNamespace

    CALLS = []

    def {fn}(x=0, error_in=None):
        if error_in is not None:
            return SimpleNamespace(r=0, error_out=error_in)
        CALLS.append(x)
        return SimpleNamespace(r=x + {add}, error_out=None)
"""

# A subVI that runs whatever its error in (a cleanup) and records that it ran.
CLEANUP_STEP = """
    from types import SimpleNamespace

    CALLS = []

    def {fn}(x=0, error_in=None):
        CALLS.append(x)
        return SimpleNamespace(r=x + {add}, error_out=error_in)
"""


def ok_step(fn: str, add: int = 1) -> str:
    return OK_STEP.format(fn=fn, add=add)


def failing_step(fn: str, code: int = 7) -> str:
    return FAILING_STEP.format(fn=fn, code=code)


def skipping_step(fn: str, add: int = 1) -> str:
    return SKIPPING_STEP.format(fn=fn, add=add)


def cleanup_step(fn: str, add: int = 1) -> str:
    return CLEANUP_STEP.format(fn=fn, add=add)


def step_vi(name: str, *, error_rule: int | None = RECOMMENDED) -> Vi:
    """The VI ``name(x) -> r`` with error in and error out on its connector pane,
    slots 0 to 3: what ``Vi.step`` calls."""
    vi = Vi(name)
    vi.control("x", I32, REQUIRED)
    vi.error_in(error_rule)
    vi.indicator("r", I32)
    vi.error_out(error_rule)
    return vi


# =============================================================
# Nodes with their own XML
# =============================================================


def increment(vi: Vi) -> dict[str, int]:
    """Increment (a node with no error terminals): terminals ``in``, ``out``."""
    return vi.primitive(1057, [("out", 0, I32, True), ("in", 1, I32, False)])


def unbundle(vi: Vi, fields: list[tuple[str, int]]) -> dict[str, int]:
    """Unbundle By Name of an error cluster: ``fields`` are ``(name, field index)``
    (0 status, 1 code, 2 source); terminals ``cluster`` and each field by name."""
    field_types = {0: BOOL, 1: I32, 2: STR}
    uid = vi.next_uid()
    agg = vi.next_uid(2)
    terms = (
        f'<SL__arrayElement class="term" uid="{agg}"><dco class="nmxDCO" '
        f'uid="{agg + 1}"><typeDesc>TypeID({ERR})</typeDesc></dco>'
        "</SL__arrayElement>"
    )
    uids = {"cluster": agg}
    drawers = []
    for name, index in fields:
        t = vi.next_uid(2)
        uids[name] = t
        drawers.append(t + 1)
        order = f"<i>{index}</i>" if index else ""
        terms += (
            f'<SL__arrayElement class="term" uid="{t}"><dco class="nmxDCO" '
            f'uid="{t + 1}"><objFlags>1</objFlags>'
            f"<typeDesc>TypeID({field_types[index]})</typeDesc>{order}</dco>"
            "</SL__arrayElement>"
        )
    listed = "".join(f'<SL__arrayElement uid="{d}" />' for d in drawers)
    vi.scope.nodes.append(
        f'<SL__arrayElement class="nMux" uid="{uid}">'
        f'<termList elements="{len(fields) + 1}">{terms}</termList>'
        f'<dcoList elements="{len(fields)}">{listed}</dcoList>'
        f'<dcoAgg uid="{agg + 1}" /></SL__arrayElement>'
    )
    return uids


def bundle(vi: Vi, fields: list[tuple[str, int]]) -> dict[str, int]:
    """Bundle By Name into an error cluster: terminals ``cluster in``,
    ``cluster out`` and each field by name."""
    field_types = {0: BOOL, 1: I32, 2: STR}
    uid = vi.next_uid()
    agg_out = vi.next_uid(2)
    agg_in = vi.next_uid()
    terms = (
        f'<SL__arrayElement class="term" uid="{agg_out}"><dco class="nmxDCO" '
        f'uid="{agg_out + 1}"><objFlags>1</objFlags>'
        f"<typeDesc>TypeID({ERR})</typeDesc></dco></SL__arrayElement>"
        f'<SL__arrayElement class="term" uid="{agg_in}"><dco uid="{agg_out + 1}" />'
        "</SL__arrayElement>"
    )
    uids = {"cluster out": agg_out, "cluster in": agg_in}
    drawers = []
    for name, index in fields:
        t = vi.next_uid(2)
        uids[name] = t
        drawers.append(t + 1)
        order = f"<i>{index}</i>" if index else ""
        terms += (
            f'<SL__arrayElement class="term" uid="{t}"><dco class="nmxDCO" '
            f'uid="{t + 1}"><typeDesc>TypeID({field_types[index]})</typeDesc>'
            f"{order}</dco></SL__arrayElement>"
        )
    listed = "".join(f'<SL__arrayElement uid="{d}" />' for d in drawers)
    vi.scope.nodes.append(
        f'<SL__arrayElement class="nMux" uid="{uid}">'
        f'<termList elements="{len(fields) + 2}">{terms}</termList>'
        f'<dcoList elements="{len(fields)}">{listed}</dcoList>'
        f'<dcoAgg uid="{agg_out + 1}" /></SL__arrayElement>'
    )
    return uids


def merge_errors(vi: Vi, inputs: int = 2) -> dict[str, int]:
    """Merge Errors with ``inputs`` error inputs: terminals ``out`` and ``in 0``,
    ``in 1``, ..."""
    uid = vi.next_uid()
    out = vi.next_uid(2)
    uids = {"out": out}
    terms = (
        f'<SL__arrayElement class="term" uid="{out}"><dco class="mergeErrorsDCO" '
        f'uid="{out + 1}"><objFlags>1</objFlags><typeDesc>TypeID({ERR})</typeDesc>'
        "</dco></SL__arrayElement>"
    )
    drawers = []
    for i in range(inputs):
        t = vi.next_uid(2)
        uids[f"in {i}"] = t
        drawers.append(t + 1)
        terms += (
            f'<SL__arrayElement class="term" uid="{t}"><dco '
            f'class="mergeErrorsDCO" uid="{t + 1}"><typeDesc>TypeID({ERR})'
            "</typeDesc></dco></SL__arrayElement>"
        )
    listed = "".join(f'<SL__arrayElement uid="{d}" />' for d in drawers)
    vi.scope.nodes.append(
        f'<SL__arrayElement class="mergeErrors" uid="{uid}">'
        f'<termList elements="{inputs + 1}">{terms}</termList>'
        f'<dcoList elements="{inputs}">{listed}</dcoList>'
        f'<dcoAgg uid="{out + 1}" /></SL__arrayElement>'
    )
    return uids


def json_body(views: ErrorViews) -> str:
    """The JSON view's block diagram, compact: types left out (the type names stay),
    empty and false fields left out."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: strip(value)
                for key, value in node.items()
                if key != "lv_type" and value not in (None, [], False)
            }
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node

    return json.dumps(strip(views.json["body"]), sort_keys=True, separators=(",", ":"))
