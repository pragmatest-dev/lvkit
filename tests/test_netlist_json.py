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
    EtaMerge,
    GammaCase,
    GammaMerge,
    MuMerge,
    NetlistBoundaryInput,
    NetlistComponent,
    NetlistFeedback,
    NetlistFrame,
    NetlistInstance,
    NetlistModule,
    NetlistOutput,
    NetlistPortBinding,
    NetlistPropertyAccess,
    NetlistScope,
    NetRef,
    netlist_to_dict,
)
from lvkit.graph.op_walk import ComponentPort


def _ref(node, port, bare, occ=None):
    return NetRef(node=node, port=port, occurrence=occ, bare=bare)


def _out(node, port, bare, occ=None):
    """Test-only convenience: a ``NetlistOutput`` wrapping ``_ref(...)`` --
    these hand-built fixtures don't exercise the ``type`` field (added by
    Phase A, no assertion in this file reads it), so a placeholder is fine.
    """
    return NetlistOutput(net=_ref(node, port, bare, occ=occ), type="?")


def test_instance_and_scope_are_kind_tagged_and_nested():
    inner = NetlistInstance(
        uid="7",
        name="Increment",
        occurrence=None,
        inputs=[NetlistPortBinding(port="x", type="I32", net=_ref(None, "n", "n"))],
        outputs=[_out("Increment", "out", "n2")],
    )
    scope = NetlistScope(
        uid="3",
        kind="case",
        selector=_ref("Sel", "0", "sel"),
        frames=[
            NetlistFrame(
                label="True",
                value="1",
                is_default=False,
                body=[inner],
                passthrough=False,
            ),
            NetlistFrame(
                label="Default",
                value="0",
                is_default=True,
                body=[],
                passthrough=True,
            ),
        ],
    )
    module = NetlistModule(
        vi_name="m.vi",
        inputs=[
            NetlistBoundaryInput(name="x", type_descriptor="I32"),
            NetlistBoundaryInput(name="err in", type_descriptor="Error"),
        ],
        outputs=[
            BoundaryOutput(
                name="y",
                type_descriptor="DBL",
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
        {"name": "err in", "type": "Error"},  # error-cluster type descriptor
    ]
    assert d["outputs"] == [
        {
            "name": "y",
            "type": "DBL",
            "source": {
                "node": "Increment",
                "port": "out",
                "occurrence": None,
                "bare": "n2",
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


def test_feedback_item_is_kind_tagged_with_mu_shape():
    """A Feedback Node body item -- the JSON counterpart of
    ``render_netlist``'s ``fb{k} := mu[z^-N](init -> …, recur -> …)`` line --
    is discriminated by ``kind="feedback"`` and carries the net name, the
    z^-N delay, and the mu ``init``/``recur`` sources (``init`` as a merge
    source that may be a type default; ``recur`` a NetRef or null)."""
    fb = NetlistFeedback(
        uid="3719",
        net="fb0",
        init=DefaultValue(literal="0.0", type_descriptor="DBL"),
        recur=_ref("now", "0", "now.0"),
        delay=1,
    )
    module = NetlistModule(vi_name="m.vi", inputs=[], outputs=[], body=[fb])

    d = netlist_to_dict(module)
    assert [item["kind"] for item in d["body"]] == ["feedback"]
    item = d["body"][0]
    assert item["uid"] == "3719"
    assert item["net"] == "fb0"
    assert item["delay"] == 1
    # init is a type-default merge source (never a fabricated wire).
    assert item["init"] == {"kind": "default", "type": "DBL", "literal": "0.0"}
    assert item["recur"]["bare"] == "now.0"

    # recur=None (never-written Feedback Node) serializes as null, not omitted.
    fb2 = NetlistFeedback(
        uid="9",
        net="fb1",
        init=_ref(None, "seed", "seed"),
        recur=None,
        delay=None,
    )
    d2 = netlist_to_dict(
        NetlistModule(vi_name="m.vi", inputs=[], outputs=[], body=[fb2])
    )
    assert d2["body"][0]["recur"] is None
    assert d2["body"][0]["delay"] is None


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
                label="True",
                value="1",
                is_default=False,
                body=[],
                passthrough=True,
            ),
            NetlistFrame(
                label="Default",
                value="Default",
                is_default=True,
                body=[],
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
                        source=DefaultValue(literal="0", type_descriptor="I32"),
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


def test_loop_scope_outputs_carries_mu_and_eta_merge_union_shape():
    """A loop scope's ``outputs`` -- the JSON counterpart of
    ``render_netlist``'s ``shift{k} := mu(...)``/``out{k} := eta(...)``
    lines -- carries one ``{"kind": "mu", ...}`` entry per shift register
    and one ``{"kind": "eta", ...}`` entry per output tunnel, alongside
    (never replacing) case scopes' ``{"kind": "gamma", ...}`` shape."""
    scope = NetlistScope(
        uid="12",
        kind="for",
        selector=None,
        frames=[NetlistFrame(label="", value="", is_default=False, body=[])],
        outputs=[
            MuMerge(
                net="loop0.shift0",
                init=DefaultValue(literal="0", type_descriptor="I32"),
                recur=_ref("Increment", "result", "result"),
            ),
            MuMerge(
                net="loop0.shift1",
                init=_ref("Seed", "0", "seed_net"),
                recur=None,
            ),
            EtaMerge(
                net="loop0.out0",
                index_mode="array",
                conditional=False,
                value=_ref("Accumulate", "result", "result"),
            ),
        ],
    )
    module = NetlistModule(vi_name="l.vi", inputs=[], outputs=[], body=[scope])

    d = netlist_to_dict(module)
    sc = d["body"][0]
    assert sc["scope_kind"] == "for"
    assert len(sc["outputs"]) == 3

    mu0 = sc["outputs"][0]
    assert mu0["net"] == "loop0.shift0"
    assert mu0["kind"] == "mu"
    assert mu0["init"] == {"kind": "default", "type": "I32", "literal": "0"}
    assert mu0["recur"]["bare"] == "result"
    assert "kind" not in mu0["recur"]

    mu1 = sc["outputs"][1]
    assert mu1["kind"] == "mu"
    assert mu1["init"]["bare"] == "seed_net"
    assert "kind" not in mu1["init"]
    # A shift register genuinely never written to has a null recur -- not
    # an unresolved placeholder.
    assert mu1["recur"] is None

    eta0 = sc["outputs"][2]
    assert eta0["net"] == "loop0.out0"
    assert eta0["kind"] == "eta"
    assert eta0["index_mode"] == "array"
    assert eta0["conditional"] is False
    assert eta0["value"]["bare"] == "result"
    assert "kind" not in eta0["value"]


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
            NetlistPortBinding(
                port="1", type="Boolean", net=_ref("Not Equal?", "result", "result")
            ),
            NetlistPortBinding(
                port="2", type="Boolean", net=_ref("Not Equal?", "result", "result")
            ),
        ],
        outputs=[_out("Compound Arithmetic", "0", "Compound Arithmetic#1.0")],
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


def test_input_binding_carries_inverted_flag():
    """Audit finding: an INPUT terminal's "Not" bubble
    (``NetlistPortBinding.inverted``, mirroring ``Terminal.inverted``) must be
    readable straight off the JSON binding -- a program can't tell
    ``x AND NOT y`` from ``x AND y`` by parsing rendered text. The inverted
    binding's own net identity (``bare``/``occurrence``) is UNCHANGED --
    ``inverted`` is an annotation on the binding, never on the net."""
    inst = NetlistInstance(
        uid="12",
        name="Compound Arithmetic",
        occurrence=2,
        inputs=[
            NetlistPortBinding(
                port="1",
                type="Boolean",
                net=_ref("Less?", "result", "result"),
            ),
            NetlistPortBinding(
                port="2",
                type="Boolean",
                net=_ref("Equal?", "equal", "equal", occ=2),
                inverted=True,
            ),
        ],
        outputs=[_out("Compound Arithmetic", "0", "Compound Arithmetic#2.0")],
        operation="and",
    )
    module = NetlistModule(vi_name="c.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    inputs = d["body"][0]["inputs"]
    assert inputs[0]["port"] == "1"
    assert inputs[0]["inverted"] is False
    assert inputs[1]["port"] == "2"
    assert inputs[1]["inverted"] is True
    # Net identity is untouched by the flag.
    assert inputs[1]["net"] == {
        "node": "Equal?",
        "port": "equal",
        "occurrence": 2,
        "bare": "equal",
    }


def test_property_node_instance_carries_structured_properties_and_object():
    """Audit finding: a Property Node used to be a black box in the JSON IR --
    which properties it accesses, and whether each is a read or a write, was
    completely lost (every value port rendered as a bare numeric index). The
    instance dict must now carry a structured ``properties`` list (name +
    direction + the net read from/written to) and the target object CLASS
    under ``object`` -- a program reads which properties without parsing
    text. Mirrors a real corpus Property Node with one write property
    (``Disabled``) and one read property (``Enabled``, e.g. a downstream
    query), targeting a "Bool" control -- shaped like the JKI-VI-Tester
    "Graphical Test Runner - Main UI" VI's own Property Nodes (see
    test_netlist.py's real-VI coverage)."""
    inst = NetlistInstance(
        uid="99",
        name="Property Node",
        occurrence=1,
        inputs=[
            NetlistPortBinding(
                port="0",
                type="refnum",
                net=_ref("Bundle/Unbundle By Name", "ref", "ref"),
            ),
            NetlistPortBinding(
                port="Disabled", type="Boolean", net=_ref(None, "True", "True")
            ),
        ],
        outputs=[
            _out("Property Node", "1", "Property Node#1.1"),
            _out("Property Node", "Enabled", "Enabled"),
        ],
        object_name="Bool",
        properties=[
            NetlistPropertyAccess(
                name="Disabled",
                direction="write",
                net=_ref(None, "True", "True"),
            ),
            NetlistPropertyAccess(
                name="Enabled",
                direction="read",
                net=_ref("Property Node", "Enabled", "Enabled", occ=1),
            ),
        ],
    )
    module = NetlistModule(vi_name="p.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    body_inst = d["body"][0]
    assert body_inst["object"] == "Bool"
    assert body_inst["properties"] == [
        {
            "name": "Disabled",
            "direction": "write",
            "net": {"node": None, "port": "True", "occurrence": None, "bare": "True"},
        },
        {
            "name": "Enabled",
            "direction": "read",
            "net": {
                "node": "Property Node",
                "port": "Enabled",
                "occurrence": 1,
                "bare": "Enabled",
            },
        },
    ]


def test_property_node_write_property_with_unwired_value_serializes_null_net():
    """A write property whose value terminal is genuinely unwired has no
    source net -- ``net`` must serialize as ``null``, never a fabricated
    reference (mirrors ``BoundaryOutput.source``'s None convention)."""
    inst = NetlistInstance(
        uid="100",
        name="Property Node",
        occurrence=None,
        inputs=[],
        outputs=[],
        object_name="Bool",
        properties=[
            NetlistPropertyAccess(name="Disabled", direction="write", net=None),
        ],
    )
    module = NetlistModule(vi_name="p.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    assert d["body"][0]["properties"] == [
        {"name": "Disabled", "direction": "write", "net": None},
    ]


def test_non_property_instance_has_no_object_and_empty_properties():
    """Regression: an ordinary instance (not a Property Node) must serialize
    exactly as before -- ``object`` is ``None``, ``properties`` is empty."""
    inst = NetlistInstance(
        uid="1",
        name="Not Equal?",
        occurrence=None,
        inputs=[NetlistPortBinding(port="x", type="I32", net=_ref(None, "n", "n"))],
        outputs=[_out("Not Equal?", "result", "result")],
    )
    module = NetlistModule(vi_name="p.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    body_inst = d["body"][0]
    assert body_inst["object"] is None
    assert body_inst["properties"] == []


def test_invoke_node_instance_carries_method_and_object():
    """Audit finding mirror for Invoke Node: the JSON instance dict must
    carry the invoked method -- the entire meaning of the node -- under
    ``method``, and the target object CLASS under ``object`` (the SAME key
    the Property Node work already added -- a program tells the two apart by
    whether ``method`` is null), so a program reads which method is called
    without parsing text. Mirrors uid 6753 of the real JKI-VI-Tester
    "Graphical Test Runner - Main UI" VI (Invoke Node#1, "Point To Row
    Column" on a "Tree (strict)" reference -- see test_netlist.py's real-VI
    coverage). Parameter ports are never named (unrecoverable from the VI
    file) and stay numeric."""
    inst = NetlistInstance(
        uid="6753",
        name="Invoke Node",
        occurrence=1,
        inputs=[
            NetlistPortBinding(
                port="0",
                type="refnum",
                net=_ref("Event Data Node", "3", "Event Data Node#12.3", occ=12),
            ),
            NetlistPortBinding(
                port="6",
                type="refnum",
                net=_ref("Event Data Node", "4", "Event Data Node#12.4", occ=12),
            ),
        ],
        outputs=[_out("Invoke Node", "1", "Invoke Node#1.1", occ=1)],
        object_name="Tree (strict)",
        method_name="Point To Row Column",
    )
    module = NetlistModule(vi_name="p.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    body_inst = d["body"][0]
    assert body_inst["method"] == "Point To Row Column"
    assert body_inst["object"] == "Tree (strict)"
    assert [b["port"] for b in body_inst["inputs"]] == ["0", "6"]
    assert body_inst["outputs"][0]["bare"] == "Invoke Node#1.1"


def test_invoke_node_without_object_name_has_null_object_but_keeps_method():
    """Faithfulness: when ``object_name`` genuinely isn't resolvable, the
    JSON must keep ``object`` null while ``method`` is still populated --
    never fabricate an object class that wasn't in the file."""
    inst = NetlistInstance(
        uid="1",
        name="Invoke Node",
        occurrence=None,
        inputs=[],
        outputs=[],
        method_name="Some Method",
    )
    module = NetlistModule(vi_name="p.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    body_inst = d["body"][0]
    assert body_inst["method"] == "Some Method"
    assert body_inst["object"] is None


def test_non_invoke_instance_has_no_method():
    """Regression: an ordinary instance (not an Invoke Node) must serialize
    exactly as before plus the new key -- ``method`` is ``None``."""
    inst = NetlistInstance(
        uid="1",
        name="Not Equal?",
        occurrence=None,
        inputs=[NetlistPortBinding(port="x", type="I32", net=_ref(None, "n", "n"))],
        outputs=[_out("Not Equal?", "result", "result")],
    )
    module = NetlistModule(vi_name="p.vi", inputs=[], outputs=[], body=[inst])

    d = netlist_to_dict(module)
    assert d["body"][0]["method"] is None


def test_non_case_scope_outputs_is_empty_list():
    """``outputs`` is always present (never omitted) but empty for every
    non-case scope kind -- loops/sequences/disabled/event structures don't
    have gamma merges."""
    scope = NetlistScope(
        uid="2",
        kind="sequence",
        selector=None,
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
                uid="1",
                kind="sequence",
                selector=None,
                frames=[
                    NetlistFrame(
                        label="0",
                        value="0",
                        is_default=False,
                        body=[],
                    )
                ],
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
