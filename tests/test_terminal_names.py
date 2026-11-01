"""Catalog terminal names are shown only when the catalog fits the node."""

from __future__ import annotations

from dataclasses import dataclass

from lvkit.graph.terminal_names import NodeSlot, catalog_terminal_names
from lvkit.primitive_resolver import get_resolver


@dataclass
class _CatalogTerminal:
    name: str | None
    index: int | None
    direction: str | None
    expandable: bool = False


# Search and Replace String's real slots: 0-5, 7-11 and 15 (no 6, no 12-14).
_GAPPED = [
    _CatalogTerminal("input string", 0, "input"),
    _CatalogTerminal("result string", 4, "output"),
    _CatalogTerminal("replace string", 7, "input"),
    _CatalogTerminal("number of replacements", 8, "output"),
    _CatalogTerminal("error out", 15, "output"),
]


def _names(slots, catalog, *, complete=True):
    return catalog_terminal_names(
        [NodeSlot(i, d) for i, d in slots], catalog, lists_every_terminal=complete
    )


def test_names_follow_index_and_direction() -> None:
    slots = [(0, "input"), (4, "output"), (7, "input"), (8, "output"), (15, "output")]
    assert _names(slots, _GAPPED) == {
        (0, "input"): "input string",
        (4, "output"): "result string",
        (7, "input"): "replace string",
        (8, "output"): "number of replacements",
        (15, "output"): "error out",
    }


def test_node_with_an_index_the_entry_lacks_gets_no_names() -> None:
    # The entry numbers its terminals by position (6 instead of 7): the node's
    # slot 7 is not in the entry, so no name may land on any terminal.
    positional = [
        _CatalogTerminal("input string", 0, "input"),
        _CatalogTerminal("replace string", 6, "input"),
    ]
    assert _names([(0, "input"), (7, "input")], positional) == {}


def test_node_with_the_other_direction_gets_no_names() -> None:
    assert _names([(0, "input"), (4, "input")], _GAPPED) == {}


def test_unknown_index_is_neutral() -> None:
    names = _names([(0, "input"), (-1, "input")], _GAPPED)
    assert names == {(0, "input"): "input string"}


def test_expandable_entry_allows_extra_terminals_without_names() -> None:
    catalog = [
        _CatalogTerminal("appended array", 0, "output"),
        _CatalogTerminal("element", 1, "input", expandable=True),
    ]
    names = _names([(0, "output"), (1, "input"), (2, "input"), (3, "input")], catalog)
    assert names == {(0, "output"): "appended array", (1, "input"): "element"}


def test_same_index_in_both_directions_keeps_each_name() -> None:
    catalog = [
        _CatalogTerminal("data in", 0, "input"),
        _CatalogTerminal("data out", 0, "output"),
    ]
    assert _names([(0, "input"), (0, "output")], catalog) == {
        (0, "input"): "data in",
        (0, "output"): "data out",
    }


def test_a_partial_entry_names_the_terminals_it_lists() -> None:
    catalog = [
        _CatalogTerminal("string", 0, "input"),
        _CatalogTerminal("trimmed string", 2, "output"),
    ]
    assert _names([(0, "input"), (2, "output")], catalog, complete=False) == {
        (0, "input"): "string",
        (2, "output"): "trimmed string",
    }


def test_partial_entry_names_what_it_lists_and_skips_direction_conflicts() -> None:
    catalog = [
        _CatalogTerminal("string", 0, "input"),
        _CatalogTerminal("trimmed string", 2, "output"),
    ]
    # Slot 1 is not listed (fine for a partial entry); slot 2 arrives as an
    # input, the other direction, so it stays unnamed.
    names = _names([(0, "input"), (1, "input"), (2, "input")], catalog, complete=False)
    assert names == {(0, "input"): "string"}


def test_entry_without_a_direction_fits_either() -> None:
    catalog = [_CatalogTerminal("value", 0, None)]
    assert _names([(0, "output")], catalog, complete=False) == {(0, "output"): "value"}


