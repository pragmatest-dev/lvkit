"""The front panel's object tree: panes, splitters, tab pages, groupers, free
labels and decorations, read from the panel heap's ``paneHierarchy``.

Every position stays as stored; placing an object in the window is the
renderer's job. The tree holds the SAME ``ParsedFPControl`` objects the flat
``ParsedFrontPanel.controls`` list holds (looked up by the fPDCO's uid), so one
control is one object however it is reached. Format facts:
``docs/_internal/design/labview-binary-format.md`` "Front panel structure".
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from collections.abc import Sequence

from ..text_encoding import decode_labview_text
from .fp_fonts import font_of_run, read_font_run
from .models import (
    ParsedFPControl,
    ParsedFPDecoration,
    ParsedFPFont,
    ParsedFPGrouper,
    ParsedFPLabel,
    ParsedFPObject,
    ParsedFPPane,
    ParsedFPSplitter,
    ParsedFPTabPage,
)
from .utils import (
    HIDDEN_FLAG,
    clean_labview_string,
    decode_xml_entities_to_bytes,
    heap_color,
    heap_rect,
    int_or_none,
    strip_surrounding_quotes,
)

_TAB_CONTROL_CLASS = "tabControl"

# The pane's background is the cosm of this part id whose picture is this
# built-in id.
_PANE_BACKGROUND_PART = "28"
_PANE_BACKGROUND_IMAGE = "-704"


def read_panel_layout(
    root: ET.Element,
    controls_by_uid: dict[str, ParsedFPControl],
    fonts: Sequence[ParsedFPFont] = (),
) -> tuple[ParsedFPPane | ParsedFPSplitter | None, list[str]]:
    """The panel's object tree (None when the heap has no ``paneHierarchy`` or
    one that cannot be read) and what lvkit could not read in it, each a phrase
    for a problem's reason (``ParsedFrontPanel.unread_objects``).

    ``root`` is the front-panel heap root; ``controls_by_uid`` holds the
    controls the flat parse produced, keyed by fPDCO uid. An fPDCO with no
    control there is left out. A tab control reached in an object list gets
    its ``tab_pages`` and ``tab_selected`` set. ``fonts`` is the VI's font table,
    which resolves a free label's font.
    """
    hierarchy = root.find("root/paneHierarchy")
    if hierarchy is None:
        return None, []
    reader = _ObjectReader(controls_by_uid, fonts)
    return reader.read_node(hierarchy), reader.unread


def _pane_background(pane: ET.Element) -> str | None:
    for part in pane.findall("partsList/SL__arrayElement[@class='cosm']"):
        if (
            part.findtext("partID") == _PANE_BACKGROUND_PART
            and part.findtext("image/ImageResID") == _PANE_BACKGROUND_IMAGE
        ):
            return heap_color(part.findtext("fgColor"))
    return None


class _ObjectReader:
    def __init__(
        self, controls_by_uid: dict[str, ParsedFPControl], fonts: Sequence[ParsedFPFont]
    ):
        self._controls = controls_by_uid
        self._fonts = fonts
        self.unread: list[str] = []

    # --- panes and splitters -------------------------------------------

    def read_node(self, elem: ET.Element) -> ParsedFPPane | ParsedFPSplitter | None:
        node_class = elem.get("class")
        bounds = _bounds(elem)
        if bounds is None:
            self.unread.append(f"the panel's {node_class} with no bounds")
            return None
        if node_class == "pane":
            return ParsedFPPane(
                bounds=bounds,
                origin=_origin(elem),
                objects=self._read_objects(elem.find("zPlaneList")),
                doc_bounds=heap_rect(elem.find("docBounds")),
                background=_pane_background(elem),
            )
        if node_class == "splitter":
            return self._read_splitter(elem, bounds)
        self.unread.append(f"the panel's {node_class} (an unknown pane kind)")
        return None

    def _read_splitter(
        self, elem: ET.Element, bounds: tuple[int, int, int, int]
    ) -> ParsedFPSplitter | None:
        first_elem = elem.find("topOrLeft")
        second_elem = elem.find("botOrRight")
        ratio = _ratio(elem.findtext("ratio"))
        if first_elem is None or second_elem is None or ratio is None:
            self.unread.append("a splitter missing a pane or its ratio")
            return None
        first = self.read_node(first_elem)
        second = self.read_node(second_elem)
        if first is None or second is None:
            return None
        side_by_side = _side_by_side(first.bounds, second.bounds)
        if side_by_side is None:
            self.unread.append(
                "a splitter whose panes are neither side by side nor stacked"
            )
            return None
        return ParsedFPSplitter(
            bounds=bounds,
            ratio=ratio,
            side_by_side=side_by_side,
            first=first,
            second=second,
        )

    # --- object lists --------------------------------------------------

    def _read_objects(self, list_elem: ET.Element | None) -> list[ParsedFPObject]:
        """The objects of a ``zPlaneList`` / ``partsList``, in stored order."""
        objects: list[ParsedFPObject] = []
        if list_elem is None:
            return objects
        for elem in list_elem.findall("SL__arrayElement"):
            obj = self._read_object(elem)
            if obj is not None:
                objects.append(obj)
        return objects

    def _read_object(self, elem: ET.Element) -> ParsedFPObject | None:
        object_class = elem.get("class", "")
        if object_class == "fPDCO":
            return self._read_control(elem)
        readers = {
            "label": self._read_label,
            "cosm": self._read_decoration,
            "grouper": self._read_grouper,
        }
        read = readers.get(object_class)
        if read is None:
            self.unread.append(f"an object of kind {object_class}")
            return None
        bounds = _bounds(elem)
        if bounds is None:
            self.unread.append(f"a {object_class} with no bounds")
            return None
        return read(elem, bounds)

    def _read_control(self, fpdco: ET.Element) -> ParsedFPControl | None:
        control = self._controls.get(fpdco.get("uid", ""))
        if control is None:
            return None
        ddo = fpdco.find("ddo")
        if ddo is not None and ddo.get("class") == _TAB_CONTROL_CLASS:
            self._fill_tab(control, fpdco, ddo)
        return control

    def _read_label(
        self, elem: ET.Element, bounds: tuple[int, int, int, int]
    ) -> ParsedFPLabel:
        runs = [
            read_font_run(run) for run in elem.findall("textRec/fr/SL__arrayElement")
        ]
        return ParsedFPLabel(
            uid=elem.get("uid", ""),
            bounds=bounds,
            text=clean_labview_string(elem.findtext("textRec/text")),
            fg_color=heap_color(elem.findtext("fgColor")),
            bg_color=heap_color(elem.findtext("bgColor")),
            hidden=bool(_int(elem.findtext("objFlags")) & HIDDEN_FLAG),
            font_runs=runs,
            font=font_of_run(self._fonts, runs[0]) if runs else None,
        )

    def _read_decoration(
        self, elem: ET.Element, bounds: tuple[int, int, int, int]
    ) -> ParsedFPDecoration | None:
        image_res_id = int_or_none(elem.findtext("image/ImageResID"))
        if image_res_id is None:
            self.unread.append("a decoration with no shape (ImageResID)")
            return None
        return ParsedFPDecoration(
            uid=elem.get("uid", ""),
            bounds=bounds,
            image_res_id=image_res_id,
            fg_color=heap_color(elem.findtext("fgColor")),
            bg_color=heap_color(elem.findtext("bgColor")),
        )

    def _read_grouper(
        self, elem: ET.Element, bounds: tuple[int, int, int, int]
    ) -> ParsedFPGrouper:
        return ParsedFPGrouper(
            uid=elem.get("uid", ""),
            bounds=bounds,
            objects=self._read_objects(elem.find("partsList")),
        )

    # --- tab controls --------------------------------------------------

    def _fill_tab(
        self, control: ParsedFPControl, fpdco: ET.Element, ddo: ET.Element
    ) -> None:
        captions = [
            _caption(caption)
            for caption in ddo.findall(
                "tabControlPageInfoArray/SL__arrayElement/caption"
            )
        ]
        pages: list[ParsedFPTabPage] = []
        page_elems = ddo.findall("pageList/SL__arrayElement")
        for index, page in enumerate(page_elems):
            page_bounds = _bounds(page)
            if page_bounds is None:
                self.unread.append("a tab page with no bounds")
                continue
            pages.append(
                ParsedFPTabPage(
                    name=captions[index] if index < len(captions) else "",
                    bounds=page_bounds,
                    objects=self._read_objects(page.find("zPlaneList")),
                )
            )
        control.tab_pages = pages
        control.tab_selected = _selected_page(fpdco, ddo, captions, len(page_elems))


def _selected_page(
    fpdco: ET.Element, ddo: ET.Element, captions: list[str], page_count: int
) -> int | None:
    """The page shown when the VI was saved: the stored index when the file
    records one (the fPDCO's ``DefaultData``, then the ddo's
    ``tabControlPageSelValue``), else the caption equal to the shown tab text
    in the tab array's own label."""
    stored = _stored_page_index(fpdco, ddo)
    if stored is not None and stored < page_count:
        return stored
    shown = ddo.findtext(
        "partsList/SL__arrayElement[@class='tabArray']"
        "/partsList/SL__arrayElement[@class='label']/textRec/text"
    )
    text = clean_labview_string(shown)
    return captions.index(text) if text and text in captions else None


