"""VI references: Open VI Reference, Close Reference, Call By Reference and
Start Asynchronous Call.

A VI is a Python function, so a VI reference is the function itself and the VI
it holds is found when the code is generated, not when it runs:

- a Static VI Reference imports the function (``codegen/context.py``);
- Open VI Reference imports the function of the VI its path or name input names
  (``InMemoryVIGraph.opened_vi``);
- Call By Reference calls that function with the called VI's own parameter
  names; Start Asynchronous Call submits the call to a thread pool;
- Close Reference of a VI reference generates nothing;
- the Name and Path properties of a VI reference are the function's own
  ``__name__`` and file (``vi_property_read``).

A reference whose VI is only known when the diagram runs stops code generation.

The nodes that call through a reference have four terminals that are not
terminals of the called VI: the VI reference in (index -1) and out (-2), then
error in (-3) and out (-4). The terminals from index 0 up are the called VI's
connector pane.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, PrimitiveNode, VINode
from lvkit.graph.vi_reference import (
    OPEN_VI_PATH_INDEX,
    OPEN_VI_REFERENCE_INDEX,
    VI_CLASS,
    VI_NAME_PROPERTY_CODE,
    VI_PATH_PROPERTY_CODE,
    ReferencedVI,
    VIReferenceComputed,
    VIReferenceUnresolved,
)
from lvkit.models import LVType, PaneSlot, PropertyDef, Terminal

from ..ast_utils import GENERATED_MODULE_NAMES, build_assign, parse_expr
from ..callee_names import pane_names
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver
from . import primitive, subvi

OPEN_VI_REFERENCE_PRIM_ID = 8010
CLOSE_REFERENCE_PRIM_ID = 8011

_REFERENCE_IN = -1
_REFERENCE_OUT = -2

# Open VI Reference options: bit 0x80 opens the VI to be called and forgotten.
CALL_AND_FORGET = 0x80

# Close Reference's reference input (data/primitives/application-control.json).
_CLOSE_REFERENCE_INDEX = 11

_EXECUTOR = "_async_call_pool"
_EXECUTOR_IMPORT = "from concurrent.futures import ThreadPoolExecutor"


class CallStyle(IntEnum):
    """How a Call By Reference node calls its VI, from the node's saved
    ``CBRSaveStyle`` byte: 0 in every plain Call By Reference node of the corpus,
    1 in every Start Asynchronous Call node."""

    CALL = 0
    START = 1


def _label(node: AnyGraphNode) -> str:
    return node.label or node.name or "VI reference node"


def _fail(node: AnyGraphNode, problem: VIReferenceUnresolved) -> ConversionGap:
    return ConversionGap(f"{_label(node)}: {problem}", node)


def _frame_terminal(
    node: AnyGraphNode, ctx: CodeGenContext, index: int
) -> Terminal | None:
    return next((t for t in ctx.enriched_terminals(node) if t.index == index), None)


def _call_style(node: VINode) -> CallStyle:
    saved = node.call_save_style
    try:
        return CallStyle(0 if saved is None else saved)
    except ValueError:
        raise ConversionGap(
            f"{_label(node)}: this kind of call by reference (saved style "
            f"{saved:#x}) is not supported yet; Wait On Asynchronous Call has no "
            "code generation yet.",
            node,
        ) from None


@dataclass
class _Call:
    """What a node calls: the callable's expression, the keyword arguments, the
    variable the result is assigned to and the node's outputs read from it."""

    function: str
    keywords: list[ast.keyword]
    result_var: str
    bindings: dict[str, str]


def _reference_input(node: VINode, ctx: CodeGenContext) -> Terminal:
    reference = _frame_terminal(node, ctx, _REFERENCE_IN)
    if reference is None or not ctx.is_wired(reference.id) or ctx.graph is None:
        if node.qualified_path:
            raise ConversionGap(
                f"{_label(node)}: the VI chosen in the node's setup, "
                f"{node.qualified_path!r}, is not loaded with this VI.",
                node,
            )
        raise ConversionGap(
            f"{_label(node)}: the VI reference input is not wired and the node "
            "records no VI of its own.",
            node,
        )
    return reference


