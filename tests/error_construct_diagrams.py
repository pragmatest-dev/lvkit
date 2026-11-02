"""One tiny VI per error construct of the errors-as-values plan.

Each builder adds its VI, named ``Err.vi``, and the VIs it calls to a ``Project``
and returns the name ``Err.vi``. A subVI is ``Step A.vi(x) -> r`` with error in and
error out (``step_vi``); the generated module of each called VI is replaced by a
stub in the tests.
"""

from __future__ import annotations

from tests.case_structure_fixture import FrameSpec
from tests.error_diagrams import (
    BOOL,
    ERR,
    FILE,
    I32,
    I64,
    PATH,
    RECOMMENDED,
    REQUIRED,
    STR,
    Project,
    Vi,
    bundle,
    increment,
    merge_errors,
    step_vi,
    unbundle,
)

NAME = "Err.vi"
VILIB_ERROR = "Utility/error.llb"


def _with_steps(project: Project, vi: Vi, *names: str) -> str:
    project.add(vi)
    for name in names:
        project.add(step_vi(name))
    return NAME


def _x_error_result(
    error_rule: int | None = RECOMMENDED,
) -> tuple[Vi, int, int, int, int]:
    """A VI with ``x`` (Required), ``error in``, ``result`` and ``error out``;
    returns the VI and the four terminals."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    ein = vi.error_in(error_rule)
    result = vi.indicator("result", I32)
    eout = vi.error_out(error_rule)
    return vi, x, ein, result, eout


# -- VI with error in / error out, subVI calls --------------------------------


def vi_error_terminals(project: Project, error_rule: int | None = RECOMMENDED) -> str:
    """``result = x + 1``; error in wired straight to error out."""
    vi, x, ein, result, eout = _x_error_result(error_rule)
    inc = increment(vi)
    vi.wire(x, inc["in"])
    vi.wire(inc["out"], result)
    vi.wire(ein, eout)
    project.add(vi)
    return NAME


def subvi_call(project: Project) -> str:
    """``Step A.vi`` with error in and error out wired."""
    vi, x, ein, result, eout = _x_error_result()
    a = vi.step("Step A.vi")
    vi.wire(x, a["x"])
    vi.wire(ein, a["error in"])
    vi.wire(a["r"], result)
    vi.wire(a["error out"], eout)
    return _with_steps(project, vi, "Step A.vi")


def subvi_chain(project: Project) -> str:
    """``Step A.vi`` then ``Step B.vi``, joined by their error wire and by A's
    result into B's ``x``."""
    vi, x, ein, result, eout = _x_error_result()
    a, b = vi.step("Step A.vi"), vi.step("Step B.vi")
    vi.wire(x, a["x"])
    vi.wire(ein, a["error in"])
    vi.wire(a["r"], b["x"])
    vi.wire(a["error out"], b["error in"])
    vi.wire(b["r"], result)
    vi.wire(b["error out"], eout)
    return _with_steps(project, vi, "Step A.vi", "Step B.vi")


def unwired_error_in(project: Project) -> str:
    """``Step A.vi`` then ``Step B.vi`` whose error in is not wired: B takes A's
    result and its error out is the VI's error out; A's error out goes nowhere."""
    vi, x, ein, result, eout = _x_error_result()
    a, b = vi.step("Step A.vi"), vi.step("Step B.vi")
    vi.wire(x, a["x"])
    vi.wire(ein, a["error in"])
    vi.wire(a["r"], b["x"])
    vi.wire(b["r"], result)
    vi.wire(b["error out"], eout)
    return _with_steps(project, vi, "Step A.vi", "Step B.vi")


