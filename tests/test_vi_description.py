"""A VI's description is its function's docstring, and the Description property of
a VI reference reads it (``inspect.getdoc``). Each case is a handmade project whose
generated package is imported and run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import ConstantNode, GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, LVTypeKind, PropertyDef, PropertySegment, Terminal
from tests.class_graphs import Project, Recipe, pane, wire
from tests.test_global_panel import STRING, _control
from tests.test_run_vi import VI_REF

DESCRIPTION = "Does <B>the</B> thing.\nSays \"hi\" and 'bye' and \\ and '''."
# What NI's VI class records for the property (TestCase shortDescription.vi and
# TestSuite GetDescription.vi).
DESCRIPTION_CODE = 502


def described(description: str | None) -> Recipe:
    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return VIContext(name=vi, description=description), []

    return build


def description_reader(target_key: str, write: bool) -> Recipe:
    """A VI that reads (or, with ``write``, writes) the Description of a Static
    VI Reference to ``target_key``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        reference = ConstantNode(
            id=f"{vi}:ref",
            vi_path=vi,
            name="Target.vi",
            value="Target.vi",
            lv_type=VI_REF,
            referenced_vi_key=target_key,
            terminals=[
                Terminal(id=f"{vi}:ref", index=0, direction="output", lv_type=VI_REF)
            ],
        )
        node = PrimitiveNode(
            id=f"{vi}:prop",
            vi_path=vi,
            name="Property Node",
            node_type="propNode",
            object_name="VI",
            terminals=[
                Terminal(
                    id=f"{vi}:prop.ref", index=0, direction="input", lv_type=VI_REF
                ),
                Terminal(
                    id=f"{vi}:prop.value",
                    index=4,
                    direction="input" if write else "output",
                    lv_type=STRING,
                ),
            ],
            properties=[
                PropertyDef(
                    segments=[
                        PropertySegment(name="Description", code=DESCRIPTION_CODE)
                    ]
                )
            ],
            property_value_terminal_ids=[f"{vi}:prop.value"],
        )
        data_flow = [wire(vi, "ref", "prop.ref")]
        outputs = []
        constants: list[GraphNode] = []
        if write:
            text = ConstantNode(
                id=f"{vi}:text",
                vi_path=vi,
                name="text",
                value="new",
                lv_type=STRING,
                terminals=[
                    Terminal(
                        id=f"{vi}:text", index=0, direction="output", lv_type=STRING
                    )
                ],
            )
            constants.append(text)
            data_flow.append(wire(vi, "text", "prop.value"))
        else:
            outputs.append(pane(vi, "output", "text", STRING, "text", 0))
            data_flow.append(wire(vi, "prop.value", "text"))
        context = VIContext(name=vi, outputs=outputs, data_flow=data_flow)
        return context, [reference, node, *constants]

    return build


def read_project(tmp_path: Path, description: str | None) -> Project:
    project = Project(tmp_path)
    target = project.add_vi("Target", described(description))
    reader = project.add_vi("Reader", description_reader(target, write=False))
    project.graph._dep_graph.add_edge(reader, target)  # noqa: SLF001
    return project


def test_a_vi_with_a_description_has_it_as_its_docstring_and_the_property_reads_it(
    tmp_path: Path,
) -> None:
    package = read_project(tmp_path, DESCRIPTION).generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("target").target.__doc__ == DESCRIPTION
        assert module("reader").reader().text == DESCRIPTION


def test_a_vi_with_no_description_reads_an_empty_string(tmp_path: Path) -> None:
    package = read_project(tmp_path, None).generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("target").target.__doc__ is None
        assert module("reader").reader().text == ""


def test_a_blank_description_is_no_docstring(tmp_path: Path) -> None:
    package = read_project(tmp_path, "  \n ").generate()
    with package.use() as module:
        assert module("target").target.__doc__ is None
        assert module("reader").reader().text == ""


def test_a_description_write_is_a_gap_with_the_standard_message(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target = project.add_vi("Target", described("Does a thing."))
    writer = project.add_vi("Writer", description_reader(target, write=True))
    project.graph._dep_graph.add_edge(writer, target)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    reason = "Property 'Description' on a VI reference has no Python equivalent"
    assert [(g.node_name, g.kind, g.reason) for g in gaps] == [
        ("Property Node", "unsupported_node", reason)
    ]
    with package.use() as module:
        with pytest.raises(NotImplementedError, match=reason):
            module("writer").writer()


def test_the_description_comes_before_the_front_panel_notes(tmp_path: Path) -> None:
    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        control = _control(
            vi,
            "7",
            "Extra",
            LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
            "3",
        )
        return VIContext(name=vi, description="Hello.", off_pane_inputs=[control]), []

    project = Project(tmp_path)
    project.add_vi("Target", build)
    package = project.generate()
    with package.use() as module:
        doc = module("target").target.__doc__
        assert doc is not None
        assert doc.startswith("Hello.\n\nFront panel, not on the connector pane:")
