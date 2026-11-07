"""A class property accessor behaves as its VI does with an incoming error.

An accessor whose diagram cannot put an error of its own on its error out stays a
Python property: a read skips (its value the type's default) when the accessor's
work sits in the No Error frame of an error case on its error in, and runs
otherwise; the error out is the incoming error. An accessor that can produce an
error of its own is an ordinary method with error in and error out. Each case is
a handmade class (``tests/class_graphs.py``) generated, imported and run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.class_model import class_model
from lvkit.graph.error_behaviour import ErrorBehaviour
from lvkit.graph.models import (
    CaseFrame,
    CaseStructureNode,
    ConstantNode,
    GraphNode,
    PrimitiveNode,
    VIContext,
)
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
    TunnelTerminal,
)
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import (
    INT,
    Project,
    Recipe,
    call,
    class_type,
    mux,
    pane,
    wire,
)

BASE, CHILD = class_type("Base"), class_type("Child")
COUNT = ClusterField(name="Count", type=INT)
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(
            name="status",
            type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
        ),
        ClusterField(name="code", type=INT),
        ClusterField(
            name="source",
            type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
        ),
    ],
)
_NO_ERROR, _ERROR = "0", "1"


def _error_pane(vi: str) -> tuple[list[Terminal], list[Terminal]]:
    """The error in (pane 8) and error out (pane 0) of an accessor."""
    return (
        [
            pane(
                vi,
                "input",
                "err_in",
                ERROR,
                "error in",
                8,
                ParsedWiringRule.RECOMMENDED,
            )
        ],
        [pane(vi, "output", "err_out", ERROR, "error out", 0)],
    )


def _tunnel(vi: str, tag: str, lv_type: LVType, into: bool) -> list[TunnelTerminal]:
    """A case structure tunnel ``tag``: its outer face ``<tag>o`` and an inner face
    ``<tag><frame>`` in each frame; ``into`` the case (an input tunnel) or out."""
    outer = TunnelTerminal(
        id=f"{vi}:{tag}o",
        index=0,
        direction="input" if into else "output",
        boundary="outer",
        tunnel_type="caseSel" if tag == "sel" else "selTun",
        paired_id=f"{vi}:{tag}{_NO_ERROR}",
        lv_type=lv_type,
    )
    inner = [
        TunnelTerminal(
            id=f"{vi}:{tag}{frame}",
            index=0,
            direction="output" if into else "input",
            boundary="inner",
            tunnel_type=outer.tunnel_type,
            paired_id=outer.id,
            frame=frame,
            lv_type=lv_type,
        )
        for frame in (_NO_ERROR, _ERROR)
    ]
    return [outer, *inner]


def _error_case(
    vi: str, inputs: list[tuple[str, LVType]], outputs: list[tuple[str, LVType]]
) -> tuple[CaseStructureNode, list]:
    """An error case selected by the VI's error in, which it passes out to the
    error out in both frames; its other tunnels are ``inputs`` and ``outputs``."""
    terminals = _tunnel(vi, "sel", ERROR, into=True) + _tunnel(
        vi, "e", ERROR, into=False
    )
    wires = [wire(vi, "err_in", "selo"), wire(vi, "eo", "err_out")]
    for frame in (_NO_ERROR, _ERROR):
        wires += [wire(vi, "selo", f"sel{frame}"), wire(vi, f"sel{frame}", f"e{frame}")]
        wires.append(wire(vi, f"e{frame}", "eo"))
    for tag, lv_type in inputs:
        terminals += _tunnel(vi, tag, lv_type, into=True)
        wires += [wire(vi, f"{tag}o", f"{tag}{f}") for f in (_NO_ERROR, _ERROR)]
    for tag, lv_type in outputs:
        terminals += _tunnel(vi, tag, lv_type, into=False)
        wires += [wire(vi, f"{tag}{f}", f"{tag}o") for f in (_NO_ERROR, _ERROR)]
    case = CaseStructureNode(
        id=f"{vi}:case",
        vi_path=vi,
        node_type="caseStruct",
        terminals=terminals,
        selector_terminal=f"{vi}:selo",
        frames=[
            CaseFrame(selector_value=_NO_ERROR, is_no_error=True),
            CaseFrame(selector_value=_ERROR),
        ],
    )
    return case, wires


def _in_no_error_frame(*nodes: GraphNode) -> None:
    for node in nodes:
        node.parent = node.id.rsplit(":", 1)[0] + ":case"
        node.frame = _NO_ERROR


def guarded_reader(owner: LVType) -> Recipe:
    """A getter whose Unbundle By Name sits in the No Error frame of an error
    case on its error in: the value is the type's default on an error."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        case, wires = _error_case(vi, [("c", owner)], [("v", INT)])
        get = mux(vi, "get", False, owner, INT, 0)
        _in_no_error_frame(get)
        wires += [
            wire(vi, "in", "out"),
            wire(vi, "in", "co"),
            wire(vi, f"c{_NO_ERROR}", "get.agg"),
            wire(vi, "get.field", f"v{_NO_ERROR}"),
            wire(vi, "vo", "value"),
        ]
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 11), *errors_in],
                outputs=[
                    pane(vi, "output", "out", owner, "Self out", 3),
                    pane(vi, "output", "value", INT, "value", 2),
                    *errors_out,
                ],
                data_flow=wires,
            ),
            [case, get],
        )

    return build


