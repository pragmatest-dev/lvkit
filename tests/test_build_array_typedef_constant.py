"""A cluster constant saved without its type definition that feeds a Build Array
is written as the type definition the array's other elements carry (an object with
an attribute for each field), whether it feeds the Build Array directly or through
a structure's tunnel. Executed generated code."""

from __future__ import annotations

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.native_vi import array, generate, node, scalar

I32 = scalar("NumInt32")


def _cluster(typedef_name: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name=typedef_name,
        fields=[ClusterField(name="a", type=I32), ClusterField(name="b", type=I32)],
    )


NAMED = _cluster("point.ctl")
ANONYMOUS = _cluster(None)


def _build(constant_type: LVType):
    """A Build Array of the control ``x`` (the type definition) and a constant."""
    build = node(
        "ba",
        None,
        [(0, "input", NAMED), (1, "input", constant_type), (2, "output", array(NAMED))],
        node_type="aBuild",
    )
    return generate(
        {"x": NAMED},
        {"out": array(NAMED)},
        [build],
        [("fp:x", "ba:0"), ("c1", "ba:1"), ("ba:2", "fp:out")],
        constants={"c1": ("{'a': 3, 'b': 4}", constant_type)},
    )


def test_an_anonymous_cluster_constant_is_one_element_of_the_type_definition() -> None:
    generated = _build(ANONYMOUS)
    assert "SimpleNamespace(a=3, b=4)" in generated.source
    assert "(3, 4)" not in generated.source.replace("SimpleNamespace(a=3, b=4)", "")


def test_a_constant_that_names_the_type_definition_is_unchanged() -> None:
    generated = _build(NAMED)
    assert "SimpleNamespace(a=3, b=4)" in generated.source
