"""A VI saved by LabVIEW 9.0 keeps the value of a constant, and the default of a
control, in its default data (``DFDS``): the object names its type with
``tdOffset``, the ``TM80`` section's ``IndexShift`` turns that into the
``DataFill`` ``TypeID``, and a type marked ``TMFBit11`` has no ``DataFill`` (its
value is the type's default).

Each case is a tiny VI written as heap XML (``structure_diagrams.py``) with its
own ``T.xml`` data space, run through the parser, graph, describe, lvnet (terse
and verbose), JSON and generate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from tests.structure_diagrams import (
    Views,
    main_xml,
    root_diagram,
    srn,
    term,
    wire,
)

BOOL, I32, STR, U32, DBL, ARRAY, REFNUM, EXT, CLUSTER, I8 = range(10)
PATH, VARIANT, CLASS_REFNUM, VISA_REFNUM = range(10, 14)
TYPES = [
    '<TypeDesc Type="Boolean" />',
    '<TypeDesc Type="NumInt32" />',
    '<TypeDesc Type="String" />',
    '<TypeDesc Type="NumUInt32" />',
    '<TypeDesc Type="NumFloat64" />',
    '<TypeDesc Type="Array" Format="inline">'
    '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" /><TypeDesc TypeID="1" />'
    "</TypeDesc>",
    '<TypeDesc Type="Refnum" RefType="LVObjCtl" CtlFlags="0x0002" />',
    '<TypeDesc Type="NumFloatExt" />',
    '<TypeDesc Type="Cluster"><TypeDesc TypeID="1" /><TypeDesc TypeID="2" />'
    "</TypeDesc>",
    '<TypeDesc Type="NumInt8" />',
    '<TypeDesc Type="Path" />',
    '<TypeDesc Type="LVVariant" />',
    '<TypeDesc Type="Refnum" RefType="UDClassInst"><Item Text="C.lvclass" />'
    "</TypeDesc>",
    '<TypeDesc Type="Refnum" RefType="VisaRef" />',
]

SHIFT = 2
_FILL_FLAG = 'TMFBit13="1"'
_DEFAULT_FLAG = 'TMFBit11="1" TMFBit13="1"'


def _dataspace(slots: list[tuple[str, str | None]]) -> str:
    """The ``TM80`` and ``DFDS`` sections for ``slots``: the n-th slot is the
    data-space type ``SHIFT + n``, a ``DataFill`` of the given XML or, when that
    is None, a type that holds its default."""
    clients = "".join(
        f"<Client {_FILL_FLAG if fill is not None else _DEFAULT_FLAG} />"
        for _, fill in slots
    )
    fills = "".join(
        f'<DataFill TypeID="{SHIFT + n}">{fill}</DataFill>'
        for n, (_, fill) in enumerate(slots)
        if fill is not None
    )
    return (
        f'<TM80><Section Index="0" IndexShift="{SHIFT}" Format="inline">{clients}'
        "</Section></TM80>"
        f'<DFDS><Section Index="0" Format="inline">{fills}</Section></DFDS>'
    )


def _stored_constant(term_uid: int, type_id: int, slot: int, extra: str = "") -> str:
    """A diagram constant with no value of its own, naming data-space slot
    ``slot`` with its ``tdOffset``."""
    return term(
        term_uid,
        f'<dco class="bDConstDCO" uid="{term_uid + 1}"><typeDesc>TypeID({type_id})'
        f"</typeDesc><tdOffset>{slot:X}</tdOffset>{extra}</dco>",
    )


def _data(raw: bytes) -> str:
    return "".join(f"&amp;#x{b:02X};" for b in raw)


def _indicator(
    uid: int,
    label: str,
    type_id: int,
    ddo_class: str,
    extra: str,
    *,
    indicator: bool = True,
) -> str:
    return (
        f'<SL__arrayElement class="fPDCO" uid="{uid}">'
        f"<objFlags>{1 if indicator else 0}</objFlags>"
        f"<typeDesc>TypeID({type_id})</typeDesc>"
        f'<ddo class="{ddo_class}" uid="{uid}1"><typeDesc>TypeID({type_id})'
        "</typeDesc><partsList>"
        f'<SL__arrayElement class="label" uid="{uid}2"><textRec class="textHair">'
        f'<text>"{label}"</text></textRec></SL__arrayElement>'
        f"</partsList></ddo>{extra}</SL__arrayElement>"
    )


def _front_panel(controls: str, pane: dict[int, int]) -> str:
    cons = "".join(
        f'<SL__arrayElement class="ConpaneConnection" index="{slot}">'
        f'<ConnectionDCO uid="{uid}" /></SL__arrayElement>'
        for slot, uid in pane.items()
    )
    order = "".join(f'<SL__arrayElement uid="{uid}" />' for uid in pane.values())
    return (
        '<SL__rootObject class="oHExt" uid="1"><root class="supC" uid="2">'
        f'<conPane class="conPane" uid="9"><conId>4800</conId><cons>{cons}</cons>'
        f"</conPane><ddoList>{order}</ddoList>"
        f'<paneHierarchy class="pane" uid="3"><zPlaneList>{controls}</zPlaneList>'
        "</paneHierarchy></root></SL__rootObject>"
    )


def _load(
    tmp_path: Path,
    bd_xml: str,
    slots: list[tuple[str, str | None]] | None,
    fp_xml: str | None = None,
) -> Views:
    """Load the VI; ``slots`` None writes a VI with no default data."""
    dataspace = "" if slots is None else _dataspace(slots)
    (tmp_path / "T.xml").write_text(
        main_xml(TYPES).replace("</RSRC>", dataspace + "</RSRC>")
    )
    bd = tmp_path / "T_BDHb.xml"
    bd.write_text(bd_xml)
    if fp_xml is not None:
        (tmp_path / "T_FPHb.xml").write_text(fp_xml)
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


def _constant_vi(
    tmp_path: Path,
    type_id: int,
    ddo_class: str,
    slots: list[tuple[str, str | None]] | None,
    slot: int,
    extra: str = "",
) -> Views:
    """A VI whose indicator ``result`` shows one constant of data-space slot
    ``slot``."""
    fp = _front_panel(_indicator(30, "result", type_id, ddo_class, ""), {0: 30})
    xml = root_diagram(
        [],
        [
            srn(
                800,
                [
                    _stored_constant(701, type_id, slot, extra),
                    '<SL__arrayElement class="fPTerm" uid="5030">'
                    '<dco uid="30"/></SL__arrayElement>',
                ],
            )
        ],
        [wire(901, 701, 5030)],
    )
    return _load(tmp_path, xml, slots, fp)


def _generate(views: Views) -> tuple[object, str, list[Gap]]:
    gaps: list[Gap] = []
    context = views.graph.get_vi_context(views.vi)
    source = build_module(context, views.vi, graph=views.graph, gaps=gaps)
    namespace: dict[str, object] = {}
    exec(compile(source, "T.py", "exec"), namespace)  # noqa: S102
    return namespace["t"], source, gaps


_STRING = "ps2000_set_sig_gen_built_in"

SLOTS: list[tuple[str, str | None]] = [
    ("U32", "<U32>6</U32>"),
    ("DBL", "<DBL>48000000</DBL>"),
    ("String", f"<String>{_STRING}</String>"),
    ("I32 default", None),
    ("Array default", None),
    ("Refnum default", None),
    ("EXT", "<EXT>1.5</EXT>"),
    ("Cluster default", None),
    ("I8 default", None),
    ("Path default", None),
    ("Variant default", None),
    ("Class default", None),
    ("VISA default", None),
]
U32_SLOT, DBL_SLOT, STR_SLOT, I32_SLOT, ARRAY_SLOT = 0, 1, 2, 3, 4
REFNUM_SLOT, EXT_SLOT, CLUSTER_SLOT, I8_SLOT = 5, 6, 7, 8
PATH_SLOT, VARIANT_SLOT, CLASS_SLOT, VISA_SLOT = 9, 10, 11, 12


@pytest.mark.parametrize(
    ("type_id", "ddo_class", "slot", "type_name", "described", "lvnet", "value"),
    [
        (U32, "stdNum", U32_SLOT, "U32", "6", "6", 6),
        (DBL, "stdNum", DBL_SLOT, "DBL", "48000000.0", "48000000.0", 48000000.0),
        (
            STR,
            "stdString",
            STR_SLOT,
            "String",
            repr(_STRING),
            f'"{_STRING!r}"',
            _STRING,
        ),
        (I32, "stdNum", I32_SLOT, "I32", "0", "0", 0),
        (I8, "stdNum", I8_SLOT, "I8", "0", "0", 0),
        (ARRAY, "indArr", ARRAY_SLOT, "[I32]", "[]", "[]", []),
    ],
)
def test_a_constant_with_no_value_takes_it_from_the_default_data(
    tmp_path: Path,
    type_id: int,
    ddo_class: str,
    slot: int,
    type_name: str,
    described: str,
    lvnet: str,
    value: object,
) -> None:
    views = _constant_vi(tmp_path, type_id, ddo_class, SLOTS, slot)
    assert f"  (unnamed): {type_name} = {described}" in views.describe.splitlines()
    assert f"    result = {lvnet}" in views.terse.splitlines()
    assert f"    result = {lvnet}" in views.diagram.splitlines()
    assert views.json["outputs"][0]["source"]["bare"] == described
    function, source, gaps = _generate(views)
    assert gaps == []
    assert function().result == value  # type: ignore[attr-defined]
    assert "lvkit" not in source


def test_a_type_that_holds_its_default_cluster_is_zeroed(tmp_path: Path) -> None:
    views = _constant_vi(tmp_path, CLUSTER, "stdClust", SLOTS, CLUSTER_SLOT)
    assert (
        "  (unnamed): cluster{field_0, field_1} = {'field_0': 0, 'field_1': ''}"
        in views.describe.splitlines()
    )
    function, _, gaps = _generate(views)
    assert gaps == []
    assert function().result == (0, "")  # type: ignore[attr-defined]


def test_a_session_reference_constant_with_no_value_is_not_a_refnum(
    tmp_path: Path,
) -> None:
    """A session reference is never saved, so the constant is Not a Refnum: the
    same as a constant that stores the handle 0."""
    (tmp_path / "default").mkdir()
    (tmp_path / "stored").mkdir()
    default = _constant_vi(
        tmp_path / "default", REFNUM, "stdRefNum", SLOTS, REFNUM_SLOT
    )
    stored = _constant_vi(
        tmp_path / "stored",
        REFNUM,
        "stdRefNum",
        SLOTS,
        REFNUM_SLOT,
        "<ConstValue>00000000</ConstValue>",
    )
    function, source, gaps = _generate(default)
    assert gaps == []
    assert function().result is None  # type: ignore[attr-defined]
    assert "lvkit" not in source
    _, stored_source, _ = _generate(stored)
    assert source == stored_source
    assert default.describe == stored.describe
    assert default.terse == stored.terse
    assert default.diagram == stored.diagram


def test_a_class_value_with_no_value_stays_a_gap_naming_the_class(
    tmp_path: Path,
) -> None:
    views = _constant_vi(tmp_path, CLASS_REFNUM, "stdRefNum", SLOTS, CLASS_SLOT)
    _, _, gaps = _generate(views)
    (gap,) = gaps
    assert "C.lvclass" in gap.reason


def test_a_tag_refnum_with_no_value_stays_a_gap(tmp_path: Path) -> None:
    views = _constant_vi(tmp_path, VISA_REFNUM, "stdRefNum", SLOTS, VISA_SLOT)
    _, _, gaps = _generate(views)
    (gap,) = gaps
    assert "VisaRef refnum" in gap.reason
    assert "default data (DFDS)" in gap.reason


@pytest.mark.parametrize(
    ("type_id", "ddo_class", "slot", "stored"),
    [
        (PATH, "stdPath", PATH_SLOT, "505448300000000400000000"),
        (VARIANT, "stdVar", VARIANT_SLOT, "0900800000000001000400000001000000000000"),
    ],
)
def test_an_empty_path_or_variant_is_the_same_as_the_stored_empty_value(
    tmp_path: Path, type_id: int, ddo_class: str, slot: int, stored: str
) -> None:
    (tmp_path / "default").mkdir()
    (tmp_path / "stored").mkdir()
    default = _constant_vi(tmp_path / "default", type_id, ddo_class, SLOTS, slot)
    explicit = _constant_vi(
        tmp_path / "stored",
        type_id,
        ddo_class,
        SLOTS,
        slot,
        f"<ConstValue>{stored}</ConstValue>",
    )
    function, source, gaps = _generate(default)
    assert gaps == []
    _, stored_source, stored_gaps = _generate(explicit)
    assert stored_gaps == []
    assert source == stored_source
    assert default.describe == explicit.describe
    assert default.terse == explicit.terse
    assert "lvkit" not in source


def test_a_tag_that_is_not_read_stays_a_gap_naming_the_tag(tmp_path: Path) -> None:
    views = _constant_vi(tmp_path, EXT, "stdNum", SLOTS, EXT_SLOT)
    _, _, gaps = _generate(views)
    (gap,) = gaps
    assert "the default-data tag EXT" in gap.reason
    assert "default data (DFDS)" in gap.reason


def test_an_object_with_no_slot_in_the_data_space_stays_a_gap(
    tmp_path: Path,
) -> None:
    views = _constant_vi(tmp_path, U32, "stdNum", SLOTS, len(SLOTS) + 3)
    _, _, gaps = _generate(views)
    (gap,) = gaps
    assert f"data-space type {len(SLOTS) + 3 + SHIFT}" in gap.reason


def test_a_constant_with_its_own_value_does_not_use_the_default_data(
    tmp_path: Path,
) -> None:
    for name, extra in (
        ("const", "<ConstValue>00000007</ConstValue>"),
        ("default", f'<DefaultData>"{_data(bytes([0, 0, 0, 7]))}"</DefaultData>'),
    ):
        (tmp_path / name).mkdir()
        views = _constant_vi(tmp_path / name, U32, "stdNum", SLOTS, U32_SLOT, extra)
        function, _, gaps = _generate(views)
        assert gaps == []
        assert function().result == 7, name  # type: ignore[operator]


def test_a_constant_of_a_vi_without_a_data_space_keeps_the_existing_gap(
    tmp_path: Path,
) -> None:
    views = _constant_vi(tmp_path, U32, "stdNum", None, 0)
    _, _, gaps = _generate(views)
    (gap,) = gaps
    assert gap.reason.endswith(
        "holds a value the VI stores in its default data (DFDS), which lvkit does "
        "not decode yet; code generation has no value to write for it."
    )
