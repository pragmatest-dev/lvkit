"""The vi.lib Picture, pixmap and graphics-format VIs (``picture.llb``,
``pictutil.llb``, ``pixmap.llb``, ``png.llb``, ``bmp.llb``, ``jpeg.llb``): a
Picture is a Pillow image, so each VI's Python is the template of its catalog
entry's ``op`` in ``picture_values`` (the representation and the rules NI leaves
open are recorded there), filled in like an inline catalog template.

A graphics-format VI with no path wired shows a file dialog in LabVIEW ("If you do
not specify a path, LabVIEW displays a file dialog box", NI, Read PNG File VI);
Python has no dialog, so that call is a gap.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode, VINode
from lvkit.models import LVTypeKind, Terminal, TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import field_attribute_names
from ..cluster_members import is_anonymous_cluster
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..picture_values import (
    FILE_PATH_PARAMS,
    IMAGE_DATA_PARAMS,
    IMAGE_DATA_UNPACK,
    IMPORTS,
    TEMPLATES,
)


def concatenate_pictures(
    node: PrimitiveNode, ctx: CodeGenContext, inputs: list[Terminal], output: Terminal
) -> CodeFragment:
    """Concatenate Strings of Pictures: a Picture is a list of drawing operations,
    and NI's page says the function can "concatenate the output from Picture
    Functions VIs so that they draw on a single picture control" in order
    (docs.ni.com labview-api-ref Concatenate Strings), so the later inputs draw
    over the earlier ones. A Picture is an RGBA image that is transparent where
    nothing is drawn (``picture_values``), so the result is a new image as large as
    the largest input with each input alpha-composited onto it at (0, 0), in
    terminal order, and the last input's pen position. An unwired input, or one that
    is not a Picture, is the empty picture (``None``) and draws nothing; no input
    drawing anything is the empty picture."""
    pictures = []
    for inp in sorted(inputs, key=lambda t: t.index):
        lv_type = inp.lv_type
        is_picture = lv_type is not None and lv_type.underlying_type == "Picture"
        value = ctx.wired_input(inp.id, node) if is_picture else None
        if value:
            pictures.append(value)
    var = ctx.make_output_var(output.name or "picture", node.id, terminal_id=output.id)
    for statement in IMPORTS:
        ctx.add_import(statement)
    statements = ast.parse(
        f"_pictures = [_p for _p in [{', '.join(pictures)}] if _p is not None]\n"
        f"if not _pictures:\n"
        f"    {var} = None\n"
        "else:\n"
        f"    {var} = Image.new('RGBA', (max(_p.width for _p in _pictures),"
        " max(_p.height for _p in _pictures)))\n"
        "    for _p in _pictures:\n"
        f"        {var}.alpha_composite(_p)\n"
        f"    {var}.info['pen'] = _pictures[-1].info.get('pen', (0, 0))\n"
    ).body
    return CodeFragment(statements=statements, bindings={output.id: var})


def _path_is_wired(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, param: str
) -> bool:
    index = next(
        t.index
        for t in entry.terminals
        if t.python_param == param and t.direction == TerminalDirection.INPUT
    )
    term = next(
        (
            t
            for t in node.terminals
            if t.index == index and t.direction == TerminalDirection.INPUT
        ),
        None,
    )
    return term is not None and ctx.wired_input(term.id, node) is not None


def _attribute_names(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, param: str, direction: str
) -> list[str] | None:
    """The attribute names of the image data cluster wired to ``param`` when its
    type is a named cluster (a typedef: one representation per cluster type, an
    attribute object); None when it is an anonymous cluster (a tuple) or the call
    has no such terminal."""
    vilib_term = next(
        (
            t
            for t in entry.terminals
            if t.python_param == param and t.direction == direction
        ),
        None,
    )
    if vilib_term is None:
        return None
    term = next(
        (
            t
            for t in ctx.enriched_terminals(node)
            if t.index == vilib_term.index and t.direction == direction
        ),
        None,
    )
    lv_type = term.lv_type if term is not None else None
    if (
        lv_type is None
        or lv_type.kind != LVTypeKind.CLUSTER
        or is_anonymous_cluster(lv_type)
    ):
        return None
    fields = (ctx.graph.get_type_fields(lv_type) if ctx.graph else None) or (
        lv_type.fields
    )
    if not fields:
        return None
    return field_attribute_names(f.name for f in fields)


def _in_cluster_form(
    template: str, node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> str:
    """``template`` with each image data cluster it reads or makes in the form
    its wire's type calls for: the tuple of the template, or the typedef's
    attribute object (``SimpleNamespace``, the form a typedef default and a
    Bundle into a typedef make)."""
    names = _attribute_names(node, ctx, entry, "image_data", "input")
    if names is not None:
        read = ", ".join(f"_cluster.{name}" for name in names)
        template = template.replace(
            IMAGE_DATA_UNPACK,
            "_cluster = {image_data}\n"
            + IMAGE_DATA_UNPACK.replace("{image_data}", read),
        )
    for param in IMAGE_DATA_PARAMS:
        names = _attribute_names(node, ctx, entry, param, "output")
        if names is None:
            continue
        ctx.add_import("from types import SimpleNamespace")
        marker = "{" + param + "} = "
        keys = repr(tuple(names))
        template = "".join(
            f"{marker}SimpleNamespace(**dict(zip({keys}, {line[len(marker) : -1]})))\n"
            if line.startswith(marker)
            else line
            for line in template.splitlines(keepends=True)
        )
    return template


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """One call of a picture VI, written from the template of its op."""
    from . import subvi

    assert entry.op is not None
    template = _in_cluster_form(TEMPLATES[entry.op], node, ctx, entry)
    path_param = FILE_PATH_PARAMS.get(entry.op)
    if path_param is not None:
        if not _path_is_wired(node, ctx, entry, path_param):
            raise ConversionGap(
                f"{entry.name} with no path shows a file dialog, which Python "
                "does not have",
                node,
            )
        template = template.replace("{path_in}", "{" + path_param + "}")
    return subvi.generate_inline(
        node,
        ctx,
        entry.model_copy(
            update={"python_code": template, "imports": [*entry.imports, *IMPORTS]}
        ),
    )
