"""Execute Property Node fragments with exact row/terminal identities."""

from __future__ import annotations

import ast
import getpass
import inspect
import re

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.errors import CodeGenError
from lvkit.codegen.gaps import gap_fragment
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


VI_CODES = {"Name": 500, "Path": 501}


def vi_fixture(rows, **kwargs):
    """A Property Node on a VI reference whose rows are VI Name / Path reads."""
    node, ctx, namespace = fixture(rows, class_name="VI", **kwargs)
    node.properties = [
        PropertyDef(segments=[PropertySegment(name=name, code=VI_CODES[name])])
        for name, _ in rows
    ]

    def target():
        return None

    namespace["ref"] = target
    namespace["inspect"] = inspect
    return node, ctx, namespace


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ref_out", [False, True])
@pytest.mark.parametrize(
    "rows",
    [
        [("Name", "output"), ("Path", "output")],
        [("Path", "output"), ("Name", "output"), ("Path", "output")],
        [("Name", "output"), ("Name", "output")],
    ],
)
def test_ordered_reads_are_independent_of_port_storage(rows, reverse, ref_out):
    node, ctx, namespace = vi_fixture(rows, reverse=reverse, ref_out=ref_out)
    reads = {"Name": "target", "Path": inspect.getfile(namespace["ref"])}
    expected = {f"row_{i}": reads[name] for i, (name, _) in enumerate(rows)}
    if ref_out:
        expected["ref_out"] = namespace["ref"]
    assert execute(node, ctx, namespace) == expected


def test_unwired_reads_still_execute_once_and_keep_distinct_snapshots():
    rows = [("Name", "output"), ("Path", "output"), ("Name", "output")]
    node, ctx, namespace = vi_fixture(rows, unwired=("row_0",))
    fragment = property_node.generate(node, ctx)
    source = ast.unparse(
        ast.fix_missing_locations(ast.Module(body=fragment.statements, type_ignores=[]))
    )
    assert source.splitlines() == [
        "ref_name = ref.__name__",
        "ref_path = inspect.getfile(ref)",
        "ref_name_2 = ref.__name__",
    ]
    assert fragment.bindings["row_0"] == "ref_name"
    assert fragment.bindings["row_1"] == "ref_path"
    assert fragment.bindings["row_2"] == "ref_name_2"


@pytest.mark.parametrize("value_type", [REF, ERROR])
def test_value_type_does_not_reclassify_a_property_as_fixed_port(value_type):
    node, ctx, namespace = vi_fixture([("Name", "output")], ref_out=True)
    node.terminals[4].lv_type = value_type
    result = execute(node, ctx, namespace)
    assert result["row_0"] == "target"
    assert result["ref_out"] is namespace["ref"]


@pytest.mark.parametrize("class_name", ["VI", "Ctl", "DAQmx Timing", None])
def test_a_property_read_with_no_python_equivalent_is_a_gap(class_name):
    node, ctx, _ = fixture([("Panel", "output")], class_name=class_name)
    on_class = f" on a {class_name} reference" if class_name else ""
    with pytest.raises(
        ConversionGap,
        match=re.escape(f"Property 'Panel'{on_class} has no Python equivalent"),
    ):
        property_node.generate(node, ctx)


def test_a_property_write_with_no_python_equivalent_is_a_gap():
    node, ctx, _ = fixture([("Panel", "input")], class_name="VI")
    with pytest.raises(
        ConversionGap,
        match=re.escape("Property 'Panel' on a VI reference has no Python equivalent"),
    ):
        property_node.generate(node, ctx)


def test_writing_a_mapped_vi_property_is_a_gap():
    node, ctx, _ = vi_fixture([("Name", "input")])
    with pytest.raises(
        ConversionGap,
        match=re.escape("Property 'Name' on a VI reference has no Python equivalent"),
    ):
        property_node.generate(node, ctx)


def test_a_gap_property_row_raises_where_it_runs():
    """A node with a gap property row makes ``generate`` raise at run time."""
    node, ctx, _ = fixture([("Panel", "output")], class_name="VI")
    with pytest.raises(ConversionGap) as gap:
        property_node.generate(node, ctx)
    fragment = gap_fragment(node, ctx, gap.value.summary())
    source = ast.unparse(
        ast.fix_missing_locations(ast.Module(body=fragment.statements, type_ignores=[]))
    )
    with pytest.raises(NotImplementedError, match="Property 'Panel' on a VI"):
        exec(compile(source, "gap", "exec"), {})


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
    node, ctx, _namespace = vi_fixture([("Name", "output"), ("Path", "output")])
    connect(ctx, "error_in", "input")
    ctx.bind("peer_error_in", "error")
    connect(ctx, "error_out", "output")
    fragment = property_node.generate(node, ctx)
    assert "error_out" not in fragment.bindings
    assert len(fragment.statements) == 2


def _generate_unwired(rows, class_name, ni_name, code):
    node, ctx, namespace = fixture(rows, ref_value=None, class_name=class_name)
    ctx.function_name = "my_function"
    node.properties = [PropertyDef(segments=[PropertySegment(name=ni_name, code=code)])]
    fragment = property_node.generate(node, ctx)
    code = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    return fragment, code, namespace


def _generate_unwired_app(rows, ni_name="App.UserName", code=1):
    return _generate_unwired(rows, "App", ni_name, code)


