# ruff: noqa: E501 -- the fixed corpus VI paths are long literals
"""Every case frame's label agrees with the label LabVIEW stored for it.

For each case structure the block-diagram heap stores the label text of the
frame that was last displayed (``selString/textRec/text``, frame ``dIdx``;
absent means frame 0). lvkit builds the label of every frame from its parsed
selector values (``op_walk._selector_label``); for the displayed frame the two
must say the same thing. This runs over a FIXED list of corpus VIs covering
enum, string, error-cluster, integer and open-range selectors, so a regression
in any of the selector-value paths shows up here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

from lvkit.extractor import resolve_extracted
from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import CaseStructureNode
from lvkit.graph.op_walk import _selector_label
from lvkit.models import _is_error_cluster
from lvkit.parser.utils import clean_labview_string
from tests.conftest import SAMPLES_ROOT

pytestmark = pytest.mark.needs_samples

_FIXED_VIS: tuple[str, ...] = (
    # The VIs the issue names: empty-string, multi-string, string-and-default,
    # enum default, open-range and error-cluster frames.
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Item Indent.vi",
    "JKI-VI-Tester/source/Utilities/Expand Pathroot.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Key Down Up/KeyDown.vi",
    "JKI-VI-Tester/source/Ant Plugin/Source/_TextTestResult.Ant/createTestCaseXMLReport.vi",
    "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/lv_icon/Support/Extract Data.vi",
    "JKI-EasyXML/Source/Support/Low Level/Escape-Unescape String for XML.vi",
    "JKI-EasyXML/Source/Fast Parser/Link XML Start Tags with End Tags.vi",
    "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/lv_icon/Support/Average Grayscale of Line.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Write Error to File.vi",
    "JKI-EasyXML/Source/Support/Low Level/Error Handling/Toolkit Error Handling - Error Cluster From Error Code.vi",
    "JKI-EasyXML/Source/JKI Reuse Candidates/Multiline String to Array (Preserve EOLs).vi",
    # Further string, enum, error-cluster and integer selectors.
    "JKI-EasyXML/Source/OpenG Derivative/Get Default Data from TD--EasyXML.vi",
    "JKI-EasyXML/Source/OpenG Derivative/Get Default Data from TD--WithTIMESTAMP.vi",
    "JKI-EasyXML/Source/Support/Low Level/Date Time/Parse XML dateTime String.vi",
    "JKI-VI-Tester/source/JKI Reuse/Draw Flattened Pixmap (Fast)__jki_lib_picture -- VI Tester.vi",
    "JKI-VI-Tester/source/Prototype/VI Tester Plugin/support/Create Unit Tests UI.vi",
    "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Get Test Path By Tag.vi",
    "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Load and Execute Tests Process__core.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/AssessRectangle.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Glyphs/SET_ToolGraphic.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Icon Editor/MenuSelection(User).vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Load Unload/Reset Layer VI.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Load Unload/Write Data to Caller.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/ni.com_iconlibrary/ni.com_iconlibrary.vi",
    "JKI-VI-Tester/source/Ant Plugin/Source/_TextTestResult.Ant/createTestCaseXMLReport_Helper.vi",
    "JKI-VI-Tester/source/Classes/TestCase/run.vi",
    "JKI-VI-Tester/source/JKI Reuse/TREE_Get Children__jki_lib_tree_control -- VI Tester.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Get All.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Item Add.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Item Modify.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Item Outdent.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Tag Check.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Tag Convert.vi",
    "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/Generate LV Icon Template Layer.vi",
    "JKI-EasyXML/Source/JKI Reuse Candidates/Read Lines from File - With Error in and Error Out - JKI.vi",
    "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner - Main UI - .vi",
    "JKI-EasyXML/Source/Easy Parse XML.vi",
    "JKI-EasyXML/Source/Easy Generate XML.vi",
    "JKI-EasyXML/Source/Fast Parser/Get Tag Content.vi",
    "JKI-EasyXML/Source/Fast Parser/Link Children.vi",
    "JKI-EasyXML/Source/Fast Parser/Draw Tree from XML.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Item Properties.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Item Remove.vi",
    "JKI-VI-Tester/source/Utilities/Tree_Control_VIs/Get Siblings.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Finalize Movement.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Icon Editor/Escape.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Layer/DeleteLayer.vi",
    # Error clusters whose second frame is the whole domain (stored `Error ..`)
    # and whose Default frame is displayed.
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Support/IE_Open Help Link.vi",
    "actor-framework/Core/Install Support/Pre-Install Custom Action.vi",
    "actor-framework/Core/Install Support/Stash Framework.vi",
    "actor-framework/Tooling/Run all tests CLI.vi",
    "LabVIEW-DAQ/Intrinsic Optical Signal/PCO Edge 5.5/PCO Examples/pco.camera_LightSheet_Example.vi",
    "measurement-plugin-labview/Source/Generator/_MeasurementClient Utility/Handle Error in Client Generation.vi",
    "measurement-plugin-labview/Source/Runtime/Clients/Discovery V1/Service Location FGV Impl.vi",
    "measurement-plugin-labview/Source/Tools/run_diff.vi",
    "actor-framework/Providers/MessageRescripter/Classes/Abstract Message Rescripter/Create Class.vi",
    "actor-framework/Tooling/support/Get Paths to Icon Editor Files in LV Installation.vi",
    "measurement-plugin-labview/Source/Generator/_MeasurementService Utility/RenameRefactor/Valid Replacement Target.vi",
)


@dataclass(frozen=True)
class FrameLabelCheck:
    """One case structure's displayed frame: the stored label and ours."""

    vi: str
    case_uid: str
    stored: str
    ours: str


