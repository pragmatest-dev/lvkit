"""A typedef of a single value (a U32 control `IEColor.ctl`) is a named type like
any other typedef: every view names it, and its `types :` entry defines it as
the scalar it wraps. Neighbouring cases keep their output."""

from __future__ import annotations

import json

from lvkit.codegen.builder import build_module
from lvkit.codegen.import_resolver import ImportResolver, NameImport
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import VIContext, Wire
from lvkit.graph.netlist import render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.netlist_models import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    NetlistBoundaryInput,
    NetlistModule,
    WiringRequirement,
)
from lvkit.models import ClusterField, EnumValue, FPTerminal, LVType, LVTypeKind
from tests.helpers import build_graph
from tests.test_lvnet_identity import _assert_graph_identity

U32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32")
COLOR = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="NumUInt32",
    typedef_name="IEColor.ctl",
    typedef_path="Controls/IEColor.ctl",
)


def _module(lv_type: LVType) -> NetlistModule:
    token = lv_type.type_descriptor(expand_named=False)
    return NetlistModule(
        vi_name="Check Color.vi",
        inputs=[
            NetlistBoundaryInput(
                name="Color in", type_descriptor=token, lv_type=lv_type
            )
        ],
        outputs=[
            BoundaryOutput(
                name="Color out", type_descriptor=token, source=None, lv_type=lv_type
            )
        ],
        connector_pane=ConnectorPane(
            pattern_id=4815,
            terminals=[
                ConnectorPaneTerminal(
                    name=name,
                    type=token,
                    direction=direction,
                    index=index,
                    wiring_requirement=WiringRequirement.UNKNOWN,
                    default=None,
                    lv_type=lv_type,
                )
                for name, direction, index in (
                    ("Color in", "input", 0),
                    ("Color out", "output", 1),
                )
            ],
        ),
    )


def test_describe_text_names_the_scalar_typedef() -> None:
    assert COLOR.type_descriptor() == "IEColor"
    assert COLOR.type_descriptor(expand_named=False) == "IEColor"
    assert U32.type_descriptor() == "U32"
    array = LVType(kind=LVTypeKind.ARRAY, dimensions=1, element_type=COLOR)
    assert array.type_descriptor() == "[IEColor]"


def test_neighbouring_named_types_are_unchanged() -> None:
    enum = LVType(
        kind=LVTypeKind.ENUM,
        typedef_name="Mode.ctl",
        values={"A": EnumValue(value=0), "B": EnumValue(value=1)},
    )
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Cfg.ctl",
        fields=[ClusterField("x", U32)],
    )
    refnum = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", classname="My.lvclass"
    )
    assert enum.type_descriptor() == "Mode{A, B}"
    assert cluster.type_descriptor() == "Cfg{x}"
    assert cluster.type_descriptor(expand_named=False) == "Cfg"
    assert refnum.type_descriptor() == "My.lvclass"
    assert refnum.type_ref() == "My.lvclass"
    assert U32.type_ref() is None
    assert COLOR.type_ref() == "IEColor.ctl"
    assert COLOR.to_python() == "IEColor"
    assert COLOR.to_python(lambda t: "IEColor") == "IEColor"


def test_lvnet_names_the_typedef_and_the_footnote_defines_it() -> None:
    module = _module(COLOR)
    terse = render_lvnet(module, display_name="Check Color.vi")
    assert "Color in" in terse and ": IEColor" in terse
    assert "U32" not in terse
    assert "types :" not in terse
    verbose = render_lvnet(module, display_name="Check Color.vi", verbose=True)
    assert "types :" in verbose
    assert "    IEColor = U32 ; ./Controls/IEColor.ctl" in verbose.splitlines()


def test_plain_u32_has_no_footnote() -> None:
    verbose = render_lvnet(_module(U32), display_name="Check Color.vi", verbose=True)
    assert "types :" not in verbose
    assert ": U32" in verbose


def test_lvnet_round_trips_by_bytes_and_graph_identity() -> None:
    module = _module(COLOR)
    text = render_lvnet(module, display_name="Check Color.vi", verbose=True)
    parsed = parse_lvnet(text)
    rebuilt = reconstruct_module(parsed)
    again = render_lvnet(rebuilt, display_name="Check Color.vi", verbose=True)
    assert again == text
    _assert_graph_identity(module, rebuilt, "Check Color.vi")
    assert netlist_signature(module) == netlist_signature(parsed)


def test_json_carries_the_typedef_name() -> None:
    data = json.dumps(netlist_to_dict(_module(COLOR), verbose=True))
    assert '"typedef_name": "IEColor.ctl"' in data


def _generated(lv_type: LVType) -> str:
    argument = FPTerminal(
        id="fp:color_in",
        index=0,
        direction="input",
        name="Color in",
        is_public=True,
        is_indicator=False,
        lv_type=lv_type,
    )
    result = FPTerminal(
        id="fp:color_out",
        index=1,
        direction="output",
        name="Color out",
        is_public=True,
        is_indicator=True,
        lv_type=lv_type,
    )
    context = VIContext(
        name="Check Color.vi",
        inputs=[argument],
        outputs=[result],
        data_flow=[
            Wire.from_terminals(from_terminal_id=argument.id, to_terminal_id=result.id)
        ],
    )
    graph = build_graph(context, context.name, [])

    def not_a_file(key: str) -> NameImport:
        raise AssertionError(key)

    def type_import(named: LVType) -> NameImport | None:
        if named.typedef_name != "IEColor.ctl":
            return None
        return NameImport("from .controls.iecolorctl import IEColor", "IEColor")

    resolver = ImportResolver(
        lambda name, key: not_a_file(name),
        lambda name: not_a_file(name),
        type_import,
        not_a_file,
        lambda: "",
        lambda key: "",
    )
    return build_module(context, context.name, resolver, graph=graph)


def test_generate_annotates_with_the_typedef_and_imports_it() -> None:
    source = _generated(COLOR)
    assert "from .controls.iecolorctl import IEColor" in source
    assert "color_in: IEColor" in source


def test_generated_code_runs_with_the_typedef_module() -> None:
    source = _generated(COLOR).replace(
        "from .controls.iecolorctl import IEColor", "IEColor = int"
    )
    namespace: dict = {}
    exec(compile(source, "check_color.py", "exec"), namespace)  # noqa: S102
    assert namespace["check_color"](7).color_out == 7


def test_a_plain_u32_is_an_int_with_no_import() -> None:
    source = _generated(U32)
    assert "color_in: int" in source
    assert "IEColor" not in source
