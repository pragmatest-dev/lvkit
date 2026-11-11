"""VI references: Open VI Reference, Close Reference, Call By Reference and
Start Asynchronous Call.

A VI is a Python function, so a VI reference is the function itself and the VI
it holds is found when the code is generated, not when it runs:

- a Static VI Reference imports the function (``codegen/context.py``);
- Open VI Reference imports the function of the VI its path or name input names
  (``InMemoryVIGraph.opened_vi``);
- Call By Reference calls that function with the called VI's own parameter
  names; Start Asynchronous Call submits the call to a thread pool;
- Close Reference of any reference generates nothing;
- the Name and Path properties of a VI reference are the function's own
  ``__name__`` and file (``vi_property_read``).

A reference to a member VI of a class is ``Class.method`` and the call passes
the object as its ``self`` keyword; Call By Reference does not support a dynamic
dispatch member VI, so a reference to one is a gap.

The nodes that call through a reference have four terminals that are not
terminals of the called VI: the VI reference in (index -1) and out (-2), then
error in (-3) and out (-4). The terminals from index 0 up are the called VI's
connector pane. The node's own error in skips the call when an error came in
(``error_flow.own_error_terminals``); its own error out reports a call that
could not be made, a reference value that is Not a Refnum. The called VI's
error in and error out are a parameter and a result of the call like any other.
"""

from __future__ import annotations

import ast
import os
import posixpath
import textwrap
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, PrimitiveNode, VINode
from lvkit.graph.vi_reference import (
    CALL_REFERENCE_IN,
    CALL_REFERENCE_OUT,
    OPEN_VI_PATH_INDEX,
    OPEN_VI_REFERENCE_INDEX,
    VI_CLASS,
    VI_EXEC_STATE_PROPERTY_CODE,
    VI_NAME_PROPERTY_CODE,
    VI_PATH_PROPERTY_CODE,
    ReferencedVI,
    VIReferenceComputed,
    VIReferenceUnresolved,
)
from lvkit.models import LVType, PaneSlot, PropertyDef, Terminal
from lvkit.vi_files import class_stem

from ..ast_utils import (
    GENERATED_MODULE_NAMES,
    build_assign,
    module_part,
    parse_expr,
    to_var_name,
)
from ..callee_names import CalleeNames, pane_names
from ..class_attributes import run_time_name
from ..class_model import (
    MethodKind,
    accessor_of,
    member_shape,
    not_a_method,
    python_class_name,
)
from ..context import CodeGenContext
from ..error_flow import assigned_names, output_defaults, own_error_terminals
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..front_panels_module import FRONT_PANEL_NAME
from ..import_resolver import ImportResolver
from ..native_failure import labview_error
from ..pane_candidates import CallForm, candidates
from . import method_call, primitive, subvi

OPEN_VI_REFERENCE_PRIM_ID = 8010
CLOSE_REFERENCE_PRIM_ID = 8011

# NI, General LabVIEW Error Codes: 1026 "VI Reference is invalid."
INVALID_VI_REFERENCE_CODE = 1026

# Open VI Reference options: bit 0x80 opens the VI to be called and forgotten.
CALL_AND_FORGET = 0x80

_EXECUTOR = "_async_call_pool"
_EXECUTOR_IMPORT = "from concurrent.futures import ThreadPoolExecutor"

# LabVIEW's reason a Call By Reference cannot call a dynamic dispatch member VI.
_DYNAMIC_MEMBER = (
    "Call By Reference does not support dynamic member VIs (NI: "
    "https://docs-be.ni.com/api/bundle/labview-api-ref/page/errors/"
    "dynamic-terminal-on-call-by-reference.html)"
)


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
    prelude: list[ast.stmt] = field(default_factory=list)


def _reference_input(node: VINode, ctx: CodeGenContext) -> Terminal:
    reference = _frame_terminal(node, ctx, CALL_REFERENCE_IN)
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
    shape = member_shape(ctx.graph, target.key)
    if shape is not None:
        reason = not_a_method(ctx.graph, target.key)
        if reason is not None:
            raise ConversionGap(reason, node)
        if shape.kind is MethodKind.DYNAMIC:
            raise ConversionGap(
                f"{_label(node)}: {target.name} is a dynamic dispatch member VI; "
                + _DYNAMIC_MEMBER,
                node,
            )
        call = method_call.build_call(callee, ctx, shape, receiver_keyword="self")
        result_var = call.result_var or ctx.make_output_var(
            "vi_reference_result", node.id
        )
        return _Call(
            ctx.vi_reference_expression(target.name, target.key),
            call.keywords,
            result_var,
            call.bindings,
            call.prelude,
        )
    parts = subvi.build_call(callee, ctx, target.name, target.key, None)
    return _Call(
        parts.func_name,
        parts.call.keywords,
        parts.result_var,
        parts.bindings,
        parts.prelude,
    )