def guarded_writer(owner: LVType) -> Recipe:
    """A setter whose Bundle By Name sits in the No Error frame of an error case
    on its error in: the field is not written on an error."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        case, wires = _error_case(vi, [("c", owner), ("v", INT)], [("k", owner)])
        put = mux(vi, "set", True, owner, INT, 0)
        _in_no_error_frame(put)
        wires += [
            wire(vi, "in", "co"),
            wire(vi, "value", "vo"),
            wire(vi, f"c{_NO_ERROR}", "set.agg"),
            wire(vi, f"v{_NO_ERROR}", "set.field"),
            wire(vi, "set.agg_out", f"k{_NO_ERROR}"),
            wire(vi, f"c{_ERROR}", f"k{_ERROR}"),
            wire(vi, "ko", "out"),
        ]
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "in", owner, "Self", 11),
                    pane(vi, "input", "value", INT, "value", 10),
                    *errors_in,
                ],
                outputs=[pane(vi, "output", "out", owner, "Self out", 3), *errors_out],
                data_flow=wires,
            ),
            [case, put],
        )

    return build


def plain_reader(owner: LVType) -> Recipe:
    """A getter that reads the field whatever error comes in and wires its error
    in straight to its error out."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 11), *errors_in],
                outputs=[
                    pane(vi, "output", "out", owner, "Self out", 3),
                    pane(vi, "output", "value", INT, "value", 2),
                    *errors_out,
                ],
                data_flow=[
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "out"),
                    wire(vi, "get.field", "value"),
                    wire(vi, "err_in", "err_out"),
                ],
            ),
            [mux(vi, "get", False, owner, INT, 0)],
        )

    return build


def failing_reader(owner: LVType) -> Recipe:
    """A getter that also puts the field into the code of its error out (a Bundle
    By Name into the error): an error of its own."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 11), *errors_in],
                outputs=[
                    pane(vi, "output", "out", owner, "Self out", 3),
                    pane(vi, "output", "value", INT, "value", 2),
                    *errors_out,
                ],
                data_flow=[
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "out"),
                    wire(vi, "get.field", "value"),
                    wire(vi, "err_in", "code.agg"),
                    wire(vi, "get.field", "code.field"),
                    wire(vi, "code.agg_out", "err_out"),
                ],
            ),
            [
                mux(vi, "get", False, owner, INT, 0),
                mux(vi, "code", True, ERROR, INT, 1),
            ],
        )

    return build


def parent_call_reader(callee: str) -> Recipe:
    """A getter of the child that is Call Parent of the parent's getter, its
    error in and error out wired through the call."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", CHILD, "Self", 11), *errors_in],
                outputs=[
                    pane(vi, "output", "out", CHILD, "Self out", 3),
                    pane(vi, "output", "value", INT, "value", 2),
                    *errors_out,
                ],
                data_flow=[
                    wire(vi, "in", "c.self"),
                    wire(vi, "err_in", "c.error in"),
                    wire(vi, "c.self out", "out"),
                    wire(vi, "c.value", "value"),
                    wire(vi, "c.error out", "err_out"),
                ],
            ),
            [
                call(
                    vi,
                    "c",
                    callee,
                    "callParentDynIUse",
                    [("self", 11, BASE), ("error in", 8, ERROR)],
                    [("self out", 3, BASE), ("value", 2, INT), ("error out", 0, ERROR)],
                )
            ],
        )

    return build


