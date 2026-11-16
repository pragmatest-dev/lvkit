"""A VI saved against an older copy of a typedef reads the loaded typedef's
fields: LabVIEW updates the VI's copy to the typedef, the node's field kept by its
position. A copy with a different number of fields is a gap that names both."""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.type_default import type_default
from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import ConstantNode, PrimitiveNode, VIContext, Wire
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from tests.helpers import build_graph, exec_generated

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
TYPEDEF_KEY = "/project/Plugin Paths.ctl"


def _fields(*names: str) -> list[ClusterField]:
    return [ClusterField(name=n, type=STRING) for n in names]


def _saved_copy(*names: str) -> LVType:
    """The cluster a VI saved with its own (here older) copy of the typedef."""
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Plugin Paths.ctl",
        fields=_fields(*names),
        file_key=TYPEDEF_KEY,
    )


def _pane(name: str, direction: str, kind: LVType, index: int = 0) -> FPTerminal:
    return FPTerminal(
        id=f"fp:{name}",
        index=index,
        direction=direction,
        wiring_rule=1 if direction == "input" else 0,
        name=name,
        is_public=True,
        is_indicator=direction == "output",
        lv_type=kind,
    )


LOADED = ("get service descriptor VI path", "get type specializations VI path")


def _unbundle(
    saved: LVType, position: int, loaded: tuple[str, ...] = LOADED
) -> tuple[InMemoryVIGraph, VIContext]:
    node = PrimitiveNode(
        id="unbundle",
        vi_path="Caller.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg", index=0, direction="input", nmux_role="agg", lv_type=saved
            ),
            Terminal(
                id="field",
                index=1,
                direction="output",
                nmux_role="list",
                nmux_field_index=position,
                lv_type=STRING,
            ),
        ],
    )
    context = VIContext(
        name="Caller.vi",
        inputs=[_pane("paths", "input", saved)],
        outputs=[_pane("value", "output", STRING)],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:paths", to_terminal_id="agg"),
            Wire.from_terminals(from_terminal_id="field", to_terminal_id="fp:value"),
        ],
    )
    graph = build_graph(context, context.name, [node])
    graph._dep_graph.add_node(TYPEDEF_KEY, fields=_fields(*loaded))
    return graph, context


def test_the_loaded_typedefs_fields_name_the_saved_copys_positions() -> None:
    saved = _saved_copy(
        "get measurement details VI path", "get type specializations VI path"
    )
    graph, context = _unbundle(saved, 0)
    assert [f.name for f in graph.get_type_fields(saved) or []] == [
        "get service descriptor VI path",
        "get type specializations VI path",
    ]
    assert graph.stale_typedef_copy(saved) is None
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec_generated(source, namespace, context.name)
    loaded = SimpleNamespace(
        get_service_descriptor_vi_path="A.vi", get_type_specializations_vi_path="B.vi"
    )
    assert namespace["caller"](loaded).value == "A.vi"


def test_a_saved_copy_with_another_field_count_is_a_gap_naming_both_lists() -> None:
    saved = _saved_copy("get measurement details VI path", "x", "y")
    graph, context = _unbundle(saved, 0)
    message = graph.stale_typedef_copy(saved)
    assert message is not None
    assert "Plugin Paths.ctl" in message
    assert "[get measurement details VI path, x, y]" in message
    assert (
        "[get service descriptor VI path, get type specializations VI path]" in message
    )
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec_generated(source, namespace, context.name)
    with pytest.raises(NotImplementedError, match="older copy of the typedef"):
        namespace["caller"](SimpleNamespace())


def test_a_cluster_that_is_no_loaded_typedef_keeps_its_own_fields() -> None:
    anonymous = LVType(kind=LVTypeKind.CLUSTER, fields=_fields("a", "b"))
    graph = InMemoryVIGraph()
    assert [f.name for f in graph.get_type_fields(anonymous) or []] == ["a", "b"]
    assert graph.stale_typedef_copy(anonymous) is None
    unloaded = _saved_copy("a", "b")
    assert [f.name for f in graph.get_type_fields(unloaded) or []] == ["a", "b"]
    assert graph.stale_typedef_copy(unloaded) is None


def test_a_cluster_constant_of_a_stale_copy_takes_the_loaded_names() -> None:
    saved = _saved_copy(
        "get measurement details VI path", "get type specializations VI path"
    )
    graph, _context = _unbundle(saved, 0)
    constant = ConstantNode(
        id="c",
        vi_path="Caller.vi",
        value="{'get measurement details VI path': 'A', "
        "'get type specializations VI path': 'B'}",
        lv_type=saved,
    )
    ctx = CodeGenContext(graph=graph)
    expression = ctx.constant_expression(constant)
    value = eval(expression, {"SimpleNamespace": SimpleNamespace})  # noqa: S307
    assert vars(value) == {
        "get_service_descriptor_vi_path": "A",
        "get_type_specializations_vi_path": "B",
    }
    longer = _saved_copy("a", "b", "c")
    with pytest.raises(ConversionGap, match="older copy of the typedef"):
        ctx.constant_expression(
            ConstantNode(id="d", vi_path="Caller.vi", value="{}", lv_type=longer)
        )


