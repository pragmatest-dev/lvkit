"""Execute Property Node fragments with exact row/terminal identities."""

from __future__ import annotations

import ast
import getpass
import types

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.errors import CodeGenError
from lvkit.codegen.nodes import generate as generate_node
from lvkit.codegen.nodes import property_node
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.models import (
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
)
from tests.helpers import error_cluster_type, make_graph_with_terminals

REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
ERROR = error_cluster_type()


class Reference:
    def __init__(self):
        self.__dict__["values"] = {"value": 100, "other": 200}
        self.__dict__["events"] = []

    def __getattr__(self, name):
        value = self.values[name]
        self.events.append(("read", name, value))
        return value

    def __setattr__(self, name, value):
        self.events.append(("write", name, value))
        self.values[name] = value


def prop(*names):
    """A Property Node row whose path is ``names`` (``Label``, ``Text``)."""
    return PropertyDef(segments=[PropertySegment(name=n, code=0) for n in names])


def fixture(
    rows, *, reverse=False, ref_out=False, unwired=(), ref_value="ref", class_name=None
):
    fixed = [
        Terminal(id="ref_in", index=0, direction="input", lv_type=REF),
        Terminal(id="ref_out", index=1, direction="output", lv_type=REF),
        Terminal(id="error_in", index=2, direction="input", lv_type=ERROR),
        Terminal(id="error_out", index=3, direction="output", lv_type=ERROR),
    ]
    values = [
        Terminal(id=f"row_{i}", index=4 + i, direction=direction)
        for i, (_, direction) in enumerate(rows)
    ]
    terminals = fixed + values
    node = PrimitiveNode(
        id="property",
        vi_path="test.vi",
        name="Property Node",
        node_type="propNode",
        terminals=list(reversed(terminals)) if reverse else terminals,
        properties=[prop(*name.split(".")) for name, _ in rows],
        property_value_terminal_ids=[term.id for term in values],
        object_name=class_name,
    )
    ids = [term.id for term in terminals]
    graph = make_graph_with_terminals(*ids, *(f"peer_{tid}" for tid in ids))
    ctx = CodeGenContext(graph=graph)
    if ref_value:
        ctx.bind("ref_in", ref_value)
    namespace = {"ref": Reference()}
    for i, term in enumerate(values):
        if term.id not in unwired:
            connect(ctx, term.id, term.direction)
        if term.direction == "input":
            ctx.bind(f"peer_{term.id}", f"supplied_{i}")
            namespace[f"supplied_{i}"] = 1000 + i
    if ref_out:
        connect(ctx, "ref_out", "output")
    return node, ctx, namespace


def connect(ctx, tid, direction):
    assert ctx.graph is not None
    src, dst = (f"peer_{tid}", tid) if direction == "input" else (tid, f"peer_{tid}")
    src_node, dst_node = ctx.graph._term_to_node[src], ctx.graph._term_to_node[dst]
    ctx.graph._graph.add_edge(
        src_node,
        dst_node,
        source=WireEnd(terminal_id=src, node_id=src_node),
        dest=WireEnd(terminal_id=dst, node_id=dst_node),
    )


def execute(node, ctx, namespace):
    fragment = property_node.generate(node, ctx)
    code = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    exec(compile(code, "property-test", "exec"), namespace)
    return {tid: namespace[name] for tid, name in fragment.bindings.items()}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ref_out", [False, True])
@pytest.mark.parametrize(
    "rows",
    [
        [("Value", "input"), ("Value", "output")],
        [("Value", "output"), ("Value", "input")],
        [("Value", "output"), ("Value", "input"), ("Value", "output")],
        [
            ("Value", "output"),
            ("Value", "input"),
            ("Value", "output"),
            ("Value", "input"),
            ("Value", "output"),
        ],
        [("Value", "input"), ("Other", "output"), ("Value", "output")],
    ],
)
def test_ordered_accesses_are_independent_of_port_storage(rows, reverse, ref_out):
    node, ctx, namespace = fixture(rows, reverse=reverse, ref_out=ref_out)
    state = {"value": 100, "other": 200}
    expected, events = {}, []
    for i, (name, direction) in enumerate(rows):
        attr = name.lower()
        if direction == "input":
            state[attr] = 1000 + i
            events.append(("write", attr, state[attr]))
        else:
            events.append(("read", attr, state[attr]))
            expected[f"row_{i}"] = state[attr]
    if ref_out:
        expected["ref_out"] = namespace["ref"]
    assert execute(node, ctx, namespace) == expected
    assert namespace["ref"].events == events
    assert namespace["ref"].values == state


def test_unwired_reads_still_execute_once_and_keep_distinct_snapshots():
    rows = [
        ("Value", "output"),
        ("Value", "input"),
        ("Value", "output"),
        ("Value", "output"),
    ]
    node, ctx, namespace = fixture(rows, unwired=("row_0", "row_3"))
    assert execute(node, ctx, namespace) == {"row_0": 100, "row_2": 1001, "row_3": 1001}
    assert namespace["ref"].events == [
        ("read", "value", 100),
        ("write", "value", 1001),
        ("read", "value", 1001),
        ("read", "value", 1001),
    ]


@pytest.mark.parametrize("value_type", [REF, ERROR])
def test_value_type_does_not_reclassify_a_property_as_fixed_port(value_type):
    node, ctx, namespace = fixture([("Value", "output")], ref_out=True)
    node.terminals[4].lv_type = value_type
    sentinel = object()
    namespace["ref"].values["value"] = sentinel
    result = execute(node, ctx, namespace)
    assert result["row_0"] is sentinel
    assert result["ref_out"] is namespace["ref"]
    assert namespace["ref"].events == [("read", "value", sentinel)]


