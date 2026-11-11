"""Where a node's output value goes: straight into the one terminal that reads it
on the node's own diagram, else into a variable named after its wire
(``codegen/output_binding.py``). Each case is a tiny diagram, generated and run."""

from __future__ import annotations

import textwrap

import pytest

from tests.case_structure_fixture import FrameSpec
from tests.error_diagrams import (
    BOOL,
    ERR,
    FILE,
    I32,
    I64,
    REQUIRED,
    Package,
    Project,
    Vi,
    increment,
    project,  # noqa: F401
)

NAME = "Bind.vi"
COMPOUND_ADD, COMPOUND_AND = 0x00000, 0xA0000
ABSOLUTE_VALUE, GET_FILE_SIZE, GREATER = 1054, 8063, 1110


def _body(package: Package) -> str:
    """The generated function's body, dedented."""
    source = package.source
    body = source[source.index("def bind(") :].split("\n", 1)[1]
    return textwrap.dedent(body).strip()


def _and_of_two_controls(vi: Vi) -> dict[str, int]:
    a = vi.control("a", BOOL, REQUIRED)
    b = vi.control("b", BOOL, REQUIRED)
    node = vi.compound_arithmetic(
        COMPOUND_AND, ("result", BOOL), [("value 0", BOOL), ("value 1", BOOL)]
    )
    vi.wire(a, node["value 0"])
    vi.wire(b, node["value 1"])
    return node


def _sum_of_two_controls(vi: Vi) -> dict[str, int]:
    a = vi.control("a", I32, REQUIRED)
    b = vi.control("b", I32, REQUIRED)
    node = vi.compound_arithmetic(
        COMPOUND_ADD, ("total", I32), [("value 0", I32), ("value 1", I32)]
    )
    vi.wire(a, node["value 0"])
    vi.wire(b, node["value 1"])
    return node


