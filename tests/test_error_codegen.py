"""Error wires in generated code.

An error cluster is not a value in generated code: a failure raises
``LabVIEWError`` (or the Python exception of the call that failed), and the nodes
that handle an error catch it. Each test builds a tiny block diagram in memory,
generates the module, asserts the exact source, and runs it.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.error_codes import describe_error_code
from lvkit.codegen.error_path import ERROR_WIRE_VI_NAMES
from lvkit.codegen.errors_module import ERRORS_MODULE_SOURCE
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import (
    CaseStructureNode,
    Constant,
    PrimitiveNode,
    VIContext,
    VINode,
    Wire,
)
from lvkit.models import (
    CaseFrame,
    ClusterField,
    LVType,
    LVTypeKind,
    Terminal,
    TunnelTerminal,
)
from tests.helpers import build_graph, register_nodes

INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
STR = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
ERR = LVType(
    kind=LVTypeKind.CLUSTER,
    typedef_name="error",
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=INT),
        ClusterField(name="source", type=STR),
    ],
)

NO_ERROR, ERROR = "No Error", "Error"
UNSPECIFIED = -2147467259


def term(tid: str, direction: str, index: int, name: str, lv: LVType) -> Terminal:
    return Terminal(id=tid, direction=direction, index=index, name=name, lv_type=lv)


def tunnel(
    tid: str,
    direction: str,
    boundary: str,
    paired: str,
    lv: LVType,
    frame: str | None = None,
    name: str = "t",
) -> TunnelTerminal:
    return TunnelTerminal(
        id=tid,
        direction=direction,
        index=0,
        name=name,
        lv_type=lv,
        tunnel_type="caseSel",
        boundary=boundary,
        paired_id=paired,
        frame=frame,
    )


def call(
    node_id: str,
    name: str,
    ins: list[Terminal],
    outs: list[Terminal],
    parent: str | None = None,
    frame: str | None = None,
) -> VINode:
    """A subVI call with ``ins`` and ``outs`` as its terminals."""
    return VINode(
        id=node_id,
        vi_path="caller.vi",
        name=name,
        node_type="iUse",
        terminals=[*ins, *outs],
        parent=parent,
        frame=frame,
        # A call of a vi.lib error VI records its vi.lib path.
        qualified_path=(
            f"<vilib>/Utility/error.llb/{name}" if name in ERROR_WIRE_VI_NAMES else None
        ),
    )


def step(node_id: str, name: str, **where: str | None) -> VINode:
    """A subVI ``name(x) -> r`` whose error in / error out are wired."""
    return call(
        node_id,
        name,
        [
            term(f"{node_id}_x", "input", 0, "x", INT),
            term(f"{node_id}_ein", "input", 1, "error in", ERR),
        ],
        [
            term(f"{node_id}_r", "output", 2, "r", INT),
            term(f"{node_id}_eout", "output", 3, "error out", ERR),
        ],
        **where,
    )


def diagram(
    nodes: list,
    wires: list[tuple[str, str]],
    inputs: list[Terminal] | None = None,
    outputs: list[Terminal] | None = None,
    constants: list[Constant] | None = None,
) -> str:
    """The module generated for a VI ``Caller`` holding ``nodes``."""
    vi_context = VIContext(
        name="Caller",
        inputs=inputs or [],
        outputs=outputs or [],
        constants=constants or [],
        data_flow=[
            Wire.from_terminals(from_terminal_id=a, to_terminal_id=b) for a, b in wires
        ],
    )
    graph = build_graph(vi_context, "caller.vi", nodes)
    return build_module(vi_context, "caller.vi", graph=graph)


X_IN = term("x", "output", 0, "x", INT)
ERR_IN = term("ein", "output", 1, "error in", ERR)
RESULT_OUT = term("result", "input", 0, "result", INT)
ERR_OUT = term("eout", "input", 1, "error out", ERR)


@pytest.fixture
def package(tmp_path: Path) -> Iterator[Callable[..., ModuleType]]:
    """Write a generated module beside stub subVI modules and the package's
    ``errors`` module, and import it."""
    loaded: list[str] = []

    def load(source: str, stubs: dict[str, str]) -> ModuleType:
        name = f"errpkg_{abs(hash(tmp_path))}_{len(loaded)}"
        root = tmp_path / name
        root.mkdir()
        (root / "__init__.py").write_text("")
        (root / "errors.py").write_text(ERRORS_MODULE_SOURCE)
        (root / "caller.py").write_text(source)
        for module, body in stubs.items():
            (root / f"{module}.py").write_text(textwrap.dedent(body))
        sys.path.insert(0, str(tmp_path))
        importlib.invalidate_caches()
        loaded.append(name)
        return importlib.import_module(f"{name}.caller")

    yield load
    while str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))
    for key in [k for k in sys.modules if k.split(".")[0] in loaded]:
        del sys.modules[key]


STEP_STUB = """
    from types import SimpleNamespace

    def {fn}(x=0):
        return SimpleNamespace(r=x + {add})
