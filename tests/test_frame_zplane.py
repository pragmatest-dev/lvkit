"""Regression: frame content that lives only in a diagram's zPlaneList (a nested
flat sequence's structure box) must be captured, not orphaned.

Real case (CallTestMethod.vi): case 910's frame lists the flat sequence's inner
`sequenceFrame` in `nodeList` but the `flatSequence` structure itself only in
`zPlaneList`, so a nodeList-only walk dropped it (and everything inside) to the
top level, where it rendered in the always-visible base layer and floated in
every frame.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.nodes.base import frame_inner_node_uids


def _diag(nodelist: str, zplane: str) -> ET.Element:
    return ET.fromstring(
        f"<diag><nodeList>{nodelist}</nodeList><zPlaneList>{zplane}</zPlaneList></diag>"
    )


def test_zplane_only_flat_sequence_is_captured():
    diag = _diag(
        # nodeList uniquely carries the shift register + the flat seq's inner frame
        '<SL__arrayElement class="sRN" uid="856"/>'
        '<SL__arrayElement class="sequenceFrame" uid="3031"/>',
        # zPlaneList uniquely carries decorations + the flatSequence STRUCTURE
        '<SL__arrayElement class="attachment" uid="2949"/>'
        '<SL__arrayElement class="label" uid="464"/>'
        '<SL__arrayElement class="flatSequence" uid="3020"/>',
    )
    uids = frame_inner_node_uids(diag)
    # nodeList members preserved (unchanged behaviour)
    assert "856" in uids and "3031" in uids
    # the fix: the flatSequence structure from zPlaneList is now captured
    assert "3020" in uids
    # decorations are NOT captured (only structures augment from zPlaneList)
    assert "2949" not in uids and "464" not in uids


def test_nested_case_in_zplane_also_captured():
    diag = _diag(
        '<SL__arrayElement class="prim" uid="10"/>',
        '<SL__arrayElement class="select" uid="20"/>'
        '<SL__arrayElement class="commentNode" uid="30"/>',  # disable struct
    )
    uids = frame_inner_node_uids(diag)
    assert uids == ["10", "20", "30"]  # order preserved, deduped, structures only


def test_no_double_count_when_in_both_lists():
    diag = _diag(
        '<SL__arrayElement class="select" uid="20"/>',
        '<SL__arrayElement class="select" uid="20"/>',  # same struct in both
    )
    assert frame_inner_node_uids(diag) == ["20"]
