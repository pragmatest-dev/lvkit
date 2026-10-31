"""An unwired input is a gap only when the function's code reads it, and what an
unwired input shows in lvnet is what the generated code does. Tiny handmade
diagrams (``native_vi.py``), executed."""

from __future__ import annotations

from pathlib import Path

from lvkit.codegen.errors import TerminalNotOnCall, UnknownDefault
from lvkit.codegen.gaps import Gap, gap_of
from lvkit.graph.unwired_default import BARE, instance_default, unwired_behavior
from lvkit.unresolved import _item_key
from tests.native_vi import array, generate, node, scalar

I32 = scalar("NumInt32")
STRING = scalar("String")
READ_FROM_TEXT_FILE = 8070
FILE_DIALOG = 8058
VARIANT_TO_DATA = 8003


def test_an_unwired_input_the_code_never_reads_is_no_gap(tmp_path: Path) -> None:
    """Read from Text File reads `file`, never `count` or `prompt`."""
    read = node(
        "rd",
        READ_FROM_TEXT_FILE,
        [
            (2, "output", STRING),
            (7, "input", STRING),
            (8, "input", None),
            (10, "input", I32),
            (11, "input", STRING),
        ],
    )
    gaps: list[Gap] = []
    generated = generate(
        {"path": STRING},
        {"text": STRING},
        [read],
        [("fp:path", "rd:11"), ("rd:2", "fp:text")],
        gaps=gaps,
    )
    assert gaps == []
    source = tmp_path / "t.txt"
    source.write_text("hello")
    assert generated.function(str(source)).text == "hello"  # type: ignore[operator]


def test_file_dialog_prompt_is_read_by_nothing() -> None:
    dialog = node(
        "fd",
        FILE_DIALOG,
        [
            (3, "output", STRING),
            (4, "input", STRING),
            (8, "input", None),
            (11, "input", STRING),
        ],
    )
    gaps: list[Gap] = []
    generate(
        {"start": STRING},
        {"chosen": STRING},
        [dialog],
        [("fp:start", "fd:11"), ("fd:3", "fp:chosen")],
        gaps=gaps,
    )
    assert gaps == []


def test_an_input_the_code_reads_that_the_call_lacks_is_a_terminal_mapping_gap() -> (
    None
):
    """Variant To Data's code reads input 8; a call numbered 0-4 has no such input.
    It is a numbering mismatch, not an unknown default, and never takes a default."""
    variant_to_data = node(
        "vd",
        VARIANT_TO_DATA,
        [
            (0, "input", None),
            (1, "output", None),
            (2, "input", scalar("Variant")),
            (3, "input", I32),
            (4, "output", I32),
        ],
    )
    gaps: list[Gap] = []
    generate(
        {"v": scalar("Variant")},
        {"data": I32},
        [variant_to_data],
        [("fp:v", "vd:2"), ("vd:4", "fp:data")],
        gaps=gaps,
    )
    assert [g.kind for g in gaps] == ["terminal_mapping"]
    assert "'variant' of Variant To Data is not on this call" in gaps[0].reason


def _index_array(rank_two: bool = False):
    elements = array("NumInt32")
    return node(
        "ia",
        None,
        [
            (0, "input", elements),
            (1, "output", I32),
            (2, "input", I32),
            (3, "output", I32),
            (4, "input", I32),
        ],
        node_type="aIndx",
    )


def test_a_later_unwired_index_shows_what_the_code_does() -> None:
    index_array = _index_array()
    first, second = index_array.terminals[2], index_array.terminals[4]
    assert unwired_behavior(index_array, first) is None
    assert unwired_behavior(index_array, second) is not None
    shown = instance_default(None, index_array, second, "Index Array")  # type: ignore[arg-type]
    assert shown.literal == "(previous index + 1)"
    assert instance_default(None, index_array, first, "Index Array").literal == "0"  # type: ignore[arg-type]


def test_a_later_unwired_index_follows_the_previous_one_in_the_code() -> None:
    generated = generate(
        {"a": array("NumInt32")},
        {"first": I32, "second": I32},
        [_index_array()],
        [("fp:a", "ia:0"), ("ia:1", "fp:first"), ("ia:3", "fp:second")],
    )
    result = generated.function([10, 20, 30])  # type: ignore[operator]
    assert (result.first, result.second) == (10, 20)


def test_unknown_default_gaps_of_different_inputs_are_different_items() -> None:
    def gap(terminal: str) -> Gap:
        return gap_of(UnknownDefault("File Dialog", terminal), "A.vi", None)

    assert _item_key(gap("prompt")) != _item_key(gap("pattern_label"))
    assert gap("prompt").name == "prompt"
    assert isinstance(TerminalNotOnCall, type)


def _index_2d(*, row: bool, column: bool, output):
    elements = array("NumInt32", 2)
    index_array = node(
        "ia",
        None,
        [
            (0, "input", elements),
            (1, "output", output),
            (2, "input", I32),
            (3, "input", I32),
        ],
        node_type="aIndx",
    )
    inputs = {"a": elements}
    if row:
        inputs["i"] = I32
    if column:
        inputs["j"] = I32
    wires = [("fp:a", "ia:0"), ("ia:1", "fp:out")]
    if row:
        wires.append(("fp:i", "ia:2"))
    if column:
        wires.append(("fp:j", "ia:3"))
    return generate(inputs, {"out": output}, [index_array], wires)