def test_unwired_app_user_name_is_the_account_running_the_process():
    fragment, code, namespace = _generate_unwired_app([("Value", "output")])
    assert ast.unparse(code) == "appusername = getpass.getuser()"
    assert fragment.imports == {"import getpass"}
    exec(compile(code, "property-test", "exec"), {"getpass": getpass}, namespace)
    assert namespace["appusername"] == getpass.getuser()


def test_an_undocumented_default_object_property_stops_generation():
    with pytest.raises(ConversionGap, match="'App.Kind' of the default App object"):
        _generate_unwired_app([("Value", "output")], "App.Kind", 3)


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
    node.properties = [PropertyDef(segments=[PropertySegment(name="App.Kind", code=3)])]
    fragment = generate_node(node, ctx)
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    source = ast.unparse(module)
    assert source.splitlines()[-1] == (
        "raise NotImplementedError(\"Property 'App.Kind' of the default App "
        "object has no Python equivalent\\ngap kind: unsupported_node\\nVI: \\n"
        'node: Property Node")'
    )
    assert fragment.imports == set()
    assert [g.reason for g in ctx.gaps] == [
        "Property 'App.Kind' of the default App object has no Python equivalent"
    ]


def test_the_property_code_decides_not_the_stored_name():
    with pytest.raises(ConversionGap, match="'App.UserName' of the default App"):
        _generate_unwired_app([("Value", "output")], "App.UserName", 3)


def test_an_unwired_app_directory_path_by_its_long_name():
    _, long_code, _ = _generate_unwired_app(
        [("Value", "output")], "Application:Directory Path", 25
    )
    _, short_code, _ = _generate_unwired_app([("Value", "output")], "App.Dir", 25)
    expression = "Path(sys.argv[0]).resolve().parent"
    assert ast.unparse(long_code).endswith(f" = {expression}")
    assert ast.unparse(short_code).endswith(f" = {expression}")


@pytest.mark.parametrize("stored_name", ["Name", "VI Name"])
def test_an_unwired_vi_name_reads_the_function_name_by_either_name(stored_name):
    fragment, code, namespace = _generate_unwired(
        [("Value", "output")], "VI", stored_name, 500
    )
    assert ast.unparse(code).endswith(" = my_function.__name__")

    def my_function():
        pass

    namespace["my_function"] = my_function
    (variable,) = fragment.bindings.values()
    exec(compile(code, "property-test", "exec"), namespace)
    assert namespace[variable] == "my_function"


def _wire_from_this_application(ctx, class_name="Application"):
    """Wire the Property Node's reference input from a "This Application"
    constant, whose wire carries ``None``."""
    from lvkit.graph.models import LocalVariableNode
    from lvkit.models import ServerClass

    reference_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="LVObjCtl",
        server_class=ServerClass.APPLICATION,
    )
    app = LocalVariableNode(
        id="app",
        vi_path="test.vi",
        name="This Application",
        node_type="ctlRefConst",
        control_name="This Application",
        terminals=[
            Terminal(id="app_out", index=0, direction="output", lv_type=reference_type)
        ],
    )
    graph = ctx.graph
    graph._graph.add_node("app", node=app)
    graph._term_to_node["app_out"] = "app"
    graph._graph.add_edge(
        "app",
        graph._term_to_node["ref_in"],
        source=WireEnd(terminal_id="app_out", node_id="app"),
        dest=WireEnd(terminal_id="ref_in", node_id=graph._term_to_node["ref_in"]),
    )
    ctx.bind("app_out", "None")
    ctx.bind("ref_in", "None")
    return app


def test_this_application_reference_gives_none_and_generates_nothing_else():
    from lvkit.codegen.nodes import local_variable

    node, ctx, _ = fixture([("Value", "output")], ref_value=None, class_name="App")
    app = _wire_from_this_application(ctx)
    fragment = local_variable.generate(app, ctx)
    assert fragment.statements == []
    assert fragment.bindings == {"app_out": "None"}


def test_a_property_read_on_this_application_is_the_default_object_property():
    node, ctx, namespace = fixture(
        [("Value", "output")], ref_value=None, class_name="App"
    )
    _wire_from_this_application(ctx)
    node.properties = [
        PropertyDef(segments=[PropertySegment(name="App.UserName", code=1)])
    ]
    fragment = property_node.generate(node, ctx)
    code = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    assert ast.unparse(code) == "appusername = getpass.getuser()"
    exec(compile(code, "property-test", "exec"), {"getpass": getpass}, namespace)
    assert namespace["appusername"] == getpass.getuser()


def test_an_undocumented_property_on_this_application_is_its_own_gap():
    node, ctx, _ = fixture([("Value", "output")], ref_value=None, class_name="App")
    _wire_from_this_application(ctx)
    node.properties = [PropertyDef(segments=[PropertySegment(name="App.Kind", code=3)])]
    with pytest.raises(ConversionGap, match="'App.Kind' of the default App object"):
        property_node.generate(node, ctx)


def test_an_invoke_on_this_application_is_its_own_gap():
    from lvkit.codegen.nodes import invoke_node

    node, ctx, _ = fixture([("Value", "output")], ref_value=None, class_name="App")
    _wire_from_this_application(ctx)
    node.method_name = "LVClass.Open"
    with pytest.raises(
        ConversionGap, match="method 'LVClass.Open' on the default App object"
    ):
        invoke_node.generate(node, ctx)