def _normalize(label: str) -> str:
    """Compare labels by their words: LabVIEW quotes item names and strings in
    the stored text and pads it with spaces."""
    return "".join(label.replace('"', "").split())


class HeapLabel(NamedTuple):
    """What the heap stores for one case structure's displayed frame."""

    stored: str
    displayed: int
    values_in_heap: bool  # the heap has SelectRangeArray32 (LabVIEW 2010+)


def _heap_labels(vi_path: Path) -> dict[str, HeapLabel]:
    """``case uid -> HeapLabel`` from the block-diagram heap."""
    bd_xml = resolve_extracted(vi_path)[0]
    root = ET.parse(bd_xml).getroot()
    labels: dict[str, HeapLabel] = {}
    for elem in root.iter():
        if elem.get("class") not in ("caseStruct", "select"):
            continue
        uid = elem.get("uid")
        text = elem.findtext("selString/textRec/text")
        if uid is None or text is None:
            continue
        d_idx = elem.findtext("dIdx")
        displayed = int(d_idx) if d_idx and d_idx.lstrip("-").isdigit() else 0
        labels[uid] = HeapLabel(
            clean_labview_string(text).strip(),
            displayed,
            elem.find("SelectRangeArray32") is not None,
        )
    return labels


def frame_label_checks(rel_path: str) -> list[FrameLabelCheck]:
    """Compare the stored and built label of every case's displayed frame in one
    corpus VI. Two kinds of case are skipped, each for a reason in the bytes:

    * A non-Boolean case with no ``SelectRangeArray32`` is a pre-2010 case whose
      values live only in the dataspace: its ``dIdx`` is a global diagram
      ordinal, not a frame index
      (``test_pre_2010_dIdx_is_a_global_ordinal``).
    * A frame whose strings hold control characters: the stored text writes
      them as backslash codes (``test_control_characters_are_stored_as_codes``).
    """
    vi_path = SAMPLES_ROOT / rel_path
    heap = _heap_labels(vi_path)
    graph = InMemoryVIGraph()
    graph.load_vi(vi_path, mode=LoadMode.MINIMAL)
    vi_name = graph.resolve_vi_name(vi_path.name)
    checks: list[FrameLabelCheck] = []
    for node_id in sorted(graph._vi_nodes[vi_name]):
        node = graph.get_graph_node(node_id)
        if not isinstance(node, CaseStructureNode):
            continue
        uid = node_id.rsplit("::", 1)[-1]
        if uid not in heap:
            continue
        stored, displayed, values_in_heap = heap[uid]
        if not 0 <= displayed < len(node.frames):
            continue
        lv_type = next(
            (t.lv_type for t in node.terminals if t.id == node.selector_terminal),
            None,
        )
        is_boolean = bool(lv_type and lv_type.underlying_type == "Boolean")
        if not values_in_heap and not is_boolean:
            continue
        is_error = bool(lv_type and _is_error_cluster(lv_type))
        ours = _selector_label(node.frames[displayed], lv_type, is_error)
        if any(ord(ch) < 0x20 for ch in ours):
            # The stored text writes a control character as a backslash code.
            continue
        checks.append(FrameLabelCheck(rel_path, uid, stored, ours))
    return checks


@pytest.mark.parametrize("rel_path", _FIXED_VIS)
def test_displayed_frame_label_matches_stored_label(rel_path: str) -> None:
    checks = frame_label_checks(rel_path)
    assert checks, f"{rel_path}: no case structure with a stored label"
    wrong = [c for c in checks if _normalize(c.stored) != _normalize(c.ours)]
    assert not wrong, "\n".join(
        f"{c.case_uid}: stored {c.stored!r}, built {c.ours!r}" for c in wrong
    )


_LEGACY_VI = "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Get Project Members from Path - SM.vi"
_CONTROL_CHAR_VI = "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/Key Down Up/KeyDown.vi"


def _case_element(rel_path: str, uid: str) -> ET.Element:
    root = ET.parse(resolve_extracted(SAMPLES_ROOT / rel_path)[0]).getroot()
    (elem,) = [
        e
        for e in root.iter()
        if e.get("class") in ("caseStruct", "select") and e.get("uid") == uid
    ]
    return elem


def test_pre_2010_dIdx_is_a_global_ordinal() -> None:
    """In a pre-2010 VI the case values are only in the dataspace table, and the
    heap ``dIdx`` counts diagrams across the whole VI: the other three cases of
    this VI store dIdx 36, 38 and 52 for 2-frame cases. Case 5 (20 frames) happens
    to have an in-range dIdx (18), but its stored label ("Project: Get All VI
    Paths") belongs to frame 19 by the table (and by that frame's contents, a
    Read VI Hierarchy call), so ``dIdx`` cannot be read as a frame index there."""
    sizes = {}
    for uid in ("5", "1320", "3477", "3839"):
        elem = _case_element(_LEGACY_VI, uid)
        assert elem.find("SelectRangeArray32") is None
        sizes[uid] = (
            int(elem.findtext("dIdx") or 0),
            len(elem.findall("diagramList/*")),
        )
    assert sizes == {
        "5": (18, 20),
        "1320": (36, 2),
        "3477": (38, 2),
        "3839": (52, 2),
    }
    assert frame_label_checks(_LEGACY_VI) == []


def test_control_characters_are_stored_as_codes() -> None:
    """KeyDown.vi case 1555 matches the string array element ``0000`` (two NUL
    bytes); the stored label shows it as one backslash code, ``\\00``, so the
    stored text does not keep the byte count and cannot be compared."""
    elem = _case_element(_CONTROL_CHAR_VI, "1555")
    elements = [e.text for e in elem.findall("SelectStringArray/*")]
    assert elements == ["0000", "08"]
    assert elem.findtext("selString/textRec/text") == '" "\\00" "'
