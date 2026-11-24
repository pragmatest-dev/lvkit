"""A Tree control's items (``TreeItems`` of the generated ``panels.py``): the items,
tags and indent levels the VI file saves are read, and every Tree property and method
on a reference, a bound node or the default Tree object runs against them. Each case
is a handmade diagram or heap xml whose generated package is imported and run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import FPTerminal, LVType, Terminal
from tests.class_graphs import INT, Project, Recipe, pane, wire
from tests.error_diagrams import REQUIRED, STR, Vi
from tests.error_diagrams import Project as XmlProject
from tests.error_diagrams import project as xml_project  # noqa: F401
from tests.panel_diagrams import value_property
from tests.test_combo_box_strings import STRINGS
from tests.test_control_references import REFNUM, property_node
from tests.test_front_panel_reference import BOOL, ERROR, STRING, constant

ADD_ITEM = 1969895428
ADD_MULTIPLE = 1969895447
DELETE = 1969895427
MOVE = 1969895436
SET_TAG = 1969895438
SORT = 1969895431
GET_CHILD = 1969895437
GET_NEXT = 1969895424
GET_PARENT = 1969895425
GET_PATH = 1969895426
OPEN_ITEM = 1969895430
OPEN_ALL = 1969895433
ENSURE_VISIBLE = 1969895432
SET_SYMBOLS = 1969895442
REVERT_SYMBOLS = 1969895444
POINT_TO_ROW_COLUMN = 1969895445

ACTIVE_TAG = (1969896461,)
ACTIVE_ROW = (1969896514,)
DISPLAYED = (1969896476,)
ACTIVE_OPEN = (1969896462,)
INDENT = (1969896465,)
SYMBOL = (1969896466,)
ACTIVE_COLUMN = (1969896467,)
CELL_STRING = (1969896473,)
CELL_WIDTH = (1969896472,)

NAMES = {
    ADD_ITEM: "Edit Tree Items.Add Item",
    ADD_MULTIPLE: "Edit Tree Items.Add Multiple Items to End",
    DELETE: "Edit Tree Items.Delete",
    MOVE: "Edit Tree Items.Move Item",
    SET_TAG: "Edit Tree Items.Set Tag",
    SORT: "Edit Tree Items.Sort Children",
    GET_CHILD: "Navigate Tree.Get Child",
    GET_NEXT: "Navigate Tree.Get Next",
    GET_PARENT: "Navigate Tree.Get Parent",
    GET_PATH: "Navigate Tree.Get Path",
    OPEN_ITEM: "Open/Close.Item",
    OPEN_ALL: "Open/Close.Open/Close All",
    ENSURE_VISIBLE: "Open/Close.Ensure Visible",
    SET_SYMBOLS: "Custom Item Symbols.Set Symbol Array",
    REVERT_SYMBOLS: "Custom Item Symbols.Revert Symbols",
    POINT_TO_ROW_COLUMN: "Point To Row Column",
}

# What a method gives at terminal 5 (None: no value).
RETURNS: dict[int, LVType | None] = {
    ADD_ITEM: STRING,
    SET_TAG: STRING,
    GET_CHILD: STRING,
    GET_NEXT: STRING,
    GET_PARENT: STRING,
    GET_PATH: STRINGS,
}

# A step: the method, its parameters as (index in NI's order, type, text), and
# whether to take the result to an output.
Step = tuple[int, list[tuple[int, LVType, str]], bool]

# Three top-level items, "a" and "c" with one child each (rows: tag, indent, texts).
SAVED_ITEMS = [
    ["a", 0, ["A", "1"]],
    ["a1", 1, ["A one", "2"]],
    ["b", 0, ["B", "3"]],
    ["c", 0, ["C", "4"]],
    ["c1", 1, ["C one", "5"]],
]


def tree_node(vi: str, tag: str, code: int, params: list[tuple[int, LVType]]) -> Any:
    """An Invoke Node of the Tree method ``code``: the reference at 0 (out 1), the
    error at 2 (out 3), the result at 5 and parameter k at input 6 + 2k."""
    returns = RETURNS.get(code)
    fixed: list[tuple[str, int, str, LVType]] = [
        ("ref", 0, "input", REFNUM),
        ("ref_out", 1, "output", REFNUM),
        ("err_in", 2, "input", ERROR),
        ("err_out", 3, "output", ERROR),
    ]
    if returns is not None:
        fixed.append(("result", 5, "output", returns))
    fixed += [(f"p{k}", 6 + 2 * k, "input", t) for k, t in params]
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name="Invoke Node",
        node_type="invokeNode",
        object_name="Tree",
        method_name=NAMES[code],
        method_code=code,
        terminals=[
            Terminal(id=f"{vi}:{tag}.{name}", index=index, direction=d, lv_type=t)
            for name, index, d, t in fixed
        ],
    )


def runner(steps: list[Step], with_reference: bool = True) -> Recipe:
    """A VI that runs ``steps`` in turn on the Tree reference ``ctl`` through the
    error wire; the result of each step taken is the output ``s<n>``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        nodes: list[GraphNode] = []
        wires = []
        inputs = [pane(vi, "input", "ctl", REFNUM, "ctl", 0)] if with_reference else []
        outputs: list[FPTerminal] = []
        previous = None
        for number, (code, params, take) in enumerate(steps):
            tag = f"s{number}"
            nodes.append(tree_node(vi, tag, code, [(k, t) for k, t, _ in params]))
            if with_reference:
                wires.append(wire(vi, "ctl", f"{tag}.ref"))
            for k, kind, text in params:
                nodes.append(constant(vi, f"{tag}_p{k}", text, kind))
                wires.append(wire(vi, f"{tag}_p{k}", f"{tag}.p{k}"))
            if previous is not None:
                wires.append(wire(vi, f"{previous}.err_out", f"{tag}.err_in"))
            previous = tag
            if take:
                kind = RETURNS[code]
                assert kind is not None
                outputs.append(pane(vi, "output", tag, kind, tag, len(outputs)))
                wires.append(wire(vi, f"{tag}.result", tag))
        outputs.append(pane(vi, "output", "err", ERROR, "error out", len(outputs)))
        wires.append(wire(vi, f"{previous}.err_out", "err"))
        return VIContext(
            name=vi, inputs=inputs, outputs=outputs, data_flow=wires
        ), nodes

    return build


