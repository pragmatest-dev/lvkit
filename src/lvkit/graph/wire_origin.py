"""Where a wire's value comes from, and the constant text a path wire holds.

``WireOriginMixin`` follows a wire back through structure tunnels to the nodes it
starts at, and evaluates the path functions whose inputs are all constants
(Build Path, String To Path, Strip Path, Current VI's Path) the way a diagram
would at run time, so a path built from constants is known without running it.
"""

from __future__ import annotations

import ast
import operator
import posixpath
from typing import TYPE_CHECKING

from ..path_value import path_text
from .models import AnyGraphNode, ConstantNode, PrimitiveNode

if TYPE_CHECKING:
    from ..models import Terminal
    from .models import WireEnd

# Path primitives (data/primitives/file-i-o.json, uncategorized.json).
STRING_TO_PATH_PRIM_ID = 1078
BUILD_PATH_PRIM_ID = 1419
STRIP_PATH_PRIM_ID = 1420
CURRENT_VI_PATH_PRIM_ID = 1426

_BUILD_PATH_RELATIVE = 1
_BUILD_PATH_BASE = 2
_STRING_TO_PATH_STRING = 1
_STRIP_PATH_PATH = 0
_STRIP_PATH_STRIPPED = 1
_STRIP_PATH_NAME = 2


_BITWISE = {"or": operator.or_, "and": operator.and_}


class PathNotConstant(Exception):
    """A path wire whose value is only known when the diagram runs."""


class WireOriginMixin:
    """Wire tracing for ``InMemoryVIGraph``."""

    if TYPE_CHECKING:

        def incoming_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def terminal_vi(self, terminal_id: str) -> str | None: ...
        def _get_typed_node(self, uid: str) -> AnyGraphNode | None: ...

    def wire_origins(
        self, terminal_id: str, _seen: frozenset[str] = frozenset()
    ) -> list[tuple[AnyGraphNode, WireEnd]]:
        """The nodes the wire into ``terminal_id`` starts at, each with the
        terminal it leaves, looking through structure tunnels. Empty when nothing
        is wired; several when a case structure's output tunnel is fed from more
        than one frame."""
        if terminal_id in _seen:
            return []
        seen = _seen | {terminal_id}
        origins: list[tuple[AnyGraphNode, WireEnd]] = []
        for source in self.incoming_edges(terminal_id):
            node = self._get_typed_node(source.node_id)
            if node is None:
                continue
            if node.kind == "structure":
                origins.extend(self.wire_origins(source.terminal_id, seen))
            else:
                origins.append((node, source))
        return origins

    def constant_int(self, terminal_id: str) -> int | None:
        """The integer the wire into ``terminal_id`` holds when it is a constant
        or a Compound Arithmetic OR / AND of integer constants (every frame of a
        case structure giving the same one); None when it is only known when the
        diagram runs."""
        origins = self.wire_origins(terminal_id)
        values = {self._origin_int(node) for node, _ in origins}
        if len(values) != 1 or None in values:
            return None
        return values.pop()

    def _origin_int(self, node: AnyGraphNode) -> int | None:
        if isinstance(node, ConstantNode):
            try:
                return int(str(node.value), 0)
            except ValueError:
                return None
        if not (
            isinstance(node, PrimitiveNode)
            and node.node_type == "cpdArith"
            and node.operation in _BITWISE
        ):
            return None
        inputs = [t for t in node.terminals if t.direction == "input"]
        if any(t.inverted for t in inputs):
            return None
        wired = [t for t in inputs if self.incoming_edges(t.id)]
        operands = [self.constant_int(t.id) for t in wired]
        if not operands or None in operands:
            return None
        result = operands[0]
        for operand in operands[1:]:
            assert operand is not None and result is not None
            result = _BITWISE[node.operation](result, operand)
        return result

    def constant_path_text(self, terminal_id: str) -> str:
        """The text of the path or string the wire into ``terminal_id`` holds.

        Raises ``PathNotConstant`` when the wire is unwired or any part of it
        is only known when the diagram runs."""
        origins = self.wire_origins(terminal_id)
        if not origins:
            raise PathNotConstant("an input is not wired")
        texts = {self.origin_text(node, source) for node, source in origins}
        if len(texts) != 1:
            raise PathNotConstant("the frames of a case structure give different paths")
        return texts.pop()

    def origin_text(self, node: AnyGraphNode, source: WireEnd) -> str:
        if isinstance(node, ConstantNode):
            text = constant_text(node)
            if text is None:
                raise PathNotConstant("a constant that is not a path or a string")
            return text
        if not isinstance(node, PrimitiveNode):
            raise PathNotConstant(f"{node.name or 'a node'}")
        inputs = {
            t.index: t
            for t in node.terminals
            if t.direction == "input" and t.index is not None
        }
        if node.prim_id == STRING_TO_PATH_PRIM_ID:
            return self._input_text(inputs, _STRING_TO_PATH_STRING)
        if node.prim_id == BUILD_PATH_PRIM_ID:
            relative = self._input_text(inputs, _BUILD_PATH_RELATIVE)
            base = (
                self._input_text(inputs, _BUILD_PATH_BASE)
                if _BUILD_PATH_BASE in inputs
                and self.incoming_edges(inputs[_BUILD_PATH_BASE].id)
                else ""
            )
            return posixpath.normpath(posixpath.join(base, relative.lstrip("/")))
        if node.prim_id == STRIP_PATH_PRIM_ID:
            path = self._input_text(inputs, _STRIP_PATH_PATH)
            if source.index == _STRIP_PATH_STRIPPED:
                return posixpath.dirname(path)
            if source.index == _STRIP_PATH_NAME:
                return posixpath.basename(path)
        if node.prim_id == CURRENT_VI_PATH_PRIM_ID:
            caller = self.terminal_vi(source.terminal_id)
            if caller:
                return caller
        raise PathNotConstant(node.name or "a node")

    def _input_text(self, inputs: dict[int, Terminal], index: int) -> str:
        terminal = inputs.get(index)
        if terminal is None:
            raise PathNotConstant("an input is not wired")
        return self.constant_path_text(terminal.id)


def constant_text(const: ConstantNode) -> str | None:
    """The text of a String constant (recorded as a Python expression) or a Path
    constant (recorded as its path text); None for Not a Path."""
    if const.lv_type is None or not isinstance(const.value, str):
        return None
    underlying = const.lv_type.underlying_type
    if underlying == "Path":
        return path_text(const.value)
    if underlying != "String":
        return None
    try:
        expr = ast.parse(const.value, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None
