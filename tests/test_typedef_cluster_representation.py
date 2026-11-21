"""A cluster value is one object however it is made: a typedef cluster is an
attribute object (``SimpleNamespace``), an anonymous one a tuple. A Bundle whose
cluster output is wired to a typedef (its saved cluster type has no typedef of its
own) makes the typedef's attribute object, so what reads it by name finds the
fields. The handmade diagram runs the Bundle; the corpus VIs run a maker and its
reader."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from tests.helpers import build_graph, exec_generated
from tests.test_catalog_corpus_callers import SAMPLES, _generate

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")


def _cluster(typedef_name: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name=typedef_name,
        fields=[
            ClusterField(name="Label", type=STRING),
            ClusterField(name="Gain", type=DBL),
        ],
    )


def _panel(name: str, direction: str, lv_type: LVType) -> FPTerminal:
    return FPTerminal(
        id=f"fp:{name}",
        index=0 if direction == "output" else (1 if name == "label" else 2),
        direction=direction,
        wiring_rule=1 if direction == "input" else 0,
        name=name,
        is_public=True,
        is_indicator=direction == "output",
        lv_type=lv_type,
    )


def _bundle_vi(output_type: LVType):
    """A Bundle with no cluster wired in (its input saved anonymous) that bundles
    two controls, its output cluster of ``output_type``."""
    node = PrimitiveNode(
        id="bundle",
        vi_path="Cluster Test.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg_out",
                index=0,
                direction="output",
                nmux_role="agg",
                lv_type=output_type,
            ),
            Terminal(
                id="agg_in",
                index=1,
                direction="input",
                nmux_role="agg",
                lv_type=_cluster(None),
            ),
            Terminal(
                id="f0",
                index=2,
                direction="input",
                nmux_role="list",
                nmux_field_index=0,
                lv_type=STRING,
            ),
            Terminal(
                id="f1",
                index=3,
                direction="input",
                nmux_role="list",
                nmux_field_index=1,
                lv_type=DBL,
            ),
        ],
    )
    context = VIContext(
        name="Cluster Test.vi",
        inputs=[_panel("label", "input", STRING), _panel("gain", "input", DBL)],
        outputs=[_panel("made", "output", output_type)],
        data_flow=[
            Wire.from_terminals(from_terminal_id=a, to_terminal_id=b)
            for a, b in [
                ("fp:label", "f0"),
                ("fp:gain", "f1"),
                ("agg_out", "fp:made"),
            ]
        ],
    )
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec_generated(source, namespace, context.name)
    return namespace["cluster_test"]


def test_bundle_into_a_typedef_makes_the_attribute_object() -> None:
    made = _bundle_vi(_cluster("Setting.ctl"))("a", 2.0).made
    assert (made.label, made.gain) == ("a", 2.0)


def test_bundle_into_an_anonymous_cluster_makes_a_tuple() -> None:
    assert _bundle_vi(_cluster(None))("a", 2.0).made == ("a", 2.0)


@pytest.mark.functional
@pytest.mark.needs_samples
def test_easyxml_default_formatting_is_read_by_name(tmp_path: Path) -> None:
    """``Get Default Type Formatting`` bundles its two arrays into the
    ``Type Formatting Cluster`` typedef and ``Date Type Format String Mapping``
    reads the fields by name."""
    low_level = SAMPLES / "JKI-EasyXML/Source/Support/Low Level"
    package = _generate(low_level / "Get Default Type Formatting.vi", tmp_path)
    sys.path.insert(0, str(package.parent))
    try:
        module = importlib.import_module(
            f"{package.name}.support.low_level.get_default_type_formatting"
        )
        formatting = module.get_default_type_formatting().default_type_formatting
        assert len(formatting.data_types) == len(formatting.format_strings) == 17
        assert formatting.format_strings[0] == "%s"
    finally:
        sys.path.remove(str(package.parent))
        for name in [m for m in sys.modules if m.split(".")[0] == package.name]:
            del sys.modules[name]
