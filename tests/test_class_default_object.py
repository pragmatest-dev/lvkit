"""The default of a class type is the class's default object, wherever LabVIEW
gives a type's default: a skipped node's output, a failed Variant To Data, an
unwired tunnel, a cluster's class field, a class's own private data. Each case is
a handmade project (``tests/class_graphs.py``) generated into a package that is
imported and run; a module-level variable holding a class starts ``None`` and the
function makes the object where it reads it."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from lvkit.codegen import import_cycles
from lvkit.codegen.ast_utils import parameter_default_expr_of_type, type_default_expr
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.type_default import type_default
from lvkit.graph.models import GraphNode, LoopNode, PrimitiveNode, VIContext
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    Terminal,
    Tunnel,
    TunnelMode,
    TunnelTerminal,
)
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import INT, Project, Recipe, class_type, pane, wire
from tests.helpers import error_cluster_type, tunnel_terminals, with_file_keys

BASE = class_type("Base")
GHOST = class_type("Ghost")
ROOT = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="UDClassInst",
    classname="LabVIEW Object",
)
COUNT = ClusterField(name="Count", type=INT)
VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Variant")
ERR = error_cluster_type()
VARIANT_TO_DATA = 8003


def convert_to(owner: LVType) -> Recipe:
    """A VI that converts its variant ``v`` to ``owner`` with Variant To Data
    (the LabVIEW 11+ numbering); its outputs are ``d`` and ``error out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = PrimitiveNode(
            id=f"{vi}:vd",
            vi_path=vi,
            name="Variant To Data",
            prim_id=VARIANT_TO_DATA,
            terminals=[
                Terminal(id=f"{vi}:vd.ein", index=0, direction="input", lv_type=ERR),
                Terminal(id=f"{vi}:vd.eout", index=3, direction="output", lv_type=ERR),
                Terminal(id=f"{vi}:vd.v", index=8, direction="input", lv_type=VARIANT),
                Terminal(id=f"{vi}:vd.t", index=9, direction="input", lv_type=owner),
                Terminal(id=f"{vi}:vd.d", index=11, direction="output", lv_type=owner),
            ],
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "v", VARIANT, "v", 0),
                    pane(
                        vi,
                        "input",
                        "ein",
                        ERR,
                        "error in",
                        1,
                        ParsedWiringRule.RECOMMENDED,
                    ),
                ],
                outputs=[
                    pane(vi, "output", "d", owner, "d", 2),
                    pane(vi, "output", "eout", ERR, "error out", 3),
                ],
                data_flow=[
                    wire(vi, "v", "vd.v"),
                    wire(vi, "ein", "vd.ein"),
                    wire(vi, "vd.d", "d"),
                    wire(vi, "vd.eout", "eout"),
                ],
            ),
            [node],
        )

    return build


def test_a_skipped_node_outputs_the_class_default_object(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Convert", convert_to(BASE))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        base_class, convert = module("base").Base, module("convert").convert
        error = module("errors").LabVIEWError(status=True, code=5, source="x")
        skipped = convert(v=object(), error_in=error)
        assert type(skipped.d) is base_class
        assert skipped.d._Base__count == 0
        assert skipped.error_out is error


def test_a_failed_variant_to_data_gives_the_class_default_object(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Convert", convert_to(BASE))
    package = project.generate()
    with package.use() as module:
        base_class, convert = module("base").Base, module("convert").convert
        failed = convert(v="not an object")
        assert failed.error_out.code == 91
        assert type(failed.d) is base_class
        assert failed.d._Base__count == 0
        mine = base_class()
        assert convert(v=mine).d is mine


def test_a_cluster_with_a_class_field_defaults_that_field_to_the_object(
    tmp_path: Path,
) -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="n", type=INT),
            ClusterField(name="obj", type=BASE),
        ],
    )
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Convert", convert_to(cluster))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        base_class, convert = module("base").Base, module("convert").convert
        error = module("errors").LabVIEWError(status=True, code=5, source="x")
        count, held = convert(v=object(), error_in=error).d
        assert count == 0
        assert type(held) is base_class


def test_a_class_that_is_not_loaded_is_a_gap_not_none(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Run", loop_with_class_state(GHOST, "last value"))
    package = project.generate()
    assert [g.message for g in package.gaps if "Ghost.lvclass" in g.message]
    assert "Ghost.lvclass names a class that is not loaded" in package.sources["run"]


def test_the_other_defaults_are_unchanged() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Pair.ctl",
        fields=[
            ClusterField(name="a", type=INT),
            ClusterField(
                name="b",
                type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
            ),
        ],
    )
    anonymous = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=INT), ClusterField(name="o", type=BASE)],
    )
    ctx = CodeGenContext()
    assert ast.unparse(type_default(ctx, INT)) == "0"
    assert ast.unparse(type_default(ctx, cluster)) == "SimpleNamespace(a=0, b='')"
    assert ast.unparse(type_default_expr(cluster)) == "SimpleNamespace(a=0, b='')"
    assert ast.unparse(type_default_expr(anonymous)) == "(0, None)"
    assert ast.unparse(type_default_expr(BASE)) == "None"
    assert ast.unparse(parameter_default_expr_of_type(BASE)) == "None"
    assert ast.unparse(parameter_default_expr_of_type(INT)) == "0"