def properties(rows: list[tuple[tuple[int, ...], str, LVType, str]]) -> Recipe:
    """A VI that does the rows of one Property Node on the Tree reference ``ctl``
    in order: ``(codes, "input" or "output", type, text)``; each read is the output
    ``r<n>``, and a write wires the constant ``text``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(
            vi,
            "p",
            "Tree",
            [(f"r{n}", codes, d, t) for n, (codes, d, t, _) in enumerate(rows)],
        )
        nodes: list[GraphNode] = [node]
        wires = [wire(vi, "ctl", "p.ref")]
        outputs: list[FPTerminal] = []
        for n, (_, direction, kind, text) in enumerate(rows):
            if direction == "input":
                nodes.append(constant(vi, f"k{n}", text, kind))
                wires.append(wire(vi, f"k{n}", f"p.r{n}"))
            else:
                outputs.append(pane(vi, "output", f"r{n}", kind, f"r{n}", len(outputs)))
                wires.append(wire(vi, f"p.r{n}", f"r{n}"))
        outputs.append(pane(vi, "output", "err", ERROR, "error out", len(outputs)))
        wires.append(wire(vi, "p.err_out", "err"))
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
                outputs=outputs,
                data_flow=wires,
            ),
            nodes,
        )

    return build


def target(items: Any = None, widths: Any = None, headers: Any = None) -> Recipe:
    """A VI whose panel has the Tree control ``tree`` (saved with ``items``)."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        control = pane(vi, "input", "tree", STRING, "tree", 0, tab_index=0)
        starting = {}
        if items is not None:
            starting["tree_items"] = items
        if widths is not None:
            starting["tree_cell_widths"] = widths
        if headers is not None:
            starting["tree_column_headers"] = headers
        control = control.model_copy(
            update={"control_type": "treeControl", "starting_properties": starting}
        )
        return (
            VIContext(
                name=vi,
                inputs=[control],
                outputs=[pane(vi, "output", "out", STRING, "out", 1, tab_index=1)],
                data_flow=[wire(vi, "tree", "out")],
            ),
            [],
        )

    return build


