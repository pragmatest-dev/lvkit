"""``control_property`` finds a listed property by the codes of the row's path
segments and the class of the control; a code that is not listed is None."""

from __future__ import annotations

from lvkit.control_properties import control_property


def test_an_inherited_code_is_found_for_any_class() -> None:
    found = control_property("Anything", [104013824])
    assert found is not None
    assert (found.name, found.member, found.value) == ("Visible", "visible", "bool")
    tip = control_property("Bool", [104013844])
    assert tip is not None and tip.value == "string"


def test_a_class_code_is_found_only_for_its_class() -> None:
    found = control_property("Arr (strict)", [104041473])
    assert found is not None
    assert (found.name, found.member, found.value) == (
        "Number of Columns",
        "number_of_columns",
        "number",
    )
    assert control_property("Pict", [104041473]) is None
    assert control_property("Path", [104032266]) is not None


def test_a_row_of_several_segments_is_found_by_all_its_codes() -> None:
    found = control_property("Knob (strict)", [104075264, 104001542])
    assert found is not None
    assert (found.member, found.value) == ("scale_maximum", "number")
    assert control_property("Slide (strict)", [104072195, 104001541]) is not None
    caption = control_property("Str", [104013828, 103995392])
    assert caption is not None and caption.member == "caption_text"


def test_a_partial_or_reordered_path_is_not_listed() -> None:
    assert control_property("Knob (strict)", [104075264]) is None
    assert control_property("Knob (strict)", [104001542, 104075264]) is None
    assert control_property("Knob (strict)", [104072195, 104001542]) is None


def test_an_unlisted_code_is_none() -> None:
    assert control_property("Tree (strict)", [1969896466]) is None
    assert control_property("Bool", [104013825]) is None
    assert control_property("Bool", []) is None
