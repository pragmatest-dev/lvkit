"""Updating the fields of an anonymous (tuple) cluster by position."""

from __future__ import annotations

import pytest

from lvkit.codegen.native_array import replaced_field


def updated(incoming, updates):
    """Apply each update the way the generated code does: one assignment after
    another to the cluster variable."""
    namespace = {"cluster": incoming}
    for path, value in updates:
        namespace["value"] = value
        source = replaced_field("cluster", path, "value")
        namespace["cluster"] = eval(source, namespace)
    return namespace["cluster"]


@pytest.mark.parametrize(
    "updates,expected",
    [
        ((), (10.0, (20.0, 30.0))),
        ((((0,), 99.0),), (99.0, (20.0, 30.0))),
        ((((1, 1), 99.0),), (10.0, (20.0, 99.0))),
        ((((1,), (99.0, 100.0)),), (10.0, (99.0, 100.0))),
        ((((1, 0), 99.0), ((1, 1), 100.0)), (10.0, (99.0, 100.0))),
    ],
)
def test_value_updates(updates, expected):
    incoming = (10.0, (20.0, 30.0))
    assert updated(incoming, updates) == expected
    assert incoming == (10.0, (20.0, 30.0))


def test_unchanged_branch_identity_and_input_preservation():
    left, right = (10.0, 20.0), (30.0, 40.0)
    incoming = (left, right)
    result = updated(incoming, (((1, 0), 99.0),))
    assert result == (left, (99.0, 40.0))
    assert result[0] is left
    assert result[1] is not right
    assert incoming == (left, right)


def test_the_source_is_slicing_and_concatenation():
    assert (
        replaced_field("cluster", (1, 0), "value")
        == "cluster[:1] + (cluster[1][:0] + (value,) + cluster[1][1:],) + cluster[2:]"
    )