def _value_call(node: VINode, ctx: CodeGenContext, reference: Terminal) -> _Call:
    """A call of the function the reference holds when the code runs, with the
    keyword names its strictly typed refnum records for the connector pane."""
    function = ctx.wired_input(reference.id, node)
    pane = pane_of(reference.lv_type)
    if function is None or pane is None:
        raise ConversionGap(
            f"{_label(node)}: which VI the reference holds is only known when the "
            "diagram runs, and its type records no connector pane to name the "
            "parameters by.",
            node,
        )
    if any(slot.dynamic_dispatch for slot in pane):
        raise ConversionGap(
            f"{_label(node)}: the reference's connector pane has a dynamic "
            "dispatch terminal; " + _DYNAMIC_MEMBER,
            node,
        )
    assert ctx.graph is not None and ctx.vi_name is not None
    form = _one_form(node, ctx, pane)
    if form is not None and form.member is not None:
        call = method_call.build_call(node, ctx, form.member, receiver_keyword="self")
        result_var = call.result_var or ctx.make_output_var(
            "vi_reference_result", node.id
        )
        return _Call(function, call.keywords, result_var, call.bindings, call.prelude)
    names = (
        CalleeNames(parameters=dict(form.keywords), fields=dict(form.fields))
        if form is not None
        else pane_names(pane)
    )
    labelled = {slot.index for slot in pane if slot.name}
    keywords: list[ast.keyword] = []
    result_var = ctx.make_output_var("vi_reference_result", node.id)
    bindings: dict[str, str] = {}
    for term in ctx.enriched_terminals(node):
        if term.index is None or term.index < 0:
            continue
        if term.index not in labelled:
            continue
        if term.index not in (
            names.parameters if term.direction == "input" else names.fields
        ):
            continue
        if term.direction == "input":
            value = ctx.wired_input(term.id, node)
            if value is not None:
                keywords.append(
                    ast.keyword(
                        arg=names.parameters[term.index], value=parse_expr(value)
                    )
                )
        else:
            bindings[term.id] = f"{result_var}.{names.fields[term.index]}"
    return _Call(function, keywords, result_var, bindings)


def _one_form(
    node: VINode, ctx: CodeGenContext, pane: tuple[PaneSlot, ...]
) -> CallForm | None:
    """The call right for every VI of the project the reference value can hold
    (``pane_candidates``): the VIs with the reference's connector pane, each
    named as its own function or method names its terminals. None when the
    project has no such VI (the call names them by the pane's labels); a gap
    when they are not all called the same way."""
    assert ctx.graph is not None and ctx.vi_name is not None
    found = candidates(ctx.graph, pane, ctx.vi_name)
    if not found.forms:
        return None
    form = found.one_form()
    if form is None:
        shown = ", ".join(ctx.graph.vi_display_name(k) for k in sorted(found.forms))
        raise ConversionGap(
            f"{_label(node)}: the VI the reference holds is only known when the "
            f"diagram runs, and the project's VIs with its connector pane ({shown}) "
            "are not all called the same way: their parameters or results are "
            "named differently, or a method of a class takes its object as self.",
            node,
        )
    return form


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
    known_when_generated = True
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
            known_when_generated = False
        else:
            call = _static_call(node, ctx, target)
            options = target.options
    # A started call goes on without the caller, which reads none of its
    # results.
    bindings = dict(call.bindings) if style is CallStyle.CALL else {}
    reference_out = _frame_terminal(node, ctx, CALL_REFERENCE_OUT)
    if reference_out is not None:
        bindings[reference_out.id] = call.function
    fragment = CodeFragment(
        statements=[*call.prelude, _invocation(node, ctx, style, call, options)],
        bindings=bindings,
    )
    if known_when_generated:
        return fragment
    return _made_only_with_a_vi(node, ctx, call.function, fragment)