def forked_error(project: Project) -> str:
    """The error in forks to ``Step A.vi`` and ``Step B.vi`` with no Merge Errors;
    each has its own result and error out."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    ein = vi.error_in()
    result_a = vi.indicator("result a", I32)
    eout_a = vi.indicator("error out a", ERR)
    result_b = vi.indicator("result b", I32)
    eout_b = vi.indicator("error out b", ERR)
    a, b = vi.step("Step A.vi"), vi.step("Step B.vi")
    vi.wire(x, a["x"], b["x"])
    vi.wire(ein, a["error in"], b["error in"])
    vi.wire(a["r"], result_a)
    vi.wire(a["error out"], eout_a)
    vi.wire(b["r"], result_b)
    vi.wire(b["error out"], eout_b)
    return _with_steps(project, vi, "Step A.vi", "Step B.vi")


# -- catalog nodes -------------------------------------------------------------


def catalog_node_skips(project: Project) -> str:
    """Get File Size (primitive 8063): ``size`` of the file ``file``."""
    vi = Vi(NAME)
    file = vi.control("file", FILE, REQUIRED)
    ein = vi.error_in()
    size = vi.indicator("size", I64)
    eout = vi.error_out()
    node = vi.primitive(
        8063,
        [
            ("error in", 4, ERR, False),
            ("refnum", 7, FILE, False),
            ("error out", 0, ERR, True),
            ("size", 2, I64, True),
            ("refnum out", 3, FILE, True),
        ],
    )
    vi.wire(file, node["refnum"])
    vi.wire(ein, node["error in"])
    vi.wire(node["size"], size)
    vi.wire(node["error out"], eout)
    project.add(vi)
    return NAME


def catalog_node_runs_despite_error(project: Project) -> str:
    """Close File (primitive 8052): ``path`` of the file it closes."""
    vi = Vi(NAME)
    file = vi.control("file", FILE, REQUIRED)
    ein = vi.error_in()
    path = vi.indicator("path", PATH)
    eout = vi.error_out()
    node = vi.primitive(
        8052,
        [
            ("error in", 4, ERR, False),
            ("refnum", 7, FILE, False),
            ("error out", 0, ERR, True),
            ("path", 3, PATH, True),
        ],
    )
    vi.wire(file, node["refnum"])
    vi.wire(ein, node["error in"])
    vi.wire(node["path"], path)
    vi.wire(node["error out"], eout)
    project.add(vi)
    return NAME


def native_failure(project: Project) -> str:
    """Open/Create/Replace File (primitive 8050) on the path ``path``; opening a
    file that is not there fails in Python."""
    vi = Vi(NAME)
    path = vi.control("path", PATH, REQUIRED)
    ein = vi.error_in()
    refnum = vi.indicator("refnum", FILE)
    eout = vi.error_out()
    node = vi.primitive(
        8050,
        [
            ("error in", 8, ERR, False),
            ("file path", 11, PATH, False),
            ("error out", 0, ERR, True),
            ("cancelled", 1, BOOL, True),
            ("refnum", 3, FILE, True),
        ],
    )
    vi.wire(path, node["file path"])
    vi.wire(ein, node["error in"])
    vi.wire(node["refnum"], refnum)
    vi.wire(node["error out"], eout)
    project.add(vi)
    return NAME


# -- error case ----------------------------------------------------------------

NO_ERROR_FRAME = FrameSpec(no_error_point=True)


def error_case_panel_fed(project: Project) -> str:
    """An error case fed straight from the VI's own error in. No Error frame:
    ``result = x + 1``. Error frame: ``result = 100``. Error out is the error in."""
    vi, x, ein, result, eout = _x_error_result()
    case = vi.case(
        "error",
        ERR,
        [NO_ERROR_FRAME, FrameSpec()],
        default_frame=1,
        tunnels=[(I32, False), (I32, True)],
    )
    x_in, out = case.tunnels
    with vi.inside(case.scopes[0]):
        inc = increment(vi)
    vi.wire(ein, case.selector, eout)
    vi.wire(x, x_in.outer)
    vi.wire(x_in.faces[0], inc["in"])
    vi.wire(inc["out"], out.faces[0])
    with vi.inside(case.scopes[1]):
        hundred = vi.int_constant(100)
    vi.wire(hundred, out.faces[1])
    vi.wire(out.outer, result)
    project.add(vi)
    return NAME


def error_case_codes(project: Project) -> str:
    """An error case fed from ``Step A.vi``'s error out, with frames No Error
    (``result = 1``), error code 7 (``result = 100``) and Default (``result =
    200``). The error in goes to the subVI; its error out is the VI's."""
    vi, x, ein, result, eout = _x_error_result()
    a = vi.step("Step A.vi")
    case = vi.case(
        "error",
        ERR,
        [NO_ERROR_FRAME, FrameSpec(ranges=((7, 7),)), FrameSpec()],
        default_frame=2,
        tunnels=[(I32, True)],
    )
    (out,) = case.tunnels
    values = []
    for scope, value in zip(case.scopes, (1, 100, 200), strict=True):
        with vi.inside(scope):
            values.append(vi.int_constant(value))
    for face, const in zip(out.faces, values, strict=True):
        vi.wire(const, face)
    vi.wire(x, a["x"])
    vi.wire(ein, a["error in"])
    vi.wire(a["error out"], case.selector, eout)
    vi.wire(out.outer, result)
    return _with_steps(project, vi, "Step A.vi")