def package_of(
    tmp_path: Path,
    steps: list[Step] | None = None,
    rows: list[tuple[tuple[int, ...], str, LVType, str]] | None = None,
    items: Any = SAVED_ITEMS,
    widths: Any = None,
    headers: Any = None,
    gaps: list[Gap] | None = None,
) -> Any:
    tmp_path.mkdir(parents=True, exist_ok=True)
    project = Project(tmp_path)
    project.add_vi("Target", target(items, widths, headers))
    if steps is not None:
        project.add_vi("Runner", runner(steps))
    if rows is not None:
        project.add_vi("Props", properties(rows))
    return project.generate(gaps)


def tree_of(module: Any) -> Any:
    refs = {c.label: c for c in module("target").target.panel.controls}
    return refs["tree"]


def tags(module: Any) -> list[tuple[str, int]]:
    return [(i.tag, i.indent) for i in tree_of(module).tree._items]  # noqa: SLF001


def string(k: int, text: str) -> tuple[int, LVType, str]:
    return (k, STRING, text)


def integer(k: int, number: int) -> tuple[int, LVType, str]:
    return (k, INT, str(number))


def boolean(k: int, flag: bool) -> tuple[int, LVType, str]:
    return (k, BOOL, str(flag))


def test_the_items_the_file_saves_are_navigated(tmp_path: Path) -> None:
    steps: list[Step] = [
        (GET_CHILD, [string(0, "")], True),
        (GET_CHILD, [string(0, "a")], True),
        (GET_CHILD, [string(0, "b")], True),
        (GET_NEXT, [string(0, "a")], True),
        (GET_NEXT, [string(0, "c")], True),
        (GET_PARENT, [string(0, "c1")], True),
        (GET_PARENT, [string(0, "c")], True),
        (GET_PATH, [string(0, "c1")], True),
    ]
    gaps: list[Gap] = []
    package = package_of(tmp_path, steps, gaps=gaps)
    assert gaps == []
    with package.use() as module:
        result = module("runner").runner(ctl=tree_of(module))
        assert result.error_out is None
        assert [getattr(result, f"s{n}") for n in range(7)] == [
            "a",
            "a1",
            "",
            "b",
            "",
            "c",
            "",
        ]
        assert result.s7 == ["c", "c1"]


def test_add_delete_move_and_set_tag(tmp_path: Path) -> None:
    steps: list[Step] = [
        # A new item under "b" (the default position 0), tagged by its text.
        (ADD_ITEM, [string(0, "b"), string(2, "B one")], True),
        # The same text again: LabVIEW appends a number to make the tag unique.
        (ADD_ITEM, [string(0, "b"), string(2, "B one")], True),
        # Child Position -1 places it under the last child.
        (ADD_ITEM, [string(0, "a"), integer(1, -1), string(2, "A two")], True),
        (DELETE, [string(0, "c")], False),
        (MOVE, [string(0, "b"), string(1, "a")], False),
        (SET_TAG, [string(0, "a"), string(1, " TREE_top ")], True),
    ]
    package = package_of(tmp_path, steps)
    with package.use() as module:
        result = module("runner").runner(ctl=tree_of(module))
        assert result.error_out is None
        assert (result.s0, result.s1, result.s2) == ("B one", "B one1", "A two")
        # A tag that begins with TREE_ gets an underscore and white space is removed.
        assert result.s5 == "_TREE_top"
        assert tags(module) == [
            ("_TREE_top", 0),
            ("b", 1),
            ("a1", 1),
            ("A two", 1),
            # Move Item without Carry Children leaves the children of "b" where they
            # were, under the parent "b" had (the top level).
            ("B one1", 0),
            ("B one", 0),
        ]


def test_delete_without_children_moves_them_up_and_an_empty_tag_empties_the_tree(
    tmp_path: Path,
) -> None:
    steps: list[Step] = [
        (DELETE, [string(0, "a"), boolean(1, False)], False),
    ]
    package = package_of(tmp_path, steps)
    with package.use() as module:
        assert module("runner").runner(ctl=tree_of(module)).error_out is None
        assert tags(module) == [("a1", 0), ("b", 0), ("c", 0), ("c1", 1)]
    empty: list[Step] = [(DELETE, [string(0, ""), boolean(1, True)], False)]
    with package_of(tmp_path / "empty", empty).use() as module:
        assert module("runner").runner(ctl=tree_of(module)).error_out is None
        assert tags(module) == []


