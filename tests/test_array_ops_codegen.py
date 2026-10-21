"""Tests for array-op code generation (aInit, aReplace, aInsert, aReshape).

Mirrors the pattern in tests/test_compound_codegen.py: build a
``PrimitiveNode`` + ``CodeGenContext`` by hand, generate the fragment, then
COMPILE AND EXECUTE the resulting statements and assert on the real
output values (not just that the code parses).
"""

from __future__ import annotations

import ast
import copy

import pytest

from lvkit.codegen.nodes import compound
from lvkit.codegen.nodes.base import CodeGenError
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.runtime import lv as _lv
from tests.helpers import make_ctx

ARRAY_TYPE = LVType(kind=LVTypeKind.ARRAY)
CLUSTER_TYPE = LVType(kind=LVTypeKind.CLUSTER)


def _compile_and_run(statements: list, local_vars: dict) -> dict:
    """Compile statements and execute, returning resulting locals.

    Provides ``copy`` in globals since real generated modules hoist
    ``fragment.imports`` ("import copy") to the module header -- here we
    exec just the fragment's statements in isolation.
    """
    module = ast.Module(body=statements, type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, "<test>", "exec")
    exec(code, {"copy": copy, "_lv": _lv}, local_vars)
    return local_vars


# ── Initialize Array (aInit) ────────────────────────────────────────


def _init_op(*dim_ids: str) -> PrimitiveNode:
    terminals = [
        Terminal(id="elem", index=0, direction="input"),
        Terminal(id="out", index=1, direction="output"),
    ]
    terminals += [
        Terminal(id=did, index=2 + i, direction="input")
        for i, did in enumerate(dim_ids)
    ]
    return PrimitiveNode(
        id="init1",
        vi_path="test.vi",
        name="Initialize Array",
        node_type="aInit",
        terminals=terminals,
    )


class TestArrayInit1D:
    """1-D Initialize Array: element repeated dimension-size times."""

    def test_generates_and_executes(self):
        ctx = make_ctx("elem", "dim0", "out")
        ctx.bind("elem", "fill_value")
        ctx.bind("dim0", "n")

        op = _init_op("dim0")
        fragment = compound.generate_array_init(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"fill_value": 7, "n": 4},
        )
        output_var = fragment.bindings["out"]
        assert result[output_var] == [7, 7, 7, 7]

    def test_zero_size_produces_empty_array(self):
        ctx = make_ctx("elem", "dim0", "out")
        ctx.bind("elem", "fill_value")
        ctx.bind("dim0", "n")

        op = _init_op("dim0")
        fragment = compound.generate_array_init(op, ctx)

        result = _compile_and_run(fragment.statements, {"fill_value": 1, "n": 0})
        assert result[fragment.bindings["out"]] == []

    def test_scalar_slots_are_independent_but_equal_value(self):
        """Mutating one immutable scalar slot doesn't affect others (trivially,
        since ints are immutable) -- included as a baseline before the 2-D
        aliasing test below.
        """
        ctx = make_ctx("elem", "dim0", "out")
        ctx.bind("elem", "fill_value")
        ctx.bind("dim0", "n")

        op = _init_op("dim0")
        fragment = compound.generate_array_init(op, ctx)

        result = _compile_and_run(fragment.statements, {"fill_value": "x", "n": 3})
        arr = result[fragment.bindings["out"]]
        assert arr == ["x", "x", "x"]


class TestArrayInit2D:
    """2-D Initialize Array: real usage found in
    determineClassHierarchy__ogtk.vi (OpenG sample) -- element + TWO
    dimension-size inputs.
    """

    def test_generates_correct_shape(self):
        ctx = make_ctx("elem", "dim0", "dim1", "out")
        ctx.bind("elem", "fill_value")
        ctx.bind("dim0", "rows")
        ctx.bind("dim1", "cols")

        op = _init_op("dim0", "dim1")
        fragment = compound.generate_array_init(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"fill_value": 0, "rows": 2, "cols": 3},
        )
        arr = result[fragment.bindings["out"]]
        assert arr == [[0, 0, 0], [0, 0, 0]]

    def test_rows_are_independent_objects_not_aliased(self):
        """`[[x] * d1] * d0` would alias every row to the same list object.
        Mutating one row must NOT affect the others.
        """
        ctx = make_ctx("elem", "dim0", "dim1", "out")
        ctx.bind("elem", "fill_value")
        ctx.bind("dim0", "rows")
        ctx.bind("dim1", "cols")

        op = _init_op("dim0", "dim1")
        fragment = compound.generate_array_init(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"fill_value": 0, "rows": 2, "cols": 3},
        )
        arr = result[fragment.bindings["out"]]
        arr[0][0] = 99
        assert arr == [[99, 0, 0], [0, 0, 0]], f"row aliasing bug: {arr}"
        assert arr[0] is not arr[1]


