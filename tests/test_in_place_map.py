"""The In Place Element structure's Map Get / Replace Value border node pair.

The diagram: controls ``m`` (a map of strings to I32), ``k`` and ``d`` feed an In
Place Element structure whose left tile looks ``k`` up in ``m`` (``d`` is the
default value), whose body adds one to the value, and whose right tile puts it back
by an action; the structure's outputs feed the indicators ``mo`` (map out), ``ko``
(key out) and ``nf`` (key not found?).

NI ("Map Get / Replace Value", labview-api-ref
``functions/map-get-replace-value.html``): action 0 Modify Entry only if found
(the default when unwired), 1 Add or Modify Entry, 2 Delete Entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.nodes import in_place_map
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind, Terminal
from tests.helpers import exec_generated
from tests.structure_diagrams import (
    BOOL,
    I32,
    STR,
    TYPES,
    constant,
    dco,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    root_diagram,
    srn,
    term,
    wire,
)

MAP = 3
MAP_TYPES = [
    *TYPES,
    '<TypeDesc Type="116" Format="inline" Label="map">'
    f'<TypeDesc TypeID="{STR}" /><TypeDesc TypeID="{I32}" /></TypeDesc>',
]


def _tile_terminal(
    uid: int, dco_uid: int, type_id: int, *, output: bool, port: int | None = None
) -> str:
    """A tile terminal; ``port`` is the structure terminal that carries the same data
    (the second face of the terminal's ``dco``)."""
    faces = None if port is None else [uid, port]
    return term(
        uid,
        dco(
            "Class018E",
            dco_uid,
            type_id=type_id,
            faces=faces,
            flags=1 if output else None,
        ),
    )


def _tile(uid: int, terminals: list[str], agg: int, listed: list[int]) -> str:
    return (
        f'<SL__arrayElement class="Class018D" uid="{uid}">'
        f'<termList elements="{len(terminals)}">{"".join(terminals)}</termList>'
        f'<dcoList elements="{len(listed)}">'
        + "".join(f'<SL__arrayElement uid="{d}" />' for d in listed)
        + f'</dcoList><dcoAgg uid="{agg}" /><poser uid="119" />'
        "<bounds>(0, 0, 36, 32)</bounds></SL__arrayElement>"
    )


def _structure() -> str:
    """Structure 100. Ports: 101 map, 102 key, 103 default value in; 104 map, 105
    key, 106 key not found? out. Left tile 120, right tile 130; an Increment (150)
    between the two ``value`` terminals; the action constant is wired to 135."""
    left = _tile(
        120,
        [
            _tile_terminal(121, 201, MAP, output=False, port=101),
            _tile_terminal(122, 202, I32, output=True),
            _tile_terminal(123, 203, STR, output=False, port=102),
            _tile_terminal(124, 204, BOOL, output=True),
            _tile_terminal(125, 205, I32, output=False, port=103),
        ],
        agg=202,
        listed=[203, 204, 205],
    )
    right = _tile(
        130,
        [
            _tile_terminal(131, 211, MAP, output=True, port=104),
            _tile_terminal(132, 212, I32, output=False),
            _tile_terminal(133, 213, STR, output=True, port=105),
            _tile_terminal(134, 214, BOOL, output=True, port=106),
            _tile_terminal(135, 215, I32, output=False),
        ],
        agg=212,
        listed=[213, 214, 215],
    )
    joins = [(122, 153), (151, 132)]
    wires = "".join(
        f'<SL__arrayElement class="signal" uid="{140 + i}"><termList elements="2">'
        f'<SL__arrayElement uid="{a}" /><SL__arrayElement uid="{b}" />'
        "</termList></SL__arrayElement>"
        for i, (a, b) in enumerate(joins)
    )
    ports = [
        term(101, '<dco uid="201" />'),
        term(102, '<dco uid="203" />'),
        term(103, '<dco uid="205" />'),
        term(104, '<dco uid="211" />'),
        term(105, '<dco uid="213" />'),
        term(106, '<dco uid="214" />'),
    ]
    nodes = [left, right, increment(150)]
    return (
        '<SL__arrayElement class="decomposeRecomposeStructure" uid="100">'
        f'<termList elements="{len(ports)}">{"".join(ports)}</termList>'
        '<diagramList elements="1"><SL__arrayElement class="diag" uid="110">'
        '<zPlaneList elements="0" />'
        f'<nodeList elements="{len(nodes)}">{"".join(nodes)}</nodeList>'
        f'<signalList elements="{len(joins)}">{wires}</signalList>'
        "</SL__arrayElement></diagramList></SL__arrayElement>"
    )


def _generate(
    tmp_path: Path, *, action: int | None, default_wired: bool = True
) -> tuple[str, dict[str, object]]:
    controls = {30: "m", 31: "k", **({32: "d"} if default_wired else {})}
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml(
            controls,
            {33: "mo", 34: "ko", 35: "nf"},
            pane={
                0: 30,
                1: 31,
                **({2: 32} if default_wired else {}),
                3: 33,
                4: 34,
                5: 35,
            },
        )
    )
    constants = [] if action is None else [constant(700, I32, action)]
    wires = [
        wire(901, 5030, 101),
        wire(902, 5031, 102),
        wire(904, 104, 5033),
        wire(905, 105, 5034),
        wire(906, 106, 5035),
    ]
    terminals = [
        fp_terminal(5030, 30),
        fp_terminal(5031, 31),
        fp_terminal(5033, 33),
        fp_terminal(5034, 34),
        fp_terminal(5035, 35),
    ]
    if default_wired:
        terminals.append(fp_terminal(5032, 32))
        wires.append(wire(903, 5032, 103))
    if constants:
        wires.append(wire(907, 700, 135))
    xml = root_diagram(
        [_structure()],
        [srn(800, [*terminals, *constants]), '<SL__arrayElement uid="100" />'],
        wires,
    )
    views = load(tmp_path, xml, MAP_TYPES)
    source = build_module(
        views.graph.get_vi_context(views.vi), views.vi, graph=views.graph
    )
    namespace: dict[str, object] = {}
    exec_generated(source, namespace, "T.py")  # noqa: S102
    return source, namespace


