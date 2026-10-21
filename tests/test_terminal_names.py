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
    _CatalogTerminal("input string", 0, "in"),
    _CatalogTerminal("result string", 4, "out"),
    _CatalogTerminal("replace string", 7, "in"),
    _CatalogTerminal("number of replacements", 8, "out"),
    _CatalogTerminal("error out", 15, "out"),
]


def _names(slots, catalog, *, complete=True):
    return catalog_terminal_names(
        [NodeSlot(i, d) for i, d in slots], catalog, lists_every_terminal=complete
    )


def test_names_follow_index_and_direction() -> None:
    slots = [(0, "in"), (4, "out"), (7, "in"), (8, "out"), (15, "out")]
    assert _names(slots, _GAPPED) == {
        (0, "in"): "input string",
        (4, "out"): "result string",
        (7, "in"): "replace string",
        (8, "out"): "number of replacements",
        (15, "out"): "error out",
    }


def test_node_with_an_index_the_entry_lacks_gets_no_names() -> None:
    # The entry numbers its terminals by position (6 instead of 7): the node's
    # slot 7 is not in the entry, so no name may land on any terminal.
    positional = [
        _CatalogTerminal("input string", 0, "in"),
        _CatalogTerminal("replace string", 6, "in"),
    ]
    assert _names([(0, "in"), (7, "in")], positional) == {}


def test_node_with_the_other_direction_gets_no_names() -> None:
    assert _names([(0, "in"), (4, "in")], _GAPPED) == {}


def test_unknown_index_is_neutral() -> None:
    names = _names([(0, "in"), (-1, "in")], _GAPPED)
    assert names == {(0, "in"): "input string"}


def test_expandable_entry_allows_extra_terminals_without_names() -> None:
    catalog = [
        _CatalogTerminal("appended array", 0, "out"),
        _CatalogTerminal("element", 1, "in", expandable=True),
    ]
    names = _names([(0, "out"), (1, "in"), (2, "in"), (3, "in")], catalog)
    assert names == {(0, "out"): "appended array", (1, "in"): "element"}


def test_same_index_in_both_directions_keeps_each_name() -> None:
    catalog = [
        _CatalogTerminal("data in", 0, "in"),
        _CatalogTerminal("data out", 0, "out"),
    ]
    assert _names([(0, "in"), (0, "out")], catalog) == {
        (0, "in"): "data in",
        (0, "out"): "data out",
    }


def test_vilib_directions_use_input_output_spelling() -> None:
    catalog = [
        _CatalogTerminal("string", 0, "input"),
        _CatalogTerminal("trimmed string", 2, "output"),
    ]
    assert _names([(0, "in"), (2, "out")], catalog, complete=False) == {
        (0, "in"): "string",
        (2, "out"): "trimmed string",
    }


def test_partial_entry_names_what_it_lists_and_skips_direction_conflicts() -> None:
    catalog = [
        _CatalogTerminal("string", 0, "input"),
        _CatalogTerminal("trimmed string", 2, "output"),
    ]
    # Slot 1 is not listed (fine for a partial entry); slot 2 arrives as an
    # input, the other direction, so it stays unnamed.
    names = _names([(0, "in"), (1, "in"), (2, "in")], catalog, complete=False)
    assert names == {(0, "in"): "string"}


def test_entry_without_a_direction_fits_either() -> None:
    catalog = [_CatalogTerminal("value", 0, None)]
    assert _names([(0, "out")], catalog, complete=False) == {(0, "out"): "value"}


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
    assert by_index[2] == ("in", "ignore case?")
    assert by_index[7] == ("in", "replace string")
    assert by_index[8] == ("out", "number of replacements")
    assert by_index[15] == ("out", "error out")


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
    slots = [(0, "in"), (1, "out"), (2, "in"), (3, "in"), (4, "in"), (5, "in")]
    names = _template_names("aInsert", slots, {0: 2})
    assert names == {
        (0, "in"): "array",
        (1, "out"): "output array",
        (2, "in"): "index 0",
        (3, "in"): "index 1",
        (4, "in"): "new element/subarray (set 1)",
        (5, "in"): "new element/subarray (set 2)",
    }


def test_insert_into_array_one_dimension_keeps_the_plain_names() -> None:
    slots = [(0, "in"), (1, "out"), (2, "in"), (3, "in")]
    names = _template_names("aInsert", slots, {0: 1})
    assert names[(2, "in")] == "index"
    assert names[(3, "in")] == "new element/subarray"


def test_reshape_and_initialize_follow_the_output_array_rank() -> None:
    slots = [(0, "in"), (1, "out"), (2, "in"), (3, "in")]
    reshape = _template_names("aReshape", slots, {1: 2})
    assert reshape[(2, "in")] == "dimension size 0"
    assert reshape[(3, "in")] == "dimension size 1"
    init = _template_names("aInit", slots, {1: 2})
    assert init[(3, "in")] == "dimension size 1"