class TestArrayInitMutableElement:
    """A cluster/array-typed element must be deep-copied per slot."""

    def test_mutable_element_slots_are_independent(self):
        ctx = make_ctx("elem", "dim0", "out")
        ctx.bind("elem", "fill_value")
        ctx.bind("dim0", "n")

        terminals = [
            Terminal(id="elem", index=0, direction="input", lv_type=CLUSTER_TYPE),
            Terminal(id="out", index=1, direction="output"),
            Terminal(id="dim0", index=2, direction="input"),
        ]
        op = PrimitiveNode(
            id="init1",
            vi_path="test.vi",
            name="Initialize Array",
            node_type="aInit",
            terminals=terminals,
        )
        fragment = compound.generate_array_init(op, ctx)
        assert "import copy" in fragment.imports

        result = _compile_and_run(
            fragment.statements,
            {"fill_value": {"a": 1}, "n": 3},
        )
        arr = result[fragment.bindings["out"]]
        arr[0]["a"] = 99
        assert arr == [{"a": 99}, {"a": 1}, {"a": 1}], f"element aliasing bug: {arr}"


# ── Replace Array Subset (aReplace) ─────────────────────────────────


def _replace_op(new_elem_type: LVType | None = None) -> PrimitiveNode:
    return PrimitiveNode(
        id="rep1",
        vi_path="test.vi",
        name="Replace Array Subset",
        node_type="aReplace",
        terminals=[
            Terminal(id="arr", index=0, direction="input", lv_type=ARRAY_TYPE),
            Terminal(id="out", index=1, direction="output"),
            Terminal(
                id="newel",
                index=2,
                direction="input",
                lv_type=new_elem_type,
            ),
            Terminal(id="idx", index=3, direction="input"),
        ],
    )


class TestArrayReplaceScalar:
    """Real samples (DCAF Replace Line.vi) only exercise this
    single-element-replace form (new-element terminal typed
    String/Cluster, not Array).
    """

    def test_replaces_element_in_bounds(self):
        ctx = make_ctx("arr", "newel", "idx", "out")
        ctx.bind("arr", "data")
        ctx.bind("newel", "val")
        ctx.bind("idx", "i")

        op = _replace_op()
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4], "val": 99, "i": 1},
        )
        assert result[fragment.bindings["out"]] == [1, 99, 3, 4]

    def test_replaces_first_and_last_element(self):
        ctx = make_ctx("arr", "newel", "idx", "out")
        ctx.bind("arr", "data")
        ctx.bind("newel", "val")
        ctx.bind("idx", "i")

        op = _replace_op()
        fragment = compound.generate_array_replace(op, ctx)

        first = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3], "val": 9, "i": 0},
        )
        assert first[fragment.bindings["out"]] == [9, 2, 3]

        last = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3], "val": 9, "i": 2},
        )
        assert last[fragment.bindings["out"]] == [1, 2, 9]

    def test_output_array_length_unchanged(self):
        ctx = make_ctx("arr", "newel", "idx", "out")
        ctx.bind("arr", "data")
        ctx.bind("newel", "val")
        ctx.bind("idx", "i")

        op = _replace_op()
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4, 5], "val": 0, "i": 2},
        )
        assert len(result[fragment.bindings["out"]]) == 5

    def test_out_of_bounds_index_is_a_noop(self):
        """NI docs: Replace Array Subset never grows the array -- an index
        at/past the end leaves the array unchanged.
        """
        ctx = make_ctx("arr", "newel", "idx", "out")
        ctx.bind("arr", "data")
        ctx.bind("newel", "val")
        ctx.bind("idx", "i")

        op = _replace_op()
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4], "val": 99, "i": 10},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3, 4]

        result_at_len = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4], "val": 99, "i": 4},
        )
        assert result_at_len[fragment.bindings["out"]] == [1, 2, 3, 4]