def test_open_close_and_displayed_items(tmp_path: Path) -> None:
    steps: list[Step] = [
        (OPEN_ITEM, [string(0, "a"), boolean(1, True)], False),
    ]
    rows = [(DISPLAYED, "output", STRINGS, "")]
    package = package_of(tmp_path, steps, rows)
    with package.use() as module:
        reference = tree_of(module)
        # Nothing is saved open: only the top level is displayed.
        assert module("props").props(ctl=reference).r0 == ["a", "b", "c"]
        assert module("runner").runner(ctl=reference).error_out is None
        assert module("props").props(ctl=reference).r0 == ["a", "a1", "b", "c"]
    close: list[Step] = [(OPEN_ALL, [boolean(0, True)], False)]
    with package_of(tmp_path / "all", close, rows).use() as module:
        reference = tree_of(module)
        assert module("runner").runner(ctl=reference).error_out is None
        assert module("props").props(ctl=reference).r0 == ["a", "a1", "b", "c", "c1"]
    ensure: list[Step] = [(ENSURE_VISIBLE, [string(0, "c1")], False)]
    with package_of(tmp_path / "ensure", ensure, rows).use() as module:
        reference = tree_of(module)
        assert module("runner").runner(ctl=reference).error_out is None
        assert module("props").props(ctl=reference).r0 == ["a", "b", "c", "c1"]


def test_sort_children_by_a_column_in_the_given_case_mode(tmp_path: Path) -> None:
    items = [["x", 0, ["b", "1"]], ["y", 0, ["A", "2"]], ["z", 0, ["c", "0"]]]
    cases = {
        # Case sensitive: "A" sorts before "b" and "c" (code point order).
        (1, False, 0): ["y", "x", "z"],
        # Case insensitive.
        (2, False, 0): ["y", "x", "z"],
        # Reverse order, by the second column.
        (1, True, 1): ["y", "x", "z"],
    }
    for number, ((case, reverse, column), expected) in enumerate(cases.items()):
        steps: list[Step] = [
            (
                SORT,
                [
                    string(0, ""),
                    integer(1, column),
                    integer(2, case),
                    boolean(4, reverse),
                ],
                False,
            )
        ]
        package = package_of(tmp_path / str(number), steps, items=items)
        with package.use() as module:
            assert module("runner").runner(ctl=tree_of(module)).error_out is None
            assert [t for t, _ in tags(module)] == expected


def test_an_unknown_tag_is_error_1136_on_the_nodes_error_out(
    tmp_path: Path,
) -> None:
    steps: list[Step] = [(OPEN_ITEM, [string(0, "nothing")], False)]
    package = package_of(tmp_path, steps)
    with package.use() as module:
        error = module("runner").runner(ctl=tree_of(module)).error_out
        assert error.code == 1136
        assert "invalid item tag" in error.message


def test_add_multiple_items_to_end_takes_an_indent_level_that_has_a_parent(
    tmp_path: Path,
) -> None:
    items = (
        ("d", (), "", 0, False, 0),
        ("e", ("E two",), "e-tag", 1, False, 0),
    )
    package = package_of(tmp_path, [(ADD_MULTIPLE, [], False)])
    with package.use() as module:
        tree = tree_of(module).tree
        tree.add_items_to_end(items)
        assert tags(module)[-2:] == [("d", 0), ("e-tag", 1)]
        assert tree._items[-1].texts == ["e", "E two"]  # noqa: SLF001
        # An indent level that has no parent item is an error.
        with pytest.raises(module("panels").PanelError) as raised:
            tree.add_items_to_end([("f", (), "", 3, False, 0)])
        assert raised.value.code == 1135


