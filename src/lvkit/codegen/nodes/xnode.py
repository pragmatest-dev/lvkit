"""Code generator dispatch for an XNode, by the recorded path of its library file
(``PrimitiveNode.xnode_library_path``, from the VI's ``XNXI`` link record). An
XNode of a library with no generator here is generated as a primitive is, and so
is an XNode no link record names (its path is ""), an Error Ring among them:
without the record nothing says which library it is."""

from __future__ import annotations

from collections.abc import Callable

from lvkit.graph.models import PrimitiveNode
from lvkit.parser.error_ring import ERROR_RING_PATH

from ..context import CodeGenContext
from ..fragment import CodeFragment
from . import error_ring, primitive

_GENERATORS: dict[str, Callable[[PrimitiveNode, CodeGenContext], CodeFragment]] = {
    ERROR_RING_PATH: error_ring.generate,
}


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The code of an XNode, by its library."""
    generator = _GENERATORS.get(node.xnode_library_path, primitive.generate)
    return generator(node, ctx)