def _static_call(node: VINode, ctx: CodeGenContext, target: ReferencedVI) -> _Call:
    """A call of the function of ``target``, with that VI's own parameter names
    (the node is copied with the target as its callee so its terminals carry
    them)."""
    assert ctx.graph is not None
    callee = node.model_copy(update={"callee_key": target.key, "name": target.name})
    if ctx.graph.loaded_callee_key(callee) is None:
        raise ConversionGap(
            f"{_label(node)}: the VI {target.name!r} is not loaded with this VI.",
            node,
        )
    parts = subvi.build_call(callee, ctx, target.name, target.key, None)
    return _Call(parts.func_name, parts.call.keywords, parts.result_var, parts.bindings)


def _value_call(node: VINode, ctx: CodeGenContext, reference: Terminal) -> _Call:
    """A call of the function the reference holds when the code runs, with the
    keyword names its strictly typed refnum records for the connector pane."""
    function = ctx.resolve(reference.id)
    pane = pane_of(reference.lv_type)
    if function is None or pane is None:
        raise ConversionGap(
            f"{_label(node)}: which VI the reference holds is only known when the "
            "diagram runs, and its type records no connector pane to name the "
            "parameters by.",
            node,
        )
    names = pane_names(pane)
    labelled = {slot.index for slot in pane if slot.name}
    keywords: list[ast.keyword] = []
    result_var = ctx.make_output_var("vi_reference_result", node.id)
    bindings: dict[str, str] = {}
    for term in ctx.enriched_terminals(node):
        if term.index is None or term.index < 0 or term.is_error_cluster:
            continue
        if term.index not in labelled:
            continue
        if term.index not in (
            names.parameters if term.direction == "input" else names.fields
        ):
            continue
        if term.direction == "input":
            value = ctx.resolve(term.id)
            if value is not None:
                keywords.append(
                    ast.keyword(
                        arg=names.parameters[term.index], value=parse_expr(value)
                    )
                )
        else:
            bindings[term.id] = f"{result_var}.{names.fields[term.index]}"
    return _Call(function, keywords, result_var, bindings)


def _input_terminal(node: PrimitiveNode, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )


def pane_of(lv_type: LVType | None) -> tuple[PaneSlot, ...] | None:
    """The connector pane a strictly typed VI refnum records."""
    if lv_type is None or lv_type.element_type is None:
        return None
    return lv_type.element_type.pane