def test_one_reader_on_the_same_diagram_takes_the_expression(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    node = _and_of_two_controls(vi)
    vi.wire(node["result"], vi.indicator("result", BOOL))
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == "return BindResult(result=a and b)"
    assert package.function(a=True, b=True).result is True
    assert package.function(a=True, b=False).result is False


def test_two_readers_read_one_variable(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    node = _and_of_two_controls(vi)
    vi.wire(node["result"], vi.indicator("r1", BOOL), vi.indicator("r2", BOOL))
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == (
        "combined = a and b\nreturn BindResult(r1=combined, r2=combined)"
    )
    out = package.function(a=True, b=True)
    assert (out.r1, out.r2) == (True, True)


def test_a_value_read_inside_a_for_loop_is_computed_once_before_it(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    node = _sum_of_two_controls(vi)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    loop = vi.loop("forLoop", tunnels=[(I32, False), (I32, True)])
    vi.wire(n, loop.selector)
    vi.wire(node["total"], loop.tunnels[0].outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
    vi.wire(loop.tunnels[0].faces[0], inc["in"])
    vi.wire(inc["out"], loop.tunnels[1].faces[0])
    vi.wire(loop.tunnels[1].outer, out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        total = a + b
        out = 0
        for i in range(n):
            out = total + 1
        return BindResult(out=out)"""
    )
    assert package.function(a=2, b=3, n=4).out == 6


def test_a_value_read_inside_a_case_frame_is_computed_before_the_case(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    flag = vi.control("flag", BOOL, REQUIRED)
    out = vi.indicator("out", I32)
    first = increment(vi)
    vi.wire(x, first["in"])
    case = vi.case(
        "boolean",
        BOOL,
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=None,
        tunnels=[(I32, False), (I32, True)],
    )
    vi.wire(flag, case.selector)
    vi.wire(first["out"], case.tunnels[0].outer)
    false_in, true_in = case.tunnels[0].faces
    false_out, true_out = case.tunnels[1].faces
    with vi.inside(case.scopes[0]):
        inc = increment(vi)
    vi.wire(false_in, inc["in"])
    vi.wire(inc["out"], false_out)
    vi.wire(true_in, true_out)
    vi.wire(case.tunnels[1].outer, out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        x1 = x + 1
        if flag:
            out = x1
        else:
            out = x1 + 1
        return BindResult(out=out)"""
    )
    assert package.function(x=1, flag=True).out == 2
    assert package.function(x=1, flag=False).out == 3


def test_a_value_for_an_indicator_inside_a_case_frame_is_set_in_the_frame(
    project: Project,  # noqa: F811
) -> None:
    """The indicator is a variable set to its default when the function starts
    and written in the frame, right after the value is made."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    flag = vi.control("flag", BOOL, REQUIRED)
    out = vi.indicator("out", I32)
    case = vi.case(
        "boolean",
        BOOL,
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=None,
        tunnels=[(I32, False)],
    )
    vi.wire(flag, case.selector)
    vi.wire(x, case.tunnels[0].outer)
    with vi.inside(case.scopes[1]):
        inc = increment(vi)
    vi.place_inside(out, case.scopes[1])
    vi.wire(case.tunnels[0].faces[1], inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        if flag:
            out = x + 1
        return BindResult(out=out)"""
    )
    assert package.function(x=1, flag=True).out == 2
    assert package.function(x=1, flag=False).out == 0


def test_a_stop_value_that_reads_a_shift_register_reads_this_iterations_value(
    project: Project,  # noqa: F811
) -> None:
    """The conditional terminal is tested after the shift register takes its next
    value; the test reads the value the iteration started with."""
    vi = Vi(NAME)
    out = vi.indicator("out", I32)
    loop = vi.loop("whileLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(vi.int_constant(0), register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
        limit = vi.int_constant(2)
        slots = [("x > y", 0, BOOL, True), ("y", 1, I32, False), ("x", 2, I32, False)]
        greater = vi.primitive(GREATER, slots)
    vi.wire(register.left_face, inc["in"], greater["x"])
    vi.wire(limit, greater["y"])
    vi.wire(inc["out"], register.right_face)
    vi.wire(greater["x > y"], loop.faces[0])
    vi.wire(register.right_outer, out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        state = 0
        result = False
        while True:
            result = state > 2
            state_updated = state + 1
            state = state_updated
            if result:
                break
        return BindResult(out=state)"""
    )
    assert package.function().out == 4


def test_a_while_loop_tunnel_value_that_reads_a_shift_register_is_kept_in_the_loop(
    project: Project,  # noqa: F811
) -> None:
    """A While Loop's tunnel is read after the loop, when the shift register
    holds its next value; the tunnel gives the last iteration's value."""
    vi = Vi(NAME)
    out = vi.indicator("out", I32)
    loop = vi.loop("whileLoop", tunnels=[(I32, True)], shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(vi.int_constant(0), register.left_outer)
    with vi.inside(loop.scopes[0]):
        step = increment(vi)
        shown = increment(vi)
        stop = vi.bool_constant(True)
    vi.wire(register.left_face, step["in"], shown["in"])
    vi.wire(step["out"], register.right_face)
    vi.wire(shown["out"], loop.tunnels[0].faces[0])
    vi.wire(stop, loop.faces[0])
    vi.wire(loop.tunnels[0].outer, out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        state = 0
        while True:
            out = state + 1
            state_updated = state + 1
            state = state_updated
            break
        return BindResult(out=out)"""
    )
    assert package.function().out == 1


def test_a_count_terminal_read_by_the_loop_alone_takes_the_expression(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    node = _sum_of_two_controls(vi)
    out = vi.indicator("out", I32)
    loop = vi.loop("forLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    zero = vi.int_constant(0)
    vi.wire(node["total"], loop.selector)
    vi.wire(zero, register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
    vi.wire(register.left_face, inc["in"])
    vi.wire(inc["out"], register.right_face)
    vi.wire(register.right_outer, out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        state = 0
        for i in range(a + b):
            state_updated = state + 1
            state = state_updated
        return BindResult(out=state)"""
    )
    assert package.function(a=2, b=3).out == 5


def test_an_expression_with_parentheses_and_no_call_is_written_into_its_reader(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    first = increment(vi)
    second = increment(vi)
    vi.wire(x, first["in"])
    vi.wire(first["out"], second["in"])
    vi.wire(second["out"], vi.indicator("result", I32))
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == "return BindResult(result=x + 1 + 1)"
    assert package.function(x=1).result == 3


def test_an_expression_with_a_call_stays_in_a_variable(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    node = vi.primitive(ABSOLUTE_VALUE, [("abs", 0, I32, True), ("x", 1, I32, False)])
    vi.wire(x, node["x"])
    vi.wire(node["abs"], vi.indicator("result", I32))
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == "abs_x = abs(x)\nreturn BindResult(result=abs_x)"
    assert package.function(x=-4).result == 4


@pytest.mark.parametrize("failed", [False, True])
def test_a_value_after_a_skipped_node_reads_its_default(
    project: Project,  # noqa: F811
    tmp_path,
    failed: bool,
) -> None:
    vi = Vi(NAME)
    file = vi.control("file", FILE, REQUIRED)
    ein = vi.error_in()
    result = vi.indicator("result", I64)
    eout = vi.error_out()
    node = vi.primitive(
        GET_FILE_SIZE,
        [
            ("error in", 4, ERR, False),
            ("refnum", 7, FILE, False),
            ("error out", 0, ERR, True),
            ("size", 2, I64, True),
            ("refnum out", 3, FILE, True),
        ],
    )
    inc = increment(vi)
    vi.wire(file, node["refnum"])
    vi.wire(ein, node["error in"])
    vi.wire(node["size"], inc["in"])
    vi.wire(inc["out"], result)
    vi.wire(node["error out"], eout)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        if error_in is None or not error_in.status:
            try:
                size = __import__('os').fstat(file.fileno()).st_size
            except OSError as exception:
                size = 0
                error_out = LabVIEWError(code=6, source='Get File Size', \
message=str(exception) or 'Generic file I/O error.')
            else:
                error_out = error_in
        else:
            error_out = error_in
            size = 0
        return BindResult(result=size + 1, error_out=error_out)"""
    )
    data = tmp_path / "data.bin"
    data.write_bytes(b"12345")
    error = package.error(7) if failed else None
    with data.open("rb") as handle:
        out = package.function(file=handle, error_in=error)
    assert out.result == (1 if failed else 6)
    assert out.error_out is error