def test_delete_from_array_names_an_index_per_dimension() -> None:
    slots = [(0, "out"), (1, "in"), (2, "in"), (3, "out"), (4, "in"), (5, "in")]
    names = _template_names("aDelete", slots, {1: 2})
    assert names[(4, "in")] == "index 0"
    assert names[(5, "in")] == "index 1"
    assert names[(2, "in")] == "length"


def test_replace_array_subset_names_an_index_per_dimension() -> None:
    slots = [(0, "in"), (1, "out"), (2, "in"), (3, "in"), (4, "in")]
    names = _template_names("aReplace", slots, {0: 2})
    assert names[(2, "in")] == "new element/subarray"
    assert names[(3, "in")] == "index 0"
    assert names[(4, "in")] == "index 1"


def test_replace_array_subset_names_every_replacement_set() -> None:
    # GIF.Write.Pack.Data__ogtk.vi: a 1-D array, three element/index pairs.
    slots = [(0, "in"), (1, "out")] + [(i, "in") for i in range(2, 8)]
    names = _template_names("aReplace", slots, {0: 1})
    assert [names[(i, "in")] for i in range(2, 8)] == [
        "new element/subarray (set 1)",
        "index (set 1)",
        "new element/subarray (set 2)",
        "index (set 2)",
        "new element/subarray (set 3)",
        "index (set 3)",
    ]


def test_index_array_names_every_element_set() -> None:
    # Create Mask By Alpha - Advanced -: a 1-D array, four element/index pairs.
    slots = [(0, "in")]
    for k in range(4):
        slots += [(1 + 2 * k, "out"), (2 + 2 * k, "in")]
    names = _template_names("aIndx", slots, {0: 1})
    assert names[(1, "out")] == "element (set 1)"
    assert names[(8, "in")] == "index (set 4)"


def test_index_array_of_a_2d_array_has_one_set_of_two_indices() -> None:
    slots = [(0, "in"), (1, "out"), (2, "in"), (3, "in")]
    names = _template_names("aIndx", slots, {0: 2})
    assert names == {
        (0, "in"): "array",
        (1, "out"): "element",
        (2, "in"): "index 0",
        (3, "in"): "index 1",
    }


def test_array_subset_interleaves_index_and_length_per_dimension() -> None:
    # GetOffsetRWIcon.vi: a 2-D array, indices at 2 and 4, lengths at 3 and 5.
    slots = [(0, "in"), (1, "out")] + [(i, "in") for i in range(2, 6)]
    names = _template_names("subset", slots, {0: 2})
    assert [names[(i, "in")] for i in range(2, 6)] == [
        "index 0",
        "length 0",
        "index 1",
        "length 1",
    ]
    one = _template_names(
        "subset", [(0, "in"), (1, "out"), (2, "in"), (3, "in")], {0: 1}
    )
    assert one[(2, "in")] == "index" and one[(3, "in")] == "length"


def test_a_terminal_count_that_makes_no_whole_set_withholds_names() -> None:
    # Three inputs after the array cannot be whole element/index pairs.
    slots = [(0, "in"), (1, "out"), (2, "in"), (3, "in"), (4, "in")]
    assert _template_names("aReplace", slots, {0: 1}) == {}


def test_unknown_array_rank_withholds_names_from_a_resized_node() -> None:
    # Without the array's rank the second index input cannot be placed.
    slots = [(0, "out"), (1, "in"), (2, "in"), (3, "out"), (4, "in"), (5, "in")]
    assert _template_names("aDelete", slots, {}) == {}


def test_merge_errors_allows_more_error_inputs() -> None:
    entry = get_resolver().resolve(prim_id=2147)
    assert entry is not None
    slots = [
        NodeSlot(0, "out"),
        NodeSlot(1, "in"),
        NodeSlot(2, "in"),
        NodeSlot(3, "in"),
    ]
    names = catalog_terminal_names(slots, entry.terminals, lists_every_terminal=True)
    assert names[(0, "out")] == "error out"
    assert (3, "in") not in names


def test_visa_find_resource_uses_the_slots_the_vi_stores() -> None:
    # A VI saved by LabVIEW 2025: parmIndex 2 is a U32 (return count), 3 a
    # string array (find list), 8 error in, 10 search mode, 11 expression.
    entry = get_resolver().resolve(prim_id=1923)
    assert entry is not None
    by_index = {t.index: (t.direction, t.name, t.type) for t in entry.terminals}
    assert sorted(by_index) == [0, 2, 3, 8, 10, 11]
    assert by_index[2][0::2] == ("out", "NumUInt32")
    assert by_index[3][0::2] == ("out", "Array")
    assert by_index[3][1] == "find list"
    names = catalog_terminal_names(
        [
            NodeSlot(0, "out"),
            NodeSlot(2, "out"),
            NodeSlot(3, "out"),
            NodeSlot(8, "in"),
            NodeSlot(10, "in"),
            NodeSlot(11, "in"),
        ],
        entry.terminals,
        lists_every_terminal=True,
    )
    assert names[(2, "out")] == "return count"
    assert names[(3, "out")] == "find list"