def _invocation(
    node: VINode,
    ctx: CodeGenContext,
    style: CallStyle,
    call: _Call,
    options: int | None,
) -> ast.stmt:
    """The statement that makes the call: the call itself, or (Start
    Asynchronous Call) its start on the thread pool."""
    if style is CallStyle.CALL:
        invoke = ast.Call(
            func=parse_expr(call.function), args=[], keywords=call.keywords
        )
        return build_assign(call.result_var, invoke)
    submit = submit_to_pool(ctx, call.function, call.keywords)
    if options is not None and options & CALL_AND_FORGET:
        return ast.Expr(value=submit)
    return build_assign(ctx.make_output_var("future", node.id), submit)


def _made_only_with_a_vi(
    node: VINode, ctx: CodeGenContext, reference: str, fragment: CodeFragment
) -> CodeFragment:
    """``fragment``, the call through the reference value ``reference``, made
    only when the value holds a VI. A reference that is Not a Refnum (``None``)
    makes no call: the node's outputs take their defaults and its own error is
    error 1026, "VI Reference is invalid." (NI, General LabVIEW Error Codes),
    which its error out reports unless an error came in (``error_flow``). A
    node whose error out is not wired has nowhere to report it, so calling the
    reference stays what fails, as a native failure does
    (``native_failure``)."""
    _, error_outs = own_error_terminals(node, ctx)
    if not any(ctx.is_wired(term.id) for term in error_outs):
        return fragment
    statements = list(fragment.statements)
    # The called VI's outputs (its connector pane, from index 0) are read from
    # the call's result only when the call is made.
    for term in ctx.enriched_terminals(node):
        value = fragment.bindings.get(term.id)
        if (
            term.direction != "output"
            or term.index is None
            or term.index < 0
            or value is None
        ):
            continue
        var = ctx.make_output_var(to_var_name(term.name or "value"), node.id, term.id)
        statements.append(build_assign(var, parse_expr(value)))
        fragment.bindings[term.id] = var
    own_error = ctx.make_output_var("own_error", node.id)
    defaults = output_defaults(
        node, ctx, fragment.bindings, assigned_names(statements), None
    )
    ctx.import_labview_error()
    invalid = labview_error(INVALID_VI_REFERENCE_CODE, _label(node))
    fragment.statements = [
        ast.If(
            test=parse_expr(f"({reference}) is None"),
            body=[*defaults, build_assign(own_error, invalid)],
            orelse=[*statements, build_assign(own_error, parse_expr("None"))],
        )
    ]
    fragment.own_error = own_error
    return fragment


def submit_to_pool(
    ctx: CodeGenContext, function: str, keywords: list[ast.keyword]
) -> ast.Call:
    """``function(**keywords)`` started on the module's thread pool, which this
    adds: the call goes on while the caller does not wait for it. Its value is
    the call's ``Future``."""
    ctx.add_import(_EXECUTOR_IMPORT)
    ctx.add_module_global(_EXECUTOR, parse_expr("ThreadPoolExecutor()"))
    return ast.Call(
        func=parse_expr(f"{_EXECUTOR}.submit"),
        args=[parse_expr(function)],
        keywords=keywords,
    )


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
    func_name = ctx.vi_reference_expression(target.name, target.key)
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
{rel} = os.path.relpath({path}, {here}).replace(os.sep, "/")
{parts} = {rel}.split("/")
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

# A VI a class lists is a method of the class, found by the name or the path
# the diagram computes among the project's class members.
_CLASS_MEMBER = """\
{member} = _class_members.get({key})
if {member} is not None:
    {reference} = getattr(
        getattr(importlib.import_module({member}[0], __package__), {member}[1]),
        {member}[2],
    )
else:
"""