def generate_call(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Call By Reference, or Start Asynchronous Call: a call of the function of
    the VI the reference holds, found when the code is generated or, when the
    reference is a value of the running code, taken from the value."""
    style = _call_style(node)
    assert ctx.graph is not None
    options: int | None = None
    if node.callee_key is not None:
        # The VI is chosen in the node's setup; the caller records it.
        name = node.qualified_name or Path(node.callee_key).name
        call = _static_call(node, ctx, ReferencedVI(name, node.callee_key))
    else:
        reference = _reference_input(node, ctx)
        try:
            target = ctx.graph.referenced_vi(reference.id)
        except VIReferenceUnresolved:
            call = _value_call(node, ctx, reference)
        else:
            call = _static_call(node, ctx, target)
            options = target.options
    bindings = dict(call.bindings)
    reference_out = _frame_terminal(node, ctx, _REFERENCE_OUT)
    if reference_out is not None:
        bindings[reference_out.id] = call.function

    if style is CallStyle.CALL:
        invoke = ast.Call(
            func=parse_expr(call.function), args=[], keywords=call.keywords
        )
        return CodeFragment(
            statements=[build_assign(call.result_var, invoke)], bindings=bindings
        )

    ctx.add_import(_EXECUTOR_IMPORT)
    ctx.add_module_global(_EXECUTOR, parse_expr("ThreadPoolExecutor()"))
    submit = ast.Call(
        func=parse_expr(f"{_EXECUTOR}.submit"),
        args=[parse_expr(call.function)],
        keywords=call.keywords,
    )
    if options is not None and options & CALL_AND_FORGET:
        return CodeFragment(statements=[ast.Expr(value=submit)], bindings=bindings)
    future = ctx.make_output_var("future", node.id)
    return CodeFragment(statements=[build_assign(future, submit)], bindings=bindings)


def generate_open(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Open VI Reference: the function of the VI its path or name input names;
    when that is computed when the diagram runs, a lookup of the computed path or
    name among the project's VIs."""
    if ctx.graph is None:
        raise CodeGenError(f"{_label(node)}: there is no graph to read.", node)
    path_term = next((t for t in node.terminals if t.index == OPEN_VI_PATH_INDEX), None)
    if path_term is None or not ctx.is_wired(path_term.id):
        return primitive.generate(node, ctx)
    reference = next(
        (t for t in node.terminals if t.index == OPEN_VI_REFERENCE_INDEX), None
    )
    try:
        target = ctx.graph.opened_vi(node)
    except VIReferenceComputed:
        return _generate_lookup(node, ctx, reference)
    except VIReferenceUnresolved as problem:
        raise _fail(node, problem) from None
    func_name = ctx.import_function(target.name, target.key)
    bindings = {reference.id: func_name} if reference is not None else {}
    return CodeFragment(bindings=bindings)


# The transform that turns one part of a path or name into a module or package
# name, as generated code applies it to a path or name computed when it runs.
# It is ``ast_utils.module_part``; ``tests/test_vi_reference.py`` checks the two
# agree.
MODULE_PART_SOURCE = """\
def _module_part(name):
    name = "".join(
        c for c in name.lower().replace(" ", "_").replace("-", "_")
        if c.isalnum() or c == "_"
    )
    if name and not (name[0].isalpha() or name[0] == "_"):
        name = "var_" + name
    if keyword.iskeyword(name):
        name += "_"
    return name or "module"
"""

# A VI's function is its module name, with ``_`` after a keyword, a builtin or a
# module the generated code imports (``ast_utils.RESERVED_NAMES``).
FUNCTION_NAME_SOURCE = """\
def _function_name(stem):
    name = _module_part(stem)
    if keyword.iskeyword(name) or name in dir(builtins) or name in {modules}:
        return name + "_"
    return name
""".replace("{modules}", repr(sorted(GENERATED_MODULE_NAMES)))

_IMPORT_BY_PATH = """\
{here} = os.path.dirname(__file__)
{path} = os.path.join({here}, os.fspath({value}).replace("\\\\", "/"))
{parts} = os.path.relpath({path}, {here}).replace(os.sep, "/").split("/")
{up} = 0
while {parts}[0] == "..":
    {parts}.pop(0)
    {up} += 1
{stem} = {parts}.pop()
{package} = "." * ({up} + 1) + "".join(_module_part(p) + "." for p in {parts})
"""

_IMPORT_BY_NAME = """\
{owner}, _, {stem} = {value}.rpartition(":")
{package} = {owners}[{owner}]
"""

_IMPORT_MODULE = """\
if {stem}.lower().endswith((".vi", ".vim", ".vit")):
    {stem} = {stem}.rsplit(".", 1)[0]
{module} = importlib.import_module({package} + _module_part({stem}), __package__)
{reference} = getattr({module}, _function_name({stem}))
"""


def _generate_lookup(
    node: PrimitiveNode, ctx: CodeGenContext, reference: Terminal | None
) -> CodeFragment:
    """An Open VI Reference of a path or name computed when the code runs: the
    module that path or name is, imported with ``importlib``. Modules mirror the
    project's folders (``module_layout.py``), so a path is each folder and the file
    stem put through the same transform, read from the folder of the VI holding the
    node; a full delimited name is an owner (a library or class, found in a
    dictionary of the project's owners and their packages) and a file stem."""
    assert ctx.graph is not None
    path_term = next((t for t in node.terminals if t.index == OPEN_VI_PATH_INDEX), None)
    value = ctx.resolve(path_term.id) if path_term is not None else None
    if path_term is None or value is None:
        raise CodeGenError(f"{_label(node)}: the VI path has no value.", node)
    by_name = _computed_by_name(path_term, ctx)
    reference_var = (
        ctx.make_output_var("vi_reference", node.id, reference.id)
        if reference is not None
        else ctx.make_output_var("vi_reference", node.id)
    )
    names = {
        key: f"{reference_var}_{key}"
        for key in ("here", "path", "parts", "up", "stem", "package", "owner", "module")
    }
    names.update(value=value, reference=reference_var)
    if by_name:
        names["owners"] = _owners_table(node, ctx)
        locate = _IMPORT_BY_NAME
        ctx.add_import("import importlib")
    else:
        locate = _IMPORT_BY_PATH
        ctx.add_import("import importlib")
        ctx.add_import("import os")
    ctx.add_import("import keyword")
    ctx.add_import("import builtins")
    for helper in (MODULE_PART_SOURCE, FUNCTION_NAME_SOURCE):
        (func_def,) = ast.parse(helper).body
        assert isinstance(func_def, ast.FunctionDef)
        ctx.add_module_function(func_def)
    statements = ast.parse((locate + _IMPORT_MODULE).format(**names)).body
    bindings = {reference.id: reference_var} if reference is not None else {}
    return CodeFragment(statements=statements, bindings=bindings)


def _owners_table(node: PrimitiveNode, ctx: CodeGenContext) -> str:
    """The module-level dictionary from each owner's full delimited name to the
    package holding the modules of its VIs, as the start of an import spec
    (the dots, then the dotted package and a final dot)."""
    assert ctx.graph is not None
    resolver = ctx.import_resolver
    owners = ctx.graph.owner_files()
    table = "_vi_owners"
    ctx.add_module_global(
        table,
        ast.Dict(
            keys=[ast.Constant(value=name) for name in owners],
            values=[
                ast.Constant(
                    value=resolver.package_spec(key)
                    if isinstance(resolver, ImportResolver)
                    else "."
                )
                for key in owners.values()
            ],
        ),
    )
    return table


def _computed_by_name(path_term: Terminal, ctx: CodeGenContext) -> bool:
    """True when the computed input is a string (a VI name), False when it is a
    path; anything else stops generation."""
    assert ctx.graph is not None
    kinds: set[str | None] = set()
    for _, source in ctx.graph.wire_origins(path_term.id):
        origin = ctx.graph.get_terminal(source.terminal_id)
        kinds.add(origin.lv_type.underlying_type if origin and origin.lv_type else None)
    if kinds == {"String"}:
        return True
    if kinds == {"Path"}:
        return False
    raise ConversionGap(
        "Open VI Reference: the computed VI path is neither a path nor a string.",
    )


def closes_vi_reference(node: PrimitiveNode, ctx: CodeGenContext) -> bool:
    """True when a Close Reference node closes a VI reference: its reference
    input is wired from a Static VI Reference or an Open VI Reference."""
    reference = next(
        (t for t in node.terminals if t.index == _CLOSE_REFERENCE_INDEX), None
    )
    return (
        reference is not None
        and ctx.graph is not None
        and ctx.graph.holds_vi_reference(reference.id)
    )


def vi_property_read(
    node: PrimitiveNode, prop: PropertyDef, reference: str
) -> ast.expr | None:
    """The read of a property of a VI reference as Python reads it from the
    function: Name is the function's name, Path its file. None for a property of
    another class, or one with no such reading."""
    if node.object_name != VI_CLASS or len(prop.segments) != 1:
        return None
    code = prop.segments[0].code
    if code == VI_NAME_PROPERTY_CODE:
        return parse_expr(f"{reference}.__name__")
    if code == VI_PATH_PROPERTY_CODE:
        return parse_expr(f"inspect.getfile({reference})")
    return None
