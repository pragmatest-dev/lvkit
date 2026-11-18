"""The tags of a VI or a library: the Tag methods of the VI Server.

A tag is a name and a Variant. NI: Tag:Get Tag "Returns a tag value associated with
the tag. Returns FALSE if the tag does not exist" (``Tag Name``; ``Is Persistent?``
and ``Value`` come back), Tag:Set Tag "Sets a tag value. Returns FALSE if LabVIEW
does not set the tag value" (``Tag Name``, ``Value``, ``Is Persistent?``, default
FALSE, and the ``Old Value``), Tag:Remove Tag "Removes the specified tag. Returns
FALSE if LabVIEW did not remove the tag" (and the ``Old Value``) (all
``properties-and-methods/vi-server/generic/``); Library Tag:Get, Set and Delete
(``properties-and-methods/vi-server/projectitem/library/``) are the same on a
library, Get returning ``Is Persistent`` "TRUE if you save the tag when you save the
project library". A persistent tag is saved with the file.

Each generated VI function carries the tags its file saves as ``function.tags``, a
``Tags`` of the package's ``panels`` module (``tags_statement``, set right after the
``def`` beside ``function.window_title``), and a call on a reference is a call on
``tags_of(reference)``: ``.get(name)`` gives ``(found, is_persistent, value)``,
``.set(name, value, persistent)`` and ``.remove(name)`` give ``(done, old value)``.
Nothing is saved back to a file, so Is Persistent only says what Get Tag then
reports. A Library Tag method is the same call on whatever ``tags_of`` is given; no
node of the generated code makes a Library reference (Library.Open is a gap), so its
tags are the ones a program sets.

The return of Get Lib Tag and Delete Lib Tag is not described by NI's pages; it is
read as the return of the Generic methods is: TRUE when the tag exists.

A call is keyed by its method code, the one the VI's node records."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind

from ..ast_utils import parse_expr
from ..constant_value import constant_value_expression
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..panels_module import (
    PANEL_ERROR_NAME,
    TAGS_NAME,
    TAGS_OF_NAME,
    UNDECODED_TAG_NAME,
)
from ..variant_values import variant_input
from . import ctrl_val

TAG_SET = 1037
TAG_GET = 1038
TAG_REMOVE = 1039
LIB_TAG_GET = 1292952580
LIB_TAG_SET = 1292952581
LIB_TAG_DELETE = 1292952582

_VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
_RETURN = 5
_NAME = 6


@dataclass(frozen=True)
class _Layout:
    """The terminals of a Tag method on the Invoke Node (indices as the corpus's
    nodes record them: a result at 5, then a pair of an input and an output
    terminal for each parameter from 6): the inputs and outputs that carry the
    value, the persistence, and the old value; None where the method has none."""

    value_in: int | None = None
    persistent_in: int | None = None
    value_out: int | None = None
    persistent_out: int | None = None
    old_out: int | None = None
    has_return: bool = True


_LAYOUTS: dict[int, _Layout] = {
    TAG_GET: _Layout(value_out=11, persistent_out=9),
    TAG_SET: _Layout(value_in=8, persistent_in=10, old_out=13),
    TAG_REMOVE: _Layout(old_out=9),
    LIB_TAG_GET: _Layout(value_out=9, persistent_out=11),
    LIB_TAG_SET: _Layout(value_in=8, persistent_in=10, has_return=False),
    LIB_TAG_DELETE: _Layout(),
}


def is_tag_method(node: PrimitiveNode) -> bool:
    """Whether ``node`` is an Invoke Node calling a Tag method on a reference."""
    return node.method_code in _LAYOUTS


def _targets(
    node: PrimitiveNode, ctx: CodeGenContext, names: list[tuple[int | None, str]]
) -> tuple[list[ast.expr], dict[str, str]]:
    """The assignment targets for the outputs at the given indices (a ``_`` for
    one that is not wired) and the bindings of the wired ones."""
    elements: list[ast.expr] = []
    bindings: dict[str, str] = {}
    for index, hint in names:
        terminal = (
            ctrl_val.terminal_at(node, "output", index) if index is not None else None
        )
        if terminal is None or not ctx.is_wired(terminal.id):
            elements.append(ast.Name(id="_", ctx=ast.Store()))
            continue
        variable = ctx.make_output_var(hint, node.id, terminal.id)
        elements.append(ast.Name(id=variable, ctx=ast.Store()))
        bindings[terminal.id] = variable
    return elements, bindings


def _wired(node: PrimitiveNode, ctx: CodeGenContext, index: int | None) -> bool:
    terminal = ctrl_val.terminal_at(node, "output", index) if index else None
    return terminal is not None and ctx.is_wired(terminal.id)


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """``tags_of(reference)`` called for the method; an error of the reference
    is the node's own error (``ctrl_val.report_panel_error``)."""
    layout = _LAYOUTS[node.method_code or 0]
    reference = ctrl_val.reference_value(node, ctx)
    name = ctrl_val.input_value(node, ctx, _NAME, repr(""))
    tags = f"{TAGS_OF_NAME}({reference})"
    code = node.method_code
    wants_old = _wired(node, ctx, layout.old_out)
    if code in (TAG_GET, LIB_TAG_GET):
        call = f"{tags}.get({name})"
        elements, bindings = _targets(
            node,
            ctx,
            [
                (_RETURN, "found"),
                (layout.persistent_out, "is_persistent"),
                (layout.value_out, "value"),
            ],
        )
    elif code in (TAG_SET, LIB_TAG_SET):
        assert layout.value_in is not None and layout.persistent_in is not None
        value = variant_input(
            ctx, node, ctrl_val.terminal_at(node, "input", layout.value_in)
        )
        persistent = ctrl_val.input_value(node, ctx, layout.persistent_in, "False")
        call = f"{tags}.set({name}, {value}, {persistent}, {wants_old})"
        elements, bindings = _targets(
            node,
            ctx,
            [(_RETURN if layout.has_return else None, "done"), (layout.old_out, "old")],
        )
    else:
        arguments = f"{name}, {wants_old}" if layout.old_out is not None else name
        call = f"{tags}.remove({arguments})"
        elements, bindings = _targets(
            node,
            ctx,
            [(_RETURN, "removed"), (layout.old_out, "old_value")],
        )
    reference_out = ctrl_val.terminal_at(node, "output", ctrl_val.REFERENCE_OUT)
    if reference_out is not None:
        bindings[reference_out.id] = reference
    statement: ast.stmt = ast.Assign(
        targets=[ast.Tuple(elts=elements, ctx=ast.Store())], value=parse_expr(call)
    )
    if all(isinstance(e, ast.Name) and e.id == "_" for e in elements):
        statement = ast.Expr(value=parse_expr(call))
    ctx.import_panels(f"{TAGS_OF_NAME}, {PANEL_ERROR_NAME}")
    return ctrl_val.report_panel_error(node, ctx, [statement], bindings)


