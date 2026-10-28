"""Tiny block diagrams with structures, tunnels, constants and front-panel
terminals, written as the heap XML the parser reads.

Every builder takes explicit uids so a test can name the terminals it wires.
A diagram is a root ``diag`` whose structures sit in the ``zPlaneList``, whose
constants sit in a root ``sRN`` and whose wires sit in the ``signalList``.
The VI's types are a flat list (``TypeID(n)`` is position ``n``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict

# Type ids of ``TYPES``.
BOOL, I32, STR = 0, 1, 2
TYPES = [
    '<TypeDesc Type="Boolean" />',
    '<TypeDesc Type="NumInt32" />',
    '<TypeDesc Type="String" />',
]

_VALUE_HEX = {BOOL: {"True": "01", "False": "00"}, I32: None}


def main_xml(types: list[str] = TYPES) -> str:
    names = [t.split('Type="')[1].split('"')[0] for t in types]
    top = "\n".join(
        f'<TypeDesc Index="{i}" FlatTypeID="{i}" />'
        f"<!-- Heap TypeID {i} = Consolidated TypeID {i}: {names[i]} -->"
        for i in range(len(types))
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n<RSRC>\n"
        f"<VCTP><TopLevel>{top}</TopLevel><Section>{''.join(types)}</Section></VCTP>\n"
        "</RSRC>\n"
    )


def _ref(uid: int) -> str:
    return f'<SL__arrayElement uid="{uid}" />'


def dco(
    cls: str | None,
    uid: int,
    *,
    type_id: int | None = None,
    faces: list[int] | None = None,
    flags: int | None = None,
    extra: str = "",
) -> str:
    """A ``dco``; ``faces`` is the tunnel termList (inner faces, then the outer)."""
    attr = f' class="{cls}"' if cls else ""
    body = ""
    if flags is not None:
        body += f"<objFlags>{flags}</objFlags>"
    if faces is not None:
        listed = "".join(map(_ref, faces))
        body += f'<termList elements="{len(faces)}">{listed}</termList>'
    if type_id is not None:
        body += f"<typeDesc>TypeID({type_id})</typeDesc>"
    return f'<dco{attr} uid="{uid}">{body}{extra}</dco>'


def term(uid: int, dco_xml: str) -> str:
    return f'<SL__arrayElement class="term" uid="{uid}">{dco_xml}</SL__arrayElement>'


def constant(term_uid: int, type_id: int, value: str | int) -> str:
    """A diagram constant (its terminal is ``term_uid``); put it in an ``srn``."""
    if type_id == BOOL:
        hexed = "01" if value in (True, "True") else "00"
    elif type_id == I32:
        hexed = f"{int(value) & 0xFFFFFFFF:08X}"
    else:
        text = str(value).encode("latin-1")
        hexed = f"{len(text):08X}" + text.hex().upper()
    return term(
        term_uid,
        f'<dco class="bDConstDCO" uid="{term_uid + 1}"><typeDesc>TypeID({type_id})'
        f"</typeDesc><ConstValue>{hexed}</ConstValue></dco>",
    )


def increment(
    uid: int, type_id: int = I32, *, out_type: int | None = None
) -> str:
    """An Increment node, a reader of one value. Terminals: ``uid + 1`` the result
    out (typed ``out_type``, default ``type_id``), ``uid + 3`` the value in (typed
    ``type_id``). Its handle is ``Increment_<uid>``."""
    return (
        f'<SL__arrayElement class="prim" uid="{uid}"><termList elements="2">'
        + term(
            uid + 1,
            dco(
                "parm",
                uid + 2,
                type_id=type_id if out_type is None else out_type,
                flags=1,
            ),
        )
        + term(
            uid + 3,
            dco("parm", uid + 4, type_id=type_id, extra="<parmIndex>1</parmIndex>"),
        )
        + f"</termList><primResID>1057</primResID><primIndex>{uid}</primIndex>"
        "</SL__arrayElement>"
    )


def srn(uid: int, terms: list[str]) -> str:
    """The node that holds a diagram's constants and a structure's inner faces."""
    return (
        f'<SL__arrayElement class="sRN" uid="{uid}">'
        f'<termList elements="{len(terms)}">{"".join(terms)}</termList>'
        "</SL__arrayElement>"
    )


def face(uid: int, tunnel_dco: int) -> str:
    """The inner face of a tunnel as its frame's ``sRN`` holds it: a terminal
    that refers back to the tunnel's ``dco``."""
    return term(uid, f'<dco uid="{tunnel_dco}" />')