# ── catalog data ────────────────────────────────────────────────────


def test_insert_into_array_template_puts_index_before_the_new_element() -> None:
    template = get_resolver().resolve_by_node_type("aInsert")
    assert template is not None
    assert [(t.index, t.name) for t in template.terminals] == [
        (0, "array"),
        (1, "output array"),
        (2, "index"),
        (3, "new element/subarray"),
    ]


def test_search_and_replace_string_uses_the_slots_the_vi_stores() -> None:
    entry = get_resolver().resolve(prim_id=3914)
    assert entry is not None
    by_index = {t.index: (t.direction, t.name) for t in entry.terminals}
    assert sorted(by_index) == [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 15]
    assert by_index[2] == ("input", "ignore case?")
    assert by_index[7] == ("input", "replace string")
    assert by_index[8] == ("output", "number of replacements")
    assert by_index[15] == ("output", "error out")


# ── terminals repeated once per dimension ───────────────────────────


def _template_names(node_type: str, slots, ranks: dict[int, int]):
    template = get_resolver().resolve_by_node_type(node_type)
    assert template is not None
    return catalog_terminal_names(
        [NodeSlot(i, d) for i, d in slots],
        template.terminals,
        lists_every_terminal=True,
        rank_of=ranks.get,
    )


def test_insert_into_array_names_one_index_per_dimension() -> None:
    # Display Client Creation Status.vi: a 2-D array, two index inputs, two
    # new subarrays.
    slots = [
        (0, "input"),
        (1, "output"),
        (2, "input"),
        (3, "input"),
        (4, "input"),
        (5, "input"),
    ]
    names = _template_names("aInsert", slots, {0: 2})
    assert names == {
        (0, "input"): "array",
        (1, "output"): "output array",
        (2, "input"): "index 0",
        (3, "input"): "index 1",
        (4, "input"): "new element/subarray (set 1)",
        (5, "input"): "new element/subarray (set 2)",
    }


def test_insert_into_array_one_dimension_keeps_the_plain_names() -> None:
    slots = [(0, "input"), (1, "output"), (2, "input"), (3, "input")]
    names = _template_names("aInsert", slots, {0: 1})
    assert names[(2, "input")] == "index"
    assert names[(3, "input")] == "new element/subarray"


def test_reshape_and_initialize_follow_the_output_array_rank() -> None:
    slots = [(0, "input"), (1, "output"), (2, "input"), (3, "input")]
    reshape = _template_names("aReshape", slots, {1: 2})
    assert reshape[(2, "input")] == "dimension size 0"
    assert reshape[(3, "input")] == "dimension size 1"
    init = _template_names("aInit", slots, {1: 2})
    assert init[(3, "input")] == "dimension size 1"


def test_delete_from_array_names_an_index_per_dimension() -> None:
    slots = [
        (0, "output"),
        (1, "input"),
        (2, "input"),
        (3, "output"),
        (4, "input"),
        (5, "input"),
    ]
    names = _template_names("aDelete", slots, {1: 2})
    assert names[(4, "input")] == "index 0"
    assert names[(5, "input")] == "index 1"
    assert names[(2, "input")] == "length"


def test_replace_array_subset_names_an_index_per_dimension() -> None:
    slots = [(0, "input"), (1, "output"), (2, "input"), (3, "input"), (4, "input")]
    names = _template_names("aReplace", slots, {0: 2})
    assert names[(2, "input")] == "new element/subarray"
    assert names[(3, "input")] == "index 0"
    assert names[(4, "input")] == "index 1"


def test_replace_array_subset_names_every_replacement_set() -> None:
    # GIF.Write.Pack.Data__ogtk.vi: a 1-D array, three element/index pairs.
    slots = [(0, "input"), (1, "output")] + [(i, "input") for i in range(2, 8)]
    names = _template_names("aReplace", slots, {0: 1})
    assert [names[(i, "input")] for i in range(2, 8)] == [
        "new element/subarray (set 1)",
        "index (set 1)",
        "new element/subarray (set 2)",
        "index (set 2)",
        "new element/subarray (set 3)",
        "index (set 3)",
    ]


