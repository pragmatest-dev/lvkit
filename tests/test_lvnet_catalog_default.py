"""What an unwired input shows as its default (#169): the catalog's documented
value, its documented behaviour, or the bare `default`; a value that is not a value
of the input's type at the call is reported and shown bare.

Each case is a tiny diagram of one primitive with its input left unwired, written as
heap XML (``structure_diagrams.py``): the lvnet text, that it reads back to the same
graph, and the Python the default generates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.primitive_resolver import get_resolver
from lvkit.terminal_default import DefaultBehavior
from tests.structure_diagrams import (
    BOOL,
    I32,
    STR,
    Views,
    constant,
    fp_terminal,
    front_panel_xml,
    load,
    one_input_primitive,
    primitive,
    root_diagram,
    srn,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity

ONE_BUTTON_DIALOG = 1340  # input 1 `button name`, default "OK"
ARRAY_OF_STRINGS_TO_PATH = 1422  # input 1 `is_relative`, default FALSE
NUMBER_TO_DECIMAL_STRING = 1180  # inputs 1 `width` (no padding), 2 `number`
STRING_SUBSET = 1503  # inputs 1 `length`, 2 `offset`, 3 `string`
MULTIPLY = 1052  # input 2 `x`: NI states no default


def _views(tmp_path: Path, node: str) -> Views:
    return load(tmp_path, root_diagram([], [node], []))


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def test_a_documented_value_is_shown(tmp_path: Path) -> None:
    views = _views(
        tmp_path, one_input_primitive(10, ONE_BUTTON_DIALOG, STR, out_type=BOOL)
    )
    assert '      in   button_name : String default "OK"' in views.lvnet
    _round_trips(views)


def test_a_value_that_is_not_of_the_inputs_type_is_reported_and_shown_bare(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    node = one_input_primitive(10, ARRAY_OF_STRINGS_TO_PATH, I32, out_type=BOOL)
    with caplog.at_level(logging.WARNING):
        views = _views(tmp_path, node)
    assert "      in   1 : I32 default\n" in views.lvnet + "\n"
    assert "not a value of its type I32" in caplog.text
    _round_trips(views)


def test_a_documented_behaviour_is_shown_in_parentheses(tmp_path: Path) -> None:
    node = primitive(10, NUMBER_TO_DECIMAL_STRING, [(1, I32), (2, I32)], out_type=STR)
    views = _views(tmp_path, node)
    assert " default (no padding)\n" in views.lvnet + "\n"
    _round_trips(views)


def test_an_input_nothing_documents_is_the_bare_default(tmp_path: Path) -> None:
    views = _views(tmp_path, primitive(10, MULTIPLY, [(2, I32)], out_type=I32))
    assert "\n      in   x" in views.lvnet
    assert views.lvnet.rstrip().splitlines()[-2].endswith(" default")
    _round_trips(views)


def test_a_behaviour_the_function_writes_is_executed(tmp_path: Path) -> None:
    """Number To Decimal String with `width` unwired pads nothing."""
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({}, {30: "result"}, pane={0: 30})
    )
    node = primitive(10, NUMBER_TO_DECIMAL_STRING, [(1, I32), (2, I32)], out_type=STR)
    xml = root_diagram(
        [],
        [srn(800, [constant(701, I32, 7), fp_terminal(5030, 30)]), node],
        [wire(901, 701, 15), wire(902, 11, 5030)],
    )
    views = load(tmp_path, xml)
    context = views.graph.get_vi_context(views.vi)
    source = build_module(context, "T.vi", graph=views.graph)
    namespace: dict[str, object] = {}
    exec(compile(source, "T.py", "exec"), namespace)  # noqa: S102
    assert namespace["t"]().result == "7"  # type: ignore[operator]


def test_an_unwired_input_nothing_documents_is_a_gap_in_the_generated_code(
    tmp_path: Path,
) -> None:
    """Multiply's `x` stays unknown, a gap and not a guess."""
    views = _views(tmp_path, primitive(10, MULTIPLY, [(2, I32)], out_type=I32))
    gaps: list = []
    build_module(
        views.graph.get_vi_context(views.vi), "T.vi", graph=views.graph, gaps=gaps
    )
    assert [g.kind for g in gaps] == ["unknown_default"]
    assert "its default is not known" in gaps[0].reason


