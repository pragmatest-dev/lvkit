"""A value a ``Variant`` still holds is copied before a node changes it: the value
of Get Variant Attribute is held by the variant input (the parent's attributes),
and the data of Variant To Data by its variant. The catalog says so with
``part_of`` and the graph build writes it on the output terminal
(``Terminal.held_in``), so a Bundle By Name that sets a field of the class object
works on a copy and the object the ``Variant`` holds keeps its field.

Each case is a handmade project (``tests/class_graphs.py``) generated into a package
that is imported and run; the catalog entry marks the node as the graph build
does (``mark_held_outputs``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.held_values import mark_held_outputs
from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from lvkit.primitive_resolver import PrimitiveResolver
from tests.class_graphs import INT, Project, class_type, mux, pane, wire
from tests.helpers import error_cluster_type

BASE = class_type("Base")
COUNT = ClusterField(name="Count", type=INT)
VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Variant")
STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
ERR = error_cluster_type()
GET_VARIANT_ATTRIBUTE = 8205
VARIANT_TO_DATA = 8003


def _marked(node: PrimitiveNode) -> PrimitiveNode:
    """``node`` with the outputs its catalog entry says an input holds marked, as the
    graph build marks them."""
    slots = {(t.index, t.direction) for t in node.terminals}
    resolved = PrimitiveResolver().resolve(prim_id=node.prim_id, node_slots=slots)
    assert resolved is not None
    mark_held_outputs(node, resolved.terminals)
    return node


def _terminal(
    vi: str, tag: str, name: str, index: int, direction: str, lv_type: LVType
) -> Terminal:
    return Terminal(
        id=f"{vi}:{tag}.{name}", index=index, direction=direction, lv_type=lv_type
    )


def _get_attribute(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """``fill(v, name, n)``: the class object that is the attribute ``name`` of the
    variant ``v``, its Count set to ``n``; ``kept`` is ``v``."""
    get = _marked(
        PrimitiveNode(
            id=f"{vi}:ga",
            vi_path=vi,
            name="Get Variant Attribute",
            prim_id=GET_VARIANT_ATTRIBUTE,
            terminals=[
                _terminal(vi, "ga", "eout", 0, "output", ERR),
                _terminal(vi, "ga", "value", 1, "output", BASE),
                _terminal(vi, "ga", "found", 2, "output", VARIANT),
                _terminal(vi, "ga", "name", 6, "input", STRING),
                _terminal(vi, "ga", "v", 7, "input", VARIANT),
            ],
        )
    )
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "v", VARIANT, "v", 0),
                pane(vi, "input", "name", STRING, "name", 1),
                pane(vi, "input", "n", INT, "n", 2),
            ],
            outputs=[
                pane(vi, "output", "out", BASE, "out", 3),
                pane(vi, "output", "kept", VARIANT, "kept", 4),
            ],
            data_flow=[
                wire(vi, "v", "ga.v"),
                wire(vi, "v", "kept"),
                wire(vi, "name", "ga.name"),
                wire(vi, "ga.value", "set.agg"),
                wire(vi, "n", "set.field"),
                wire(vi, "set.agg_out", "out"),
            ],
        ),
        [get, mux(vi, "set", True, BASE, INT, 0)],
    )


def _to_data(numbering: str):
    """``fill(v, n)``: the class object Variant To Data takes out of the variant
    ``v``, its Count set to ``n``; ``numbering`` is the node's terminal numbering
    (``"new"`` 0/3/8/9/11 or ``"old"`` 0..4, the two LabVIEW versions write)."""
    indices = (
        {"ein": 0, "eout": 3, "v": 8, "type": 9, "data": 11}
        if numbering == "new"
        else {"ein": 0, "eout": 1, "v": 2, "type": 3, "data": 4}
    )

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        convert = _marked(
            PrimitiveNode(
                id=f"{vi}:vd",
                vi_path=vi,
                name="Variant To Data",
                prim_id=VARIANT_TO_DATA,
                terminals=[
                    _terminal(vi, "vd", "ein", indices["ein"], "input", ERR),
                    _terminal(vi, "vd", "eout", indices["eout"], "output", ERR),
                    _terminal(vi, "vd", "v", indices["v"], "input", VARIANT),
                    _terminal(vi, "vd", "type", indices["type"], "input", BASE),
                    _terminal(vi, "vd", "data", indices["data"], "output", BASE),
                ],
            )
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "v", VARIANT, "v", 0),
                    pane(vi, "input", "n", INT, "n", 1),
                ],
                outputs=[pane(vi, "output", "out", BASE, "out", 2)],
                data_flow=[
                    wire(vi, "v", "vd.v"),
                    wire(vi, "vd.data", "set.agg"),
                    wire(vi, "n", "set.field"),
                    wire(vi, "set.agg_out", "out"),
                ],
            ),
            [convert, mux(vi, "set", True, BASE, INT, 0)],
        )

    return build


def _held_type(variants):
    return variants.LVType(112, "Base.lvclass")


def test_the_value_of_get_variant_attribute_is_held_by_its_variant() -> None:
    node = _get_attribute("v")[1][0]
    assert isinstance(node, PrimitiveNode)
    variant = next(t for t in node.terminals if t.index == 7)
    held = {t.index: t.held_in for t in node.terminals if t.direction == "output"}
    assert held == {0: None, 1: variant.id, 2: None}


@pytest.mark.parametrize("numbering", ["new", "old"])
def test_the_data_of_variant_to_data_is_held_by_its_variant(numbering: str) -> None:
    node = _to_data(numbering)("v")[1][0]
    assert isinstance(node, PrimitiveNode)
    variant = next(t for t in node.terminals if t.name is None and t.id.endswith(".v"))
    data = next(t for t in node.terminals if t.id.endswith(".data"))
    error_out = next(t for t in node.terminals if t.id.endswith(".eout"))
    assert (data.held_in, error_out.held_in) == (variant.id, None)


def test_a_class_object_taken_out_of_an_attribute_is_copied_before_it_is_changed(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Fill", _get_attribute, static=True)
    package = project.generate()
    assert package.gaps == []
    assert "copy.copy(" in package.sources["base"]
    with package.use() as module:
        base, variants = module("base").Base, module("variants")
        mine = base()
        attribute = variants.Variant(mine, _held_type(variants))
        given = variants.Variant(attributes={"o": attribute})
        result = base.fill(v=given, name="o", n=5)
        assert result.out is not mine
        assert (result.out._Base__count, mine._Base__count) == (5, 0)
        # The variant still holds the object it held.
        assert given.attributes["o"] is attribute and attribute.value is mine
        assert result.kept is given


@pytest.mark.parametrize("numbering", ["new", "old"])
def test_a_class_object_taken_out_of_a_variant_is_copied_before_it_is_changed(
    tmp_path: Path, numbering: str
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Fill", _to_data(numbering), static=True)
    package = project.generate()
    assert package.gaps == []
    assert "copy.copy(" in package.sources["base"]
    with package.use() as module:
        base, variants = module("base").Base, module("variants")
        mine = base()
        given = variants.Variant(mine, _held_type(variants))
        result = base.fill(v=given, n=5)
        assert result.out is not mine
        assert (result.out._Base__count, mine._Base__count) == (5, 0)
        assert given.value is mine
