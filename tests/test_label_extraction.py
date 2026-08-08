"""extract_label must return an object's OWN label, never a nested object's.

The old arbitrary-depth XPaths grabbed the first descendant label, so a
container with an unreadable own label was named after an inner node (a subVI
in a loop frame) or a cluster field ('status'/'source'). The fix scopes the
search to the object's own parts: an object's label lives in ITS <partsList>,
while a nested object keeps its parts (and label) under itself. These cases pin
that object-scoped behaviour, which let the loop/Select handlers drop their
guards.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.utils import extract_label

# A label as it appears in a real heap: class="label", a partID child, text
# under textRec/text — living inside its owning object's <partsList>.
_LABEL = (
    '<SL__arrayElement class="label"><partID>16</partID>'
    "<textRec><text>{}</text></textRec></SL__arrayElement>"
)


def _parts(name: str) -> str:
    return f"<partsList>{_LABEL.format(name)}</partsList>"


def _xml(s: str) -> ET.Element:
    return ET.fromstring(s)


def test_own_label_is_returned() -> None:
    obj = _xml(
        f'<obj class="stdClust">{_parts("error out")}'
        f'<field class="stdString">{_parts("source")}</field></obj>'
    )
    assert extract_label(obj) == "error out"


def test_empty_own_label_does_not_leak_a_field_name() -> None:
    # Own label compressed/empty -> must be None, NOT the nested field's 'source'
    # (the field keeps 'source' inside its OWN partsList).
    obj = _xml(
        f'<obj class="stdClust">{_parts("")}'
        f'<field class="stdString">{_parts("source")}</field></obj>'
    )
    assert extract_label(obj) is None


def test_container_is_not_named_after_an_inner_node() -> None:
    # A loop/case-structure frame containing a subVI must not borrow its label.
    loop = _xml(
        "<whileLoop><diagram>"
        f'<subVI class="subVI">{_parts("addSkipped.vi")}</subVI>'
        "</diagram></whileLoop>"
    )
    assert extract_label(loop) is None
