"""Global variable nodes: a ``gRef`` that carries a connector type names a control
of a Global VI, not of the calling VI."""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

from lvkit.codegen.ast_utils import parameter_names
from lvkit.codegen.builder import build_module
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import global_variable as global_variable_codegen
from lvkit.extractor import extract_vi_xml
from lvkit.graph import InMemoryVIGraph, load_vi_by_path
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import (
    GlobalVariableNode,
    GlobalVIRef,
    LocalVariableNode,
    PrimitiveNode,
    VINode,
    WireEnd,
)
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.load_mode import LoadMode
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.parser import parse_vi
from lvkit.parser.models import ParsedFrontPanel
from lvkit.parser.node_types import GRefNode
from tests.helpers import register_nodes

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
ICON = SAMPLES / "ni-labview-icon-editor"
LV_ICON = ICON / "resource/plugins/lv_icon.vi"
DATA_VI = ICON / "resource/plugins/NIIconEditor/Global Variables/Data.vi"
VALIDATE_ENUM = (
    SAMPLES
    / "measurement-plugin-labview/Source/Runtime/Measurements/Helpers"
    / "Validate Enum Strings.vi"
)
TRACE = (
    SAMPLES
    / "actor-framework/Core/ActorFramework/tracing/Trace Generation"
    / "Generate Trace for Message.vi"
)
LOAD_TEMPLATES = (
    ICON / "resource/plugins/NIIconEditor/Miscellaneous/Graphics/LoadTemplates.vi"
)


def _load(vi: Path, root: Path):
    return load_vi_by_path(vi, LoadMode.MINIMAL, search_paths=[root], layout=False)


def _globals(graph, key) -> list[GlobalVariableNode]:
    return [n for n in graph.iter_nodes(key) if isinstance(n, GlobalVariableNode)]


def test_lv_icon_global_variables_name_the_global_vi_and_control() -> None:
    graph, key = _load(LV_ICON, ICON)
    nodes = _globals(graph, key)
    assert len(nodes) == 4
    assert {(n.global_vi.qualified_name, n.control_name) for n in nodes} == {
        ("Data.vi", "Debug")
    }
    assert {n.param_idx for n in nodes} == {6}
    assert sorted(n.is_write for n in nodes) == [False, False, False, True]
    assert nodes[0].global_vi.path_tokens == (
        "",
        "NIIconEditor",
        "Global Variables",
        "Data.vi",
    )
    # none of them is wired to a control of lv_icon.vi itself
    for n in nodes:
        assert not any(
            graph.incoming_edges(t.id) and graph.get_graph_node(key) is None
            for t in n.terminals
        )


def test_lv_icon_lvnet_lists_the_global_and_keeps_its_real_locals() -> None:
    graph, key = _load(LV_ICON, ICON)
    text = render_lvnet(build_netlist_from_graph(graph, key), verbose=True)
    assert "global-variable Debug_18698 : read Data.vi::Debug" in text
    assert 'global-variable Debug_19484 : write Data.vi::Debug = "False"' in text
    assert "    global  Data.vi" in text
    assert not any(
        isinstance(n, LocalVariableNode) and n.control_name == "Debug"
        for n in graph.iter_nodes(key)
    )
    bd, fp, main_xml = extract_vi_xml(LV_ICON)
    refs = [
        n
        for n in parse_vi(bd_xml=bd, fp_xml=fp, main_xml=main_xml).block_diagram.nodes
        if isinstance(n, GRefNode)
    ]
    assert len([n for n in refs if n.is_global_variable]) == 4
    assert len([n for n in refs if not n.is_global_variable]) == 8


def test_global_vi_is_a_dependency() -> None:
    graph, key = _load(LV_ICON, ICON)
    (global_key, _) = graph.global_vi_control(_globals(graph, key)[0])
    assert global_key in graph.get_vi_dependencies(key)
    assert isinstance(graph.get_graph_node(global_key), VINode)


def test_decoded_link_table_caller_resolves_control_by_index() -> None:
    graph, key = _load(VALIDATE_ENUM, SAMPLES / "measurement-plugin-labview")
    (node,) = _globals(graph, key)
    assert node.global_vi.qualified_name == "Error Strings Global Internal.vi"
    assert (node.param_idx, node.control_name) == (20, "5070")
    assert graph.global_vi_key(node) in graph.get_vi_dependencies(key)


