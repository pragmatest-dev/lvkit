"""Format String (printf) code generation: a format string that is a wired value
is applied at run time, a literal is inlined."""

from __future__ import annotations

import ast

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import printf
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.models import Terminal
from tests.helpers import register_nodes


def _generate(format_value: str, argument: str) -> str:
    graph = InMemoryVIGraph()
    sources = [
        PrimitiveNode(
            id=f"src{i}",
            vi_path="t.vi",
            name="source",
            terminals=[Terminal(id=f"src{i}_t", index=0, direction="output")],
        )
        for i in range(2)
    ]
    node = PrimitiveNode(
        id="fmt",
        vi_path="t.vi",
        name="Format Into String",
        terminals=[
            Terminal(id="fmt_in0", index=0, direction="input"),
            Terminal(id="fmt_in1", index=1, direction="input"),
            Terminal(id="fmt_out", index=2, direction="output", name="formatted"),
        ],
    )
    register_nodes(graph, [*sources, node], vi_name="t.vi")
    for src, dst in zip(sources, ("fmt_in0", "fmt_in1"), strict=True):
        graph._graph.add_edge(
            src.id,
            node.id,
            source=WireEnd(terminal_id=src.terminals[0].id, node_id=src.id),
            dest=WireEnd(terminal_id=dst, node_id=node.id),
        )
    ctx = CodeGenContext(graph=graph)
    ctx.bind("src0_t", format_value)
    ctx.bind("src1_t", argument)
    fragment = printf.generate(node, ctx)
    module = ast.Module(body=fragment.statements, type_ignores=[])
    return ast.unparse(ast.fix_missing_locations(module))


def test_wired_format_string_is_applied_at_run_time() -> None:
    source = _generate("message", "count")
    namespace: dict[str, object] = {"message": "found %d items", "count": 3}
    exec(compile(source, "printf", "exec"), namespace)
    assert namespace["formatted"] == "found 3 items"


def test_literal_format_string_is_inlined() -> None:
    source = _generate("'found %d items'", "count")
    namespace: dict[str, object] = {"count": 3}
    exec(compile(source, "printf", "exec"), namespace)
    assert namespace["formatted"] == "found 3 items"