def reads_count(callee: str) -> Recipe:
    """A VI that calls the getter ``callee`` with its error in and returns the
    value and the error out."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", BASE, "obj", 11), *errors_in],
                outputs=[pane(vi, "output", "count", INT, "count", 2), *errors_out],
                data_flow=[
                    wire(vi, "obj", "r.self"),
                    wire(vi, "err_in", "r.error in"),
                    wire(vi, "r.value", "count"),
                    wire(vi, "r.error out", "err_out"),
                ],
            ),
            [
                call(
                    vi,
                    "r",
                    callee,
                    "iUse",
                    [("self", 11, BASE), ("error in", 8, ERROR)],
                    [("self out", 3, BASE), ("value", 2, INT), ("error out", 0, ERROR)],
                )
            ],
        )

    return build


def writes_count(callee: str) -> Recipe:
    """A VI that calls the setter ``callee`` with its error in."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", BASE, "obj", 11),
                    pane(vi, "input", "n", INT, "n", 10),
                    *errors_in,
                ],
                outputs=[*errors_out],
                data_flow=[
                    wire(vi, "obj", "w.self"),
                    wire(vi, "n", "w.value"),
                    wire(vi, "err_in", "w.error in"),
                    wire(vi, "w.error out", "err_out"),
                ],
            ),
            [
                call(
                    vi,
                    "w",
                    callee,
                    "iUse",
                    [("self", 11, BASE), ("value", 10, INT), ("error in", 8, ERROR)],
                    [("self out", 3, BASE), ("error out", 0, ERROR)],
                )
            ],
        )

    return build


def _project(tmp_path: Path, getter: Recipe, setter: Recipe | None = None) -> Project:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    read = project.add_method("Base", "Read Count", getter, property_name="Count")
    project.add_vi("Reader", reads_count(read))
    if setter is not None:
        write = project.add_method("Base", "Write Count", setter, property_name="Count")
        project.add_vi("Writer", writes_count(write))
    return project


def test_a_reader_whose_work_is_in_the_no_error_frame_skips_on_an_error(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, guarded_reader(BASE))
    key = project.vis["Base.Read Count"]
    assert project.graph.vi_error_behaviour(key) is ErrorBehaviour.SKIPS_ON_ERROR
    package = project.generate()
    assert package.gaps == []
    assert "obj.count" in package.sources["reader"]
    with package.use() as module:
        errors, base = module("errors"), module("base").Base()
        base._Base__count = 7
        reader = module("reader").reader
        result = reader(obj=base)
        assert (result.count, result.error_out) == (7, None)
        error = errors.LabVIEWError(status=True, code=42, source="before")
        result = reader(obj=base, error_in=error)
        assert (result.count, result.error_out) == (0, error)
        warning = errors.LabVIEWError(status=False, code=5, source="before")
        result = reader(obj=base, error_in=warning)
        assert (result.count, result.error_out) == (7, warning)


def test_a_reader_with_no_error_case_runs_on_an_error(tmp_path: Path) -> None:
    project = _project(tmp_path, plain_reader(BASE))
    key = project.vis["Base.Read Count"]
    assert project.graph.vi_error_behaviour(key) is ErrorBehaviour.RUNS_REGARDLESS
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        errors, base = module("errors"), module("base").Base()
        base._Base__count = 7
        error = errors.LabVIEWError(status=True, code=42, source="before")
        result = module("reader").reader(obj=base, error_in=error)
        assert (result.count, result.error_out) == (7, error)


def test_a_writer_whose_work_is_in_the_no_error_frame_skips_on_an_error(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, guarded_reader(BASE), guarded_writer(BASE))
    key = project.vis["Base.Write Count"]
    assert project.graph.vi_error_behaviour(key) is ErrorBehaviour.SKIPS_ON_ERROR
    package = project.generate()
    assert package.gaps == []
    assert "obj.count = n" in package.sources["writer"]
    with package.use() as module:
        errors, base = module("errors"), module("base").Base()
        writer = module("writer").writer
        assert writer(obj=base, n=3).error_out is None
        assert base.count == 3
        error = errors.LabVIEWError(status=True, code=42, source="before")
        assert writer(obj=base, n=9, error_in=error).error_out == error
        assert base.count == 3


