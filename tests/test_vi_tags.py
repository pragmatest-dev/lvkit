"""The tags a VI or a library saves, and the Tag methods that read and write them
(``parser/saved_tags.py``, ``codegen/nodes/tag_methods.py``): the bytes of a
``VITS`` block and the properties of a library file are read, every view shows the
tags, and the generated code of each method is imported and run."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, SavedTag, Terminal, VariantData
from lvkit.parser.saved_tags import read_property_tags
from tests.class_graphs import INT, Project, Recipe, pane, wire
from tests.test_front_panel_reference import (
    BOOL,
    ERROR,
    STRING,
    VARIANT,
    VI_REF,
    constant,
    static_reference,
)
from tests.test_saved_tags import LOCALIZED, SOURCE_ONLY, saved_from_bytes

SET, GET, REMOVE = 1037, 1038, 1039
LIB_GET, LIB_SET, LIB_DELETE = 1292952580, 1292952581, 1292952582

# Terminal index, name, direction and type of each parameter of the methods, as the
# corpus's Invoke Nodes record them (a result at 5, then an input and an output
# terminal for each parameter from 6).
LAYOUTS: dict[int, list[tuple[str, int, str, LVType]]] = {
    GET: [
        ("found", 5, "output", BOOL),
        ("name", 6, "input", STRING),
        ("persistent", 9, "output", BOOL),
        ("value", 11, "output", VARIANT),
    ],
    SET: [
        ("done", 5, "output", BOOL),
        ("name", 6, "input", STRING),
        ("value", 8, "input", VARIANT),
        ("persistent", 10, "input", BOOL),
        ("old", 13, "output", VARIANT),
    ],
    REMOVE: [
        ("removed", 5, "output", BOOL),
        ("name", 6, "input", STRING),
        ("old", 9, "output", VARIANT),
    ],
    LIB_GET: [
        ("found", 5, "output", BOOL),
        ("name", 6, "input", STRING),
        ("value", 9, "output", VARIANT),
        ("persistent", 11, "output", BOOL),
    ],
    LIB_SET: [
        ("name", 6, "input", STRING),
        ("value", 8, "input", VARIANT),
        ("persistent", 10, "input", BOOL),
    ],
    LIB_DELETE: [("removed", 5, "output", BOOL), ("name", 6, "input", STRING)],
}
METHOD_NAMES = {
    GET: "Tag.Get Tag",
    SET: "Tag.Set Tag",
    REMOVE: "Tag.Remove Tag",
    LIB_GET: "Get Lib Tag",
    LIB_SET: "Set Lib Tag",
    LIB_DELETE: "Delete Lib Tag",
}


def tag_node(vi: str, tag: str, code: int) -> PrimitiveNode:
    """An Invoke Node of the Tag method ``code``."""
    terminals = [
        ("ref", 0, "input", VI_REF),
        ("ref_out", 1, "output", VI_REF),
        ("err_in", 2, "input", ERROR),
        ("err_out", 3, "output", ERROR),
        *LAYOUTS[code],
    ]
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name="Invoke Node",
        node_type="invokeNode",
        object_name="Library" if code >= LIB_GET else "VI",
        method_name=METHOD_NAMES[code],
        method_code=code,
        terminals=[
            Terminal(id=f"{vi}:{tag}.{name}", index=index, direction=d, lv_type=t)
            for name, index, d, t in terminals
        ],
    )


# One step of a diagram: the method, the String constant wired to Tag Name, the
# constant wired to Value and Is Persistent (type, text), and the outputs of the
# node taken to the VI's own outputs, by terminal name.
Step = tuple[int, str, tuple[LVType, str] | None, bool | None, tuple[str, ...]]


def runner(target_key: str | None, steps: list[Step]) -> Recipe:
    """A VI that runs ``steps`` in turn on one reference (a static reference to
    ``target_key``, else the VI's ``ref`` input) through the error wire. Each output
    taken is a pane output named ``<step>_<terminal>``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        nodes: list[GraphNode] = []
        wires = []
        inputs = []
        if target_key is not None:
            nodes.append(static_reference(vi, target_key))
        else:
            inputs.append(pane(vi, "input", "ref", VI_REF, "ref", 0))
        outputs = []
        previous = None
        for number, (code, name, value, persistent, taken) in enumerate(steps):
            tag = f"s{number}"
            nodes.append(tag_node(vi, tag, code))
            nodes.append(constant(vi, f"{tag}_name", name, STRING))
            wires.append(wire(vi, f"{tag}_name", f"{tag}.name"))
            wires.append(wire(vi, "ref", f"{tag}.ref"))
            if value is not None:
                nodes.append(constant(vi, f"{tag}_value", value[1], value[0]))
                wires.append(wire(vi, f"{tag}_value", f"{tag}.value"))
            if persistent is not None:
                nodes.append(constant(vi, f"{tag}_persistent", str(persistent), BOOL))
                wires.append(wire(vi, f"{tag}_persistent", f"{tag}.persistent"))
            if previous is not None:
                wires.append(wire(vi, f"{previous}.err_out", f"{tag}.err_in"))
            previous = tag
            types = {n: t for n, _, _, t in LAYOUTS[code]}
            for terminal in taken:
                output = f"{tag}_{terminal}"
                outputs.append(
                    pane(vi, "output", output, types[terminal], output, len(outputs))
                )
                wires.append(wire(vi, f"{tag}.{terminal}", output))
        outputs.append(pane(vi, "output", "err", ERROR, "error out", len(outputs)))
        wires.append(wire(vi, f"{previous}.err_out", "err"))
        return VIContext(
            name=vi, inputs=inputs, outputs=outputs, data_flow=wires
        ), nodes

    return build


def target_vi(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """An empty VI: the owner of the tags."""
    return VIContext(name=vi), []


def project_of(
    tmp_path: Path, steps: list[Step], saved: list[SavedTag] | None = None
) -> Project:
    """A Target VI that saves ``saved`` and a Runner of ``steps`` on its static
    reference."""
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target_vi)
    runner_key = project.add_vi("Runner", runner(target_key, steps))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    project.graph._vi_tags[target_key] = saved or []  # noqa: SLF001
    return project


def test_get_tag_returns_a_tag_the_vi_saved(tmp_path: Path) -> None:
    steps: list[Step] = [
        (GET, LOCALIZED, None, None, ("found", "persistent", "value")),
        (GET, "nothing", None, None, ("found", "persistent", "value")),
    ]
    gaps: list[Gap] = []
    package = project_of(tmp_path, steps, saved_from_bytes(tmp_path)).generate(gaps)
    assert gaps == []
    with package.use() as module:
        variants = module("variants")
        result = module("runner").runner()
        assert result.s0_found is True
        assert result.s0_persistent is True
        assert result.s0_value == variants.Variant("Don't Localize", variants.LV_String)
        # A tag that does not exist is FALSE with an empty Variant.
        assert result.s1_found is False
        assert result.s1_persistent is False
        assert result.s1_value == variants.Variant()
        assert result.error_out is None
        assert module("target").target.tags.get(SOURCE_ONLY)[2].value is True


def test_set_tag_then_get_tag(tmp_path: Path) -> None:
    steps: list[Step] = [
        (SET, "mine", (INT, "4"), True, ("done", "old")),
        (GET, "mine", None, None, ("found", "persistent", "value")),
        (SET, "mine", (INT, "5"), None, ("old",)),
        (GET, "mine", None, None, ("persistent", "value")),
    ]
    package = project_of(tmp_path, steps).generate()
    with package.use() as module:
        variants = module("variants")
        result = module("runner").runner()
        assert result.s0_done is True
        assert result.s0_old == variants.Variant()
        assert (result.s1_found, result.s1_persistent) == (True, True)
        assert result.s1_value == variants.Variant(4, variants.LV_I32)
        # The second Set gives the first value back, and is not persistent.
        assert result.s2_old == variants.Variant(4, variants.LV_I32)
        assert result.s3_persistent is False
        assert result.s3_value == variants.Variant(5, variants.LV_I32)
        # The VI saved no tags: the first Set made them.
        assert module("target").target.tags.owner == "Target.vi"


def test_remove_tag(tmp_path: Path) -> None:
    steps: list[Step] = [
        (REMOVE, LOCALIZED, None, None, ("removed", "old")),
        (GET, LOCALIZED, None, None, ("found",)),
        (REMOVE, LOCALIZED, None, None, ("removed", "old")),
    ]
    package = project_of(tmp_path, steps, saved_from_bytes(tmp_path)).generate()
    with package.use() as module:
        variants = module("variants")
        result = module("runner").runner()
        assert result.s0_removed is True
        assert result.s0_old == variants.Variant("Don't Localize", variants.LV_String)
        assert result.s1_found is False
        # Removing a tag that does not exist removes nothing.
        assert result.s2_removed is False
        assert result.s2_old == variants.Variant()


def test_a_reference_that_holds_no_vi_is_error_1026(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi(
        "Runner", runner(None, [(GET, LOCALIZED, None, None, ("found", "value"))])
    )
    package = project.generate()
    with package.use() as module:
        variants = module("variants")
        result = module("runner").runner(ref=None)
        assert result.error_out.code == 1026
        assert result.s0_found is False
        assert result.s0_value == variants.Variant()


def test_an_undecoded_tag_raises_when_it_is_read(tmp_path: Path) -> None:
    saved = [
        SavedTag("odd", None, "its 3-byte Variant is in a form lvkit does not read")
    ]
    steps: list[Step] = [
        (GET, "other", None, None, ("found",)),
        (GET, "odd", None, None, ("value",)),
    ]
    package = project_of(tmp_path, steps, saved).generate()
    assert "UndecodedTag(" in package.sources["target"]
    with package.use() as module:
        with pytest.raises(NotImplementedError) as raised:
            module("runner").runner()
        message = str(raised.value)
        assert "'odd'" in message
        assert "Target.vi" in message
        assert "3-byte Variant" in message
        # Every other tag is still there.
        assert module("target").target.tags.get("other")[0] is False


def test_a_value_with_no_python_form_is_kept_as_an_undecoded_tag(
    tmp_path: Path,
) -> None:
    """A Boolean whose value was not read cannot be written as a constant: the
    tag is listed, and reading it names the reason."""
    saved = [SavedTag("p", VariantData(BOOL, None))]
    package = project_of(
        tmp_path, [(GET, "q", None, None, ("found",))], saved
    ).generate()
    with package.use() as module:
        assert module("runner").runner().s0_found is False
        with pytest.raises(NotImplementedError, match="was not read from the VI"):
            module("target").target.tags.get("p")


def test_get_lib_tag_set_lib_tag_and_delete_lib_tag(tmp_path: Path) -> None:
    steps: list[Step] = [
        (LIB_GET, "NI.Lib.Version", None, None, ("found", "persistent", "value")),
        (LIB_SET, "mine", (INT, "7"), True, ()),
        (LIB_GET, "mine", None, None, ("found", "persistent", "value")),
        (LIB_DELETE, "NI.Lib.Version", None, None, ("removed",)),
        (LIB_GET, "NI.Lib.Version", None, None, ("found",)),
    ]
    project = Project(tmp_path)
    project.add_vi("Runner", runner(None, steps))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    root = ET.fromstring(
        "<Library>"
        '<Property Name="NI.Lib.Version" Type="Str">1.0.0.2</Property>'
        "</Library>"
    )
    (version,) = read_property_tags(root)
    with package.use() as module:
        variants = module("variants")
        library = SimpleNamespace(
            tags=module("panels").Tags(
                "Lib.lvlib",
                {
                    version.name: variants.Variant(
                        eval(version.value_text() or ""), variants.LV_String
                    )
                },
            )
        )
        result = module("runner").runner(ref=library)
        assert result.s0_found is True
        assert result.s0_value == variants.Variant("1.0.0.2", variants.LV_String)
        assert result.s2_found is True
        assert result.s2_persistent is True
        assert result.s2_value == variants.Variant(7, variants.LV_I32)
        assert result.s3_removed is True
        assert result.s4_found is False
