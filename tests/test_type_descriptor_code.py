"""OpenG ``Get TDEnum from Data__ogtk`` returns the type-descriptor code of the
data wired to it. The caller records that wire's type, so the code is written as
a constant, read from the type descriptor; data whose type is only known when
the code runs (a variant) is a gap. The codes are the item values of OpenG's own
``Type Descriptor Enumeration__ogtk.ctl``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.load_ctl import load_ctl_by_path
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.lv_type_codes import TD_FULL_TYPE, type_descriptor_code
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.vilib_resolver import VIEntry, get_resolver
from tests.helpers import build_graph

_CATALOG = Path(__file__).parent.parent / "src/lvkit/data/openg/variant.json"
_ENTRY = VIEntry.model_validate(json.loads(_CATALOG.read_text())["entries"][0])
_FILE = "Get TDEnum from Data__ogtk.vi"
_KEY = f"/userlib/{_FILE}"
_U8 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="UnitUInt8")
_TD_ENUM_CTL = Path(
    ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/lvdata"
    "/lvdata.llb/Type Descriptor Enumeration__ogtk.ctl"
)


def _generate(
    data: LVType, monkeypatch: pytest.MonkeyPatch, gaps: list[Gap]
) -> str:
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(_KEY, node_type="vi")  # noqa: SLF001
    call = VINode(
        id="call",
        vi_path="caller.vi",
        name="Get TDEnum from Data__ogtk.vi",
        node_type="iUse",
        callee_key=_KEY,
        terminals=[
            FPTerminal(
                id="k:d", index=4, direction="input", wiring_rule=1, lv_type=data
            ),
            FPTerminal(id="k:t", index=1, direction="output", lv_type=_U8),
        ],
    )
    context = VIContext(
        name="caller.vi",
        inputs=[
            FPTerminal(
                id="fp:x", index=0, direction="input", wiring_rule=1, name="x",
                is_public=True, is_indicator=False, lv_type=data,
            )
        ],
        outputs=[
            FPTerminal(
                id="fp:t", index=0, direction="output", name="t", is_public=True,
                is_indicator=True, lv_type=_U8,
            )
        ],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:x", to_terminal_id="k:d"),
            Wire.from_terminals(from_terminal_id="k:t", to_terminal_id="fp:t"),
        ],
    )
    built = build_graph(context, "caller.vi", [call], graph=graph)
    monkeypatch.setattr(built, "loaded_callee_key", lambda node: None)
    monkeypatch.setattr(get_resolver(), "resolve_recorded", lambda path: _ENTRY)
    monkeypatch.setattr(get_resolver(), "catalog_file_name", lambda path: _FILE)
    return build_module(context, "caller.vi", graph=built, gaps=gaps)


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"), 0x03),
        (LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"), 0x0A),
        (LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"), 0x30),
        (LVType(kind=LVTypeKind.ARRAY, underlying_type="Array"), 0x40),
        (LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="MeasureData"), 0x54),
    ],
)
def test_the_code_of_the_wired_type_is_a_constant(
    data: LVType, code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    source = _generate(data, monkeypatch, gaps)
    assert gaps == []
    assert f"type_descriptor_enumeration = {code}" in source
    namespace: dict = {}
    exec(compile(source, "caller.py", "exec"), namespace)
    assert namespace["caller"](None).t == code


def test_a_variant_is_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    gaps: list[Gap] = []
    variant = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
    source = _generate(variant, monkeypatch, gaps)
    assert [g.reason for g in gaps] == [
        "Get TDEnum from Data__ogtk reads the type of a variant, which is only "
        "known when the code runs; the generated code has no LabVIEW type for a "
        "value"
    ]
    assert "raise NotImplementedError" in source


@pytest.mark.needs_samples
def test_the_openg_enum_items_are_the_type_descriptor_codes() -> None:
    graph, key = load_ctl_by_path(_TD_ENUM_CTL)
    items = {
        ev.value: name for name, ev in graph.get_typedef(key).root_type.values.items()
    }
    named = {
        "I8": "NumInt8",
        "DBL": "NumFloat64",
        "Boolean": "Boolean",
        "String": "String",
        "Path": "Path",
        "Array": "Array",
        "Cluster": "Cluster",
        "Variant": "LVVariant",
        "Waveform": "MeasureData",
        "Refnum": "Refnum",
        "Enum U16": "UnitUInt16",
    }
    for item, td in named.items():
        assert items[TD_FULL_TYPE[td].value] == item
        lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=td)
        assert type_descriptor_code(lv_type) == TD_FULL_TYPE[td].value