def test_get_variant_attribute_with_default_value_unwired_returns_none() -> None:
    """Unwired, `default value` is an empty variant: None in Python."""
    from tests.native_vi import generate, node, scalar

    variant = scalar("Variant")
    name = scalar("String")
    get_attribute = node(
        "ga",
        8205,
        [
            (1, "output", variant),
            (4, "input", None),
            (5, "input", variant),
            (6, "input", name),
            (7, "input", variant),
        ],
    )
    generated = generate(
        {"v": variant, "n": name},
        {"value": variant},
        [get_attribute],
        [("fp:v", "ga:7"), ("fp:n", "ga:6"), ("ga:1", "fp:value")],
    )
    holder = type("Holder", (), {"present": 3})()
    assert generated.function(holder, "present").value == 3  # type: ignore[operator]
    assert generated.function(holder, "absent").value is None  # type: ignore[operator]


def test_a_project_vi_call_shows_the_default_its_uses_entry_shows(
    tmp_path: Path,
) -> None:
    """The called VI's connector pane is the one source: an unwired input of the
    call shows the value of the callee's control, the same value its `uses :`
    entry shows; a callee control with no saved default shows the bare default."""
    from lvkit.graph.models import VINode
    from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
    from tests import vi_reference_diagrams as d

    number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    loaded = d.load(tmp_path, [], [])
    graph, caller, callee = loaded.graph, loaded.caller, loaded.callee
    callee_node = graph._graph.nodes[callee]["node"]  # noqa: SLF001
    callee_node.terminals = [
        FPTerminal(
            id="x",
            index=0,
            direction="input",
            name="x",
            lv_type=number,
            is_public=True,
            default_value="7",
        ),
        FPTerminal(
            id="z",
            index=1,
            direction="input",
            name="z",
            lv_type=number,
            is_public=True,
        ),
    ]
    call = VINode(
        id=f"{caller}::50",
        vi_path=caller,
        name="Callee.vi",
        node_type="iUse",
        callee_key=callee,
        terminals=[
            Terminal(id=f"{caller}::51", index=0, direction="input", lv_type=number),
            Terminal(id=f"{caller}::52", index=1, direction="input", lv_type=number),
        ],
    )
    graph._graph.add_node(call.id, node=call)  # noqa: SLF001
    graph._vi_nodes[caller].add(call.id)  # noqa: SLF001
    for terminal in call.terminals:
        graph._term_to_node[terminal.id] = call.id  # noqa: SLF001
    text = render_lvnet(build_netlist_from_graph(graph, caller), verbose=True)
    assert "      in   x : I32 default 7\n" in text + "\n"
    assert "      in   z : I32 default\n" in text + "\n"
    assert "    in   x : I32 default 7" in text


def test_simple_error_handler_inputs_sit_at_the_slots_its_calls_wire() -> None:
    """Calls wire an I32 at 3, a String at 4, a U16 at 8 and an error cluster at 9:
    error code, error source, type of dialog, error in."""
    from lvkit.vilib_resolver import get_resolver

    entry = get_resolver().resolve_by_name("Simple Error Handler.vi")
    assert entry is not None
    inputs = {t.index: t.name for t in entry.terminals if t.direction == "in"}
    assert inputs == {
        3: "error code (no error:0)",
        4: 'error source (" ")',
        8: "type of dialog (OK msg:1)",
        9: "error in (no error)",
    }
    dialog = next(t for t in entry.terminals if t.index == 8)
    assert (dialog.type, dialog.default) == ("NumUInt16", 1)


