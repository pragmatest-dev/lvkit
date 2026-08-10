"""Reconstruct an ``LVType`` from a front-panel control's heap (DDO) subtree.

The fallback path for VIs that carry no VCTP consolidated type pool — pre-LV9
(LV8.x) VIs, whose types pylabview cannot resolve because it reads types only
from ``VCTP`` (absent) and leaves the pre-LV9 ``TRec`` blocks raw. The type is
NOT lost, though: LabVIEW stores how it *draws* each control in the FP heap
(``_FPHb.xml``), and that drawing IS the structure —

* a **ring/enum** carries its item labels inline in a ``multiLabel`` ``<buf>``;
* a **cluster** lists its fields (in cluster order) in ``<ddoList>``, with each
  field's control nested in ``paneHierarchy/zPlaneList``;
* an **array** nests its element control the same way.

So we reconstruct the ``LVType`` by walking that subtree — clean-room, no VCTP,
no binary reverse-engineering, no LabVIEW. Used only when the VCTP path yields
nothing (see ``extract_fp_terminals``); for LV9+ VIs the VCTP result wins.

Field *names* are not recovered here: in pre-LV9 heaps a control's label is a
text-pool reference, not inline text, so cluster fields come back positional
(``field_0`` …). The error-cluster detector keys on structure, not names, so the
common ``status``/``code``/``source`` case is still labelled faithfully.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..models import ClusterField, LVType, enum_values_from_labels

# Leaf FP control classes → their faithful scalar type token. (Numeric
# representation — I32 vs U16 vs DBL — is a further refinement; a bare
# ``Numeric`` is already faithful as "a scalar number", unlike ``cluster``.)
_SCALAR_CLASSES = {
    "stdString": "String",
    "stdBool": "Boolean",
    "stdPath": "Path",
    "stdNum": "Numeric",
}
_RING_CLASSES = {"stdRing", "stdEnum"}
_ARRAY_CLASSES = {"indArr", "stdArray"}
# Control classes this reconstructor understands (a typedef wraps one of these).
_KNOWN_CLASSES = (
    _SCALAR_CLASSES.keys() | _RING_CLASSES | _ARRAY_CLASSES | {"stdClust"}
)


def _multilabel_items(ctrl: ET.Element) -> list[str]:
    """The ordered item labels of a ring/enum, from its ``multiLabel`` ``<buf>``
    (``(4)"No Op""Major Increment"…``). Empty when none is present."""
    for ml in ctrl.iter("SL__arrayElement"):
        if ml.get("class") != "multiLabel":
            continue
        buf = ml.findtext("buf")
        if not buf:
            continue
        # Drop the leading ``(count)`` then read the concatenated quoted items.
        body = buf.split(")", 1)[1] if ")" in buf else buf
        return re.findall(r'"([^"]*)"', body)
    return []


def _wrapped_control(typedef: ET.Element) -> ET.Element | None:
    """The single control a ``typeDef`` DDO wraps — the first known-class control
    in document order (the wrapped type precedes its own children)."""
    for e in typedef.iter():
        if e is not typedef and e.get("class") in _KNOWN_CLASSES:
            return e
    return None


def _direct_fields(clust: ET.Element) -> list[ET.Element]:
    """A cluster's DIRECT field controls, in CLUSTER order.

    ``<ddoList>`` lists the field DDO uids in the logical cluster order (e.g.
    status/code/source for an error cluster); the field controls themselves sit
    in this cluster's own ``paneHierarchy/zPlaneList``. Matching by uid — rather
    than walking descendants — is what keeps a nested cluster's OWN fields from
    leaking up into its parent.
    """
    ddo_list = clust.find("ddoList")
    if ddo_list is None:
        return []
    order = [e.get("uid") for e in ddo_list.findall("SL__arrayElement")]

    zplane = clust.find("paneHierarchy/zPlaneList")
    if zplane is None:
        return []
    by_uid = {
        e.get("uid"): e
        for e in zplane.findall("SL__arrayElement")
        if e.get("class") in _KNOWN_CLASSES or e.get("class") == "typeDef"
    }
    return [by_uid[uid] for uid in order if uid in by_uid]


def _array_element(arr: ET.Element) -> ET.Element | None:
    """An array's element control — the first known-class control in its
    ``paneHierarchy/zPlaneList``."""
    zplane = arr.find("paneHierarchy/zPlaneList")
    if zplane is None:
        return None
    for e in zplane.findall("SL__arrayElement"):
        if e.get("class") in _KNOWN_CLASSES or e.get("class") == "typeDef":
            return e
    return None


def reconstruct_control_lvtype(
    ctrl: ET.Element, depth: int = 0,
) -> LVType | None:
    """Reconstruct the ``LVType`` of one FP control DDO from its heap subtree.

    Returns ``None`` for a control class this reconstructor does not model (e.g.
    ``udClassDDO`` — a class refnum whose class name is not in the FP heap), so
    the caller can fall through to another source rather than mislabel it.
    """
    if depth > 32:  # pathological nesting guard
        return None
    cls = ctrl.get("class", "")

    if cls == "typeDef":
        inner = _wrapped_control(ctrl)
        if inner is None:
            return None
        return reconstruct_control_lvtype(inner, depth + 1)

    if cls in _RING_CLASSES:
        items = _multilabel_items(ctrl)
        if not items:
            return None
        return LVType(
            kind="enum", underlying_type="Ring",
            values=enum_values_from_labels(items),
        )

    if cls == "stdClust":
        fields: list[ClusterField] = []
        for i, field_ctrl in enumerate(_direct_fields(ctrl)):
            fields.append(
                ClusterField(
                    name=f"field_{i}",
                    type=reconstruct_control_lvtype(field_ctrl, depth + 1),
                )
            )
        if not fields:
            return None
        return LVType(kind="cluster", underlying_type="Cluster", fields=fields)

    if cls in _ARRAY_CLASSES:
        elem = _array_element(ctrl)
        return LVType(
            kind="array",
            underlying_type="Array",
            element_type=(
                reconstruct_control_lvtype(elem, depth + 1)
                if elem is not None else None
            ),
            dimensions=1,
        )

    if cls in _SCALAR_CLASSES:
        return LVType(kind="primitive", underlying_type=_SCALAR_CLASSES[cls])

    return None


def reconstruct_dco_lvtype(fpdco: ET.Element) -> LVType | None:
    """Reconstruct the ``LVType`` of a front-panel DCO from its control subtree.

    The DCO's control is its ``<ddo>`` child; delegate to
    :func:`reconstruct_control_lvtype`. ``None`` when the DCO has no control or
    its type isn't reconstructable from the heap.
    """
    ddo = fpdco.find("ddo")
    return reconstruct_control_lvtype(ddo) if ddo is not None else None