def diag(
    uid: int, nodes: list[str] | tuple[str, ...] = (), structures: list[str] = ()
) -> str:
    return (
        f'<SL__arrayElement class="diag" uid="{uid}">'
        f'<zPlaneList elements="{len(structures)}">{"".join(structures)}</zPlaneList>'
        f'<nodeList elements="{len(nodes)}">{"".join(nodes)}</nodeList>'
        '<signalList elements="0" /></SL__arrayElement>'
    )


def wire(uid: int, source: int, dest: int) -> str:
    return (
        f'<SL__arrayElement class="signal" uid="{uid}"><termList elements="2">'
        f"{_ref(source)}{_ref(dest)}</termList></SL__arrayElement>"
    )


def root_diagram(
    structures: list[str], nodes: list[str], wires: list[str]
) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        f'<zPlaneList elements="{len(structures)}">{"".join(structures)}</zPlaneList>'
        f'<nodeList elements="{len(nodes)}">{"".join(nodes)}</nodeList>'
        f'<signalList elements="{len(wires)}">{"".join(wires)}</signalList>'
        "</root></SL__rootObject>\n"
    )


@dataclass
class Views:
    """The text every view prints for one diagram."""

    graph: InMemoryVIGraph
    vi: str
    lvnet: str
    terse: str
    describe: str
    json: dict

    @property
    def diagram(self) -> str:
        """The ``block-diagram :`` section of the verbose lvnet, without the
        header line."""
        return self.lvnet.split("  block-diagram :\n", 1)[1].split("\n  types :", 1)[0]


def load(tmp_path: Path, xml: str, types: list[str] = TYPES) -> Views:
    (tmp_path / "T.xml").write_text(main_xml(types))
    bd = tmp_path / "T_BDHb.xml"
    bd.write_text(xml)
    graph = InMemoryVIGraph()
    graph.load_vi(bd)
    vi = graph.list_vis()[0]
    module = build_netlist_from_graph(graph, vi)
    return Views(
        graph,
        vi,
        render_lvnet(module, display_name="T.vi", verbose=True),
        render_lvnet(module, display_name="T.vi"),
        describe_vi(graph, vi),
        netlist_to_dict(module, verbose=True),
    )


# ---------------------------------------------------------------------------
# Structures. Each builder takes the uids it needs and returns the structure's XML;
# a tunnel is ``(outer terminal uid, dco uid)`` and its inner faces come from
# ``faces`` (one terminal uid per frame).
# ---------------------------------------------------------------------------


def tunnel(
    cls: str, outer: int, dco_uid: int, faces: list[int], *, output: bool = False
) -> str:
    """A border terminal with its tunnel ``dco``: ``faces`` are the inner faces
    (one per frame, or the single inner face of a loop), then the outer terminal.
    An ``output`` tunnel is marked as one for the case no wire says so."""
    return term(
        outer,
        dco(
            cls,
            dco_uid,
            type_id=I32,
            faces=[*faces, outer],
            flags=1 if output else None,
        ),
    )


def frame_diagram(uid: int, srn_uid: int, terms: list[str]) -> str:
    """A frame's diagram: one ``sRN`` holds the inner faces and constants."""
    return diag(uid, nodes=[srn(srn_uid, terms)])


