"""Corpus VIs whose Bundle / Unbundle elements used to stop on "Type resolution
needed": each generates its cluster reads and writes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit.pipeline import generate_python

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
OPENG_USER_LIB = SAMPLES / "OpenG/extracted/File Group 0/user.lib"
ICON_SUPPORT = (
    SAMPLES / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/lv_icon/Support"
)
MESSAGE_MAKER = (
    SAMPLES / "actor-framework/Providers/MessageMakerProvider/_Message Maker"
)


def _generate(vi: Path, tmp_path: Path) -> tuple[str, list[str]]:
    result = generate_python(vi, tmp_path, userlib_root=OPENG_USER_LIB)
    written = Path(result["vi_paths"][str(vi.resolve())])
    return written.read_text(encoding="utf-8"), [gap.reason for gap in result["gaps"]]


def _type_gaps(vi: Path, tmp_path: Path) -> tuple[str, list[str]]:
    source, gaps = _generate(vi, tmp_path)
    return source, [gap for gap in gaps if "Type resolution needed" in gap]


def test_by_name_elements_inside_a_typedef_cluster_of_a_tuple_are_read_and_set(
    tmp_path: Path,
) -> None:
    vi = OPENG_USER_LIB / "_OpenG.lib/picture/picture.llb/GIF.Read.Unpack__ogtk.vi"
    source, type_gaps = _type_gaps(vi, tmp_path)
    assert type_gaps == []
    # the typedef cluster at position 1 is copied before one of its fields is set
    assert "cluster_part.image_type = 0" in source
    assert re.search(
        r"(cluster_part\w*)\.rectangle = \1\.rectangle\[:2\] \+ \(", source
    )


def test_a_classic_unbundle_stores_the_position_among_the_clusters_own_fields(
    tmp_path: Path,
) -> None:
    source, type_gaps = _type_gaps(ICON_SUPPORT / "Extract Data.vi", tmp_path)
    assert type_gaps == []
    # element 2 of (number, image data, image data) is the last cluster
    assert re.search(r"copy\.copy\(\w+\[2\]\)", source)


def test_a_cluster_constant_saved_without_its_typedef_is_written_as_the_typedef(
    tmp_path: Path,
) -> None:
    """The constant that feeds a Bundle By Name of LVPointTypeDef is that
    typedef's value: an object with attributes, not a tuple the node cannot set
    an attribute on."""
    vi = (
        OPENG_USER_LIB
        / "_OpenG.lib/appcontrol/appcontrol.llb/Fit VI window to Largest Dec__ogtk.vi"
    )
    source, _ = _generate(vi, tmp_path)
    assert "cluster = SimpleNamespace(horizontal=0, vertical=0)" in source
    assert "cluster = (0, 0)" not in source


def test_an_unwired_bundle_element_keeps_the_value_of_the_cluster_wired_in(
    tmp_path: Path,
) -> None:
    source, type_gaps = _type_gaps(ICON_SUPPORT / "Flood Glyph.vi", tmp_path)
    assert type_gaps == []


def test_an_unwired_element_that_selects_no_field_of_a_class_reads_nothing(
    tmp_path: Path,
) -> None:
    vi = MESSAGE_MAKER / "_templates/Message Template/Do.vi"
    _, type_gaps = _type_gaps(vi, tmp_path)
    assert type_gaps == []


def test_unbundle_by_name_inside_a_named_cluster_of_a_tuple(tmp_path: Path) -> None:
    _, type_gaps = _type_gaps(MESSAGE_MAKER / "_support/Sort Control Refs.vi", tmp_path)
    assert type_gaps == []
