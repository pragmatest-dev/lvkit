"""A Conditional Disable frame's condition, read from its expression tree.

A frame's ``SelectorInfoElement`` holds an ``ExpressionInfo`` (heap tag
``activeDiag``) with two lists (see ``docs/_internal/design/
labview-binary-format.md``, "A Conditional Disable condition is an expression
tree"):

- ``Tag0000``: the condition's strings, hex-encoded (symbols and values).
- ``activeDiag``: the tree, one ``TreeElt`` per node, the root first. A node's
  ``Tag0000`` says what it is (``FF`` an operator, ``00`` a string); an
  operator's ``activeDiag`` (hex) and ``activeMarker`` (decimal) are the indexes
  of its two operands; its ``activePlot`` is the operator's code. A string's
  ``activePlot`` (hex) is the index of its text in the string list.

The only operator code in the corpus is ``FD``, which is ``==`` (deduced: a
structure whose frames are ``TARGET_TYPE`` Unix, Windows and Mac only works if
each frame tests equality). Any other code is an operator lvkit cannot read: the
label shows ``?`` in its place and the frame is reported.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from lvkit.text_encoding import decode_labview_text

_OPERATOR_NODE = "FF"
_STRING_NODE = "00"
# The operator code -> how a label writes it.
_OPERATORS = {"FD": "=="}


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


class _Reader:
    """Reads one condition tree, collecting the operator codes it cannot read."""

    def __init__(self, strings: list[str], nodes: list[ET.Element]) -> None:
        self.strings = strings
        self.nodes = nodes
        self.unknown: list[str] = []

    def read(self, index: int | None, depth: int = 0) -> str | None:
        """The text of node ``index``; ``None`` when the tree is malformed."""
        if index is None or not 0 <= index < len(self.nodes):
            return None
        if depth > len(self.nodes):  # a node that leads back to itself
            return None
        node = self.nodes[index]
        kind = (node.findtext("Tag0000") or "").strip().upper()
        if kind == _STRING_NODE:
            at = _index(node.findtext("activePlot"), 16)
            if at is None or not 0 <= at < len(self.strings):
                return None
            return self.strings[at]
        if kind != _OPERATOR_NODE:
            return None
        left = self.read(_index(node.findtext("activeDiag"), 16), depth + 1)
        right = self.read(_index(node.findtext("activeMarker"), 10), depth + 1)
        if left is None or right is None:
            return None
        code = (node.findtext("activePlot") or "").strip().upper()
        operator = _OPERATORS.get(code)
        if operator is None:
            self.unknown.append(code)
            return f"{left} ? {right}"
        return f"{left}{operator}{right}"


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
    label = reader.read(0)
    if label is None:
        return Condition(" ".join(strings), "lvkit could not follow its condition")
    if reader.unknown:
        codes = ", ".join(f"0x{code}" if code else "none" for code in reader.unknown)
        return Condition(
            label, f"lvkit could not read the operator of its condition (code {codes})"
        )
    return Condition(label)