def test_a_reader_that_can_fail_is_a_method_with_error_terminals(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, failing_reader(BASE))
    key = project.vis["Base.Read Count"]
    assert project.graph.vi_error_behaviour(key) is ErrorBehaviour.CAN_FAIL
    model = class_model(project.graph, project.classes["Base"])
    assert model.attributes.accessors == {}
    package = project.generate()
    assert package.gaps == []
    assert "@property" not in package.sources["base"]
    assert ".read_count(error_in=" in package.sources["reader"]
    with package.use() as module:
        errors, base = module("errors"), module("base").Base()
        base._Base__count = 7
        reader = module("reader").reader
        result = reader(obj=base)
        # The accessor's own error (a warning with the field as its code) reaches
        # the caller's error out.
        assert result.count == 7
        assert (result.error_out.status, result.error_out.code) == (False, 7)
        error = errors.LabVIEWError(status=True, code=42, source="before")
        result = reader(obj=base, error_in=error)
        assert result.error_out.code == 42


def test_call_parent_of_a_skipping_reader_is_a_skipping_property(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    read = project.add_method(
        "Base", "Read Count", guarded_reader(BASE), property_name="Count"
    )
    project.add_method(
        "Child", "Read Count", parent_call_reader(read), property_name="Count"
    )
    project.add_vi("Reader", reads_count(read))
    child_key = project.vis["Child.Read Count"]
    assert project.graph.vi_error_behaviour(child_key) is ErrorBehaviour.SKIPS_ON_ERROR
    package = project.generate()
    assert package.gaps == []
    assert "super().count" in package.sources["child"]
    with package.use() as module:
        errors, child = module("errors"), module("child").Child()
        child._Base__count = 4
        reader = module("reader").reader
        assert reader(obj=child).count == 4
        error = errors.LabVIEWError(status=True, code=1, source="before")
        assert reader(obj=child, error_in=error).count == 0


def test_a_family_with_one_reader_that_can_fail_is_methods_throughout(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    project.add_method("Base", "Read Count", plain_reader(BASE), property_name="Count")
    project.add_method(
        "Child", "Read Count", failing_reader(CHILD), property_name="Count"
    )
    graph = project.graph
    for cls in ("Base", "Child"):
        assert class_model(graph, project.classes[cls]).attributes.accessors == {}
    package = project.generate()
    assert package.gaps == []
    assert "def read_count(self, *, error_in" in package.sources["base"]
    assert "def read_count(self, *, error_in" in package.sources["child"]


# --- a Property Node on a class wire ---------------------------------------------

BOOLEAN = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")


def erroring_reader(owner: LVType) -> Recipe:
    """A getter that reads the field and reports an error whose code is the
    field (a Bundle By Name of status True and the code into its error in)."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        bundle = PrimitiveNode(
            id=f"{vi}:b",
            vi_path=vi,
            name="Bundle By Name",
            node_type="nMux",
            terminals=[
                Terminal(
                    id=f"{vi}:b.agg",
                    index=0,
                    direction="input",
                    lv_type=ERROR,
                    nmux_role="agg",
                ),
                Terminal(
                    id=f"{vi}:b.status",
                    index=2,
                    direction="input",
                    lv_type=BOOLEAN,
                    nmux_role="list",
                    nmux_field_index=0,
                ),
                Terminal(
                    id=f"{vi}:b.code",
                    index=3,
                    direction="input",
                    lv_type=INT,
                    nmux_role="list",
                    nmux_field_index=1,
                ),
                Terminal(
                    id=f"{vi}:b.agg_out",
                    index=1,
                    direction="output",
                    lv_type=ERROR,
                    nmux_role="agg",
                ),
            ],
        )
        true = ConstantNode(
            id=f"{vi}:t",
            vi_path=vi,
            value="True",
            lv_type=BOOLEAN,
            terminals=[
                Terminal(id=f"{vi}:t", index=0, direction="output", lv_type=BOOLEAN)
            ],
        )
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 11), *errors_in],
                outputs=[
                    pane(vi, "output", "out", owner, "Self out", 3),
                    pane(vi, "output", "value", INT, "value", 2),
                    *errors_out,
                ],
                data_flow=[
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "out"),
                    wire(vi, "get.field", "value"),
                    wire(vi, "err_in", "b.agg"),
                    wire(vi, "t", "b.status"),
                    wire(vi, "get.field", "b.code"),
                    wire(vi, "b.agg_out", "err_out"),
                ],
            ),
            [mux(vi, "get", False, owner, INT, 0), bundle, true],
        )

    return build


def property_node_vi(owner: LVType, rows: list[tuple[str, bool]]) -> Recipe:
    """A VI with one Property Node on its ``obj`` wire: each row ``(path, reads)``
    reads the property into the indicator ``r<i>`` or writes the control ``w<i>``
    to it; the node's error in and error out are the VI's."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        errors_in, errors_out = _error_pane(vi)
        terminals = [
            Terminal(id=f"{vi}:p.ref", index=0, direction="input", lv_type=owner),
            Terminal(id=f"{vi}:p.ref_out", index=1, direction="output", lv_type=owner),
            Terminal(id=f"{vi}:p.err", index=2, direction="input", lv_type=ERROR),
            Terminal(id=f"{vi}:p.err_out", index=3, direction="output", lv_type=ERROR),
        ]
        inputs = [pane(vi, "input", "obj", owner, "obj", 11), *errors_in]
        outputs = list(errors_out)
        wires = [
            wire(vi, "obj", "p.ref"),
            wire(vi, "err_in", "p.err"),
            wire(vi, "p.err_out", "err_out"),
        ]
        properties, value_ids = [], []
        for i, (path, reads) in enumerate(rows):
            tag = f"p.row{i}"
            terminals.append(
                Terminal(
                    id=f"{vi}:{tag}",
                    index=4 + i,
                    direction="output" if reads else "input",
                    lv_type=INT,
                    name=path,
                )
            )
            properties.append(
                PropertyDef(segments=[PropertySegment(name=path, code=34)])
            )
            value_ids.append(f"{vi}:{tag}")
            if reads:
                outputs.append(pane(vi, "output", f"r{i}", INT, f"r{i}", 2 + i))
                wires.append(wire(vi, tag, f"r{i}"))
            else:
                inputs.append(pane(vi, "input", f"w{i}", INT, f"w{i}", 5 + i))
                wires.append(wire(vi, f"w{i}", tag))
        node = PrimitiveNode(
            id=f"{vi}:p",
            vi_path=vi,
            name="Property Node",
            node_type="propNode",
            terminals=terminals,
            properties=properties,
            property_value_terminal_ids=value_ids,
        )
        return (
            VIContext(name=vi, inputs=inputs, outputs=outputs, data_flow=wires),
            [node],
        )

    return build


def _property_node_project(tmp_path: Path, rows: list[tuple[str, bool]]) -> Project:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    folder = ("Accessor",)
    project.add_method(
        "Base",
        "Read Count",
        guarded_reader(BASE),
        property_name="Count",
        folders=folder,
    )
    project.add_method(
        "Base",
        "Write Count",
        guarded_writer(BASE),
        property_name="Count",
        folders=folder,
    )
    project.add_method(
        "Base",
        "Read Check",
        erroring_reader(BASE),
        property_name="Check",
        folders=folder,
    )
    project.add_vi("Probe", property_node_vi(BASE, rows))
    return project


def test_a_property_node_row_after_an_error_does_not_run(tmp_path: Path) -> None:
    project = _property_node_project(
        tmp_path, [("Accessor:Check", True), ("Accessor:Count", True)]
    )
    package = project.generate()
    assert package.gaps == []
    source = package.sources["probe"]
    assert ".read_check(error_in=" in source
    with package.use() as module:
        errors, base = module("errors"), module("base").Base()
        base._Base__count = 7
        probe = module("probe").probe
        result = probe(obj=base)
        # The first row reports its error: the second does not run.
        assert (result.r0, result.r1) == (7, 0)
        assert (result.error_out.status, result.error_out.code) == (True, 7)
        # An error that came in skips the whole node.
        error = errors.LabVIEWError(status=True, code=42, source="before")
        result = probe(obj=base, error_in=error)
        assert (result.r0, result.r1, result.error_out) == (0, 0, error)


def test_property_node_rows_of_python_properties_use_their_python_names(
    tmp_path: Path,
) -> None:
    project = _property_node_project(
        tmp_path, [("Accessor:Count", False), ("Accessor:Count", True)]
    )
    package = project.generate()
    assert package.gaps == []
    source = package.sources["probe"]
    assert "obj.count = w0" in source and "= obj.count" in source
    assert "accessor" not in source
    with package.use() as module:
        base = module("base").Base()
        result = module("probe").probe(obj=base, w0=5)
        assert (result.r1, result.error_out, base.count) == (5, None, 5)


def test_a_property_node_on_a_class_that_is_not_loaded_is_a_gap(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi(
        "Probe", property_node_vi(class_type("Missing"), [("Accessor:Count", True)])
    )
    package = project.generate()
    assert len(package.gaps) == 1
    assert "'Missing.lvclass'" in package.gaps[0].message
    assert "is not loaded" in package.gaps[0].message
    with package.use() as module:
        with pytest.raises(NotImplementedError, match="is not loaded"):
            module("probe").probe(obj=None)