# -- Unbundle / Bundle ---------------------------------------------------------


def unbundle_error(project: Project) -> str:
    """Unbundle By Name of the error in: ``failed`` is its status, ``code`` its
    code."""
    vi = Vi(NAME)
    ein = vi.error_in()
    failed = vi.indicator("failed", BOOL)
    code = vi.indicator("code", I32)
    node = unbundle(vi, [("status", 0), ("code", 1)])
    vi.wire(ein, node["cluster"])
    vi.wire(node["status"], failed)
    vi.wire(node["code"], code)
    project.add(vi)
    return NAME


def status_selects_case(project: Project) -> str:
    """The status of the error in selects a Boolean case: False gives
    ``result = 1``, True gives ``result = 100``."""
    vi = Vi(NAME)
    ein = vi.error_in()
    result = vi.indicator("result", I32)
    node = unbundle(vi, [("status", 0)])
    case = vi.case(
        "boolean",
        BOOL,
        [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
        default_frame=None,
        tunnels=[(I32, True)],
    )
    (out,) = case.tunnels
    values = []
    for scope, value in zip(case.scopes, (1, 100), strict=True):
        with vi.inside(scope):
            values.append(vi.int_constant(value))
    for face, const in zip(out.faces, values, strict=True):
        vi.wire(const, face)
    vi.wire(ein, node["cluster"])
    vi.wire(node["status"], case.selector)
    vi.wire(out.outer, result)
    project.add(vi)
    return NAME


def bundle_error(project: Project) -> str:
    """Bundle By Name into an error cluster from ``status``, ``code`` and
    ``source``."""
    vi = Vi(NAME)
    status = vi.control("status", BOOL, REQUIRED)
    code = vi.control("code", I32, REQUIRED)
    source = vi.control("source", STR, REQUIRED)
    eout = vi.error_out()
    node = bundle(vi, [("status", 0), ("code", 1), ("source", 2)])
    none = vi.error_constant("none")
    vi.wire(none, node["cluster in"])
    vi.wire(status, node["status"])
    vi.wire(code, node["code"])
    vi.wire(source, node["source"])
    vi.wire(node["cluster out"], eout)
    project.add(vi)
    return NAME


# -- Merge Errors, Clear Errors, error codes, handlers -------------------------


def merge_errors_in_order(project: Project) -> str:
    """``Step A.vi`` and ``Step B.vi`` both take the error in; their errors merge,
    A's first, and ``Step C.vi`` follows the merged error."""
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    ein = vi.error_in()
    result = vi.indicator("result", I32)
    eout = vi.error_out()
    a, b, c = vi.step("Step A.vi"), vi.step("Step B.vi"), vi.step("Step C.vi")
    merge = merge_errors(vi, 2)
    vi.wire(x, a["x"], b["x"], c["x"])
    vi.wire(ein, a["error in"], b["error in"])
    vi.wire(a["error out"], merge["in 0"])
    vi.wire(b["error out"], merge["in 1"])
    vi.wire(merge["out"], c["error in"])
    vi.wire(c["r"], result)
    vi.wire(c["error out"], eout)
    return _with_steps(project, vi, "Step A.vi", "Step B.vi", "Step C.vi")


def clear_errors(project: Project, *, code: int | None = None) -> str:
    """``Step A.vi`` then Clear Errors (clearing only ``code`` when given) then
    ``Step B.vi`` on the cleared error."""
    vi, x, ein, result, eout = _x_error_result()
    a, b = vi.step("Step A.vi"), vi.step("Step B.vi")
    clear = vi.call(
        "Clear Errors.vi",
        [
            ("specific error code to clear (0: all)", 7, I32, False),
            ("error in (no error)", 4, ERR, False),
            ("specific error cleared?", 3, BOOL, True),
            ("error out", 0, ERR, True),
        ],
        vilib=VILIB_ERROR,
    )
    vi.wire(x, a["x"], b["x"])
    vi.wire(ein, a["error in"])
    vi.wire(a["error out"], clear["error in (no error)"])
    if code is not None:
        vi.wire(vi.int_constant(code), clear["specific error code to clear (0: all)"])
    vi.wire(clear["error out"], b["error in"])
    vi.wire(b["r"], result)
    vi.wire(b["error out"], eout)
    return _with_steps(project, vi, "Step A.vi", "Step B.vi")


def error_cluster_from_error_code(project: Project, *, warning: bool = False) -> str:
    """Error Cluster From Error Code from the control ``code``; ``is warning?`` is
    the constant ``warning``."""
    vi = Vi(NAME)
    code = vi.control("code", I32, REQUIRED)
    eout = vi.error_out()
    node = vi.call(
        "Error Cluster From Error Code.vi",
        [
            ("is warning? (False)", 4, BOOL, False),
            ("show call chain? (False)", 5, BOOL, False),
            ("error in (no error)", 6, ERR, False),
            ('error message ("")', 8, STR, False),
            ("error code (0)", 9, I32, False),
            ("error out", 0, ERR, True),
        ],
        vilib=VILIB_ERROR,
    )
    vi.wire(code, node["error code (0)"])
    if warning:
        vi.wire(vi.bool_constant(True), node["is warning? (False)"])
    vi.wire(node["error out"], eout)
    project.add(vi)
    return NAME


def simple_error_handler(project: Project, *, with_code: bool = False) -> str:
    """Simple Error Handler on the error in: ``failed`` is its ``error?``, ``code``
    its ``code out``; its error out is the VI's. ``with_code`` wires the controls
    ``error code`` and ``error source`` to its error code and error source, and
    its ``source out`` to the indicator ``source``."""
    vi = Vi(NAME)
    ein = vi.error_in()
    code_in = vi.control("error code", I32, RECOMMENDED) if with_code else None
    source_in = vi.control("error source", STR, RECOMMENDED) if with_code else None
    failed = vi.indicator("failed", BOOL)
    code = vi.indicator("code", I32)
    source = vi.indicator("source", STR) if with_code else None
    eout = vi.error_out()
    node = vi.call(
        "Simple Error Handler.vi",
        [
            ("error code (no error:0)", 3, I32, False),
            ('error source (" ")', 4, STR, False),
            ("type of dialog (OK msg:1)", 8, I32, False),
            ("error in (no error)", 9, ERR, False),
            ("error?", 0, BOOL, True),
            ("code out", 1, I32, True),
            ("source out", 5, STR, True),
            ("error out", 6, ERR, True),
            ("message", 7, STR, True),
        ],
        vilib=VILIB_ERROR,
    )
    vi.wire(ein, node["error in (no error)"])
    vi.wire(node["error?"], failed)
    vi.wire(node["code out"], code)
    vi.wire(node["error out"], eout)
    if code_in is not None and source_in is not None and source is not None:
        vi.wire(code_in, node["error code (no error:0)"])
        vi.wire(source_in, node['error source (" ")'])
        vi.wire(node["source out"], source)
    project.add(vi)
    return NAME


# -- loops, constants, local variables -----------------------------------------


def error_shift_register(project: Project) -> str:
    """A For Loop of 3 iterations with an error shift register starting from the
    error in; the body calls ``Step A.vi`` on it with x = 1."""
    vi, x, ein, result, eout = _x_error_result()
    loop = vi.loop("forLoop", shift_registers=[ERR])
    (sr,) = loop.shift_registers
    three = vi.int_constant(3)
    with vi.inside(loop.scopes[0]):
        a = vi.step("Step A.vi")
        one = vi.int_constant(1)
    vi.wire(three, loop.selector)
    vi.wire(ein, sr.left_outer)
    vi.wire(sr.left_face, a["error in"])
    vi.wire(one, a["x"])
    vi.wire(a["error out"], sr.right_face)
    vi.wire(sr.right_outer, eout)
    vi.wire(x, result)
    return _with_steps(project, vi, "Step A.vi")


def error_loop_tunnel(project: Project) -> str:
    """A For Loop of 1 iteration; the error in enters by a tunnel, ``Step A.vi``
    in the body takes it, and its error out leaves by a tunnel."""
    vi, x, ein, result, eout = _x_error_result()
    loop = vi.loop("forLoop", tunnels=[(ERR, False), (ERR, True)])
    t_in, t_out = loop.tunnels
    one = vi.int_constant(1)
    with vi.inside(loop.scopes[0]):
        a = vi.step("Step A.vi")
        one_inner = vi.int_constant(1)
    vi.wire(one, loop.selector)
    vi.wire(ein, t_in.outer)
    vi.wire(t_in.faces[0], a["error in"])
    vi.wire(one_inner, a["x"])
    vi.wire(a["error out"], t_out.faces[0])
    vi.wire(t_out.outer, eout)
    vi.wire(x, result)
    return _with_steps(project, vi, "Step A.vi")


def loop_stops_on_error(project: Project) -> str:
    """A While Loop whose stop terminal is wired from ``Step A.vi``'s error out
    (the error in goes to a tunnel); the error out leaves by a tunnel."""
    vi, x, ein, result, eout = _x_error_result()
    loop = vi.loop("whileLoop", tunnels=[(ERR, True)])
    (t_out,) = loop.tunnels
    with vi.inside(loop.scopes[0]):
        a = vi.step("Step A.vi")
        one = vi.int_constant(1)
    vi.wire(one, a["x"])
    vi.wire(a["error out"], loop.faces[0], t_out.faces[0])
    vi.wire(t_out.outer, eout)
    vi.wire(x, result)
    return _with_steps(project, vi, "Step A.vi")


def error_constant(project: Project, kind: str) -> str:
    """An error constant of ``kind`` (none, error5, warning5) wired to error out."""
    vi = Vi(NAME)
    eout = vi.error_out()
    vi.wire(vi.error_constant(kind), eout)
    project.add(vi)
    return NAME


def error_local_variable(project: Project) -> str:
    """The error in is written to the local variable of the indicator ``saved``;
    a read of the local feeds ``Step A.vi``'s error in."""
    vi, x, ein, result, eout = _x_error_result()
    saved = vi.indicator("saved", ERR, None)
    write = vi.local_variable(saved)
    read = vi.local_variable(saved)
    a = vi.step("Step A.vi")
    vi.wire(ein, write)
    vi.wire(read, a["error in"])
    vi.wire(x, a["x"])
    vi.wire(a["r"], result)
    vi.wire(a["error out"], eout)
    return _with_steps(project, vi, "Step A.vi")