class TestArrayReplaceSubset:
    """Array-typed "new element" input -- not observed in local samples,
    but a real LabVIEW form (Replace Array Subset accepts a subarray).
    Verified against NI documentation: replacement is clipped to the
    target array's existing bounds, never grows it.
    """

    def test_replaces_contiguous_subset_in_bounds(self):
        ctx = make_ctx("arr", "newel", "idx", "out")
        ctx.bind("arr", "data")
        ctx.bind("newel", "sub")
        ctx.bind("idx", "i")

        op = _replace_op(new_elem_type=ARRAY_TYPE)
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4, 5], "sub": [8, 9], "i": 1},
        )
        assert result[fragment.bindings["out"]] == [1, 8, 9, 4, 5]

    def test_subset_clipped_to_fit_at_end(self):
        """A subset that would run past the end is clipped -- the array
        does not grow.
        """
        ctx = make_ctx("arr", "newel", "idx", "out")
        ctx.bind("arr", "data")
        ctx.bind("newel", "sub")
        ctx.bind("idx", "i")

        op = _replace_op(new_elem_type=ARRAY_TYPE)
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4, 5], "sub": [8, 9, 10, 11], "i": 3},
        )
        out = result[fragment.bindings["out"]]
        assert out == [1, 2, 3, 8, 9]
        assert len(out) == 5

    def test_empty_subset_leaves_array_unchanged(self):
        ctx = make_ctx("arr", "newel", "idx", "out")
        ctx.bind("arr", "data")
        ctx.bind("newel", "sub")
        ctx.bind("idx", "i")

        op = _replace_op(new_elem_type=ARRAY_TYPE)
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3], "sub": [], "i": 1},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3]


def _replace_op_nd(rank: int, sets: int, element_types=None) -> PrimitiveNode:
    """Replace Array Subset on a ``rank``-D array resized to ``sets``
    replacements: each set is a new element followed by one index per
    dimension."""
    array_type = LVType(kind=LVTypeKind.ARRAY, dimensions=rank)
    terminals = [
        Terminal(id="arr", index=0, direction="input", lv_type=array_type),
        Terminal(id="out", index=1, direction="output"),
    ]
    index = 2
    for k in range(sets):
        element_type = element_types[k] if element_types else None
        terminals.append(
            Terminal(id=f"el{k}", index=index, direction="input", lv_type=element_type)
        )
        index += 1
        for d in range(rank):
            terminals.append(Terminal(id=f"ix{k}_{d}", index=index, direction="input"))
            index += 1
    return PrimitiveNode(
        id="rep2",
        vi_path="test.vi",
        name="Replace Array Subset",
        node_type="aReplace",
        terminals=terminals,
    )


