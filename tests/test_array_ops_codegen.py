"""Tests for array-op code generation (aInit, aReplace, aInsert, aReshape).

Mirrors the pattern in tests/test_compound_codegen.py: build a
``PrimitiveNode`` + ``CodeGenContext`` by hand, generate the fragment, then
COMPILE AND EXECUTE the resulting statements and assert on the real
output values (not just that the code parses).
"""

from __future__ import annotations

import ast
import copy

from lvkit.codegen.nodes import compound
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind, Terminal
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
    exec(code, {"copy": copy}, local_vars)
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
            Terminal(id="arr", index=0, direction="input"),
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
            Terminal(id="arr", index=0, direction="input"),
            Terminal(id="out", index=1, direction="output"),
            Terminal(id="idx", index=2, direction="input"),
            Terminal(id="elem", index=3, direction="input", lv_type=elem_type),
        ],
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