def _run(tmp_path: Path, action: int | None, entries: dict, key: str, default=10):
    source, namespace = _generate(tmp_path, action=action)
    assert "NotImplementedError" not in source
    result = namespace["t"](m=entries, k=key, d=default)  # type: ignore[operator]
    return result.mo, result.ko, result.nf


def test_the_tiles_are_named_and_paired_by_what_the_file_records(
    tmp_path: Path,
) -> None:
    _generate(tmp_path, action=1)
    from lvkit.graph import InMemoryVIGraph

    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "T_BDHb.xml")
    names = {
        n["node"].id.split("::")[-1]: n["node"].name
        for n in graph._graph.nodes.values()
        if n["node"].id.split("::")[-1] in ("120", "130")
    }
    assert names == {"120": "Map Get Value", "130": "Map Replace Value"}


def test_add_or_modify_replaces_a_found_value(tmp_path: Path) -> None:
    mo, ko, nf = _run(tmp_path, 1, {"a": 1}, "a")
    assert (mo, ko, nf) == ({"a": 2}, "a", False)


def test_add_or_modify_adds_a_key_that_is_not_found_with_the_default_plus_one(
    tmp_path: Path,
) -> None:
    mo, ko, nf = _run(tmp_path, 1, {"a": 1}, "z")
    assert (mo, ko, nf) == ({"a": 1, "z": 11}, "z", True)


def test_modify_only_if_found_changes_a_found_entry(tmp_path: Path) -> None:
    assert _run(tmp_path, 0, {"a": 1}, "a") == ({"a": 2}, "a", False)


def test_modify_only_if_found_does_nothing_for_a_key_that_is_not_found(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, 0, {"a": 1}, "z") == ({"a": 1}, "z", True)


def test_an_unwired_action_is_modify_only_if_found(tmp_path: Path) -> None:
    assert _run(tmp_path, None, {"a": 1}, "z")[0] == {"a": 1}
    assert _run(tmp_path, None, {"a": 1}, "a")[0] == {"a": 2}


def test_delete_removes_a_found_entry_and_leaves_the_map_that_came_in(
    tmp_path: Path,
) -> None:
    entries = {"a": 1, "b": 2}
    assert _run(tmp_path, 2, entries, "a") == ({"b": 2}, "a", False)
    assert entries == {"a": 1, "b": 2}


def test_delete_does_nothing_for_a_key_that_is_not_found(tmp_path: Path) -> None:
    assert _run(tmp_path, 2, {"a": 1}, "z") == ({"a": 1}, "z", True)


def test_an_action_value_nobody_documents_is_a_gap(tmp_path: Path) -> None:
    source, _namespace = _generate(tmp_path, action=3)
    assert "NotImplementedError" in source
    assert "action 3" in source


def test_a_found_value_is_read_without_the_default(tmp_path: Path) -> None:
    source, _namespace = _generate(tmp_path, action=1)
    assert ".get(" in source


def _tile_node(node_id: str, terminals: list[Terminal]) -> PrimitiveNode:
    return PrimitiveNode(
        id=node_id, vi_path="t.vi", name="x", node_type="Class018D", terminals=terminals
    )


