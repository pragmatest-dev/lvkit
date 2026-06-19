"""Tests for lvkit."""

from importlib.metadata import version

import lvkit


def test_version() -> None:
    """__version__ is defined and matches the installed package metadata."""
    assert lvkit.__version__
    assert lvkit.__version__ == version("lvkit")