def tags_statements(ctx: CodeGenContext, key: str, target: str) -> list[ast.stmt]:
    """``target.tags = Tags("<VI name>", {name: Variant, ...})`` for the VI ``key``
    from the tags its file saves; nothing when it saves none. A value that is not
    decoded, or that has no Python form, is an ``UndecodedTag`` with its reason."""
    if ctx.graph is None:
        return []
    saved = ctx.graph.get_vi_tags(key)
    if not saved:
        return []
    owner = ctx.graph.vi_display_name(key)
    keys: list[ast.expr | None] = []
    values: list[ast.expr] = []
    undecoded = False
    for tag in saved:
        keys.append(ast.Constant(value=tag.name))
        text = None
        reason = tag.undecoded
        if tag.value is not None:
            try:
                text = constant_value_expression(
                    ctx, _VARIANT, tag.value, f"the tag {tag.name!r}"
                )
            except ConversionGap as gap:
                reason = gap.summary()
        if text is None:
            undecoded = True
            values.append(
                ast.Call(
                    func=ast.Name(id=UNDECODED_TAG_NAME, ctx=ast.Load()),
                    args=[ast.Constant(value=reason or "")],
                    keywords=[],
                )
            )
        else:
            values.append(parse_expr(text))
    imports = f"{TAGS_NAME}, {UNDECODED_TAG_NAME}" if undecoded else TAGS_NAME
    ctx.import_panels(imports, at_module_level=True)
    return [
        ast.Assign(
            targets=[
                ast.Attribute(value=parse_expr(target), attr="tags", ctx=ast.Store())
            ],
            value=ast.Call(
                func=ast.Name(id=TAGS_NAME, ctx=ast.Load()),
                args=[ast.Constant(value=owner), ast.Dict(keys=keys, values=values)],
                keywords=[],
            ),
        )
    ]