def test_actor_framework_caller_resolves_control_by_index() -> None:
    graph, key = _load(TRACE, SAMPLES / "actor-framework")
    by_idx = {n.param_idx: n.control_name for n in _globals(graph, key)}
    assert by_idx[7] == "to"
    assert {n.global_vi.qualified_name for n in _globals(graph, key)} == {
        "Localized Strings.vi"
    }


def test_global_vi_interface_is_its_controls_in_front_panel_order() -> None:
    graph, key = _load(DATA_VI, ICON)
    vi_node = graph.get_graph_node(key)
    assert isinstance(vi_node, VINode)
    assert [t.name for t in vi_node.terminals] == [
        "Framework Templates",
        "Layer Clust",
        "Template Color",
        "Layer Color",
        "All Templates",
        "All Glyphs",
        "Debug",
    ]
    assert all(not t.is_public and t.index is None for t in vi_node.terminals)
    ctx = graph.get_vi_context(key)
    assert len(ctx.off_pane_inputs) == 7 and not ctx.inputs


def test_every_global_variable_node_in_the_corpus_resolves_its_global_vi() -> None:
    callers = Path(__file__).with_name("global_variable_callers.txt")
    total = resolved = 0
    for line in callers.read_text().splitlines():
        vi = Path(line.strip())
        if not vi.exists():
            continue
        bd, fp, main_xml = extract_vi_xml(vi)
        parsed = parse_vi(bd_xml=bd, fp_xml=fp, main_xml=main_xml)
        for node in parsed.block_diagram.nodes:
            if isinstance(node, GRefNode) and node.is_global_variable:
                total += 1
                ref = parsed.metadata.iuse_refs.get(node.uid)
                resolved += ref is not None and bool(ref.qualified_name)
        del parsed
    assert total == resolved == 159


def test_ordinary_local_with_connector_type_zero_stays_local() -> None:
    vi = (
        SAMPLES
        / "JKI-EasyXML/Source/Options API/Private"
        / "Read VI Hierarchy on Disk Prototype.vi"
    )
    if not vi.exists():
        pytest.skip("sample absent")
    bd, fp, main_xml = extract_vi_xml(vi)
    refs = [
        n
        for n in parse_vi(bd_xml=bd, fp_xml=fp, main_xml=main_xml).block_diagram.nodes
        if isinstance(n, GRefNode)
    ]
    assert refs and not any(n.is_global_variable for n in refs)


class _AnyResult:
    """A subVI stand-in result: every field reads as None."""

    def __getattr__(self, name: str) -> None:
        return None


def _import_generated(name: str, source: str, package: types.ModuleType):
    module = types.ModuleType(f"{package.__name__}.{name}")
    module.__package__ = package.__name__
    sys.modules[module.__name__] = module
    exec(compile(source, name, "exec"), module.__dict__)
    setattr(package, name, module)
    return module


def test_generated_caller_and_global_module_share_state() -> None:
    graph, key = _load(LOAD_TEMPLATES, ICON)
    caller_src = build_module(
        graph.get_vi_context(key), key, graph=graph, soft_unresolved=True
    )
    (global_key, _) = graph.global_vi_control(_globals(graph, key)[0])
    global_src = build_module(graph.get_vi_context(global_key), global_key, graph=graph)
    assert "from . import data as data_global" in caller_src
    assert "data_global.all_templates" in caller_src

    package = types.ModuleType("gvpkg")
    package.__path__ = []  # type: ignore[attr-defined]
    sys.modules["gvpkg"] = package
    try:
        data = _import_generated("data", global_src, package)
        assert data.all_templates == "All Templates"
        assert data.debug is False

        seen: list[object] = []

        def stub(**kw):
            return _AnyResult()

        def buildcategories(**kw):
            seen.append(kw.get("main_item"))
            return _AnyResult()

        for sub in (
            "read_data_from_caller",
            "specify_path_enum",
            "read_glyphs_from_file",
            "buildcategories",
        ):
            m = types.ModuleType(f"gvpkg.{sub}")
            setattr(m, sub, buildcategories if sub == "buildcategories" else stub)
            sys.modules[m.__name__] = m
            setattr(package, sub, m)
        caller = _import_generated("loadtemplates", caller_src, package)
        data.all_templates = "Changed by another VI"
        data_in = types.SimpleNamespace(
            ref=types.SimpleNamespace(templates_tree=None, ie_vi_ref=None)
        )
        caller.loadtemplates(data_in=data_in)
        assert seen == ["Changed by another VI"]
    finally:
        for name in [n for n in sys.modules if n.startswith("gvpkg")]:
            del sys.modules[name]