def _stored_page_index(fpdco: ET.Element, ddo: ET.Element) -> int | None:
    default = fpdco.findtext("DefaultData")
    if default:
        raw = decode_xml_entities_to_bytes(strip_surrounding_quotes(default))
        if len(raw) == 4:
            return int.from_bytes(raw, "big")
    selected = (ddo.findtext("tabControlPageSelValue") or "").strip()
    if selected:
        try:
            return int.from_bytes(bytes.fromhex(selected), "big")
        except ValueError:
            return None
    return None


def _caption(elem: ET.Element) -> str:
    """A tab caption: hex of the VI's text bytes, in the VI's own encoding."""
    try:
        raw = bytes.fromhex((elem.text or "").strip())
    except ValueError:
        return ""
    return decode_labview_text(raw).strip("\x00")


# --- field readers -----------------------------------------------------


def _numbers(text: str | None) -> list[int] | None:
    try:
        return [int(part) for part in (text or "").strip().strip("()").split(",")]
    except ValueError:
        return None


def _bounds(elem: ET.Element) -> tuple[int, int, int, int] | None:
    return heap_rect(elem.find("bounds"))


def _origin(elem: ET.Element) -> tuple[int, int]:
    """The pane's stored ``origin`` as ``(x, y)``; ``(0, 0)`` when absent."""
    nums = _numbers(elem.findtext("origin"))
    if nums is None or len(nums) != 2:
        return (0, 0)
    return (nums[0], nums[1])


def _ratio(text: str | None) -> float | None:
    try:
        return float(struct.unpack(">f", bytes.fromhex((text or "").strip()))[0])
    except (ValueError, struct.error):
        return None


def _side_by_side(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> bool | None:
    """True when the two panes share top and bottom (one left of the other),
    False when they share left and right (one above the other)."""
    if first[0] == second[0] and first[2] == second[2]:
        return True
    if first[1] == second[1] and first[3] == second[3]:
        return False
    return None


def _int(text: str | None) -> int:
    return int_or_none(text) or 0
