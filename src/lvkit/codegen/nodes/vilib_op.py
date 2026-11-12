"""vi.lib / user.lib VIs whose Python the generator writes from the graph, not
from a catalog template: the catalog entry names an ``op`` and this module
writes it.

``TYPE_DESCRIPTOR_CODE`` (OpenG ``Get TDEnum from Data__ogtk``): the VI returns
the type-descriptor code of the data wired to it, in the numbering of OpenG's
``Type Descriptor Enumeration__ogtk.ctl`` (its items' values are LabVIEW's
type-descriptor codes: 0x01 I8 ... 0x0A DBL, 0x21 Boolean, 0x30 String, 0x40
Array, 0x50 Cluster, 0x54 Waveform; see ``labview-binary-format.md``). The
type of the wire is recorded in the caller, so the code is a constant, read
from that type's descriptor (``LVType.underlying_type``, a ``TD_FULL_TYPE``
name). A variant records its type: the code is read from it when the code runs
(0, Void, for an empty variant).
"""

from __future__ import annotations

from collections.abc import Callable

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.lv_type_codes import is_variant_type_name, type_descriptor_code
from lvkit.models import TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..output_binding import bind_output


def _type_descriptor_code(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    data = next(
        (t for t in entry.terminals if t.direction is TerminalDirection.INPUT), None
    )
    result = next(
        (t for t in entry.terminals if t.direction is TerminalDirection.OUTPUT), None
    )
    assert data is not None and result is not None, entry.name
    out = next(
        (
            t
            for t in node.terminals
            if t.index == result.index and t.direction == TerminalDirection.OUTPUT
        ),
        None,
    )
    if out is None or not ctx.is_wired(out.id):
        return CodeFragment.empty()
    wired = next(
        (
            t
            for t in node.terminals
            if t.index == data.index and t.direction == TerminalDirection.INPUT
        ),
        None,
    )
    lv_type = None
    if ctx.graph is not None and wired is not None:
        sources = ctx.graph.incoming_edges(wired.id)
        source = ctx.graph.get_terminal(sources[0].terminal_id) if sources else None
        lv_type = source.lv_type if source is not None else None
    if lv_type is not None and is_variant_type_name(lv_type.underlying_type):
        variant = ctx.wired_input(wired.id, node) if wired is not None else None
        if variant is None:
            raise ConversionGap(f"{entry.name} has no variant wired to it", node)
        read = f"({variant}.type.code if {variant}.type is not None else 0)"
        base_name = result.python_param or "type_descriptor_enumeration"
        return bind_output(node, out.id, base_name, parse_expr(read), ctx)
    code = type_descriptor_code(lv_type) if lv_type is not None else None
    if code is None:
        raise ConversionGap(
            f"{entry.name}: the type of the data wired to it is not recorded "
            "as a LabVIEW type descriptor",
            node,
        )
    base_name = result.python_param or "type_descriptor_enumeration"
    return bind_output(node, out.id, base_name, parse_expr(str(code)), ctx)


_HANDLERS: dict[str, Callable[[VINode, CodeGenContext, VIEntry], CodeFragment]] = {
    "TYPE_DESCRIPTOR_CODE": _type_descriptor_code,
}


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """The Python of a call of ``entry``, whose ``op`` names how it is written;
    an op no handler writes is an lvkit error in the catalog."""
    assert entry.op is not None
    handler = _HANDLERS.get(entry.op)
    if handler is None:
        raise ValueError(f"no generator for the catalog op {entry.op!r}")
    return handler(node, ctx, entry)