def test_generated_write_assigns_the_global_module_attribute() -> None:
    graph, key = _load(LV_ICON, ICON)
    write = next(n for n in _globals(graph, key) if n.is_write)
    ctx = CodeGenContext.from_vi_context(graph.get_vi_context(key), graph=graph)
    ctx.vi_name = key
    fragment = global_variable_codegen.generate(write, ctx)
    (stmt,) = fragment.statements
    ast.fix_missing_locations(stmt)
    assert ast.unparse(stmt) == "data_global.debug = False"
    assert fragment.imports == {"from . import data as data_global"}

    global_key, control = graph.global_vi_control(write)
    data = types.SimpleNamespace(debug=True)
    exec(compile(ast.Module([stmt], []), "w", "exec"), {"data_global": data})
    assert data.debug is False
    names = parameter_names(graph.get_graph_node(global_key).terminals)
    assert names[control.id] == "debug"


def test_global_module_keeps_cluster_saved_defaults() -> None:
    graph, key = _load(DATA_VI, ICON)
    source = build_module(graph.get_vi_context(key), key, graph=graph)
    namespace: dict[str, object] = {}
    exec(compile(source, "data", "exec"), namespace)
    # An anonymous cluster is a positional tuple, in field order: the saved
    # defaults of Data.vi's two cluster controls.
    assert namespace["framework_templates"] == (0, 1, 5, 6)
    assert namespace["layer_clust"] == (1, 1, 4, 1)
    assert namespace["template_color"] == 12303291
    assert namespace["all_templates"] == "All Templates"


# --- Synthetic Global VI, its callers, and the module they share -------------

_GLOBAL_KEY = "Counter.vi"
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(
            name="status",
            type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
        ),
        ClusterField(name="code", type=_I32),
        ClusterField(
            name="source",
            type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
        ),
    ],
)


def _control(uid: str, name: str, lv_type: LVType, default: str | None) -> FPTerminal:
    return FPTerminal(
        id=f"fp_{uid}",
        index=None,
        direction="output",
        name=name,
        lv_type=lv_type,
        default_value=default,
        is_public=False,
        fp_dco_uid=uid,
    )


def _synthetic_global_vi() -> VINode:
    """A Global VI whose controls are a count, an error cluster and a cluster."""
    controls = [
        _control("d0", "Count", _I32, "5"),
        _control("d1", "error out", _ERROR, None),
        _control(
            "d2",
            "Settings",
            LVType(
                kind=LVTypeKind.CLUSTER,
                fields=[
                    ClusterField(name="Rows", type=_I32),
                    ClusterField(name="Cols", type=_I32),
                ],
            ),
            "{'Rows': 3, 'Cols': 4}",
        ),
    ]
    return VINode(
        id=_GLOBAL_KEY,
        vi_path=_GLOBAL_KEY,
        name=_GLOBAL_KEY,
        terminals=controls,
        front_panel=ParsedFrontPanel(
            controls=[], panel_bounds=(0, 0, 0, 0), dco_order=["d0", "d1", "d2"]
        ),
    )


def _synthetic_graph(caller_nodes: list, caller_key: str) -> InMemoryVIGraph:
    graph = InMemoryVIGraph()
    global_vi = _synthetic_global_vi()
    graph._graph.add_node(_GLOBAL_KEY, node=global_vi)
    graph._dep_graph.add_node(_GLOBAL_KEY, name=_GLOBAL_KEY, qname=_GLOBAL_KEY)
    register_nodes(graph, caller_nodes, vi_name=caller_key)
    return graph


def _global_node(
    node_id: str, param_idx: int, control: str, *, is_write: bool, caller: str
) -> GlobalVariableNode:
    return GlobalVariableNode(
        id=node_id,
        vi_path=caller,
        name=control,
        terminals=[
            Terminal(
                id=f"{node_id}_t",
                index=0,
                direction="input" if is_write else "output",
            )
        ],
        global_vi=GlobalVIRef(qualified_name=_GLOBAL_KEY, path_tokens=(_GLOBAL_KEY,)),
        control_name=control,
        param_idx=param_idx,
        is_write=is_write,
    )


