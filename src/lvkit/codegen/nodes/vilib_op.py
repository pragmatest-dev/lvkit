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
name). Data whose type is only known when the code runs (a variant) is a gap.
"""

from __future__ import annotations

from collections.abc import Callable

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.lv_type_codes import type_descriptor_code
from lvkit.models import TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment

_VARIANT_TYPES = ("LVVariant", "Variant")


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
    if lv_type is not None and lv_type.underlying_type in _VARIANT_TYPES:
        raise ConversionGap(
            f"{entry.name} reads the type of a variant, which is only known when "
            "the code runs; the generated code has no LabVIEW type for a value",
            node,
        )
    code = type_descriptor_code(lv_type) if lv_type is not None else None
    if code is None:
        raise ConversionGap(
            f"{entry.name}: the type of the data wired to it is not recorded "
            "as a LabVIEW type descriptor",
            node,
        )
    variable = ctx.make_output_var(
        result.python_param or "type_descriptor_enumeration",
        node.id,
        terminal_id=out.id,
    )
    return CodeFragment(
        statements=[build_assign(variable, parse_expr(str(code)))],
        bindings={out.id: variable},
    )


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