"""


def stub(fn: str, add: int = 1) -> str:
    return STEP_STUB.format(fn=fn, add=add)


def errors_of(module: ModuleType) -> type[Exception]:
    errors = importlib.import_module(module.__name__.rsplit(".", 1)[0] + ".errors")
    return errors.LabVIEWError


# =============================================================
# A chain of subVIs joined by error wires
# =============================================================


def _chain() -> str:
    return diagram(
        [step("A", "Step A.vi"), step("B", "Step B.vi")],
        [
            ("x", "A_x"),
            ("ein", "A_ein"),
            ("A_r", "B_x"),
            ("A_eout", "B_ein"),
            ("B_r", "result"),
            ("B_eout", "eout"),
        ],
        inputs=[X_IN, ERR_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )


def test_error_wires_between_subvis_are_plain_calls() -> None:
    assert _chain() == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .step_a import step_a
        from .step_b import step_b

        class CallerResult(NamedTuple):
            result: int

        def caller(x: int=0) -> CallerResult:
            step_a_result = step_a(x=x)
            step_b_result = step_b(x=step_a_result.r)
            return CallerResult(result=step_b_result.r)"""
    )


def test_a_chain_of_subvis_runs_and_an_exception_propagates(package) -> None:
    module = package(_chain(), {"step_a": stub("step_a"), "step_b": stub("step_b", 10)})
    assert module.caller(x=1).result == 12
    failing = """
        def step_a(x=0):
            raise FileNotFoundError("missing")
    """
    module = package(_chain(), {"step_a": failing, "step_b": stub("step_b")})
    with pytest.raises(FileNotFoundError):
        module.caller(x=1)


# =============================================================
# Error Cluster From Error Code
# =============================================================

ECFEC = "Error Cluster From Error Code.vi"


def _ecfec(*, message: bool = False) -> VINode:
    ins = [
        term("e_warn", "input", 4, "is warning? (False)", BOOL),
        term("e_chain", "input", 5, "show call chain? (False)", BOOL),
        term("e_ein", "input", 6, "error in (no error)", ERR),
        term("e_msg", "input", 8, 'error message ("")', STR),
        term("e_code", "input", 9, "error code (0)", INT),
    ]
    return call("E", ECFEC, ins, [term("e_eout", "output", 0, "error out", ERR)])


