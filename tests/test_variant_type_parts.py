"""``Get Array Information.vi``, ``Get Cluster Information.vi`` and ``Get Numeric
Information.vi`` read the parts of the type a variant records with the reader in
the generated ``variants`` module. The tests generate a caller of each, run it on
variants made from the types ``type_string.encode`` writes, and check each output;
Get Refnum Information and Get Waveform Information stay gaps."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit import type_string
from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.lv_type_codes import TD_FULL_TYPE
from lvkit.models import (
    ClusterField,
    EnumValue,
    FPTerminal,
    LVType,
    LVTypeKind,
)
from lvkit.vilib_resolver import VIEntry, VILibResolver
from tests.helpers import build_graph, exec_generated, generated_variants

_CATALOG = (
    Path(__file__).parent.parent / "src/lvkit/data/vilib/cluster-class-variant.json"
)
_VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_TEXT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_U32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32")


def _enum(*labels: str) -> LVType:
    return LVType(
        LVTypeKind.ENUM,
        underlying_type="UnitUInt16",
        values={label: EnumValue(value=i) for i, label in enumerate(labels)},
    )


_MEMORY_INFO = LVType(
    LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    typedef_name="NI_Data Type.lvlib:Memory Info.ctl",
    fields=[
        ClusterField("Type", _enum("Variable", "Fixed", "Bounded")),
        ClusterField("Size", _U32),
    ],
)
_LENGTHS = LVType(
    LVTypeKind.ARRAY, underlying_type="Array", element_type=_MEMORY_INFO
)
_VARIANTS = LVType(
    LVTypeKind.ARRAY, underlying_type="Array", element_type=_VARIANT
)
_NAMES = LVType(LVTypeKind.ARRAY, underlying_type="Array", element_type=_TEXT)
_REPRESENTATION = _enum(
    "Extended Precision", "Double Precision", "Single Precision", "Long", "Word",
    "Byte", "Unsigned Long", "Unsigned Word", "Unsigned Byte", "Complex Extended",
    "Complex Double", "Complex Single", "Quad", "Unsigned Quad", "Fixed-Point",
)
_UNITS = LVType(LVTypeKind.ARRAY, underlying_type="Array", element_type=_I32)
_ELEMENT = {
    "Get Array Information.vi": {
        "element": (1, _VARIANT),
        "lengths": (2, _LENGTHS),
        "dimensions": (3, _I32),
    },
    "Get Cluster Information.vi": {"elements": (3, _VARIANTS)},
    "Get Numeric Information.vi": {
        "representation": (1, _REPRESENTATION),
        "units": (2, _UNITS),
        "names": (3, _NAMES),
    },
    "Get Refnum Information.vi": {"reference": (3, _enum("Queue"))},
    "Get Waveform Information.vi": {"y": (3, _VARIANT)},
}


def _entry(file: str) -> VIEntry:
    entries = json.loads(_CATALOG.read_text())["entries"]
    return VIEntry.model_validate(next(e for e in entries if e["name"] == file))


def _generate(
    file: str,
    wired_outputs: list[str],
    monkeypatch: pytest.MonkeyPatch,
    gaps: list[Gap],
) -> str:
    key = f"/vilib/{file}"
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(key, node_type="vi")  # noqa: SLF001
    terminals = [
        FPTerminal(
            id="k:v", index=11, direction="input", wiring_rule=1, lv_type=_VARIANT
        )
    ]
    outputs = []
    flow = [Wire.from_terminals(from_terminal_id="fp:x", to_terminal_id="k:v")]
    for name in wired_outputs:
        index, lv_type = _ELEMENT[file][name]
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
        id="call", vi_path="caller.vi", name=file, node_type="iUse",
        callee_key=key, terminals=terminals,
    )
    context = VIContext(
        name="caller.vi",
        inputs=[
            FPTerminal(
                id="fp:x", index=0, direction="input", wiring_rule=1, name="x",
                is_public=True, is_indicator=False, lv_type=_VARIANT,
            )
        ],
        outputs=outputs,
        data_flow=flow,
    )
    built = build_graph(context, "caller.vi", [call], graph=graph)
    monkeypatch.setattr(built, "loaded_callee_key", lambda node: None)
    entry = _entry(file)
    monkeypatch.setattr(VILibResolver, "resolve_recorded", lambda _self, path: entry)
    monkeypatch.setattr(VILibResolver, "catalog_file_name", lambda _self, path: file)
    return build_module(context, "caller.vi", graph=built, gaps=gaps)


def _caller(source: str):
    namespace: dict = {}
    exec_generated(source, namespace, "caller.py")
    return namespace["caller"]


def _scalar(name: str) -> LVType:
    return LVType(LVTypeKind.PRIMITIVE, underlying_type=name)


def _of(lv_type: LVType):
    v = generated_variants()
    code = int(TD_FULL_TYPE[lv_type.underlying_type].value)
    words = tuple(type_string.encode(lv_type))
    return v.Variant(None, v.LVType(code, "x", words))


def _array(element: LVType, dimensions: int = 1) -> LVType:
    return LVType(
        LVTypeKind.ARRAY, underlying_type="Array", element_type=element,
        dimensions=dimensions,
    )


def _cluster(*members: LVType) -> LVType:
    return LVType(
        LVTypeKind.CLUSTER, underlying_type="Cluster",
        fields=[ClusterField(f"f{i}", m) for i, m in enumerate(members)],
    )


def _enum_type(*labels: str) -> LVType:
    return LVType(
        LVTypeKind.ENUM, underlying_type="UnitUInt8",
        values={label: EnumValue(value=i) for i, label in enumerate(labels)},
    )


def test_array_information_reads_the_dimensions_and_the_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gaps: list[Gap] = []
    source = _generate(
        "Get Array Information.vi", ["element", "lengths", "dimensions"],
        monkeypatch, gaps,
    )
    assert gaps == []
    result = _caller(source)(_of(_array(_scalar("NumFloat64"), 2)))
    v = generated_variants()
    assert result.dimensions == 2
    assert result.element == v.Variant(0.0, v.LV_DBL)
    assert [(r.type_, r.size) for r in result.lengths] == [(0, 0), (0, 0)]
    assert isinstance(result.lengths[0], SimpleNamespace)


def test_array_information_of_a_variable_array_of_clusters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _generate("Get Array Information.vi", ["element"], monkeypatch, [])
    cluster = _cluster(_scalar("NumInt32"), _scalar("String"))
    element = _caller(source)(_of(_array(cluster))).element
    assert element.type.code == 0x50
    assert element.type.type_string == tuple(type_string.encode(cluster))


def test_array_information_refuses_a_variant_that_is_not_an_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _generate("Get Array Information.vi", ["dimensions"], monkeypatch, [])
    v = generated_variants()
    with pytest.raises(ValueError, match="Get Array Information.vi: I32 is not an"):
        _caller(source)(v.Variant(5, v.LV_I32))
    with pytest.raises(ValueError, match="an empty variant is not an array"):
        _caller(source)(v.Variant())


def test_array_information_of_a_type_with_no_string_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _generate("Get Array Information.vi", ["dimensions"], monkeypatch, [])
    v = generated_variants()
    with pytest.raises(NotImplementedError, match="records no type string"):
        _caller(source)(v.Variant([1], v.LVType(0x40, "[I32]", None)))


def test_cluster_information_lists_the_member_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gaps: list[Gap] = []
    source = _generate("Get Cluster Information.vi", ["elements"], monkeypatch, gaps)
    assert gaps == []
    cluster = _cluster(_scalar("NumInt32"), _scalar("String"), _scalar("Boolean"))
    result = _caller(source)(_of(cluster))
    v = generated_variants()
    # Each member is labelled f0, f1, f2, so its descriptor carries that name
    # (flag 0x40 on the type code, then a Pascal string "f<i>" padded to even).
    assert result.elements == [
        v.Variant(0, v.LVType(3, "f0", (8, 0x4003, 0x0266, 0x3000))),
        v.Variant("", v.LVType(0x30, "f1", (12, 0x4030, -1, -1, 0x0266, 0x3100))),
        v.Variant(False, v.LVType(0x21, "f2", (8, 0x4021, 0x0266, 0x3200))),
    ]
    with pytest.raises(ValueError, match="is not a cluster"):
        _caller(source)(v.Variant(1, v.LV_I32))


def test_numeric_information_of_an_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    gaps: list[Gap] = []
    source = _generate(
        "Get Numeric Information.vi", ["names", "units", "representation"],
        monkeypatch, gaps,
    )
    assert gaps == []
    result = _caller(source)(_of(_enum_type("am", "fm", "fm stereo")))
    assert result.names == ["am", "fm", "fm stereo"]
    assert result.units == []
    assert result.representation == _REPRESENTATION.values["Unsigned Byte"].value


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("NumInt32", "Long"), ("NumInt16", "Word"), ("NumInt8", "Byte"),
        ("NumInt64", "Quad"), ("NumUInt32", "Unsigned Long"),
        ("NumUInt64", "Unsigned Quad"), ("NumFloat64", "Double Precision"),
        ("NumFloat32", "Single Precision"), ("NumFloatExt", "Extended Precision"),
        ("NumComplex128", "Complex Double"), ("NumComplex64", "Complex Single"),
        ("NumComplexExt", "Complex Extended"),
    ],
)
def test_numeric_information_representation(
    name: str, label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _generate(
        "Get Numeric Information.vi", ["representation", "names"], monkeypatch, []
    )
    result = _caller(source)(_of(_scalar(name)))
    assert result.representation == _REPRESENTATION.values[label].value
    assert result.names == []


def test_numeric_information_refuses_a_non_numeric_and_a_number_with_a_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _generate(
        "Get Numeric Information.vi", ["representation"], monkeypatch, []
    )
    v = generated_variants()
    with pytest.raises(ValueError, match="String is not a numeric"):
        _caller(source)(v.Variant("x", v.LV_String))
    with pytest.raises(NotImplementedError, match="with a unit"):
        _caller(source)(v.Variant(1.0, v.LVType(0x19, "SGL with unit", (4, 0x19))))


@pytest.mark.parametrize(
    ("file", "output"),
    [("Get Refnum Information.vi", "reference"), ("Get Waveform Information.vi", "y")],
)
def test_refnum_and_waveform_information_stay_gaps(
    file: str, output: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    source = _generate(file, [output], monkeypatch, gaps)
    assert len(gaps) == 1
    assert "records only the" in gaps[0].detail
    with pytest.raises(NotImplementedError):
        _caller(source)(None)