_IMPORT_MODULE = """\
{suffix} = ""
if {stem}.lower().endswith((".vi", ".vim", ".vit")):
    {stem}, {suffix} = {stem}.rsplit(".", 1)
    {suffix} = {{"vim": "_vim", "vit": "_vit"}}.get({suffix}.lower(), "")
{module} = importlib.import_module(
    {package} + _module_part({stem} + {suffix}), __package__
)
{reference} = getattr({module}, _function_name({stem}), None)
if {reference} is None:
    raise LookupError(
        f"the module of {{{stem}!r}} has no function for it (a polymorphic VI "
        "has none: only its variants do)"
    )
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
    value = ctx.wired_input(path_term.id, node) if path_term is not None else None
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
        for key in (
            "here",
            "path",
            "parts",
            "up",
            "stem",
            "suffix",
            "package",
            "owner",
            "module",
        )
    }
    names.update(value=value, reference=reference_var)
    names["rel"] = f"{reference_var}_rel"
    names["member"] = f"{reference_var}_member"
    has_members = bool(ctx.graph.class_members())
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
        # These helpers call each other by name, so they cannot be renamed.
        if ctx.add_module_function(func_def) != func_def.name:
            raise CodeGenError(
                f"The module-level name {func_def.name} is taken by an import"
            )
    text = (locate + _IMPORT_MODULE).format(**names)
    if has_members:
        _class_members_table(node, ctx)
        names["key"] = value if by_name else names["rel"]
        if by_name:
            head, rest = "", text
        else:
            head, rest = text.split(f"{names['parts']} = ", 1)
            rest = f"{names['parts']} = " + rest
        text = head + _CLASS_MEMBER.format(**names) + textwrap.indent(rest, "    ")
    statements = ast.parse(text).body
    bindings = {reference.id: reference_var} if reference is not None else {}
    return CodeFragment(statements=statements, bindings=bindings)


def _class_members_table(node: PrimitiveNode, ctx: CodeGenContext) -> None:
    """The module-level dictionary of the project's class members, by the name
    (``Owner:VI.vi``) and by the path from the folder of the VI holding the node
    that a diagram can compute, each with its class's module spec, its class's
    name and its method's name."""
    assert ctx.graph is not None and ctx.vi_name is not None
    resolver = ctx.import_resolver
    # A path computed when the code runs is read from the folder of the module
    # being written (the resolver's ``module_key``: a class, a polymorphic VI,
    # the VI itself).
    module_key = (
        resolver.module_key
        if isinstance(resolver, ImportResolver) and resolver.module_key
        else ctx.graph.owning_class_key(ctx.vi_name) or ctx.vi_name
    )
    entries: dict[str, tuple[str, str, str]] = {}
    for name, key, class_key in ctx.graph.class_members():
        shape = member_shape(ctx.graph, key)
        if (
            shape is None
            or not_a_method(ctx.graph, key) is not None
            or accessor_of(ctx.graph, key) is not None
        ):
            continue
        spec = (
            resolver.package_spec(class_key)
            if isinstance(resolver, ImportResolver)
            else "."
        )
        module = spec + module_part(class_stem(Path(class_key).name))
        class_name = python_class_name(class_key)
        entry = (module, class_name, run_time_name(class_name, shape.name))
        entries[name] = entry
        entries[_path_from(resolver, key, module_key)] = entry
    ctx.add_module_global(
        "_class_members",
        ast.Dict(
            keys=[ast.Constant(value=name) for name in sorted(entries)],
            values=[
                ast.Tuple(
                    elts=[ast.Constant(value=part) for part in entries[name]],
                    ctx=ast.Load(),
                )
                for name in sorted(entries)
            ],
        ),
    )


def _path_from(resolver: object, key: str, module_key: str) -> str:
    """The path of the file ``key`` from the folder of the module generated for
    ``module_key``, both by the project-relative paths modules are placed by."""
    if isinstance(resolver, ImportResolver):
        member = resolver.shown(key)
        folder = posixpath.dirname(resolver.shown(module_key))
        return posixpath.relpath(member, folder or ".")
    return Path(os.path.relpath(key, os.path.dirname(module_key))).as_posix()


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


def vi_property_read(
    node: PrimitiveNode, prop: PropertyDef, reference: str, ctx: CodeGenContext
) -> ast.expr | None:
    """The read of a property of a VI reference as Python reads it from the
    function: Name is the function's name, Path its file, Exec.State the state of
    its front panel (``FrontPanel.state``: 1 Idle, 2 Run top level while it runs).
    None for a property of another class, or one with no such reading. The
    imports the read needs are added to ``ctx``."""
    if node.object_name != VI_CLASS or len(prop.segments) != 1:
        return None
    code = prop.segments[0].code
    if code == VI_NAME_PROPERTY_CODE:
        return parse_expr(f"{reference}.__name__")
    if code == VI_PATH_PROPERTY_CODE:
        ctx.add_import("import inspect")
        return parse_expr(f"inspect.getfile({reference})")
    if code == VI_EXEC_STATE_PROPERTY_CODE:
        ctx.import_front_panel()
        return parse_expr(f"{FRONT_PANEL_NAME}.of({reference}).state")
    return None
