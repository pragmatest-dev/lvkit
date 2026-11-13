"""A terminal's type is the typeDesc directly under its dco. A block-diagram
constant's dco holds a display object whose nested controls carry typeDescs ahead
of the dco's own, so a cluster constant in a structure frame must not take the
type of its first field."""

from __future__ import annotations

from tests.case_structure_fixture import FrameSpec
from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    STR,
    Vi,
    bundle,
    project,  # noqa: F401  (the fixture)
)
from tests.error_diagrams import Project as ProjectType

NAME = "Err.vi"


def _constant_in_frame(proj: ProjectType) -> str:
    """A Boolean case: frame False bundles a status into an error constant that
    has a string control nested in its display object; frame True passes a plain
    error constant."""
    vi = Vi(NAME)
    selector = vi.control("sel", BOOL)
    eout = vi.error_out()
    case = vi.case(
        "boolean",
        BOOL,
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=None,
        tunnels=[(ERR, True)],
    )
    (out,) = case.tunnels
    with vi.inside(case.scopes[0]):
        node = bundle(vi, [("status", 0)])
        cluster = vi.error_constant("none", nested_type=STR)
        status = vi.bool_constant(True)
    vi.wire(cluster, node["cluster in"])
    vi.wire(status, node["status"])
    vi.wire(node["cluster out"], out.faces[0])
    with vi.inside(case.scopes[1]):
        plain = vi.error_constant("none")
    vi.wire(plain, out.faces[1])
    vi.wire(selector, case.selector)
    vi.wire(out.outer, eout)
    proj.add(vi)
    return NAME


def _constant_outside_frame(proj: ProjectType) -> str:
    """The same constant on the VI's own diagram, and a scalar constant."""
    vi = Vi(NAME)
    code = vi.indicator("code", I32)
    eout = vi.error_out()
    node = bundle(vi, [("code", 1)])
    vi.wire(vi.error_constant("none", nested_type=STR), node["cluster in"])
    vi.wire(vi.int_constant(5), node["code"])
    vi.wire(node["cluster out"], eout)
    vi.wire(vi.int_constant(3), code)
    proj.add(vi)
    return NAME


def test_cluster_constant_in_frame_is_the_cluster(project: ProjectType) -> None:  # noqa: F811
    _constant_in_frame(project)
    lvnet = project.views(NAME).lvnet
    assert "      in   1      : Error = no error\n" in lvnet
    assert "''" not in lvnet


def test_cluster_constant_in_frame_generates_the_error_cluster(
    project: ProjectType,  # noqa: F811
) -> None:
    _constant_in_frame(project)
    source = project.generate(NAME).source
    assert "error = LabVIEWError(status=True, code=0, source='')" in source
    assert "''.status" not in source


def test_neighbours_keep_their_types(project: ProjectType) -> None:  # noqa: F811
    _constant_outside_frame(project)
    lvnet = project.views(NAME).lvnet
    assert "      in   1    : Error = no error\n" in lvnet
    assert "      in   code : I32   = 5\n" in lvnet
    assert "    code = 3\n" in lvnet