def test_the_default_of_a_stale_copy_takes_the_loaded_names() -> None:
    saved = _saved_copy(
        "get measurement details VI path", "get type specializations VI path"
    )
    graph, _context = _unbundle(saved, 0)
    ctx = CodeGenContext(graph=graph)
    expression = ast.unparse(type_default(ctx, saved))
    value = eval(expression, {"SimpleNamespace": SimpleNamespace})  # noqa: S307
    assert list(vars(value)) == [
        "get_service_descriptor_vi_path",
        "get_type_specializations_vi_path",
    ]
    with pytest.raises(ConversionGap, match="older copy of the typedef"):
        type_default(ctx, _saved_copy("a", "b", "c"))


def test_a_decoded_cluster_constant_of_a_stale_copy_takes_the_loaded_names() -> None:
    saved = _saved_copy(
        "get measurement details VI path", "get type specializations VI path"
    )
    graph, _context = _unbundle(saved, 0)
    path = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
    saved.fields = [ClusterField(name=f.name, type=path) for f in saved.fields or []]
    constant = ConstantNode(
        id="c",
        vi_path="Caller.vi",
        value=None,
        structured_value={
            "get measurement details VI path": "A",
            "get type specializations VI path": "B",
        },
        lv_type=saved,
    )
    expression = CodeGenContext(graph=graph).constant_expression(constant)
    assert expression.startswith("SimpleNamespace(get_service_descriptor_vi_path=")
    assert "get_type_specializations_vi_path=" in expression
    assert "get_measurement_details" not in expression


def _run_unbundle(saved: LVType, position: int, loaded: tuple[str, ...], value):
    graph, context = _unbundle(saved, position, loaded)
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec_generated(source, namespace, context.name)
    return namespace["caller"](value).value


def test_a_typedef_that_gained_fields_maps_each_saved_field_by_name() -> None:
    # (a) every saved name exists in the loaded typedef: fields appended after.
    loaded = ("alpha", "beta", "gamma", "delta")
    saved = _saved_copy("alpha", "beta")
    graph, _context = _unbundle(saved, 1, loaded)
    assert graph.stale_typedef_copy(saved) is None
    assert [f.name for f in graph.get_type_fields(saved) or []] == ["alpha", "beta"]
    value = SimpleNamespace(alpha="A", beta="B", gamma="C", delta="D")
    assert _run_unbundle(saved, 1, loaded, value) == "B"
    ctx = CodeGenContext(graph=graph)
    default = eval(  # noqa: S307
        ast.unparse(type_default(ctx, saved)), {"SimpleNamespace": SimpleNamespace}
    )
    assert list(vars(default)) == ["alpha", "beta"]


def test_a_typedef_with_reordered_fields_maps_each_saved_field_by_name() -> None:
    # (a) the same names in another order: by name, not by position.
    loaded = ("beta", "alpha")
    saved = _saved_copy("alpha", "beta")
    graph, _context = _unbundle(saved, 0, loaded)
    assert graph.stale_typedef_copy(saved) is None
    assert [f.name for f in graph.get_type_fields(saved) or []] == ["alpha", "beta"]
    assert _run_unbundle(saved, 0, loaded, SimpleNamespace(alpha="A", beta="B")) == "A"


def test_a_renamed_field_maps_by_position_when_the_counts_match() -> None:
    # (b) a name is missing from the loaded typedef and the counts are equal.
    loaded = ("alpha", "renamed")
    saved = _saved_copy("alpha", "beta")
    graph, _context = _unbundle(saved, 1, loaded)
    assert graph.stale_typedef_copy(saved) is None
    assert [f.name for f in graph.get_type_fields(saved) or []] == list(loaded)
    assert (
        _run_unbundle(saved, 1, loaded, SimpleNamespace(alpha="A", renamed="R")) == "R"
    )


def test_renamed_and_shorter_is_a_gap_naming_both_lists() -> None:
    # (c) a name is missing and the counts differ.
    loaded = ("w", "x", "y", "z")
    saved = _saved_copy("a", "b", "c")
    graph, context = _unbundle(saved, 0, loaded)
    message = graph.stale_typedef_copy(saved)
    assert message is not None
    assert "[a, b, c]" in message
    assert "[w, x, y, z]" in message
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec_generated(source, namespace, context.name)
    with pytest.raises(NotImplementedError, match="older copy of the typedef"):
        namespace["caller"](SimpleNamespace())