def test_active_item_tag_then_a_property_of_that_item(tmp_path: Path) -> None:
    rows = [
        (ACTIVE_TAG, "input", STRING, "c1"),
        (INDENT, "output", INT, ""),
        (ACTIVE_COLUMN, "input", INT, "1"),
        (CELL_STRING, "input", STRING, "five"),
        (CELL_STRING, "output", STRING, ""),
        (ACTIVE_OPEN, "input", BOOL, "True"),
        (ACTIVE_TAG, "output", STRING, ""),
    ]
    package = package_of(tmp_path, rows=rows)
    with package.use() as module:
        result = module("props").props(ctl=tree_of(module))
        assert result.error_out is None
        # "c1" is under "c": level 1; its second cell was written.
        assert result.r1 == 1
        assert result.r4 == "five"
        assert tree_of(module).tree._items[4].texts == ["C one", "five"]  # noqa: SLF001
        assert result.r6 == "c1"
        assert result.error_out is None


def test_the_active_item_before_a_write_is_the_controls_value(tmp_path: Path) -> None:
    rows = [(ACTIVE_TAG, "output", STRING, ""), (INDENT, "output", INT, "")]
    package = package_of(tmp_path, rows=rows)
    with package.use() as module:
        reference = tree_of(module)
        reference.value = "a1"
        result = module("props").props(ctl=reference)
        assert (result.r0, result.r1) == ("a1", 1)


def test_the_active_row_selects_a_displayed_item_and_an_invalid_row_is_1135(
    tmp_path: Path,
) -> None:
    rows = [(ACTIVE_ROW, "input", INT, "2"), (ACTIVE_TAG, "output", STRING, "")]
    package = package_of(tmp_path, rows=rows)
    with package.use() as module:
        assert module("props").props(ctl=tree_of(module)).r1 == "c"
    bad = [(ACTIVE_ROW, "input", INT, "9")]
    with package_of(tmp_path / "bad", rows=bad).use() as module:
        error = module("props").props(ctl=tree_of(module)).error_out
        assert error.code == 1135


def test_a_property_of_the_column_headers_item_is_error_1135(tmp_path: Path) -> None:
    rows = [
        (ACTIVE_TAG, "input", STRING, "TREE_COLUMN_HEADERS"),
        (INDENT, "output", INT, ""),
    ]
    package = package_of(tmp_path, rows=rows)
    with package.use() as module:
        assert module("props").props(ctl=tree_of(module)).error_out.code == 1135


def test_the_cell_widths_the_file_saves_are_read_then_written(tmp_path: Path) -> None:
    rows = [
        (ACTIVE_COLUMN, "input", INT, "0"),
        (CELL_WIDTH, "output", INT, ""),
        (ACTIVE_COLUMN, "input", INT, "1"),
        (CELL_WIDTH, "output", INT, ""),
        (CELL_WIDTH, "input", INT, "40"),
        (CELL_WIDTH, "output", INT, ""),
    ]
    package = package_of(tmp_path, rows=rows, widths=[[-2, 35], [0, 176]])
    with package.use() as module:
        result = module("props").props(ctl=tree_of(module))
        # Column 0 has its own width; column 1 the width of every column.
        assert (result.r1, result.r3, result.r5) == (176, 35, 40)


def test_custom_symbols_are_held_until_reverted(tmp_path: Path) -> None:
    steps: list[Step] = [(SET_SYMBOLS, [], False), (REVERT_SYMBOLS, [], False)]
    package = package_of(tmp_path, steps)
    with package.use() as module:
        assert module("runner").runner(ctl=tree_of(module)).error_out is None
        assert tree_of(module).tree.symbols is None


def test_a_node_with_no_reference_acts_on_the_default_tree_object(
    tmp_path: Path,
) -> None:
    steps: list[Step] = [
        (DELETE, [string(0, ""), boolean(1, True)], False),
        (ADD_ITEM, [string(2, "first")], True),
    ]
    project = Project(tmp_path)
    project.add_vi("Runner", runner(steps, with_reference=False))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        result = module("runner").runner()
        assert (result.s1, result.error_out) == ("first", None)


