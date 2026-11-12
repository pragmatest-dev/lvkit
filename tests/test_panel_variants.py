"""The Ctrl Val methods and the Value property of a control reference carry a
``Variant`` while a panel holds each control's value in the control's own type
(``codegen/panels_module.py``, ``codegen/panel_types.py``): ``set_variant`` stores
the data of the variant (a control that holds a variant keeps the variant; an
empty variant is error 1 for any other), ``get_variant`` gives the value with the
type the panel records."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import generated_panels, generated_variants
from tests.test_front_panel_reference import SET, project_with, step


def _panel():
    """A panel of a number control ``x`` (I32), a text indicator ``t`` (String), a
    control ``v`` that holds a variant and a control ``u`` the panel records no type
    for."""
    variants = generated_variants()
    return generated_panels().Panel(
        lambda **_: None,
        controls={"x": "x", "v": "v", "u": "u"},
        indicators={"t": "t"},
        types={"x": variants.I32, "t": variants.String},
        variants=["v"],
    )


def test_set_variant_stores_the_data_of_the_variant_in_a_typed_control() -> None:
    variants, panel = generated_variants(), _panel()
    panel.set_variant("x", variants.Variant(4, variants.I32))
    assert panel.get("x") == 4
    # A control takes the data of any non-empty variant: the type is not judged.
    panel.set_variant("x", variants.Variant(2.5, variants.DBL))
    assert panel.get("x") == 2.5


def test_set_variant_of_an_empty_variant_is_error_1() -> None:
    variants, panel = generated_variants(), _panel()
    with pytest.raises(generated_panels().PanelError) as raised:
        panel.set_variant("x", variants.Variant())
    assert raised.value.code == 1
    assert panel.get("x") is None


def test_a_control_that_holds_a_variant_keeps_the_variant_it_is_given() -> None:
    variants, panel = generated_variants(), _panel()
    assert panel.get_variant("v") == variants.Variant()  # nothing set yet
    given = variants.Variant(
        5, variants.I32, {"a": variants.Variant("x", variants.String)}
    )
    panel.set_variant("v", given)
    assert panel.get_variant("v") is given
    panel.set_variant("v", variants.Variant())  # an empty variant is a value here
    assert panel.get_variant("v") == variants.Variant()


def test_get_variant_gives_the_value_with_the_type_the_panel_records() -> None:
    variants, panel = generated_variants(), _panel()
    panel.put("x", 7)
    assert panel.get_variant("x") == variants.Variant(7, variants.I32)
    assert panel.get_variant("t") == variants.Variant(None, variants.String)
    # A control the panel records no type for gives a Variant of no type.
    panel.put("u", 3)
    assert panel.get_variant("u") == variants.Variant(3)


def test_get_all_variants_gives_each_value_as_get_variant_does() -> None:
    variants, panel = generated_variants(), _panel()
    panel.put("x", 7)
    assert panel.get_all_variants(True) == [
        ("x", variants.Variant(7, variants.I32)),
        ("v", variants.Variant()),
        ("u", variants.Variant(None)),
    ]
    assert panel.get_all_variants(False) == [
        ("t", variants.Variant(None, variants.String))
    ]


def test_the_variant_of_a_control_reference_goes_through_the_same_methods() -> None:
    variants, panel = generated_variants(), _panel()
    reference = {c.name: c for c in panel.controls}
    reference["x"].variant = variants.Variant(9, variants.I32)
    assert panel.get("x") == 9
    assert reference["x"].variant == variants.Variant(9, variants.I32)
    assert reference["x"].value == 9  # the plain value is unchanged
    with pytest.raises(generated_panels().PanelError):
        reference["x"].variant = variants.Variant()


def test_ctrl_val_set_of_an_empty_variant_is_error_1_on_the_error_out(
    tmp_path: Path,
) -> None:
    steps = [step("Ctrl Val.Set", SET, name="x", empty=True)]
    package = project_with(tmp_path, steps, None).generate()
    assert "set_variant(" in package.sources["runner"]
    with package.use() as module:
        result = module("runner").runner()
        assert result.error_out.code == 1
        assert module("target").target.panel.get("x") == 0  # still its default
