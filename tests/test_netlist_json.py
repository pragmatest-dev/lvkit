"""`netlist_to_dict` — the canonical STRUCTURED (JSON) projection of the netlist
IR, the shared shape every `format="json"` surface returns.

Hand-built IR so the serializer is pinned independently of graph construction:
the instance/scope union must stay `kind`-tagged and scopes must nest their
frames' bodies recursively (the parts `dataclasses.asdict` would get wrong).
"""

from __future__ import annotations

from lvkit.graph.netlist import (
    NetlistComponent,
    NetlistFrame,
    NetlistInstance,
    NetlistModule,
    NetlistPortBinding,
    NetlistScope,
    NetRef,
    netlist_to_dict,
)
from lvkit.graph.op_walk import ComponentPort


def _ref(node, port, bare, occ=None):
    return NetRef(node=node, port=port, occurrence=occ, bare=bare)


def test_instance_and_scope_are_kind_tagged_and_nested():
    inner = NetlistInstance(
        uid="7",
        name="Increment",
        occurrence=None,
        inputs=[NetlistPortBinding(port="x", net=_ref(None, "n", "n"))],
        outputs=[_ref("Increment", "out", "n2")],
    )
    scope = NetlistScope(
        uid="3",
        kind="case",
        selector=_ref("Sel", "0", "sel"),
        frames=[
            NetlistFrame(
                label="True", value="1", is_default=False, body=[inner],
                passthrough=False,
            ),
            NetlistFrame(
                label="Default", value="0", is_default=True, body=[],
                passthrough=True,
            ),
        ],
    )
    module = NetlistModule(
        vi_name="m.vi",
        inputs=[("x", "I32"), ("err in", "error cluster")],
        outputs=[("y", "DBL")],
        body=[inner, scope],
        components=[
            NetlistComponent(
                name="Increment",
                inputs=[ComponentPort(name="x", type="I32")],
                outputs=[ComponentPort(name="out", type="I32")],
            )
        ],
    )

    d = netlist_to_dict(module)

    assert d["vi"] == "m.vi"
    assert d["inputs"] == [
        {"name": "x", "type": "I32"},
        {"name": "err in", "type": "error cluster"},  # faithful type label
    ]
    assert d["outputs"] == [{"name": "y", "type": "DBL"}]
    assert d["components"][0]["name"] == "Increment"
    assert d["components"][0]["inputs"] == [{"name": "x", "type": "I32"}]

    # The union is discriminated by `kind`.
    assert [item["kind"] for item in d["body"]] == ["instance", "scope"]

    inst = d["body"][0]
    assert inst["name"] == "Increment"
    assert inst["inputs"][0]["port"] == "x"
    assert inst["inputs"][0]["net"]["bare"] == "n"
    assert inst["outputs"][0]["bare"] == "n2"

    sc = d["body"][1]
    assert sc["scope_kind"] == "case"
    assert sc["selector"]["bare"] == "sel"
    # Frames nest their body recursively, also kind-tagged.
    assert sc["frames"][0]["label"] == "True"
    assert sc["frames"][0]["body"][0]["kind"] == "instance"
    assert sc["frames"][1]["is_default"] is True
    assert sc["frames"][1]["passthrough"] is True
    assert sc["frames"][1]["body"] == []


def test_scope_without_selector_serializes_null():
    module = NetlistModule(
        vi_name="s.vi",
        inputs=[],
        outputs=[],
        body=[
            NetlistScope(
                uid="1", kind="sequence", selector=None,
                frames=[NetlistFrame(
                    label="0", value="0", is_default=False, body=[],
                )],
            )
        ],
    )
    d = netlist_to_dict(module)
    assert d["body"][0]["selector"] is None


def test_is_json_serializable():
    import json

    module = NetlistModule(vi_name="e.vi", inputs=[], outputs=[], body=[])
    # Must round-trip through json with no custom encoder.
    assert json.loads(json.dumps(netlist_to_dict(module)))["vi"] == "e.vi"
