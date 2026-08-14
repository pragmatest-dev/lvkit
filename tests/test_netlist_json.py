"""`netlist_to_dict` — the canonical STRUCTURED (JSON) projection of the netlist
IR, the shared shape every `format="json"` surface returns.

Hand-built IR so the serializer is pinned independently of graph construction:
the instance/scope union must stay `kind`-tagged and scopes must nest their
frames' bodies recursively (the parts `dataclasses.asdict` would get wrong).
"""

from __future__ import annotations

from lvkit.graph.netlist import (
    BoundaryOutput,
    DefaultValue,
    GammaCase,
    GammaMerge,
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
        outputs=[
            BoundaryOutput(
                name="y", lv_label="DBL",
                source=_ref("Increment", "out", "n2"),
            )
        ],
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
    assert d["outputs"] == [
        {
            "name": "y",
            "type": "DBL",
            "source": {
                "node": "Increment", "port": "out",
                "occurrence": None, "bare": "n2",
            },
        }
    ]
    assert d["components"][0]["name"] == "Increment"
    assert d["components"][0]["inputs"] == [{"name": "x", "type": "I32"}]

    # The union is discriminated by `kind`.
    assert [item["kind"] for item in d["body"]] == ["instance", "scope"]

    inst = d["body"][0]
    assert inst["name"] == "Increment"
    assert inst["inputs"][0]["port"] == "x"
    assert inst["inputs"][0]["net"]["bare"] == "n"
    assert inst["outputs"][0]["bare"] == "n2"
    # A non-cpdArith instance carries no operation -- the key is present
    # (stable shape) but null.
    assert inst["operation"] is None

    sc = d["body"][1]
    assert sc["scope_kind"] == "case"
    assert sc["selector"]["bare"] == "sel"
    # Frames nest their body recursively, also kind-tagged.
    assert sc["frames"][0]["label"] == "True"
    assert sc["frames"][0]["body"][0]["kind"] == "instance"
    assert sc["frames"][1]["is_default"] is True
    assert sc["frames"][1]["passthrough"] is True
    assert sc["frames"][1]["body"] == []


def test_case_scope_outputs_carries_gamma_merge_union_shape():
    """A case scope's ``outputs`` -- the JSON counterpart of ``render_netlist``'s
    ``out{k} := gamma(...)`` line -- carries the full net name, a "gamma" kind
    tag, the selector NetRef, and one {frame, source} pair per frame. A
    wired frame's ``source`` is a plain NetRef dict (no "kind" key, same as
    every other net reference in this IR); an unwired frame's is a
    discriminated ``{"kind": "default", ...}`` DefaultValue -- the two must
    stay distinguishable without inspecting anything but the dict itself."""
    scope = NetlistScope(
        uid="9",
        kind="case",
        selector=_ref("Sel2", "0", "sel2"),
        frames=[
            NetlistFrame(
                label="True", value="1", is_default=False, body=[],
                passthrough=True,
            ),
            NetlistFrame(
                label="Default", value="Default", is_default=True, body=[],
                passthrough=True,
            ),
        ],
        outputs=[
            GammaMerge(
                net="case0.out0",
                selector=_ref("Sel2", "0", "sel2"),
                cases=[
                    GammaCase(
                        frame_key="True",
                        source=_ref("Subtract", "difference", "difference"),
                    ),
                    GammaCase(
                        frame_key="default",
                        source=DefaultValue(literal="0", lv_label="I32"),
                    ),
                ],
            )
        ],
    )
    module = NetlistModule(vi_name="g.vi", inputs=[], outputs=[], body=[scope])

    d = netlist_to_dict(module)
    sc = d["body"][0]
    assert sc["scope_kind"] == "case"
    assert len(sc["outputs"]) == 1

    gamma = sc["outputs"][0]
    assert gamma["net"] == "case0.out0"
    assert gamma["kind"] == "gamma"
    assert gamma["selector"]["bare"] == "sel2"
    assert len(gamma["cases"]) == 2

    wired = gamma["cases"][0]
    assert wired["frame"] == "True"
    assert wired["source"]["bare"] == "difference"
    assert "kind" not in wired["source"]

    unwired = gamma["cases"][1]
    assert unwired["frame"] == "default"
    assert unwired["source"] == {"kind": "default", "type": "I32", "literal": "0"}


def test_instance_carries_cpdarith_operation():
    """Audit finding: a Compound Arithmetic (``cpdArith``) instance's mode
    (add/multiply/and/or/xor) must be readable straight off the JSON
    instance dict -- a program can't tell an AND from an OR from the
    display ``name`` alone (both render "Compound Arithmetic")."""
    inst = NetlistInstance(
        uid="12",
        name="Compound Arithmetic",
        occurrence=1,
        inputs=[
            NetlistPortBinding(port="1", net=_ref("Not Equal?", "result", "result")),
            NetlistPortBinding(port="2", net=_ref("Not Equal?", "result", "result")),
        ],
        outputs=[_ref("Compound Arithmetic", "0", "Compound Arithmetic#1.0")],
        operation="and",
    )
    module = NetlistModule(vi_name="c.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    body_inst = d["body"][0]
    assert body_inst["kind"] == "instance"
    assert body_inst["name"] == "Compound Arithmetic"
    assert body_inst["operation"] == "and"
    # The operator annotation must not perturb the net identity fields.
    assert body_inst["occurrence"] == 1
    assert body_inst["outputs"][0]["bare"] == "Compound Arithmetic#1.0"


def test_non_case_scope_outputs_is_empty_list():
    """``outputs`` is always present (never omitted) but empty for every
    non-case scope kind -- loops/sequences/disabled/event structures don't
    have gamma merges."""
    scope = NetlistScope(
        uid="2", kind="sequence", selector=None,
        frames=[NetlistFrame(label="0", value="0", is_default=False, body=[])],
    )
    module = NetlistModule(vi_name="s2.vi", inputs=[], outputs=[], body=[scope])
    d = netlist_to_dict(module)
    assert d["body"][0]["outputs"] == []


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