def test_point_to_row_column_stays_a_gap_that_says_why(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Target", target(SAVED_ITEMS))
    project.add_vi("Runner", runner([(POINT_TO_ROW_COLUMN, [], False)]))
    gaps: list[Gap] = []
    project.generate(gaps)
    assert len(gaps) == 1
    assert "pixel coordinate" in gaps[0].reason


def tree_heap() -> str:
    """The heap xml of a Tree control saved with the items "r" and its child "l"."""
    cells = "".join(
        f'<SL__arrayElement>"{text}"</SL__arrayElement>'
        for text in ("Root", "1", "Leaf", "2")
    )
    nodes = "".join(
        f'<SL__arrayElement class="TreeNode"><nodeFlags>0</nodeFlags>'
        f'<tag>"{tag}"</tag><indentLevel>{indent}</indentLevel></SL__arrayElement>'
        for tag, indent in (("r", 0), ("l", 1))
    )
    inner = (
        '<attributeList elements="3">'
        '<SL__arrayElement class="TableAttribute"><cellPosRow>-2</cellPosRow>'
        "<cellPosCol>-2</cellPosCol><width>35</width></SL__arrayElement>"
        '<SL__arrayElement class="TableAttribute"><cellPosRow>-1</cellPosRow>'
        "<cellPosCol>-2</cellPosCol></SL__arrayElement>"
        '<SL__arrayElement class="TableAttribute"><cellPosRow>-2</cellPosRow>'
        "<cellPosCol>0</cellPosCol><width>176</width></SL__arrayElement>"
        "</attributeList>"
        '<activeCell class="StorageRowCol"><row>2</row><col>1</col></activeCell>'
        '<baseListboxItemStrings class="SL__multiDimArray">'
        '<multiDimArraySizes elements="2"><SL__arrayElement>2</SL__arrayElement>'
        "<SL__arrayElement>2</SL__arrayElement></multiDimArraySizes>"
        f'<multiDimArrayElems elements="4">{cells}</multiDimArrayElems>'
        "</baseListboxItemStrings>"
        f'<treeNodeArray elements="2">{nodes}</treeNodeArray>'
    )
    return inner


def test_a_trees_starting_items_are_read_from_the_file(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi("Tree.vi")
    kind = vi.add_type('<TypeDesc Type="String" />')
    vi.control("t", kind, REQUIRED, ddo_class="treeControl", inner=tree_heap())
    xml_project.add(vi)
    graph = xml_project.views("Tree.vi").graph
    (control,) = graph.get_vi_context(graph.list_vis()[0]).inputs
    assert isinstance(control, FPTerminal)
    found = control.starting_properties
    assert found["tree_items"] == [["r", 0, ["Root", "1"]], ["l", 1, ["Leaf", "2"]]]
    assert found["tree_cell_widths"] == [[-2, 35], [0, 176]]
    assert found["tree_active_column"] == 1
    assert "tree_column_headers" not in found


def test_a_property_node_bound_to_the_tree_control_acts_on_its_items(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi("Bound.vi")
    tree = vi.control("t", STR, REQUIRED, ddo_class="treeControl", inner=tree_heap())
    seen = vi.indicator("seen", STR, ddo_class="stdString")
    rows = [("Active Item Tag", ACTIVE_TAG[0]), ("Cell String", CELL_STRING[0])]
    write, read = value_property(vi, tree, rows, class_name="Tree")
    vi.wire(vi.constant(STR, f"{len('l'):08X}" + b"l".hex().upper()), write)
    vi.wire(read, seen)
    xml_project.add(vi)
    package = xml_project.generate("Bound.vi")
    assert package.gaps == []
    # "l" is the item; the file saves column 1 as the active column.
    assert package.function(t="").seen == "2"


def test_the_column_headers_the_file_saves_are_read_and_written(
    tmp_path: Path,
) -> None:
    rows = [
        (ACTIVE_TAG, "input", STRING, "TREE_COLUMN_HEADERS"),
        (ACTIVE_COLUMN, "input", INT, "1"),
        (CELL_STRING, "output", STRING, ""),
        ((1969896448,), "output", STRINGS, ""),
        (CELL_STRING, "input", STRING, "Y"),
        ((1969896448,), "output", STRINGS, ""),
    ]
    package = package_of(tmp_path, rows=rows, headers=["Name", "X"])
    with package.use() as module:
        result = module("props").props(ctl=tree_of(module))
        assert (result.r2, result.r3, result.r5) == ("X", ["Name", "X"], ["Name", "Y"])