def test_an_action_that_is_only_known_at_run_time_chooses_the_branch_then(
    tmp_path: Path,
) -> None:
    from tests.helpers import make_ctx

    right = _tile_node(
        "right",
        [
            Terminal(
                id="map_out",
                index=0,
                direction="output",
                lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Map"),
            ),
            Terminal(id="value_in", index=1, direction="input", nmux_role="agg"),
            Terminal(id="key_out", index=2, direction="output", nmux_role="list"),
            Terminal(id="nf_out", index=3, direction="output", nmux_role="list"),
            Terminal(id="action_in", index=4, direction="input", nmux_role="list"),
        ],
    )
    ctx = make_ctx("map_out", "value_in", "key_out", "nf_out", "action_in")
    ctx.bind("value_in", "new")
    ctx.bind("action_in", "action")
    statements = in_place_map.write_tile(right, "m", "k", ctx)
    code = "\n".join(ast.unparse(s) for s in statements)
    cases = (
        (0, {"a": 1}, "a", {"a": 5}),
        (0, {"a": 1}, "z", {"a": 1}),
        (1, {"a": 1}, "a", {"a": 5}),
        (1, {"a": 1}, "z", {"a": 1, "z": 5}),
        (2, {"a": 1, "z": 9}, "z", {"a": 1}),
        (2, {"a": 1}, "z", {"a": 1}),
    )
    for action, entries, key, expected in cases:
        namespace: dict[str, object] = {
            "m": entries,
            "k": key,
            "new": 5,
            "action": action,
        }
        exec(compile(code, "<generated>", "exec"), namespace)
        assert namespace[ctx.resolve("map_out")] == expected
    namespace = {"m": {}, "k": "a", "new": 5, "action": 7}
    with pytest.raises(NotImplementedError, match="action 7"):
        exec(compile(code, "<generated>", "exec"), namespace)


def test_a_cluster_key_stays_a_gap(tmp_path: Path) -> None:
    from tests.helpers import make_ctx

    cluster = LVType(kind=LVTypeKind.CLUSTER)
    left = _tile_node(
        "left",
        [
            Terminal(
                id="map_in",
                index=0,
                direction="input",
                lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Map"),
            ),
            Terminal(id="value_out", index=1, direction="output", nmux_role="agg"),
            Terminal(
                id="key_in",
                index=2,
                direction="input",
                nmux_role="list",
                lv_type=cluster,
            ),
            Terminal(id="nf_out", index=3, direction="output", nmux_role="list"),
            Terminal(id="default_in", index=4, direction="input", nmux_role="list"),
        ],
    )
    ctx = make_ctx("map_in", "value_out", "key_in", "nf_out", "default_in")
    ctx.bind("map_in", "m")
    ctx.bind("key_in", "k")
    with pytest.raises(ConversionGap, match="key of cluster type"):
        in_place_map.read_tile(left, ctx)


def test_an_unwired_default_value_is_the_default_of_the_value_type(
    tmp_path: Path,
) -> None:
    source, namespace = _generate(tmp_path, action=1, default_wired=False)
    assert "NotImplementedError" not in source
    result = namespace["t"](m={"a": 1}, k="z")  # type: ignore[operator]
    assert (result.mo, result.nf) == ({"a": 1, "z": 1}, True)


def test_the_border_nodes_are_nets_of_the_structure_in_the_views(
    tmp_path: Path,
) -> None:
    """Like the other border nodes, the tiles sit on the structure's boundary: a
    value one supplies to the body is ``get<k>``, a value the body feeds one is
    ``put<k>`` (map, key, default value, value, action in terminal order)."""
    _generate(tmp_path, action=1)
    views = load(tmp_path, (tmp_path / "T_BDHb.xml").read_text(), MAP_TYPES)
    assert views.diagram == (
        "    in-place-element (id 100) :\n"
        "      function Increment_150 : Increment\n"
        "        in   x   : I32 = inplace_100::get0\n"
        "        out  x+1 : I32\n"
        "      inplace_100::put0 = m\n"
        "      inplace_100::put1 = k\n"
        "      inplace_100::put2 = d\n"
        "      inplace_100::put3 = Increment_150::x+1\n"
        "      inplace_100::put4 = 1\n"
        "    mo = inplace_100::out0\n"
        "    ko = inplace_100::out1\n"
        "    nf = inplace_100::out2"
    )
    assert "Map Get Value" not in views.describe


def test_the_default_of_a_map_or_set_type_is_an_empty_one() -> None:
    """A map of maps read for a missing key gives an empty map to read on."""
    from lvkit.codegen.ast_utils import type_default_expr

    for name, text in (("Map", "{}"), ("Set", "set()")):
        lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)
        assert ast.unparse(type_default_expr(lv_type)) == text
