"""``Get LV Class Name.vi`` (``<vilib>/Utility/LVClass``), and the
``__lv_class_name__`` every generated class carries for it.

NI, "Get LV Class Name" (labview-api-ref, vi-lib/utility/lvclass): "Returns the name
of the class of the object"; ``class name`` "returns the class name of object". NI
gives no format. A class is named by its qualified name, the libraries that own it
and then its file (``Lib.lvlib:Class.lvclass``, a nested library
``Outer.lvlib:Inner.lvlib:Class.lvclass``): LAVA, "LV Object: Full Qualified Name"
(lavag.org/topic/14230) writes the class of a wire that way, and LabVIEW's flattened
object holds the class name library-qualified (labviewwiki.org, "LabVIEW Object":
``Actor Framework.lvlib`` then ``Actor.lvclass``). No page found gives the string
this VI itself returns, so the qualified name is a recorded default. LabVIEW Object,
the class every class descends from, has no file: it is named ``LabVIEW Object``
(``type_names.ROOT_CLASS``), and its objects are Python's ``object``.

Each generated class sets ``__lv_class_name__`` to its qualified name in its body
(``class_name_statement``), and the VI reads it from the type of the object it is
given, so an object of a child class on a parent's wire answers its own class. The
name is a dunder so no private data field of the class can take it.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VINode
from lvkit.vilib_resolver import VIEntry

from ...type_names import ROOT_CLASS
from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .grpc_pane import output_of, wired

CLASS_NAME_ATTRIBUTE = "__lv_class_name__"


def class_name_statement(graph: InMemoryVIGraph, class_key: str) -> ast.stmt:
    """``__lv_class_name__ = "<qualified name>"`` for the class ``class_key``."""
    return build_assign(
        CLASS_NAME_ATTRIBUTE, ast.Constant(value=graph.vi_display_name(class_key))
    )


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """The qualified name of the class of the wired object."""
    out = output_of(node, entry, "class_name")
    if out is None or not ctx.is_wired(out.id):
        return CodeFragment.empty()
    value = wired(node, entry, "object_", ctx)
    if value is None:
        raise ConversionGap("no object is wired to it", node)
    var = ctx.make_output_var("class_name", node.id, terminal_id=out.id)
    return CodeFragment(
        statements=[
            build_assign(
                var,
                parse_expr(
                    f"getattr(type({value}), {CLASS_NAME_ATTRIBUTE!r}, {ROOT_CLASS!r})"
                ),
            )
        ],
        bindings={out.id: var},
    )
