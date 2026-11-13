"""Empty String/Path? on a Variant is TRUE only for an empty variant (one whose
``type`` is ``None``): a variant holding ``""`` is not empty; on a String or Path
it keeps its meaning. The function is generated, imported and called."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import Project, pane, wire

BOOLEAN = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Variant")
STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
EMPTY_STRING_PATH = 1112


def is_empty_vi(lv_type: LVType):
    """A VI whose input ``c`` goes through Empty String/Path? to ``out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = PrimitiveNode(
            id=f"{vi}:n",
            vi_path=vi,
            name="Empty String/Path?",
            prim_id=EMPTY_STRING_PATH,
            terminals=[
                Terminal(id=f"{vi}:n.in", index=1, direction="input", lv_type=lv_type),
                Terminal(
                    id=f"{vi}:n.out", index=0, direction="output", lv_type=BOOLEAN
                ),
            ],
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "c", lv_type, "c", 0, ParsedWiringRule.OPTIONAL)
                ],
                outputs=[pane(vi, "output", "out", BOOLEAN, "out", 1)],
                data_flow=[wire(vi, "c", "n.in"), wire(vi, "n.out", "out")],
            ),
            [node],
        )

    return build


def generated(tmp_path: Path, lv_type: LVType):
    project = Project(tmp_path)
    project.add_vi("IsEmpty", is_empty_vi(lv_type))
    package = project.generate()
    assert package.gaps == []
    return package


@pytest.mark.parametrize(
    ("type_name", "value", "is_empty"),
    [
        (None, None, True),
        ("I32", 0, False),
        ("String", "", False),
        ("String", "x", False),
    ],
)
def test_a_variant_is_empty_only_when_it_holds_no_data(
    tmp_path: Path, type_name: str | None, value: object, is_empty: bool
) -> None:
    package = generated(tmp_path, VARIANT)
    source = package.sources["isempty"]
    assert "    return IsemptyResult(out=c.type is None)\n" in source
    with package.use() as module:
        variants = module("variants")
        held = variants.Variant()
        if type_name is not None:
            held = variants.Variant(value, getattr(variants, f"LV_{type_name}"))
        assert module("isempty").isempty(c=held).out is is_empty


def test_an_unwired_variant_is_empty(tmp_path: Path) -> None:
    package = generated(tmp_path, VARIANT)
    with package.use() as module:
        assert module("isempty").isempty().out is True


@pytest.mark.parametrize(("value", "is_empty"), [("", True), ("x", False)])
def test_a_string_is_empty_when_it_has_no_characters(
    tmp_path: Path, value: str, is_empty: bool
) -> None:
    package = generated(tmp_path, STRING)
    with package.use() as module:
        assert module("isempty").isempty(c=value).out is is_empty


@pytest.mark.parametrize(
    ("value", "is_empty"),
    [(Path(""), True), (Path("a/b"), False), (None, False)],
)
def test_a_path_is_empty_when_it_has_no_component(
    tmp_path: Path, value: Path | None, is_empty: bool
) -> None:
    package = generated(tmp_path, PATH)
    with package.use() as module:
        assert module("isempty").isempty(c=value).out is is_empty
