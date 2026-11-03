"""A connector-pane indicator written inside a structure frame returns its default
when the call finishes without writing it (``codegen/local_indicators.py``). Each
case is a tiny diagram, generated and run."""

from __future__ import annotations

import textwrap

import pytest

from tests.case_structure_fixture import FrameSpec
from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    increment,
    project,  # noqa: F401
)

NAME = "Cond.vi"
FRAMES = [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))]


def _body(package: Package) -> str:
    source = package.source
    body = source[source.index("def cond(") :].split("\n", 1)[1]
    return textwrap.dedent(body).strip()


def _written_in_true_frame(project: Project) -> None:  # noqa: F811
    """``out = x + 1`` only in the True frame of a Case on ``flag``."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    flag = vi.control("flag", BOOL, REQUIRED)
    out = vi.indicator("out", I32)
    case = vi.case("boolean", BOOL, FRAMES, default_frame=None, tunnels=[(I32, False)])
    vi.wire(flag, case.selector)
    vi.wire(x, case.tunnels[0].outer)
    with vi.inside(case.scopes[1]):
        inc = increment(vi)
    vi.place_inside(out, case.scopes[1])
    vi.wire(case.tunnels[0].faces[1], inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)


def test_an_indicator_a_frame_that_does_not_run_writes_returns_its_default(
    project: Project,  # noqa: F811
) -> None:
    _written_in_true_frame(project)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        if flag:
            out = x + 1
        return CondResult(out=out)"""
    )
    assert package.function(x=1, flag=True).out == 2
    assert package.function(x=1, flag=False).out == 0


