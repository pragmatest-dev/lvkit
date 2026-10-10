"""Anonymous tuple updates preserve input values and validate index paths."""

from __future__ import annotations

import pytest

from lvkit.runtime.lv import replace_fields


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
    assert replace_fields(incoming, updates) == expected
    assert incoming == (10.0, (20.0, 30.0))


def test_unchanged_branch_identity_and_input_preservation():
    left, right = (10.0, 20.0), (30.0, 40.0)
    incoming = (left, right)
    result = replace_fields(incoming, (((1, 0), 99.0),))
    assert result == (left, (99.0, 40.0))
    assert result[0] is left
    assert result[1] is not right
    assert incoming == (left, right)


@pytest.mark.parametrize("incoming", [None, [10.0, 20.0], 10.0])
def test_non_tuple_clusters_rejected(incoming):
    with pytest.raises(TypeError, match="must be a tuple"):
        replace_fields(incoming, (((0,), 99.0),))


def test_non_tuple_nested_branch_rejected():
    with pytest.raises(TypeError, match="must be a tuple"):
        replace_fields((10.0, 20.0), (((1, 0), 99.0),))


@pytest.mark.parametrize("path", [(), (-1,), (True,), (1.0,)])
def test_invalid_paths_rejected(path):
    with pytest.raises(ValueError, match="index path"):
        replace_fields((10.0, 20.0), ((path, 99.0),))


@pytest.mark.parametrize("path", [(2,), (1, 2)])
def test_out_of_range_paths_rejected(path):
    with pytest.raises(IndexError):
        replace_fields((10.0, (20.0, 30.0)), ((path, 99.0),))