# (primitive ID, input terminal index, documented default, documented behaviour)
NI_DOCUMENTED_DEFAULTS = [
    pytest.param(1505, 1, True, None, id="Number To Exponential String decimal"),
    pytest.param(1505, 2, 6, None, id="Number To Exponential String precision"),
    pytest.param(1183, 1, True, None, id="Number To Engineering String decimal"),
    pytest.param(1183, 2, 6, None, id="Number To Engineering String precision"),
    pytest.param(2044, 3, None, DefaultBehavior.REST, id="Array Subset (2044) length"),
    pytest.param(
        3912, 2, None, DefaultBehavior.ONE_ELEMENT, id="Delete From Array (3912) length"
    ),
    pytest.param(
        3912, 4, None, DefaultBehavior.LAST_ELEMENT, id="Delete From Array (3912) index"
    ),
    pytest.param(
        3911, 2, None, DefaultBehavior.APPEND, id="Insert Into Array (3911) index"
    ),
    pytest.param(1506, 1, True, None, id="Number To Fractional String decimal"),
    pytest.param(1506, 2, 6, None, id="Number To Fractional String precision"),
    pytest.param(1187, 2, True, None, id="Fract/Exp String To Number decimal"),
    pytest.param(9111, 2, -1, None, id="Enqueue Element timeout"),
    pytest.param(9129, 2, -1, None, id="Enqueue Element At Opposite End timeout"),
    pytest.param(9113, 2, -1, None, id="Dequeue Element timeout"),
    pytest.param(9105, 2, -1, None, id="Wait on Notification timeout"),
    pytest.param(1503, 1, None, DefaultBehavior.REST, id="String Subset length"),
    pytest.param(1539, 1, "\t", None, id="Spreadsheet String To Array delimiter"),
    pytest.param(1352, 0, True, None, id="Quit LabVIEW quit?"),
]


@pytest.mark.parametrize("prim_id,index,default,behavior", NI_DOCUMENTED_DEFAULTS)
def test_the_catalog_holds_the_default_ni_documents(
    prim_id: int, index: int, default: object, behavior: DefaultBehavior | None
) -> None:
    resolved = get_resolver().resolve(prim_id=prim_id)
    assert resolved is not None
    terminal = next(
        t for t in resolved.terminals if t.direction == "in" and t.index == index
    )
    assert terminal.default == default
    assert terminal.default_behavior == behavior


def test_string_subset_with_length_unwired_returns_the_rest_of_the_string(
    tmp_path: Path,
) -> None:
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({}, {30: "result"}, pane={0: 30})
    )
    node = primitive(10, STRING_SUBSET, [(1, I32), (2, I32), (3, STR)], out_type=STR)
    xml = root_diagram(
        [],
        [
            srn(800, [constant(701, STR, "abcdef"), constant(703, I32, 2)]),
            srn(810, [fp_terminal(5030, 30)]),
            node,
        ],
        [wire(901, 701, 17), wire(902, 703, 15), wire(903, 11, 5030)],
    )
    views = load(tmp_path, xml)
    context = views.graph.get_vi_context(views.vi)
    source = build_module(context, "T.vi", graph=views.graph)
    namespace: dict[str, object] = {}
    exec(compile(source, "T.py", "exec"), namespace)  # noqa: S102
    assert namespace["t"]().result == "cdef"  # type: ignore[operator]


