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

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..picture_values import FILE_PATH_PARAMS, IMPORTS, TEMPLATES


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


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """One call of a picture VI, written from the template of its op."""
    from . import subvi

    assert entry.op is not None
    template = TEMPLATES[entry.op]
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