GRID = [[1, 2], [3, 4]]


def test_a_row_of_a_2d_array() -> None:
    generated = _index_2d(row=True, column=False, output=array("NumInt32"))
    assert generated.function(GRID, 1).out == [3, 4]  # type: ignore[operator]
    assert generated.function(GRID, 7).out == []  # type: ignore[operator]


def test_a_column_of_a_2d_array() -> None:
    generated = _index_2d(row=False, column=True, output=array("NumInt32"))
    assert generated.function(GRID, 1).out == [2, 4]  # type: ignore[operator]


def test_an_element_of_a_2d_array() -> None:
    generated = _index_2d(row=True, column=True, output=I32)
    assert generated.function(GRID, 1, 0).out == 3  # type: ignore[operator]
    assert generated.function(GRID, 1, 5).out == 0  # type: ignore[operator]


class _NoWires:
    """A graph whose terminals have nothing wired to them."""

    def incoming_edges(self, terminal_id: str) -> list[object]:
        return []


def _shown(index_array, graph) -> list[str]:
    return [
        instance_default(graph, index_array, t, "Index Array").literal  # type: ignore[arg-type]
        for t in index_array.terminals
        if t.direction == "input" and t.index > 0
    ]


def _index_node(rank: int, indices: int, output):
    elements = array("NumInt32", rank)
    terminals = [(0, "input", elements), (1, "output", output)]
    terminals += [(2 + i, "input", I32) for i in range(indices)]
    return node("ia", None, terminals, node_type="aIndx")


def test_with_no_index_wired_the_first_dimension_is_row_zero() -> None:
    """The output is one dimension lower, so one dimension is indexed: the first,
    at 0. The second is taken whole. lvnet and the code say the same."""
    index_array = _index_node(2, 2, array("NumInt32"))
    assert _shown(index_array, _NoWires()) == ["0", "(entire row or column)"]
    generated = _index_2d(row=False, column=False, output=array("NumInt32"))
    assert generated.function(GRID).out == [1, 2]  # type: ignore[operator]


def test_a_3d_array_with_two_index_terminals_follows_the_same_plan() -> None:
    index_array = _index_node(3, 2, array("NumInt32", 2))
    assert _shown(index_array, _NoWires()) == ["0", "(entire row or column)"]
    cube = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]
    generated = generate(
        {"a": array("NumInt32", 3)},
        {"out": array("NumInt32", 2)},
        [index_array],
        [("fp:a", "ia:0"), ("ia:1", "fp:out")],
    )
    assert generated.function(cube).out == [[1, 2], [3, 4]]  # type: ignore[operator]


def test_an_output_that_does_not_fit_the_indexes_is_a_gap() -> None:
    """Both indexes wired on a 2-D array index two dimensions, which an output that
    is still one-dimensional contradicts."""
    index_array = _index_node(2, 2, array("NumInt32"))
    gaps: list[Gap] = []
    generate(
        {"a": array("NumInt32", 2), "i": I32, "j": I32},
        {"out": array("NumInt32")},
        [index_array],
        [("fp:a", "ia:0"), ("fp:i", "ia:2"), ("fp:j", "ia:3"), ("ia:1", "fp:out")],
        gaps=gaps,
    )
    assert len(gaps) == 1


def test_an_index_outside_its_dimension_gives_an_empty_array_on_either_dimension() -> (
    None
):
    row = _index_2d(row=True, column=False, output=array("NumInt32"))
    column = _index_2d(row=False, column=True, output=array("NumInt32"))
    assert row.function(GRID, 5).out == []  # type: ignore[operator]
    assert column.function(GRID, 5).out == []  # type: ignore[operator]
    element = _index_2d(row=True, column=True, output=I32)
    assert element.function(GRID, 5, 0).out == 0  # type: ignore[operator]


def test_a_saved_disabled_index_takes_its_whole_dimension() -> None:
    """The row index is saved as disabled and the column index is not, so with
    nothing wired the column is position 0 and the rows are taken whole."""
    index_array = _index_node(2, 2, array("NumInt32"))
    row = next(t for t in index_array.terminals if t.index == 2)
    row.indexing_disabled = True
    assert _shown(index_array, _NoWires()) == ["(entire row or column)", "0"]
    generated = generate(
        {"a": array("NumInt32", 2)},
        {"out": array("NumInt32")},
        [index_array],
        [("fp:a", "ia:0"), ("ia:1", "fp:out")],
    )
    assert generated.function(GRID).out == [1, 3]  # type: ignore[operator]


def test_an_index_array_that_is_a_gap_shows_the_bare_default() -> None:
    """A scalar output contradicts two unwired indexes on a 2-D array, so there is
    no plan: lvnet shows the bare default on both, as the code is a gap."""
    index_array = _index_node(2, 2, I32)
    column = next(t for t in index_array.terminals if t.index == 3)
    column.indexing_disabled = True
    assert _shown(index_array, _NoWires()) == [BARE, BARE]