def test_error_cluster_from_a_computed_code_raises_when_it_is_not_zero(
    package,
) -> None:
    source = diagram(
        [_ecfec()],
        [("code", "e_code"), ("e_eout", "eout")],
        inputs=[term("code", "output", 0, "code", INT)],
        outputs=[ERR_OUT],
    )
    assert source == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError

        def caller(code: int=0):
            if code != 0:
                raise LabVIEWError(code=code, message='')"""
    )
    module = package(source, {})
    module.caller(code=0)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(code=5)
    assert (raised.value.code, raised.value.message) == (5, "")


def test_error_cluster_from_a_constant_code_writes_nis_description() -> None:
    description = describe_error_code(UNSPECIFIED)
    assert description
    source = diagram(
        [_ecfec()],
        [("c1", "e_code"), ("e_eout", "eout")],
        outputs=[ERR_OUT],
        constants=[Constant(id="c1", value=str(UNSPECIFIED), lv_type=INT)],
    )
    assert source == textwrap.dedent(
        f"""\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError

        def caller():
            raise LabVIEWError(code={UNSPECIFIED}, message={description!r})"""
    )


def test_error_cluster_from_a_wired_message_keeps_the_message_first() -> None:
    description = describe_error_code(UNSPECIFIED)
    source = diagram(
        [_ecfec()],
        [("c1", "e_code"), ("msg", "e_msg"), ("e_eout", "eout")],
        inputs=[term("msg", "output", 0, "msg", STR)],
        outputs=[ERR_OUT],
        constants=[Constant(id="c1", value=str(UNSPECIFIED), lv_type=INT)],
    )
    assert (
        f"raise LabVIEWError(code={UNSPECIFIED}, message=msg or {description!r})"
        in source
    )


def test_error_cluster_from_the_code_zero_raises_nothing() -> None:
    source = diagram(
        [_ecfec()],
        [("c0", "e_code"), ("e_eout", "eout")],
        outputs=[ERR_OUT],
        constants=[Constant(id="c0", value=0, lv_type=INT)],
    )
    assert "raise" not in source
    assert "LabVIEWError" not in source


# =============================================================
# Clear Errors
# =============================================================


def _clear() -> VINode:
    return call(
        "C",
        "Clear Errors.vi",
        [
            term("c_code", "input", 7, "specific error code to clear (0: all)", INT),
            term("c_ein", "input", 4, "error in (no error)", ERR),
        ],
        [
            term("c_cleared", "output", 3, "specific error cleared?", BOOL),
            term("c_eout", "output", 0, "error out", ERR),
        ],
    )


def _clear_chain() -> str:
    return diagram(
        [step("A", "Step A.vi"), _clear(), step("B", "Step B.vi")],
        [
            ("x", "A_x"),
            ("A_eout", "c_ein"),
            ("x", "B_x"),
            ("c_eout", "B_ein"),
            ("B_r", "result"),
            ("B_eout", "eout"),
        ],
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )


def test_clear_errors_catches_the_errors_of_the_code_on_its_wire() -> None:
    assert _clear_chain() == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError
        from .step_a import step_a
        from .step_b import step_b

        class CallerResult(NamedTuple):
            result: int

        def caller(x: int=0) -> CallerResult:
            try:
                step_a_result = step_a(x=x)
            except LabVIEWError:
                pass
            step_b_result = step_b(x=x)
            return CallerResult(result=step_b_result.r)"""
    )


def test_cleared_error_does_not_stop_the_code_after_it(package) -> None:
    module = package(
        _clear_chain(),
        {
            "step_a": """
                from ..errors import LabVIEWError
                def step_a(x=0):
                    raise LabVIEWError(code=7)
            """.replace("..errors", ".errors"),
            "step_b": stub("step_b"),
        },
    )
    assert module.caller(x=1).result == 2


# =============================================================
# Merge Errors and parallel branches
# =============================================================


def _merge_fan_out() -> str:
    merge = PrimitiveNode(
        id="M",
        vi_path="caller.vi",
        name="Merge Errors",
        node_type="prim",
        prim_id=2147,
        terminals=[
            term("m_in0", "input", 0, "error in 0", ERR),
            term("m_in1", "input", 1, "error in 1", ERR),
            term("m_out", "output", 2, "error out", ERR),
        ],
    )
    return diagram(
        [step("A", "Step A.vi"), step("B", "Step B.vi"), step("C", "Step C.vi"), merge],
        [
            ("x", "A_x"),
            ("A_eout", "B_ein"),
            ("A_eout", "C_ein"),
            ("x", "B_x"),
            ("x", "C_x"),
            ("B_eout", "m_in0"),
            ("C_eout", "m_in1"),
            ("m_out", "eout"),
            ("B_r", "result"),
        ],
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )


def test_parallel_branches_with_errors_hold_their_errors_to_the_merge() -> None:
    assert _merge_fan_out() == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError
        from .step_a import step_a
        from .step_b import step_b
        from .step_c import step_c
        import concurrent.futures

        class CallerResult(NamedTuple):
            result: int

        def caller(x: int=0) -> CallerResult:
            _held_error = None
            step_a(x=x)
            with concurrent.futures.ThreadPoolExecutor() as _executor:

                def _branch_0():
                    step_b_result = step_b(x=x)
                    return step_b_result
                _f0 = _executor.submit(_branch_0)

                def _branch_1():
                    step_c_result = step_c(x=x)
                    return step_c_result
                _f1 = _executor.submit(_branch_1)
            try:
                step_b_result = _f0.result()
            except LabVIEWError as e:
                _held_error = _held_error or e
                step_b_result = None
            try:
                step_c_result = _f1.result()
            except LabVIEWError as e:
                _held_error = _held_error or e
                step_c_result = None
            if _held_error:
                raise _held_error
            return CallerResult(result=step_b_result.r)"""
    )


def test_every_branch_runs_and_the_first_error_is_raised_at_the_merge(
    package,
) -> None:
    def branch(name: str, fail: bool) -> str:
        body = f"raise LabVIEWError(code={ord(name)})" if fail else "pass"
        return f"""
            from .errors import LabVIEWError
            calls = []
            def step_{name}(x=0):
                calls.append(x)
                {body}
                from types import SimpleNamespace
                return SimpleNamespace(r=x)
        """

    module = package(
        _merge_fan_out(),
        {
            "step_a": stub("step_a"),
            "step_b": branch("b", True),
            "step_c": branch("c", True),
        },
    )
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == ord("b")
    package_name = module.__name__.rsplit(".", 1)[0]
    assert importlib.import_module(package_name + ".step_c").calls == [1]


# =============================================================
# A VI whose only output is its error
# =============================================================


def test_a_vi_with_only_error_out_returns_none(package) -> None:
    source = diagram(
        [step("A", "Step A.vi")],
        [("x", "A_x"), ("ein", "A_ein"), ("A_eout", "eout")],
        inputs=[X_IN, ERR_IN],
        outputs=[ERR_OUT],
    )
    assert source == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .step_a import step_a

        def caller(x: int=0):
            step_a(x=x)"""
    )
    module = package(source, {"step_a": stub("step_a")})
    assert module.caller(x=1) is None


# =============================================================
# Bundling an error cluster
# =============================================================


def _bundle_error() -> VINode:
    return PrimitiveNode(
        id="N",
        vi_path="caller.vi",
        name="Bundle",
        node_type="nMux",
        terminals=[
            Terminal(
                id="n_agg",
                direction="input",
                index=0,
                name="error",
                lv_type=ERR,
                nmux_role="agg",
            ),
            Terminal(
                id="n_aggo",
                direction="output",
                index=0,
                name="error",
                lv_type=ERR,
                nmux_role="agg",
            ),
            Terminal(
                id="n_status",
                direction="input",
                index=1,
                name="status",
                lv_type=BOOL,
                nmux_role="list",
                nmux_field_index=0,
            ),
            Terminal(
                id="n_code",
                direction="input",
                index=2,
                name="code",
                lv_type=INT,
                nmux_role="list",
                nmux_field_index=1,
            ),
            Terminal(
                id="n_source",
                direction="input",
                index=3,
                name="source",
                lv_type=STR,
                nmux_role="list",
                nmux_field_index=2,
            ),
        ],
    )


