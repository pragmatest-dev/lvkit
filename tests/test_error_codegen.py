"""Error Cluster From Error Code in generated code, and the NI error
descriptions it writes. Each test builds a tiny block diagram in memory and
generates the module."""

from __future__ import annotations

from lvkit.codegen.builder import build_module
from lvkit.codegen.error_codes import describe_error_code
from lvkit.codegen.error_path import ERROR_WIRE_VI_NAMES
from lvkit.graph.models import (
    Constant,
    VIContext,
    VINode,
    Wire,
)
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    Terminal,
)
from tests.helpers import build_graph

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

UNSPECIFIED = -2147467259


def term(tid: str, direction: str, index: int, name: str, lv: LVType) -> Terminal:
    # A control of the VI (the diagram's source) is a Required input.
    rule = 1 if direction == "output" else 0
    return Terminal(
        id=tid,
        direction=direction,
        index=index,
        name=name,
        lv_type=lv,
        wiring_rule=rule,
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


ERR_OUT = term("eout", "input", 1, "error out", ERR)

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
        f"error = LabVIEWError(code={UNSPECIFIED}, message=msg or {description!r}, "
        "status=True)"
    ) in source


def test_error_code_descriptions_are_looked_up_at_generation_time() -> None:
    assert describe_error_code(UNSPECIFIED) == "Unspecified error."
    assert describe_error_code(999999999) is None