def _caller_function(
    graph: InMemoryVIGraph, node: GlobalVariableNode, caller: str, *, value: str | None
) -> str:
    """Source of a one-node VI function: it writes ``value`` to, or returns, the
    global the node names."""
    ctx = CodeGenContext(graph=graph)
    ctx.vi_name = caller
    if node.is_write:
        source = PrimitiveNode(
            id=f"{node.id}_src",
            vi_path=caller,
            name="source",
            terminals=[Terminal(id=f"{node.id}_src_t", index=0, direction="output")],
        )
        register_nodes(graph, [source], vi_name=caller)
        graph._graph.add_edge(
            source.id,
            node.id,
            source=WireEnd(terminal_id=source.terminals[0].id, node_id=source.id),
            dest=WireEnd(terminal_id=node.terminals[0].id, node_id=node.id),
        )
        ctx.bind(source.terminals[0].id, str(value))
    fragment = global_variable_codegen.generate(node, ctx)
    body = [*fragment.statements]
    if not node.is_write:
        returned = fragment.bindings[node.terminals[0].id]
        body.append(ast.Return(value=ast.Name(id=returned, ctx=ast.Load())))
    func = ast.FunctionDef(
        name=caller,
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=body or [ast.Pass()],
        decorator_list=[],
    )
    imports = "\n".join(sorted(fragment.imports))
    module = ast.Module(body=[func], type_ignores=[])
    return f"{imports}\n{ast.unparse(ast.fix_missing_locations(module))}"


def test_global_vi_module_keeps_saved_values_and_leaves_out_error_cluster() -> None:
    from lvkit.codegen.global_module import build_global_module

    source = build_global_module(_synthetic_global_vi())
    module = types.ModuleType("counter")
    exec(compile(source, "counter", "exec"), module.__dict__)
    assert module.count == 5
    assert module.settings == (3, 4)
    assert not hasattr(module, "error_out")


def test_global_variable_node_on_an_error_cluster_control_generates_nothing() -> None:
    node = _global_node("ge", 1, "error out", is_write=False, caller="reader")
    graph = _synthetic_graph([node], "reader")
    ctx = CodeGenContext(graph=graph)
    ctx.vi_name = "reader"
    fragment = global_variable_codegen.generate(node, ctx)
    assert fragment.statements == [] and fragment.imports == set()


def test_one_vi_writes_a_global_and_another_reads_it_back() -> None:
    writer_node = _global_node("gw", 0, "Count", is_write=True, caller="writer")
    reader_node = _global_node("gr", 0, "Count", is_write=False, caller="reader")
    graph = _synthetic_graph([writer_node, reader_node], "writer")
    writer_src = _caller_function(graph, writer_node, "writer", value="42")
    reader_src = _caller_function(graph, reader_node, "reader", value=None)
    assert "counter_global.count = 42" in writer_src
    assert "return count" in reader_src

    from lvkit.codegen.global_module import build_global_module

    package = types.ModuleType("gvsynth")
    package.__path__ = []  # type: ignore[attr-defined]
    sys.modules["gvsynth"] = package
    try:
        counter = _import_generated(
            "counter", build_global_module(_synthetic_global_vi()), package
        )
        writer = _import_generated("writer_vi", writer_src, package)
        reader = _import_generated("reader_vi", reader_src, package)
        assert reader.reader() == 5
        writer.writer()
        assert counter.count == 42
        assert reader.reader() == 42
    finally:
        for name in [n for n in sys.modules if n.startswith("gvsynth")]:
            del sys.modules[name]


def _flatten(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        out.append(item)
        for frame in item.get("frames", []):
            out.extend(_flatten(frame.get("body", [])))
    return out


def test_describe_lists_the_global_vi_as_a_dependency() -> None:
    graph, key = _load(LV_ICON, ICON)
    text = describe_vi(graph, key)
    section = text.split("## Dependencies")[1].split("\n## ")[0]
    assert "  Data.vi (Global VI)" in section.splitlines()
    assert describe_vi(graph, key, verbose=True).count("  Data.vi (Global VI)") == 1


def test_describe_json_shows_the_global_vi_verbose_and_not() -> None:
    graph, key = _load(LV_ICON, ICON)
    module = build_netlist_from_graph(graph, key)
    plain = netlist_to_dict(module)
    verbose = netlist_to_dict(module, verbose=True)
    assert "dependencies" not in plain
    for result in (plain, verbose):
        globals_ = [
            i for i in _flatten(result["body"]) if i["kind"] == "global_variable"
        ]
        assert len(globals_) == 4
        assert {(g["global_vi"], g["control"]) for g in globals_} == {
            ("Data.vi", "Debug")
        }
    assert {"kind": "global", "qualified": "Data.vi"}.items() <= next(
        d for d in verbose["dependencies"] if d["kind"] == "global"
    ).items()