def loop_with_class_state(owner: LVType, tunnel_kind: str) -> Recipe:
    """A for loop (``n`` iterations) with a tunnel of the class ``owner``:
    ``tunnel_kind`` is a "shift register" nothing initializes (its value goes
    round the loop unchanged), or a "last value" tunnel that carries the input
    ``obj`` out; ``out`` is the loop's output."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        shift = tunnel_kind == "shift register"
        tunnels = [
            Tunnel(
                outer_terminal_uid=f"{vi}:n_o",
                inner_terminal_uid=f"{vi}:n_i",
                tunnel_type="lMax",
            )
        ]
        if shift:
            tunnels += [
                Tunnel(
                    outer_terminal_uid=f"{vi}:s_o",
                    inner_terminal_uid=f"{vi}:s_i",
                    tunnel_type="lSR",
                ),
                Tunnel(
                    outer_terminal_uid=f"{vi}:r_o",
                    inner_terminal_uid=f"{vi}:r_i",
                    tunnel_type="rSR",
                ),
            ]
        else:
            tunnels.append(
                Tunnel(
                    outer_terminal_uid=f"{vi}:o_o",
                    inner_terminal_uid=f"{vi}:o_i",
                    tunnel_type="lpTun",
                    mode=TunnelMode.PASSTHROUGH,
                )
            )
        terminals = tunnel_terminals(tunnels)
        if not shift:
            terminals += [
                TunnelTerminal(
                    id=f"{vi}:r_i",
                    index=20,
                    direction="input",
                    boundary="inner",
                    tunnel_type="lpTun",
                    paired_id=f"{vi}:r_o",
                    mode=TunnelMode.LAST_VALUE,
                ),
                TunnelTerminal(
                    id=f"{vi}:r_o",
                    index=21,
                    direction="output",
                    boundary="outer",
                    tunnel_type="lpTun",
                    paired_id=f"{vi}:r_i",
                    mode=TunnelMode.LAST_VALUE,
                ),
            ]
        for t in terminals:
            t.lv_type = INT if t.id.endswith(("n_o", "n_i")) else owner
        loop = LoopNode(
            id=f"{vi}:for",
            vi_path=vi,
            node_type="forLoop",
            loop_type="forLoop",
            terminals=terminals,
        )
        data_flow = [
            wire(vi, "n", "n_o"),
            wire(vi, "r_i", "r_o"),
            wire(vi, "r_o", "out"),
        ]
        if shift:
            data_flow.append(wire(vi, "s_i", "r_i"))
        else:
            data_flow += [
                wire(vi, "obj", "o_o"),
                wire(vi, "o_o", "o_i"),
                wire(vi, "o_i", "r_i"),
            ]
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", owner, "obj", 0),
                    pane(vi, "input", "n", INT, "n", 1),
                ],
                outputs=[pane(vi, "output", "out", owner, "out", 2)],
                data_flow=data_flow,
            ),
            [loop],
        )

    return build


def test_an_unwired_loop_output_tunnel_is_the_class_default_object(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Run", loop_with_class_state(BASE, "last value"))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        base_class, run = module("base").Base, module("run").run
        mine = base_class()
        empty = run(obj=mine, n=0).out
        assert type(empty) is base_class and empty is not mine
        assert empty._Base__count == 0
        assert run(obj=mine, n=2).out is mine


def test_a_labview_object_loop_tunnel_default_is_a_plain_object(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Run", loop_with_class_state(ROOT, "last value"))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        assert type(module("run").run(obj=1, n=0).out) is object


def test_a_shift_register_holding_a_class_starts_none_and_is_made_where_read(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Run", loop_with_class_state(BASE, "shift register"))
    package = project.generate()
    assert package.gaps == []
    source = package.sources["run"]
    state = re.search(r"^(_lv_state_\w+) = None$", source, re.M)
    assert state is not None
    assert (
        f"    if {state.group(1)} is None:\n        {state.group(1)} = Base()\n"
        in source
    )
    with package.use() as module:
        base_class, run = module("base").Base, module("run").run
        first = run(obj=base_class(), n=1).out
        assert type(first) is base_class and first._Base__count == 0
        assert type(run(obj=base_class(), n=0).out) is base_class


def test_a_module_on_an_import_cycle_imports_and_runs_a_class_state(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    free = project.add_vi("Run", loop_with_class_state(BASE, "shift register"))
    method = project.add_method("Base", "Probe", convert_to(BASE), static=True)
    graph = project.graph
    graph._dep_graph.add_edge(method, free)  # noqa: SLF001
    graph._dep_graph.add_edge(free, method)  # noqa: SLF001
    assert import_cycles.in_import_cycle(graph, free)
    package = project.generate()
    source = package.sources["run"]
    head, body = source.split("def run(")
    assert not re.search(r"^from \.base import Base$", head, re.M)
    assert "    from .base import Base\n" in body
    with package.use() as module:
        base_class, run = module("base").Base, module("run").run
        out = run(obj=base_class(), n=1).out
        assert type(out) is base_class and out._Base__count == 0
        assert type(run(obj=base_class(), n=0).out) is base_class


def test_a_class_field_of_a_class_starts_at_the_default_object(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Holder", [ClusterField(name="Inner", type=BASE)])
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        held = module("holder").Holder()
        assert type(held._Holder__inner) is module("base").Base
        assert held._Holder__inner is not module("holder").Holder()._Holder__inner


def test_a_class_field_on_an_import_cycle_imports_the_class_in_init(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Holder", [ClusterField(name="Inner", type=BASE)])
    holder_method = project.add_method("Holder", "Probe", convert_to(BASE), static=True)
    base_method = project.add_method("Base", "Probe", convert_to(BASE), static=True)
    graph = project.graph
    graph._dep_graph.add_edge(holder_method, base_method)  # noqa: SLF001
    graph._dep_graph.add_edge(base_method, holder_method)  # noqa: SLF001
    assert import_cycles.in_import_cycle(graph, project.classes["Holder"])
    package = project.generate()
    assert package.gaps == []
    init = package.sources["holder"].split("def __init__")[1].split("\n    def ")[0]
    assert "from .base import Base" in init
    with package.use() as module:
        held = module("holder").Holder()
        assert type(held._Holder__inner) is module("base").Base


def test_an_unwired_case_output_tunnel_is_pre_declared_as_the_class_object(
    tmp_path: Path,
) -> None:
    from lvkit.codegen.nodes.frame_structure import pre_declare_outputs
    from lvkit.graph.models import CaseStructureNode
    from lvkit.pipeline import create_import_resolver

    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    free = project.add_vi("Run", loop_with_class_state(BASE, "last value"))
    (tmp_path / "pkg").mkdir()
    paths = {
        project.classes["Base"]: tmp_path / "pkg" / "base.py",
        free: tmp_path / "pkg" / "run.py",
    }
    resolver = create_import_resolver(
        "pkg",
        tmp_path / "pkg",
        paths,
        paths[free],
        caller_key=free,
        graph=project.graph,
    )
    ctx = CodeGenContext(graph=project.graph, import_resolver=resolver)
    case = CaseStructureNode(
        id="case",
        vi_path=free,
        node_type="caseStruct",
        terminals=[
            TunnelTerminal(
                id="case:out",
                index=0,
                direction="output",
                boundary="outer",
                tunnel_type="lpTun",
                lv_type=with_file_keys(BASE, {"Base.lvclass": project.classes["Base"]}),
            )
        ],
    )
    declared = pre_declare_outputs(case, {"case:out": "case_output"}, ctx)
    module = ast.fix_missing_locations(ast.Module(body=declared, type_ignores=[]))
    assert ast.unparse(module) == "case_output = Base()"
    assert "from .base import Base" in ctx.imports


def test_a_shared_variable_holding_a_class_starts_none_and_is_made_by_the_reader() -> (
    None
):
    from lvkit.codegen.shared_variable_module import PackageSharedVariables
    from lvkit.graph.package_queries import SharedVariableUse

    uses = [
        SharedVariableUse(
            node_id="n",
            vi_key="vi",
            library_key="lib.lvlib",
            library_name="Lib",
            variable="Obj",
            value_type=BASE,
        ),
        SharedVariableUse(
            node_id="m",
            vi_key="vi",
            library_key="lib.lvlib",
            library_name="Lib",
            variable="Count",
            value_type=INT,
        ),
    ]
    source = PackageSharedVariables.from_uses(uses).build_module()
    assert source is not None
    assert "lib_obj: Any = None" in source
    assert "lib_count: Any = 0" in source


def test_default_if_unset_is_only_for_a_type_that_holds_a_class(
    tmp_path: Path,
) -> None:
    from lvkit.codegen.type_default import default_if_unset, state_seed

    ctx = CodeGenContext()
    assert default_if_unset("x", INT, ctx, "x") == []
    anonymous = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=INT), ClusterField(name="o", type=BASE)],
    )
    assert ast.unparse(state_seed(anonymous)) == "None"
    assert ast.unparse(state_seed(INT)) == "0"
    (statement,) = default_if_unset("x", BASE, ctx, "the Feedback Node's value")
    module = ast.fix_missing_locations(ast.Module(body=[statement], type_ignores=[]))
    text = ast.unparse(module)
    assert text.startswith("if x is None:\n    raise NotImplementedError(")
    assert "Base.lvclass names a class that is not loaded" in text
    assert [g.node_name for g in ctx.gaps] == ["the Feedback Node's value"]