def test_a_write_wired_from_a_gap_constant_writes_none():
    node, ctx, namespace = fixture([("Value", "input")])
    ctx.bind("peer_row_0", "None")
    assert execute(node, ctx, namespace) == {}
    assert namespace["ref"].events == [("write", "value", None)]


@pytest.mark.parametrize(
    "failure",
    [
        "missing_ids",
        "duplicate_ids",
        "missing_terminal",
        "duplicate_terminal",
        "missing_name",
        "name_collision",
        "implicit_ref",
        "unwired_write",
        "unresolved_write",
        "unknown_fixed_output",
    ],
)
def test_unresolved_or_unsupported_nodes_fail_without_guessing(failure):
    node, ctx, namespace = fixture([("Value", "input"), ("Value", "output")])
    if failure == "missing_ids":
        node.property_value_terminal_ids = []
    elif failure == "duplicate_ids":
        node.property_value_terminal_ids = ["row_0", "row_0"]
    elif failure == "missing_terminal":
        node.property_value_terminal_ids[1] = "absent"
    elif failure == "duplicate_terminal":
        node.terminals.append(node.terminals[-1].model_copy())
    elif failure == "missing_name":
        node.properties[0] = prop("")
    elif failure == "name_collision":
        node.properties = [prop("Some Value"), prop("Some_Value")]
    elif failure == "implicit_ref":
        node.bound_control_uid = "stored-control-identity"
    elif failure == "unwired_write":
        node, ctx, namespace = fixture(
            [("Value", "input"), ("Value", "output")], unwired=("row_0",)
        )
    elif failure == "unresolved_write":
        ctx.graph.set_var_name("peer_row_0", None)  # type: ignore[union-attr,arg-type]
    else:
        node.terminals[1].lv_type = None
        connect(ctx, "ref_out", "output")
    with pytest.raises((CodeGenError, ConversionGap)):
        property_node.generate(node, ctx)
    assert namespace["ref"].events == []


def test_wired_error_terminals_are_skipped():
    """Error clusters become Python exceptions: wired error in/out add nothing."""
    node, ctx, _namespace = fixture([("Value", "input"), ("Value", "output")])
    connect(ctx, "error_in", "input")
    ctx.bind("peer_error_in", "error")
    connect(ctx, "error_out", "output")
    fragment = property_node.generate(node, ctx)
    assert "error_out" not in fragment.bindings
    assert len(fragment.statements) == 2


def test_nested_property_writes_then_reads_its_attribute_chain():
    """A nested property (``Label.Text``) is one row: an attribute chain."""
    node, ctx, namespace = fixture([("Label.Text", "input"), ("Label.Text", "output")])
    namespace["ref"] = types.SimpleNamespace(label=types.SimpleNamespace(text="old"))
    assert execute(node, ctx, namespace) == {"row_1": 1000}
    assert namespace["ref"].label.text == 1000


def _generate_unwired_app(rows, ni_name="App.UserName"):
    node, ctx, namespace = fixture(rows, ref_value=None, class_name="App")
    node.properties = [PropertyDef(segments=[PropertySegment(name=ni_name, code=1)])]
    fragment = property_node.generate(node, ctx)
    code = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    return fragment, code, namespace


def test_unwired_app_user_name_is_the_account_running_the_process():
    fragment, code, namespace = _generate_unwired_app([("Value", "output")])
    assert ast.unparse(code) == "appusername = getpass.getuser()"
    assert fragment.imports == {"import getpass"}
    exec(compile(code, "property-test", "exec"), {"getpass": getpass}, namespace)
    assert namespace["appusername"] == getpass.getuser()


def test_an_undocumented_default_object_property_stops_generation():
    with pytest.raises(ConversionGap, match="'App.Kind' of the default App object"):
        _generate_unwired_app([("Value", "output")], "App.Kind")


def test_writing_a_default_object_property_stops_generation():
    with pytest.raises(ConversionGap, match="'App.UserName' is written on the default"):
        _generate_unwired_app([("Value", "input")])


def test_unwired_reference_without_an_object_class_is_an_error():
    node, ctx, _ = fixture([("Value", "output")], ref_value=None)
    with pytest.raises(ConversionGap, match="no object class"):
        property_node.generate(node, ctx)


def test_wired_reference_without_a_value_is_an_error():
    node, ctx, _ = fixture(
        [("Value", "output")], ref_value=None, class_name="Application"
    )
    connect(ctx, "ref_in", "input")
    with pytest.raises(
        CodeGenError, match="The wired input 0 of 'Property Node' has no variable"
    ):
        property_node.generate(node, ctx)


def test_an_undocumented_default_object_property_becomes_a_raise():
    node, ctx, _ = fixture([("Value", "output")], ref_value=None, class_name="App")
    node.properties = [PropertyDef(segments=[PropertySegment(name="App.Kind", code=1)])]
    fragment = generate_node(node, ctx)
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    source = ast.unparse(module)
    assert source.splitlines()[-1] == (
        "raise NotImplementedError(\"Property 'App.Kind' of the default App "
        'object has no Python equivalent\\ngap kind: unsupported_node\\nVI: \\n'
        'node: Property Node")'
    )
    assert fragment.imports == set()
    assert [g.reason for g in ctx.gaps] == [
        "Property 'App.Kind' of the default App object has no Python equivalent"
    ]