def test_bundling_a_true_status_into_an_error_raises_it() -> None:
    source = diagram(
        [_bundle_error()],
        [
            ("t", "n_status"),
            ("code", "n_code"),
            ("src", "n_source"),
            ("n_aggo", "eout"),
        ],
        inputs=[
            term("code", "output", 0, "code", INT),
            term("src", "output", 1, "src", STR),
        ],
        outputs=[ERR_OUT],
        constants=[Constant(id="t", value="True", lv_type=BOOL)],
    )
    assert source == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError

        def caller(code: int=0, src: str=''):
            raise LabVIEWError(code=code, source=src)"""
    )


def test_bundling_a_computed_status_into_an_error_raises_when_it_is_true(
    package,
) -> None:
    source = diagram(
        [_bundle_error()],
        [("fail", "n_status"), ("code", "n_code"), ("n_aggo", "eout")],
        inputs=[
            term("fail", "output", 0, "fail", BOOL),
            term("code", "output", 1, "code", INT),
        ],
        outputs=[ERR_OUT],
    )
    assert "if fail:\n        raise LabVIEWError(code=code)" in source
    module = package(source, {})
    module.caller(fail=False, code=3)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(fail=True, code=3)
    assert raised.value.code == 3


# =============================================================
# Reading the status and source of an error
# =============================================================


def _read_error() -> PrimitiveNode:
    """An Unbundle By Name of an error cluster: status and source."""
    return PrimitiveNode(
        id="U",
        vi_path="caller.vi",
        name="Unbundle",
        node_type="nMux",
        terminals=[
            Terminal(
                id="u_agg",
                direction="input",
                index=0,
                name="error",
                lv_type=ERR,
                nmux_role="agg",
            ),
            Terminal(
                id="u_status",
                direction="output",
                index=1,
                name="status",
                lv_type=BOOL,
                nmux_role="list",
                nmux_field_index=0,
            ),
            Terminal(
                id="u_source",
                direction="output",
                index=2,
                name="source",
                lv_type=STR,
                nmux_role="list",
                nmux_field_index=2,
            ),
        ],
    )


def _status_diagram() -> str:
    return diagram(
        [step("A", "Step A.vi"), _read_error()],
        [
            ("x", "A_x"),
            ("A_eout", "u_agg"),
            ("u_status", "failed"),
            ("u_source", "src"),
        ],
        inputs=[X_IN],
        outputs=[
            term("failed", "input", 0, "failed", BOOL),
            term("src", "input", 1, "src", STR),
        ],
    )


def test_status_of_an_error_from_the_caller_is_false() -> None:
    source = diagram(
        [_read_error()],
        [("ein", "u_agg"), ("u_status", "failed")],
        inputs=[ERR_IN],
        outputs=[term("failed", "input", 0, "failed", BOOL)],
    )
    assert source == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple

        class CallerResult(NamedTuple):
            failed: bool

        def caller() -> CallerResult:
            return CallerResult(failed=False)"""
    )


# =============================================================
# Error case structures
# =============================================================


