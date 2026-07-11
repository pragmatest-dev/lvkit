"""Tests for array-op code generation (aInit, aReplace).

Mirrors the pattern in tests/test_compound_codegen.py: build a
PrimitiveOperation + CodeGenContext by hand, generate the fragment, then
COMPILE AND EXECUTE the resulting statements and assert on the real
output values (not just that the code parses).
"""

from __future__ import annotations

import ast
import copy

from lvkit.codegen.nodes import compound
from lvkit.models import LVType, PrimitiveOperation, Terminal
from tests.helpers import make_ctx

ARRAY_TYPE = LVType(kind="array")
CLUSTER_TYPE = LVType(kind="cluster")


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


def _init_op(*dim_ids: str) -> PrimitiveOperation:
    terminals = [
        Terminal(id="elem", index=0, direction="input"),
        Terminal(id="out", index=1, direction="output"),
    ]
    terminals += [
        Terminal(id=did, index=2 + i, direction="input")
        for i, did in enumerate(dim_ids)
    ]
    return PrimitiveOperation(
        id="init1",
        name="Initialize Array",
        labels=["ArrayInit"],
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
            fragment.statements, {"fill_value": 7, "n": 4},
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
            fragment.statements, {"fill_value": 0, "rows": 2, "cols": 3},
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
            fragment.statements, {"fill_value": 0, "rows": 2, "cols": 3},
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
        op = PrimitiveOperation(
            id="init1",
            name="Initialize Array",
            labels=["ArrayInit"],
            node_type="aInit",
            terminals=terminals,
        )
        fragment = compound.generate_array_init(op, ctx)
        assert "import copy" in fragment.imports

        result = _compile_and_run(
            fragment.statements, {"fill_value": {"a": 1}, "n": 3},
        )
        arr = result[fragment.bindings["out"]]
        arr[0]["a"] = 99
        assert arr == [{"a": 99}, {"a": 1}, {"a": 1}], f"element aliasing bug: {arr}"


# ── Replace Array Subset (aReplace) ─────────────────────────────────


def _replace_op(new_elem_type: LVType | None = None) -> PrimitiveOperation:
    return PrimitiveOperation(
        id="rep1",
        name="Replace Array Subset",
        labels=["ArrayReplace"],
        node_type="aReplace",
        terminals=[
            Terminal(id="arr", index=0, direction="input"),
            Terminal(id="out", index=1, direction="output"),
            Terminal(
                id="newel", index=2, direction="input", lv_type=new_elem_type,
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
            fragment.statements, {"data": [1, 2, 3, 4], "val": 99, "i": 1},
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
            fragment.statements, {"data": [1, 2, 3], "val": 9, "i": 0},
        )
        assert first[fragment.bindings["out"]] == [9, 2, 3]

        last = _compile_and_run(
            fragment.statements, {"data": [1, 2, 3], "val": 9, "i": 2},
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
            fragment.statements, {"data": [1, 2, 3, 4, 5], "val": 0, "i": 2},
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
            fragment.statements, {"data": [1, 2, 3, 4], "val": 99, "i": 10},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3, 4]

        result_at_len = _compile_and_run(
            fragment.statements, {"data": [1, 2, 3, 4], "val": 99, "i": 4},
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
            fragment.statements, {"data": [1, 2, 3], "sub": [], "i": 1},
        )
        assert result[fragment.bindings["out"]] == [1, 2, 3]
