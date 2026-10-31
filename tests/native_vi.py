"""Build a one-function VI from typed controls and graph nodes, generate its
Python and run it: the harness for tests that pin the exact generated source of
a construct and execute it."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors_module import ERRORS_MODULE_SOURCE
from lvkit.codegen.gaps import Gap
from lvkit.graph.models import Constant, PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal, ValueNotStored
from tests.helpers import build_graph


def scalar(underlying: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)


def array(underlying: str | LVType, rank: int = 1) -> LVType:
    element = scalar(underlying) if isinstance(underlying, str) else underlying
    return LVType(kind=LVTypeKind.ARRAY, element_type=element, dimensions=rank)


def node(
    node_id: str,
    prim_id: int | None,
    terminals: list[tuple[int, str, LVType | None]],
    node_type: str = "prim",
) -> PrimitiveNode:
    """A primitive whose terminals are ``(index, direction, type)``; the
    terminal ids are ``<node_id>:<index>``."""
    return PrimitiveNode(
        id=node_id,
        vi_path="Native Test.vi",
        name=node_id,
        prim_id=prim_id,
        node_type=node_type,
        terminals=[
            Terminal(id=f"{node_id}:{i}", index=i, direction=d, lv_type=t)
            for i, d, t in terminals
        ],
    )


# The package a generated module runs in, holding the ``errors`` module every
# generated package has (``from .errors import LabVIEWError``).
_PACKAGE = "native_package"


def _install_package() -> None:
    if f"{_PACKAGE}.errors" in sys.modules:
        return
    package = ModuleType(_PACKAGE)
    package.__path__ = []
    errors = ModuleType(f"{_PACKAGE}.errors")
    sys.modules[_PACKAGE] = package
    sys.modules[f"{_PACKAGE}.errors"] = errors
    exec(compile(ERRORS_MODULE_SOURCE, "errors.py", "exec"), errors.__dict__)


@dataclass
class Generated:
    source: str
    module: ModuleType
    function: object


def generate(
    inputs: dict[str, LVType],
    outputs: dict[str, LVType],
    nodes: list[PrimitiveNode],
    wires: list[tuple[str, str]],
    constants: dict[str, tuple[object, LVType]] | None = None,
    gaps: list[Gap] | None = None,
) -> Generated:
    """``inputs`` / ``outputs`` are the VI's controls and indicators by name;
    ``wires`` connect terminal ids (``in:<name>``, ``out:<name>``,
    ``<node_id>:<index>``); ``constants`` maps a terminal id to the (value, type)
    of a diagram constant wired from it."""
    controls = [
        FPTerminal(
            id=f"fp:{name}",
            index=i,
            direction="input",
            name=name,
            is_public=True,
            is_indicator=False,
            lv_type=lv_type,
        )
        for i, (name, lv_type) in enumerate(inputs.items())
    ]
    indicators = [
        FPTerminal(
            id=f"fp:{name}",
            index=i,
            direction="output",
            name=name,
            is_public=True,
            is_indicator=True,
            lv_type=lv_type,
        )
        for i, (name, lv_type) in enumerate(outputs.items())
    ]
    constant_nodes = [
        Constant(
            id=terminal,
            value="" if isinstance(value, ValueNotStored) else value,
            lv_type=lv_type,
            unwritable=value if isinstance(value, ValueNotStored) else None,
        )
        for terminal, (value, lv_type) in (constants or {}).items()
    ]
    context = VIContext(
        name="Native Test.vi",
        inputs=controls,
        outputs=indicators,
        constants=constant_nodes,
        data_flow=[Wire.from_terminals(a, b) for a, b in wires],
    )
    graph = build_graph(context, context.name, nodes)
    source = build_module(context, context.name, graph=graph, gaps=gaps)
    namespace: dict = {"__name__": f"{_PACKAGE}.native_test", "__package__": _PACKAGE}
    _install_package()
    exec(compile(source, "native_test.py", "exec"), namespace)
    module = ModuleType("native_test")
    module.__dict__.update(namespace)
    return Generated(source, module, namespace["native_test"])