def _error_case(
    *,
    error_frame_work: bool,
    keeps_error: bool = False,
    reads_error: bool = False,
    from_caller: bool = False,
    reads_producer: bool = False,
    builtin_in_error_frame: bool = False,
    producer_raises: bool = False,
) -> str:
    """``Open`` raises -> a case on its error. The No Error frame calls ``Use``;
    the Error frame (when it does work) calls ``Report``. Both send their result
    out of the case; the error leaves the Error frame as it came in
    (``keeps_error``) or is not sent out (cleared)."""
    nodes: list = [
        step("P", "Open.vi"),
        step("Q", "Use.vi", parent="CS", frame=NO_ERROR),
    ]
    terminals = [
        term("sel", "input", 0, "sel", ERR),
        tunnel("t_in", "input", "outer", "t_in_e", ERR),
        tunnel("t_in_e", "output", "inner", "t_in", ERR, frame=ERROR),
        tunnel("t_out", "output", "outer", "t_out_e", ERR),
        tunnel("t_out_e", "input", "inner", "t_out", ERR, frame=ERROR),
        tunnel("d_out", "output", "outer", "d_n", INT, name="data"),
        tunnel("d_n", "input", "inner", "d_out", INT, frame=NO_ERROR, name="data"),
        tunnel("d_e", "input", "inner", "d_out", INT, frame=ERROR, name="data"),
    ]
    wires = [
        ("x", "P_x"),
        ("ein" if from_caller else "P_eout", "sel"),
        ("ein" if from_caller else "P_eout", "t_in"),
        ("P_r", "Q_x"),
        ("Q_r", "d_n"),
        ("d_out", "result"),
        ("t_out", "eout"),
    ]
    if keeps_error:
        wires.append(("t_in_e", "t_out_e"))
    if builtin_in_error_frame:
        nodes.append(
            PrimitiveNode(
                id="B",
                vi_path="caller.vi",
                name="Some Built-in Node",
                node_type="prim",
                parent="CS",
                frame=ERROR,
                terminals=[
                    term("B_ein", "input", 0, "error in", ERR),
                    term("B_eout", "output", 1, "error out", ERR),
                ],
            )
        )
        wires += [("t_in_e", "B_ein"), ("B_eout", "t_out_e")]
    if error_frame_work:
        report_x = "P_r" if reads_producer else "x"
        if reads_error:
            nodes.append(
                PrimitiveNode(
                    id="U",
                    vi_path="caller.vi",
                    name="Unbundle",
                    node_type="nMux",
                    parent="CS",
                    frame=ERROR,
                    terminals=[
                        Terminal(
                            id="u_agg",
                            direction="input",
                            index=0,
                            name="error",
                            lv_type=ERR,
                            nmux_role="agg",
                        ),
                        Terminal(
                            id="u_code",
                            direction="output",
                            index=1,
                            name="code",
                            lv_type=INT,
                            nmux_role="list",
                            nmux_field_index=1,
                        ),
                    ],
                )
            )
            wires.append(("t_in_e", "u_agg"))
            report_x = "u_code"
        nodes.append(step("R", "Report.vi", parent="CS", frame=ERROR))
        wires += [(report_x, "R_x"), ("R_r", "d_e")]
    case = CaseStructureNode(
        id="CS",
        vi_path="caller.vi",
        name="Case",
        node_type="caseStruct",
        selector_terminal="sel",
        frames=[
            CaseFrame(selector_value=NO_ERROR, is_no_error=True),
            CaseFrame(selector_value=ERROR),
        ],
        terminals=terminals,
    )
    inputs = [X_IN, *([ERR_IN] if from_caller else [])]
    if producer_raises:
        # Error Cluster From Error Code raises the error the case handles.
        nodes[0] = _ecfec()
        inputs.append(term("code", "output", 2, "code", INT))
        wires = [
            ("e_eout" if a == "P_eout" else "x" if a == "P_r" else a, b)
            for a, b in wires
            if b != "P_x"
        ] + [("code", "e_code")]
    return diagram(
        [*nodes, case],
        wires,
        inputs=inputs,
        outputs=[RESULT_OUT, ERR_OUT],
    )


def test_error_frame_that_does_work_is_the_handler_of_the_producers() -> None:
    assert _error_case(error_frame_work=True) == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError
        from .open_ import open_
        from .report import report
        from .use import use

        class CallerResult(NamedTuple):
            result: int

        def caller(x: int=0) -> CallerResult:
            data = 0
            try:
                open__result = open_(x=x)
            except LabVIEWError as e:
                report_result = report(x=x)
                data = report_result.r
                raise
            else:
                use_result = use(x=open__result.r)
                data = use_result.r
            return CallerResult(result=data)"""
    )


def test_error_frame_runs_in_the_handler_and_the_other_frame_after_a_clean_call(
    package,
) -> None:
    source = _error_case(error_frame_work=True)
    stubs = {
        "open_": stub("open_", 0),
        "use": stub("use", 100),
        "report": """
            from types import SimpleNamespace
            calls = []
            def report(x=0):
                calls.append(x)
                return SimpleNamespace(r=x + 200)
        """,
    }
    module = package(source, stubs)
    assert module.caller(x=1).result == 101
    failing = {
        **stubs,
        "open_": """
            from .errors import LabVIEWError
            def open_(x=0):
                raise LabVIEWError(code=4)
        """,
    }
    module = package(source, failing)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 4
    report_calls = importlib.import_module(
        module.__name__.rsplit(".", 1)[0] + ".report"
    ).calls
    assert report_calls == [1]


def test_error_frame_reads_the_code_of_an_error_the_diagram_raises(package) -> None:
    source = _error_case(error_frame_work=True, reads_error=True, producer_raises=True)
    assert "report_result = report(x=e.code)" in source
    module = package(source, {"use": stub("use"), "report": stub("report", 0)})
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1, code=42)
    assert raised.value.code == 42
    assert module.caller(x=1, code=0).result == 2


def test_error_case_with_an_empty_error_frame_is_straight_line_code(package) -> None:
    source = _error_case(error_frame_work=False)
    assert source == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .open_ import open_
        from .use import use

        class CallerResult(NamedTuple):
            result: int

        def caller(x: int=0) -> CallerResult:
            open__result = open_(x=x)
            data = 0
            use_result = use(x=open__result.r)
            data = use_result.r
            return CallerResult(result=data)"""
    )
    module = package(source, {"open_": stub("open_", 0), "use": stub("use", 100)})
    assert module.caller(x=1).result == 101