def loop(
    kind: str, uid: int, border: list[str], inner: str, extra: str = ""
) -> str:
    """A ``forLoop`` / ``whileLoop`` with its border terminals, one body diagram and
    ``extra`` direct children (a ``loopTestDCO``)."""
    return (
        f'<SL__arrayElement class="{kind}" uid="{uid}">'
        f'<termList elements="{len(border)}">{"".join(border)}</termList>'
        f'{extra}<diagramList elements="1">{inner}</diagramList></SL__arrayElement>'
    )


def flat_sequence(uid: int, frames: list[tuple[int, list[str], str]]) -> str:
    """A Flat Sequence; each frame is ``(uid, its edge tunnels, its diagram)``."""
    xml = "".join(
        f'<SL__arrayElement class="sequenceFrame" uid="{fid}">'
        f'<termList elements="{len(edge)}">{"".join(edge)}</termList>'
        f'<diagramList elements="1">{body}</diagramList></SL__arrayElement>'
        for fid, edge, body in frames
    )
    return (
        f'<SL__arrayElement class="flatSequence" uid="{uid}">'
        f'<sequenceList elements="{len(frames)}">{xml}</sequenceList>'
        "</SL__arrayElement>"
    )


def stacked_sequence(uid: int, border: list[str], frames: list[str]) -> str:
    return (
        f'<SL__arrayElement class="seq" uid="{uid}">'
        f'<termList elements="{len(border)}">{"".join(border)}</termList>'
        f'<diagramList elements="{len(frames)}">{"".join(frames)}</diagramList>'
        "</SL__arrayElement>"
    )


def disable_structure(
    uid: int,
    border: list[str],
    frames: list[str],
    *,
    conditions: list[list[str]] | None = None,
    type_specialization: bool = False,
) -> str:
    """A Diagram Disable Structure (a ``commentNode`` with subdiagrams); with
    ``conditions`` (the symbol and value of each frame, empty for the Default
    frame) a Conditional Disable Structure; with ``type_specialization`` a Type
    Specialization Structure."""
    selectors = ""
    if conditions is not None:
        infos = "".join(
            '<SL__arrayElement class="SelectorInfoElement"><activeDiag><Tag0000>'
            + "".join(
                f"<SL__arrayElement>{token.encode().hex().upper()}</SL__arrayElement>"
                for token in tokens
            )
            + "</Tag0000></activeDiag></SL__arrayElement>"
            for tokens in conditions
        )
        selectors = f"<commentSelInfoArray>{infos}</commentSelInfoArray>"
    marker = "<Tag0273>00</Tag0273>" if type_specialization else ""
    return (
        f'<SL__arrayElement class="commentNode" uid="{uid}">'
        f'<termList elements="{len(border)}">{"".join(border)}</termList>'
        f'<diagramList elements="{len(frames)}">{"".join(frames)}</diagramList>'
        f"{selectors}{marker}</SL__arrayElement>"
    )


def event_structure(
    uid: int,
    border: list[str],
    frames: list[str],
    types: list[int],
    sources: list[int] | None = None,
) -> str:
    """An Event Structure; ``types`` is each frame's event type code and
    ``sources`` the ``ddo`` uid of the control each frame's event is on (0, or
    omitted, for none)."""
    specs = "".join(
        '<SL__arrayElement class="EventSpec">'
        f"<diagramIdx>{i}</diagramIdx>"
        f"<ddoUID>{(sources or [0] * len(types))[i]}</ddoUID><type>{code}</type>"
        "</SL__arrayElement>"
        for i, code in enumerate(types)
    )
    return (
        f'<SL__arrayElement class="eventStruct" uid="{uid}">'
        f'<termList elements="{len(border)}">{"".join(border)}</termList>'
        f'<diagramList elements="{len(frames)}">{"".join(frames)}</diagramList>'
        f'<EventNodeEvents elements="{len(types)}">{specs}</EventNodeEvents>'
        "</SL__arrayElement>"
    )


EVENT_TIMEOUT, EVENT_VALUE_CHANGE = 1073741825, 1073741826