def test_an_indicator_a_frame_wires_from_its_tunnel_is_written_in_the_frame(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    flag = vi.control("flag", BOOL, REQUIRED)
    out = vi.indicator("out", I32)
    case = vi.case("boolean", BOOL, FRAMES, default_frame=None, tunnels=[(I32, False)])
    vi.wire(flag, case.selector)
    vi.wire(x, case.tunnels[0].outer)
    vi.place_inside(out, case.scopes[1])
    vi.wire(case.tunnels[0].faces[1], out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        if flag:
            out = x
        return CondResult(out=out)"""
    )
    assert package.function(x=5, flag=True).out == 5
    assert package.function(x=5, flag=False).out == 0


def test_an_indicator_labelled_like_a_control_does_not_take_its_variable(
    project: Project,  # noqa: F811
) -> None:
    """The control ``v`` is read after the indicator ``v`` gets its default."""
    vi = Vi(NAME)
    v = vi.control("v", I32, REQUIRED)
    flag = vi.control("flag", BOOL, REQUIRED)
    out = vi.indicator("v", I32)
    echo = vi.indicator("echo", I32)
    case = vi.case("boolean", BOOL, FRAMES, default_frame=None, tunnels=[(I32, False)])
    vi.wire(flag, case.selector)
    vi.wire(v, case.tunnels[0].outer, echo)
    vi.place_inside(out, case.scopes[1])
    vi.wire(case.tunnels[0].faces[1], out)
    project.add(vi)
    package = project.generate(NAME)
    result = package.function(v=3, flag=False)
    assert result.echo == 3
    assert package.function(v=3, flag=True).echo == 3


def test_an_indicator_a_constant_in_a_frame_writes_is_written_in_the_frame(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    flag = vi.control("flag", BOOL, REQUIRED)
    out = vi.indicator("out", I32)
    case = vi.case("boolean", BOOL, FRAMES, default_frame=None)
    vi.wire(flag, case.selector)
    with vi.inside(case.scopes[1]):
        seven = vi.int_constant(7)
    vi.place_inside(out, case.scopes[1])
    vi.wire(seven, out)
    project.add(vi)
    package = project.generate(NAME)
    assert package.function(flag=True).out == 7
    assert package.function(flag=False).out == 0


def test_an_indicator_a_loop_wires_from_its_inside_face_is_written_in_the_loop(
    project: Project,  # noqa: F811
) -> None:
    """The indicator sits in a For Loop's body, wired straight from the inside
    face of the loop's input tunnel."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    loop = vi.loop("forLoop", tunnels=[(I32, False)])
    vi.wire(n, loop.selector)
    vi.wire(x, loop.tunnels[0].outer)
    vi.place_inside(out, loop.scopes[0])
    vi.wire(loop.tunnels[0].faces[0], out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        for i in range(n):
            out = x
        return CondResult(out=out)"""
    )
    assert package.function(x=5, n=2).out == 5
    assert package.function(x=5, n=0).out == 0


def test_an_indicator_a_shift_register_face_writes_is_written_in_the_loop(
    project: Project,  # noqa: F811
) -> None:
    """The indicator in the loop body shows the shift register's value at the
    start of the last iteration."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    loop = vi.loop("forLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(n, loop.selector)
    vi.wire(x, register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
    vi.place_inside(out, loop.scopes[0])
    vi.wire(register.left_face, inc["in"], out)
    vi.wire(inc["out"], register.right_face)
    project.add(vi)
    package = project.generate(NAME)
    assert package.function(x=10, n=3).out == 12
    assert package.function(x=10, n=0).out == 0


def test_an_indicator_a_sequence_frame_wires_from_its_tunnel_is_written_there(
    project: Project,  # noqa: F811
) -> None:
    """The indicator sits in the second frame of a Flat Sequence, wired straight
    from that frame's input tunnel; the first frame holds an Increment."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    out = vi.indicator("out", I32)
    first = vi.indicator("first", I32)
    seq = vi.flat_sequence([[I32], [I32]])
    vi.wire(x, seq.tunnels[0].outer, seq.tunnels[1].outer)
    with vi.inside(seq.scopes[0]):
        inc = increment(vi)
    vi.place_inside(first, seq.scopes[0])
    vi.wire(seq.tunnels[0].faces[0], inc["in"])
    vi.wire(inc["out"], first)
    vi.place_inside(out, seq.scopes[1])
    vi.wire(seq.tunnels[1].faces[0], out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        first = 0
        first = x + 1
        out = x
        return CondResult(out=out, first=first)"""
    )
    result = package.function(x=5)
    assert (result.out, result.first) == (5, 6)


def test_the_default_is_the_indicators_saved_default_every_call(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def saved(ctrl: object, lv_type: object) -> str | None:
        return "7" if getattr(ctrl, "name", None) == "out" else None

    monkeypatch.setattr("lvkit.graph.construction._default_for", saved)
    _written_in_true_frame(project)
    package = project.generate(NAME)
    assert _body(package).startswith("out = 7\nif flag:\n"), _body(package)
    assert package.function(x=1, flag=True).out == 2
    assert package.function(x=1, flag=False).out == 7


def test_an_indicator_every_frame_writes_through_a_tunnel_is_unchanged(
    project: Project,  # noqa: F811
) -> None:
    """The Case's output tunnel gives the indicator a value in every frame; the
    indicator is on the VI's own diagram."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    flag = vi.control("flag", BOOL, REQUIRED)
    out = vi.indicator("out", I32)
    case = vi.case(
        "boolean",
        BOOL,
        FRAMES,
        default_frame=None,
        tunnels=[(I32, False), (I32, True)],
    )
    vi.wire(flag, case.selector)
    vi.wire(x, case.tunnels[0].outer)
    with vi.inside(case.scopes[1]):
        inc = increment(vi)
    vi.wire(case.tunnels[0].faces[1], inc["in"])
    vi.wire(inc["out"], case.tunnels[1].faces[1])
    vi.wire(case.tunnels[0].faces[0], case.tunnels[1].faces[0])
    vi.wire(case.tunnels[1].outer, out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        if flag:
            out = x + 1
        else:
            out = x
        return CondResult(out=out)"""
    )
    assert package.function(x=1, flag=False).out == 1


def test_an_indicator_in_a_loop_that_runs_zero_times_returns_its_default(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    loop = vi.loop("forLoop", tunnels=[(I32, False)])
    vi.wire(n, loop.selector)
    vi.wire(x, loop.tunnels[0].outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
    vi.place_inside(out, loop.scopes[0])
    vi.wire(loop.tunnels[0].faces[0], inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        for i in range(n):
            out = x + 1
        return CondResult(out=out)"""
    )
    assert package.function(x=1, n=3).out == 2
    assert package.function(x=1, n=0).out == 0


def test_an_indicator_on_the_vis_own_diagram_is_unchanged(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    out = vi.indicator("out", I32)
    inc = increment(vi)
    vi.wire(x, inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == "return CondResult(out=x + 1)"