def test_error_case_on_an_error_from_the_caller_runs_the_no_error_frame() -> None:
    source = _error_case(error_frame_work=True, from_caller=True)
    assert "except" not in source
    assert "report" not in source
    assert "use_result = use(" in source


# =============================================================
# Error handler VIs
# =============================================================


# =============================================================
# Which nodes handle an error wire
# =============================================================


def test_merge_errors_generates_no_code() -> None:
    from lvkit.codegen.context import CodeGenContext
    from lvkit.codegen.nodes import primitive, subvi

    ctx = CodeGenContext()
    merge = PrimitiveNode(
        id="m", vi_path="x.vi", name="Merge Errors", node_type="prim", prim_id=2147
    )
    fragment = primitive.generate(merge, ctx)
    assert (fragment.statements, fragment.bindings) == ([], {})
    wrapper = call("w", "Merge Errors.vi", [], [])
    fragment = subvi.generate(wrapper, ctx)
    assert (fragment.statements, fragment.bindings, fragment.imports) == ([], {}, set())


def test_error_code_descriptions_are_looked_up_at_generation_time() -> None:
    assert describe_error_code(UNSPECIFIED) == "Unspecified error."
    assert describe_error_code(999999999) is None


def test_clear_errors_clears_a_labview_error_not_a_python_exception(package) -> None:
    module = package(
        _clear_chain(),
        {
            "step_a": """
                def step_a(x=0):
                    raise FileNotFoundError("missing")
            """,
            "step_b": stub("step_b"),
        },
    )
    with pytest.raises(FileNotFoundError):
        module.caller(x=1)


def _merge_node() -> PrimitiveNode:
    return PrimitiveNode(
        id="M",
        vi_path="caller.vi",
        name="Merge Errors",
        node_type="prim",
        prim_id=2147,
        terminals=[
            term("m_in0", "input", 0, "error in 0", ERR),
            term("m_in1", "input", 1, "error in 1", ERR),
            term("m_out", "output", 2, "error out", ERR),
        ],
    )


def _clear_beside_a_merge() -> str:
    """``Step A`` forks into two branches, ``Step B`` and ``Step C``. The error of
    ``Step B`` goes on to the VI's error out through Merge Errors, and also through
    Clear Errors to ``Step D``, which runs whatever happened."""
    return diagram(
        [
            step("A", "Step A.vi"),
            step("B", "Step B.vi"),
            step("S", "Step C.vi"),
            _clear(),
            step("D", "Step D.vi"),
            _merge_node(),
        ],
        [
            ("x", "A_x"),
            ("x", "B_x"),
            ("x", "S_x"),
            ("x", "D_x"),
            ("A_eout", "B_ein"),
            ("A_eout", "S_ein"),
            ("B_eout", "c_ein"),
            ("B_eout", "m_in0"),
            ("S_eout", "m_in1"),
            ("c_eout", "D_ein"),
            ("m_out", "eout"),
            ("D_r", "result"),
        ],
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )


def test_clear_errors_beside_a_merge_runs_its_branch_and_the_error_reaches_the_caller(
    package,
) -> None:
    source = _clear_beside_a_merge()
    assert source == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError
        from .step_a import step_a
        from .step_b import step_b
        from .step_c import step_c
        from .step_d import step_d
        import concurrent.futures

        class CallerResult(NamedTuple):
            result: int

        def caller(x: int=0) -> CallerResult:
            _held_error = None
            try:
                step_a_result = step_a(x=x)
                with concurrent.futures.ThreadPoolExecutor() as _executor:

                    def _branch_0():
                        step_b_result = step_b(x=x)
                        return step_b_result
                    _f0 = _executor.submit(_branch_0)

                    def _branch_1():
                        step_c_result = step_c(x=x)
                        return step_c_result
                    _f1 = _executor.submit(_branch_1)
                try:
                    step_b_result = _f0.result()
                except LabVIEWError as e:
                    _held_error = _held_error or e
                    step_b_result = None
                try:
                    step_c_result = _f1.result()
                except LabVIEWError as e:
                    _held_error = _held_error or e
                    step_c_result = None
            except LabVIEWError:
                pass
            step_d_result = step_d(x=x)
            if _held_error:
                raise _held_error
            return CallerResult(result=step_d_result.r)"""
    )
    stubs = {
        "step_a": stub("step_a"),
        "step_b": """
            from .errors import LabVIEWError
            def step_b(x=0):
                raise LabVIEWError(code=9)
        """,
        "step_c": stub("step_c"),
        "step_d": """
            from types import SimpleNamespace
            calls = []
            def step_d(x=0):
                calls.append(x)
                return SimpleNamespace(r=x)
        """,
    }
    module = package(source, stubs)
    with pytest.raises(errors_of(module)):
        module.caller(x=5)
    step_d = importlib.import_module(module.__name__.rsplit(".", 1)[0] + ".step_d")
    assert step_d.calls == [5]


def test_clear_errors_with_a_specific_code_raises_where_it_would_run() -> None:
    source = diagram(
        [step("A", "Step A.vi"), _clear()],
        [
            ("x", "A_x"),
            ("A_eout", "c_ein"),
            ("seven", "c_code"),
            ("c_eout", "eout"),
        ],
        inputs=[X_IN],
        outputs=[ERR_OUT],
        constants=[Constant(id="seven", value="7", lv_type=INT)],
    )
    assert (
        "raise NotImplementedError("
        "'Clear Errors with a specific error code is not generated yet\\n"
        "gap kind: unsupported_node\\nVI: caller.vi\\nnode: Clear Errors.vi')"
    ) in source


def test_unbundling_an_error_outside_a_handler_reads_the_no_error_cluster() -> None:
    reader = PrimitiveNode(
        id="U",
        vi_path="caller.vi",
        name="Unbundle",
        node_type="nMux",
        terminals=[
            Terminal(
                id="u_agg",
                direction="input",
                index=0,
                name="error",
                lv_type=ERR,
                nmux_role="agg",
            ),
            Terminal(
                id="u_status",
                direction="output",
                index=1,
                name="status",
                lv_type=BOOL,
                nmux_role="list",
                nmux_field_index=0,
            ),
        ],
    )
    source = diagram(
        [step("A", "Step A.vi"), reader],
        [("x", "A_x"), ("A_eout", "u_agg"), ("u_status", "failed")],
        inputs=[X_IN],
        outputs=[term("failed", "input", 0, "failed", BOOL)],
    )
    assert "return CallerResult(failed=False)" in source


def test_only_a_vi_with_merge_errors_holds_its_errors() -> None:
    from lvkit.codegen.error_handler import needs_error_handling

    graph = InMemoryVIGraph()
    ctx = CodeGenContext(graph=graph, vi_name="test.vi")
    plain = [step("A", "Step A.vi")]
    with_merge = [*plain, _merge_node()]
    register_nodes(graph, with_merge)
    assert needs_error_handling(plain, ctx) is False
    assert needs_error_handling(with_merge, ctx) is True
