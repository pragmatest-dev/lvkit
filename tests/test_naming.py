"""Tests for naming utilities (build/parse qualified names, relative paths)."""

from __future__ import annotations

import pytest

from lvpy.naming import build_qualified_name, build_relative_path, parse_qualified_name


class TestBuildQualifiedName:
    def test_standalone_item(self):
        assert build_qualified_name([], "Standalone.vi") == "Standalone.vi"

    def test_single_container(self):
        assert (
            build_qualified_name(["sysdir.llb"], "Type.ctl")
            == "sysdir.llb:Type.ctl"
        )

    def test_nested_containers(self):
        assert (
            build_qualified_name(["Lib.lvlib", "Class.lvclass"], "Method.vi")
            == "Lib.lvlib:Class.lvclass:Method.vi"
        )


class TestParseQualifiedName:
    def test_standalone(self):
        assert parse_qualified_name("Standalone.vi") == ([], "Standalone.vi")

    def test_single_container(self):
        assert parse_qualified_name("sysdir.llb:Type.ctl") == (
            ["sysdir.llb"],
            "Type.ctl",
        )

    def test_nested_containers(self):
        assert parse_qualified_name("Lib.lvlib:Class.lvclass:Method.vi") == (
            ["Lib.lvlib", "Class.lvclass"],
            "Method.vi",
        )


@pytest.mark.parametrize(
    "chain, item",
    [
        ([], "Standalone.vi"),
        (["sysdir.llb"], "Type.ctl"),
        (["Lib.lvlib", "Class.lvclass"], "Method.vi"),
        (["A.lvlib", "B.lvlib", "C.lvclass"], "Deep.vi"),
    ],
)
def test_round_trip(chain: list[str], item: str):
    """build then parse should return the original chain and item."""
    result_chain, result_item = parse_qualified_name(
        build_qualified_name(chain, item),
    )
    assert result_chain == chain
    assert result_item == item


class TestBuildRelativePath:
    def test_basic_path(self):
        assert (
            build_relative_path(["Utility", "sysdir.llb", "Type.ctl"])
            == "Utility/sysdir.llb/Type.ctl"
        )

    def test_single_component(self):
        assert build_relative_path(["file.vi"]) == "file.vi"

    def test_empty(self):
        assert build_relative_path([]) == ""
