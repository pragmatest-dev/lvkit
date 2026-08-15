"""FP-heap type reconstruction — the clean-room fallback for pre-LV9 VIs that
carry no VCTP consolidated type pool (``fp_heap_type``).

The hermetic tests pin the reconstruction logic on hand-built heap fragments (no
corpus needed); the ``needs_samples`` test proves it end-to-end on a real
LabVIEW 8.2 VI whose ring + clusters are otherwise unresolvable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.parser.fp_heap_type import reconstruct_control_lvtype

# --- hermetic: reconstruct directly from hand-built heap fragments ----------


def test_ring_items_reconstructed_from_multilabel():
    """A ring/enum's item labels live inline in its ``multiLabel`` ``<buf>``."""
    ring = ET.fromstring(
        '<ddo class="stdRing" uid="1">'
        "  <partsList>"
        '    <SL__arrayElement class="multiLabel" uid="2">'
        '      <buf>(3)"No Op""Increment""Reset"</buf>'
        "    </SL__arrayElement>"
        "  </partsList>"
        "</ddo>"
    )
    t = reconstruct_control_lvtype(ring)
    assert t is not None
    assert t.kind == "enum"
    assert t.values is not None
    assert list(t.values) == ["No Op", "Increment", "Reset"]
    assert [v.value for v in t.values.values()] == [0, 1, 2]


def test_cluster_fields_in_ddolist_order():
    """Cluster fields come back in CLUSTER order (``<ddoList>``), NOT z-order,
    with each field's type reconstructed from its control."""
    clust = ET.fromstring(
        '<ddo class="stdClust" uid="1">'
        '  <ddoList elements="3">'  # cluster order: bool, num, string
        '    <SL__arrayElement uid="30" />'
        '    <SL__arrayElement uid="20" />'
        '    <SL__arrayElement uid="10" />'
        "  </ddoList>"
        '  <paneHierarchy class="pane">'
        "    <zPlaneList>"  # z-order differs from cluster order
        '      <SL__arrayElement class="stdString" uid="10" index="0" />'
        '      <SL__arrayElement class="stdNum" uid="20" index="1" />'
        '      <SL__arrayElement class="stdBool" uid="30" index="2" />'
        "    </zPlaneList>"
        "  </paneHierarchy>"
        "</ddo>"
    )
    t = reconstruct_control_lvtype(clust)
    assert t is not None
    assert t.kind == "cluster"
    assert t.fields is not None
    kinds = [(f.type.underlying_type if f.type else None) for f in t.fields]
    # ddoList order 30,20,10 -> Boolean, Numeric, String
    assert kinds == ["Boolean", "Numeric", "String"]


def test_nested_cluster_does_not_leak_parent_fields():
    """A cluster field that is itself a cluster keeps its OWN fields — matching
    by ``<ddoList>`` uid, not descendant-walking, is what prevents leakage."""
    clust = ET.fromstring(
        '<ddo class="stdClust" uid="1">'
        '  <ddoList elements="1">'
        '    <SL__arrayElement uid="100" />'
        "  </ddoList>"
        '  <paneHierarchy class="pane">'
        "    <zPlaneList>"
        '      <SL__arrayElement class="stdClust" uid="100">'
        '        <ddoList elements="1">'
        '          <SL__arrayElement uid="200" />'
        "        </ddoList>"
        '        <paneHierarchy class="pane">'
        "          <zPlaneList>"
        '            <SL__arrayElement class="stdBool" uid="200" />'
        "          </zPlaneList>"
        "        </paneHierarchy>"
        "      </SL__arrayElement>"
        "    </zPlaneList>"
        "  </paneHierarchy>"
        "</ddo>"
    )
    t = reconstruct_control_lvtype(clust)
    assert t is not None and t.fields is not None
    assert len(t.fields) == 1  # parent has exactly ONE field
    inner = t.fields[0].type
    assert inner is not None and inner.kind == "cluster"
    assert inner.fields is not None and len(inner.fields) == 1


def test_typedef_wrapper_is_unwrapped():
    """A ``typeDef`` DDO wraps one control; reconstruction sees through it."""
    td = ET.fromstring(
        '<ddo class="typeDef" uid="1">'
        "  <typeDesc>TypeID(27)</typeDesc>"
        '  <paneHierarchy class="pane"><zPlaneList>'
        '    <SL__arrayElement class="stdRing" uid="5">'
        '      <SL__arrayElement class="multiLabel" uid="6">'
        '        <buf>(2)"A""B"</buf>'
        "      </SL__arrayElement>"
        "    </SL__arrayElement>"
        "  </zPlaneList></paneHierarchy>"
        "</ddo>"
    )
    t = reconstruct_control_lvtype(td)
    assert t is not None and t.kind == "enum"
    assert list(t.values or {}) == ["A", "B"]


def test_unmodelled_control_returns_none():
    """A class refnum (name isn't in the FP heap) returns None so the caller can
    fall through rather than mislabel it."""
    assert (
        reconstruct_control_lvtype(ET.fromstring('<ddo class="udClassDDO" uid="1"/>'))
        is None
    )


# --- end-to-end on a real LabVIEW 8.2 VI (no VCTP) --------------------------

pytestmark_samples = pytest.mark.needs_samples
_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_LV82_VI = (
    _SAMPLES
    / "JKI-VI-Tester"
    / "source"
    / "Build Support"
    / "Package Builder Utilities"
    / "Auto Increment Package Version__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _LV82_VI.exists(), reason="JKI-VI-Tester sample absent")
def test_lv82_ring_and_clusters_resolve_end_to_end():
    """The LV8.2 VI has no VCTP; its ring + error clusters used to fall back to
    the bare family word. Now they resolve — the ring via the FP-heap
    reconstructor, and (authoritatively, with field names) via CONP, so the
    error clusters are even detected as such. See ``test_conp_types`` for the
    CONP path in isolation."""
    from lvkit.mcp.server import _load_one

    g, name = _load_one(str(_LV82_VI))
    terms = [
        *g.get_inputs(name, public_only=False),
        *g.get_outputs(name, public_only=False),
    ]
    labels = {t.type_descriptor() for t in terms}

    # The ring is fully recovered WITH its item labels.
    assert any(
        lbl.startswith("enum{") and "Major Increment" in lbl for lbl in labels
    ), labels
    # CONP recovers the cluster field names, so the error clusters are detected.
    assert "Error" in labels, labels
    # No structured terminal is left as the bare family word.
    assert "ring" not in labels and "cluster" not in labels, labels
