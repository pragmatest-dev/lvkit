"""A Conditional Disable frame's condition, read from its expression tree.

A frame's ``SelectorInfoElement`` holds an ``ExpressionInfo`` (heap tag
``activeDiag``) with two lists (see ``docs/_internal/design/
labview-binary-format.md``, "A Conditional Disable condition is an expression
tree"):

- ``Tag0000``: the condition's strings, hex-encoded (symbols and values).
- ``activeDiag``: the tree, one ``TreeElt`` per node. A node's ``Tag0000`` is
  the index of its parent (``FF`` for the root). A node with no operands
  (``activeDiag`` ``FF`` and ``activeMarker`` ``-1``) is a string, and its
  ``activePlot`` (hex) is the index of its text in the string list. Any other
  node is an operator: its ``activeDiag`` (hex) and ``activeMarker`` (decimal)
  are the indexes of its two operands, and its ``activePlot`` is the
  operator's code.

The operator codes are ``FB`` ``||``, ``FC`` ``&&``, ``FD`` ``==`` and ``FE``
``!=`` (as an outside tester described the VI they built with these conditions;
see the format doc). Any other code is an operator lvkit cannot read: the label shows
``?`` in its place and the frame is reported. A comparison binds tighter than
``&&``/``||``; an ``&&`` inside an ``||`` (or the reverse) is parenthesised.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from lvkit.text_encoding import decode_labview_text

_ROOT_PARENT = "FF"
# The operator code -> how a label writes it.
_OPERATORS = {"FB": "||", "FC": "&&", "FD": "==", "FE": "!="}
_LOGICAL = {"||", "&&"}


@dataclass(frozen=True)
class Condition:
    """One frame's condition as lvkit read it: its label, and why it could not
    be read in full (``None`` when it was)."""

    label: str
    unread: str | None = None


def condition_tokens(sel_info: ET.Element) -> list[str]:
    """Raw hex-encoded condition strings for one frame -- empty for a
    non-Conditional frame or a Conditional's else/Default frame."""
    return [
        raw
        for el in sel_info.findall("activeDiag/Tag0000/SL__arrayElement")
        if (raw := (el.text or "").strip())
    ]


def _text(raw: str) -> str:
    try:
        return decode_labview_text(bytes.fromhex(raw))
    except ValueError:
        return "?"


def _index(text: str | None, base: int) -> int | None:
    """A node or string index; ``None`` for an absent or "none" (``FF``/``-1``)
    one."""
    try:
        value = int(text or "", base)
    except ValueError:
        return None
    return None if value in (-1, 0xFF) else value


@dataclass(frozen=True)
class _Text:
    """A node's label text, and the operator it is built with (``None`` for a
    string), so a parent can decide whether it needs parentheses."""

    text: str
    operator: str | None = None


class _Reader:
    """Reads one condition tree, collecting the operator codes it cannot read."""

    def __init__(self, strings: list[str], nodes: list[ET.Element]) -> None:
        self.strings = strings
        self.nodes = nodes
        self.unknown: list[str] = []

    def root(self) -> int | None:
        """The one node whose parent is ``FF``; ``None`` unless there is
        exactly one."""
        roots = [
            i
            for i, node in enumerate(self.nodes)
            if (node.findtext("Tag0000") or "").strip().upper() == _ROOT_PARENT
        ]
        return roots[0] if len(roots) == 1 else None

    def read(self, index: int | None, parent: int | None = None) -> _Text | None:
        """The text of node ``index``, whose parent is node ``parent`` (None
        for the root); ``None`` when the tree is malformed."""
        if index is None or not 0 <= index < len(self.nodes):
            return None
        node = self.nodes[index]
        if _index(node.findtext("Tag0000"), 16) != parent:
            return None  # also stops a node that leads back to itself
        left_at = _index(node.findtext("activeDiag"), 16)
        right_at = _index(node.findtext("activeMarker"), 10)
        code = (node.findtext("activePlot") or "").strip().upper()
        if left_at is None and right_at is None:
            at = _index(code, 16)
            if at is None or not 0 <= at < len(self.strings):
                return None
            return _Text(self.strings[at])
        left = self.read(left_at, index)
        right = self.read(right_at, index)
        if left is None or right is None:
            return None
        operator = _OPERATORS.get(code)
        if operator is None:
            self.unknown.append(code)
            return _Text(f"{_operand(left, '?')} ? {_operand(right, '?')}", "?")
        if operator in _LOGICAL:
            return _Text(
                f"{_operand(left, operator)} {operator} {_operand(right, operator)}",
                operator,
            )
        return _Text(
            f"{_operand(left, operator)}{operator}{_operand(right, operator)}",
            operator,
        )


def _operand(operand: _Text, operator: str) -> str:
    """An operand's text inside ``operator``: bare when it is a string, a
    comparison inside ``&&``/``||``, or the same ``&&``/``||``; otherwise
    parenthesised (``&&`` inside ``||`` and the reverse, a comparison inside a
    comparison, an operator lvkit could not read)."""
    inner = operand.operator
    if inner is None:
        return operand.text
    if operator in _LOGICAL and (inner == operator or inner in ("==", "!=")):
        return operand.text
    return f"({operand.text})"


def read_condition(sel_info: ET.Element) -> Condition:
    """The condition of one Conditional Disable frame. No strings: the
    else/Default frame. A tree lvkit cannot follow shows its strings joined by
    spaces; an operator code it does not know shows as ``?``. Both are reported
    (``Condition.unread``)."""
    strings = [_text(raw) for raw in condition_tokens(sel_info)]
    if not strings:
        return Condition("Default")
    nodes = sel_info.findall("activeDiag/activeDiag/SL__arrayElement")
    reader = _Reader(strings, nodes)
    read = reader.read(reader.root())
    if read is None:
        return Condition(" ".join(strings), "lvkit could not follow its condition")
    label = read.text
    if reader.unknown:
        codes = ", ".join(f"0x{code}" if code else "none" for code in reader.unknown)
        return Condition(
            label, f"lvkit could not read the operator of its condition (code {codes})"
        )
    return Condition(label)