class TestArrayReplaceResized:
    """Resized nodes: several element/index sets (GIF.Write.Pack.Data__ogtk.vi
    has three on a 1-D array) and a 2-D array with an index per dimension."""

    def test_pairs_apply_in_order_to_a_1d_array(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "el1", "ix1_0", "out")
        for tid, var in (
            ("arr", "data"),
            ("el0", "a"),
            ("ix0_0", "i"),
            ("el1", "b"),
            ("ix1_0", "j"),
        ):
            ctx.bind(tid, var)

        op = _replace_op_nd(1, 2)
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [0, 0, 0, 0], "a": 7, "i": 0, "b": 9, "j": 2},
        )
        assert result[fragment.bindings["out"]] == [7, 0, 9, 0]

    def test_a_later_pair_sees_the_earlier_replacement(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "el1", "ix1_0", "out")
        for tid, var in (
            ("arr", "data"),
            ("el0", "a"),
            ("ix0_0", "i"),
            ("el1", "b"),
            ("ix1_0", "i"),
        ):
            ctx.bind(tid, var)

        op = _replace_op_nd(1, 2)
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements, {"data": [0, 0], "a": 7, "i": 1, "b": 9}
        )
        assert result[fragment.bindings["out"]] == [0, 9]

    def test_2d_element_replaces_at_row_and_column(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "out")
        for tid, var in (("arr", "grid"), ("el0", "x"), ("ix0_0", "r"), ("ix0_1", "c")):
            ctx.bind(tid, var)

        op = _replace_op_nd(2, 1)
        fragment = compound.generate_array_replace(op, ctx)

        grid = [[1, 2], [3, 4]]
        result = _compile_and_run(
            fragment.statements, {"grid": grid, "x": 9, "r": 1, "c": 0}
        )
        assert result[fragment.bindings["out"]] == [[1, 2], [9, 4]]
        assert grid == [[1, 2], [3, 4]]  # the input array is not modified

    def test_2d_index_out_of_range_has_no_effect(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "out")
        for tid, var in (("arr", "grid"), ("el0", "x"), ("ix0_0", "r"), ("ix0_1", "c")):
            ctx.bind(tid, var)

        op = _replace_op_nd(2, 1)
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements, {"grid": [[1, 2]], "x": 9, "r": 0, "c": 5}
        )
        assert result[fragment.bindings["out"]] == [[1, 2]]

    def test_unwired_index_starts_at_element_zero(self):
        # NI: "starting with element 0" when the index is not wired.
        ctx = make_ctx("arr", "el0", "ix0_0", "out")
        ctx.bind("arr", "data")
        ctx.bind("el0", "sub")
        op = _replace_op_nd(1, 1, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements, {"data": [1, 2, 3], "sub": [8, 9]}
        )
        assert result[fragment.bindings["out"]] == [8, 9, 3]

    def test_subarray_continues_from_the_index_and_is_cropped(self):
        # NI: index 1, three elements, two new ones -> elements 1 and 2.
        ctx = make_ctx("arr", "el0", "ix0_0", "out")
        for tid, var in (("arr", "data"), ("el0", "sub"), ("ix0_0", "i")):
            ctx.bind(tid, var)
        op = _replace_op_nd(1, 1, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements, {"data": [1, 2, 3], "sub": [8, 9], "i": 1}
        )
        assert result[fragment.bindings["out"]] == [1, 8, 9]
        result = _compile_and_run(
            fragment.statements, {"data": [1, 2, 3], "sub": [8, 9], "i": 2}
        )
        assert result[fragment.bindings["out"]] == [1, 2, 8]

    def test_negative_index_has_no_effect(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "out")
        for tid, var in (("arr", "data"), ("el0", "x"), ("ix0_0", "i")):
            ctx.bind(tid, var)
        fragment = compound.generate_array_replace(_replace_op_nd(1, 1), ctx)

        result = _compile_and_run(
            fragment.statements, {"data": [1, 2, 3], "x": 9, "i": -1}
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3]

    def test_2d_unwired_second_index_continues_at_column_zero(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "out")
        for tid, var in (("arr", "grid"), ("el0", "x"), ("ix0_0", "r")):
            ctx.bind(tid, var)
        fragment = compound.generate_array_replace(_replace_op_nd(2, 1), ctx)

        result = _compile_and_run(
            fragment.statements, {"grid": [[1, 2], [3, 4]], "x": 9, "r": 1}
        )
        assert result[fragment.bindings["out"]] == [[1, 2], [9, 4]]

    def test_2d_row_into_the_row_index_is_cropped_to_fit(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "out")
        for tid, var in (
            ("arr", "grid"),
            ("el0", "row"),
            ("ix0_0", "r"),
            ("ix0_1", "c"),
        ):
            ctx.bind(tid, var)
        op = _replace_op_nd(2, 1, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        fragment = compound.generate_array_replace(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"grid": [[1, 2, 3], [4, 5, 6]], "row": [8, 9, 7], "r": 1, "c": 1},
        )
        assert result[fragment.bindings["out"]] == [[1, 2, 3], [4, 8, 9]]

    def test_3d_element_replaces_at_three_indices(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "ix0_2", "out")
        for tid, var in (
            ("arr", "cube"),
            ("el0", "x"),
            ("ix0_0", "a"),
            ("ix0_1", "b"),
            ("ix0_2", "c"),
        ):
            ctx.bind(tid, var)
        fragment = compound.generate_array_replace(_replace_op_nd(3, 1), ctx)

        cube = [[[0, 0], [0, 0]], [[0, 0], [0, 0]]]
        result = _compile_and_run(
            fragment.statements, {"cube": cube, "x": 7, "a": 1, "b": 0, "c": 1}
        )
        assert result[fragment.bindings["out"]] == [
            [[0, 0], [0, 0]],
            [[0, 7], [0, 0]],
        ]
        assert cube == [[[0, 0], [0, 0]], [[0, 0], [0, 0]]]

    def test_3d_plane_replaces_a_page_and_is_cropped(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "ix0_2", "out")
        for tid, var in (("arr", "cube"), ("el0", "page"), ("ix0_0", "a")):
            ctx.bind(tid, var)
        op = _replace_op_nd(3, 1, [LVType(kind=LVTypeKind.ARRAY, dimensions=2)])
        fragment = compound.generate_array_replace(op, ctx)

        cube = [[[0, 0], [0, 0]], [[0, 0], [0, 0]]]
        result = _compile_and_run(
            fragment.statements,
            {"cube": cube, "page": [[1, 2, 3], [4, 5, 6], [7, 8, 9]], "a": 1},
        )
        assert result[fragment.bindings["out"]] == [
            [[0, 0], [0, 0]],
            [[1, 2], [4, 5]],
        ]

    def test_3d_out_of_range_index_has_no_effect(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "ix0_2", "out")
        for tid, var in (
            ("arr", "cube"),
            ("el0", "x"),
            ("ix0_0", "a"),
            ("ix0_1", "b"),
            ("ix0_2", "c"),
        ):
            ctx.bind(tid, var)
        fragment = compound.generate_array_replace(_replace_op_nd(3, 1), ctx)

        result = _compile_and_run(
            fragment.statements,
            {"cube": [[[0, 0]]], "x": 7, "a": 0, "b": 0, "c": 2},
        )
        assert result[fragment.bindings["out"]] == [[[0, 0]]]

    def test_subarray_without_its_leading_index_is_a_diagnostic(self):
        ctx = make_ctx("arr", "el0", "ix0_0", "ix0_1", "out")
        ctx.bind("arr", "grid")
        ctx.bind("el0", "row")
        ctx.bind("ix0_1", "c")
        op = _replace_op_nd(2, 1, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        with pytest.raises(CodeGenError, match="leading 1 index"):
            compound.generate_array_replace(op, ctx)


# ── Insert Into Array (aInsert) ─────────────────────────────────────
#
# Real terminal layout verified against Reserve cDAQ.vi (DCAF-DAQModule):
# 0 = array in, 1 = output array, 2 = index (numeric), 3 = new
# element/subarray (matches the array's own element type) -- the LAST
# input terminal is always the element, confirmed by its resolved LVType
# matching the array's element type (String, in the real sample).


def _insert_op(elem_type: LVType | None = None) -> PrimitiveNode:
    return PrimitiveNode(
        id="ins1",
        vi_path="test.vi",
        name="Insert Into Array",
        node_type="aInsert",
        terminals=[
            Terminal(id="arr", index=0, direction="input", lv_type=ARRAY_TYPE),
            Terminal(id="out", index=1, direction="output"),
            Terminal(id="idx", index=2, direction="input"),
            Terminal(id="elem", index=3, direction="input", lv_type=elem_type),
        ],
    )


def _insert_op_nd(rank: int, element_types: list[LVType | None]) -> PrimitiveNode:
    """Insert Into Array on a ``rank``-D array: one index input per dimension,
    then one terminal per new element/subarray."""
    array_type = LVType(kind=LVTypeKind.ARRAY, dimensions=rank)
    terminals = [
        Terminal(id="arr", index=0, direction="input", lv_type=array_type),
        Terminal(id="out", index=1, direction="output"),
    ]
    terminals += [
        Terminal(id=f"idx{k}", index=2 + k, direction="input") for k in range(rank)
    ]
    terminals += [
        Terminal(id=f"elem{k}", index=2 + rank + k, direction="input", lv_type=t)
        for k, t in enumerate(element_types)
    ]
    return PrimitiveNode(
        id="ins2",
        vi_path="test.vi",
        name="Insert Into Array",
        node_type="aInsert",
        terminals=terminals,
    )


class TestArrayInsertScalar:
    """Real sample (Reserve cDAQ.vi) only exercises the scalar-element
    insert form (new-element terminal typed String, not Array)."""

    def test_inserts_scalar_at_index_and_grows_array(self):
        ctx = make_ctx("arr", "idx", "elem", "out")
        ctx.bind("arr", "data")
        ctx.bind("idx", "i")
        ctx.bind("elem", "val")

        op = _insert_op()
        fragment = compound.generate_array_insert(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3], "i": 1, "val": 99},
        )
        out = result[fragment.bindings["out"]]
        assert out == [1, 99, 2, 3]
        assert len(out) == 4

    def test_unwired_index_appends_to_end(self):
        """NI docs: if no index is wired, the new element/subarray is
        appended to the end of the array."""
        ctx = make_ctx("arr", "idx", "elem", "out")
        ctx.bind("arr", "data")
        ctx.bind("elem", "val")
        # "idx" deliberately left unbound -- ctx.resolve(idx) returns None.

        op = _insert_op()
        fragment = compound.generate_array_insert(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3], "val": 99},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3, 99]

    def test_index_beyond_array_length_is_a_true_noop(self):
        """Unlike Replace Array Subset (which clips), Insert Into Array
        does NOT insert anything when the index is beyond the array's
        current length -- the output is the unchanged input array.
        """
        ctx = make_ctx("arr", "idx", "elem", "out")
        ctx.bind("arr", "data")
        ctx.bind("idx", "i")
        ctx.bind("elem", "val")

        op = _insert_op()
        fragment = compound.generate_array_insert(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3], "i": 10, "val": 99},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3]