def test_index_array_names_every_element_set() -> None:
    # Create Mask By Alpha - Advanced -: a 1-D array, four element/index pairs.
    slots = [(0, "input")]
    for k in range(4):
        slots += [(1 + 2 * k, "output"), (2 + 2 * k, "input")]
    names = _template_names("aIndx", slots, {0: 1})
    assert names[(1, "output")] == "element (set 1)"
    assert names[(8, "input")] == "index (set 4)"


def test_index_array_of_a_2d_array_has_one_set_of_two_indices() -> None:
    slots = [(0, "input"), (1, "output"), (2, "input"), (3, "input")]
    names = _template_names("aIndx", slots, {0: 2})
    assert names == {
        (0, "input"): "array",
        (1, "output"): "element",
        (2, "input"): "index 0",
        (3, "input"): "index 1",
    }


def test_array_subset_interleaves_index_and_length_per_dimension() -> None:
    # GetOffsetRWIcon.vi: a 2-D array, indices at 2 and 4, lengths at 3 and 5.
    slots = [(0, "input"), (1, "output")] + [(i, "input") for i in range(2, 6)]
    names = _template_names("subset", slots, {0: 2})
    assert [names[(i, "input")] for i in range(2, 6)] == [
        "index 0",
        "length 0",
        "index 1",
        "length 1",
    ]
    one = _template_names(
        "subset", [(0, "input"), (1, "output"), (2, "input"), (3, "input")], {0: 1}
    )
    assert one[(2, "input")] == "index" and one[(3, "input")] == "length"


def test_a_terminal_count_that_makes_no_whole_set_withholds_names() -> None:
    # Three inputs after the array cannot be whole element/index pairs.
    slots = [(0, "input"), (1, "output"), (2, "input"), (3, "input"), (4, "input")]
    assert _template_names("aReplace", slots, {0: 1}) == {}


def test_unknown_array_rank_withholds_names_from_a_resized_node() -> None:
    # Without the array's rank the second index input cannot be placed.
    slots = [
        (0, "output"),
        (1, "input"),
        (2, "input"),
        (3, "output"),
        (4, "input"),
        (5, "input"),
    ]
    assert _template_names("aDelete", slots, {}) == {}


def test_merge_errors_allows_more_error_inputs() -> None:
    entry = get_resolver().resolve(prim_id=2147)
    assert entry is not None
    slots = [
        NodeSlot(0, "output"),
        NodeSlot(1, "input"),
        NodeSlot(2, "input"),
        NodeSlot(3, "input"),
    ]
    names = catalog_terminal_names(slots, entry.terminals, lists_every_terminal=True)
    assert names[(0, "output")] == "error out"
    assert (3, "input") not in names


def test_visa_find_resource_uses_the_slots_the_vi_stores() -> None:
    # A VI saved by LabVIEW 2025: parmIndex 2 is a U32 (return count), 3 a
    # string array (find list), 8 error in, 10 search mode, 11 expression.
    entry = get_resolver().resolve(prim_id=1923)
    assert entry is not None
    by_index = {t.index: (t.direction, t.name, t.type) for t in entry.terminals}
    assert sorted(by_index) == [0, 2, 3, 8, 10, 11]
    assert by_index[2][0::2] == ("output", "NumUInt32")
    assert by_index[3][0::2] == ("output", "Array")
    assert by_index[3][1] == "find list"
    names = catalog_terminal_names(
        [
            NodeSlot(0, "output"),
            NodeSlot(2, "output"),
            NodeSlot(3, "output"),
            NodeSlot(8, "input"),
            NodeSlot(10, "input"),
            NodeSlot(11, "input"),
        ],
        entry.terminals,
        lists_every_terminal=True,
    )
    assert names[(2, "output")] == "return count"
    assert names[(3, "output")] == "find list"
