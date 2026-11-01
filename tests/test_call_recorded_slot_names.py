"""A call node's terminals are named by the labels the calling VI records for the
called VI's connector pane (the Function type its ``<connectorTM>`` names), by
slot, whether or not the called VI is loaded."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import ClusterField, LVType, LVTypeKind, PaneSlot
from lvkit.parser.vi import _recorded_slot_names

_BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")


def _function() -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Function",
        pane=(
            PaneSlot(0, True, _BOOL, "error out"),
            PaneSlot(2, False, _BOOL, "error in (no error)"),
            PaneSlot(5, False, _BOOL, None),
        ),
    )


def _call(type_id: str | None) -> ET.Element:
    body = f"<connectorTM>{type_id}</connectorTM>" if type_id else ""
    return ET.fromstring(
        f'<SL__arrayElement class="iUse" uid="1">{body}</SL__arrayElement>'
    )


def test_labels_are_read_by_slot_and_unlabelled_slots_are_left_out() -> None:
    names = _recorded_slot_names(_call("TypeID(7)"), {7: _function()})
    assert names == {0: "error out", 2: "error in (no error)"}


def test_a_cluster_holding_one_function_gives_that_functions_labels() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[ClusterField(name="", type=_function())],
    )
    names = _recorded_slot_names(_call("TypeID(7)"), {7: cluster})
    assert names == {0: "error out", 2: "error in (no error)"}


def test_a_call_without_a_recorded_connector_type_has_no_names() -> None:
    assert _recorded_slot_names(_call(None), {7: _function()}) == {}
    assert _recorded_slot_names(_call("TypeID(9)"), {7: _function()}) == {}
    assert _recorded_slot_names(_call("TypeID(7)"), None) == {}