class TestArrayInsertSubarray:
    """Array-typed "new element" input -- inserting a subarray/row."""

    def test_inserts_subarray_at_index(self):
        ctx = make_ctx("arr", "idx", "elem", "out")
        ctx.bind("arr", "data")
        ctx.bind("idx", "i")
        ctx.bind("elem", "sub")

        op = _insert_op(elem_type=ARRAY_TYPE)
        fragment = compound.generate_array_insert(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3], "i": 1, "sub": [8, 9]},
        )
        out = result[fragment.bindings["out"]]
        assert out == [1, 8, 9, 2, 3]
        assert len(out) == 5


class TestArrayInsertResized:
    """Resized nodes: index inputs follow the array's dimension count and the
    remaining inputs are new elements/subarrays (Get Metadata.vi has two on a
    1-D array; Display Client Creation Status.vi is 2-D)."""

    def test_two_new_elements_on_a_1d_array_insert_in_order(self):
        ctx = make_ctx("arr", "idx0", "elem0", "elem1", "out")
        ctx.bind("arr", "data")
        ctx.bind("idx0", "i")
        ctx.bind("elem0", "a")
        ctx.bind("elem1", "b")

        op = _insert_op_nd(1, [None, None])
        fragment = compound.generate_array_insert(op, ctx)

        result = _compile_and_run(
            fragment.statements, {"data": [1, 2, 3], "i": 1, "a": 8, "b": 9}
        )
        assert result[fragment.bindings["out"]] == [1, 8, 9, 2, 3]

    def test_2d_rows_inserted_along_the_first_dimension(self):
        ctx = make_ctx("arr", "idx0", "idx1", "elem0", "out")
        ctx.bind("arr", "grid")
        ctx.bind("idx0", "i")
        ctx.bind("elem0", "row")

        op = _insert_op_nd(2, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        fragment = compound.generate_array_insert(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"grid": [[1, 2], [3, 4]], "i": 1, "row": [8, 9]},
        )
        assert result[fragment.bindings["out"]] == [[1, 2], [8, 9], [3, 4]]

    def test_2d_with_no_index_wired_appends_a_row(self):
        ctx = make_ctx("arr", "idx0", "idx1", "elem0", "out")
        ctx.bind("arr", "grid")
        ctx.bind("elem0", "row")

        op = _insert_op_nd(2, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        fragment = compound.generate_array_insert(op, ctx)

        result = _compile_and_run(
            fragment.statements, {"grid": [[1, 2]], "row": [8, 9]}
        )
        assert result[fragment.bindings["out"]] == [[1, 2], [8, 9]]

    def test_2d_insert_along_the_second_dimension_is_a_diagnostic(self):
        ctx = make_ctx("arr", "idx0", "idx1", "elem0", "out")
        ctx.bind("arr", "grid")
        ctx.bind("idx1", "j")
        ctx.bind("elem0", "col")

        op = _insert_op_nd(2, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        with pytest.raises(CodeGenError, match="dimension 1"):
            compound.generate_array_insert(op, ctx)

    def test_two_wired_indexes_is_a_diagnostic(self):
        ctx = make_ctx("arr", "idx0", "idx1", "elem0", "out")
        for tid in ("arr", "idx0", "idx1", "elem0"):
            ctx.bind(tid, tid)

        op = _insert_op_nd(2, [LVType(kind=LVTypeKind.ARRAY, dimensions=1)])
        with pytest.raises(CodeGenError, match="more than one index"):
            compound.generate_array_insert(op, ctx)


# ── Reshape Array (aReshape) ────────────────────────────────────────
#
# Verified against a real 2-D-to-1-D reshape in OpenG's "1D Array of
# VArrays to MultiD Array.vi": a 2-D source array, ONE dimension-size
# terminal, 1-D output -- dimension-terminal count tracks the requested
# OUTPUT rank, not the source array's own rank.

INT_TYPE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _reshape_op(source_ndim: int, *dim_ids: str) -> PrimitiveNode:
    array_type = LVType(
        kind=LVTypeKind.ARRAY, element_type=INT_TYPE, dimensions=source_ndim
    )
    terminals = [
        Terminal(id="arr", index=0, direction="input", lv_type=array_type),
        Terminal(id="out", index=1, direction="output"),
    ]
    terminals += [
        Terminal(id=did, index=2 + i, direction="input")
        for i, did in enumerate(dim_ids)
    ]
    return PrimitiveNode(
        id="rshp1",
        vi_path="test.vi",
        name="Reshape Array",
        node_type="aReshape",
        terminals=terminals,
    )


class TestArrayReshape1Dto1D:
    """1-D source reshaped to a (possibly different-length) 1-D target --
    truncating or zero-padding to fit."""

    def test_truncates_when_source_has_too_many_elements(self):
        ctx = make_ctx("arr", "dim0", "out")
        ctx.bind("arr", "data")
        ctx.bind("dim0", "n")

        op = _reshape_op(1, "dim0")
        fragment = compound.generate_array_reshape(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4, 5], "n": 3},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3]

    def test_pads_with_zero_default_when_source_has_too_few_elements(self):
        ctx = make_ctx("arr", "dim0", "out")
        ctx.bind("arr", "data")
        ctx.bind("dim0", "n")

        op = _reshape_op(1, "dim0")
        fragment = compound.generate_array_reshape(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2], "n": 5},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 0, 0, 0]