def front_panel_xml(
    controls: dict[int, str],
    indicators: dict[int, str],
    pane: dict[int, int] | None = None,
) -> str:
    """The front-panel heap for I32 controls and indicators (``{fPDCO uid: label}``).
    ``pane`` maps connector-pane slot index to the fPDCO uid wired to it; a control
    in no slot is off the pane. Without ``pane`` the heap has no connector pane."""

    def fpdco(uid: int, label: str, indicator: bool) -> str:
        return (
            f'<SL__arrayElement class="fPDCO" uid="{uid}">'
            f"<objFlags>{1 if indicator else 0}</objFlags>"
            f"<typeDesc>TypeID({I32})</typeDesc>"
            f'<ddo class="stdNum" uid="{uid}1"><typeDesc>TypeID({I32})</typeDesc>'
            "<partsList>"
            f'<SL__arrayElement class="label" uid="{uid}2"><textRec class="textHair">'
            f'<text>"{label}"</text></textRec></SL__arrayElement>'
            "</partsList></ddo></SL__arrayElement>"
        )

    both = [
        *(fpdco(u, n, False) for u, n in controls.items()),
        *(fpdco(u, n, True) for u, n in indicators.items()),
    ]
    order = "".join(f'<SL__arrayElement uid="{u}" />' for u in (*controls, *indicators))
    cons = "".join(
        f'<SL__arrayElement class="ConpaneConnection" index="{slot}">'
        f'<ConnectionDCO uid="{fpdco}" /></SL__arrayElement>'
        for slot, fpdco in (pane or {}).items()
    )
    conpane = (
        f'<conPane class="conPane" uid="9"><conId>4800</conId>'
        f"<cons>{cons}</cons></conPane>"
        if pane is not None
        else ""
    )
    return (
        '<SL__rootObject class="oHExt" uid="1"><root class="supC" uid="2">'
        f"{conpane}<ddoList>{order}</ddoList>"
        f'<paneHierarchy class="pane" uid="3"><zPlaneList>{"".join(both)}'
        "</zPlaneList></paneHierarchy></root></SL__rootObject>"
    )


def fp_terminal(uid: int, fpdco_uid: int) -> str:
    """A front-panel terminal on the diagram (put it in an ``srn``)."""
    return (
        f'<SL__arrayElement class="fPTerm" uid="{uid}">'
        f'<dco uid="{fpdco_uid}"/></SL__arrayElement>'
    )


def reference_constant(
    uid: int, label: str, type_id: int, ddo_uid: int | None = None
) -> str:
    """A Control Reference constant labelled ``label``; terminal ``uid + 1``. With
    ``ddo_uid`` it references the front-panel control whose ``ddo`` has that uid."""
    ddo = f'<ddo uid="{ddo_uid}" />' if ddo_uid is not None else ""
    return (
        f'<SL__arrayElement class="ctlRefConst" uid="{uid}">'
        f'<termList elements="1">'
        + term(
            uid + 1,
            dco("ctlRefDCO", uid + 2, type_id=type_id, flags=1),
        )
        + f"</termList>{ddo}"
        f'<label class="label" uid="{uid + 3}"><partID>16</partID>'
        f'<textRec class="textHair"><text>"{label}"</text></textRec></label>'
        "</SL__arrayElement>"
    )


REFERENCE_TYPES = [
    '<TypeDesc Type="Refnum" RefType="LVObjCtl" CtlFlags="0x0002" />',  # This VI
    '<TypeDesc Type="Refnum" RefType="LVObjCtl" CtlFlags="0x0001" />',  # Application
    '<TypeDesc Type="Refnum" RefType="LVObjCtl" CtlFlags="0x001D" />',  # a control
]
THIS_VI_TYPE, APPLICATION_TYPE, CONTROL_TYPE = 3, 4, 5


VOID_TYPES = [
    '<TypeDesc Type="Void" />',
    '<TypeDesc Type="Void" Format="corrupt-stub" />',
]
VOID, STUB = 3, 4