def _vilib_call_lvnet(
    tmp_path: Path,
    vi_name: str,
    path_tokens: list[str],
    terminals: list[tuple[int, str]],
) -> str:
    """lvnet of a caller VI holding one call of a vi.lib VI whose input
    terminals, all unwired, sit at the given connector-pane slots with the given
    LabVIEW types."""
    names = sorted({type_name for _, type_name in terminals})
    type_ids = {name: 20 + n for n, name in enumerate(names)}
    comments = "".join(f"<!-- TypeID {i}: {t} -->\n" for t, i in type_ids.items())
    terms = "".join(
        f"""<SL__arrayElement class="term" uid="{21 + n}">
        <dco class="parm" uid="{121 + n}">
          <paramIdx>{slot}</paramIdx>
          <typeDesc>TypeID({type_ids[type_name]})</typeDesc>
        </dco></SL__arrayElement>"""
        for n, (slot, type_name) in enumerate(terminals)
    )
    (tmp_path / "Caller_BDHb.xml").write_text(
        f"""<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">
<zPlaneList elements="0" /><nodeList elements="1">
<SL__arrayElement class="iUse" uid="20"><termList elements="{len(terminals)}">
{terms}</termList></SL__arrayElement></nodeList></root></SL__rootObject>"""
    )
    strings = "".join(f"<String>{escape(token)}</String>" for token in path_tokens)
    (tmp_path / "Caller.xml").write_text(
        f"""<?xml version='1.0' encoding='utf-8'?>
<LVSR><TM80>{comments}</TM80><LIbd><Section><BDHP><IUVI LinkSaveFlag="0">
<LinkSaveQualName><String>{vi_name}</String></LinkSaveQualName>
<LinkSavePathRef>{strings}</LinkSavePathRef>
<LinkOffsetList><Offset>0x14</Offset></LinkOffsetList>
</IUVI></BDHP></Section></LIbd></LVSR>"""
    )
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Caller_BDHb.xml")
    return render_lvnet(
        build_netlist_from_graph(graph, graph.list_vis()[0]), verbose=True
    )


# (vi.lib VI, path under <vilib>, [(connector-pane slot, type)], expected lvnet
# lines). The slots are the ones corpus callers record for the VI's controls;
# each input is named by the catalog's label for its slot.
VILIB_CALL_DEFAULTS = [
    pytest.param(
        "System Exec.vi",
        ["Platform", "system.llb"],
        [(5, "NumUInt32"), (6, "Boolean"), (7, "Boolean"), (11, "String")],
        [
            "in expected output size (4096) : U32 default 4096",
            "in run minimized? (F) : TF default False",
            "in wait until completion? (T) : TF default True",
            "in 11 : String default",
        ],
        id="System Exec",
    ),
    pytest.param(
        "Three Button Dialog.vi",
        ["dialog"],
        [(4, "Boolean"), (7, "String"), (8, "String"), (9, "String"), (10, "String")],
        [
            "in Allow user to close window? (TRUE) : TF default True",
            'in Window Title ("") : String default ""',
            'in Right Button Text ("Cancel") : String default "Cancel"',
            'in Center Button Text ("No") : String default "No"',
            'in Left Button Text ("Yes") : String default "Yes"',
        ],
        id="Three Button Dialog",
    ),
    pytest.param(
        "Picture to Pixmap.vi",
        ["picture", "pictutil.llb"],
        [(9, "NumInt32")],
        ["in depth : I32 default 24"],
        id="Picture to Pixmap",
    ),
    pytest.param(
        "Write to System Log.vi",
        ["Utility", "SystemLogging"],
        [(10, "NumInt32")],
        ["in severity : I32 default 0"],
        id="Write to System Log",
    ),
    pytest.param(
        "Butterworth Filter.vi",
        ["Analysis", "3filter.llb"],
        [(0, "Boolean"), (4, "NumInt32"), (8, "NumFloat64")],
        [
            "in init/cont (init:F) : TF default False",
            "in order : I32 default 2",
            "in sampling freq: fs : DBL default 1",
        ],
        id="Butterworth Filter",
    ),
]


@pytest.mark.parametrize("vi_name,path,terminals,expected", VILIB_CALL_DEFAULTS)
def test_an_unwired_vilib_input_shows_the_default_ni_documents(
    tmp_path: Path,
    vi_name: str,
    path: list[str],
    terminals: list[tuple[int, str]],
    expected: list[str],
) -> None:
    text = _vilib_call_lvnet(tmp_path, vi_name, ["<vilib>", *path, vi_name], terminals)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    for line in expected:
        assert line in lines