class TestArrayReshape1Dto2D:
    """1-D source reshaped (row-major) into a 2-D target shape."""

    def test_reshapes_flat_array_into_rows(self):
        ctx = make_ctx("arr", "dim0", "dim1", "out")
        ctx.bind("arr", "data")
        ctx.bind("dim0", "rows")
        ctx.bind("dim1", "cols")

        op = _reshape_op(1, "dim0", "dim1")
        fragment = compound.generate_array_reshape(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4, 5, 6], "rows": 2, "cols": 3},
        )
        assert result[fragment.bindings["out"]] == [[1, 2, 3], [4, 5, 6]]

    def test_pads_last_row_when_source_has_too_few_elements(self):
        ctx = make_ctx("arr", "dim0", "dim1", "out")
        ctx.bind("arr", "data")
        ctx.bind("dim0", "rows")
        ctx.bind("dim1", "cols")

        op = _reshape_op(1, "dim0", "dim1")
        fragment = compound.generate_array_reshape(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [1, 2, 3, 4], "rows": 2, "cols": 3},
        )
        assert result[fragment.bindings["out"]] == [[1, 2, 3], [4, 0, 0]]


class TestArrayReshape2DSource:
    """2-D source flattened (row-major) before re-chunking -- the real
    shape verified against OpenG's "1D Array of VArrays to MultiD
    Array.vi" (2-D source, 1-D target)."""

    def test_flattens_2d_source_into_1d_target(self):
        ctx = make_ctx("arr", "dim0", "out")
        ctx.bind("arr", "data")
        ctx.bind("dim0", "n")

        op = _reshape_op(2, "dim0")
        fragment = compound.generate_array_reshape(op, ctx)

        result = _compile_and_run(
            fragment.statements,
            {"data": [[1, 2], [3, 4], [5]], "n": 5},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3, 4, 5]
