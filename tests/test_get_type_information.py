"""``Get Type Information.vi`` returns the item of NI's ``Data Type`` enum that the
type-descriptor code of its variant is. The generated code reads the code the
``Variant`` records and looks it up in a dict literal of the items the caller's
Type output holds; a code that names several items by itself raises where the node
runs. Type Definition and Type Definition Info read the type definition name the
variant records."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.codegen.panel_types import panel_type_keywords
from lvkit.codegen.variants_module import lv_type_expression
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import (
    KindProps,
    TypedefStatus,
    VIContext,
    VINode,
    VIProperties,
    Wire,
)
from lvkit.graph.node_kinds import NodeType
from lvkit.models import ClusterField, EnumValue, FPTerminal, LVType, LVTypeKind
from lvkit.timestamp_value import TIMESTAMP_FLAVOR
from lvkit.variant_type_items import item_values_by_code
from lvkit.vilib_resolver import VIEntry, VILibResolver
from tests.helpers import build_graph, exec_generated, generated_variants

_CATALOG = (
    Path(__file__).parent.parent
    / "src/lvkit/data/vilib/cluster-class-variant.json"
)
_ENTRY = VIEntry.model_validate(
    next(
        e
        for e in json.loads(_CATALOG.read_text(encoding="utf-8"))["entries"]
        if e["name"] == "Get Type Information.vi"
    )
)
_FILE = "Get Type Information.vi"
_KEY = f"/vilib/{_FILE}"
_VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
_ITEMS = [
    "Invalid Type", "Void", "I8", "I16", "I32", "I64", "U8", "U16", "U32", "U64",
    "Single Float", "Double Float", "Extended Float", "Single Complex",
    "Double Complex", "Extended Complex", "Enum U8", "Enum U16", "Enum U32",
    "Enum U64", "Single Float With Unit", "Double Float With Unit",
    "Extended Float With Unit", "Single Complex with Unit",
    "Double Complex with Unit", "Extended Complex with Unit", "Boolean", "String",
    "Path", "Picture", "Tag", "Array", "Cluster", "Variant", "LV Variant",
    "Waveform", "Timestamp", "Digital Waveform", "Digital Data", "ExpressData",
    "Refnum", "External Data", "VI", "Poly VI", "LabVIEW Class Instance",
    "Fixed Point",
]
_DATA_TYPE = LVType(
    kind=LVTypeKind.ENUM,
    underlying_type="UnitUInt8",
    values={label: EnumValue(value=i) for i, label in enumerate(_ITEMS)},
    typedef_name="NI_Data Type.lvlib:Data Type.ctl",
)
_TEXT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
_STAMP = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32")
_TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)
_INFO = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[ClusterField("Name", _TEXT), ClusterField("Timestamp", _STAMP)],
    typedef_name="NI_Data Type.lvlib:Type Definition Info.ctl",
)
# The caller returns each output of the call it wires: name -> (index, type).
_OUTPUTS = {
    "type": (3, _DATA_TYPE),
    "name": (2, _TEXT),
    "type_definition": (1, _BOOL),
    "type_definition_info": (4, _INFO),
}


def _generate(
    wired_outputs: list[str],
    monkeypatch: pytest.MonkeyPatch,
    gaps: list[Gap],
    data: LVType = _VARIANT,
) -> str:
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(_KEY, node_type="vi")  # noqa: SLF001
    terminals = [
        FPTerminal(
            id="k:v", index=11, direction="input", wiring_rule=1, lv_type=_VARIANT
        )
    ]
    outputs = []
    flow = [Wire.from_terminals(from_terminal_id="fp:x", to_terminal_id="k:v")]
    for name in wired_outputs:
        index, lv_type = _OUTPUTS[name]
        terminals.append(
            FPTerminal(
                id=f"k:{name}", index=index, direction="output", lv_type=lv_type,
                name=name,
            )
        )
        outputs.append(
            FPTerminal(
                id=f"fp:{name}", index=len(outputs), direction="output", name=name,
                is_public=True, is_indicator=True, lv_type=lv_type,
            )
        )
        flow.append(
            Wire.from_terminals(
                from_terminal_id=f"k:{name}", to_terminal_id=f"fp:{name}"
            )
        )
    call = VINode(
        id="call",
        vi_path="caller.vi",
        name=_FILE,
        node_type="iUse",
        callee_key=_KEY,
        terminals=terminals,
    )
    context = VIContext(
        name="caller.vi",
        inputs=[
            FPTerminal(
                id="fp:x", index=0, direction="input", wiring_rule=1, name="x",
                is_public=True, is_indicator=False, lv_type=data,
            )
        ],
        outputs=outputs,
        data_flow=flow,
    )
    built = build_graph(context, "caller.vi", [call], graph=graph)
    monkeypatch.setattr(built, "loaded_callee_key", lambda node: None)
    monkeypatch.setattr(VILibResolver, "resolve_recorded", lambda _self, path: _ENTRY)
    monkeypatch.setattr(VILibResolver, "catalog_file_name", lambda _self, path: _FILE)
    return build_module(context, "caller.vi", graph=built, gaps=gaps)


def _run(source: str):
    namespace: dict = {}
    exec_generated(source, namespace, "caller.py")
    return namespace["caller"]


def _item(label: str) -> int:
    return _ITEMS.index(label)


def test_the_item_is_the_one_of_the_code_the_variant_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gaps: list[Gap] = []
    source = _generate(["type"], monkeypatch, gaps)
    assert gaps == []
    caller = _run(source)
    v = generated_variants()
    assert caller(v.Variant(5, v.LV_I32)).type_ == _item("I32")
    assert caller(v.Variant(5.0, v.LV_DBL)).type_ == _item("Double Float")
    assert caller(v.Variant("x", v.LV_String)).type_ == _item("String")
    assert caller(v.Variant(True, v.LV_TF)).type_ == _item("Boolean")
    array = v.Variant([1], v.LVType(0x40, "Array", None))
    assert caller(array).type_ == _item("Array")
    cluster = v.Variant(None, v.LVType(0x50, "Lib.lvlib:Point.ctl", None))
    assert caller(cluster).type_ == _item("Cluster")
    enum = v.Variant(1, v.LVType(0x15, "Lib.lvlib:Mode.ctl", None))
    assert caller(enum).type_ == _item("Enum U8")
    assert caller(v.Variant(None, v.LVType(0x53, "LVVariant", None))).type_ == _item(
        "LV Variant"
    )


def test_an_empty_variant_is_void(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _generate(["type"], monkeypatch, [])
    assert _run(source)(generated_variants().Variant()).type_ == _item("Void")


@pytest.mark.parametrize(
    ("code", "name"),
    [(0x54, "MeasureData"), (0x70, "MyClass.lvclass"), (0x70, "Refnum")],
)
def test_a_code_that_names_several_items_raises_where_it_runs(
    code: int, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _generate(["type"], monkeypatch, [])
    v = generated_variants()
    with pytest.raises(NotImplementedError, match=f"code {code} \\({name}\\)"):
        _run(source)(v.Variant(None, v.LVType(code, name, None)))


def test_the_name_is_the_label_the_variant_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gaps: list[Gap] = []
    source = _generate(["name", "type"], monkeypatch, gaps)
    assert gaps == []
    caller = _run(source)
    v = generated_variants()
    result = caller(v.Variant(5, v.LV_I32, {}, "Voltage"))
    assert (result.name, result.type_) == ("Voltage", _item("I32"))
    # A variant made from no labelled data, and an empty one, have no name.
    assert caller(v.Variant(5, v.LV_I32)).name == ""
    assert caller(v.Variant()).name == ""


def test_the_name_alone_writes_no_type_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _generate(["name"], monkeypatch, [])
    assert "variant_type" not in source
    assert _run(source)(generated_variants().Variant(1, None, {}, "x")).name == "x"


def test_a_variant_of_a_type_definition_records_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gaps: list[Gap] = []
    source = _generate(["type_definition", "type_definition_info"], monkeypatch, gaps)
    assert gaps == []
    caller = _run(source)
    v = generated_variants()
    name = "Lib.lvlib:Point.ctl"
    plain = caller(v.Variant(5, v.LV_I32))
    assert plain.type_definition is False
    assert plain.type_definition_info.name == ""
    typedef_type = v.LVType(0x3, "Lib.lvlib:Mode.ctl", None, typedef_name=name)
    typedef = caller(v.Variant(5, typedef_type))
    assert typedef.type_definition is True
    assert typedef.type_definition_info.name == name
    assert typedef.type_definition_info.timestamp == 0
    assert caller(v.Variant()).type_definition is False


def test_the_timestamp_of_a_strict_type_definition_raises_when_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gaps: list[Gap] = []
    source = _generate(["type_definition", "type_definition_info"], monkeypatch, gaps)
    assert gaps == []
    caller = _run(source)
    v = generated_variants()
    name = "Lib.lvlib:Mode.ctl"
    loose = v.LVType(0x3, name, None, typedef_name=name)
    strict = v.LVType(0x3, name, None, typedef_name=name, strict_typedef=True)
    # Not strict: the 0 NI gives.
    assert caller(v.Variant(5, loose)).type_definition_info.timestamp == 0
    # Strict, only the Name read: no raise.
    strict_info = caller(v.Variant(5, strict)).type_definition_info
    assert strict_info.name == name
    # Strict, the Timestamp read: raises, naming the definition.
    with pytest.raises(NotImplementedError, match=r"strict type definition 'Lib"):
        strict_info.timestamp
    assert "Mode.ctl" in _message(strict_info)


def _message(info) -> str:
    try:
        info.timestamp
    except NotImplementedError as refusal:
        return str(refusal)
    return ""


def test_the_graph_tells_a_strict_type_definition_from_a_plain_one() -> None:
    graph = InMemoryVIGraph()
    for key, status in (
        ("/lib/Strict.ctl", TypedefStatus.STRICT_TYPEDEF),
        ("/lib/Plain.ctl", TypedefStatus.TYPEDEF),
    ):
        graph._dep_graph.add_node(key, node_type=NodeType.TYPEDEF)
        graph._vi_properties[key] = VIProperties(
            kind=KindProps(typedef_status=status)
        )

    def instance(key: str) -> LVType:
        return LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type="NumInt32",
            typedef_name="Lib.lvlib:T.ctl",
            file_key=key,
        )

    assert graph.is_strict_typedef(instance("/lib/Strict.ctl")) is True
    assert graph.is_strict_typedef(instance("/lib/Plain.ctl")) is False
    assert graph.is_strict_typedef(instance("/lib/Missing.ctl")) is False
    strict = instance("/lib/Strict.ctl")
    strict.strict_typedef = graph.is_strict_typedef(strict)
    plain = instance("/lib/Plain.ctl")
    plain.strict_typedef = graph.is_strict_typedef(plain)
    # One expression for the type, read off it: a variant's type and a panel
    # control's type of the same strict type definition are written alike.
    assert "strict_typedef=True" in lv_type_expression(strict).text
    assert "strict_typedef" not in lv_type_expression(plain).text
    panel = panel_type_keywords([("mode", strict)])
    assert lv_type_expression(strict).text in ast.unparse(panel.keywords[0][1])


def test_the_package_writes_the_typedef_name_of_a_type_definition() -> None:
    typedef = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="NumInt32",
        typedef_name="Lib.lvlib:Mode.ctl",
    )
    plain = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    assert "typedef_name='Lib.lvlib:Mode.ctl'" in lv_type_expression(typedef).text
    assert lv_type_expression(plain).text == "LV_I32"


def test_a_wrongly_recorded_info_cluster_is_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_OUTPUTS, "type_definition_info", (4, _TEXT))
    gaps: list[Gap] = []
    _generate(["type_definition_info"], monkeypatch, gaps)
    assert len(gaps) == 1
    assert "Type Definition Info" in gaps[0].detail


def test_an_unwired_type_writes_no_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    gaps: list[Gap] = []
    source = _generate([], monkeypatch, gaps)
    assert gaps == []
    assert "variant_type" not in source


def test_the_table_lists_only_codes_and_items_that_exist() -> None:
    items = item_values_by_code(_DATA_TYPE)
    assert items is not None
    assert items[0x00] == _item("Void")
    assert items[0x21] == items[0x20] == _item("Boolean")
    assert 0x54 not in items
    assert 0x70 not in items
    assert item_values_by_code(_TEXT) is None
